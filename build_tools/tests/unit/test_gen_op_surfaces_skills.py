"""Unit tests for generated installed SKILL.md capability matrices (Plan 005 Task 8)."""
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from build_tools.gen_op_surfaces.constants import SKILL_OPS_BEGIN, SKILL_OPS_END
from build_tools.gen_op_surfaces.emit import (
    SurfaceTarget,
    check_surfaces,
    surface_targets,
    write_surfaces,
)
from build_tools.gen_op_surfaces.skills import (
    SkillOpsParseError,
    emit_skill_ops_block,
    installed_skill_ops_rows,
    parse_skill_ops_block,
)
from nextseek_api.cc_assistant.op_registry.models import (
    GateClass,
    OpSpec,
    SkillRow,
    Transport,
)
from nextseek_api.cc_assistant.op_registry.ops import OPS

REPO_ROOT = Path(__file__).resolve().parents[3]


def _fixture_op(
    op_id: str,
    bin_name: str,
    *,
    skill_name: str | None,
    purpose: str,
    transport: Transport = Transport.sidecar,
    gate_class: GateClass = GateClass.read,
    available: bool = True,
    per_op_gate_enabled: bool = True,
) -> OpSpec:
    return OpSpec(
        op_id=op_id,
        bin_name=bin_name,
        runner_key=op_id,
        runner="bin/_nextseek_runner.py",
        transport=transport,
        assistant_endpoint=f"/nextseek_api/assistant/{op_id}/",
        gate_class=gate_class,
        available=available,
        per_op_gate_enabled=per_op_gate_enabled,
        skill_name=skill_name,
        skill_row=SkillRow(purpose=purpose, input=f"--{op_id}", output="{ok}"),
    )


def _write_plugin(
    plugins_root: Path,
    *,
    plugin_name: str,
    skill_names: tuple[str, ...],
    shims: tuple[str, ...] = (),
    with_markers: bool = True,
) -> None:
    plugin_dir = plugins_root / plugin_name
    plugin_dir.mkdir(parents=True)
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir()
    payload = {
        "name": plugin_name,
        "version": "0.1.0",
        "description": "Claude Code plugin for NExtSEEK research workflows.",
        "author": {"name": "BMC"},
    }
    manifest_dir.joinpath("plugin.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    for skill_name in skill_names:
        skill_path = plugin_dir / "skills" / skill_name / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        body = [f"---\nname: {skill_name}\ndescription: test\n---", "", "prose-before"]
        if with_markers:
            body.extend(["", SKILL_OPS_BEGIN, SKILL_OPS_END])
        body.extend(["", "prose-after", ""])
        skill_path.write_text("\n".join(body), encoding="utf-8")
    if shims:
        shim_dir = plugin_dir / "bin"
        shim_dir.mkdir()
        for name in shims:
            path = shim_dir / name
            path.write_text("#!/bin/sh\n", encoding="utf-8")
            path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


def _write_dockerfile(path: Path, plugin_names: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["FROM scratch"]
    for plugin in plugin_names:
        lines.append(f"COPY build_context/plugins/{plugin}/ /app/plugins/{plugin}/")
        lines.append(f'ENV PATH="/app/plugins/{plugin}/bin:${{PATH}}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seed_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    plugins_root = repo / "docker/cc-runtime/build_context/plugins"
    dockerfile = repo / "docker/cc-runtime/Dockerfile"
    _write_plugin(
        plugins_root,
        plugin_name="alpha-plugin",
        skill_names=("alpha-skill", "beta-skill"),
        shims=("nextseek-alpha-op", "nextseek-beta-op", "nextseek-gamma-op"),
    )
    _write_dockerfile(dockerfile, ("alpha-plugin",))
    return repo, plugins_root, dockerfile


def test_parse_skill_ops_block_returns_exact_normalized_rows():
    text = (
        "prefix\n"
        f"{SKILL_OPS_BEGIN}\n"
        "alpha\tnextseek-alpha-op\t  Alpha purpose  \tsidecar\tread\ttrue\ttrue\n"
        "beta\tnextseek-beta-op\tBeta purpose\tviewset\tunrouted\tfalse\tfalse\n"
        f"{SKILL_OPS_END}\n"
        "suffix\n"
    )
    parsed = parse_skill_ops_block(text)
    assert parsed == {
        ("alpha", "nextseek-alpha-op", "Alpha purpose", "sidecar", "read", True, True),
        ("beta", "nextseek-beta-op", "Beta purpose", "viewset", "unrouted", False, False),
    }


def test_emit_matches_independently_derived_installed_rows(tmp_path: Path):
    repo, plugins_root, dockerfile = _seed_repo(tmp_path)
    ops = [
        _fixture_op("alpha", "nextseek-alpha-op", skill_name="alpha-skill", purpose="Alpha purpose"),
        _fixture_op("beta", "nextseek-beta-op", skill_name="beta-skill", purpose="Beta purpose"),
        _fixture_op("gamma", "nextseek-gamma-op", skill_name="alpha-skill", purpose="Gamma purpose"),
    ]
    block = emit_skill_ops_block(
        repo,
        skill_name="alpha-skill",
        ops=ops,
        plugins_root=plugins_root,
        dockerfile_path=dockerfile,
    )
    wrapped = f"{SKILL_OPS_BEGIN}\n{block}{SKILL_OPS_END}\n"
    parsed = parse_skill_ops_block(wrapped)
    expected = installed_skill_ops_rows(ops, skill_name="alpha-skill")
    assert parsed == expected
    assert {row[0] for row in parsed} == {"alpha", "gamma"}


@pytest.mark.parametrize(
    "mutator",
    [
        "missing",
        "duplicate",
        "swapped",
        "altered",
        "junk",
        "wrong-skill",
    ],
)
def test_mutated_skill_matrix_fails_field_equality(tmp_path: Path, mutator: str):
    repo, plugins_root, dockerfile = _seed_repo(tmp_path)
    ops = [
        _fixture_op("alpha", "nextseek-alpha-op", skill_name="alpha-skill", purpose="Alpha purpose"),
        _fixture_op("beta", "nextseek-beta-op", skill_name="beta-skill", purpose="Beta purpose"),
    ]
    good = emit_skill_ops_block(
        repo,
        skill_name="alpha-skill",
        ops=ops,
        plugins_root=plugins_root,
        dockerfile_path=dockerfile,
    )
    expected = installed_skill_ops_rows(ops, skill_name="alpha-skill")
    lines = [line for line in good.splitlines() if line.strip()]
    if mutator == "missing":
        mutated = ""
    elif mutator == "duplicate":
        mutated = lines[0] + "\n" + lines[0] + "\n"
    elif mutator == "swapped":
        parts = lines[0].split("\t")
        parts[0], parts[1] = parts[1], parts[0]
        mutated = "\t".join(parts) + "\n"
    elif mutator == "altered":
        parts = lines[0].split("\t")
        parts[2] = "semantically different purpose"
        mutated = "\t".join(parts) + "\n"
    elif mutator == "junk":
        mutated = "this is not a capability row\n"
    else:
        mutated = good + "beta\tnextseek-beta-op\tBeta purpose\tviewset\tread\ttrue\ttrue\n"

    wrapped = f"{SKILL_OPS_BEGIN}\n{mutated}{SKILL_OPS_END}\n"
    if mutator in {"duplicate", "junk"}:
        with pytest.raises(SkillOpsParseError):
            parse_skill_ops_block(wrapped)
        return
    parsed = parse_skill_ops_block(wrapped)
    assert parsed != expected


def test_fixture_skill_and_op_need_no_emitter_change(tmp_path: Path):
    repo, plugins_root, dockerfile = _seed_repo(tmp_path)
    extra_plugin = plugins_root / "omega-plugin"
    _write_plugin(
        plugins_root,
        plugin_name="omega-plugin",
        skill_names=("omega-skill",),
        shims=("nextseek-omega-op",),
    )
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8")
        + "COPY build_context/plugins/omega-plugin/ /app/plugins/omega-plugin/\n"
        + 'ENV PATH="/app/plugins/omega-plugin/bin:${PATH}"\n',
        encoding="utf-8",
    )
    ops = [
        _fixture_op("alpha", "nextseek-alpha-op", skill_name="alpha-skill", purpose="Alpha purpose"),
        _fixture_op("omega", "nextseek-omega-op", skill_name="omega-skill", purpose="Omega purpose"),
    ]
    block = emit_skill_ops_block(
        repo,
        skill_name="omega-skill",
        ops=ops,
        plugins_root=plugins_root,
        dockerfile_path=dockerfile,
    )
    parsed = parse_skill_ops_block(f"{SKILL_OPS_BEGIN}\n{block}{SKILL_OPS_END}\n")
    assert parsed == installed_skill_ops_rows(ops, skill_name="omega-skill")
    assert extra_plugin.joinpath("skills/omega-skill/SKILL.md").is_file()


def test_write_preserves_prose_outside_markers(tmp_path: Path):
    repo, plugins_root, dockerfile = _seed_repo(tmp_path)
    rel = "docker/cc-runtime/build_context/plugins/alpha-plugin/skills/alpha-skill/SKILL.md"
    skill_path = repo / rel
    original = skill_path.read_bytes()
    ops = [
        _fixture_op("alpha", "nextseek-alpha-op", skill_name="alpha-skill", purpose="Alpha purpose"),
    ]
    targets = (
        SurfaceTarget(
            rel_path=rel,
            kind="marked_block",
            begin_marker=SKILL_OPS_BEGIN,
            end_marker=SKILL_OPS_END,
            emit=lambda root: emit_skill_ops_block(
                root,
                skill_name="alpha-skill",
                ops=ops,
                plugins_root=plugins_root,
                dockerfile_path=dockerfile,
            ),
        ),
    )
    write_surfaces(repo_root=repo, targets=targets)
    updated = skill_path.read_text(encoding="utf-8")
    prefix, _, rest = original.decode("utf-8").partition(SKILL_OPS_BEGIN)
    _, _, suffix = rest.partition(SKILL_OPS_END)
    assert updated.startswith(prefix + SKILL_OPS_BEGIN)
    assert updated.endswith(SKILL_OPS_END + suffix)
    assert "prose-before" in updated
    assert "prose-after" in updated
    parsed = parse_skill_ops_block(updated)
    assert parsed == installed_skill_ops_rows(ops, skill_name="alpha-skill")


def test_skill_surfaces_are_registered_from_install_oracle():
    rel_paths = [target.rel_path for target in surface_targets(REPO_ROOT)]
    assert any(path.endswith("/skills/nextseek/SKILL.md") for path in rel_paths)
    assert any(
        path.endswith("/skills/nextseek-batch-upload/SKILL.md") for path in rel_paths
    )


def test_committed_skill_matrices_match_ops_projection():
    nextseek_path = (
        REPO_ROOT
        / "docker/cc-runtime/build_context/plugins/nextseek/skills/nextseek/SKILL.md"
    )
    batch_path = (
        REPO_ROOT
        / "docker/cc-runtime/build_context/plugins/nextseek/skills/nextseek-batch-upload/SKILL.md"
    )
    nextseek_parsed = parse_skill_ops_block(nextseek_path.read_text(encoding="utf-8"))
    batch_parsed = parse_skill_ops_block(batch_path.read_text(encoding="utf-8"))
    assert nextseek_parsed == installed_skill_ops_rows(OPS, skill_name="nextseek")
    assert batch_parsed == installed_skill_ops_rows(
        OPS, skill_name="nextseek-batch-upload"
    )
    assert not {row[0] for row in nextseek_parsed} & {row[0] for row in batch_parsed}


def test_gen_op_surfaces_check_covers_skill_matrices():
    check_surfaces(repo_root=REPO_ROOT)
