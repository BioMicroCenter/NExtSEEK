"""Pulled images carry an explicit tag, uv is copied (not piped to a shell), the neo4j allowlist is not '*'."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"


def _services():
    return yaml.safe_load(COMPOSE.read_text())["services"]


def test_every_pulled_image_has_an_explicit_tag():
    bad = []
    for name, svc in _services().items():
        if "build" in svc:  # built locally
            continue
        image = svc["image"]
        # strip a ${VAR:-default} wrapper to the default
        m = re.fullmatch(r".*:\$\{[A-Z0-9_]+:-([^}]+)\}", image)
        tag = m.group(1) if m else (image.rsplit(":", 1)[1] if ":" in image.rsplit("/", 1)[-1] else "")
        if not tag or tag == "latest":
            bad.append(f"{name}: {image}")
    assert not bad, f"pulled images without a pinned tag: {bad}"


def test_no_dockerfile_pipes_a_download_into_a_shell():
    bad = []
    for path in REPO_ROOT.rglob("Dockerfile*"):
        rel = path.relative_to(REPO_ROOT)
        if not path.is_file() or {"node_modules", ".git", "history"} & set(rel.parts):
            continue
        if re.search(r"\|\s*(sh|bash)\b", path.read_text()):
            bad.append(str(rel))
    assert not bad, f"Dockerfiles that pipe a download into a shell: {bad}"


def test_neo4j_allowlist_default_is_not_everything():
    env = _services()["neo4j"]["environment"]
    value = env["NEO4J_dbms_security_procedures_allowlist"]
    default = re.search(r":-([^}]*)\}", value).group(1)
    assert default.strip() != "*"
