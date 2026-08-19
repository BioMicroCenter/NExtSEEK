"""Module discovery shared by the smoke tests.

Deliberately walks the filesystem rather than using ``pkgutil.walk_packages``:
``seek/timeline/`` has no ``__init__.py``, so pkgutil does not descend into it
and all 10 timeline modules would be silently skipped.
"""

from pathlib import Path

import seek

SEEK_ROOT = Path(seek.__file__).parent


def module_names(subpackage=""):
    """Dotted names of every importable module under ``seek/``.

    ``seek.tests`` is excluded (importing the test package from within itself is
    pointless) as is the ``nhp_cache_cli`` script, whose module-level
    ``logging.basicConfig`` writes a log file into the working directory as a
    side effect of being imported.

    Pass ``subpackage`` to restrict the walk, e.g. ``"timeline"``.
    """
    root = SEEK_ROOT / subpackage if subpackage else SEEK_ROOT
    names = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(SEEK_ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
            if not parts:
                continue
        name = "seek." + ".".join(parts)
        if name == "seek.tests" or name.startswith("seek.tests."):
            continue
        if name == "seek.timeline.services.nhp_cache_cli":
            continue
        names.append(name)
    return names
