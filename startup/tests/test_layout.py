"""Pin startup/lib/layout.py, and the tracked env sources, to the real tree.

startup/ restates these locations instead of importing NessieAI/paths.py (it
never imports outside its own project), so nothing else would notice a later
move leaving one of them behind. Each test reads the file that actually
consumes the location.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from startup.lib import layout
from startup.steps import validate

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_proxy_secret_env_is_the_env_file_compose_gives_bedrock_proxy() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    assert f"./{layout.PROXY_SECRET_ENV.as_posix()}" in compose
    assert f"./{layout.LEGACY_PROXY_SECRET_ENV.as_posix()}" not in compose
    # The committed template sits beside the real (gitignored) file.
    example = REPO_ROOT / layout.PROXY_SECRET_ENV.parent / "proxy-secret.env.example"
    assert example.is_file()


def test_chat_nextseek_dir_is_where_the_unit_lives() -> None:
    assert (REPO_ROOT / layout.CHAT_NEXTSEEK_DIR / "pyproject.toml").is_file()


def _constants_literals() -> dict[str, object]:
    """The literal assignments in gen_op_surfaces' constants, read without importing it."""
    path = REPO_ROOT / "NessieAI" / "build_tools" / "gen_op_surfaces" / "constants.py"
    values: dict[str, object] = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                try:
                    values[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    continue
    return values


def test_canonical_context_files_are_the_ones_gen_op_surfaces_bakes() -> None:
    constants = _constants_literals()
    assert layout.CANONICAL_CONTEXT_FILES == constants["CANONICAL_CONTEXT_FILES"]
    assert layout.CC_AGENT_CONTEXT_DIR == constants["IMAGE_CONTEXT_DIR"]
    assert layout.CANONICAL_CONTEXT_DIR == (
        layout.CHAT_NEXTSEEK_DIR / constants["CANONICAL_CONTEXT_DIR_IN_CONTEXT"]
    )


def test_the_cc_agent_dockerfile_bakes_exactly_these_files_from_the_checkout() -> None:
    """The image check compares these six paths; the Dockerfile is what fills them.

    Each COPY reads the compose named context ``chat_nextseek``, which must be
    CHAT_NEXTSEEK_DIR, so the checkout file on one side of the comparison is the
    file the build copied.
    """
    dockerfile = (REPO_ROOT / "NessieAI" / "docker" / "cc-runtime" / "Dockerfile").read_text()
    copies = re.findall(
        r"^COPY --from=chat_nextseek src/chat_nextseek/context/(\S+) (\S+)$",
        dockerfile, re.M,
    )
    assert sorted(copies) == sorted(
        (name, f"{layout.CC_AGENT_CONTEXT_DIR}/{name}")
        for name in layout.CANONICAL_CONTEXT_FILES
    )
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    assert f"chat_nextseek: ./{layout.CHAT_NEXTSEEK_DIR.as_posix()}\n" in compose
    missing = [
        name for name in layout.CANONICAL_CONTEXT_FILES
        if not (REPO_ROOT / layout.CANONICAL_CONTEXT_DIR / name).is_file()
    ]
    assert missing == []


@pytest.mark.parametrize(
    "rel", ["startup/templates/nextseek.env.template", "docker/nextseek.env.example"]
)
def test_tracked_env_sources_name_a_catalog_that_exists(rel: str) -> None:
    """CATALOG_FILE is read when settings import, so a wrong value stops the site."""
    source = REPO_ROOT / rel
    values = [
        line.split("=", 1)[1].strip().strip('"')
        for line in source.read_text().splitlines()
        if line.startswith("CATALOG_FILE=")
    ]
    assert len(values) == 1
    assert values[0].startswith("/app/")
    assert (REPO_ROOT / values[0][len("/app/"):]).is_file()
    # And neither tracked source would trip the rebuild preflight.
    assert validate.find_stale_nessie_env_lines(source) == []


def _lane_script() -> str:
    return (REPO_ROOT / "startup" / "dev" / "run_full_test_lane.sh").read_text()


def test_full_lane_selection_names_only_paths_that_exist() -> None:
    """The lane refuses a tree missing any of these; this says so before a run."""
    script = _lane_script()
    block = re.search(r"^MOVED_AI_TESTS=\((.*?)^\)", script, re.S | re.M)
    assert block is not None
    moved = block.group(1).split()
    # The 19 schema_rag and AI API files that left nextseek_api/tests in the move.
    assert len(moved) == 19 == len(set(moved))
    missing = [m for m in moved if not (REPO_ROOT / m).is_file()]
    assert missing == []
    assert 'sh nextseek_api/tests startup/tests "${MOVED_AI_TESTS[@]}"' in script


def test_full_lane_catalog_is_inside_the_mounted_tree() -> None:
    script = _lane_script()
    values = set(re.findall(r"-e CATALOG_FILE=(\S+)", script))
    assert len(values) == 1
    (value,) = values
    assert value.startswith("/work/")
    assert (REPO_ROOT / value[len("/work/"):]).is_file()
