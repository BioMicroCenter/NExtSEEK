"""Unit tests for NessieAI.build_tools.gen_op_surfaces (Plan 005 Task 6)."""
from __future__ import annotations

import errno
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from NessieAI import paths
from NessieAI.build_tools.gen_op_surfaces.blocks import MarkerError, render_marked_file
from NessieAI.build_tools.gen_op_surfaces.constants import (
    CANONICAL_CAPABILITIES_REL,
    CANONICAL_CONTEXT_DIR_IN_CONTEXT,
    CANONICAL_CONTEXT_FILES,
    EXIT_CHANGES_WRITTEN,
    EXIT_ERROR,
    EXIT_NO_CHANGE,
    IMAGE_CONTEXT_DIR,
    NAMED_CAPABILITIES_CONTEXT,
    NAMED_CAPABILITIES_CONTEXT_PATH,
    PLUGIN_CONTEXT_REL,
    ROUTE_CAPABILITIES_REL,
)
from NessieAI.build_tools.gen_op_surfaces.docker_blocks import (
    emit_capabilities_copy_block,
    validate_canonical_context_final_writers,
)
from NessieAI.build_tools.gen_op_surfaces.emit import (
    SurfaceTarget,
    check_surfaces,
    surface_targets,
    write_surfaces,
)
from NessieAI.build_tools.gen_op_surfaces.paths import PathEscapeError, resolve_under_root

# A whole-file target that copies a "canonical" file to a "copy" path: the shape
# the retired plugin-tree capabilities.md target had, kept as a fixture for the
# check/write machinery that route_capabilities.json still uses.
FIXTURE_CANONICAL_REL = "canonical/capabilities.md"
FIXTURE_COPY_REL = "copy/capabilities.md"


def _fixture_copy_bytes(repo_root: Path) -> bytes:
    return resolve_under_root(repo_root, FIXTURE_CANONICAL_REL).read_bytes()


def _capabilities_only_targets() -> tuple[SurfaceTarget, ...]:
    return (
        SurfaceTarget(
            rel_path=FIXTURE_COPY_REL,
            kind="whole_file",
            emit=_fixture_copy_bytes,
        ),
    )


REPO_ROOT = paths.REPO_ROOT
EXPORT_MODULE = "NessieAI.cc.op_registry.export"
GEN_MODULE = "NessieAI.build_tools.gen_op_surfaces"
PYTHONPATH = f"{REPO_ROOT}:{paths.DMAC_ASSISTANT_DIR / 'src'}:{paths.CHAT_NEXTSEEK_DIR / 'src'}"
DMAC_PYTHON = Path(os.environ.get("DMAC_PORT_SOURCE", "dmac-assistant"), ".venv/bin/python3")
IMAGE_PYTHON = Path("/app/.venv/bin/python")


def _cli_python() -> str:
    if IMAGE_PYTHON.is_file():
        return str(IMAGE_PYTHON)
    if DMAC_PYTHON.is_file():
        return str(DMAC_PYTHON)
    return sys.executable


def _env(**extra: str) -> dict[str, str]:
    return {
        **dict(os.environ),
        "PYTHONPATH": PYTHONPATH,
        "PYTHONDONTWRITEBYTECODE": "1",
        **extra,
    }


def _seed_marked(path: Path, *, begin: str, end: str, inner: str = "old\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"prefix\n{begin}\n{inner}{end}\nsuffix\n", encoding="utf-8")


def test_render_marked_file_replaces_only_block_content(tmp_path: Path) -> None:
    begin = "<!-- BEGIN TEST -->"
    end = "<!-- END TEST -->"
    path = tmp_path / "doc.md"
    _seed_marked(path, begin=begin, end=end, inner="old\n")
    original = path.read_text(encoding="utf-8")
    updated = render_marked_file(original, begin, end, "new\n")
    path.write_text(updated, encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("prefix\n")
    assert "old\n" not in text
    assert "new\n" in text
    assert text.endswith("suffix\n")


def test_render_marked_file_is_idempotent(tmp_path: Path) -> None:
    begin = "<!-- BEGIN TEST -->"
    end = "<!-- END TEST -->"
    original = f"head\n{begin}\nbody\n{end}\ntail\n"
    once = render_marked_file(original, begin, end, "body\n")
    twice = render_marked_file(once, begin, end, "body\n")
    assert once == twice


@pytest.mark.parametrize(
    ("text", "message_fragment"),
    [
        ("no markers", "missing"),
        ("<!-- BEGIN -->\n<!-- BEGIN -->\n<!-- END -->\n", "duplicate"),
        ("<!-- BEGIN -->\n<!-- END -->\n<!-- END -->\n", "duplicate"),
        ("<!-- END -->\n<!-- BEGIN -->\n", "inverted"),
    ],
)
def test_marker_validation_failures(text: str, message_fragment: str) -> None:
    begin = "<!-- BEGIN -->"
    end = "<!-- END -->"
    with pytest.raises(MarkerError) as exc:
        render_marked_file(text, begin, end, "x\n")
    assert message_fragment in str(exc.value).lower()


def test_nested_marker_inside_block_raises() -> None:
    begin = "<!-- BEGIN DOC -->"
    end = "<!-- END DOC -->"
    text = f"head\n{begin}\n{begin}\nbody\n{end}\ntail\n"
    with pytest.raises(MarkerError) as exc:
        render_marked_file(text, begin, end, "x\n")
    assert "duplicate" in str(exc.value).lower()


def test_resolve_rejects_parent_traversal(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(PathEscapeError):
        resolve_under_root(root, "../outside")


def test_resolve_rejects_symlink_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("nope", encoding="utf-8")
    link = root / "escape"
    link.symlink_to(secret)
    with pytest.raises(PathEscapeError):
        resolve_under_root(root, "escape")


def test_surface_targets_have_stable_sorted_order() -> None:
    targets = surface_targets(REPO_ROOT)
    paths = [target.rel_path for target in targets]
    assert paths == sorted(paths)
    assert len(paths) >= 1


def test_capabilities_copy_matches_canonical_bytes() -> None:
    """The image's capabilities.md, and every other canonical context file, is the
    canonical file itself: the plugin tree holds no copy, and the Dockerfile's last
    writer of each in-image path is the named-context COPY of the canonical file."""
    canonical = resolve_under_root(REPO_ROOT, CANONICAL_CAPABILITIES_REL)
    assert canonical.is_file()
    assert CANONICAL_CAPABILITIES_REL == (
        f"{NAMED_CAPABILITIES_CONTEXT_PATH}/{CANONICAL_CONTEXT_DIR_IN_CONTEXT}/capabilities.md"
    )
    plugin_context = REPO_ROOT / PLUGIN_CONTEXT_REL
    assert plugin_context.is_dir()
    for name in CANONICAL_CONTEXT_FILES:
        source = REPO_ROOT / NAMED_CAPABILITIES_CONTEXT_PATH / CANONICAL_CONTEXT_DIR_IN_CONTEXT / name
        assert source.is_file(), f"canonical context file missing: {source}"
        assert not (plugin_context / name).exists(), f"plugin-tree copy is back: {name}"
    dockerfile_text = (paths.CC_RUNTIME_DIR / "Dockerfile").read_text(encoding="utf-8")
    validate_canonical_context_final_writers(dockerfile_text)
    assert emit_capabilities_copy_block(REPO_ROOT) == "".join(
        f"COPY --from={NAMED_CAPABILITIES_CONTEXT} {CANONICAL_CONTEXT_DIR_IN_CONTEXT}/{name} "
        f"{IMAGE_CONTEXT_DIR}/{name}\n"
        for name in CANONICAL_CONTEXT_FILES
    )


def test_plugin_tree_copy_of_a_canonical_context_file_is_refused(tmp_path: Path) -> None:
    """A copy put back in the plugin tree would never reach the image, so the
    capabilities-copy emitter refuses it and --check and --write both stop."""
    repo = tmp_path / "repo"
    plugin_context = repo / PLUGIN_CONTEXT_REL
    plugin_context.mkdir(parents=True)
    emit_capabilities_copy_block(repo)
    (plugin_context / "min_assays_db.json").write_bytes(b"[]\n")
    with pytest.raises(SystemExit, match="min_assays_db.json"):
        emit_capabilities_copy_block(repo)


def test_check_surfaces_passes_on_current_tree() -> None:
    check_surfaces(repo_root=REPO_ROOT)


def test_check_surfaces_does_not_rewrite_targets_or_create_repo_pyc() -> None:
    target = paths.DMAC_BUILD_CONTEXT / "route_capabilities.json"
    before = (target.stat().st_mtime_ns, target.stat().st_size)
    check_surfaces(repo_root=REPO_ROOT)
    after = (target.stat().st_mtime_ns, target.stat().st_size)
    assert after == before
    assert not (REPO_ROOT / "plan005-surfaces-tmp").exists()


def test_stale_capabilities_copy_fails_check(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    canonical = repo / FIXTURE_CANONICAL_REL
    baked = repo / FIXTURE_COPY_REL
    canonical.parent.mkdir(parents=True)
    baked.parent.mkdir(parents=True)
    canonical.write_bytes(b"canonical bytes\n")
    baked.write_bytes(b"stale bytes\n")
    with pytest.raises(SystemExit) as exc:
        check_surfaces(repo_root=repo, targets=_capabilities_only_targets())
    assert exc.value.code != 0


def test_write_surfaces_is_idempotent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    canonical = repo / FIXTURE_CANONICAL_REL
    baked = repo / FIXTURE_COPY_REL
    canonical.parent.mkdir(parents=True)
    baked.parent.mkdir(parents=True)
    canonical.write_bytes(b"same\n")
    baked.write_bytes(b"same\n")
    assert write_surfaces(repo_root=repo, targets=_capabilities_only_targets()) == EXIT_NO_CHANGE
    assert write_surfaces(repo_root=repo, targets=_capabilities_only_targets()) == EXIT_NO_CHANGE


def test_write_surfaces_returns_exit_changes_written(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    canonical = repo / FIXTURE_CANONICAL_REL
    baked = repo / FIXTURE_COPY_REL
    canonical.parent.mkdir(parents=True)
    baked.parent.mkdir(parents=True)
    canonical.write_bytes(b"canonical\n")
    baked.write_bytes(b"stale\n")
    assert write_surfaces(repo_root=repo, targets=_capabilities_only_targets()) == EXIT_CHANGES_WRITTEN
    assert baked.read_bytes() == b"canonical\n"
    assert write_surfaces(repo_root=repo, targets=_capabilities_only_targets()) == EXIT_NO_CHANGE


def test_check_mode_does_not_mutate_committed_targets(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    canonical = repo / FIXTURE_CANONICAL_REL
    baked = repo / FIXTURE_COPY_REL
    canonical.parent.mkdir(parents=True)
    baked.parent.mkdir(parents=True)
    payload = b"canonical payload\n"
    canonical.write_bytes(payload)
    baked.write_bytes(payload)
    for path in (canonical, baked):
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    check_surfaces(repo_root=repo, targets=_capabilities_only_targets())
    assert canonical.read_bytes() == payload
    assert baked.read_bytes() == payload


def _run_module_cli(
    module: str,
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    with_pydantic: bool = False,
    python: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if python:
        cmd = [python, "-m", module, *args]
    else:
        cmd = [_cli_python(), "-m", module, *args]
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_surface_targets_include_route_capabilities() -> None:
    paths = [target.rel_path for target in surface_targets(REPO_ROOT)]
    assert ROUTE_CAPABILITIES_REL in paths


def test_gen_op_surfaces_check_cli_exits_zero() -> None:
    result = _run_module_cli(
        GEN_MODULE,
        ["--check", "--root", str(REPO_ROOT)],
        cwd=REPO_ROOT,
        env=_env(TMPDIR="/tmp"),
        python=str(_cli_python()),
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_export_check_cli_exits_zero() -> None:
    result = _run_module_cli(
        EXPORT_MODULE,
        ["--check"],
        cwd=REPO_ROOT,
        env=_env(TMPDIR="/tmp"),
        with_pydantic=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_readonly_repo_mount_no_write_oracle_for_export_and_gen_surfaces() -> None:
    """Load-bearing oracle: real CLIs on read-only targets cannot write the tree."""
    # The plugin tree's capabilities.md copy is gone (NessieAI Phase C: the image
    # takes the canonical file); the generated route_capabilities.json takes its slot.
    target_paths = [
        paths.CHAT_NEXTSEEK_DIR / "src" / "chat_nextseek" / "context" / "capabilities.md",
        paths.DMAC_BUILD_CONTEXT / "route_capabilities.json",
        paths.CC_DIR / "op_registry" / "ops.json",
        paths.CC_PLUGIN_DIR / "context" / "ops.json",
    ]
    # Every target must exist: a move must not shrink the oracle silently.
    missing = [path for path in target_paths if not path.is_file()]
    assert not missing, f"no-write oracle targets are missing: {missing}"
    existing = target_paths
    before = {path: path.read_bytes() for path in existing}
    original_modes = {path: path.stat().st_mode for path in existing}
    chmod_applied = False

    try:
        for path in existing:
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        chmod_applied = True
    except OSError as exc:
        if exc.errno != errno.EROFS:
            raise

    tmpdir = Path("/tmp") / f"plan005-gen-op-surfaces-{os.getpid()}"
    tmpdir.mkdir(exist_ok=True)
    env = _env(TMPDIR=str(tmpdir), XDG_CACHE_HOME=str(tmpdir / "cache"))

    try:
        export = _run_module_cli(
            EXPORT_MODULE,
            ["--check"],
            cwd=REPO_ROOT,
            env=env,
            with_pydantic=True,
        )
        assert export.returncode == 0, export.stderr or export.stdout

        surfaces = _run_module_cli(
            GEN_MODULE,
            ["--check", "--root", str(REPO_ROOT)],
            cwd=REPO_ROOT,
            env=env,
            python=str(_cli_python()),
        )
        assert surfaces.returncode == 0, surfaces.stderr or surfaces.stdout

        after = {path: path.read_bytes() for path in existing}
        assert before == after
    finally:
        if chmod_applied:
            for path, mode in original_modes.items():
                path.chmod(mode)


def test_json_emitter_replaces_whole_document(tmp_path: Path) -> None:
    target = SurfaceTarget(
        rel_path="generated/sample.json",
        kind="whole_file",
        emit=lambda _root: b'{"a":1}\n',
    )
    repo = tmp_path / "repo"
    path = repo / target.rel_path
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"a":1}\n')
    check_surfaces(repo_root=repo, targets=(target,))
    path.write_bytes(b'{"a":2}\n')
    with pytest.raises(SystemExit):
        check_surfaces(repo_root=repo, targets=(target,))


def test_markdown_emitter_preserves_outside_markers(tmp_path: Path) -> None:
    begin = "<!-- BEGIN DOC -->"
    end = "<!-- END DOC -->"
    repo = tmp_path / "repo"
    rel = "docs/sample.md"
    path = repo / rel
    _seed_marked(path, begin=begin, end=end, inner="old\n")
    target = SurfaceTarget(
        rel_path=rel,
        kind="marked_block",
        begin_marker=begin,
        end_marker=end,
        emit=lambda _root: "new\n",
    )
    assert write_surfaces(repo_root=repo, targets=(target,)) == EXIT_CHANGES_WRITTEN
    check_surfaces(repo_root=repo, targets=(target,))
    text = path.read_text(encoding="utf-8")
    assert "prefix\n" in text
    assert "suffix\n" in text
    assert "old\n" not in text
    assert "new\n" in text
