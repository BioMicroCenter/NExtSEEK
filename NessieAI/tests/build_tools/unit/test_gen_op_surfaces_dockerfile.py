"""Unit tests for generated Dockerfile COPY/PATH and Compose context (Plan 005 Task 9)."""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from NessieAI import paths
from NessieAI.build_tools.gen_op_surfaces.constants import (
    ADDITIONAL_CONTEXTS_BEGIN,
    ADDITIONAL_CONTEXTS_END,
    CANONICAL_CONTEXT_FILES,
    CAPABILITIES_COPY_BEGIN,
    CAPABILITIES_COPY_END,
    COMPOSE_REL,
    DOCKERFILE_REL,
    IMAGE_BAML_SRC_PATH,
    NAMED_BAML_CONTEXT,
    NAMED_BAML_CONTEXT_PATH,
    NAMED_BUILD_CONTEXTS,
    NAMED_CAPABILITIES_CONTEXT,
    NAMED_CAPABILITIES_CONTEXT_PATH,
    PLUGIN_COPY_BEGIN,
    PLUGIN_COPY_END,
    PLUGIN_PATH_BEGIN,
    PLUGIN_PATH_END,
    PLUGINS_ROOT_REL,
)
from NessieAI.build_tools.gen_op_surfaces.blocks import render_marked_file
from NessieAI.build_tools.gen_op_surfaces.docker_blocks import (
    BamlContextError,
    CanonicalCapabilitiesError,
    ComposeContextError,
    emit_additional_contexts_block,
    emit_capabilities_copy_block,
    emit_plugin_copy_block,
    emit_plugin_path_block,
    four_install_sets,
    parse_additional_contexts_block,
    parse_plugin_copy_names,
    parse_plugin_path_names,
    validate_baml_context_copy,
    validate_canonical_capabilities_final_writer,
    validate_canonical_context_final_writers,
    validate_compose_named_context,
    validate_compose_named_contexts,
)
from NessieAI.build_tools.gen_op_surfaces.emit import (
    SurfaceTarget,
    check_surfaces,
    surface_targets,
    write_surfaces,
)
from NessieAI.cc.op_registry.install_oracle import discover_install

REPO_ROOT = paths.REPO_ROOT
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DOCKERFILE = paths.CC_RUNTIME_DIR / "Dockerfile"
PLUGINS_ROOT = paths.CC_PLUGIN_DIR.parent
# The context NAME the Dockerfile COPYs from never moves; its PATH is the tree.
NAMED_CONTEXT = "chat_nextseek"
NAMED_CONTEXT_PATH = paths.repo_relative(paths.CHAT_NEXTSEEK_DIR)
CANONICAL_SRC = "src/chat_nextseek/context/capabilities.md"
IMAGE_CAPABILITIES = "/app/plugins/nextseek/context/capabilities.md"
# Every chat_nextseek context file the image bakes comes from the same named
# context (Phase C); the plugin tree keeps no copy of any of them.
CANONICAL_CONTEXT_NAMES = (
    "capabilities.md",
    "min_api_endpoints.json",
    "min_api_endpoints_enriched.json",
    "min_assays_db.json",
    "min_sampletypes_db.json",
    "projects_db.json",
    "ONTOLOGIES.json",
    "ontology_species.json",
    "ontology_sex.json",
    "ontology_organ.json",
    "ontology_disease.json",
    "ontology_units.json",
    "ontology_mouse_genotype.json",
    "ontology_mouse_strain.json",
    "ontology_library_strategy.json",
    "ontology_library_source.json",
    "ontology_library_selection.json",
    "ontology_library_layout.json",
    "ontology_extracted_molecule.json",
    "ontology_instrument_model.json",
    "ontology_file_format.json",
    "ontology_genome_assembly.json",
)
CANONICAL_CONTEXT_COPY_BLOCK = "".join(
    f"COPY --from={NAMED_CONTEXT} src/chat_nextseek/context/{name} "
    f"/app/plugins/nextseek/context/{name}\n"
    for name in CANONICAL_CONTEXT_NAMES
)
# The BAML sources reach the image through a second named context, which is the
# canonical BAML tree itself (Phase C); the cc-runtime mirror it replaced is gone.
BAML_CONTEXT = "dmac_assistant_baml"
BAML_CONTEXT_PATH = paths.repo_relative(paths.DMAC_ASSISTANT_DIR / "baml_src")
RETIRED_BAML_MIRROR = paths.repo_relative(paths.CC_RUNTIME_DIR / "baml_src")
IMAGE_BAML_SRC = "/app/baml_src/"
BAML_COPY = f"COPY --from={BAML_CONTEXT} . {IMAGE_BAML_SRC}"

COMPOSE_QUIET = [
    "timeout",
    "60s",
    "docker",
    "compose",
    "-f",
    str(COMPOSE_FILE),
    "config",
    "--no-env-resolution",
    "--quiet",
]
COMPOSE_JSON = [
    "timeout",
    "60s",
    "docker",
    "compose",
    "-f",
    str(COMPOSE_FILE),
    "config",
    "--no-env-resolution",
    "--format",
    "json",
]


def _write_manifest(plugin_dir: Path) -> None:
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": plugin_dir.name,
        "version": "0.0.1",
        "description": "synthetic plugin",
    }
    (manifest_dir / "plugin.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_plugin(plugins_root: Path, name: str, *, with_bin: bool = True) -> Path:
    plugin_dir = plugins_root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(plugin_dir)
    if with_bin:
        bin_dir = plugin_dir / "bin"
        bin_dir.mkdir(exist_ok=True)
        shim = bin_dir / "nextseek-fixture-op"
        shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        shim.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return plugin_dir


def _seed_docker_repo(tmp_path: Path, plugin_names: tuple[str, ...]) -> Path:
    repo = tmp_path / "repo"
    plugins_root = repo / PLUGINS_ROOT_REL
    dockerfile = repo / DOCKERFILE_REL
    compose = repo / COMPOSE_REL
    plugins_root.mkdir(parents=True)
    for name in plugin_names:
        _write_plugin(plugins_root, name)
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    dockerfile.write_text(
        "\n".join(
            [
                "FROM scratch",
                "prose-before-copy",
                PLUGIN_COPY_BEGIN,
                PLUGIN_COPY_END,
                CAPABILITIES_COPY_BEGIN,
                CAPABILITIES_COPY_END,
                "prose-after-copy",
                PLUGIN_PATH_BEGIN,
                PLUGIN_PATH_END,
                "prose-after-path",
                "",
            ]
        ),
        encoding="utf-8",
    )
    compose.write_text(
        "\n".join(
            [
                "services:",
                "  cc-agent:",
                "    build:",
                "      context: ./NessieAI/docker/cc-runtime",
                ADDITIONAL_CONTEXTS_BEGIN,
                ADDITIONAL_CONTEXTS_END,
                "    image: dmac-assistant:poc",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return repo


def _docker_targets() -> tuple[SurfaceTarget, ...]:
    return tuple(
        target
        for target in (
            SurfaceTarget(
                rel_path=DOCKERFILE_REL,
                kind="marked_block",
                begin_marker=PLUGIN_COPY_BEGIN,
                end_marker=PLUGIN_COPY_END,
                emit=emit_plugin_copy_block,
            ),
            SurfaceTarget(
                rel_path=DOCKERFILE_REL,
                kind="marked_block",
                begin_marker=CAPABILITIES_COPY_BEGIN,
                end_marker=CAPABILITIES_COPY_END,
                emit=emit_capabilities_copy_block,
            ),
            SurfaceTarget(
                rel_path=DOCKERFILE_REL,
                kind="marked_block",
                begin_marker=PLUGIN_PATH_BEGIN,
                end_marker=PLUGIN_PATH_END,
                emit=emit_plugin_path_block,
            ),
            SurfaceTarget(
                rel_path=COMPOSE_REL,
                kind="marked_block",
                begin_marker=ADDITIONAL_CONTEXTS_BEGIN,
                end_marker=ADDITIONAL_CONTEXTS_END,
                emit=emit_additional_contexts_block,
            ),
        )
    )


def test_four_sets_agree_after_write(tmp_path: Path) -> None:
    repo = _seed_docker_repo(tmp_path, ("alpha-plugin", "beta-plugin"))
    write_surfaces(repo_root=repo, targets=_docker_targets())
    plugins_root = repo / PLUGINS_ROOT_REL
    dockerfile = repo / DOCKERFILE_REL
    dirs, copies, installed, paths = four_install_sets(
        plugins_root=plugins_root,
        dockerfile_path=dockerfile,
    )
    assert dirs == copies == installed == paths == {"alpha-plugin", "beta-plugin"}
    text = dockerfile.read_text(encoding="utf-8")
    assert parse_plugin_copy_names(text) == {"alpha-plugin", "beta-plugin"}
    assert parse_plugin_path_names(text) == {"alpha-plugin", "beta-plugin"}
    assert "${PATH}" in text
    assert "COPY build_context/plugins/" not in text.replace(
        "COPY build_context/plugins/alpha-plugin/", ""
    ).replace("COPY build_context/plugins/beta-plugin/", "")


def test_fixture_plugin_fails_check_until_regeneration(tmp_path: Path) -> None:
    repo = _seed_docker_repo(tmp_path, ("keep-plugin",))
    write_surfaces(repo_root=repo, targets=_docker_targets())
    check_surfaces(repo_root=repo, targets=_docker_targets())
    plugins_root = repo / PLUGINS_ROOT_REL
    dockerfile = repo / DOCKERFILE_REL
    _write_plugin(plugins_root, "fixture-plugin")
    with pytest.raises(SystemExit):
        check_surfaces(repo_root=repo, targets=_docker_targets())
    write_surfaces(repo_root=repo, targets=_docker_targets())
    dirs, copies, installed, paths = four_install_sets(
        plugins_root=plugins_root,
        dockerfile_path=dockerfile,
    )
    assert dirs == copies == installed == paths == {"keep-plugin", "fixture-plugin"}
    check_surfaces(repo_root=repo, targets=_docker_targets())


def test_write_preserves_dockerfile_and_compose_prose(tmp_path: Path) -> None:
    repo = _seed_docker_repo(tmp_path, ("keep-plugin",))
    write_surfaces(repo_root=repo, targets=_docker_targets())
    dockerfile_text = (repo / DOCKERFILE_REL).read_text(encoding="utf-8")
    compose_text = (repo / COMPOSE_REL).read_text(encoding="utf-8")
    assert "prose-before-copy" in dockerfile_text
    assert "prose-after-copy" in dockerfile_text
    assert "prose-after-path" in dockerfile_text
    assert "image: dmac-assistant:poc" in compose_text
    assert "context: ./NessieAI/docker/cc-runtime" in compose_text


def test_canonical_capabilities_is_final_writer() -> None:
    text = "\n".join(
        [
            "COPY build_context/plugins/nextseek/ /app/plugins/nextseek/",
            f"COPY --from={NAMED_CONTEXT} {CANONICAL_SRC} {IMAGE_CAPABILITIES}",
        ]
    )
    validate_canonical_capabilities_final_writer(text)


def test_missing_named_context_copy_fails() -> None:
    with pytest.raises(CanonicalCapabilitiesError, match="named context|chat_nextseek"):
        validate_canonical_capabilities_final_writer(
            "COPY build_context/plugins/nextseek/ /app/plugins/nextseek/\n"
        )


def test_wrong_capabilities_source_or_destination_fails() -> None:
    with pytest.raises(CanonicalCapabilitiesError):
        validate_canonical_capabilities_final_writer(
            f"COPY --from={NAMED_CONTEXT} wrong.md {IMAGE_CAPABILITIES}\n"
        )
    with pytest.raises(CanonicalCapabilitiesError):
        validate_canonical_capabilities_final_writer(
            f"COPY --from={NAMED_CONTEXT} {CANONICAL_SRC} /tmp/capabilities.md\n"
        )


def test_later_overwrite_of_capabilities_fails() -> None:
    text = "\n".join(
        [
            f"COPY --from={NAMED_CONTEXT} {CANONICAL_SRC} {IMAGE_CAPABILITIES}",
            "COPY build_context/plugins/nextseek/ /app/plugins/nextseek/",
        ]
    )
    with pytest.raises(CanonicalCapabilitiesError, match="overwrite|final"):
        validate_canonical_capabilities_final_writer(text)


def test_every_canonical_context_file_needs_a_final_named_context_writer() -> None:
    """The plugin COPY lays the tree down first; each canonical file's named-context
    COPY must follow it. One missing, or one overwritten afterwards, is refused."""
    plugin_copy = "COPY build_context/plugins/nextseek/ /app/plugins/nextseek/\n"
    validate_canonical_context_final_writers(plugin_copy + CANONICAL_CONTEXT_COPY_BLOCK)
    for name in CANONICAL_CONTEXT_NAMES:
        without = "".join(
            line + "\n"
            for line in CANONICAL_CONTEXT_COPY_BLOCK.splitlines()
            if not line.endswith(f"/{name}")
        )
        with pytest.raises(CanonicalCapabilitiesError, match=f"canonical {re.escape(name)}"):
            validate_canonical_context_final_writers(plugin_copy + without)
    with pytest.raises(CanonicalCapabilitiesError, match="overwrite"):
        validate_canonical_context_final_writers(CANONICAL_CONTEXT_COPY_BLOCK + plugin_copy)
    with pytest.raises(CanonicalCapabilitiesError, match="overwrite"):
        validate_canonical_context_final_writers(
            CANONICAL_CONTEXT_COPY_BLOCK
            + "COPY build_context/plugins/nextseek/context/min_assays_db.json "
            "/app/plugins/nextseek/context/min_assays_db.json\n"
        )


def test_compose_named_context_must_be_vendored_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    vendored = repo / NAMED_CONTEXT_PATH
    vendored.mkdir(parents=True)
    validate_compose_named_context(
        repo_root=repo,
        contexts={NAMED_CONTEXT: f"./{NAMED_CONTEXT_PATH}"},
    )
    with pytest.raises(ComposeContextError, match="missing"):
        validate_compose_named_context(repo_root=repo, contexts={})
    with pytest.raises(ComposeContextError, match="absolute|external"):
        validate_compose_named_context(
            repo_root=repo,
            contexts={NAMED_CONTEXT: "/tmp/chat_nextseek"},
        )
    with pytest.raises(ComposeContextError, match="absolute|external|traversal"):
        validate_compose_named_context(
            repo_root=repo,
            contexts={NAMED_CONTEXT: "../chat_nextseek"},
        )
    with pytest.raises(ComposeContextError, match="source|destination|resolve"):
        validate_compose_named_context(
            repo_root=repo,
            contexts={NAMED_CONTEXT: "./elsewhere"},
        )
    # The bare NAME is not a path any more: the pre-move ./chat_nextseek
    # is refused even when a directory of that name exists.
    (repo / NAMED_CONTEXT).mkdir()
    with pytest.raises(ComposeContextError, match="resolve"):
        validate_compose_named_context(
            repo_root=repo,
            contexts={NAMED_CONTEXT: "./chat_nextseek"},
        )


def test_named_context_name_is_kept_and_its_path_is_the_moved_tree() -> None:
    """The Dockerfile COPY --from= references the NAME, which never moves;
    the generated compose block maps that NAME onto the tree's PATH."""
    assert NAMED_CAPABILITIES_CONTEXT == NAMED_CONTEXT
    assert NAMED_CAPABILITIES_CONTEXT_PATH == NAMED_CONTEXT_PATH
    assert emit_additional_contexts_block(REPO_ROOT) == (
        "      additional_contexts:\n"
        f"        {NAMED_CONTEXT}: ./{NAMED_CONTEXT_PATH}\n"
        f"        {BAML_CONTEXT}: ./{BAML_CONTEXT_PATH}\n"
    )
    assert CANONICAL_CONTEXT_FILES == CANONICAL_CONTEXT_NAMES
    emitted = emit_capabilities_copy_block(REPO_ROOT)
    assert emitted == CANONICAL_CONTEXT_COPY_BLOCK
    assert emitted.startswith(
        f"COPY --from={NAMED_CONTEXT} {CANONICAL_SRC} {IMAGE_CAPABILITIES}\n"
    )
    assert (REPO_ROOT / NAMED_CONTEXT_PATH / CANONICAL_SRC).is_file()
    for name in CANONICAL_CONTEXT_NAMES:
        assert (REPO_ROOT / NAMED_CONTEXT_PATH / "src/chat_nextseek/context" / name).is_file()


def test_current_tree_four_sets_agree() -> None:
    plugins_root = PLUGINS_ROOT
    dirs, copies, installed, paths = four_install_sets(
        plugins_root=plugins_root,
        dockerfile_path=DOCKERFILE,
    )
    assert dirs == copies == installed == paths
    assert dirs
    text = DOCKERFILE.read_text(encoding="utf-8")
    validate_canonical_capabilities_final_writer(text)
    validate_canonical_context_final_writers(text)
    assert "COPY build_context/plugins/ /app/plugins/" not in text


def test_dockerfile_and_compose_are_registered_surface_targets() -> None:
    rel_paths = [target.rel_path for target in surface_targets(REPO_ROOT)]
    assert DOCKERFILE_REL in rel_paths
    assert COMPOSE_REL in rel_paths


def test_real_compose_config_quiet_succeeds() -> None:
    if shutil_which_docker():
        proc = subprocess.run(COMPOSE_QUIET, capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr or proc.stdout
        return
    compose = REPO_ROOT / COMPOSE_REL
    assert compose.is_file()
    validate_compose_named_contexts(
        repo_root=REPO_ROOT,
        contexts=parse_additional_contexts_block(compose.read_text(encoding="utf-8")),
    )


def test_real_compose_config_json_resolves_chat_nextseek_inside_repo() -> None:
    if shutil_which_docker():
        proc = subprocess.run(COMPOSE_JSON, capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr or proc.stdout
        payload = json.loads(proc.stdout)
        build = payload["services"]["cc-agent"]["build"]
        contexts = build.get("additional_contexts") or build.get("additionalContexts")
        assert isinstance(contexts, dict), f"expected named context map, got {contexts!r}"
        for name, rel in ((NAMED_CONTEXT, NAMED_CONTEXT_PATH), (BAML_CONTEXT, BAML_CONTEXT_PATH)):
            assert name in contexts
            resolved = Path(contexts[name]).resolve()
            expected = (REPO_ROOT / rel).resolve()
            assert resolved == expected
            assert resolved.is_dir()
        validate_compose_named_contexts(
            repo_root=REPO_ROOT,
            contexts={
                NAMED_CONTEXT: f"./{NAMED_CONTEXT_PATH}",
                BAML_CONTEXT: f"./{BAML_CONTEXT_PATH}",
            },
        )
        return
    for rel in (NAMED_CONTEXT_PATH, BAML_CONTEXT_PATH):
        assert (REPO_ROOT / rel).resolve().is_dir()
    validate_compose_named_contexts(
        repo_root=REPO_ROOT,
        contexts=parse_additional_contexts_block(
            (REPO_ROOT / COMPOSE_REL).read_text(encoding="utf-8")
        ),
    )


def shutil_which_docker() -> bool:
    from shutil import which

    return which("docker") is not None and which("timeout") is not None


def test_gen_op_surfaces_check_includes_docker_surfaces() -> None:
    check_surfaces(repo_root=REPO_ROOT)


def test_baml_named_context_constants() -> None:
    assert NAMED_BAML_CONTEXT == BAML_CONTEXT
    assert NAMED_BAML_CONTEXT_PATH == BAML_CONTEXT_PATH
    assert IMAGE_BAML_SRC_PATH == IMAGE_BAML_SRC
    assert NAMED_BUILD_CONTEXTS == (
        (NAMED_CONTEXT, NAMED_CONTEXT_PATH),
        (BAML_CONTEXT, BAML_CONTEXT_PATH),
    )


def test_compose_baml_context_must_be_the_canonical_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / BAML_CONTEXT_PATH).mkdir(parents=True)
    (repo / RETIRED_BAML_MIRROR).mkdir(parents=True)
    assert validate_compose_named_context(
        repo_root=repo,
        contexts={BAML_CONTEXT: f"./{BAML_CONTEXT_PATH}"},
        name=BAML_CONTEXT,
    ) == (repo / BAML_CONTEXT_PATH).resolve()
    with pytest.raises(ComposeContextError, match="missing"):
        validate_compose_named_context(repo_root=repo, contexts={}, name=BAML_CONTEXT)
    with pytest.raises(ComposeContextError, match="absolute|external"):
        validate_compose_named_context(
            repo_root=repo, contexts={BAML_CONTEXT: "/tmp/baml_src"}, name=BAML_CONTEXT
        )
    with pytest.raises(ComposeContextError, match="traversal"):
        validate_compose_named_context(
            repo_root=repo, contexts={BAML_CONTEXT: "../baml_src"}, name=BAML_CONTEXT
        )
    # The retired mirror exists as a directory here and is still refused.
    with pytest.raises(ComposeContextError, match="resolve"):
        validate_compose_named_context(
            repo_root=repo,
            contexts={BAML_CONTEXT: f"./{RETIRED_BAML_MIRROR}"},
            name=BAML_CONTEXT,
        )
    # Each context is checked against its own tree, not any declared tree.
    with pytest.raises(ComposeContextError, match="resolve"):
        validate_compose_named_context(
            repo_root=repo,
            contexts={BAML_CONTEXT: f"./{NAMED_CONTEXT_PATH}"},
            name=BAML_CONTEXT,
        )


def test_unknown_named_context_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ComposeContextError, match="unknown"):
        validate_compose_named_context(
            repo_root=tmp_path, contexts={"other": "./other"}, name="other"
        )


def test_every_named_context_is_required(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / NAMED_CONTEXT_PATH).mkdir(parents=True)
    (repo / BAML_CONTEXT_PATH).mkdir(parents=True)
    both = {
        NAMED_CONTEXT: f"./{NAMED_CONTEXT_PATH}",
        BAML_CONTEXT: f"./{BAML_CONTEXT_PATH}",
    }
    resolved = validate_compose_named_contexts(repo_root=repo, contexts=both)
    assert resolved == {
        NAMED_CONTEXT: (repo / NAMED_CONTEXT_PATH).resolve(),
        BAML_CONTEXT: (repo / BAML_CONTEXT_PATH).resolve(),
    }
    for name in both:
        partial = {key: value for key, value in both.items() if key != name}
        with pytest.raises(ComposeContextError, match=f"missing named context {name}"):
            validate_compose_named_contexts(repo_root=repo, contexts=partial)


def test_parse_additional_contexts_block_reads_what_the_generator_writes(tmp_path: Path) -> None:
    repo = _seed_docker_repo(tmp_path, ("keep-plugin",))
    write_surfaces(repo_root=repo, targets=_docker_targets())
    text = (repo / COMPOSE_REL).read_text(encoding="utf-8")
    assert parse_additional_contexts_block(text) == {
        name: f"./{rel}" for name, rel in NAMED_BUILD_CONTEXTS
    }


def test_parse_additional_contexts_block_refuses_what_it_cannot_read() -> None:
    with pytest.raises(ComposeContextError, match="marker"):
        parse_additional_contexts_block("services: {}\n")
    unreadable = render_marked_file(
        f"{ADDITIONAL_CONTEXTS_BEGIN}\n{ADDITIONAL_CONTEXTS_END}\n",
        ADDITIONAL_CONTEXTS_BEGIN,
        ADDITIONAL_CONTEXTS_END,
        "      additional_contexts:\n        - not a mapping entry\n",
    )
    with pytest.raises(ComposeContextError, match="unparsed"):
        parse_additional_contexts_block(unreadable)
    keyless = render_marked_file(
        f"{ADDITIONAL_CONTEXTS_BEGIN}\n{ADDITIONAL_CONTEXTS_END}\n",
        ADDITIONAL_CONTEXTS_BEGIN,
        ADDITIONAL_CONTEXTS_END,
        f"        {BAML_CONTEXT}: ./{BAML_CONTEXT_PATH}\n",
    )
    with pytest.raises(ComposeContextError, match="additional_contexts key"):
        parse_additional_contexts_block(keyless)


def test_committed_compose_block_maps_every_context_onto_its_tree() -> None:
    contexts = parse_additional_contexts_block(
        (REPO_ROOT / COMPOSE_REL).read_text(encoding="utf-8")
    )
    assert contexts == {name: f"./{rel}" for name, rel in NAMED_BUILD_CONTEXTS}
    resolved = validate_compose_named_contexts(repo_root=REPO_ROOT, contexts=contexts)
    assert resolved[BAML_CONTEXT] == (paths.DMAC_ASSISTANT_DIR / "baml_src").resolve()


def test_committed_docker_surfaces_are_current() -> None:
    """The Dockerfile and compose blocks are exactly what the generator emits.

    This checks the docker targets alone, so a hand edit of a named-context block
    is caught even when another target of the full ``--check`` is red.
    """
    docker_targets = [
        target
        for target in surface_targets(REPO_ROOT)
        if target.rel_path in (DOCKERFILE_REL, COMPOSE_REL)
    ]
    assert {target.rel_path for target in docker_targets} == {DOCKERFILE_REL, COMPOSE_REL}
    check_surfaces(repo_root=REPO_ROOT, targets=docker_targets)


def test_baml_context_copy_is_the_only_writer_of_the_image_tree() -> None:
    validate_baml_context_copy(f"{BAML_COPY}\n")
    validate_baml_context_copy(
        f"COPY tools/__init__.py /app/tools/__init__.py\n{BAML_COPY}\n"
        "COPY tools/e2e/judge_runner.py /app/tools/e2e/judge_runner.py\n"
    )


def test_baml_copy_from_the_build_context_is_refused() -> None:
    """The pre-Phase-C line: the image read a mirror kept in its own context."""
    with pytest.raises(BamlContextError, match="not the named context"):
        validate_baml_context_copy(f"COPY baml_src/ {IMAGE_BAML_SRC}\n")


def test_missing_or_extra_baml_writers_are_refused() -> None:
    with pytest.raises(BamlContextError, match="missing"):
        validate_baml_context_copy("COPY tools/__init__.py /app/tools/__init__.py\n")
    with pytest.raises(BamlContextError, match="more than one"):
        validate_baml_context_copy(
            f"{BAML_COPY}\nCOPY baml_src/router.baml {IMAGE_BAML_SRC}router.baml\n"
        )
    with pytest.raises(BamlContextError, match="more than one"):
        validate_baml_context_copy(f"{BAML_COPY}\nCOPY a.baml b.baml {IMAGE_BAML_SRC}\n")


def test_partial_or_misplaced_named_context_copy_is_refused() -> None:
    with pytest.raises(BamlContextError, match="whole tree"):
        validate_baml_context_copy(
            f"COPY --from={BAML_CONTEXT} router.baml {IMAGE_BAML_SRC}\n"
        )
    with pytest.raises(BamlContextError, match="not the named context"):
        validate_baml_context_copy(f"COPY --from={NAMED_CONTEXT} . {IMAGE_BAML_SRC}\n")
    with pytest.raises(BamlContextError, match="whole tree"):
        validate_baml_context_copy(f"COPY --from={BAML_CONTEXT} . /app/baml_src/sub/\n")


def test_current_dockerfile_takes_baml_from_the_named_context() -> None:
    validate_baml_context_copy(DOCKERFILE.read_text(encoding="utf-8"))
