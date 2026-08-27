"""Every relative import inside ``seek/`` must resolve — module *and* name.

This exists because the same defect shipped twice. When ``seek/views.py`` and
``seek/dbtable_sample.py`` became packages, the modules moved one directory
deeper, so a ``from .models import X`` written for ``seek/dbtable_sample.py``
started meaning ``seek.sample.models`` instead of ``seek.models``.

Nothing else catches it:

* ``compileall`` and ruff are happy — the syntax is valid and ruff does not
  resolve module paths.
* Importing the package does not catch it either, when the offending import sits
  **inside a function body**. A deferred import only runs when its function runs,
  which in the cases that shipped meant a batch upload or an advanced search.
* Two of the six broken imports pointed at a module that genuinely *exists*
  (``seek/sample/search.py``), just not the intended one. A check that stops at
  "does the module resolve" passes those. The failure is at the **name**.

So this walks every module's whole AST, not just its top level, resolves each
relative import against the filesystem, and then confirms the imported name is
actually defined in the target.
"""

import ast

import pytest

from .discovery import SEEK_ROOT

PACKAGE_ROOT = SEEK_ROOT.parent


def _python_files():
    for path in sorted(SEEK_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or "migrations" in path.parts:
            continue
        yield path


def _relative_imports():
    """(file, node) for every `from .x import y`, at any nesting depth."""
    for path in _python_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                yield path, node


def _resolve(path, node):
    """The file a relative import names, or None."""
    base = path.parent
    for _ in range(node.level - 1):
        base = base.parent
    if not node.module:
        return base / "__init__.py"
    candidate = base.joinpath(*node.module.split("."))
    module = candidate.with_suffix(".py")
    if module.exists():
        return module
    package = candidate / "__init__.py"
    return package if package.exists() else None


def _exported(path):
    """Names a module provides, `None` if it star-re-exports and cannot be enumerated."""
    tree = ast.parse(path.read_text(), filename=str(path))
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            names |= {(a.asname or a.name.split(".")[0]) for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if any(a.name == "*" for a in node.names):
                return None            # seek/models/__init__.py does this
            names |= {(a.asname or a.name) for a in node.names}
    return names


# Known-broken, documented rather than fixed. `nhp_cache_cli.py` imports
# `get_nhp_data` from `nhp_service`, which has never defined it — the only
# `get_nhp_data` in the tree is a view. The script raises ImportError the moment
# it runs. Pre-existing since before this test's branch; LATENT_BUGS.md #47.
KNOWN_BROKEN = {"seek/timeline/services/nhp_cache_cli.py:8"}


def _case(path, node):
    ident = f"{path.relative_to(PACKAGE_ROOT)}:{node.lineno}"
    marks = ()
    if ident in KNOWN_BROKEN:
        marks = pytest.mark.xfail(
            strict=True,
            reason="LATENT_BUGS #47: nhp_service does not define get_nhp_data",
        )
    return pytest.param(path, node, marks=marks, id=ident)


CASES = [_case(p, n) for p, n in _relative_imports()]


def test_there_are_relative_imports_to_check():
    """Guard against the walk silently finding nothing."""
    assert len(CASES) >= 20, f"only found {len(CASES)} relative imports; is the walk working?"


@pytest.mark.parametrize("path,node", CASES)
def test_relative_import_resolves(path, node):
    dotted = "." * node.level + (node.module or "")
    target = _resolve(path, node)
    assert target is not None, (
        f"{path.relative_to(PACKAGE_ROOT)}:{node.lineno}: `from {dotted} import ...` "
        f"resolves to no module. If this file recently moved into a package, the "
        f"import needs one more dot."
    )
    exported = _exported(target)
    if exported is None:
        return                          # star re-export: cannot enumerate, do not guess
    for alias in node.names:
        if alias.name == "*":
            continue
        # `from . import views` names a submodule, not something __init__ defines
        sibling = target.parent / alias.name
        if sibling.with_suffix(".py").exists() or (sibling / "__init__.py").exists():
            continue
        assert alias.name in exported, (
            f"{path.relative_to(PACKAGE_ROOT)}:{node.lineno}: `from {dotted} import "
            f"{alias.name}` resolves to {target.relative_to(PACKAGE_ROOT)}, which does "
            f"not define {alias.name!r}. A same-named module at the wrong depth is the "
            f"usual cause."
        )
