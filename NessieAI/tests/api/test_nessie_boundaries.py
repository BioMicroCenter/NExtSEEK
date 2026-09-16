"""The NessieAI / API boundary, frozen at the Phase A move and amended by Phase B.

NessieAI declares no models, migrations, AppConfig or app label. Its engine
packages may still reach back into the Django project, and a few engine and
build modules import the test harness as a library. Both kinds of edge are
frozen here as allowlists, so a new one fails this test instead of growing
unnoticed:

- back-edges: an engine module importing ``nextseek_api``, ``seek`` or
  ``dmac``. The sanctioned one is ``nextseek_api.assistant.models_db`` (the
  ORM models live under the ``nextseek_api`` app label); the rest predate the
  move. Phase B moved the engine half of ``nextseek_api/services`` into
  ``router/policy.py``, ``cc/turn.py`` and ``ns/{turn,artifacts,retry}.py``
  and listed only edges the services code already held. None of them may
  import ``nextseek_api.services``: that would be the API calling itself
  through the engine.
- engine-to-harness edges: an engine module importing ``NessieAI.tests``.
  Exactly three modules do so (audit decision 14). A non-test harness
  library would shrink this list to zero; that extraction has not been done.

The allowlists are exact: a listed edge that disappears fails too, so the
list only ever shrinks by an edit that removes the entry.

Two hygiene rules cover the whole live NessieAI tree (history/ and docker/
excluded): no ``sys.path`` entry may give a NessieAI package a second
top-level name (``sys.path.insert(0, <NessieAI/>)`` is the case that bit the
move: it shadows the vendored runners' ``import hibayes``), and no module
imports the retired bare names ``e2e``, ``pathsetup`` or ``nessie_tests``.

The walk covers the full AST, function bodies included, so lazy imports and
literal ``importlib.import_module`` strings count. Pure stdlib: no Django.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Iterator

import pytest

from NessieAI import paths

ENGINE_PACKAGES = ("cc", "router", "hibayes", "ns", "schema_rag", "build_tools")

# Per-package module floors, well under the counts at the move (cc 31,
# router 10, hibayes 72, ns 7, schema_rag 7, build_tools 23). A walk over a
# moved or emptied package fails; ordinary additions and deletions do not.
MIN_MODULES = {
    "cc": 20,
    "router": 5,
    "hibayes": 50,
    "ns": 5,
    "schema_rag": 5,
    "build_tools": 15,
}

# Top-level packages of the Django host. An engine import of any of them is a
# back-edge across the boundary.
HOST_PACKAGES = frozenset({"nextseek_api", "seek", "dmac"})

HARNESS_PREFIX = "NessieAI.tests"

# Retired import names: the pre-move top-level harness and e2e packages and
# the sys.path helper deleted in C5. Their qualified names are
# NessieAI.tests.nessie_tests and NessieAI.tests.e2e.
BARE_FORBIDDEN = frozenset({"e2e", "pathsetup", "nessie_tests"})

# Frozen back-edges: engine module (repo-relative) -> host modules it imports.
BACK_EDGE_ALLOWLIST: dict[str, frozenset[str]] = {
    # CC engine: SeekDB for provisioning (lazy), the transcript ORM model.
    "NessieAI/cc/cc_provision.py": frozenset({"seek.seekdb"}),
    "NessieAI/cc/cc_transcript_store.py": frozenset({"nextseek_api.assistant.models_db"}),
    # The CC turn (Phase B, moved from nextseek_api/services/cc_assistant.py,
    # which already held this edge): ChatSession reads and summary writes, and
    # the CCSessionTranscript upsert when a CC turn completes.
    "NessieAI/cc/turn.py": frozenset({"nextseek_api.assistant.models_db"}),
    # Reingest: PipelineRun records which NExtSEEK sequencing samples a pipeline
    # run consumed. The model lives in nextseek_api/assistant/models_db.py because
    # models and migrations stay with the API (app label nextseek_api), so reading
    # it from the engine is the same sanctioned edge cc/turn.py and the HiBayes
    # ORM modules already hold.
    "NessieAI/ns/reingest/launch_record.py": frozenset({"nextseek_api.assistant.models_db"}),
    "NessieAI/ns/reingest/uid_resolve.py": frozenset({"nextseek_api.assistant.models_db"}),
    # Router telemetry and the posterior leg, through the ORM models.
    "NessieAI/router/risk_overlay.py": frozenset({"nextseek_api.assistant.models_db"}),
    "NessieAI/router/turn_ledger.py": frozenset({"nextseek_api.assistant.models_db"}),
    # The HiBayes ORM modules (eval_* tables under app label nextseek_api).
    "NessieAI/hibayes/export.py": frozenset({"nextseek_api.assistant.models_db"}),
    "NessieAI/hibayes/fit/fit_boundary.py": frozenset({"nextseek_api.assistant.models_db"}),
    "NessieAI/hibayes/generation_store.py": frozenset({"nextseek_api.assistant.models_db"}),
    "NessieAI/hibayes/generation_validation.py": frozenset({"nextseek_api.assistant.models_db"}),
    "NessieAI/hibayes/human_grade_fit.py": frozenset({"nextseek_api.assistant.models_db"}),
    "NessieAI/hibayes/judge_cache.py": frozenset({"nextseek_api.assistant.models_db"}),
    "NessieAI/hibayes/paid_run_state.py": frozenset({"nextseek_api.assistant.models_db"}),
    "NessieAI/hibayes/paired_run_registry.py": frozenset({"nextseek_api.assistant.models_db"}),
    "NessieAI/hibayes/run_authorization.py": frozenset({"nextseek_api.assistant.models_db"}),
    "NessieAI/hibayes/spend_conservation.py": frozenset({"nextseek_api.assistant.models_db"}),
    "NessieAI/hibayes/task6_replay.py": frozenset({"nextseek_api.assistant.models_db"}),
    # Task-6 AppConfig: import_module string, kept on purpose (audit section 3).
    "NessieAI/hibayes/task6_app.py": frozenset({"nextseek_api.assistant.models_db"}),
    # run-harvest's D.SEQ fastq-path lookup: a plain filtered SELECT against
    # the seek-mirrored samples table, wrapped by reingest_lookups so the
    # query is written once rather than re-invented at each call site.
    "NessieAI/ns/granular.py": frozenset({"nextseek_api.services.reingest_lookups"}),
    # NS engine: the upload helper reingest QA reuses.
    "NessieAI/ns/reingest_qa.py": frozenset({"nextseek_api.batch_upload.helpers"}),
    # The evaluator's retry engine (Phase B, moved from
    # nextseek_api/services/evaluator.py, which already held both edges): the
    # pydantic retry-context response models it builds, and QueryTask, read to
    # find the task that produced a bundle. The ViewSet builds the adapter and
    # the event callback and hands them in.
    "NessieAI/ns/retry.py": frozenset({
        "nextseek_api.assistant.models_db",
        "nextseek_api.assistant.models_evaluator",
    }),
    # The NS turn (Phase B, moved from nextseek_api/services/assistant.py, which
    # already held this edge): _save_session_or_report catches SessionSaveError,
    # the error a failed ChatSession save raises, to tell the user the turn was
    # not saved. The adapter itself is built by the ViewSet and handed in.
    "NessieAI/ns/turn.py": frozenset({"nextseek_api.assistant.session_adapter"}),
    # schema_rag: the Ingest/Retrieve pydantic API models stay in nextseek_api.
    "NessieAI/schema_rag/__init__.py": frozenset({"nextseek_api.models"}),
    "NessieAI/schema_rag/service.py": frozenset({"nextseek_api.models"}),
}

# Frozen engine-to-harness edges (audit section 3 and decision 14).
_NT = "NessieAI.tests.nessie_tests"
HARNESS_EDGE_ALLOWLIST: dict[str, frozenset[str]] = {
    "NessieAI/cc/op_registry/paired_evidence.py": frozenset(
        {f"{_NT}.bayes_manifest", f"{_NT}.corpus", f"{_NT}.export", f"{_NT}.runner"}
    ),
    "NessieAI/build_tools/gen_op_surfaces/route_capabilities.py": frozenset(
        {f"{_NT}.corpus", f"{_NT}.export", f"{_NT}.runner"}
    ),
    "NessieAI/hibayes/human_grade_fit.py": frozenset({f"{_NT}.bayes_manifest"}),
}

# sys.path mutations whose argument this test cannot evaluate statically, each
# checked by hand. All seven are the same run-by-path bootstrap: walk up from
# the script and insert the first parent holding NessieAI/__init__.py, which
# is the checkout root (NessieAI's canonical parent), never a dir inside it.
UNRESOLVED_SYS_PATH_ALLOWLIST: frozenset[str] = frozenset({
    "NessieAI/tests/cc/scripts/full_ui_e2e.py",
    "NessieAI/tests/cc/scripts/step7_gate3d_host_finalize.py",
    "NessieAI/tests/cc/scripts/step7_gate3d_live.py",
    "NessieAI/tests/cc/scripts/step7_gate3d_per_op.py",
    "NessieAI/tests/cc/scripts/step7_validator_dry_run.py",
    "NessieAI/tests/cc/scripts/verify_host_only_allowlist.py",
    "NessieAI/tests/cc/scripts/verify_prod_readiness_manifest.py",
})

# Live NessieAI trees the hygiene rules skip: frozen records, and the image
# build contexts, which run in their own interpreters with their own sys.path
# conventions (the plugin bin runner, the cc-runtime tests).
HYGIENE_SKIP = ("history", "docker", "chat_frontend")

REPO_ROOT = paths.REPO_ROOT
NESSIE_ROOT = paths.NESSIE_ROOT


# ---------------------------------------------------------------------------
# AST walk
# ---------------------------------------------------------------------------

def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _dotted_name(path: Path) -> str:
    """The import name of ``path``: climb while the parent dir is a package."""
    parts = [] if path.name == "__init__.py" else [path.stem]
    d = path.parent
    while (d / "__init__.py").is_file():
        parts.insert(0, d.name)
        d = d.parent
    return ".".join(parts)


def _is_module(dotted: str) -> bool:
    """True when ``dotted`` names a module or package under the repo root."""
    base = REPO_ROOT.joinpath(*dotted.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _resolve_from(node: ast.ImportFrom, module: str, is_package: bool) -> str:
    if node.level == 0:
        return node.module or ""
    base = module.split(".") if module else []
    if not is_package:
        base = base[:-1]
    if node.level > 1:
        base = base[: max(len(base) - (node.level - 1), 0)]
    return ".".join(base + ([node.module] if node.module else []))


def _targets(node: ast.AST, module: str, is_package: bool) -> list[str]:
    """Modules an import node or a literal import_module call loads."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        base = _resolve_from(node, module, is_package)
        out = []
        for alias in node.names:
            sub = f"{base}.{alias.name}" if base else alias.name
            out.append(sub if alias.name != "*" and _is_module(sub) else base)
        return out
    if isinstance(node, ast.Call):
        fn = ast.unparse(node.func)
        if fn in ("importlib.import_module", "import_module", "__import__") and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                if first.value.startswith("."):
                    return []  # package-relative, stays inside its own package
                return [first.value]
    return []


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(path: Path) -> Iterator[tuple[int, str]]:
    tree = _parse(path)
    module = _dotted_name(path)
    is_package = path.name == "__init__.py"
    for node in ast.walk(tree):
        for target in _targets(node, module, is_package):
            if target:
                yield node.lineno, target


def _is_source(path: Path) -> bool:
    """Skip untracked tool trees a box may hold (a .venv, node_modules, caches)."""
    parts = path.relative_to(NESSIE_ROOT).parts[:-1]
    return not any(
        p.startswith(".") or p in ("node_modules", "__pycache__", "site-packages")
        for p in parts
    )


def _engine_files() -> list[Path]:
    files: list[Path] = []
    for pkg in ENGINE_PACKAGES:
        files.extend(p for p in sorted((NESSIE_ROOT / pkg).rglob("*.py")) if _is_source(p))
    return files


def _hygiene_files() -> list[Path]:
    return [
        path for path in sorted(NESSIE_ROOT.rglob("*.py"))
        if path.relative_to(NESSIE_ROOT).parts[0] not in HYGIENE_SKIP and _is_source(path)
    ]


def _edges(prefix_test) -> dict[str, dict[str, list[int]]]:
    """engine file -> target module -> lines, for targets matching prefix_test."""
    found: dict[str, dict[str, list[int]]] = {}
    for path in _engine_files():
        for lineno, target in _imports(path):
            if prefix_test(target):
                found.setdefault(_rel(path), {}).setdefault(target, []).append(lineno)
    return found


def _is_host(target: str) -> bool:
    return target.split(".")[0] in HOST_PACKAGES


def _is_harness(target: str) -> bool:
    return target == HARNESS_PREFIX or target.startswith(HARNESS_PREFIX + ".")


def _is_bare_forbidden(target: str) -> bool:
    segments = target.split(".")
    return segments[0] in BARE_FORBIDDEN or "pathsetup" in segments


# ---------------------------------------------------------------------------
# Static evaluation of sys.path arguments
# ---------------------------------------------------------------------------

class _Unresolved(Exception):
    pass


def _single_assignments(tree: ast.Module) -> dict[str, ast.expr]:
    """Names bound exactly once anywhere in the file, by a plain ``x = expr``."""
    seen: dict[str, list[ast.expr]] = {}
    rebound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    seen.setdefault(tgt.id, []).append(node.value)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            rebound.add(node.target.id)
            if isinstance(node, ast.AnnAssign) and node.value is not None:
                seen.setdefault(node.target.id, []).append(node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    rebound.add(sub.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for arg in ast.walk(node.args):
                if isinstance(arg, ast.arg):
                    rebound.add(arg.arg)
    return {k: v[0] for k, v in seen.items() if len(v) == 1 and k not in rebound}


class _PathEvaluator:
    """Evaluate the small path-expression language the tree uses.

    Handles ``Path(__file__)``, ``.resolve()``, ``.absolute()``, ``.parent``,
    ``.parents[n]``, ``/ "segment"``, ``str()``, ``os.fspath``,
    ``os.path.{dirname,abspath,realpath,join}``, ``NessieAI.paths`` constants
    and names bound once in the file. Anything else raises _Unresolved.
    """

    def __init__(self, path: Path, tree: ast.Module) -> None:
        self.file = path
        self.names = _single_assignments(tree)
        self.depth = 0

    def eval(self, node: ast.expr):  # noqa: C901 - one dispatch table
        self.depth += 1
        try:
            if self.depth > 50:
                raise _Unresolved("expression too deep")
            return self._eval(node)
        finally:
            self.depth -= 1

    def _eval(self, node: ast.expr):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, int)):
            return node.value
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return str(self.file)
            if node.id in self.names:
                return self.eval(self.names[node.id])
            raise _Unresolved(f"name {node.id!r}")
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "paths":
                return getattr(paths, node.attr)
            if node.attr == "parent":
                return Path(self.eval(node.value)).parent
            if node.attr == "parents":
                return Path(self.eval(node.value)).parents
            raise _Unresolved(f"attribute {node.attr!r}")
        if isinstance(node, ast.Subscript):
            return self.eval(node.value)[self.eval(node.slice)]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return Path(self.eval(node.left)) / self.eval(node.right)
        if isinstance(node, ast.Call):
            fn = ast.unparse(node.func)
            args = [self.eval(a) for a in node.args]
            if fn in ("Path", "pathlib.Path", "PurePath", "pathlib.PurePath"):
                return Path(*args)
            if fn in ("str", "os.fspath"):
                return str(args[0])
            if fn == "os.path.dirname":
                return os.path.dirname(args[0])
            if fn in ("os.path.abspath", "os.path.realpath"):
                return os.path.abspath(args[0])
            if fn == "os.path.join":
                return os.path.join(*[str(a) for a in args])
            if isinstance(node.func, ast.Attribute) and node.func.attr in ("resolve", "absolute"):
                return Path(self.eval(node.func.value)).resolve()
            raise _Unresolved(f"call {fn}()")
        raise _Unresolved(type(node).__name__)


def _sys_path_spellings(tree: ast.Module) -> frozenset[str]:
    """Every spelling of ``sys.path`` the file binds.

    ``sys.path`` itself, ``<alias>.path`` for ``import sys as <alias>``, and
    the bare name bound by ``from sys import path [as <name>]``.
    """
    spellings = {"sys.path"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sys":
                    spellings.add(f"{alias.asname or 'sys'}.path")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "sys":
            for alias in node.names:
                if alias.name == "path":
                    spellings.add(alias.asname or "path")
    return frozenset(spellings)


def _sys_path_mutations(tree: ast.Module) -> Iterator[tuple[int, ast.expr | None]]:
    """(line, inserted expression or None when not a single entry) per mutation."""
    spellings = _sys_path_spellings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if ast.unparse(node.func.value) in spellings:
                if node.func.attr == "insert" and len(node.args) == 2:
                    yield node.lineno, node.args[1]
                elif node.func.attr == "append" and len(node.args) == 1:
                    yield node.lineno, node.args[0]
                elif node.func.attr == "extend":
                    yield node.lineno, None
        elif isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for tgt in targets:
                base = tgt.value if isinstance(tgt, ast.Subscript) else tgt
                if ast.unparse(base) in spellings:
                    yield node.lineno, None


def _second_identities(entry: Path) -> list[str]:
    """NessieAI packages that ``entry`` on sys.path would expose under a new name.

    A directory inside NessieAI/ re-roots every subdirectory that is itself
    part of the NessieAI package tree (``__init__.py`` all the way down from
    NessieAI/). NessieAI/ itself re-roots cc, router, hibayes and the rest.
    """
    try:
        entry = entry.resolve()
    except OSError:
        return []
    if entry != NESSIE_ROOT and NESSIE_ROOT not in entry.parents:
        return []
    chain = [entry, *entry.parents]
    in_tree = all(
        (d / "__init__.py").is_file()
        for d in chain[: chain.index(NESSIE_ROOT) + 1]
    )
    if not in_tree or not entry.is_dir():
        return []
    return sorted(
        child.name for child in entry.iterdir()
        if child.is_dir() and (child / "__init__.py").is_file()
    )


def _sys_path_violations(path: Path) -> list[str]:
    tree = _parse(path)
    rel = _rel(path)
    out = []
    evaluator = _PathEvaluator(path, tree)
    for lineno, expr in _sys_path_mutations(tree):
        if expr is None:
            if rel not in UNRESOLVED_SYS_PATH_ALLOWLIST:
                out.append(f"{rel}:{lineno}: sys.path mutation this test cannot check")
            continue
        try:
            value = evaluator.eval(expr)
        except (_Unresolved, AttributeError, IndexError, TypeError, ValueError) as exc:
            if rel not in UNRESOLVED_SYS_PATH_ALLOWLIST:
                out.append(f"{rel}:{lineno}: cannot evaluate sys.path entry ({exc})")
            continue
        clash = _second_identities(Path(str(value)))
        if clash:
            out.append(
                f"{rel}:{lineno}: sys.path entry {_rel_or_abs(Path(str(value)))} gives "
                f"NessieAI packages a second top-level name: {', '.join(clash)}"
            )
    return out


def _rel_or_abs(p: Path) -> str:
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix() or "."
    except ValueError:
        return str(p)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_engine_packages_are_present_and_parse():
    """Guard against a vacuous pass over a moved or empty tree."""
    assert set(MIN_MODULES) == set(ENGINE_PACKAGES)
    for pkg in ENGINE_PACKAGES:
        root = NESSIE_ROOT / pkg
        assert (root / "__init__.py").is_file(), f"missing engine package NessieAI/{pkg}"
        count = sum(1 for p in root.rglob("*.py") if _is_source(p))
        assert count >= MIN_MODULES[pkg], (
            f"only {count} modules under NessieAI/{pkg} (floor {MIN_MODULES[pkg]})"
        )
    files = _engine_files()
    for path in files:
        _parse(path)  # a SyntaxError here means the boundary cannot be checked
    assert len(_hygiene_files()) > len(files)


def test_no_new_back_edges_into_the_django_host():
    found = _edges(_is_host)
    new = [
        f"{rel} -> {target} (line {', '.join(map(str, lines))})"
        for rel, targets in sorted(found.items())
        for target, lines in sorted(targets.items())
        if target not in BACK_EDGE_ALLOWLIST.get(rel, frozenset())
    ]
    assert not new, (
        "New engine import of nextseek_api/seek/dmac. Keep engine code free of the "
        "Django host (take the dependency as an argument, or put the code in the "
        "API shell). Only if the edge is truly needed, add it to "
        "BACK_EDGE_ALLOWLIST with a reason:\n  " + "\n  ".join(new)
    )


def test_back_edge_allowlist_has_no_stale_entries():
    found = _edges(_is_host)
    stale = [
        f"{rel} -> {target}"
        for rel, targets in sorted(BACK_EDGE_ALLOWLIST.items())
        for target in sorted(targets)
        if target not in found.get(rel, {})
    ]
    assert not stale, (
        "These allowlisted back-edges no longer exist. Remove them from "
        "BACK_EDGE_ALLOWLIST so the list stays exact:\n  " + "\n  ".join(stale)
    )


def test_no_new_engine_imports_of_the_test_tree():
    found = _edges(_is_harness)
    new = [
        f"{rel} -> {target} (line {', '.join(map(str, lines))})"
        for rel, targets in sorted(found.items())
        for target, lines in sorted(targets.items())
        if target not in HARNESS_EDGE_ALLOWLIST.get(rel, frozenset())
    ]
    assert not new, (
        "Engine or build code imports NessieAI.tests. Only the three frozen "
        "harness edges are allowed (audit decision 14):\n  " + "\n  ".join(new)
    )


def test_harness_edge_allowlist_has_no_stale_entries():
    found = _edges(_is_harness)
    stale = [
        f"{rel} -> {target}"
        for rel, targets in sorted(HARNESS_EDGE_ALLOWLIST.items())
        for target in sorted(targets)
        if target not in found.get(rel, {})
    ]
    assert not stale, (
        "These allowlisted harness edges no longer exist. Remove them from "
        "HARNESS_EDGE_ALLOWLIST:\n  " + "\n  ".join(stale)
    )


def test_no_sys_path_entry_reroots_a_nessie_package():
    violations = [v for path in _hygiene_files() for v in _sys_path_violations(path)]
    assert not violations, (
        "A sys.path entry inside NessieAI/ imports its packages under a second "
        "name (two module identities, and NessieAI/ shadows the vendored "
        "runners' `import hibayes`). Import by the NessieAI.* name instead:\n  "
        + "\n  ".join(violations)
    )


def test_unresolved_sys_path_allowlist_has_no_stale_entries():
    stale = []
    for rel in sorted(UNRESOLVED_SYS_PATH_ALLOWLIST):
        path = REPO_ROOT / rel
        if not path.is_file() or not any(True for _ in _sys_path_mutations(_parse(path))):
            stale.append(rel)
    assert not stale, (
        "Remove from UNRESOLVED_SYS_PATH_ALLOWLIST (file gone, or no sys.path "
        "mutation left):\n  " + "\n  ".join(stale)
    )


def test_no_bare_e2e_pathsetup_or_nessie_tests_imports():
    hits = [
        f"{_rel(path)}:{lineno}: {target}"
        for path in _hygiene_files()
        for lineno, target in _imports(path)
        if _is_bare_forbidden(target)
    ]
    assert not hits, (
        "Retired import names. Use NessieAI.tests.e2e and "
        "NessieAI.tests.nessie_tests; pathsetup is gone:\n  " + "\n  ".join(hits)
    )


# ---------------------------------------------------------------------------
# The checker itself, on synthetic sources, so a broken walker cannot pass
# ---------------------------------------------------------------------------

def _synthetic(tmp_path: Path, rel: str, source: str) -> Path:
    """Write ``source`` at ``rel`` in a scratch tree and return a fake repo path.

    The evaluator only needs ``__file__`` to sit at the real location's
    depth, so the returned path is ``REPO_ROOT / rel`` and the file is parsed
    from ``tmp_path``.
    """
    scratch = tmp_path / "src.py"
    scratch.write_text(source, encoding="utf-8")
    return scratch


@pytest.mark.parametrize(
    ("rel", "source", "expected"),
    [
        # The C5 hazard: parents[2] of a hibayes test is NessieAI/.
        ("NessieAI/tests/hibayes/test_x.py",
         "import sys\nfrom pathlib import Path\n"
         "sys.path.insert(0, str(Path(__file__).resolve().parents[2]))\n",
         ["cc", "hibayes", "router"]),
        # The old pathsetup hazard: NessieAI/tests re-roots e2e and nessie_tests.
        ("NessieAI/tests/cc/test_x.py",
         "import sys, os\n"
         "sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n",
         ["e2e", "nessie_tests"]),
        # The same, through an aliased sys and through a bare sys.path name.
        ("NessieAI/tests/hibayes/test_x.py",
         "import sys as _s\nfrom pathlib import Path\n"
         "_s.path.insert(0, str(Path(__file__).resolve().parents[2]))\n",
         ["cc", "hibayes", "router"]),
        ("NessieAI/tests/hibayes/test_x.py",
         "from sys import path as sp\nfrom pathlib import Path\n"
         "sp.append(str(Path(__file__).resolve().parents[2]))\n",
         ["cc", "hibayes", "router"]),
        # Through a name bound once, and through NessieAI.paths.
        ("NessieAI/tests/router/test_x.py",
         "import sys\nfrom NessieAI import paths\nROOT = paths.NESSIE_ROOT\n"
         "sys.path.insert(0, str(ROOT))\n",
         ["cc", "hibayes", "router"]),
        # Fine: the checkout root, and a plugin bin dir outside the package tree.
        ("NessieAI/tests/cc/test_x.py",
         "import sys\nfrom pathlib import Path\n"
         "sys.path.insert(0, str(Path(__file__).resolve().parents[3]))\n",
         []),
        ("NessieAI/tests/cc/test_x.py",
         "import sys\nfrom NessieAI import paths\n"
         "sys.path.insert(0, str(paths.CC_PLUGIN_BIN))\n",
         []),
    ],
)
def test_sys_path_checker_on_synthetic_sources(tmp_path, rel, source, expected):
    scratch = _synthetic(tmp_path, rel, source)
    tree = _parse(scratch)
    evaluator = _PathEvaluator(REPO_ROOT / rel, tree)
    [(_, expr)] = list(_sys_path_mutations(tree))
    value = Path(str(evaluator.eval(expr)))
    clash = _second_identities(value)
    for name in expected:
        assert name in clash
    if not expected:
        assert clash == []


def test_sys_path_checker_flags_what_it_cannot_evaluate(tmp_path):
    scratch = _synthetic(
        tmp_path, "NessieAI/tests/cc/test_x.py",
        "import sys\nfor p in ['a']:\n    sys.path.insert(0, p)\nsys.path[0:0] = ['b']\n",
    )
    tree = _parse(scratch)
    evaluator = _PathEvaluator(REPO_ROOT / "NessieAI/tests/cc/test_x.py", tree)
    mutations = list(_sys_path_mutations(tree))
    assert len(mutations) == 2
    loop_expr = mutations[0][1] if mutations[0][1] is not None else mutations[1][1]
    with pytest.raises(_Unresolved):
        evaluator.eval(loop_expr)
    assert any(expr is None for _, expr in mutations)


def test_import_walker_sees_lazy_relative_and_string_imports(tmp_path):
    source = (
        "import importlib\n"
        "def later():\n"
        "    from nextseek_api.services import assistant\n"
        "    importlib.import_module('seek.seekdb')\n"
        "    from ..tests.nessie_tests import corpus\n"
        "    import e2e.catalog\n"
    )
    scratch = _synthetic(tmp_path, "NessieAI/cc/x.py", source)
    tree = _parse(scratch)
    targets = [t for node in ast.walk(tree) for t in _targets(node, "NessieAI.cc.x", False)]
    # The walker names the submodule when it exists on disk, else the package,
    # so accept either: the point is that the lazy import is seen at all.
    assert any(t.split(".")[:2] == ["nextseek_api", "services"] for t in targets)
    assert "seek.seekdb" in targets
    assert "NessieAI.tests.nessie_tests.corpus" in targets
    assert any(_is_host(t) for t in targets)
    assert any(_is_harness(t) for t in targets)
    assert any(_is_bare_forbidden(t) for t in targets)
