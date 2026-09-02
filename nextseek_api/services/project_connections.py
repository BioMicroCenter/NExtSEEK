"""The sample-type connection diagram, and template bundles, for one SEEK project.

Calls sampletype_connections' own functions in-process rather than its HTTP
endpoint. That endpoint is [IsAuthenticated, IsSuperUser] and stays that way:
the project page is visible to every member of the project, and reaching the
same code directly means the page's own membership check is the gate, with no
new authorization surface and no change to a shipped endpoint.

`seek_inv_id` is a SEEK *project* id and covers every investigation in that
project, which is exactly the project page's scope. That module's docstring
states it; this one restates it because the parameter name does not.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.core.cache import cache

from nextseek_api.models import SampleTypeConnectionsRequest
from nextseek_api.services.sampletype_connections import (
    fetch_clade_map,
    rows_to_html,
    run_connections_query,
)
from seek.models import Project_template_bundles

logger = logging.getLogger(__name__)

CACHE_KEY_TEMPLATE = "stconn:html:{project_id}"

# One hour by default. A project's shape changes when someone uploads a new kind
# of sample, which is a weekly event at most, so an hour is already fresher than
# the data.
DEFAULT_TTL = 3600


def _ttl() -> int:
    return int(getattr(settings, "PROJECT_CONNECTIONS_CACHE_SECONDS", DEFAULT_TTL))


def connection_rows(project_id: int) -> list[dict]:
    """(parent_type, child_type, internal_assay, n_edges) for one project.

    Uncached, because its two callers want different things from it: the diagram
    caches the rendered HTML, while the bundle fallback and the types-in-use list
    run inside a page render that is already cheap.

    Never raises. Neo4j being unreachable costs the diagram, not the page.
    """
    try:
        selector = SampleTypeConnectionsRequest.model_validate({
            "seek_inv_id": int(project_id),
            "graph_inv_id": None,
            "name": None,
            "sample_type": None,
            "direct_connections": False,
            "all_conns": False,
            "layout": None,
            "output_format": "html",
        })
        return run_connections_query(selector)
    except Exception:
        logger.exception("connection rows unavailable for project_id=%s", project_id)
        return []


def connections_html(project_id: int, title: str = "Sample type connections") -> str:
    """The rendered diagram for one project, cached. Empty string when there is none.

    An empty result is NOT cached. Zero rows means either a genuinely empty
    project or Neo4j being unreachable, and the two are indistinguishable here.
    Caching the second for an hour would turn a momentary blip into an outage the
    page cannot recover from until the TTL expires.
    """
    key = CACHE_KEY_TEMPLATE.format(project_id=int(project_id))
    cached = cache.get(key)
    if cached is not None:
        return cached

    rows = connection_rows(project_id)
    if not rows:
        return ""

    html = rows_to_html(rows, fetch_clade_map(), title=title)
    cache.set(key, html, _ttl())
    return html


def types_in_use(rows) -> list[str]:
    """Every sample type on either end of this project's connection edges, sorted."""
    seen = set()
    for row in rows or []:
        for key in ("parent_sample_type", "child_sample_type"):
            code = row.get(key)
            if code:
                seen.add(code)
    return sorted(seen)


def _curated_bundles(project_id: int) -> list[dict]:
    """Hand-curated bundles for one project, in display order. [] when none.

    Its own function so tests can replace the database without replacing the
    curation-beats-fallback rule that project_bundles applies.
    """
    try:
        rows = list(
            Project_template_bundles.objects
            .filter(project_id=int(project_id))
            .order_by("position", "id")
            .values("label", "codes")
        )
    except Exception:
        logger.exception("project_template_bundles unavailable for project_id=%s", project_id)
        return []

    out = []
    for row in rows:
        try:
            codes = json.loads(row.get("codes") or "[]")
        except Exception:
            logger.warning("bundle %r on project %s has unparseable codes; skipped",
                           row.get("label"), project_id)
            continue
        if isinstance(codes, list):
            out.append({"label": row.get("label") or "", "codes": [str(c) for c in codes]})
    return out


def _derived_bundles(rows, known_codes) -> list[dict]:
    """One bundle per internal assay this project actually uses.

    The codes are the parent and child types that appear on THAT assay's edges in
    THIS project, which is what makes the fallback project-specific rather than a
    restatement of assay_context. Ordered by label so the strip is stable between
    page loads.
    """
    by_assay: dict[str, list[str]] = {}
    for row in rows or []:
        assay = row.get("internal_assay")
        if not assay:
            continue
        bucket = by_assay.setdefault(assay, [])
        for key in ("parent_sample_type", "child_sample_type"):
            code = row.get(key)
            if code and code in known_codes and code not in bucket:
                bucket.append(code)
    return [{"label": assay, "codes": codes}
            for assay, codes in sorted(by_assay.items()) if codes]


def project_bundles(project_id: int, rows, known_codes) -> list[dict]:
    """Named one-click template downloads for one project.

    Curation wins OUTRIGHT where it exists: a curated project gets exactly its
    curated bundles and none of the derived ones. Merging the two would mean a
    curator could not suppress a bundle, only add to it, and the point of
    curating is to choose.

    A code the instance does not have is dropped, the same rule
    templatesDownload already applies to a stale bookmark, and a bundle emptied
    by that drop is not returned at all.
    """
    curated = _curated_bundles(project_id)
    bundles = curated if curated else _derived_bundles(rows, known_codes)

    out = []
    for bundle in bundles:
        codes = [c for c in bundle["codes"] if c in known_codes]
        if codes:
            out.append({"label": bundle["label"], "codes": codes})
    return out
