"""The runtime dependency list holds only what the app imports."""
import ast
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# distribution name (normalised) -> top-level import names
NEVER_IMPORTED = {
    "six": {"six"},
    "future": {"future", "past", "libfuturize", "libpasteurize"},
    "pathlib": set(),  # the stdlib module; the PyPI backport only shadows it
    "jdcal": {"jdcal"},
    "fpdf": {"fpdf"},
    "rdflib": {"rdflib"},
    "sparqlwrapper": {"SPARQLWrapper"},
    "h5py": {"h5py"},
    "python-docx": {"docx"},
    "reportlab": {"reportlab"},
    "django-realtime": {"drealtime"},
    "django-rest-framework": set(),  # an empty shim of djangorestframework
}
DEV_ONLY = {"ruff", "pytest-django", "pymysql"}


def _name(spec):
    return spec.split("[")[0].split(">")[0].split("<")[0].split("=")[0].strip().lower().replace("_", "-")


def _project():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_never_imported_packages_are_not_runtime_dependencies():
    declared = {_name(s) for s in _project()["project"]["dependencies"]}
    assert not (declared & set(NEVER_IMPORTED)), sorted(declared & set(NEVER_IMPORTED))


def test_dev_tools_are_not_runtime_dependencies_but_stay_in_the_dev_group():
    proj = _project()
    declared = {_name(s) for s in proj["project"]["dependencies"]}
    dev = {_name(s) for s in proj["dependency-groups"]["dev"]}
    assert not (declared & DEV_ONLY), sorted(declared & DEV_ONLY)
    assert DEV_ONLY <= dev, sorted(DEV_ONLY - dev)


def test_no_tracked_python_file_imports_a_removed_package():
    banned = set().union(*NEVER_IMPORTED.values())
    # a walk, not `git ls-files`: the lane container has no git metadata
    files = []
    for d, dirs, names in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in {".git", ".venv", "node_modules", "__pycache__"}]
        files += [os.path.relpath(os.path.join(d, n), ROOT) for n in names if n.endswith(".py")]
    hits = []
    for f in files:
        try:
            tree = ast.parse((ROOT / f).read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.level == 0:
                mods = [n.module or ""]
            else:
                continue
            hits += [f"{f}:{n.lineno} {m}" for m in mods if m.split(".")[0] in banned]
    assert not hits, hits
