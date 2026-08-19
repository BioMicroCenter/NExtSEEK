"""Every module under ``seek/`` must import cleanly -- with no exceptions.

The cheapest possible regression net: it catches a deleted module that something
still imports, a moved helper, a circular import introduced by splitting a file.
There is no other coverage of this package, so this test is the floor.

This started out with five ``xfail`` marks for modules that could not be imported
at all (Python-2 ``urllib2`` imports, FastAPI routers with no FastAPI). Plan Steps
3 and 6 deleted all five, so the invariant is now unconditional -- which is the
stronger statement, and the reason the marks were ``strict=True``: a stale mark
would have failed as XPASS rather than quietly outliving its reason. If a future
step ever needs one back, wrap the name in
``pytest.param(name, marks=pytest.mark.xfail(raises=ImportError, strict=True,
reason=...))`` and say which step removes it.
"""

import importlib

import pytest

from seek.tests.discovery import module_names

MODULES = module_names()


def test_discovery_found_the_package():
    """Guard against the walk silently finding nothing, or half of it.

    A bare count would have to be edited by every step that deletes a module, so
    it is a loose floor. The sentinels are the assertion with teeth: the timeline
    one is what catches the ``pkgutil.walk_packages`` failure mode described in
    ``discovery.py``, where a subpackage is skipped and the suite still passes.
    """
    assert len(MODULES) >= 30, MODULES
    for sentinel in ("seek.views", "seek.models", "seek.timeline.core.database"):
        assert sentinel in MODULES, f"{sentinel} missing from {MODULES}"


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    importlib.import_module(name)
