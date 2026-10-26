"""Only cc-spawner holds the Docker socket, and no app module imports the Docker SDK (W9 part 2).

The Docker socket is root on the host. cc-spawner is the one hardened service that mounts it; `web` and the
worker reach Docker only through `NessieAI/cc/spawner_client.py`. These guards keep it that way: outside cc-spawner no
mount may name `docker.sock` or bind `/run`, `/var/run` or a folder above them (a `${VAR:-default}` source is read as
its default), and no service sets `DOCKER_HOST`.
"""
from __future__ import annotations

import ast
import copy
import posixpath
import re
from pathlib import Path

import pytest
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


# The socket's folders, and every folder above them: mounting any of these hands over the socket.
_SOCKET_DIRS = frozenset({"/", "/var", "/var/run", "/run"})
_VAR = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?::?-([^}]*))?\}")


def _source(mount: str) -> str:
    """A mount's host side, with ``${VAR:-default}`` read as its default."""
    resolved = _VAR.sub(lambda m: m.group(1) or "", mount)
    return posixpath.normpath(resolved.split(":")[0]) if resolved.startswith("/") else resolved.split(":")[0]


def _env_keys(service: dict) -> set[str]:
    env = service.get("environment") or {}
    return set(env) if isinstance(env, dict) else {e.split("=", 1)[0] for e in env}


def _violations(services: dict) -> list[tuple[str, str]]:
    """(service, what) for every way a service reaches Docker other than cc-spawner's own socket mount."""
    found = []
    for name, svc in services.items():
        if "DOCKER_HOST" in _env_keys(svc):
            found.append((name, "DOCKER_HOST"))
        if name == "cc-spawner":
            continue
        found += [(name, m) for m in _mounts(svc) if "docker.sock" in m or _source(m) in _SOCKET_DIRS]
    return found


def test_only_cc_spawner_mounts_the_docker_socket():
    assert _violations(SERVICES) == []
    assert any(m.split(":")[0] == SOCK for m in _mounts(SERVICES["cc-spawner"]))


@pytest.mark.parametrize("plant", [
    {"volumes": ["/run/docker.sock:/var/run/docker.sock"]},
    {"volumes": ["/var/run:/host-run"]},
    {"volumes": ["${DOCKER_SOCK:-/var/run/docker.sock}:/var/run/docker.sock"]},
    {"volumes": [{"type": "bind", "source": "/run", "target": "/host-run"}]},
    {"volumes": ["/:/host:ro"]},
    {"environment": {"DOCKER_HOST": "tcp://docker-proxy:2375"}},
    {"environment": ["DOCKER_HOST=unix:///host-run/docker.sock"]},
])
def test_a_planted_road_to_docker_in_web_is_caught(plant):
    services = copy.deepcopy(SERVICES)
    for key, value in plant.items():
        if key == "volumes":
            services["web"].setdefault("volumes", []).extend(value)
        else:
            services["web"]["environment"] = value
    assert "web" in {n for n, _ in _violations(services)}


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
