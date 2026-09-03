"""The CI route registry.

STDLIB ONLY. This module is imported by two environments that share nothing:

  * ci/gate/     runs inside the pytest lane, which has Django but not requests
  * ci/smoke/    runs outside the container, which has requests but not Django

A third-party import here breaks one of them.

Every application route is declared exactly once. A route that is not declared
is refused at request time, which is what makes running against production
defensible: dangerous routes are excluded because nobody opted them in, not
because somebody remembered to list them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from urllib.parse import urlsplit

PROFILES = ("local", "dev", "prod")

EXCLUDE_CODES = frozenset({
    "EXCLUDE_COST",           # calls a paid model
    "EXCLUDE_EXTERNAL",       # needs Luria SSH or another external system
    "EXCLUDE_UNSAFE_METHOD",  # unsafe to call from an automated sweep
    "EXCLUDE_DEAD",           # route cannot function; tracked separately
    "EXCLUDE_ADMIN",          # administrative surface, out of scope for CI
})

# A '$' that ends the pattern and is not escaped, i.e. a real tail anchor rather
# than a literal dollar in the path.
_TAIL_ANCHOR = re.compile(r"(?<!\\)\$$")


@dataclass(frozen=True)
class Route:
    pattern: str                      # verbatim from Django's resolver; the gate diffs this
    path: str | None                  # concrete request path, may contain {placeholders}
    methods: tuple[str, ...]          # what CI will send, not what the route allows
    profiles: frozenset[str]          # empty means never called; then exclude is required
    auth: str = "smoke"               # anon | smoke | web | write
    # The status a WORKING route returns, never the status a broken one returns
    # today. With `xfail` set, that makes the tier report xfailed while the defect
    # is there and XPASS the day it is fixed. Declaring the broken status instead
    # inverts both signals, so ci/smoke/test_registry_contents.py refuses a 500 in
    # the `expect` of an xfailed route.
    expect: int | tuple[int, ...] = 200
    shape: str | None = None          # a key that must exist in the JSON body
    xfail: str | None = None          # reason, when the route is broken today
    exclude: str | None = None        # a CATEGORY CODE; see EXCLUDE_CODES
    resolver: bool = True             # Django's resolver reports it; the gate expects it
    prod_allows_non_get: bool = False  # prod otherwise refuses non-GET; this one is allowed
    note: str | None = None

    def __post_init__(self) -> None:
        # Entries are authored as profiles="local,dev" for readability. Normalise to a
        # frozenset so exactly one representation exists at run time. Without this,
        # `profile in route.profiles` is a SUBSTRING test: "od" in "local,dev,prod"
        # is True, and the guard silently passes garbage.
        if isinstance(self.profiles, str):
            object.__setattr__(
                self, "profiles",
                frozenset(p.strip() for p in self.profiles.split(",") if p.strip()),
            )
        # Same reasoning for methods: one representation, so `"GET" in route.methods`
        # and `set(methods) - {"GET"}` cannot be defeated by a lowercase entry.
        # A bare string is wrapped first, exactly as `profiles` is: iterating one
        # yields CHARACTERS, so methods="GET" would become ("G", "E", "T") -- an
        # entry with no GET in it at all, which every consumer reads as a route CI
        # does not call, and `set(methods) - {"GET"}` reads as three write methods.
        if isinstance(self.methods, str):
            object.__setattr__(self, "methods", (self.methods,))
        object.__setattr__(
            self, "methods", tuple(m.strip().upper() for m in self.methods)
        )
        unknown = self.profiles - set(PROFILES)
        if unknown:
            raise ValueError(f"unknown profile(s) {sorted(unknown)} in {self.pattern}")
        if not self.profiles and not self.exclude:
            raise ValueError(
                f"{self.pattern}: a route with no profiles must carry an exclude code"
            )
        if self.exclude and self.exclude not in EXCLUDE_CODES:
            raise ValueError(
                f"{self.pattern}: exclude must be a category code from "
                f"{sorted(EXCLUDE_CODES)}, not a description. This repo is public."
            )
        if self.prod_allows_non_get:
            if "prod" not in self.profiles:
                raise ValueError(
                    f"{self.pattern}: prod_allows_non_get needs 'prod' in profiles"
                )
            if not set(self.methods) - {"GET"}:
                raise ValueError(
                    f"{self.pattern}: prod_allows_non_get needs a non-GET method"
                )
        # Compile now rather than on first use. A malformed pattern is a declaration
        # bug, and a cached_property would only surface it mid-sweep, from inside
        # whichever consumer happened to call matches() first.
        try:
            self.matcher
        except re.error as exc:
            raise ValueError(f"{self.pattern}: not a usable regex ({exc})") from exc

    @cached_property
    def is_exact(self) -> bool:
        """The pattern pins the whole path rather than matching a prefix of it."""
        return bool(_TAIL_ANCHOR.search(self.pattern))

    @cached_property
    def literal_length(self) -> int:
        """How much of the path the pattern spells out, discounting regex groups.

        A viewset's detail route is longer than its own list-level action --
        '^attributes/(?P<pk>[^/.]+)/$' vs '^attributes/search/$' -- yet the action is
        the more specific declaration, and the one Django's resolver returns. Ranking
        on literal characters instead of raw length reproduces that ordering.
        """
        return len(re.sub(r"\(.*?\)", "", self.pattern))

    @cached_property
    def matcher(self) -> re.Pattern[str]:
        """A regex matching a request path.

        Django patterns arrive concatenated from nested include()s, so they carry
        interior anchors: '^seek/^sample/id=(?P<id>\\d+)/$'. Strip the anchors and
        anchor once at the front.

        Two kinds of character must survive that strip:

          * '[^/.]+', how every DRF detail route spells its pk. That caret negates a
            character class; removing it inverts the class and the route never matches.
          * an escaped '\\$', a literal dollar in the path rather than an anchor.

        Django's re_path is a PREFIX match unless the pattern ends in '$': '^login'
        resolves '/login/'. So the tail is anchored only when the original was.
        """
        body = re.sub(r"(?<!\[)\^", "", self.pattern)   # anchors, not class negations
        body = re.sub(r"(?<!\\)\$", "", body)           # anchors, not literal dollars
        tail = "$" if self.is_exact else ""
        return re.compile("^/" + body.lstrip("/") + tail)

    def matches(self, url_path: str) -> bool:
        return bool(self.matcher.match(url_path))


# Placeholder vocabulary. Every '{name}' any path below uses is a key here, and
# every key is used by at least one path -- ci/smoke/test_registry_contents.py
# asserts both directions. One name per ID SPACE, not per route: several routes
# take the same kind of identifier and must resolve it the same way.
#
# The values say where a run-time fixture finds a real value. They are ids that
# differ between the seed, dev and production, so none of them is ever written
# into a path literally. Where an id space has nothing to discover -- a job,
# task, session, bundle or NHP name -- the path carries a syntactically valid
# NONEXISTENT literal instead, and `expect` is what that route answers for an
# identifier it cannot find once it behaves. That is not always a not-found
# status: the three outcomes are listed with the literals below. Either way it
# proves the route resolves, and it needs no fixture.
PLACEHOLDERS: dict[str, str] = {
    "assay_id":        "data[0].id of GET /nextseek_api/assays/ (SEEK numeric assay id)",
    "assay_slug":      "the slug of the first detail link on /seek/assays/ "
                       "(None where assay_context is absent, which skips the detail route)",
    "attribute_id":    "id of the first item in GET /nextseek_api/attributes/",
    "sample_type_code": "the code of the first detail link on /seek/sampletypes/ "
                        "(a NExtSEEK sample type code such as D.FLOW, not a numeric id)",
    "data_file_id":    "data[0].id of GET /nextseek_api/data_files/ (SEEK numeric data-file id)",
    "investigation_id": "data[0].id of GET /nextseek_api/investigations/ (SEEK numeric id)",
    "person_id":       "data[0].id of GET /nextseek_api/people/ (SEEK numeric person id)",
    "sample_id":       "numeric id of the first row of GET /seek/searchAdvanced/ for a broad "
                       "PARTIAL search (a SEEK samples.id; every proxy detail route resolves it)",
    "sample_type_id":  "data[0].id of GET /nextseek_api/sample_types/ (SEEK numeric sample-type id)",
    "sample_uid":      "uid of that same searchAdvanced row with its HTML tags stripped "
                       "(a NExtSEEK UID string, not a number)",
    "seek_project_id": "data[0].id of GET /nextseek_api/projects/ (SEEK numeric project id)",
    "sop_id":          "data[0].id of GET /nextseek_api/sops/ (SEEK numeric SOP id)",
    "study_id":        "data[0].id of GET /nextseek_api/studies/ (SEEK numeric study id)",
}

# A UUID that is valid syntax and belongs to nothing, for the job / task /
# session id spaces. Every route that takes one is declared with whatever it
# answers for an identifier it cannot find once it behaves, and that is three
# different things: a 404 for most; a 200 whose body carries the failure instead,
# for two of the seek helpers (their notes say so); and, for
# /seek/nhpdata/<name>/, the same 404 its two siblings return, which it does not
# manage today -- that entry carries an xfail naming what it raises instead. All
# three prove the route resolves without a fixture.
_NO_SUCH_UUID = "00000000-0000-4000-8000-000000000000"
_NO_SUCH_ID = "999999999"
_NO_SUCH_NHP = "NEXTSEEK-CI-NO-SUCH-NHP"

# `methods` is what CI SENDS, not what the route allows. Two consequences worth
# stating once rather than in fifty notes:
#
#   * A route enabled for `prod` is declared GET-only. Several of them also
#     accept a write method; the write lane exercises those on local and dev,
#     where the profile permits it. Declaring the write method here would say
#     that CI sends it under every profile the entry names, prod included.
#   * A route with no GET at all is declared with its write method and never
#     carries `prod`: the guard refuses every non-GET under that profile anyway,
#     so naming prod on a POST-only route is a contradiction, not extra reach.
REGISTRY: list[Route] = [

    # ----------------------------------------------------------------- #
    # project-level
    # ----------------------------------------------------------------- #
    Route(pattern=r"^$", path="/",
          methods=("GET",), profiles="local,dev,prod", auth="anon", expect=200,
          note="home dashboard; each tile query is separately guarded, so 200 "
               "does not prove the database is answering"),
    Route(pattern=r"^accounts/login/", path="/accounts/login/",
          methods=("GET",), profiles="local,dev,prod", auth="anon", expect=200,
          note="second registration of the login view, below the CMS catch-all"),
    Route(pattern=r"^accounts/signup/", path="/accounts/signup/",
          methods=("GET",), profiles="local,dev,prod", auth="anon", expect=302,
          note="hands account creation to SEEK; redirects to SEEK_PUBLIC_URL/signup"),
    Route(pattern=r"^login", path="/login/",
          methods=("GET", "POST"), profiles="local,dev,prod", auth="anon", expect=200,
          prod_allows_non_get=True,
          note="the one non-GET the prod profile permits: authenticating is a "
               "precondition of a read-only sweep"),
    Route(pattern=r"^media/(?P<path>.*)$",
          path="/media/download/nextseek-ci-probe-no-such-file.xlsx",
          methods=("GET",), profiles="local,dev,prod", auth="anon", expect=404,
          note="Django's static serve; probed with a nonexistent file, so the entry "
               "proves the route resolves and denies and downloads nothing"),
    Route(pattern=r"^signup/", path="/signup/",
          methods=("GET",), profiles="local,dev,prod", auth="anon", expect=302,
          note="hands account creation to SEEK; redirects to SEEK_PUBLIC_URL/signup"),

    # project-level, excluded
    Route(pattern=r"^logout$", path=None,
          methods=(), profiles="", auth="anon", exclude="EXCLUDE_UNSAFE_METHOD",
          note="the SEEK logout view"),

    # ----------------------------------------------------------------- #
    # not in the application resolver: nginx-served asset, Django admin
    # The gate ignores resolver=False entries in both directions.
    # ----------------------------------------------------------------- #
    Route(pattern=r"^admin/login/$", path="/admin/login/",
          methods=("GET",), profiles="local,dev,prod", auth="anon", expect=200,
          resolver=False,
          note="Django admin is outside the gate's denominator; kept as the "
               "cheapest proof Django itself serves"),
    Route(pattern=r"^static/(?P<path>.*)$", path="/static/css/nextseek.css",
          methods=("GET",), profiles="local,dev,prod", auth="anon", expect=200,
          resolver=False,
          note="served by nginx; a collected asset, catches a missed collectstatic"),

    # ----------------------------------------------------------------- #
    # seek: pages (the 23 of the design's page inventory)
    # ----------------------------------------------------------------- #
    Route(pattern=r"^seek/^admin/clades/$", path="/seek/admin/clades/",
          methods=("GET",), profiles="local,dev", auth="write", expect=200,
          note="clade editor page"),
    Route(pattern=r"^seek/^admin/internal_assays/$", path="/seek/admin/internal_assays/",
          methods=("GET",), profiles="local,dev", auth="write", expect=200,
          note="internal-assay editor page"),
    Route(pattern=r"^seek/^admin/retrieve/", path="/seek/admin/retrieve/",
          methods=("GET",), profiles="local,dev", auth="write", expect=200,
          note="bulk retrieval page; the GET renders the form, the POST builds a workbook"),
    Route(pattern=r"^seek/^assistant/", path="/seek/assistant/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="chat assistant page; renders an error template at 200 for an "
               "anonymous visitor rather than bouncing"),
    Route(pattern=r"^seek/^data/upload/", path="/seek/data/upload/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200),
    Route(pattern=r"^seek/^datafile/query/", path="/seek/datafile/query/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200),
    Route(pattern=r"^seek/^assays/$", path="/seek/assays/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="renders its empty state on any stack without dmac.assay_context, "
               "which today is every stack but prod and the seeded local one; a "
               "200 here is NOT evidence the table landed"),
    Route(pattern=r"^seek/^assays/(?P<slug>[\w-]+)/$", path="/seek/assays/{assay_slug}/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="skipped wherever assay_slug is None, i.e. wherever the list page "
               "rendered no assays to scrape a link from"),
    Route(pattern=r"^seek/^help/$", path="/seek/help/",
          methods=("GET",), profiles="local,dev,prod", auth="anon", expect=200),
    Route(pattern=r"^seek/^newsearch/", path="/seek/newsearch/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="unlinked from the UI; recorded so nobody mistakes it for the daily driver"),
    Route(pattern=r"^seek/^projects/$", path="/seek/projects/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200),
    Route(pattern=r"^seek/^projects/(?P<project_id>\d+)/$",
          path="/seek/projects/{seek_project_id}/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200),
    Route(pattern=r"^seek/^projects/(?P<project_id>\d+)/connections/$",
          path="/seek/projects/{seek_project_id}/connections/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="the project page's diagram iframe. 200 with a placeholder body "
               "when the graph returns nothing, so a dead Neo4j is not a red "
               "route; seek/tests/test_project_page.py pins that separately"),
    Route(pattern=r"^seek/^projects/(?P<project_id>\d+)/samples/$",
          path="/seek/projects/{seek_project_id}/samples/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="the project page's sample-counts full view, opened as a modal "
               "or visited directly; membership-gated like connections"),
    Route(pattern=r"^seek/^remote/", path="/seek/remote/",
          methods=("GET",), profiles="local,dev", auth="web", expect=200,
          xfail="NameError: samples is not defined, raised by "
                "seek/views/search.py::remote"),
    Route(pattern=r"^seek/^sample/id=(?P<id>\d+)/$", path="/seek/sample/id={sample_id}/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200),
    Route(pattern=r"^seek/^sample_timeline/.*$", path="/seek/sample_timeline/",
          methods=("GET",), profiles="local,dev,prod", auth="anon", expect=200,
          note="a TemplateView, so a scan for render( misses it; its bundle emits "
               "unavoidable 404s, so T2 exempts it from the console check"),
    Route(pattern=r"^seek/^sample_types/id=(?P<id>\d+)/$",
          path="/seek/sample_types/id={sample_type_id}/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200),
    Route(pattern=r"^seek/^sampletypes/$", path="/seek/sampletypes/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200),
    Route(pattern=r"^seek/^sampletypes/(?P<code>[\w.]+)/$",
          path="/seek/sampletypes/{sample_type_code}/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="404s for a code with no sample_types_context row, which is why "
               "the placeholder is scraped off the list page rather than taken "
               "from /nextseek_api/sample_types/"),
    Route(pattern=r"^seek/^samples/attributes/", path="/seek/samples/attributes/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200),
    Route(pattern=r"^seek/^samples/query/", path="/seek/samples/query/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200),
    Route(pattern=r"^seek/^samples/search/", path="/seek/samples/search/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=302,
          note="a pure 302 to /seek/search/ for a logged-in caller"),
    Route(pattern=r"^seek/^samples/upload/", path="/seek/samples/upload/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="bounces an anonymous visitor to /login/?next=/seek/samples/batchupload/, "
               "which is not the requested path; do not assert on next"),
    Route(pattern=r"^seek/^sampletree/uid=(?P<uid>[\w.-]{0,256})/$",
          path="/seek/sampletree/uid={sample_uid}/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="the only page that exercises UID resolution; id= never does"),
    Route(pattern=r"^seek/^search/", path="/seek/search/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="the daily driver"),
    Route(pattern=r"^seek/^sop/query/", path="/seek/sop/query/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="grid fed by /nextseek_api/sops/"),
    Route(pattern=r"^seek/^templates/", path="/seek/templates/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="renders zero template links on a stock deployment; T2 pins that"),
    Route(pattern=r"^seek/^templates/download/$", path="/seek/templates/download/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="the Download Templates picker's submit target. A bare GET carries no "
               "codes and re-renders the picker with a message; the POST that streams "
               "a workbook is write-lane material on local and dev"),
    Route(pattern=r"^seek/^url/(?P<url>[\w-]+)/$", path="/seek/url/smoke/",
          methods=("GET",), profiles="local,dev", auth="web", expect=200,
          xfail="NameError: getPageRequests is not defined, raised by "
                "seek/views/samples.py::seek"),

    # ----------------------------------------------------------------- #
    # seek: JSON helpers behind the pages
    # ----------------------------------------------------------------- #
    Route(pattern=r"^seek/^attributes/id=(?P<id>[\w.-]{0,256})/$",
          path="/seek/attributes/id={sample_type_id}/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="attribute definitions for a sample type; takes an id or a title"),
    Route(pattern=r"^seek/^document/id=(?P<id>\d+)/$",
          path="/seek/document/id=" + _NO_SUCH_ID + "/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="resolves a SEEK document to a download URL and returns it as JSON. "
               "Unknown id: proves the route resolves and denies, at status 200 "
               "with status:0 in the body"),
    Route(pattern=r"^seek/^eventdata/(?P<nhp_name>[\w-]+)/(?P<event_type>[\w.-]+)/(?P<date>[\w-]+)/$",
          path="/seek/eventdata/" + _NO_SUCH_NHP + "/imaging/2026-01-01/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=404,
          note="NHP timeline events. Unknown name: proves the route resolves and denies"),
    Route(pattern=r"^seek/^instituion/id=(?P<id>\d+)/$",
          path="/seek/instituion/id=" + _NO_SUCH_ID + "/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="institution member list. Unknown id: a non-supervisor caller gets the "
               "Default option without the id being read, so this stays a 200"),
    Route(pattern=r"^seek/^investigations/id=(?P<id>\d+)/$",
          path="/seek/investigations/id={investigation_id}/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="study options for an investigation, for the search form"),
    Route(pattern=r"^seek/^nhpdata/(?P<nhp_name>[\w-]+)/download/$",
          path="/seek/nhpdata/" + _NO_SUCH_NHP + "/download/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=404,
          note="NHP timeline workbook. Unknown name: proves the route resolves and denies"),
    Route(pattern=r"^seek/^operators/$", path="/seek/operators/",
          methods=("GET",), profiles="local,dev", auth="web", expect=(200, 400),
          xfail="MultiValueDictKeyError: 'sampletype_id' from "
                "seek/views/samples.py::getOperators, which indexes the query string "
                "for two required parameters with no default, so a bare GET is a 500 "
                "rather than a 400"),
    Route(pattern=r"^seek/^sample/id=(?P<id>\d+)/edit",
          path="/seek/sample/id={sample_id}/edit",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=302,
          note="a pure 302 out to the SEEK sample editor"),
    Route(pattern=r"^seek/^sample/id=(?P<id>\d+)/manage",
          path="/seek/sample/id={sample_id}/manage",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=302,
          note="a pure 302 out to the SEEK sample manager"),
    Route(pattern=r"^seek/^samplefind/", path="/seek/samplefind/",
          methods=("GET",), profiles="local,dev", auth="web", expect=200,
          note="workbook-driven sample lookup; the real path is a POST with a file, "
               "which writes an export file, so this stays off prod"),
    Route(pattern=r"^seek/^samples/download/", path="/seek/samples/download/",
          methods=("GET",), profiles="local,dev", auth="web", expect=(200, 400),
          xfail="MultiValueDictKeyError: 'includeSampleTree' from "
                "seek/views/samples.py::sampleDownload, which indexes the query "
                "string for a required parameter with no default, so a bare GET is "
                "a 500 rather than a 400",
          note="a parameterised call writes an export file, so this stays off prod"),
    Route(pattern=r"^seek/^samples/export/", path="/seek/samples/export/",
          methods=("GET",), profiles="local,dev", auth="web", expect=(200, 400),
          xfail="MultiValueDictKeyError: 'allids' from "
                "seek/views/samples.py::sampleExport, which indexes the query string "
                "for a required parameter with no default, so a bare GET is a 500 "
                "rather than a 400",
          note="a parameterised call writes an export file, so this stays off prod"),
    Route(pattern=r"^seek/^samples/retrieveType/", path="/seek/samples/retrieveType/",
          methods=("GET",), profiles="local,dev", auth="web", expect=(200, 400),
          xfail="MultiValueDictKeyError: 'sampletype_id' from "
                "seek/views/samples.py::getSampleType, which indexes the query string "
                "for two required parameters with no default, so a bare GET is a 500 "
                "rather than a 400"),
    Route(pattern=r"^seek/^samples/searching/", path="/seek/samples/searching/",
          methods=("GET",), profiles="local,dev", auth="web", expect=(200, 400),
          xfail="MultiValueDictKeyError: 'sampletype_id' from "
                "seek/views/search.py::sampleSearching, whose filter builder indexes "
                "the query string for required parameters with no default, so a bare "
                "GET is a 500 rather than a 400"),
    Route(pattern=r"^seek/^samplesvalidate/", path="/seek/samplesvalidate/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="upload validator; a GET returns the not-a-POST envelope at 200 and "
               "reads nothing"),
    Route(pattern=r"^seek/^sampleupload/", path="/seek/sampleupload/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="upload receiver; a GET returns the not-a-POST envelope at 200 and "
               "reads nothing"),
    Route(pattern=r"^seek/^searchAdvanced/", path="/seek/searchAdvanced/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=(200, 400),
          xfail="MultiValueDictKeyError: 'filter_searchText' from "
                "seek/views/search.py::searchingAdvanced, whose filter builder indexes "
                "the query string for required parameters with no default, so a bare "
                "GET is a 500 rather than a 400",
          note="the JSON behind the advanced-search grid. Read-only, and prod-enabled "
               "for that reason: it is where the sample fixtures find a real sample id "
               "and UID, so without it the sample pages and the sample-page flow have "
               "nothing to ask for under prod. The bare GET T0 sends is pinned by the "
               "xfail above; the parameterised query is the one discovery and the "
               "flows make"),
    Route(pattern=r"^seek/^searchUIDs/", path="/seek/searchUIDs/",
          methods=("GET",), profiles="local,dev", auth="web", expect=(200, 400),
          xfail="MultiValueDictKeyError: 'filter_searchUIDs' from "
                "seek/views/search.py::searchingUIDs, whose filter builder indexes the "
                "query string for required parameters with no default, so a bare GET "
                "is a 500 rather than a 400"),
    Route(pattern=r"^seek/^studies/id=(?P<id>\d+)/$", path="/seek/studies/id={study_id}/",
          methods=("GET",), profiles="local,dev,prod", auth="web", expect=200,
          note="assay options for a study, for the search form"),
    Route(pattern=r"^seek/nhpdata/(?P<nhp_name>[\w-]+)/$",
          path="/seek/nhpdata/" + _NO_SUCH_NHP + "/",
          methods=("GET",), profiles="local,dev", auth="smoke", expect=404,
          xfail="TypeError: 'NoneType' object does not support item assignment, "
                "surfaced by seek/views/timeline.py::get_nhp_data. The timeline "
                "builder does not handle an NHP it cannot find, so an unknown name is "
                "a 500 rather than the 404 its siblings return"),
    Route(pattern=r"^seek/nhpinfo/(?P<nhp_name>[\w-]+)/$",
          path="/seek/nhpinfo/" + _NO_SUCH_NHP + "/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=404,
          note="NHP metadata. Unknown name: proves the route resolves and denies"),

    # ----------------------------------------------------------------- #
    # seek: excluded
    # ----------------------------------------------------------------- #
    Route(pattern=r"^seek/^admin/clades/syncSampleTypes/$", path=None,
          methods=(), profiles="", auth="write", exclude="EXCLUDE_UNSAFE_METHOD",
          note="clade / sample-type synchronisation action"),
    Route(pattern=r"^seek/^admin/internal_assays/syncInternalAssays$", path=None,
          methods=(), profiles="", auth="write", exclude="EXCLUDE_UNSAFE_METHOD",
          note="assay / internal-assay synchronisation action"),
    Route(pattern=r"^seek/^attribute/delete/", path=None,
          methods=(), profiles="", auth="write", exclude="EXCLUDE_UNSAFE_METHOD",
          note="sample-attribute editor, delete action"),
    Route(pattern=r"^seek/^attribute/save/", path=None,
          methods=(), profiles="", auth="write", exclude="EXCLUDE_UNSAFE_METHOD",
          note="sample-attribute editor, save action"),
    Route(pattern=r"^seek/^clade/delete/$", path=None,
          methods=(), profiles="", auth="write", exclude="EXCLUDE_UNSAFE_METHOD",
          note="clade editor, delete action"),
    Route(pattern=r"^seek/^clade/sampleTypes/save/$", path=None,
          methods=(), profiles="", auth="write", exclude="EXCLUDE_UNSAFE_METHOD",
          note="clade editor, sample-type mapping save action"),
    Route(pattern=r"^seek/^clade/save/$", path=None,
          methods=(), profiles="", auth="write", exclude="EXCLUDE_UNSAFE_METHOD",
          note="clade editor, save action"),
    Route(pattern=r"^seek/^internal_assays/assayAssociation/save$", path=None,
          methods=(), profiles="", auth="write", exclude="EXCLUDE_UNSAFE_METHOD",
          note="internal-assay editor, association save action"),
    Route(pattern=r"^seek/^internal_assays/delete$", path=None,
          methods=(), profiles="", auth="write", exclude="EXCLUDE_UNSAFE_METHOD",
          note="internal-assay editor, delete action"),
    Route(pattern=r"^seek/^internal_assays/save$", path=None,
          methods=(), profiles="", auth="write", exclude="EXCLUDE_UNSAFE_METHOD",
          note="internal-assay editor, save action"),
    Route(pattern=r"^seek/^retrieve/samples/", path=None,
          methods=(), profiles="", auth="anon", exclude="EXCLUDE_UNSAFE_METHOD",
          note="sample retrieval endpoint behind the retrieval page"),
    Route(pattern=r"^seek/^samples/delete/", path=None,
          methods=(), profiles="", auth="write", exclude="EXCLUDE_UNSAFE_METHOD",
          note="sample deletion endpoint behind the search grid"),

    # ----------------------------------------------------------------- #
    # nextseek_api: reads
    # ----------------------------------------------------------------- #
    Route(pattern=r"^nextseek_api/^$", path="/nextseek_api/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          note="router root; exactly 15 registrations, so a 16th or a missing one "
               "means a registration changed"),
    Route(pattern=r"^nextseek_api/^^admin/project-export/(?P<pk>[^/.]+)/$",
          path="/nextseek_api/admin/project-export/{seek_project_id}/",
          methods=("GET",), profiles="local,dev", auth="write", expect=200,
          note="browser-friendly twin of the export action; superuser only, so the "
               "expectation is by inspection -- the sweep never holds those rights"),
    Route(pattern=r"^nextseek_api/^^assay-registrations/jobs/(?P<job_id>[^/.]+)/$",
          path="/nextseek_api/assay-registrations/jobs/" + _NO_SUCH_UUID + "/",
          methods=("GET",), profiles="local,dev", auth="write", expect=404,
          note="unknown id: proves the route resolves and denies. Superuser only, so "
               "the expectation is by inspection"),
    Route(pattern=r"^nextseek_api/^^assays/$", path="/nextseek_api/assays/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data", note="also accepts POST, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^assays/(?P<uid>[^/]+)/$",
          path="/nextseek_api/assays/{assay_id}/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data", note="also accepts PATCH, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^assistant/me/$", path="/nextseek_api/assistant/me/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="is_admin",
          note="is_admin reflects is_superuser only: login sets is_staff on every SEEK "
               "user, so is_staff admits everyone"),
    Route(pattern=r"^nextseek_api/^^assistant/sessions/$",
          path="/nextseek_api/assistant/sessions/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="sessions",
          note="also accepts POST, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^assistant/sessions/(?P<session_id>[0-9a-f-]+)/$",
          path="/nextseek_api/assistant/sessions/" + _NO_SUCH_UUID + "/",
          methods=("GET",), profiles="local,dev", auth="smoke", expect=404,
          note="unknown id: proves the route resolves and denies. Also accepts PATCH "
               "and DELETE, which only the destructive lane sends"),
    Route(pattern=r"^nextseek_api/^^assistant/sessions/(?P<session_id>[0-9a-f-]+)/bundles/(?P<bundle_id>\d+)/$",
          path="/nextseek_api/assistant/sessions/" + _NO_SUCH_UUID + "/bundles/1/",
          methods=("GET",), profiles="local,dev", auth="smoke", expect=404,
          note="unknown id: proves the route resolves and denies"),
    Route(pattern=r"^nextseek_api/^^assistant/sessions/(?P<session_id>[0-9a-f-]+)/bundles/(?P<bundle_id>\d+)/artifacts/(?P<artifact_key>[\w]+)/$",
          path="/nextseek_api/assistant/sessions/" + _NO_SUCH_UUID + "/bundles/1/artifacts/report/",
          methods=("GET",), profiles="local,dev", auth="smoke", expect=404,
          note="unknown id: proves the route resolves and denies"),
    Route(pattern=r"^nextseek_api/^^assistant/tasks/(?P<task_id>[0-9a-f-]+)/progress/$",
          path="/nextseek_api/assistant/tasks/" + _NO_SUCH_UUID + "/progress/",
          methods=("GET",), profiles="local,dev", auth="smoke", expect=404,
          note="unknown id: proves the route resolves and denies"),
    Route(pattern=r"^nextseek_api/^^assistant/test-cases/$",
          path="/nextseek_api/assistant/test-cases/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="test_cases"),
    Route(pattern=r"^nextseek_api/^^attributes/$", path="/nextseek_api/attributes/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="attributes",
          note="re-proves the caller against SEEK on every request, so a SEEK outage "
               "is a 401 here rather than a 5xx"),
    Route(pattern=r"^nextseek_api/^^attributes/(?P<pk>[^/.]+)/$",
          path="/nextseek_api/attributes/{attribute_id}/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          note="a flat attribute record, not an envelope"),
    Route(pattern=r"^nextseek_api/^^attributes/jobs/(?P<job_id>[^/.]+)/$",
          path="/nextseek_api/attributes/jobs/" + _NO_SUCH_UUID + "/",
          methods=("GET",), profiles="local,dev", auth="write", expect=404,
          note="unknown id: proves the route resolves and denies. Superuser only, so "
               "the expectation is by inspection"),
    Route(pattern=r"^nextseek_api/^^batch-upload/$", path="/nextseek_api/batch-upload/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="jobs"),
    Route(pattern=r"^nextseek_api/^^batch-upload/status/(?P<job_id>[^/.]+)/$",
          path="/nextseek_api/batch-upload/status/" + _NO_SUCH_UUID + "/",
          methods=("GET",), profiles="local,dev", auth="smoke", expect=404,
          note="unknown id: proves the route resolves and denies"),
    Route(pattern=r"^nextseek_api/^^batch-upload/summary/(?P<job_id>[^/.]+)/$",
          path="/nextseek_api/batch-upload/summary/" + _NO_SUCH_UUID + "/",
          methods=("GET",), profiles="local,dev", auth="smoke", expect=404,
          note="unknown id: proves the route resolves and denies"),
    Route(pattern=r"^nextseek_api/^^cc-assistant/artifacts/(?P<session>[0-9a-f-]+)/download/$",
          path="/nextseek_api/cc-assistant/artifacts/" + _NO_SUCH_UUID + "/download/",
          methods=("GET",), profiles="local,dev", auth="smoke", expect=404,
          note="unknown id: proves the route resolves and denies"),
    Route(pattern=r"^nextseek_api/^^cc-assistant/tasks/(?P<task_id>[0-9a-f-]+)/progress/$",
          path="/nextseek_api/cc-assistant/tasks/" + _NO_SUCH_UUID + "/progress/",
          methods=("GET",), profiles="local,dev", auth="smoke", expect=404,
          note="unknown id: proves the route resolves and denies"),
    Route(pattern=r"^nextseek_api/^^cc-assistant/transcript/(?P<session>[0-9a-f-]+)/(?P<turn>[^/.]+)/$",
          path="/nextseek_api/cc-assistant/transcript/" + _NO_SUCH_UUID + "/1/",
          methods=("GET",), profiles="local,dev", auth="smoke", expect=404,
          note="unknown id: proves the route resolves and denies"),
    Route(pattern=r"^nextseek_api/^^cc-assistant/upload/list/$",
          path="/nextseek_api/cc-assistant/upload/list/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="files"),
    Route(pattern=r"^nextseek_api/^^cc-assistant/upload/status/(?P<job_id>[^/.]+)/$",
          path="/nextseek_api/cc-assistant/upload/status/" + _NO_SUCH_UUID + "/",
          methods=("GET",), profiles="local,dev", auth="smoke", expect=404,
          note="unknown id: proves the route resolves and denies"),
    Route(pattern=r"^nextseek_api/^^data_files/$", path="/nextseek_api/data_files/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data", note="also accepts POST, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^data_files/(?P<uid>[^/]+)/$",
          path="/nextseek_api/data_files/{data_file_id}/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data", note="also accepts PATCH, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^entity_tree/edge_attributes/$",
          path="/nextseek_api/entity_tree/edge_attributes/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="results",
          note="every internal_assay_id null means the MySQL enrichment failed behind "
               "a 200; T1 asserts that, not the status"),
    Route(pattern=r"^nextseek_api/^^entity_tree/edges/$",
          path="/nextseek_api/entity_tree/edges/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="count", note="cheapest liveness proof for neo4j that is also a real surface"),
    Route(pattern=r"^nextseek_api/^^entity_tree/nodes/$",
          path="/nextseek_api/entity_tree/nodes/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="results",
          xfail="34 sample types have no attribute definitions, so "
                "nextseek_api/services/entity_tree.py::EntityTreeViewSet.list_nodes "
                "returns an application-level 502 rather than emit empty "
                "metadata_fields",
          note="the envelope is doubly wrapped: assert results.total, never a "
               "top-level total"),
    Route(pattern=r"^nextseek_api/^^evaluator/runs/$", path="/nextseek_api/evaluator/runs/",
          methods=("GET",), profiles="local,dev", auth="write", expect=200,
          note="superuser only, so the expectation is by inspection"),
    Route(pattern=r"^nextseek_api/^^evaluator/sessions/(?P<session_id>[0-9a-f-]+)/bundles/(?P<bundle_id>\d+)/retry-context/$",
          path="/nextseek_api/evaluator/sessions/" + _NO_SUCH_UUID + "/bundles/1/retry-context/",
          methods=("GET",), profiles="local,dev", auth="write", expect=404,
          note="unknown id: proves the route resolves and denies. Superuser only, so "
               "the expectation is by inspection"),
    Route(pattern=r"^nextseek_api/^^evaluator/tasks/(?P<task_id>[0-9a-f-]+)/retry-context/$",
          path="/nextseek_api/evaluator/tasks/" + _NO_SUCH_UUID + "/retry-context/",
          methods=("GET",), profiles="local,dev", auth="write", expect=404,
          note="unknown id: proves the route resolves and denies. Superuser only, so "
               "the expectation is by inspection"),
    Route(pattern=r"^nextseek_api/^^investigations/$", path="/nextseek_api/investigations/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data", note="also accepts POST, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^investigations/(?P<uid>[^/]+)/$",
          path="/nextseek_api/investigations/{investigation_id}/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data", note="also accepts PATCH, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^people/$", path="/nextseek_api/people/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data", note="also accepts POST, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^people/(?P<uid>[^/]+)/$",
          path="/nextseek_api/people/{person_id}/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data", note="also accepts PATCH, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^people/current/$", path="/nextseek_api/people/current/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data",
          note="the identity probe: proves MySQL and SEEK Rails are both answering, "
               "and T1 asserts the login is the caller's own"),
    Route(pattern=r"^nextseek_api/^^projects/$", path="/nextseek_api/projects/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data", note="also accepts POST, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^projects/(?P<uid>[^/]+)/$",
          path="/nextseek_api/projects/{seek_project_id}/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data", note="also accepts PATCH, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^sample-tree/(?P<uid>[^/]+)/tree/$",
          path="/nextseek_api/sample-tree/{sample_id}/tree/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="nodes", note="takes a sample id or a sample UID"),
    Route(pattern=r"^nextseek_api/^^sample_types/$", path="/nextseek_api/sample_types/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data", note="also accepts POST, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^sample_types/(?P<uid>[^/]+)/$",
          path="/nextseek_api/sample_types/{sample_type_id}/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data", note="also accepts PATCH, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^sample_types/connections/$",
          path="/nextseek_api/sample_types/connections/",
          methods=("GET",), profiles="local,dev", auth="write", expect=200,
          note="superuser only, so the expectation is by inspection"),
    Route(pattern=r"^nextseek_api/^^samples/(?P<uid>[^/]+)/$",
          path="/nextseek_api/samples/{sample_id}/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data",
          note="also accepts PATCH and DELETE, which only the write and destructive "
               "lanes send"),
    Route(pattern=r"^nextseek_api/^^sampletypes/(?P<uid>[^/]+)/child_types/$",
          path="/nextseek_api/sampletypes/{sample_id}/child_types/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="child_types",
          note="despite the prefix the path segment is a SAMPLE id or UID, not a "
               "sample-type one"),
    Route(pattern=r"^nextseek_api/^^sops/$", path="/nextseek_api/sops/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data", note="also accepts POST, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^sops/(?P<uid>[^/]+)/$",
          path="/nextseek_api/sops/{sop_id}/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data", note="also accepts PATCH, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^studies/$", path="/nextseek_api/studies/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data", note="also accepts POST, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^studies/(?P<uid>[^/]+)/$",
          path="/nextseek_api/studies/{study_id}/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          shape="data", note="also accepts PATCH, which the write lane sends on local and dev"),
    Route(pattern=r"^nextseek_api/^^users/$", path="/nextseek_api/users/",
          methods=("GET",), profiles="local,dev", auth="write", expect=200,
          note="superuser only, so the expectation is by inspection. Also accepts POST"),
    Route(pattern=r"^nextseek_api/^^users/(?P<uid>[^/]+)/$",
          path="/nextseek_api/users/" + _NO_SUCH_ID + "/",
          methods=("GET",), profiles="local,dev", auth="write", expect=404,
          note="unknown id: proves the route resolves and denies. Superuser only, so "
               "the expectation is by inspection"),
    Route(pattern=r"^nextseek_api/^redoc/$", path="/nextseek_api/redoc/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          note="the ReDoc UI renders HTML only, and T0 sends Accept: */*, which "
               "content negotiation answers with that document. A client pinned to "
               "Accept: application/json gets a 406 here instead"),
    Route(pattern=r"^nextseek_api/^schema/$", path="/nextseek_api/schema/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          note="drf-spectacular walks every annotated endpoint; validates 67 paths at once"),
    Route(pattern=r"^nextseek_api/^swagger/$", path="/nextseek_api/swagger/",
          methods=("GET",), profiles="local,dev,prod", auth="smoke", expect=200,
          note="the Swagger UI renders HTML only, and T0 sends Accept: */*, which "
               "content negotiation answers with that document. A client pinned to "
               "Accept: application/json gets a 406 here instead"),

    # ----------------------------------------------------------------- #
    # nextseek_api: writes
    # ----------------------------------------------------------------- #
    Route(pattern=r"^nextseek_api/^^admin/project-export/run/$",
          path="/nextseek_api/admin/project-export/run/",
          methods=("POST",), profiles="local,dev", auth="write", expect=200),
    Route(pattern=r"^nextseek_api/^^admin/samples/retrieve/$",
          path="/nextseek_api/admin/samples/retrieve/",
          methods=("POST",), profiles="local,dev", auth="write", expect=200,
          note="builds a sample workbook, so it stays off prod"),
    Route(pattern=r"^nextseek_api/^^assay-registrations/$",
          path="/nextseek_api/assay-registrations/",
          methods=("POST",), profiles="local,dev", auth="write", expect=(200, 409),
          note="one of the five safe previews: dry_run returns the full plan and "
               "persists nothing"),
    Route(pattern=r"^nextseek_api/^^assay-registrations/jobs/(?P<job_id>[^/.]+)/cancel/$",
          path="/nextseek_api/assay-registrations/jobs/" + _NO_SUCH_UUID + "/cancel/",
          methods=("POST",), profiles="local,dev", auth="write", expect=404,
          note="unknown id: proves the route resolves and denies"),
    Route(pattern=r"^nextseek_api/^^assistant/build-upload-xlsx/$",
          path="/nextseek_api/assistant/build-upload-xlsx/",
          methods=("POST",), profiles="local,dev", auth="smoke", expect=200,
          note="deterministic workbook renderer, no model call; writes files under "
               "the run root, so it stays off prod"),
    Route(pattern=r"^nextseek_api/^^attributes/batch-create/$",
          path="/nextseek_api/attributes/batch-create/",
          methods=("POST",), profiles="local,dev", auth="write", expect=(200, 202),
          note="one of the five safe previews: dry_run changes no sample rows"),
    Route(pattern=r"^nextseek_api/^^attributes/batch-delete/$",
          path="/nextseek_api/attributes/batch-delete/",
          methods=("POST",), profiles="local,dev", auth="write", expect=(200, 202),
          note="one of the five safe previews: dry_run changes no sample rows"),
    Route(pattern=r"^nextseek_api/^^attributes/batch-patch/$",
          path="/nextseek_api/attributes/batch-patch/",
          methods=("PATCH",), profiles="local,dev", auth="write", expect=(200, 202),
          note="one of the five safe previews: dry_run changes no sample rows"),
    Route(pattern=r"^nextseek_api/^^attributes/jobs/(?P<job_id>[^/.]+)/cancel/$",
          path="/nextseek_api/attributes/jobs/" + _NO_SUCH_UUID + "/cancel/",
          methods=("POST",), profiles="local,dev", auth="write", expect=404,
          note="unknown id: proves the route resolves and denies"),
    Route(pattern=r"^nextseek_api/^^attributes/search/$",
          path="/nextseek_api/attributes/search/",
          methods=("POST",), profiles="local,dev", auth="smoke", expect=200,
          shape="attributes",
          note="a read expressed as a POST, so the prod guard refuses it; the write "
               "lane uses it to build and then verify its requests"),
    Route(pattern=r"^nextseek_api/^^batch-upload/cancel/(?P<job_id>[^/.]+)/$",
          path="/nextseek_api/batch-upload/cancel/" + _NO_SUCH_UUID + "/",
          methods=("DELETE",), profiles="local,dev", auth="smoke", expect=404,
          note="unknown id: proves the route resolves and denies"),
    Route(pattern=r"^nextseek_api/^^batch-upload/start/$",
          path="/nextseek_api/batch-upload/start/",
          methods=("POST",), profiles="local,dev", auth="smoke", expect=202,
          note="starts a real ingest and returns the Celery job id; the destructive "
               "lane owns it. 202 by inspection at "
               "nextseek_api/batch_upload/views.py::BatchUploadViewSet.start; unprobed"),
    Route(pattern=r"^nextseek_api/^^batch-upload/validate/$",
          path="/nextseek_api/batch-upload/validate/",
          methods=("POST",), profiles="local,dev", auth="smoke", expect=200,
          note="the fifth safe preview: a separate action rather than a dry_run flag"),
    Route(pattern=r"^nextseek_api/^^cc-assistant/upload/$",
          path="/nextseek_api/cc-assistant/upload/",
          methods=("POST",), profiles="local,dev", auth="smoke", expect=202,
          note="queues the upload and returns the job id. 202 by inspection at "
               "nextseek_api/services/cc_assistant.py::CCAssistantViewSet.upload; "
               "unprobed"),
    Route(pattern=r"^nextseek_api/^^data_files/download/$",
          path="/nextseek_api/data_files/download/",
          methods=("POST",), profiles="local,dev", auth="smoke", expect=200),
    Route(pattern=r"^nextseek_api/^^entity_tree/lineage/$",
          path="/nextseek_api/entity_tree/lineage/",
          methods=("POST",), profiles="local,dev", auth="smoke", expect=200,
          note="a graph read expressed as a POST, so the prod guard refuses it"),
    Route(pattern=r"^nextseek_api/^^sample_types/get_parents/parents_by_child_types/$",
          path="/nextseek_api/sample_types/get_parents/parents_by_child_types/",
          methods=("POST",), profiles="local,dev", auth="smoke", expect=200,
          note="a read expressed as a POST, so the prod guard refuses it"),
    Route(pattern=r"^nextseek_api/^^samples/$", path="/nextseek_api/samples/",
          methods=("POST",), profiles="local,dev", auth="smoke", expect=(200, 201),
          note="create only; the list action is not registered on this viewset. The "
               "proxy passes SEEK's own status through "
               "(nextseek_api/services/samples.py::SampleProxyViewSet.create), so "
               "either is correct. By inspection; unprobed"),
    Route(pattern=r"^nextseek_api/^^samples/advanced_search/$",
          path="/nextseek_api/samples/advanced_search/",
          methods=("POST",), profiles="local,dev", auth="smoke", expect=200,
          note="a search expressed as a POST, so the prod guard refuses it"),
    Route(pattern=r"^nextseek_api/^^schema_rag/ingest/$",
          path="/nextseek_api/schema_rag/ingest/",
          methods=("POST",), profiles="local,dev", auth="smoke", expect=201,
          note="201 by inspection at "
               "nextseek_api/services/schema_rag.py::SchemaRAGViewSet.ingest; unprobed"),
    Route(pattern=r"^nextseek_api/^^schema_rag/retrieve/$",
          path="/nextseek_api/schema_rag/retrieve/",
          methods=("POST",), profiles="local,dev", auth="smoke", expect=200,
          note="returns 200 unconditionally; every failure is encoded in "
               "body['debug']['error_code']"),
    Route(pattern=r"^nextseek_api/^^sops/download/$", path="/nextseek_api/sops/download/",
          methods=("POST",), profiles="local,dev", auth="smoke", expect=200),

    # ----------------------------------------------------------------- #
    # nextseek_api: excluded
    # ----------------------------------------------------------------- #
    Route(pattern=r"^nextseek_api/^^assistant/api-read/$", path=None,
          methods=(), profiles="", auth="smoke", exclude="EXCLUDE_COST",
          note="granular assistant op: build an API request from a parser plan and "
               "execute a read-safe call"),
    Route(pattern=r"^nextseek_api/^^assistant/api-write/$", path=None,
          methods=(), profiles="", auth="smoke", exclude="EXCLUDE_COST",
          note="granular assistant op: execute a write API call from a parser plan"),
    Route(pattern=r"^nextseek_api/^^assistant/entity/$", path=None,
          methods=(), profiles="", auth="smoke", exclude="EXCLUDE_COST",
          note="granular assistant op: entity extraction"),
    Route(pattern=r"^nextseek_api/^^assistant/generate-submission/$", path=None,
          methods=(), profiles="", auth="smoke", exclude="EXCLUDE_COST",
          note="granular assistant op: generate a GEO/SRA/PRIDE/nf-core submission report"),
    Route(pattern=r"^nextseek_api/^^assistant/graph/$", path=None,
          methods=(), profiles="", auth="smoke", exclude="EXCLUDE_COST",
          note="granular assistant op: build a Cypher plan and execute it"),
    Route(pattern=r"^nextseek_api/^^assistant/parse/$", path=None,
          methods=(), profiles="", auth="smoke", exclude="EXCLUDE_COST",
          note="granular assistant op: build a parser plan"),
    Route(pattern=r"^nextseek_api/^^assistant/query/$", path=None,
          methods=(), profiles="", auth="smoke", exclude="EXCLUDE_COST",
          note="the synchronous assistant chat turn"),
    Route(pattern=r"^nextseek_api/^^assistant/query/async/$", path=None,
          methods=(), profiles="", auth="smoke", exclude="EXCLUDE_COST",
          note="the asynchronous assistant chat turn"),
    Route(pattern=r"^nextseek_api/^^assistant/report/$", path=None,
          methods=(), profiles="", auth="smoke", exclude="EXCLUDE_COST",
          note="granular assistant op: reporter summary"),
    Route(pattern=r"^nextseek_api/^^assistant/run-ls/$", path=None,
          methods=(), profiles="", auth="smoke", exclude="EXCLUDE_EXTERNAL",
          note="recursive listing of a finished Luria run directory over SSH"),
    Route(pattern=r"^nextseek_api/^^cc-assistant/cc/query/async/$", path=None,
          methods=(), profiles="", auth="smoke", exclude="EXCLUDE_COST",
          note="the chat turn forced onto the Container-CC engine"),
    Route(pattern=r"^nextseek_api/^^cc-assistant/query/async/$", path=None,
          methods=(), profiles="", auth="smoke", exclude="EXCLUDE_COST",
          note="the router-dispatched chat turn; either engine may answer it"),
    Route(pattern=r"^nextseek_api/^^evaluator/retry/$", path=None,
          methods=(), profiles="", auth="write", exclude="EXCLUDE_COST",
          note="re-runs a recorded query through the whole assistant pipeline"),
]


def _check_unique_patterns(routes: list[Route]) -> None:
    """Refuse two entries carrying the same pattern.

    Every route is declared exactly once, so a repeated pattern is a declaration bug:
    the second entry's profiles, methods and exclusions are silently unreachable
    through match(). Called on REGISTRY at import, and callable on any list so a test
    can assert it of the populated registry.
    """
    seen: set[str] = set()
    duplicates: set[str] = set()
    for route in routes:
        if route.pattern in seen:
            duplicates.add(route.pattern)
        seen.add(route.pattern)
    if duplicates:
        raise ValueError(
            f"duplicate pattern(s) in the registry: {sorted(duplicates)}"
        )


def _specificity(route: Route) -> tuple[bool, int]:
    """Rank key for two patterns that both match the same path. Higher wins."""
    return (route.is_exact, route.literal_length)


def match(url: str) -> Route | None:
    """Return the Route for a URL or path, or None when nothing is declared."""
    url_path = urlsplit(url).path or "/"
    hits = [route for route in REGISTRY if route.matches(url_path)]
    if not hits:
        return None
    # Patterns overlap. '^login' matches '/login/special/' as surely as
    # '^login/special/$' does, and a viewset's detail route '[^/.]+' swallows the path
    # of its own list-level action. Take the most specific: the pattern that pins the
    # whole path first, then the one spelling out the most literal characters, which
    # puts the action above the detail route the way Django's resolver does. Two
    # OVERLAPPING patterns of EQUAL specificity would fall back on declaration order;
    # the registry holds none, and identical ones are refused by
    # _check_unique_patterns.
    return max(hits, key=_specificity)


_check_unique_patterns(REGISTRY)
