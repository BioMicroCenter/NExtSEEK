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

    @cached_property
    def is_exact(self) -> bool:
        """The pattern pins the whole path rather than matching a prefix of it."""
        return bool(_TAIL_ANCHOR.search(self.pattern))

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


REGISTRY: list[Route] = []


def match(url: str) -> Route | None:
    """Return the Route for a URL or path, or None when nothing is declared."""
    url_path = urlsplit(url).path or "/"
    hits = [route for route in REGISTRY if route.matches(url_path)]
    if not hits:
        return None
    # Prefix patterns overlap: '^login' matches '/login/special/' just as surely as
    # '^login/special/$' does. Take the most specific declaration -- one that pins the
    # whole path first, then the longest -- so declaration order decides nothing.
    return max(hits, key=lambda route: (route.is_exact, len(route.pattern)))
