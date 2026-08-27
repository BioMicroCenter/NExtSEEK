"""Hermetic tests for PLAN-7 Task 4: the ported Bedrock auth-proxy source
tree at ``docker/bedrock-proxy/``.

Purpose: prove that ``docker/bedrock-proxy/`` (the canonical, NExtSEEK-tracked
build target for the OI-3 hardened Bedrock auth-proxy sidecar) carries every
asset the image build needs -- the ``app/`` package (config + relay logic),
the top-level package marker, the Dockerfile, and the committed ``.example``
secret-env template -- byte-identical to the pinned port source, AND that the
real secret file (``proxy-secret.env``) is never present/tracked/copied into
the image build context.

No Docker, no network, no DB. File-presence, hash, and grep assertions only,
against the real repo tree checked out at test time (this is deliberately
NOT mocked: the whole point of this suite is to catch a missing/renamed file,
a logic drift from the pinned source, or a secret-handling regression in the
real ``docker/bedrock-proxy/`` directory).

Actually invoking ``docker build`` against this context (context =
``docker/bedrock-proxy/``, tag ``bedrock-proxy:step7-port-test``, followed by
``docker rmi``) is exercised separately, outside the hermetic pytest suite
(see task-4-report.md for the command + output tail) -- building requires
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
PROXY_DIR = REPO_ROOT / "docker" / "bedrock-proxy"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
GITIGNORE_FILE = REPO_ROOT / ".gitignore"

# The pinned port source commit + the source path the port was taken from.
# PORT_SOURCE_PATH_RECORDED is a plain string used ONLY to cross-check what
# PORT-EVIDENCE.json records -- no test touches that filesystem path (it is
# not mounted in the mandated hermetic harness container).
PORT_SOURCE_PATH_RECORDED = "/home/taishajo/work/dmac-assistant/bedrock-proxy"
PORT_SOURCE_COMMIT = "a429f1372a075e5db586a1b6efc8c3b1663e211a"

REAL_SECRET_FILENAME = "proxy-secret.env"

# sha256 of each verbatim-ported file, pinned as literals so the drift guard
# runs everywhere (including the --network none harness container, where the
# port-source clone is not mounted) with zero skips.
#
# These constants pin the byte-identical port at source commit a429f137: at
# port time the worktree copies were verified byte-identical to the pinned
# dmac-assistant/bedrock-proxy source both by a live `diff` against the clone
# (empty for all five files) and by matching sha256 digests (task-4-report.md
# sections 1-2 record that one-time manual verification); these literals are
# those digests. Any deliberate future change to a ported proxy file must
# consciously update BOTH the constant here AND
# docker/bedrock-proxy/PORT-EVIDENCE.json. That is deliberate: an in-place
# edit of proxy.py/config.py that also regenerates PORT-EVIDENCE.json to
# match (defeating the dest-vs-manifest inventory test) still fails here,
# because these expected digests do not live in the regenerable manifest.
PINNED_SOURCE_SHA256 = {
    "__init__.py": "6e20c439586b2f237ade5334ec7f4d62d57903f527bceea74fc1841a93fba069",
    "app/__init__.py": "e81e6cfec1157608321d2166cda98f3376486f49308c7ea80038fc1fb6bd86fd",
    "app/config.py": "eb464c910f288964f7d80753c5fe9ef7abdf9abc7900abde7f19579cb4c826cb",
    "app/proxy.py": "e890f99a690b6dd3e03b454f95200c3261186f5df90cdf5f001a0b957f3b6859",
    "proxy-secret.env.example": "fdbf6c5cb8184158faf9ea06668d2dcf2aee21a37516103142040cfd02bf1d16",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ==========================================================================
# Directory / file presence
# ==========================================================================


def test_bedrock_proxy_directory_exists():
    assert PROXY_DIR.is_dir(), (
        "docker/bedrock-proxy/ must exist as the canonical, NExtSEEK-owned "
        "Bedrock auth-proxy build source (PLAN-7 Task 4)."
    )


def test_bedrock_proxy_app_package_present():
    assert (PROXY_DIR / "app" / "__init__.py").is_file()
    assert (PROXY_DIR / "app" / "config.py").is_file()
    assert (PROXY_DIR / "app" / "proxy.py").is_file()


def test_bedrock_proxy_top_level_package_marker_present():
    marker = PROXY_DIR / "__init__.py"
    assert marker.is_file()
    text = _read(marker)
    assert "hyphenated" in text.lower() or "not importable" in text.lower()


def test_bedrock_proxy_dockerfile_present_and_non_empty():
    dockerfile = PROXY_DIR / "Dockerfile"
    assert dockerfile.is_file()
    assert len(_read(dockerfile).strip()) > 0


def test_bedrock_proxy_build_ignore_file_present():
    assert (PROXY_DIR / ".dockerignore").is_file()


def test_bedrock_proxy_example_env_present():
    example = PROXY_DIR / "proxy-secret.env.example"
    assert example.is_file()


# ==========================================================================
# Byte-identical port: app/ logic files + top-level marker + .example must
# match the pinned source EXACTLY (no logic rewrite in the port).
# ==========================================================================


@pytest.mark.parametrize("rel_path", sorted(PINNED_SOURCE_SHA256))
def test_ported_file_is_byte_identical_to_pinned_source(rel_path):
    """Unconditional drift guard: each verbatim-ported file must hash to the
    literal sha256 pinned in PINNED_SOURCE_SHA256 (= the pinned source's
    digest at commit a429f137). Runs in every environment, no skips, and
    catches any post-port in-place edit -- including one that regenerates
    PORT-EVIDENCE.json to match, since these expected digests are pinned
    here, not read from that manifest."""
    dest = PROXY_DIR / rel_path
    assert dest.is_file(), f"missing ported file: {dest}"
    assert _sha256(dest) == PINNED_SOURCE_SHA256[rel_path], (
        f"{rel_path} has drifted from the pinned port source "
        f"({PORT_SOURCE_COMMIT}) -- proxy logic must be ported verbatim, "
        "never rewritten. A deliberate change must update both "
        "PINNED_SOURCE_SHA256 and docker/bedrock-proxy/PORT-EVIDENCE.json "
        "consciously."
    )


def test_proxy_py_carries_hardening_markers():
    """Cheap content-level sanity check (in addition to the byte-identical
    hash check above) that the ported proxy.py still carries its documented
    hardening features: exact-match allowlist, body cap, redacting logger,
    healthcheck route, and runtime token injection."""
    text = _read(PROXY_DIR / "app" / "proxy.py")
    assert "_allowed(" in text
    assert "_is_canonical(" in text
    assert "_content_length_over_cap(" in text
    assert "_read_body_capped(" in text
    assert "_log_access(" in text
    assert '"/healthz"' in text
    assert "config.token" in text
    assert 'out_headers["Authorization"]' in text


def test_config_py_carries_runtime_token_injection_from_env():
    text = _read(PROXY_DIR / "app" / "config.py")
    assert "from_env" in text
    assert "AWS_BEARER_TOKEN_BEDROCK" in text
    assert "<redacted>" in text


# ==========================================================================
# Dockerfile: build-context adaptation is comment/path-level only (this port
# builds with context = docker/bedrock-proxy/ itself, not the source's
# repo-root context), never a logic change.
# ==========================================================================


def test_dockerfile_copy_source_matches_this_ports_build_context():
    """The pinned source's Dockerfile COPYs `bedrock-proxy/app/` because its
    compose sets `context: ..` (repo root). This port's hermetic guard builds
    with context = docker/bedrock-proxy/ itself (see success conditions), so
    the COPY source must be the context-relative `app/`, not the source's
    repo-root-relative `bedrock-proxy/app/`."""
    text = _read(PROXY_DIR / "Dockerfile")
    assert re.search(r"^COPY\s+app/\s+/app/app/\s*$", text, re.MULTILINE), (
        "Dockerfile COPY line must reference app/ relative to the "
        "docker/bedrock-proxy/ build context."
    )
    assert "COPY bedrock-proxy/app/" not in text, (
        "Dockerfile must not carry the source's repo-root-relative COPY "
        "path -- it does not resolve when building with context = "
        "docker/bedrock-proxy/."
    )


def test_dockerfile_preserves_hardening_invariants():
    """Everything except the COPY source path must be preserved verbatim:
    non-root user, healthcheck hitting /healthz, uvicorn CMD, pinned deps."""
    text = _read(PROXY_DIR / "Dockerfile")
    assert "useradd -m -u 1001" in text
    assert "USER proxyapp" in text
    assert "HEALTHCHECK" in text
    assert "/healthz" in text
    assert 'CMD ["uvicorn", "app.proxy:app"' in text


def test_dockerfile_does_not_copy_the_real_secret_filename():
    """(a) from the success conditions: a Dockerfile must never COPY the real
    secret filename into the image -- the token is injected only at runtime
    via compose env_file:, never baked into the image (gate G6)."""
    text = _read(PROXY_DIR / "Dockerfile")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("COPY") or stripped.startswith("ADD"):
            assert REAL_SECRET_FILENAME not in stripped, (
                f"Dockerfile COPYs/ADDs the real secret filename: {stripped!r}"
            )


# ==========================================================================
# Real secret file must never be present in this tree, must be gitignored,
# and must be excluded from the Docker build context.
# ==========================================================================


def test_real_secret_file_not_present_in_ported_tree():
    """The real proxy-secret.env is a REAL SECRET FILE in the pinned source
    (mode 600). It must never be copied into this repo."""
    hits = [
        str(p.relative_to(PROXY_DIR))
        for p in PROXY_DIR.rglob("*")
        if p.is_file() and p.name == REAL_SECRET_FILENAME
    ]
    assert not hits, f"real secret file committed under docker/bedrock-proxy/: {hits}"


@pytest.mark.skipif(
    not GITIGNORE_FILE.exists(),
    reason="source-tree check: .gitignore is excluded from the image build "
    "context (.dockerignore), so this runs against the working checkout, not "
    "inside a built image. The in-image secret-absence is covered by "
    "test_real_secret_file_not_present_in_ported_tree.",
)
def test_real_secret_filename_is_gitignored():
    """(b): the real secret filename must be excluded from git tracking via
    an explicit .gitignore entry (root .gitignore, matching the existing
    docker/nextseek.env / docker/db.env convention)."""
    text = _read(GITIGNORE_FILE)
    assert f"docker/bedrock-proxy/{REAL_SECRET_FILENAME}" in text or (
        f"**/{REAL_SECRET_FILENAME}" in text
    ), (
        "root .gitignore must exclude the real "
        f"docker/bedrock-proxy/{REAL_SECRET_FILENAME} from tracking."
    )


def test_real_secret_filename_is_dockerignored():
    """Build-context exclusion: even if the real secret file were ever
    present on disk (gitignored, so it can still exist locally), it must
    never enter the Docker build context."""
    text = _read(PROXY_DIR / ".dockerignore")
    assert REAL_SECRET_FILENAME in text, (
        "docker/bedrock-proxy/.dockerignore must exclude "
        f"{REAL_SECRET_FILENAME} from the build context."
    )


def test_real_secret_filename_is_excluded_from_root_build_context():
    """The nextseek image builds from the REPO ROOT (`COPY . /app/`), so the
    root .dockerignore must also exclude the real secret file. The deploy
    clone legitimately holds it on disk for compose env_file:, and without
    this entry every nextseek image bakes the Bedrock token into /app."""
    text = _read(REPO_ROOT / ".dockerignore")
    assert f"docker/bedrock-proxy/{REAL_SECRET_FILENAME}" in text or (
        f"**/{REAL_SECRET_FILENAME}" in text
    ), (
        "root .dockerignore must exclude docker/bedrock-proxy/"
        f"{REAL_SECRET_FILENAME} from the nextseek image build context."
    )


def test_example_env_contains_only_placeholder_shape_no_real_token():
    """(d): the committed .example must carry key NAMES only -- assert the
    values area for AWS_BEARER_TOKEN_BEDROCK/AWS_REGION is empty/placeholder
    shaped, never a real-looking Bedrock bearer token value."""
    text = _read(PROXY_DIR / "proxy-secret.env.example")
    assert re.search(r"^AWS_BEARER_TOKEN_BEDROCK=\s*$", text, re.MULTILINE), (
        "proxy-secret.env.example must declare AWS_BEARER_TOKEN_BEDROCK with "
        "an empty placeholder value, never a real token."
    )
    assert re.search(r"^AWS_REGION=\s*$", text, re.MULTILINE), (
        "proxy-secret.env.example must declare AWS_REGION with an empty "
        "placeholder value."
    )
    # No real-looking Bedrock bearer token shape anywhere in the template.
    assert not re.search(r"ABSK-[A-Za-z0-9+/=]{10,}", text)


def test_real_secret_file_is_not_git_tracked():
    """(b), enforced against the actual git index rather than just the
    .gitignore text: even if a real secret file existed on disk locally, it
    must never show up as a tracked path."""
    import subprocess

    result = subprocess.run(
        ["git", "ls-files", "docker/bedrock-proxy/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    tracked = result.stdout.splitlines()
    hits = [p for p in tracked if Path(p).name == REAL_SECRET_FILENAME]
    assert not hits, f"real secret file is git-tracked: {hits}"


# ==========================================================================
# Negative test: docker-compose.yml bedrock-proxy service must never expose
# a host `ports:` key. Root-compose wiring is Task 5's job; this guard is
# written NOW so Task 5 is caught if it ever adds `ports:` to the
# bedrock-proxy service. Two-state reasoning (mirrors Task 3's
# test_compose_cc_build_context_is_not_cc_runner_dockerfile):
#   - today: docker-compose.yml declares no bedrock-proxy service at all
#     (Task 5 hasn't wired it up yet) -- vacuously true, nothing to violate.
#   - after Task 5: whatever `bedrock-proxy:` service block root compose
#     gains must not carry a `ports:` key -- the proxy's :8080 is reachable
#     ONLY on the internal Docker network (R-8/gate G5), never bound to a
#     host port.
# ==========================================================================


def test_compose_bedrock_proxy_service_has_no_host_ports_key():
    compose_text = _read(COMPOSE_FILE)
    try:
        import yaml
    except ImportError:
        pytest.skip("PyYAML not available in this hermetic environment")
    data = yaml.safe_load(compose_text) or {}
    services = data.get("services") or {}
    if "bedrock-proxy" not in services:
        # Today: Task 5 has not wired root compose yet. Vacuously true.
        return
    proxy_service = services["bedrock-proxy"] or {}
    assert "ports" not in proxy_service, (
        "docker-compose.yml's bedrock-proxy service must not publish a host "
        "ports: key -- :8080 must be reachable only on the internal Docker "
        "network (R-8/gate G5). Task 5 introduced a regression."
    )


# ==========================================================================
# Standalone sidecar docker-compose.yml from the port source is reference
# only -- it must NOT be ported verbatim (Task 5 owns root-compose wiring).
# ==========================================================================


def test_standalone_sidecar_compose_not_ported():
    """The pinned source's bedrock-proxy/docker-compose.yml is a standalone
    sidecar compose (its own `name:`/network join) -- reference only. Root
    compose wiring is Task 5's job, so this port must not carry a
    docker-compose.yml of its own under docker/bedrock-proxy/."""
    assert not (PROXY_DIR / "docker-compose.yml").is_file(), (
        "docker/bedrock-proxy/ must not carry a standalone docker-compose.yml "
        "-- root-compose wiring is Task 5's job (PLAN-7)."
    )


# ==========================================================================
# PORT-EVIDENCE.json traceability (mirrors docker/cc-runtime/PORT-EVIDENCE.json)
# ==========================================================================


def test_port_evidence_file_present_and_valid_json():
    evidence_path = PROXY_DIR / "PORT-EVIDENCE.json"
    assert evidence_path.is_file()
    json.loads(_read(evidence_path))


def test_port_evidence_records_source_path_and_pinned_commit():
    data = json.loads(_read(PROXY_DIR / "PORT-EVIDENCE.json"))
    assert data["port_source_path"] == PORT_SOURCE_PATH_RECORDED
    assert data["port_source_commit"] == PORT_SOURCE_COMMIT
    assert re.fullmatch(r"[0-9a-f]{40}", data["port_source_commit"])
    assert data["port_source_verified_clean_tree"] is True


def test_port_evidence_file_inventory_matches_ported_files():
    data = json.loads(_read(PROXY_DIR / "PORT-EVIDENCE.json"))
    inventory = data["file_inventory"]
    assert isinstance(inventory, list) and len(inventory) > 0
    for entry in inventory:
        path = PROXY_DIR / entry["path"]
        assert path.is_file(), f"PORT-EVIDENCE.json inventory references missing file: {entry['path']}"
        actual = path.read_bytes()
        assert len(actual) == entry["size_bytes"]
        assert hashlib.sha256(actual).hexdigest() == entry["sha256"]


# ==========================================================================
# Secret-safety: no real secrets committed anywhere under docker/bedrock-proxy/
# ==========================================================================


_SECRET_VALUE_RE = re.compile(
    r"(AKIA[0-9A-Z]{16})"           # AWS access key id
    r"|(-----BEGIN [A-Z ]*PRIVATE KEY-----)"
    r"|(ABSK-[A-Za-z0-9+/=]{10,})"  # Bedrock bearer-style token shape
)


def test_no_secret_looking_values_anywhere_under_bedrock_proxy():
    hits = []
    for path in PROXY_DIR.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in _SECRET_VALUE_RE.finditer(text):
            hits.append((str(path.relative_to(PROXY_DIR)), m.group(0)[:12]))
    assert not hits, f"secret-looking values found under docker/bedrock-proxy/: {hits}"
