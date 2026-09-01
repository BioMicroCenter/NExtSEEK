r"""Walk Django's URL resolver and report the patterns CI is responsible for.

Two callers:

  * ci/gate/test_route_registry.py diffs live_patterns() against ci.routes.REGISTRY
  * scripts/dump_routes.py prints registry skeleton entries for whatever is missing

Django is imported inside the functions that need it, so suggest_path() stays a
pure string helper that any environment can import.

Running it locally
------------------
The container's /app is baked from its own checkout, and mysqlclient does not
build on the host, so run a throwaway container over this worktree instead::

    mkdir -p schema_rag/duckdb schema_rag/embedding_models
    docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
      -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
      -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
      /app/.venv/bin/python -m pytest ci/gate -q -p no:cacheprovider

Swap the last line for ``/app/.venv/bin/python scripts/dump_routes.py`` to print
the skeleton. The two schema_rag directories exist because settings.py creates
them at import time and the mount is read-only; they are empty, so git ignores
them. About 11 seconds either way.
"""
from __future__ import annotations

import re

# What CI owns. Everything else is excluded from the denominator entirely rather
# than declared, because it is third-party surface: the Django admin, Mezzanine's
# CMS catch-all at the URL root, and the format-suffix duplicate a DRF router
# generates for every route it already emitted.
IGNORE_PREFIXES = ("admin/", "^admin/")

OWNED_PREFIXES = ("nextseek_api/", "^nextseek_api/", "seek/", "^seek/")

# Project-level routes CI owns. Anything else at the URL root belongs to Mezzanine.
# '^logout$' keeps its anchor: that is the pattern the resolver reports, and the
# diff is by exact string.
_PROJECT_LEVEL = {
    "^$",
    "^login",
    "^logout$",
    "^signup/",
    "^accounts/login/",
    "^accounts/signup/",
    "^media/(?P<path>.*)$",
}

# '(?P<name>...)' with no parenthesis of its own inside, which is every named
# group this URLconf spells.
_NAMED_GROUP = re.compile(r"\(\?P<(\w+)>[^()]*\)")


def _is_format_suffix(pattern: str) -> bool:
    """A DRF router's '.json' twin of a route it already generated.

    A router emits the suffix twin two ways: a regex group named 'format', and,
    for its own root view, the path converter '<drf_format_suffix:format>'.
    """
    return "(?P<format>" in pattern or "drf_format_suffix" in pattern


def suggest_path(pattern: str) -> str:
    """The registry 'path' form of a resolver pattern: a requestable URL path.

    Named groups become '{name}' placeholders for a human to fill in, and the
    anchors go, the same way ci.routes.Route.matcher strips them -- a '^' that
    negates a character class is not an anchor.
    """
    body = _NAMED_GROUP.sub(lambda m: "{" + m.group(1) + "}", pattern)
    body = re.sub(r"(?<!\[)\^", "", body)   # anchors, not class negations
    body = re.sub(r"(?<!\\)\$", "", body)   # anchors, not literal dollars
    return "/" + body.lstrip("/")


def _walk(resolver, prefix: str = ""):
    """Yield (pattern, converter) for every leaf under a resolver.

    'pattern' is the include() prefixes and the leaf concatenated, which is how
    ci.routes declares a route. 'converter' is the leaf's own route string when
    that leaf came from path() and spells a '<converter>', and None otherwise;
    live_patterns() refuses to hand one to the stdlib matcher.
    """
    from django.urls.resolvers import RoutePattern

    for entry in resolver.url_patterns:
        if hasattr(entry, "url_patterns"):
            yield from _walk(entry, prefix + str(entry.pattern))
            continue
        text = str(entry.pattern)
        converter = None
        if isinstance(entry.pattern, RoutePattern):
            # Django anchors every path() endpoint with '\Z' but str() drops it,
            # and a pattern with no terminal '$' is a prefix match. Unanchored,
            # the DRF router root '^nextseek_api/^' would swallow every API URL.
            converter = text if "<" in text else None
            text += "$"
        yield prefix + text, converter


def live_patterns() -> set[str]:
    """Every application URL pattern CI is responsible for declaring."""
    from django.urls import get_resolver

    out: set[str] = set()
    for pattern, converter in _walk(get_resolver()):
        if pattern.startswith(IGNORE_PREFIXES):
            continue
        if _is_format_suffix(pattern):
            continue
        if not pattern.startswith(OWNED_PREFIXES) and pattern not in _PROJECT_LEVEL:
            continue
        if converter is not None:
            # ci.routes matches paths with the stdlib re module, which cannot read
            # path() converter syntax, so such an entry would silently never match.
            raise NotImplementedError(
                f"{pattern}: an owned route declared with path() converter syntax "
                f"({converter}). ci.routes.Route.matcher is a plain regex and "
                f"cannot match it; teach it the converters before declaring this."
            )
        out.add(pattern)
    return out
