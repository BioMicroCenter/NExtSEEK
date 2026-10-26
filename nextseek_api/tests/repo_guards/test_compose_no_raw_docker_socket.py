"""Only cc-spawner holds the Docker socket, and no app module imports the Docker SDK (W9 part 2).

The Docker socket is root on the host. cc-spawner is the one hardened service that mounts it; `web` and the
worker reach Docker only through `NessieAI/cc/spawner_client.py`. These two guards keep it that way.
"""
from __future__ import annotations

import ast
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())["services"]
SOCK = "/var/run/docker.sock"
# The app code roots. docker/cc-spawner/ is outside them on purpose: it keeps the SDK.
APP_ROOTS = ("nextseek_api", "NessieAI/cc", "NessieAI/chat_nextseek/src", "seek", "dmac")
# Test files may fake or drive the SDK; app modules may not import it. No exception is needed today.
ALLOWED: set[str] = set()


def _mounts(service: dict) -> list[str]:
    return [v if isinstance(v, str) else f"{v.get('source')}:{v.get('target')}" for v in service.get("volumes", [])]


def test_only_cc_spawner_mounts_the_docker_socket():
    holders = {n for n, s in SERVICES.items() if any(m.split(":")[0] == SOCK for m in _mounts(s))}
    assert holders == {"cc-spawner"}, holders


def test_no_service_joins_the_docker_group_or_runs_privileged():
    for name, svc in SERVICES.items():
        assert "group_add" not in svc, f"{name} adds a group (the docker group is the socket)"
        assert not svc.get("privileged"), name


def _imports_docker(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "docker" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").split(".")[0] == "docker":
            return True
    return False


def test_no_app_module_imports_the_docker_sdk():
    found = []
    for root in APP_ROOTS:
        for path in (REPO_ROOT / root).rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if "/tests/" in rel or rel.rsplit("/", 1)[-1].startswith("test_") or rel in ALLOWED:
                continue
            if _imports_docker(ast.parse(path.read_text(), filename=rel)):
                found.append(rel)
    assert not found, found
