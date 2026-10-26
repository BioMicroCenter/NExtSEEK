"""Which paths the app containers write, and the docker argv that hands them to the dedicated app uid/gid (APP_UID).

Pure functions, no subprocess: the caller runs the argv lists (steps/volumes.py). The `nextseek` worker and `web`
run as APP_UID:APP_GID, so every volume and host bind they write must be owned by it. The one-time switchover and
every later rebuild use the same two commands: FIX (chown what is not) and CHECK (count what is not).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

APP_UID = APP_GID = 10001   # dedicated: the app, cc-agent and ns-sidecar images all use it
SERVICES = ("nextseek", "web")
STOP_SERVICES = ("web", "nextseek", "nextseek-sidecar")   # the switchover stops all three
PEER_SERVICES = ("cc-agent", "nextseek-sidecar")   # share dmac-cc-users with the app: on APP_UID before it switches
CC_USERS_TARGET = "/dmac/users"
# Theme templates from the checkout, bound read-only (targets() skips read-only mounts too): never chowned.
SKIP_TARGET_PREFIXES = ("/app/themes",)

_VOL_NOT_OWNED = rf"\( ! -user {APP_UID} -o ! -group {APP_GID} \)"
# host binds: owner APP_UID, group = the deploy account's gid (H), folders setgid + group rwx
_BIND_NOT_OWNED = rf"\( ! -user {APP_UID} -o ! -group {{h}} -o \( -type d ! -perm -2070 \) \)"
CHECK_SH = '''n=$(find /v -xdev {w} | wc -l); echo "$n"; find /v -xdev {w} | head -5 >&2'''


@dataclass(frozen=True)
class Target:
    kind: str     # "volume" or "bind"
    source: str   # rendered volume name, or absolute host path
    label: str    # container path it serves, for messages
    gid: str      # group wanted: APP_GID for a volume, the host gid H for a bind


def image(compose_config_json: str) -> str:
    return json.loads(compose_config_json)["services"]["nextseek"]["image"]


def targets(compose_config_json: str, repo_root: Path, host_gid: str) -> list[Target]:
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
                seen.setdefault(name, Target("volume", name, m["target"], str(APP_GID)))
            elif m["type"] == "bind":
                src = Path(m["source"])
                if not src.is_absolute():
                    src = Path(repo_root) / src
                if src.exists() and not src.is_dir():   # a file bind (local_settings.py) or a socket
                    continue
                seen.setdefault(str(src), Target("bind", str(src), m["target"], str(host_gid)))
    return list(seen.values())


def _where(target: Target) -> str:
    return _BIND_NOT_OWNED.format(h=target.gid) if target.kind == "bind" else _VOL_NOT_OWNED


def _fix_sh(target: Target) -> str:
    chown = f"find /v -xdev {_where(target)} -exec chown -h {APP_UID}:{target.gid} {{}} +"
    if target.kind == "volume":
        return chown
    # Folders only: setgid + group rwx, so new files land in group H and the app's umask 002 makes them 0664.
    # Files keep their mode (a 0600 log stays private; the root 0644 ones are group-readable already).
    return f"{chown}; find /v -xdev -type d ! -perm -2070 -exec chmod g+rwxs {{}} +"


def _argv(target: Target, image: str, sh: str, check: bool) -> list[str]:
    opts = (["ro"] if check else []) + (["z"] if target.kind == "bind" else [])
    mount = ":".join([target.source, "/v", *([",".join(opts)] if opts else [])])
    return ["docker", "run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "sh",
            "-v", mount, image, "-c", sh]


def fix_argv(target: Target, image: str) -> list[str]:
    return _argv(target, image, _fix_sh(target), check=False)


def check_argv(target: Target, image: str) -> list[str]:
    return _argv(target, image, CHECK_SH.format(w=_where(target)), check=True)
