"""Unit tests for generated container CLAUDE.md inventories (Plan 005 Task 10)."""
from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from build_tools.gen_op_surfaces.claude_md import (
    ClaudeMdDocsError,
    emit_claude_ops_block,
    emit_claude_plugins_block,
    emit_claude_skills_block,
    extract_nextseek_docs_block,
    guard_claude_md_render,
    installed_claude_ops,
    installed_claude_plugins,
    installed_claude_skills,
    parse_claude_ops_block,
    parse_claude_plugins_block,
    parse_claude_skills_block,
    validate_plan005_markers_outside_docs,
)
from build_tools.gen_op_surfaces.constants import (
    CLAUDE_MD_REL,
    CLAUDE_OPS_BEGIN,
    CLAUDE_OPS_END,
    CLAUDE_PLUGINS_BEGIN,
    CLAUDE_PLUGINS_END,
    CLAUDE_SKILLS_BEGIN,
    CLAUDE_SKILLS_END,
    CONTENT_HASH_REL,
    DOCKERFILE_REL,
    NEXTSEEK_DOCS_BEGIN,
    NEXTSEEK_DOCS_END,
    NEXTSEEK_DOCS_PIN_REF,
)
from build_tools.gen_op_surfaces.emit import (
    SurfaceTarget,
    check_surfaces,
    surface_targets,
    write_surfaces,
)
from nextseek_api.cc_assistant.op_registry.install_oracle import discover_install
from nextseek_api.cc_assistant.op_registry.models import GateClass, OpSpec, Transport
from nextseek_api.cc_assistant.op_registry.ops import OPS

REPO_ROOT = Path(__file__).resolve().parents[3]
CLAUDE_MD = REPO_ROOT / CLAUDE_MD_REL
CONTENT_HASH = REPO_ROOT / CONTENT_HASH_REL

REQUIRED_PROSE = (
    "Confirm every write with the user conversationally before executing it.",
    "It builds and validates the payload for the user to inspect and never uploads.",
    "Reingest exception (also load-bearing)",
    "NOT `nextseek-batch-upload`",
    "Never call `AskUserQuestion`.",
    "Do NOT retry a third time.",
    "Write-safety on NExtSEEK.",
)


def _git_show(rel: str) -> bytes:
    return subprocess.check_output(
        ["git", "show", f"{NEXTSEEK_DOCS_PIN_REF}:{rel}"],
        cwd=REPO_ROOT,
    )


def _fixture_op(op_id: str, bin_name: str, *, purpose: str = "") -> OpSpec:
    from nextseek_api.cc_assistant.op_registry.models import SkillRow

    return OpSpec(
        op_id=op_id,
        bin_name=bin_name,
        runner_key=op_id,
        runner="bin/_nextseek_runner.py",
        transport=Transport.sidecar,
        assistant_endpoint=f"/nextseek_api/assistant/{op_id}/",
        gate_class=GateClass.read,
        skill_row=SkillRow(purpose=purpose, input=f"--{op_id}", output="{ok}")
        if purpose
        else None,
    )


def _write_plugin(
    plugins_root: Path,
    name: str,
    *,
    skills: tuple[str, ...] = (),
    shims: tuple[str, ...] = (),
) -> Path:
    plugin_dir = plugins_root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": name,
        "version": "0.0.1",
        "description": "synthetic plugin",
    }
    (manifest_dir / "plugin.json").write_text(json.dumps(payload), encoding="utf-8")
    for skill_name in skills:
        skill_path = plugin_dir / "skills" / skill_name / "SKILL.md"
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(f"---\nname: {skill_name}\n---\n", encoding="utf-8")
    if shims:
        bin_dir = plugin_dir / "bin"
        bin_dir.mkdir(exist_ok=True)
        for shim_name in shims:
            shim = bin_dir / shim_name
            shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            shim.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    else:
        (plugin_dir / "bin").mkdir(exist_ok=True)
    return plugin_dir


def _seed_claude_repo(
    tmp_path: Path,
    plugin_names: tuple[str, ...] = ("alpha-plugin",),
) -> Path:
    repo = tmp_path / "repo"
    plugins_root = repo / "docker/cc-runtime/build_context/plugins"
    plugins_root.mkdir(parents=True)
    dockerfile_lines = ["FROM scratch"]
    for name in plugin_names:
        skills = (f"{name}-skill",) if name == "alpha-plugin" else (f"{name}-skill",)
        shims = (f"nextseek-{name}-op",)
        _write_plugin(plugins_root, name, skills=skills, shims=shims)
        dockerfile_lines.append(
            f"COPY build_context/plugins/{name}/ /app/plugins/{name}/"
        )
        dockerfile_lines.append(f'ENV PATH="/app/plugins/{name}/bin:${{PATH}}"')
    dockerfile = repo / DOCKERFILE_REL
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    dockerfile.write_text("\n".join(dockerfile_lines) + "\n", encoding="utf-8")

    claude = repo / CLAUDE_MD_REL
    claude.parent.mkdir(parents=True, exist_ok=True)
    claude.write_text(
        "\n".join(
            [
                "# In-Container Agent Instructions",
                "Write-safety on NExtSEEK. Confirm every write.",
                "## Plugins available in this image",
                CLAUDE_PLUGINS_BEGIN,
                CLAUDE_PLUGINS_END,
                "plugin-prose-outside",
                "Installed bin ops:",
                CLAUDE_OPS_BEGIN,
                "hand-authored-row",
                CLAUDE_OPS_END,
                "## Skills in this image",
                "Routing rule (load-bearing): batch-upload owns create/update.",
                "Reingest exception (also load-bearing) stays human text.",
                CLAUDE_SKILLS_BEGIN,
                CLAUDE_SKILLS_END,
                "Never call `AskUserQuestion`.",
                "Do NOT retry a third time.",
                NEXTSEEK_DOCS_BEGIN,
                "## NExtSEEK Documentation",
                "Pinned docs body.",
                NEXTSEEK_DOCS_END,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return repo


def _claude_targets(
    *,
    plugins_root: Path | None = None,
    dockerfile_path: Path | None = None,
    ops: list[OpSpec] | None = None,
) -> tuple[SurfaceTarget, ...]:
    return (
        SurfaceTarget(
            rel_path=CLAUDE_MD_REL,
            kind="marked_block",
            begin_marker=CLAUDE_PLUGINS_BEGIN,
            end_marker=CLAUDE_PLUGINS_END,
            emit=lambda root: emit_claude_plugins_block(
                root,
                plugins_root=plugins_root,
                dockerfile_path=dockerfile_path,
            ),
        ),
        SurfaceTarget(
            rel_path=CLAUDE_MD_REL,
            kind="marked_block",
            begin_marker=CLAUDE_SKILLS_BEGIN,
            end_marker=CLAUDE_SKILLS_END,
            emit=lambda root: emit_claude_skills_block(
                root,
                plugins_root=plugins_root,
                dockerfile_path=dockerfile_path,
            ),
        ),
        SurfaceTarget(
            rel_path=CLAUDE_MD_REL,
            kind="marked_block",
            begin_marker=CLAUDE_OPS_BEGIN,
            end_marker=CLAUDE_OPS_END,
            emit=lambda root: emit_claude_ops_block(
                root,
                ops=ops,
                plugins_root=plugins_root,
                dockerfile_path=dockerfile_path,
            ),
        ),
    )


def _independent_expected(
    repo: Path,
    ops: list[OpSpec],
    *,
    plugins_root: Path | None = None,
    dockerfile_path: Path | None = None,
) -> tuple[frozenset[str], frozenset[tuple[str, str]], frozenset[tuple[str, str, str]]]:
    plugins_root = plugins_root or (
        repo / "docker/cc-runtime/build_context/plugins"
    )
    dockerfile_path = dockerfile_path or (repo / DOCKERFILE_REL)
    discovery = discover_install(
        plugins_root=plugins_root,
        dockerfile_path=dockerfile_path,
    )
    plugins = frozenset(discovery.plugins)
    skills = frozenset(
        (skill.plugin_dir, skill.skill_name) for skill in discovery.skills
    )
    shims = {shim.shim_name for shim in discovery.shims}
    op_rows = frozenset(
        (
            op.bin_name,
            op.op_id,
            op.skill_row.purpose.strip() if op.skill_row is not None else "",
        )
        for op in ops
        if op.available and op.bin_name in shims
    )
    return plugins, skills, op_rows


def test_parse_inventory_blocks_exact_set_equality() -> None:
    text = "\n".join(
        [
            CLAUDE_PLUGINS_BEGIN,
            "nextseek",
            "omega",
            CLAUDE_PLUGINS_END,
            CLAUDE_SKILLS_BEGIN,
            "nextseek\tnextseek",
            "nextseek\tnextseek-batch-upload",
            CLAUDE_SKILLS_END,
            CLAUDE_OPS_BEGIN,
            "nextseek-query\tquery\tQuery purpose",
            "nextseek-recall\trecall\tRecall purpose",
            CLAUDE_OPS_END,
            "",
        ]
    )
    assert parse_claude_plugins_block(text) == {"nextseek", "omega"}
    assert parse_claude_skills_block(text) == {
        ("nextseek", "nextseek"),
        ("nextseek", "nextseek-batch-upload"),
    }
    assert parse_claude_ops_block(text) == {
        ("nextseek-query", "query", "Query purpose"),
        ("nextseek-recall", "recall", "Recall purpose"),
    }


def test_emit_matches_independently_derived_oracle_sets(tmp_path: Path) -> None:
    repo = _seed_claude_repo(tmp_path, ("alpha-plugin",))
    plugins_root = repo / "docker/cc-runtime/build_context/plugins"
    dockerfile = repo / DOCKERFILE_REL
    ops = [
        _fixture_op("alpha", "nextseek-alpha-plugin-op", purpose="Alpha purpose"),
    ]
    expected_plugins, expected_skills, expected_ops = _independent_expected(
        repo, ops, plugins_root=plugins_root, dockerfile_path=dockerfile
    )
    plugin_block = emit_claude_plugins_block(
        repo, plugins_root=plugins_root, dockerfile_path=dockerfile
    )
    skill_block = emit_claude_skills_block(
        repo, plugins_root=plugins_root, dockerfile_path=dockerfile
    )
    op_block = emit_claude_ops_block(
        repo, ops=ops, plugins_root=plugins_root, dockerfile_path=dockerfile
    )
    assert parse_claude_plugins_block(
        f"{CLAUDE_PLUGINS_BEGIN}\n{plugin_block}{CLAUDE_PLUGINS_END}\n"
    ) == expected_plugins
    assert parse_claude_skills_block(
        f"{CLAUDE_SKILLS_BEGIN}\n{skill_block}{CLAUDE_SKILLS_END}\n"
    ) == expected_skills
    assert parse_claude_ops_block(
        f"{CLAUDE_OPS_BEGIN}\n{op_block}{CLAUDE_OPS_END}\n"
    ) == expected_ops
    assert expected_plugins == {"alpha-plugin"}
    assert expected_skills == {("alpha-plugin", "alpha-plugin-skill")}
    assert expected_ops == {
        ("nextseek-alpha-plugin-op", "alpha", "Alpha purpose")
    }


def test_fixture_add_remove_propagates_without_emitter_change(tmp_path: Path) -> None:
    repo = _seed_claude_repo(tmp_path, ("alpha-plugin",))
    plugins_root = repo / "docker/cc-runtime/build_context/plugins"
    dockerfile = repo / DOCKERFILE_REL
    base_ops = [
        _fixture_op("alpha", "nextseek-alpha-plugin-op", purpose="Alpha purpose"),
    ]
    base_plugins, base_skills, base_ops_set = _independent_expected(
        repo, base_ops, plugins_root=plugins_root, dockerfile_path=dockerfile
    )
    parsed_plugins = parse_claude_plugins_block(
        f"{CLAUDE_PLUGINS_BEGIN}\n"
        f"{emit_claude_plugins_block(repo, plugins_root=plugins_root, dockerfile_path=dockerfile)}"
        f"{CLAUDE_PLUGINS_END}\n"
    )
    assert parsed_plugins == base_plugins

    _write_plugin(
        plugins_root,
        "omega-plugin",
        skills=("omega-skill",),
        shims=("nextseek-omega-plugin-op",),
    )
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8")
        + "COPY build_context/plugins/omega-plugin/ /app/plugins/omega-plugin/\n"
        + 'ENV PATH="/app/plugins/omega-plugin/bin:${PATH}"\n',
        encoding="utf-8",
    )
    added_ops = [
        *base_ops,
        _fixture_op("omega", "nextseek-omega-plugin-op", purpose="Omega purpose"),
    ]
    added_plugins, added_skills, added_ops_set = _independent_expected(
        repo, added_ops, plugins_root=plugins_root, dockerfile_path=dockerfile
    )
    assert added_plugins - base_plugins == {"omega-plugin"}
    assert added_skills - base_skills == {("omega-plugin", "omega-skill")}
    assert added_ops_set - base_ops_set == {
        ("nextseek-omega-plugin-op", "omega", "Omega purpose")
    }
    assert parse_claude_plugins_block(
        f"{CLAUDE_PLUGINS_BEGIN}\n"
        f"{emit_claude_plugins_block(repo, plugins_root=plugins_root, dockerfile_path=dockerfile)}"
        f"{CLAUDE_PLUGINS_END}\n"
    ) == added_plugins
    assert parse_claude_skills_block(
        f"{CLAUDE_SKILLS_BEGIN}\n"
        f"{emit_claude_skills_block(repo, plugins_root=plugins_root, dockerfile_path=dockerfile)}"
        f"{CLAUDE_SKILLS_END}\n"
    ) == added_skills
    assert parse_claude_ops_block(
        f"{CLAUDE_OPS_BEGIN}\n"
        f"{emit_claude_ops_block(repo, ops=added_ops, plugins_root=plugins_root, dockerfile_path=dockerfile)}"
        f"{CLAUDE_OPS_END}\n"
    ) == added_ops_set

    (plugins_root / "omega-plugin").rename(tmp_path / "omega-plugin-removed")
    dockerfile.write_text(
        "\n".join(
            [
                "FROM scratch",
                "COPY build_context/plugins/alpha-plugin/ /app/plugins/alpha-plugin/",
                'ENV PATH="/app/plugins/alpha-plugin/bin:${PATH}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    removed_plugins, removed_skills, removed_ops_set = _independent_expected(
        repo, added_ops, plugins_root=plugins_root, dockerfile_path=dockerfile
    )
    assert base_plugins - removed_plugins == set()
    assert "omega-plugin" not in removed_plugins
    assert parse_claude_plugins_block(
        f"{CLAUDE_PLUGINS_BEGIN}\n"
        f"{emit_claude_plugins_block(repo, plugins_root=plugins_root, dockerfile_path=dockerfile)}"
        f"{CLAUDE_PLUGINS_END}\n"
    ) == removed_plugins
    assert parse_claude_ops_block(
        f"{CLAUDE_OPS_BEGIN}\n"
        f"{emit_claude_ops_block(repo, ops=added_ops, plugins_root=plugins_root, dockerfile_path=dockerfile)}"
        f"{CLAUDE_OPS_END}\n"
    ) == removed_ops_set
    assert "nextseek-omega-plugin-op" not in {row[0] for row in removed_ops_set}


def test_marker_inside_docs_block_is_rejected() -> None:
    text = "\n".join(
        [
            CLAUDE_PLUGINS_BEGIN,
            "nextseek",
            CLAUDE_PLUGINS_END,
            NEXTSEEK_DOCS_BEGIN,
            CLAUDE_OPS_BEGIN,
            "nextseek-query\tquery\tinside docs",
            CLAUDE_OPS_END,
            NEXTSEEK_DOCS_END,
            "",
        ]
    )
    with pytest.raises(ClaudeMdDocsError, match="outside"):
        validate_plan005_markers_outside_docs(text)


def test_changing_docs_block_fails_pin_contract() -> None:
    pinned = extract_nextseek_docs_block(
        _git_show(CLAUDE_MD_REL).decode("utf-8")
    )
    mutated = pinned.replace("NExtSEEK Documentation", "mutated documentation heading")
    assert mutated != pinned
    with pytest.raises(AssertionError):
        assert mutated == pinned


def test_guard_rejects_docs_block_rewrite() -> None:
    original = CLAUDE_MD.read_text(encoding="utf-8")
    updated = original.replace("NExtSEEK Documentation", "mutated documentation heading")
    with pytest.raises(ClaudeMdDocsError, match="byte-identical"):
        guard_claude_md_render(original=original, updated=updated)


def test_changing_content_hash_fails_pin_contract() -> None:
    pinned = _git_show(CONTENT_HASH_REL)
    mutated = b"0" * 64 + b"\n"
    assert mutated != pinned
    with pytest.raises(AssertionError):
        assert mutated == pinned


def test_committed_docs_block_and_hash_match_pin() -> None:
    current_text = CLAUDE_MD.read_text(encoding="utf-8")
    pinned_text = _git_show(CLAUDE_MD_REL).decode("utf-8")
    assert extract_nextseek_docs_block(current_text) == extract_nextseek_docs_block(
        pinned_text
    )
    assert CONTENT_HASH.read_bytes() == _git_show(CONTENT_HASH_REL)
    validate_plan005_markers_outside_docs(current_text)


def test_write_keeps_markers_outside_docs_and_preserves_prose(tmp_path: Path) -> None:
    repo = _seed_claude_repo(tmp_path)
    plugins_root = repo / "docker/cc-runtime/build_context/plugins"
    dockerfile = repo / DOCKERFILE_REL
    claude = repo / CLAUDE_MD_REL
    original = claude.read_text(encoding="utf-8")
    original_docs = extract_nextseek_docs_block(original)
    ops = [_fixture_op("alpha", "nextseek-alpha-plugin-op", purpose="Alpha purpose")]
    write_surfaces(
        repo_root=repo,
        targets=_claude_targets(
            plugins_root=plugins_root,
            dockerfile_path=dockerfile,
            ops=ops,
        ),
    )
    updated = claude.read_text(encoding="utf-8")
    validate_plan005_markers_outside_docs(updated)
    assert extract_nextseek_docs_block(updated) == original_docs
    assert "plugin-prose-outside" in updated
    assert "Routing rule (load-bearing): batch-upload owns create/update." in updated
    assert "Reingest exception (also load-bearing) stays human text." in updated
    assert "Never call `AskUserQuestion`." in updated
    assert "Do NOT retry a third time." in updated
    ops_block = updated[
        updated.index(CLAUDE_OPS_BEGIN) : updated.index(CLAUDE_OPS_END)
    ]
    assert "hand-authored-row" not in ops_block
    assert "| Op |" not in ops_block


def test_committed_inventories_match_independent_oracle() -> None:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    validate_plan005_markers_outside_docs(text)
    discovery = discover_install(
        plugins_root=REPO_ROOT / "docker/cc-runtime/build_context/plugins",
        dockerfile_path=REPO_ROOT / DOCKERFILE_REL,
    )
    expected_plugins, expected_skills, expected_ops = _independent_expected(
        REPO_ROOT, OPS
    )
    assert parse_claude_plugins_block(text) == expected_plugins
    assert parse_claude_skills_block(text) == expected_skills
    assert parse_claude_ops_block(text) == expected_ops
    assert parse_claude_plugins_block(text) == installed_claude_plugins(discovery)
    assert parse_claude_skills_block(text) == installed_claude_skills(discovery)
    assert parse_claude_ops_block(text) == installed_claude_ops(discovery, OPS)
    assert "hand-authored" not in text[
        text.index(CLAUDE_OPS_BEGIN) : text.index(CLAUDE_OPS_END)
    ]
    for phrase in REQUIRED_PROSE:
        assert phrase in text


def test_claude_md_surfaces_are_registered() -> None:
    rel_and_markers = {
        (target.rel_path, target.begin_marker)
        for target in surface_targets(REPO_ROOT)
    }
    assert (CLAUDE_MD_REL, CLAUDE_PLUGINS_BEGIN) in rel_and_markers
    assert (CLAUDE_MD_REL, CLAUDE_SKILLS_BEGIN) in rel_and_markers
    assert (CLAUDE_MD_REL, CLAUDE_OPS_BEGIN) in rel_and_markers
