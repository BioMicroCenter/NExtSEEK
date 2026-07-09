"""Hermetic tests for Task 12 (G7-11): the ported NS sidecar source tree at
``docker/ns-sidecar/``.

Purpose: prove that ``docker/ns-sidecar/`` (the canonical, NExtSEEK-tracked
build target for the WS op-proxy sidecar that backs 7 of the 9 CC agent
plugin ops) carries every asset the image build needs -- the ``app/``
package (config + protocol + the 7 ops + HTTP client + staging + write gate
+ healthcheck), the top-level package marker, and the Dockerfile --
byte-identical to the pinned port source, AND that:

  - the upstream standalone ``sidecar/docker-compose.yml`` fragment is never
    ported/wired here (later tasks 13-15 own compose wiring, per the task
    brief),
  - no upstream local-dev env overlay files (``local-nextseek*.env``) exist
    anywhere under the ported tree (OI-3: the sidecar holds no credentials;
    its only env is ``NEXTSEEK_BASE_URL``, ``SIDECAR_STAGING_DIR``,
    ``SIDECAR_WS_PORT``),
  - the T16/T17 lean contract holds: no live ``SESSION_DB_*`` env access, no
    ``chat_nextseek`` reference, no ``torch`` import anywhere in the ported
    app package.

No Docker, no network, no DB. File-presence, hash, and grep assertions only,
against the real repo tree checked out at test time (this is deliberately
NOT mocked: the whole point of this suite is to catch a missing/renamed
file, a logic drift from the pinned source, or a secret-handling regression
in the real ``docker/ns-sidecar/`` directory).

Actually invoking ``docker build`` against this context (context =
``docker/ns-sidecar/``, throwaway tag, followed by ``docker rmi``) is
exercised separately, outside the hermetic pytest suite (see
task-12-report.md for the command + output tail) -- building requires
network for the base image and apt/uv dependencies, which the hermetic
harness runs with ``--network none``.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.host_only

REPO_ROOT = Path(__file__).resolve().parents[3]
SIDECAR_DIR = REPO_ROOT / "docker" / "ns-sidecar"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

# The pinned port source commit + the source path the port was taken from.
# PORT_SOURCE_PATH_RECORDED is a plain string used ONLY to cross-check what
# PORT-EVIDENCE.json records -- no test touches that filesystem path (it is
# not mounted in the mandated hermetic harness container).
PORT_SOURCE_PATH_RECORDED = "/home/taishajo/work/dmac-assistant/sidecar"
PORT_SOURCE_COMMIT = "a429f1372a075e5db586a1b6efc8c3b1663e211a"

APP_MODULES = (
    "config.py",
    "contract.py",
    "exceptions.py",
    "granular_models.py",
    "healthcheck.py",
    "ns_client.py",
    "ops.py",
    "server.py",
    "staging.py",
    "write_gate.py",
)

# sha256 of each verbatim-ported file, pinned as literals so the drift guard
# runs everywhere (including the --network none harness container, where the
# port-source clone is not mounted) with zero skips.
#
# These constants pin the byte-identical port at source commit a429f137: at
# port time the worktree copies were verified byte-identical to the pinned
# dmac-assistant/sidecar source both by a live `diff` against the clone
# (empty for all twelve files) and by matching sha256 digests (task-12-report.md
# records that one-time manual verification); these literals are those
# digests. Any deliberate future change to a ported sidecar file must
# consciously update BOTH the constant here AND
# docker/ns-sidecar/PORT-EVIDENCE.json. That is deliberate: an in-place edit
# of e.g. ops.py/server.py that also regenerates PORT-EVIDENCE.json to match
# (defeating the dest-vs-manifest inventory test) still fails here, because
# these expected digests do not live in the regenerable manifest.
PINNED_SOURCE_SHA256 = {
    "__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "app/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "app/config.py": "2e79ac3f162df3fb1782be583db775da2faa3890378ecd582922603766ffc9df",
    "app/contract.py": "f40e5f49aead01a7e55cb0eb68165dd1f09a5773455868ca518ccfbb8769f6b4",
    "app/exceptions.py": "172d216e9c7373a491007c2eb4b39992b863c78f383f8a4cbd07a76a3ec8b0fb",
    "app/granular_models.py": "231aff9f576efb6e620f62019ce031e34fe59558a03f5b5247f55e4e5663a347",
    "app/healthcheck.py": "bb0bb5db69201a895ea6c55bad3f74142971a550c167e9e0b8b99c2b1dfb8ba3",
    "app/ns_client.py": "e565dea4c67bccb1fcccc14356b0c1988b1cd0e4f9f4606e7c06a519b0f59ae5",
    "app/ops.py": "e1ed9947f06d92b98b9c324643b839274601b40d2c9ba80578677a4541c07dfb",
    "app/server.py": "dd2a9266dba4fb61287c1b18eb1fb639408abedb6dbd3a2fddf69a5113f3c17b",
    "app/staging.py": "a34b2c40c8a3b8f907960d1b4f2ad6ac38c60703d1c64d0a7c37860527814ac9",
    "app/write_gate.py": "86ec8f617cd287d89b35856307bd7a04fdd0bdafc4edbc789391eb69ce4b788e",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ==========================================================================
# Directory / file presence
# ==========================================================================


def test_ns_sidecar_directory_exists():
    assert SIDECAR_DIR.is_dir(), (
        "docker/ns-sidecar/ must exist as the canonical, NExtSEEK-owned "
        "NS sidecar build source (Task 12, G7-11)."
    )


def test_ns_sidecar_app_package_present():
    assert (SIDECAR_DIR / "app" / "__init__.py").is_file()
    for module in APP_MODULES:
        assert (SIDECAR_DIR / "app" / module).is_file(), f"missing app/{module}"


def test_ns_sidecar_top_level_package_marker_present():
    """iter-1 L-5: the upstream Dockerfile COPYs BOTH sidecar/__init__.py (the
    parent package marker) and sidecar/app/ -- `python -m sidecar.app.server`
    with PYTHONPATH=/app requires the full `sidecar` package shape. Both
    __init__.py files must exist in the port, test-pinned."""
    marker = SIDECAR_DIR / "__init__.py"
    assert marker.is_file()


def test_ns_sidecar_dockerfile_present_and_non_empty():
    dockerfile = SIDECAR_DIR / "Dockerfile"
    assert dockerfile.is_file()
    assert len(_read(dockerfile).strip()) > 0


def test_ns_sidecar_build_ignore_file_present():
    assert (SIDECAR_DIR / ".dockerignore").is_file()


# ==========================================================================
# Byte-identical port: app/ logic files + both package markers must match
# the pinned source EXACTLY (no logic rewrite in the port).
# ==========================================================================


@pytest.mark.parametrize("rel_path", sorted(PINNED_SOURCE_SHA256))
def test_ported_file_is_byte_identical_to_pinned_source(rel_path):
    """Unconditional drift guard: each verbatim-ported file must hash to the
    literal sha256 pinned in PINNED_SOURCE_SHA256 (= the pinned source's
    digest at commit a429f137). Runs in every environment, no skips, and
    catches any post-port in-place edit -- including one that regenerates
    PORT-EVIDENCE.json to match, since these expected digests are pinned
    here, not read from that manifest."""
    dest = SIDECAR_DIR / rel_path
    assert dest.is_file(), f"missing ported file: {dest}"
    assert _sha256(dest) == PINNED_SOURCE_SHA256[rel_path], (
        f"{rel_path} has drifted from the pinned port source "
        f"({PORT_SOURCE_COMMIT}) -- sidecar logic must be ported verbatim, "
        "never rewritten. A deliberate change must update both "
        "PINNED_SOURCE_SHA256 and docker/ns-sidecar/PORT-EVIDENCE.json "
        "consciously."
    )


# ==========================================================================
# Content-level sanity checks (in addition to the byte-identical hash check
# above) that the ported modules still carry their documented contracts.
# ==========================================================================


def test_contract_py_carries_the_seven_ops_and_error_taxonomy():
    text = _read(SIDECAR_DIR / "app" / "contract.py")
    assert 'SIDECAR_OPS = frozenset(' in text
    for op in ("entity", "parse", "api-read", "api-write", "graph", "report",
               "generate-submission"):
        assert f'"{op}"' in text
    for code in ("CONFIG_MISSING", "VALIDATION", "AGENT_FAILED",
                  "WRITE_BLOCKED", "CONFIG_ERROR", "TRANSPORT_ERROR",
                  "AUTH_FAILED", "STAGING_ERROR"):
        assert code in text


def test_config_py_required_env_is_exactly_the_t16_pair():
    """OI-3 / T16 simplification: the sidecar's ONLY required env is
    NEXTSEEK_BASE_URL + SIDECAR_STAGING_DIR (SIDECAR_WS_PORT is optional,
    defaulted). No credentials, no SESSION_DB_* family."""
    text = _read(SIDECAR_DIR / "app" / "config.py")
    match = re.search(r"_REQUIRED\s*=\s*\(([^)]*)\)", text, re.DOTALL)
    assert match, "config.py must define a _REQUIRED tuple"
    required_block = match.group(1)
    assert "NEXTSEEK_BASE_URL" in required_block
    assert "SIDECAR_STAGING_DIR" in required_block
    assert "SESSION_DB_" not in required_block
    assert "SIDECAR_WS_PORT" not in required_block  # optional, not required


def test_healthcheck_py_probes_exactly_the_documented_endpoint():
    """Verify the brief's healthcheck claim against the ported source: a
    single HTTP GET to {NEXTSEEK_BASE_URL}/nextseek_api/assistant/me/,
    treating 200 or 401 as healthy, with no WS probe and no direct DB/Neo4j
    access."""
    text = _read(SIDECAR_DIR / "app" / "healthcheck.py")
    assert '/nextseek_api/assistant/me/' in text
    assert "httpx.get(url, timeout=5.0)" in text
    assert "resp.status_code in (200, 401)" in text
    # Read-only by construction: exactly one httpx call site (a GET), no POST.
    assert text.count("httpx.get(") == 1
    assert "httpx.post(" not in text


def test_write_gate_py_enforces_strict_confirmed_write_only():
    text = _read(SIDECAR_DIR / "app" / "write_gate.py")
    assert "confirmed_write is not True" in text  # strict identity, not truthy
    assert "WriteBlockedError" in text


def test_server_py_redacts_credentials_in_repr():
    """Credential flow: the per-request NsHttpConfig must never leak
    api_user/api_pass through accidental logging of the config object."""
    text = _read(SIDECAR_DIR / "app" / "server.py")
    assert "<redacted>" in text
    assert "auth=('<redacted>', '<redacted>')" in text


def test_staging_py_hashes_api_user_never_stores_raw_username():
    text = _read(SIDECAR_DIR / "app" / "staging.py")
    assert "hashlib.sha256(api_user.encode" in text


def test_ops_py_commits_bytes_exactly_once_outside_the_artifact_loop():
    """DD-A5-5/F-T16-2-B: report/generate-submission must call commit_bytes()
    exactly once, after the per-artifact loop, never inside it."""
    text = _read(SIDECAR_DIR / "app" / "ops.py")
    for fn_name in ("_report", "_generate_submission"):
        start = text.index(f"def {fn_name}(")
        end = text.index("\n\n\n", start)
        body = text[start:end]
        assert body.count("commit_bytes()") == 1, (
            f"{fn_name} must call commit_bytes() exactly once"
        )
        # Not inside the per-artifact loop: the call must appear after the
        # loop's closing (the `for art in download["artifacts"]:` block ends
        # before the commit call, i.e. commit is not indented inside it).
        commit_line = next(l for l in body.splitlines() if "commit_bytes()" in l)
        assert commit_line.startswith("        commit_bytes()"), (
            f"{fn_name}: commit_bytes() must be at the function's own "
            "indent level (outside the for-loop), not nested inside it"
        )


# ==========================================================================
# Dockerfile: build-context adaptation is COPY-source-path-level only (this
# port builds with context = docker/ns-sidecar/ itself, not the source's
# repo-root context), never a logic change. The COPY *destination* paths
# (which create the `sidecar` package name inside the image) must stay
# byte-identical to upstream -- that is the import-layout invariant
# (iter-1 L-5, `python -m sidecar.app.server` + PYTHONPATH=/app).
# ==========================================================================


def test_dockerfile_copy_source_matches_this_ports_build_context():
    text = _read(SIDECAR_DIR / "Dockerfile")
    assert re.search(r"^COPY\s+__init__\.py\s+/app/sidecar/__init__\.py\s*$", text, re.MULTILINE), (
        "Dockerfile must COPY the context-relative __init__.py (not "
        "sidecar/__init__.py) into /app/sidecar/__init__.py."
    )
    assert re.search(r"^COPY\s+app/\s+/app/sidecar/app/\s*$", text, re.MULTILINE), (
        "Dockerfile must COPY the context-relative app/ (not sidecar/app/) "
        "into /app/sidecar/app/."
    )
    assert "COPY sidecar/__init__.py" not in text, (
        "Dockerfile must not carry the source's repo-root-relative COPY "
        "path -- it does not resolve when building with context = "
        "docker/ns-sidecar/."
    )
    assert "COPY sidecar/app/" not in text, (
        "Dockerfile must not carry the source's repo-root-relative COPY "
        "path -- it does not resolve when building with context = "
        "docker/ns-sidecar/."
    )


def test_dockerfile_preserves_import_layout_and_run_invariants():
    """Everything except the COPY source paths must be preserved verbatim:
    non-root user (uid 1001), WORKDIR/PYTHONPATH=/app, and the
    `python -m sidecar.app.server` CMD that requires the package shape."""
    text = _read(SIDECAR_DIR / "Dockerfile")
    assert "useradd -m -u 1001" in text
    assert "USER sidecar" in text
    assert "WORKDIR /app" in text
    assert "ENV PYTHONPATH=/app" in text
    assert 'CMD ["python", "-m", "sidecar.app.server"]' in text


def test_dockerfile_pins_the_documented_dependency_floors():
    text = _read(SIDECAR_DIR / "Dockerfile")
    assert "httpx>=0.28.1" in text
    assert "websockets~=16.0" in text
    assert "pydantic>=2.9" in text


def test_dockerfile_does_not_copy_any_env_file():
    """Build-context/secret guard: no Dockerfile line may COPY/ADD an env
    file into the image -- OI-3 credentials arrive only via per-request
    Basic auth in the WS envelope, never baked into the image or injected
    via a compose env_file at build time."""
    text = _read(SIDECAR_DIR / "Dockerfile")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("COPY") or stripped.startswith("ADD"):
            assert ".env" not in stripped, f"Dockerfile COPYs/ADDs an env file: {stripped!r}"


# ==========================================================================
# T16/T17 lean contract + OI-3: grep guards against reintroducing retired
# session-runtime plumbing, the removed chat_nextseek dependency, or any
# torch import (this sidecar is a thin HTTP forwarder, not an LLM runtime).
# ==========================================================================


def _all_ported_py_files():
    yield SIDECAR_DIR / "__init__.py"
    yield from sorted((SIDECAR_DIR / "app").glob("*.py"))


def test_no_torch_import_anywhere_in_ported_app():
    for path in _all_ported_py_files():
        text = _read(path)
        assert "import torch" not in text, f"{path.name}: torch import found"
        assert "from torch" not in text, f"{path.name}: torch import found"


def test_no_chat_nextseek_reference_anywhere_in_ported_app():
    for path in _all_ported_py_files():
        text = _read(path)
        assert "chat_nextseek" not in text, f"{path.name}: chat_nextseek reference found"


def test_no_live_session_db_env_access_anywhere_in_ported_app():
    """T16 retired the SESSION_DB_* env family (sessions removed). A
    docstring/comment *mentioning* the retired name to explain what is no
    longer required is fine and expected (config.py's module docstring does
    this); what must never reappear is a LIVE env-var access of that
    family (os.environ[...]/os.environ.get(...) referencing SESSION_DB_)."""
    pattern = re.compile(r"os\.environ(\.get)?\s*[\[\(]\s*[\"']SESSION_DB_")
    for path in _all_ported_py_files():
        text = _read(path)
        assert not pattern.search(text), f"{path.name}: live SESSION_DB_ env access found"


def test_dockerfile_has_no_torch_or_chat_nextseek_or_session_db():
    text = _read(SIDECAR_DIR / "Dockerfile")
    assert "torch" not in text
    assert "chat_nextseek" not in text
    assert "SESSION_DB_" not in text


# ==========================================================================
# The upstream standalone sidecar/docker-compose.yml fragment must NOT be
# ported/wired by this task -- later tasks (13-15) own compose wiring.
# ==========================================================================


def test_standalone_sidecar_compose_not_ported():
    assert not (SIDECAR_DIR / "docker-compose.yml").is_file(), (
        "docker/ns-sidecar/ must not carry a standalone docker-compose.yml "
        "-- the upstream fragment is reference only and root-compose wiring "
        "is a later task's job (13-15)."
    )


def test_no_local_nextseek_env_overlay_files_anywhere_under_ported_tree():
    """OI-3: the sidecar holds no credentials; its only env is
    NEXTSEEK_BASE_URL/SIDECAR_STAGING_DIR/SIDECAR_WS_PORT. The upstream
    local-dev overlay files (local-nextseek-db.env, local-nextseek.env,
    local-nextseek-dmac.env) must never be ported."""
    hits = [
        str(p.relative_to(SIDECAR_DIR))
        for p in SIDECAR_DIR.rglob("local-nextseek*.env")
    ]
    assert not hits, f"upstream local-nextseek*.env overlay file(s) found: {hits}"


def test_no_env_files_of_any_kind_committed_under_ported_tree():
    hits = [
        str(p.relative_to(SIDECAR_DIR))
        for p in SIDECAR_DIR.rglob("*")
        if p.is_file() and p.suffix == ".env"
    ]
    assert not hits, f"env file(s) found under docker/ns-sidecar/: {hits}"


# ==========================================================================
# Negative test: root docker-compose.yml must not yet expose a host
# `ports:` key for a nextseek-sidecar service (mirrors Task 4's forward
# guard). Root-compose wiring is a later task's job; this guard is written
# NOW so that task is caught if it ever adds `ports:` to the service.
# Two-state reasoning:
#   - today: docker-compose.yml declares no nextseek-sidecar service at all
#     (Task 12 does not wire compose) -- vacuously true, nothing to violate.
#   - after wiring: whatever `nextseek-sidecar:` service block root compose
#     gains must not carry a `ports:` key -- the WS port is reachable ONLY
#     on the internal dmac-cc-net Docker network, never bound to a host port.
# ==========================================================================


def test_compose_ns_sidecar_service_has_no_host_ports_key():
    compose_text = _read(COMPOSE_FILE)
    try:
        import yaml
    except ImportError:
        pytest.skip("PyYAML not available in this hermetic environment")
    data = yaml.safe_load(compose_text) or {}
    services = data.get("services") or {}
    if "nextseek-sidecar" not in services:
        # Today: no later task has wired root compose yet. Vacuously true.
        return
    sidecar_service = services["nextseek-sidecar"] or {}
    assert "ports" not in sidecar_service, (
        "docker-compose.yml's nextseek-sidecar service must not publish a "
        "host ports: key -- the WS port must be reachable only on the "
        "internal dmac-cc-net Docker network. A later task introduced a "
        "regression."
    )


# ==========================================================================
# PORT-EVIDENCE.json traceability (mirrors docker/bedrock-proxy/PORT-EVIDENCE.json)
# ==========================================================================


def test_port_evidence_file_present_and_valid_json():
    evidence_path = SIDECAR_DIR / "PORT-EVIDENCE.json"
    assert evidence_path.is_file()
    json.loads(_read(evidence_path))


def test_port_evidence_records_source_path_and_pinned_commit():
    data = json.loads(_read(SIDECAR_DIR / "PORT-EVIDENCE.json"))
    assert data["port_source_path"] == PORT_SOURCE_PATH_RECORDED
    assert data["port_source_commit"] == PORT_SOURCE_COMMIT
    assert re.fullmatch(r"[0-9a-f]{40}", data["port_source_commit"])
    assert data["port_source_verified_clean_tree"] is True


def test_port_evidence_file_inventory_matches_ported_files():
    data = json.loads(_read(SIDECAR_DIR / "PORT-EVIDENCE.json"))
    inventory = data["file_inventory"]
    assert isinstance(inventory, list) and len(inventory) > 0
    for entry in inventory:
        path = SIDECAR_DIR / entry["path"]
        assert path.is_file(), f"PORT-EVIDENCE.json inventory references missing file: {entry['path']}"
        actual = path.read_bytes()
        assert len(actual) == entry["size_bytes"]
        assert hashlib.sha256(actual).hexdigest() == entry["sha256"]


def test_port_evidence_not_ported_records_the_compose_fragment():
    data = json.loads(_read(SIDECAR_DIR / "PORT-EVIDENCE.json"))
    not_ported_paths = [entry["path"] for entry in data.get("not_ported", [])]
    assert "docker-compose.yml" in not_ported_paths


# ==========================================================================
# Secret-safety: no real secrets committed anywhere under docker/ns-sidecar/
# ==========================================================================


_SECRET_VALUE_RE = re.compile(
    r"(AKIA[0-9A-Z]{16})"           # AWS access key id
    r"|(-----BEGIN [A-Z ]*PRIVATE KEY-----)"
    r"|(ABSK-[A-Za-z0-9+/=]{10,})"  # Bedrock bearer-style token shape
)


def test_no_secret_looking_values_anywhere_under_ns_sidecar():
    hits = []
    for path in SIDECAR_DIR.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in _SECRET_VALUE_RE.finditer(text):
            hits.append((str(path.relative_to(SIDECAR_DIR)), m.group(0)[:12]))
    assert not hits, f"secret-looking values found under docker/ns-sidecar/: {hits}"
