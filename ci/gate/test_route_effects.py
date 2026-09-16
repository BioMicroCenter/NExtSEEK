"""What a route writes, checked against the writer registry, in both directions.

ci/routes.py says what each route does to the graph's sources (`effect`) and, for
a route that writes one, which writers of ci/writers.py do it (`writers`). This
module is the other half of that claim:

  * every id a route names is a writer that exists;
  * every writer this repository reaches through a URL is named by some route, so
    a hooked writer cannot quietly lose the route that enters it;
  * live_views() resolves each owned pattern to the view code behind it;
  * and a TRIPWIRE, which only reports: a route declared `reads` whose view
    reaches a writer site through statically resolvable calls. It is report-only
    because the walk is a name-resolving heuristic, not a call graph: it follows a
    call only where exactly one function of that name exists in the scanned tree,
    so it under-reaches by design (an overloaded name, a call through a variable,
    a string dispatch, a decorator that does not set __wrapped__) and can still
    over-reach where one name is reached by a path no request takes. One assertion
    is made on it, and it is about a page whose view is known to read only.

Standard library, pytest, and Django for live_views(): this is the gate lane.
"""
import ast
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ci import writers  # noqa: E402
from ci.gate import writer_scan  # noqa: E402
from ci.gate.live_routes import live_patterns, live_views  # noqa: E402
from ci.routes import REGISTRY  # noqa: E402

GRAPH_SEARCH_PAGE = r"^seek/^graph/search/"

# Writers with a hook that no URL enters: the entry point is a command line, not a
# route. Everything else that calls a hook is reached through a route, so it must
# be named by one.
NOT_ENTERED_BY_A_ROUTE = frozenset({
    "WR-16",   # manage.py backfill_publication_attributes --apply
})

# The tripwire's bounds. Depth is how many calls it follows from a view, and
# MAX_SITES stops one view walking the whole tree.
#
# Two bounds decide whether a call is evidence, and both were measured on this
# tree rather than guessed:
#
#   MAX_DEFINITIONS = 1. A call is followed only when exactly ONE function of that
#   name exists in the scanned tree. At 12, the walk reported almost every /seek/
#   page as reaching WR-14 and WR-15, because the legacy table classes spell their
#   writes `new`, `update` and `delete`, and any call of those names landed on
#   them.
#
#   MAX_CALLERS = 20. A uniquely-named function that a fifth of a hundred sites
#   call is a PROTOCOL method that one function in this tree happens to share a
#   name with, not a function those sites call. `execute` is the measured case:
#   158 sites call something `.execute(...)`, nearly all of them a database
#   cursor, and the tree holds exactly one function called `execute` -- assay
#   registration's. Following it connected every read-only SQL view to batch
#   upload's inserts and produced six hits that were all the same bridge.
#
# Distinctive names -- _storeSample, resolve_orphans, bulk_update_samples -- are
# what the walk is for, and they sit far below both bounds.
MAX_DEPTH = 3
MAX_DEFINITIONS = 1
MAX_CALLERS = 20
MAX_SITES = 400

@lru_cache(maxsize=1)
def _index() -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]], dict[str, int]]:
    """(sites by function name, called names by site, callers per name), one pass.

    writer_scan.defined_at answers the first half already, but it re-reads every
    scanned module for each name it is asked about. That is right for the dozen
    hook names the writer registry checks and far too slow for a walk that meets
    hundreds of names, so the walk builds its own index once, over the same files
    (writer_scan.is_scanned decides which) and with the same reading of a function:
    every call in its subtree, unparsed.
    """
    definitions: dict[str, list[str]] = {}
    calls: dict[str, tuple[str, ...]] = {}

    def collect(node, stack: list[str], rel: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                here = stack + [child.name]
                if not isinstance(child, ast.ClassDef):
                    site = f"{rel}::{'.'.join(here)}"
                    definitions.setdefault(child.name, []).append(site)
                    calls[site] = tuple(sorted({
                        ast.unparse(call.func) for call in ast.walk(child)
                        if isinstance(call, ast.Call)
                    }))
                collect(child, here, rel)

    for base in writer_scan.SCAN_ROOTS:
        top = ROOT / base
        if not top.is_dir():
            continue
        for path in sorted(top.rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if not writer_scan.is_scanned(rel):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
                continue
            collect(tree, [], rel)
    popularity: dict[str, int] = {}
    for names in calls.values():
        for name in {call.rsplit(".", 1)[-1] for call in names}:
            popularity[name] = popularity.get(name, 0) + 1
    return {name: tuple(sites) for name, sites in definitions.items()}, calls, popularity


def writers_reached(sites: tuple[str, ...]) -> dict[str, str]:
    """{writer site: writer id} reachable from these view sites, by name.

    A call is resolved by its last dotted part -- `self._storeSample` resolves as
    `_storeSample` -- to the one function of that name in the scanned tree, and is
    dropped where the name names more than one, or where so much of the tree calls
    it that it is plainly a protocol method (the two bounds above). That is the
    resolution the writer registry uses to follow a hook helper to its definition,
    narrowed, and it is why this walk reports rather than fails.
    """
    definitions, calls, popularity = _index()
    found: dict[str, str] = {}
    seen = set(sites)
    frontier = list(sites)
    for site in frontier:
        if site in writers.DECLARED_SITES:
            found[site] = writers.DECLARED_SITES[site]
    for _ in range(MAX_DEPTH):
        nxt: list[str] = []
        for site in frontier:
            if len(seen) > MAX_SITES:
                break
            for call in calls.get(site, ()):
                name = call.rsplit(".", 1)[-1]
                targets = definitions.get(name, ())
                if not targets or len(targets) > MAX_DEFINITIONS:
                    continue
                if popularity.get(name, 0) > MAX_CALLERS:
                    continue
                for target in targets:
                    if target in seen:
                        continue
                    seen.add(target)
                    nxt.append(target)
                    if target in writers.DECLARED_SITES:
                        found[target] = writers.DECLARED_SITES[target]
        frontier = nxt
    return found


def test_every_writer_a_route_names_exists():
    """A typo in an id is a route claiming a hook that nothing implements."""
    unknown = sorted({
        (route.pattern, writer_id)
        for route in REGISTRY
        for writer_id in route.writers
        if writer_id not in writers.BY_ID
    })
    assert not unknown, (
        "\n\n  these routes name writer ids ci/writers.py does not declare:\n\n"
        + "\n".join(f"    {pattern}  {writer_id}" for pattern, writer_id in unknown)
        + "\n"
    )


def test_every_writer_a_route_enters_is_named_by_one():
    """The registry's half of the writer inventory, from the route end.

    A writer that calls a hook is one this repository's own code reaches, and all
    but the command-line ones are reached through a URL. If no route names such a
    writer, either a route lost its classification or the writer lost its entry
    point, and both are worth a red test rather than a reader's assumption.
    """
    named = {writer_id for route in REGISTRY for writer_id in route.writers}
    hooked = {w.id for w in writers.WRITERS if w.hook}
    missing = sorted(hooked - named - NOT_ENTERED_BY_A_ROUTE)
    assert not missing, (
        "\n\n  these writers call a hook but no route names them:\n\n"
        + "\n".join(f"    {writer_id}: {writers.BY_ID[writer_id].note}" for writer_id in missing)
        + "\n\n  Give the route that enters the writer effect=\"writes\" and its id, or, if\n"
          "  nothing reaches it through a URL any more, add it to NOT_ENTERED_BY_A_ROUTE\n"
          "  with the entry point that does.\n"
    )


def test_a_route_named_by_nothing_is_not_silently_carrying_a_stale_id():
    """The other direction: an id named by a route is a writer that still exists
    and still says how the graph hears about it."""
    for route in REGISTRY:
        for writer_id in route.writers:
            writer = writers.BY_ID[writer_id]
            assert writer.hook or writer.reconcile, f"{writer_id} says neither"


def test_live_views_covers_every_owned_pattern():
    """The resolver walk that live_patterns() reports, with the view behind each."""
    assert set(live_views()) == live_patterns()


def test_every_view_site_is_a_file_of_this_repository():
    """A site is 'repo/relative/path.py::symbol', the vocabulary ci/writers.py uses.

    Third-party views (Django's own, Mezzanine's, drf-spectacular's) resolve to no
    site at all rather than to a path outside the tree, so a consumer can read
    every site it gets as a file it can open.
    """
    for pattern, sites in live_views().items():
        for site in sites:
            path, _, symbol = site.partition("::")
            assert symbol, f"{pattern}: {site} names no symbol"
            assert (ROOT / path).is_file(), f"{pattern}: {site} is not a file of this tree"


def test_the_graph_search_page_resolves_to_its_own_view_and_reads():
    """The page dev-graph added: spec 19.1 declares it read-only, and this pins it."""
    assert live_views()[GRAPH_SEARCH_PAGE] == ("seek/views/search.py::graphSearch",)
    page = [r for r in REGISTRY if r.pattern == GRAPH_SEARCH_PAGE]
    assert [(r.effect, r.writers) for r in page] == [("reads", ())]


def test_the_tripwire_reports_reads_routes_that_reach_a_writer():
    """Report-only, with one assertion. See this module's docstring for why.

    The Graph Search page is the assertion because its view is known to read only:
    it asks DBtable_sampletype for the type list, which is a SELECT through
    DBtable.getComboboxOptions, and renders. A walk that reports that page is
    over-reaching badly enough that its output would not be worth reading.
    """
    views = live_views()
    listed: dict[str, list[str]] = {}
    for route in REGISTRY:
        if route.effect != "reads":
            continue
        reached = writers_reached(views.get(route.pattern, ()))
        if reached:
            listed[route.pattern] = sorted(set(reached.values()))
    if listed:
        print(f"\ntripwire: {len(listed)} route(s) declared 'reads' reach a writer site")
        for pattern in sorted(listed):
            print(f"  {pattern}  ->  {', '.join(listed[pattern])}")
    assert GRAPH_SEARCH_PAGE not in listed, (
        f"the tripwire reports the Graph Search page as reaching {listed.get(GRAPH_SEARCH_PAGE)}, "
        "which its view does not: the walk has become too loose to read"
    )
