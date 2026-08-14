"""Unit tests for build_tools.gen_op_surfaces (Plan 005 Task 6)."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from build_tools.gen_op_surfaces.blocks import MarkerError, render_marked_file
from build_tools.gen_op_surfaces.constants import (
    BAKED_CAPABILITIES_REL,
    CANONICAL_CAPABILITIES_REL,
    EXIT_CHANGES_WRITTEN,
    EXIT_ERROR,
    EXIT_NO_CHANGE,
    ROUTE_CAPABILITIES_REL,
)


def _capabilities_only_targets() -> tuple[SurfaceTarget, ...]:
    return (
        SurfaceTarget(
            rel_path=BAKED_CAPABILITIES_REL,
            kind="whole_file",
            emit=capabilities_bytes,
        ),
    )
from build_tools.gen_op_surfaces.emit import (
    SurfaceTarget,
    capabilities_bytes,
    check_surfaces,
    surface_targets,
    write_surfaces,
)
from build_tools.gen_op_surfaces.paths import PathEscapeError, resolve_under_root

REPO_ROOT = Path(__file__).resolve().parents[3]
EXPORT_MODULE = "nextseek_api.cc_assistant.op_registry.export"
GEN_MODULE = "build_tools.gen_op_surfaces"
PYTHONPATH = f"{REPO_ROOT}:{REPO_ROOT / 'dmac_assistant' / 'src'}:{REPO_ROOT / 'chat_nextseek' / 'src'}"
DMAC_PYTHON = Path("/home/taishajo/work/dmac-assistant/.venv/bin/python3")


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
    canonical = resolve_under_root(REPO_ROOT, CANONICAL_CAPABILITIES_REL)
    baked = resolve_under_root(REPO_ROOT, BAKED_CAPABILITIES_REL)
    assert baked.read_bytes() == canonical.read_bytes()
    assert capabilities_bytes(REPO_ROOT) == canonical.read_bytes()


def test_check_surfaces_passes_on_current_tree() -> None:
    check_surfaces(repo_root=REPO_ROOT)


def test_stale_capabilities_copy_fails_check(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    canonical = repo / CANONICAL_CAPABILITIES_REL
    baked = repo / BAKED_CAPABILITIES_REL
    canonical.parent.mkdir(parents=True)
    baked.parent.mkdir(parents=True)
    canonical.write_bytes(b"canonical bytes\n")
    baked.write_bytes(b"stale bytes\n")
    with pytest.raises(SystemExit) as exc:
        check_surfaces(repo_root=repo, targets=_capabilities_only_targets())
    assert exc.value.code != 0


def test_write_surfaces_is_idempotent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    canonical = repo / CANONICAL_CAPABILITIES_REL
    baked = repo / BAKED_CAPABILITIES_REL
    canonical.parent.mkdir(parents=True)
    baked.parent.mkdir(parents=True)
    canonical.write_bytes(b"same\n")
    baked.write_bytes(b"same\n")
    assert write_surfaces(repo_root=repo, targets=_capabilities_only_targets()) == EXIT_NO_CHANGE
    assert write_surfaces(repo_root=repo, targets=_capabilities_only_targets()) == EXIT_NO_CHANGE


def test_write_surfaces_returns_exit_changes_written(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    canonical = repo / CANONICAL_CAPABILITIES_REL
    baked = repo / BAKED_CAPABILITIES_REL
    canonical.parent.mkdir(parents=True)
    baked.parent.mkdir(parents=True)
    canonical.write_bytes(b"canonical\n")
    baked.write_bytes(b"stale\n")
    assert write_surfaces(repo_root=repo, targets=_capabilities_only_targets()) == EXIT_CHANGES_WRITTEN
    assert baked.read_bytes() == b"canonical\n"
    assert write_surfaces(repo_root=repo, targets=_capabilities_only_targets()) == EXIT_NO_CHANGE


def test_check_mode_does_not_mutate_committed_targets(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    canonical = repo / CANONICAL_CAPABILITIES_REL
    baked = repo / BAKED_CAPABILITIES_REL
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
        cmd = ["uv", "run", "--no-project"]
        if with_pydantic:
            cmd.extend(["--with", "pydantic"])
        cmd.extend(["python", "-m", module, *args])
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
        python=str(DMAC_PYTHON),
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
    target_paths = [
        REPO_ROOT / CANONICAL_CAPABILITIES_REL,
        REPO_ROOT / BAKED_CAPABILITIES_REL,
        REPO_ROOT / "nextseek_api/cc_assistant/op_registry/ops.json",
        REPO_ROOT
        / "docker/cc-runtime/build_context/plugins/nextseek/context/ops.json",
    ]
    existing = [path for path in target_paths if path.is_file()]
    before = {path: path.read_bytes() for path in existing}
    original_modes = {path: path.stat().st_mode for path in existing}

    for path in existing:
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

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
            python=str(DMAC_PYTHON),
        )
        assert surfaces.returncode == 0, surfaces.stderr or surfaces.stdout

        after = {path: path.read_bytes() for path in existing}
        assert before == after
    finally:
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
