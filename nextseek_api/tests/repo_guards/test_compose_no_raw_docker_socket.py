"""Only cc-spawner holds the Docker socket, and no app module imports the Docker SDK (W9 part 2).

The Docker socket is root on the host. cc-spawner is the one hardened service that mounts it; `web` and the
worker reach Docker only through `NessieAI/cc/spawner_client.py`. These guards keep it that way: outside cc-spawner no
mount may name `docker.sock` or bind `/run`, `/var/run` or a folder above them (a `${VAR:-default}` source is read as
its default), and no service sets `DOCKER_HOST`.
"""
from __future__ import annotations

import ast
import copy
import os
import posixpath
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())["services"]
SOCK = "/var/run/docker.sock"
# Every module in the repo is scanned but these folders. cc-spawner keeps the SDK; startup/ and scripts/ run on the
# host, as do the hidden folders (.claude/skills); history and archive are frozen. Test folders and files may fake or
# drive the SDK. Any folder of these names is skipped wherever it sits.
EXEMPT_PATHS = ("docker/cc-spawner", "startup", "scripts", "NessieAI/history", "docs/archive")
EXEMPT_DIR_NAMES = frozenset({"tests", "OLD", "node_modules", "__pycache__"})


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


def _docker_importers(root: Path) -> list[str]:
    found = []
    for here, dirs, files in os.walk(root):
        rel_dir = Path(here).relative_to(root).as_posix()
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in EXEMPT_DIR_NAMES
                         and posixpath.normpath(f"{rel_dir}/{d}") not in EXEMPT_PATHS)
        for name in sorted(files):
            if not name.endswith(".py") or name.startswith("test_") or name == "conftest.py":
                continue
            rel = posixpath.normpath(f"{rel_dir}/{name}")
            if _imports_docker(ast.parse((Path(here) / name).read_bytes(), filename=rel)):
                found.append(rel)
    return found


def test_no_app_module_imports_the_docker_sdk():
    assert _docker_importers(REPO_ROOT) == []


@pytest.mark.parametrize("rel,caught", [
    ("NessieAI/router/x.py", True),
    ("ci/smoke/x.py", True),
    ("manage_tool.py", True),
    ("docker/cc-spawner/x.py", False),
    ("NessieAI/tests/cc/x.py", False),
    ("startup/lib/x.py", False),
    ("scripts/x.py", False),
    ("NessieAI/history/cc/x.py", False),
])
def test_a_planted_docker_import_is_caught_outside_the_exempt_folders(tmp_path, rel, caught):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def f():\n    import docker\n")
    assert (_docker_importers(tmp_path) == [rel]) is caught
