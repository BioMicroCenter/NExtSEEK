"""Create the named volumes required by the compose stack."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable

from startup.lib import ownership
from startup.lib.docker_ops import _build_env, _check

from startup.lib.docker_ops import (
    DockerOpsError, volume_exists, volume_create, volume_remove, volume_users,
    bootstrap_staging_dir,
)

REQUIRED_VOLUMES: list[str] = [
    "seek-filestore",
    "seek-mysql-db",
    "seek-solr-data",
    "seek-cache",
    "nextseek-static-files",
    "neo4j-data",
    # Container-CC per-project/per-user trees (G7-10) — persists CC input,
    # shared, scratch, cc-state, memory, and output across container recreate.
    "dmac-cc-users",
]


def volume_names_for_prefix(prefix: str) -> list[str]:
    """Return the full volume names including the instance prefix."""
    return [f"{prefix}{name}" for name in REQUIRED_VOLUMES]


def ensure_volumes(prefix: str) -> list[str]:
    """Create any missing volumes. Returns the names actually created (idempotent: empty if all already exist)."""
    created: list[str] = []
    for full_name in volume_names_for_prefix(prefix):
        if not volume_exists(full_name):
            volume_create(full_name)
            created.append(full_name)
    return created


def remove_volumes(prefix: str) -> list[str]:
    """Remove every data volume of the instance. Returns the names removed.

    Refuses, removing nothing, if any container still mounts one of them.
    """
    present = [n for n in volume_names_for_prefix(prefix) if volume_exists(n)]
    for name in present:
        users = volume_users(name)
        if users:
            raise DockerOpsError(f"{name} is still used by {', '.join(users)}; nothing was removed")
    for name in present:
        volume_remove(name)
    return present


def ensure_cc_staging_dir(prefix: str) -> None:
    """Step 2c (G7-11 Task 13, iter-1 M-1): ensure the `_staging` subdir
    exists inside the (possibly instance-prefixed) `dmac-cc-users` volume,
    owned by uid 1001 (the NS sidecar's non-root user), before Task 14's
    `_staging` subpath mount is ever attempted.

    Docker's Engine refuses to start a container whose
    `VolumeOptions.Subpath` backing directory does not already exist inside
    the volume, and compose `restart:` does NOT retry container-create
    failures -- so this must run at install time (`./startup.sh install`,
    same as `ensure_volumes` above), not lazily at sidecar startup. `_staging`
    is a reserved top-level name distinct from every project dir (those are
    always `{pid}-{slug}`, always containing a hyphen after a numeric id),
    so it can never collide with per-project CC trees in the same volume.
    Idempotent (mkdir -p / chown).
    """
    bootstrap_staging_dir(f"{prefix}dmac-cc-users")


def _run(cmd: list[str], repo_root: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(repo_root), env=_build_env(env), capture_output=True, text=True)


def ensure_app_ownership(
    repo_root: Path,
    env: dict[str, str],
    *,
    check_only: bool,
    say: Callable[[str, int | None, float], None] | None = None,
) -> dict[str, int | None]:
    """Run the FIX (chown to 1001:1001) or CHECK (count what is not 1001) pass over every writable
    volume and bind of `nextseek` and `web`. Returns {target: count}; FIX returns None for a count
    (it changes what it finds and does not count it). `say(label, count, seconds)` is called per target."""
    cfg = _check_out(_run(["docker", "compose", "config", "--format", "json"], repo_root, env),
                     "docker compose config")
    img = ownership.image(cfg)
    counts: dict[str, int | None] = {}
    for t in ownership.targets(cfg, repo_root):
        argv = (ownership.check_argv if check_only else ownership.fix_argv)(t, img)
        t0 = time.monotonic()
        out = _check_out(subprocess.run(argv, capture_output=True, text=True), f"ownership pass on {t.source}")
        n = int(out.strip().splitlines()[-1]) if check_only else None
        counts[t.source] = n
        if say:
            say(t.source, n, time.monotonic() - t0)
    return counts


def _check_out(result: subprocess.CompletedProcess, context: str) -> str:
    _check(result, context)
    return result.stdout or ""


def _user(run: Callable[[list[str]], subprocess.CompletedProcess], cmd: list[str]) -> str:
    r = run(cmd)
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def app_user_change(repo_root: Path, env: dict[str, str]) -> bool:
    """True when a running `web` or `nextseek` runs as a different user than the new image's configured
    user (the root-to-1001 switchover). No running container, or an unreadable answer, is not a change."""
    run = lambda cmd: _run(cmd, repo_root, env)  # noqa: E731
    cfg = _check_out(run(["docker", "compose", "config", "--format", "json"]), "docker compose config")
    want = _user(run, ["docker", "image", "inspect", "-f", "{{.Config.User}}", ownership.image(cfg)]) or "0"
    for svc in ownership.SERVICES:
        for cid in _user(run, ["docker", "compose", "ps", "-q", svc]).split():
            have = _user(run, ["docker", "inspect", "-f", "{{.Config.User}}", cid]) or "0"
            if have != want:
                return True
    return False


def stop_app_services(repo_root: Path, env: dict[str, str]) -> None:
    """Stop `web` and `nextseek` (each drains its chat turns first) so the FIX pass sees no writers."""
    _check(_run(["docker", "compose", "stop", *ownership.SERVICES], repo_root, env), "docker compose stop web nextseek")
