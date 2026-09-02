#!/usr/bin/env python
"""Print ci/routes.py skeleton entries for every route nobody has declared yet.

The resolver walk itself lives in ci/gate/live_routes.py, which the completeness
gate imports directly; this is only the command line around it. Requires Django,
so run it in the pytest lane or in a container -- see that module's docstring for
the exact command.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    # Without this a hand run dies on a bare ImproperlyConfigured. The gate lane
    # already exports it, and setdefault leaves that export alone.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.test_settings")

    import django

    django.setup()

    from ci.gate.live_routes import live_patterns, suggest_path
    from ci.routes import REGISTRY

    declared = {route.pattern for route in REGISTRY if route.resolver}
    missing = sorted(live_patterns() - declared)
    for pattern in missing:
        print(f'    Route(pattern=r"{pattern}",')
        print(f'          path="{suggest_path(pattern)}",')
        print('          methods=("GET",), profiles="local,dev", auth="smoke", expect=200),')
    print(f"\n# {len(missing)} undeclared route(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
