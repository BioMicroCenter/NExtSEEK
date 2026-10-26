"""Which paths the app containers write, and the docker argv that hands them to uid/gid 1001.

Pure functions, no subprocess: the caller runs the argv lists (steps/volumes.py). The `nextseek` worker and `web`
run as 1001:1001, so every volume and host bind they write must be owned by it. The one-time switchover and
every later rebuild use the same two commands: FIX (chown what is not 1001) and CHECK (count what is not).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

UID = GID = 1001
SERVICES = ("nextseek", "web")
# Writable by compose, read-only in practice (theme templates from the checkout): never chowned.
SKIP_TARGET_PREFIXES = ("/app/themes",)

_NOT_OWNED = rf"\( ! -user {UID} -o ! -group {GID} \)"
FIX_SH = f"find /v -xdev {_NOT_OWNED} -exec chown -h {UID}:{GID} {{}} +"
CHECK_SH = (f'n=$(find /v -xdev {_NOT_OWNED} | wc -l); echo "$n"; '
            f"find /v -xdev {_NOT_OWNED} | head -5 >&2")


@dataclass(frozen=True)
class Target:
    kind: str     # "volume" or "bind"
    source: str   # rendered volume name, or absolute host path
    label: str    # container path it serves, for messages


def image(compose_config_json: str) -> str:
    return json.loads(compose_config_json)["services"]["nextseek"]["image"]


def targets(compose_config_json: str, repo_root: Path) -> list[Target]:
    """Writable named volumes and directory binds of `nextseek` and `web`, deduplicated, in compose order."""
    cfg = json.loads(compose_config_json)
    volumes = cfg.get("volumes") or {}
    seen: dict[str, Target] = {}
    for svc in SERVICES:
        for m in (cfg.get("services", {}).get(svc, {}).get("volumes") or []):
            if m.get("read_only") or m["target"].startswith(SKIP_TARGET_PREFIXES):
                continue
            if m["type"] == "volume":
                name = (volumes.get(m["source"]) or {}).get("name") or m["source"]
                seen.setdefault(name, Target("volume", name, m["target"]))
            elif m["type"] == "bind":
                src = Path(m["source"])
                if not src.is_absolute():
                    src = Path(repo_root) / src
                if src.exists() and not src.is_dir():   # a file bind (local_settings.py) or a socket
                    continue
                seen.setdefault(str(src), Target("bind", str(src), m["target"]))
    return list(seen.values())


def _argv(target: Target, image: str, sh: str, check: bool) -> list[str]:
    opts = (["ro"] if check else []) + (["z"] if target.kind == "bind" else [])
    mount = ":".join([target.source, "/v", *([",".join(opts)] if opts else [])])
    return ["docker", "run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "sh",
            "-v", mount, image, "-c", sh]


def fix_argv(target: Target, image: str) -> list[str]:
    return _argv(target, image, FIX_SH, check=False)


def check_argv(target: Target, image: str) -> list[str]:
    return _argv(target, image, CHECK_SH, check=True)
