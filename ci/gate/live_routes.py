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

import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

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

    Only FLAT named groups are substituted. Anything else -- an unnamed group, a
    bare wildcard, a named group with a group of its own inside -- passes through
    verbatim for the author of the entry to replace by hand.
    """
    body = _NAMED_GROUP.sub(lambda m: "{" + m.group(1) + "}", pattern)
    body = re.sub(r"(?<!\[)\^", "", body)   # anchors, not class negations
    body = re.sub(r"(?<!\\)\$", "", body)   # anchors, not literal dollars
    return "/" + body.lstrip("/")


def _walk(resolver, prefix: str = "", converter: str | None = None):
    """Yield (pattern, converter, entry) for every leaf under a resolver.

    'pattern' is the include() prefixes and the leaf concatenated, which is how
    ci.routes declares a route. 'converter' is the route string of the first
    path() component on the way down that spells a '<converter>' -- the leaf
    itself, or any include() prefix above it, since a prefix lands in the
    concatenated pattern just as surely as the leaf does -- and None otherwise;
    live_patterns() refuses to hand one to the stdlib matcher. 'entry' is the
    resolver's own URLPattern, which is what live_views() reads the view off.
    """
    from django.urls.resolvers import RoutePattern

    for entry in resolver.url_patterns:
        text = str(entry.pattern)
        found = converter
        if found is None and isinstance(entry.pattern, RoutePattern) and "<" in text:
            found = text
        if hasattr(entry, "url_patterns"):
            yield from _walk(entry, prefix + text, found)
            continue
        if isinstance(entry.pattern, RoutePattern):
            # Django anchors every path() endpoint with '\Z' but str() drops it,
            # and a pattern with no terminal '$' is a prefix match. Unanchored,
            # the DRF router root '^nextseek_api/^' would swallow every API URL.
            text += "$"
        yield prefix + text, found, entry


def _owned_leaves():
    """Yield (pattern, entry) for every leaf CI owns. The one resolver walk."""
    from django.urls import get_resolver

    for pattern, converter, entry in _walk(get_resolver()):
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
        yield pattern, entry


def live_patterns() -> set[str]:
    """Every application URL pattern CI is responsible for declaring."""
    return {pattern for pattern, _ in _owned_leaves()}


# The handlers a class-based view may define. Read off the class rather than
# guessed, so a view that answers only POST contributes only its POST.
_HTTP_HANDLERS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")


def _site(obj) -> str | None:
    """'repo/relative/path.py::Symbol' for a function or class of this tree, else None.

    The same vocabulary ci/writers.py and ci/gate/writer_scan.py use, so a caller
    can go from a route straight to the writer registry. Decorated views are
    unwrapped first: seek/decorators.py wraps with functools.wraps, so the
    original function is one __wrapped__ away, and without the unwrap every
    decorated page would resolve to the decorator's own module.
    """
    target = inspect.unwrap(obj)
    try:
        file = inspect.getsourcefile(target) or inspect.getfile(target)
    except TypeError:
        return None
    if not file:
        return None
    try:
        rel = Path(file).resolve().relative_to(ROOT)
    except ValueError:
        return None      # Django's own views, Mezzanine's, drf-spectacular's
    name = getattr(target, "__qualname__", "") or getattr(target, "__name__", "")
    return f"{rel.as_posix()}::{name}" if name else None


def _view_sites(callback) -> tuple[str, ...]:
    """Every site of this repository's code that one URL dispatches to."""
    cls = getattr(callback, "cls", None)                 # DRF, APIView.as_view
    actions = getattr(callback, "actions", None)         # DRF, ViewSetMixin.as_view
    holder = cls if cls is not None else getattr(callback, "view_class", None)
    funcs = []
    if holder is not None:
        names = (list(dict.fromkeys(actions.values())) if actions
                 else [m for m in _HTTP_HANDLERS if callable(getattr(holder, m, None))])
        funcs = [getattr(holder, name) for name in names if callable(getattr(holder, name, None))]
    else:
        funcs = [callback]
    sites = [site for site in (_site(func) for func in funcs) if site]
    if not sites and holder is not None:
        # rest_framework's @api_view builds a throwaway APIView class whose
        # handlers are the library's own `handler`, so nothing above resolves;
        # it does copy the decorated function's __name__ and __module__ onto the
        # class, which is enough to name the function in its own module.
        module = sys.modules.get(getattr(holder, "__module__", "") or "")
        file = getattr(module, "__file__", None)
        name = getattr(holder, "__name__", "")
        if file and name:
            try:
                rel = Path(file).resolve().relative_to(ROOT)
            except ValueError:
                return ()
            return (f"{rel.as_posix()}::{name}",)
    return tuple(dict.fromkeys(sites))


def live_views() -> dict[str, tuple[str, ...]]:
    """Every owned pattern, with the sites of this repository's code behind it.

    A viewset contributes one site per action the URL dispatches to, a
    class-based view one per handler it defines, a function view itself. A
    third-party view contributes none: it is outside this tree, and nothing here
    declares what it writes. ci/gate/test_route_effects.py walks from these sites
    to the writer sites of ci/writers.py.
    """
    out: dict[str, tuple[str, ...]] = {}
    for pattern, entry in _owned_leaves():
        sites = out.get(pattern, ()) + _view_sites(entry.callback)
        out[pattern] = tuple(dict.fromkeys(sites))
    return out
