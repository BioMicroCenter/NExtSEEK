"""The app image runs as uid/gid 1001 (W9 part 2, phase 4).

`nextseek` (worker) and `web` run as 1001 with every capability dropped. That only works if
the image is built for it: the venv's python must not sit under /root (0700), uv and ssh
need a passwd entry with a home, and every path a service writes must be created 1001-owned
so a fresh named volume copies the owner up. Static: parses Dockerfile and docker-compose.yml.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = (REPO_ROOT / "Dockerfile").read_text()
# Continuation lines joined, so one instruction is one string.
INSTRUCTIONS = [i.strip() for i in re.sub(r"\\\n", " ", DOCKERFILE).splitlines() if i.strip() and not i.strip().startswith("#")]
SERVICES = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())["services"]
CONTEXT_DIR = "/app/NessieAI/chat_nextseek/src/chat_nextseek/context"


def _index(pattern: str) -> int:
    return next(i for i, line in enumerate(INSTRUCTIONS) if re.search(pattern, line))


def _writable_named_volume_targets() -> set[str]:
    targets = set()
    for name in ("nextseek", "web"):
        for vol in SERVICES[name]["volumes"]:
            parts = vol.split(":")
            source, target, opts = parts[0], parts[1], parts[2:]
            if source.startswith((".", "/", "$")) or "ro" in ",".join(opts).split(","):
                continue  # a host bind or a read-only mount
            targets.add(target)
    return targets


def test_last_user_is_1001():
    users = [line for line in INSTRUCTIONS if line.startswith("USER ")]
    assert users and users[-1] in ("USER 1001:1001", "USER 1001"), users
    assert INSTRUCTIONS.index(users[-1]) > _index(r"^RUN .*compileall"), "USER comes after the root build steps"
    assert INSTRUCTIONS.index(users[-1]) > _index(r"^EXPOSE ")


def test_useradd_makes_uid_1001_with_a_home():
    line = INSTRUCTIONS[_index(r"\buseradd\b")]
    assert re.search(r"useradd\b.*-u 1001\b", line) and re.search(r"useradd\b.*-m\b", line), line


def test_python_install_dir_is_set_before_uv_sync():
    env = _index(r"^ENV UV_PYTHON_INSTALL_DIR=/opt/")
    assert env < _index(r"\buv sync\b")


def test_runtime_written_image_paths_are_chowned_to_1001():
    chown = " ".join(line for line in INSTRUCTIONS if re.search(r"\bchown\b.*\b1001:1001\b", line))
    assert CONTEXT_DIR in chown and "/app/schema_rag/duckdb" in chown
    assert _index(r"\bchown\b.*" + re.escape(CONTEXT_DIR)) > _index(r"^COPY \. /app/")


def test_every_writable_named_volume_target_is_created_1001_owned():
    created = " ".join(line for line in INSTRUCTIONS if re.search(r"\binstall -d\b.*-o 1001 -g 1001\b", line))
    targets = _writable_named_volume_targets() | {"/app/runs"}
    assert {"/static", "/media", "/var/celery", "/dmac/users", "/app/.ssh"} <= targets, targets
    missing = sorted(t for t in targets if not re.search(re.escape(t) + r"(\s|$)", created))
    assert not missing, f"not created 1001-owned in the Dockerfile: {missing}"


def test_ssh_dir_is_private():
    line = next(l for l in INSTRUCTIONS if "/app/.ssh" in l and "install -d" in l)
    assert re.search(r"install -d -m 0700 .*/app/\.ssh", line), line
