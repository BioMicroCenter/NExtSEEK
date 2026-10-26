"""Create the named volumes required by the compose stack."""
from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path
from typing import Callable

from startup.lib import ownership
from startup.lib.docker_ops import DockerOpsError, _build_env, _check
from startup.lib.env import read_env

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
    owned by the app uid (the NS sidecar's non-root user), before Task 14's
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


def host_gid(repo_root: Path, env: dict[str, str]) -> str:
    """NEXTSEEK_HOST_GID: the compose env first, then the root .env (written by the config step)."""
    return env.get("NEXTSEEK_HOST_GID") or read_env(Path(repo_root) / ".env").get("NEXTSEEK_HOST_GID", "")


def ensure_app_ownership(
    repo_root: Path,
    env: dict[str, str],
    *,
    check_only: bool,
    skip_cc_users: bool = False,
    say: Callable[[str, int | None, float], None] | None = None,
) -> dict[str, int | None]:
    """Run the FIX (chown to the app uid) or CHECK (count what is not owned) pass over every writable
    volume and bind of `nextseek` and `web`. Returns {target: count}; FIX returns None for a count
    (it changes what it finds and does not count it). `say(label, count, seconds)` is called per target.
    `skip_cc_users` leaves dmac-cc-users alone: before the switchover its running agents still own
    their 0600 files as the old uid, and a chown would lock them out."""
    cfg = _check_out(_run(["docker", "compose", "config", "--format", "json"], repo_root, env),
                     "docker compose config")
    img = ownership.image(cfg)
    gid = host_gid(repo_root, env)
    all_targets = ownership.targets(cfg, repo_root, gid or "0")
    if any(t.kind == "bind" for t in all_targets) and not gid:
        raise DockerOpsError("NEXTSEEK_HOST_GID is not set (.env): the log and output binds need it")
    counts: dict[str, int | None] = {}
    for t in all_targets:
        if skip_cc_users and t.label == ownership.CC_USERS_TARGET:
            continue
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
    user (the switchover to the dedicated app uid). No running container, or an unreadable answer, is not a change."""
    run = lambda cmd: _run(cmd, repo_root, env)  # noqa: E731
    cfg = _check_out(run(["docker", "compose", "config", "--format", "json"]), "docker compose config")
    want = _user(run, ["docker", "image", "inspect", "-f", "{{.Config.User}}", ownership.image(cfg)]) or "0"
    for svc in ownership.SERVICES:
        for cid in _user(run, ["docker", "compose", "ps", "-q", svc]).split():
            have = _user(run, ["docker", "inspect", "-f", "{{.Config.User}}", cid]) or "0"
            if have != want:
                return True
    return False


def before_switchover(repo_root: Path, env: dict[str, str]) -> bool:
    """True until the app runs as APP_UID:APP_GID: a running `web` or `nextseek`, or the app image at the compose
    tag, whose user is anything else (empty, root, 0, an old uid). Until then the agents on the old uid own 0600
    files in dmac-cc-users, so fix-ownership must leave that volume to the switchover rebuild. Fails closed: a
    docker answer it cannot read raises."""
    run = lambda cmd: _run(cmd, repo_root, env)  # noqa: E731
    want = f"{ownership.APP_UID}:{ownership.APP_GID}"
    cfg = _check_out(run(["docker", "compose", "config", "--format", "json"]), "docker compose config")
    img = ownership.image(cfg)
    users = [_check_out(run(["docker", "image", "inspect", "-f", "{{.Config.User}}", img]), f"docker image inspect {img}")]
    for svc in ownership.SERVICES:
        for cid in _check_out(run(["docker", "compose", "ps", "-q", svc]), f"docker compose ps {svc}").split():
            users.append(_check_out(run(["docker", "inspect", "-f", "{{.Config.User}}", cid]), f"docker inspect {cid}"))
    return any(u.strip() != want for u in users)


def stop_app_services(repo_root: Path, env: dict[str, str]) -> None:
    """Stop `web`, `nextseek` and `nextseek-sidecar` (each drains first) so the FIX pass sees no writers."""
    _check(_run(["docker", "compose", "stop", *ownership.STOP_SERVICES], repo_root, env),
           "docker compose stop " + " ".join(ownership.STOP_SERVICES))


def ensure_secret_group_read(repo_root: Path, env: dict[str, str]) -> list[str]:
    """Give dmac/local_settings.py and the Luria key (LURIAKEY, skipped when unset or /dev/null) mode 0640
    and group NEXTSEEK_HOST_GID, so the app (which has that gid as an extra group) can read them.

    Runs on the host as the installing user, no container. Returns one message per problem (empty = done):
    a file we do not own gets the command for its owner to run; a file with a POSIX ACL is left untouched
    (a chmod would rewrite the ACL mask). No box has ACLs on these files today."""
    gid = host_gid(repo_root, env)
    if not gid.isdigit():
        return ["NEXTSEEK_HOST_GID is not set (.env), so the secret files cannot be given a group"]
    paths = [Path(repo_root) / "dmac" / "local_settings.py"]
    key = env.get("LURIAKEY") or read_env(Path(repo_root) / ".env").get("LURIAKEY", "")
    if key and key != "/dev/null":
        paths.append(Path(key))
    problems: list[str] = []
    for p in paths:
        if not p.is_file():
            continue
        st = p.stat()
        if st.st_gid == int(gid) and stat.S_IMODE(st.st_mode) == 0o640:
            continue
        try:
            os.getxattr(p, "system.posix_acl_access")
            problems.append(f"{p} has a POSIX ACL: left alone (mode/group not changed); check it by hand")
            continue
        except OSError:
            pass
        fix = f"sudo chgrp {gid} {p} && sudo chmod 640 {p}"
        if st.st_uid != os.getuid():
            problems.append(f"{p} is not owned by this user: its owner runs `{fix}`")
            continue
        try:
            os.chown(p, -1, int(gid))
            os.chmod(p, 0o640)
        except PermissionError:
            problems.append(f"could not set group {gid} on {p} (this user is not in that group): run `{fix}`")
    return problems
