"""Hermetic tests for PLAN-7 Task 3: the ported Container-CC (CC) runtime
tree at ``docker/cc-runtime/``.

Purpose: prove that ``docker/cc-runtime/`` (the canonical, NExtSEEK-tracked
build target for the CC agent image) actually carries every asset the image
build needs -- Dockerfile, build-ignore file, ``container/CLAUDE.md``,
entrypoint, runner helper modules, plugin manifest, plugin ``SKILL.md``,
plugin command, plugin bin scripts, and context/catalog files -- and that
none of the excluded standalone WS/FastAPI server layer (``src/``,
``sidecar/``, ``main.py``) leaked into the port or got wired into the image's
ENTRYPOINT/CMD/entrypoint.sh.

No Docker, no network, no DB. File-presence and grep assertions only, against
the real repo tree checked out at test time (this is deliberately NOT mocked:
the whole point of this suite is to catch a missing/renamed file in the real
``docker/cc-runtime/`` directory).

Actually invoking ``docker build`` against this context is exercised
separately, outside the hermetic pytest suite (see task-3-report.md for the
command + output tail) -- building requires network for the base image and
npm/uv/apt dependencies, which the hermetic harness runs with
``--network none``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.host_only

REPO_ROOT = Path(__file__).resolve().parents[3]
CC_RUNTIME = REPO_ROOT / "docker" / "cc-runtime"
CC_RUNNER = REPO_ROOT / "docker" / "cc-runner"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ==========================================================================
# Top-level runtime assets
# ==========================================================================


def test_cc_runtime_directory_exists():
    assert CC_RUNTIME.is_dir(), (
        "docker/cc-runtime/ must exist as the canonical CC image build "
        "target (docker/cc-runner/ is a separate, non-production lean proof "
        "image and is NOT the canonical target)."
    )


def test_cc_runtime_dockerfile_present_and_non_empty():
    dockerfile = CC_RUNTIME / "Dockerfile"
    assert dockerfile.is_file()
    assert len(_read(dockerfile).strip()) > 0


def test_cc_runtime_build_ignore_file_present():
    ignore_file = CC_RUNTIME / ".dockerignore"
    assert ignore_file.is_file()


@pytest.mark.parametrize("secret_pattern", [
    "**/.env\n",
    "**/.env.example",
    "**/.env.prod",
    "**/.env.dev",
    "proxy-secret.env",
    ".git",
    ".venv",
])
def test_cc_runtime_build_ignore_covers_known_secret_filenames(secret_pattern):
    text = _read(CC_RUNTIME / ".dockerignore")
    assert secret_pattern.strip() in text, (
        f"docker/cc-runtime/.dockerignore must exclude {secret_pattern!r} "
        "from the build context."
    )


def test_cc_runtime_container_claude_md_present():
    claude_md = CC_RUNTIME / "container" / "CLAUDE.md"
    assert claude_md.is_file()
    text = _read(claude_md)
    assert "DMAC assistant" in text or "NExtSEEK" in text


def test_cc_runtime_entrypoint_present_and_executable_shape():
    entrypoint = CC_RUNTIME / "container" / "entrypoint.sh"
    assert entrypoint.is_file()
    text = _read(entrypoint)
    assert text.startswith("#!/bin/sh") or text.startswith("#!/bin/bash")


def test_cc_runtime_runner_helper_present():
    """runner_ns.py is the in-image NS-route runner helper the Dockerfile
    COPYs to /opt/dmac/runner_ns.py."""
    runner = CC_RUNTIME / "container" / "runner_ns.py"
    assert runner.is_file()
    assert "runner_ns" in _read(runner)


@pytest.mark.parametrize("helper", [
    "_ws_contract.py",
    "_assistant_models.py",
    "_assistant_client.py",
    "_sidecar_client.py",
    # #16 SP3: the shared NExtSEEK-credential resolver. runner_ns.py imports it
    # for auth_from_env, so it needs the same /opt/dmac/ COPY as the others.
    "_ns_auth.py",
])
def test_cc_runtime_runner_sibling_helpers_present(helper):
    """The Dockerfile COPYs these sibling helper modules to /opt/dmac/
    alongside runner_ns.py; they must be sourced from the plugin bin/ dir in
    the port (matching the original Dockerfile's COPY lines)."""
    path = CC_RUNTIME / "build_context" / "plugins" / "nextseek" / "bin" / helper
    assert path.is_file(), f"missing runner sibling helper: {path}"


# ==========================================================================
# Plugin manifest / skill / command / bin scripts
# ==========================================================================


def test_cc_runtime_plugin_manifest_present_and_valid_json():
    manifest = CC_RUNTIME / "build_context" / "plugins" / "nextseek" / ".claude-plugin" / "plugin.json"
    assert manifest.is_file()
    data = json.loads(_read(manifest))
    assert data["name"] == "nextseek"


def test_cc_runtime_plugin_skill_md_present():
    skill = CC_RUNTIME / "build_context" / "plugins" / "nextseek" / "skills" / "nextseek" / "SKILL.md"
    assert skill.is_file()
    text = _read(skill)
    assert text.startswith("---")
    assert "nextseek-query" in text
    assert "nextseek-recall" in text


def test_cc_runtime_plugin_command_present():
    command = CC_RUNTIME / "build_context" / "plugins" / "nextseek" / "commands" / "nextseek.md"
    assert command.is_file()


@pytest.mark.parametrize("bin_name", [
    "nextseek-entity-extract",
    "nextseek-parse",
    "nextseek-plan",
    "nextseek-query",
    "nextseek-api-read",
    "nextseek-api-write",
    "nextseek-graph",
    "nextseek-report",
    "nextseek-generate-submission",
])
def test_cc_runtime_plugin_bin_scripts_present(bin_name):
    path = CC_RUNTIME / "build_context" / "plugins" / "nextseek" / "bin" / bin_name
    assert path.is_file(), f"missing plugin bin script: {path}"


def test_cc_runtime_nextseek_query_bin_present_and_executable():
    """spec-001 T10: nextseek-query re-enabled with PD-5 live-session semantics."""
    path = CC_RUNTIME / "build_context" / "plugins" / "nextseek" / "bin" / "nextseek-query"
    assert path.is_file(), "nextseek-query bin must exist (re-enabled in T10)"
    import os
    assert os.access(path, os.X_OK)


def test_cc_runtime_plugin_scripts_setup_present():
    setup = CC_RUNTIME / "build_context" / "plugins" / "nextseek" / "scripts" / "setup.sh"
    assert setup.is_file()


# ==========================================================================
# Context / catalog files
# ==========================================================================


@pytest.mark.parametrize("catalog", [
    "capabilities.md",
    "min_api_endpoints.json",
    "min_api_endpoints_enriched.json",
    "min_assays_db.json",
    "min_graph_schema.json",
    "min_sampletypes_db.json",
    "neo4j_schema.json",
    "projects_db.json",
    "read_safe_endpoints.json",
])
def test_cc_runtime_context_catalog_files_present(catalog):
    path = CC_RUNTIME / "build_context" / "plugins" / "nextseek" / "context" / catalog
    assert path.is_file(), f"missing context/catalog file: {path}"
    assert path.stat().st_size > 0


def test_cc_runtime_context_min_json_files_are_valid_json():
    context_dir = CC_RUNTIME / "build_context" / "plugins" / "nextseek" / "context"
    for name in context_dir.glob("min_*.json"):
        json.loads(name.read_text(encoding="utf-8"))  # raises on malformed JSON


def test_cc_runtime_docs_nextseek_present():
    docs_dir = CC_RUNTIME / "docs" / "nextseek"
    assert docs_dir.is_dir()
    assert (docs_dir / "README.md").is_file()
    assert list(docs_dir.glob("*.md"))


# ==========================================================================
# Build inputs the Dockerfile's COPY lines require to exist
# ==========================================================================


@pytest.mark.parametrize("rel_path", [
    "pyproject.toml",
    "uv.lock",
    "tools/__init__.py",
    "tools/e2e/__init__.py",
    "tools/e2e/judge_runner.py",
    "baml_src/generators.baml",
    "baml_src/clients.baml",
    "build_context/docs/nextseek-api",
])
def test_cc_runtime_dockerfile_copy_sources_exist(rel_path):
    path = CC_RUNTIME / rel_path
    assert path.exists(), f"Dockerfile COPY source missing from port: {path}"


def test_cc_runtime_dockerfile_copy_lines_reference_only_existing_sources():
    """Cross-check: every literal COPY <src> path in the ported Dockerfile
    (excluding COPY --from and destination-only tokens) must exist relative
    to docker/cc-runtime/, i.e. the build context this Dockerfile is meant to
    be built with."""
    dockerfile_text = _read(CC_RUNTIME / "Dockerfile")
    copy_srcs = re.findall(r"^COPY\s+(?!--from)([^\n]+?)\s+\S+\s*$", dockerfile_text, re.MULTILINE)
    checked = 0
    for line in copy_srcs:
        for src in line.split():
            # Multi-source COPY lines (e.g. "COPY pyproject.toml uv.lock /tmp/x/")
            # list every source but the last token before dest was already
            # excluded by the regex; iterate all remaining whitespace-split
            # tokens as sources.
            candidate = (CC_RUNTIME / src).resolve()
            assert str(candidate).startswith(str(CC_RUNTIME.resolve())), (
                f"COPY source escapes build context: {src}"
            )
            assert candidate.exists(), f"COPY source does not exist: {src}"
            checked += 1
    assert checked > 0, "sanity: expected to find at least one COPY line"


# ==========================================================================
# Negative test: compose CC image build context must NOT be
# docker/cc-runner/Dockerfile (G7-3) -- docker/cc-runner/ is a deliberately
# separate, non-production lean proof image.
# ==========================================================================


def test_cc_runner_lean_proof_image_still_present_and_untouched():
    """docker/cc-runner/ is explicitly out of scope for this port -- it must
    remain in place as the lean proof image, not be deleted or overwritten."""
    dockerfile = CC_RUNNER / "Dockerfile"
    assert dockerfile.is_file()
    text = _read(dockerfile)
    assert "lean" in text.lower() or "deliberately minimal" in text.lower()


def test_cc_runtime_and_cc_runner_dockerfiles_are_genuinely_distinct():
    """docker/cc-runtime/Dockerfile must not just be a copy of
    docker/cc-runner/Dockerfile under a new path -- it is the fuller,
    production-capable image (node+uv+baked plugin+BAML client), not the
    lean proof image."""
    runtime_text = _read(CC_RUNTIME / "Dockerfile")
    runner_text = _read(CC_RUNNER / "Dockerfile")
    assert runtime_text != runner_text
    # The lean proof image never installs uv / syncs a Python venv / bakes
    # the nextseek plugin's context catalogs; the full runtime image does.
    assert "uv sync" in runtime_text
    assert "uv sync" not in runner_text
    assert "COPY build_context/plugins/nextseek/" in runtime_text
    assert "COPY build_context/plugins/nextseek/" not in runner_text


def test_compose_cc_build_context_is_not_cc_runner_dockerfile():
    """G7-3: whatever CC image build target root compose eventually wires
    (Task 5's job), it must never be docker/cc-runner/Dockerfile -- that
    image is an explicitly non-production lean proof image, not the
    canonical build target.

    docker-compose.yml does not yet declare any CC-image build service (Task
    5 wires that up); this test is written to hold under BOTH states so it
    keeps enforcing the rule once Task 5 lands:
      - today: no service's build context/dockerfile mentions cc-runner at
        all (vacuously satisfies the rule -- there is nothing to violate it
        yet).
      - after Task 5: any build stanza that does reference a `docker/cc-*`
        path must reference `cc-runtime`, never `cc-runner`.
    """
    compose_text = _read(COMPOSE_FILE)
    assert "docker/cc-runner" not in compose_text, (
        "docker-compose.yml must not point any build context/dockerfile at "
        "docker/cc-runner/ (the lean, explicitly non-production proof "
        "image) -- Task 5 must wire the canonical docker/cc-runtime/ "
        "instead."
    )


# ==========================================================================
# Prove the excluded standalone WS/FastAPI server layer did NOT get ported
# or wired in.
# ==========================================================================


def test_cc_runtime_contains_no_standalone_server_layer_files():
    """The old standalone WS/FastAPI bridge (src/ app, sidecar/, top-level
    main.py) is explicitly excluded as runnable infrastructure -- it must not
    exist anywhere under docker/cc-runtime/."""
    forbidden_names = {"main.py"}
    forbidden_dirs = {"src", "sidecar"}
    for path in CC_RUNTIME.rglob("*"):
        rel = path.relative_to(CC_RUNTIME)
        if path.is_dir() and path.name in forbidden_dirs:
            pytest.fail(f"forbidden server-layer directory ported: {rel}")
        if path.is_file() and path.name in forbidden_names:
            pytest.fail(f"forbidden server-layer file ported: {rel}")


def test_cc_runtime_dockerfile_does_not_start_a_ws_or_http_server():
    """The image's ENTRYPOINT/CMD must launch the `claude` CLI via
    entrypoint.sh, never a FastAPI/uvicorn/websocket server process."""
    text = _read(CC_RUNTIME / "Dockerfile")
    lowered = text.lower()
    assert "uvicorn" not in lowered
    assert "fastapi" not in lowered
    assert 'entrypoint ["/usr/local/bin/entrypoint.sh"]' in lowered
    assert '"claude"' in text


def test_cc_runtime_entrypoint_sh_does_not_start_a_ws_or_http_server():
    text = _read(CC_RUNTIME / "container" / "entrypoint.sh")
    lowered = text.lower()
    assert "uvicorn" not in lowered
    assert "fastapi" not in lowered
    # entrypoint.sh's job is env bridging + settings scrub + exec "$@" (or
    # idle sleep) -- never a bound server process.
    assert 'exec "$@"' in text


def test_cc_runtime_pyproject_declares_no_new_server_startup_script():
    """pyproject.toml legitimately still LISTS fastapi/uvicorn/websockets as
    dependencies (installed into the image venv for shared-lib reuse by
    baml-cli/httpx-based tooling), but must not declare any [project.scripts]
    / console_scripts entry point that would start an HTTP/WS server."""
    text = _read(CC_RUNTIME / "pyproject.toml")
    assert "[project.scripts]" not in text


# ==========================================================================
# Boundary: docker/cc-runtime/ must not duplicate the in-tree dmac_assistant/
# Django-side package.
# ==========================================================================


def test_dmac_assistant_in_tree_package_untouched_by_this_port():
    """The Django-side dmac_assistant/ package (router BAML, run_tracker,
    etc.) is a separate, pre-existing in-tree dependency -- this port must
    not delete or shadow it."""
    in_tree_pkg = REPO_ROOT / "dmac_assistant"
    assert in_tree_pkg.is_dir()
    assert (in_tree_pkg / "pyproject.toml").is_file()
    assert (in_tree_pkg / "src" / "dmac_assistant").is_dir()


# ==========================================================================
# PORT-EVIDENCE.json (Step 3: plugin-context generation attempt record)
# ==========================================================================


def test_port_evidence_file_present_and_valid_json():
    evidence_path = CC_RUNTIME / "PORT-EVIDENCE.json"
    assert evidence_path.is_file()
    json.loads(_read(evidence_path))


def test_port_evidence_records_source_path_and_pinned_commit():
    data = json.loads(_read(CC_RUNTIME / "PORT-EVIDENCE.json"))
    assert data["port_source_path"] == "/home/taishajo/work/dmac-assistant"
    assert re.fullmatch(r"[0-9a-f]{40}", data["port_source_commit"])


def test_port_evidence_records_at_least_one_generation_attempt_with_command_and_blocker():
    data = json.loads(_read(CC_RUNTIME / "PORT-EVIDENCE.json"))
    attempts = data["generation_attempts"]
    assert isinstance(attempts, list) and len(attempts) >= 1
    for attempt in attempts:
        assert attempt["attempted_command"]
        assert attempt["result"] in {"BLOCKED", "SUCCEEDED"}
        if attempt["result"] == "BLOCKED":
            assert attempt["blocker_output"]


def test_port_evidence_context_file_inventory_matches_ported_files():
    data = json.loads(_read(CC_RUNTIME / "PORT-EVIDENCE.json"))
    inventory = data["context_file_inventory"]
    assert isinstance(inventory, list) and len(inventory) > 0
    for entry in inventory:
        path = CC_RUNTIME / entry["path"]
        assert path.is_file(), f"PORT-EVIDENCE.json inventory references missing file: {entry['path']}"
        actual = path.read_bytes()
        assert len(actual) == entry["size_bytes"]
        import hashlib
        assert hashlib.sha256(actual).hexdigest() == entry["sha256"]


# ==========================================================================
# Secret-safety: no real secrets committed anywhere under docker/cc-runtime/
# ==========================================================================


_SECRET_VALUE_RE = re.compile(
    r"(AKIA[0-9A-Z]{16})"           # AWS access key id
    r"|(-----BEGIN [A-Z ]*PRIVATE KEY-----)"
    r"|(ABSK-[A-Za-z0-9+/=]{10,})"  # Bedrock bearer-style token shape
)

# Lines that legitimately mention credential *names* (env var names, doc
# placeholders like <password>, or instructions telling the agent never to
# leak credentials) are not secrets -- only a real-looking secret *value*
# trips this scan.
def test_no_secret_looking_values_anywhere_under_cc_runtime():
    hits = []
    for path in CC_RUNTIME.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix in {".json"} and "context" in path.parts:
            # Reference catalogs are large; still scanned, just skip binary-ish surprises.
            pass
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in _SECRET_VALUE_RE.finditer(text):
            hits.append((str(path.relative_to(CC_RUNTIME)), m.group(0)[:12]))
    assert not hits, f"secret-looking values found under docker/cc-runtime/: {hits}"


def test_no_real_dotenv_files_only_examples_or_none():
    """The port must carry only .example templates, never a real .env /
    .env.prod / .env.dev file."""
    forbidden = {".env", ".env.prod", ".env.dev", ".env.local"}
    hits = [
        str(p.relative_to(CC_RUNTIME))
        for p in CC_RUNTIME.rglob("*")
        if p.is_file() and p.name in forbidden
    ]
    assert not hits, f"real env file(s) committed under docker/cc-runtime/: {hits}"
