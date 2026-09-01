"""The completeness gate. Runs in the pytest lane, where Django is importable.

This is the one test in an otherwise informational job that BLOCKS. It is
deterministic and new, so it carries none of the "red on run one" risk that
made the rest of that job informational.

Both tests diff only the resolver-owned half of the registry. An entry with
resolver=False is a URL CI requests that Django's resolver does not report
(an nginx-served asset, a route served by another app), so it is neither
missing from the registry nor stale in it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci.gate.live_routes import live_patterns, suggest_path
from ci.routes import REGISTRY

SUGGESTION = '''    Route(pattern=r"{pattern}",
          path="{path}",
          methods=("GET",), profiles="local,dev", auth="smoke", expect=200)'''


def _suggest(pattern: str) -> str:
    return SUGGESTION.format(pattern=pattern, path=suggest_path(pattern))


def test_every_route_is_registered():
    live = live_patterns()
    declared = {r.pattern for r in REGISTRY if r.resolver}
    missing = sorted(live - declared)
    assert not missing, (
        f"\n\n  {len(missing)} route(s) are not declared in ci/routes.py:\n\n"
        + "\n".join(f"    {p}" for p in missing)
        + "\n\n  Add each one, or declare it excluded with a category code:\n\n"
        + "\n\n".join(_suggest(p) for p in missing[:3])
        + "\n"
    )


def test_no_stale_registry_entries():
    live = live_patterns()
    declared = {r.pattern for r in REGISTRY if r.resolver}
    stale = sorted(declared - live)
    assert not stale, (
        f"\n\n  {len(stale)} registry entr(ies) no longer match any route:\n\n"
        + "\n".join(f"    {p}" for p in stale)
        + "\n\n  The route was renamed or removed. Update or delete the entry.\n"
    )
