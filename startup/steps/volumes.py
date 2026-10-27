"""Create the named volumes required by the compose stack."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from pathlib import Path
from typing import Callable

from startup.lib import ownership
from startup.lib.docker_ops import DockerOpsError, _build_env, _check
from startup.lib.env import read_env, update_env

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
        # Not created yet (prod has no nextseek-media or throttle volume before this deploy): a `docker run -v`
        # would create it without compose's labels. The image's 10001 mount point owns it on its first mount.
        if t.kind == "volume" and not volume_exists(t.source):
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


def peers_not_on_app_uid(repo_root: Path, env: dict[str, str]) -> list[str]:
    """The cc-agent and sidecar images (by their compose tags) that do not run as APP_UID, one line each.
    `id -u` in a throwaway container: no network, never pulled. The switchover must not start while either
    is still on the old uid: the app's FIX would hand their files to a uid they do not run as."""
    cfg = json.loads(_check_out(_run(["docker", "compose", "config", "--format", "json"], repo_root, env),
                                "docker compose config"))
    bad: list[str] = []
    for svc in ownership.PEER_SERVICES:
        img = cfg["services"][svc]["image"]
        r = _run(["docker", "run", "--rm", "--pull", "never", "--network", "none", "--entrypoint", "id", img, "-u"],
                 repo_root, env)
        uid = (r.stdout or "").strip()
        if r.returncode != 0 or uid != str(ownership.APP_UID):
            bad.append(f"{svc} ({img}) runs as {uid or 'unknown: ' + (r.stderr or '').strip()[:160]}")
    return bad


def stop_app_services(repo_root: Path, env: dict[str, str]) -> None:
    """Stop `web`, `nextseek` and `nextseek-sidecar` (each drains first) so the FIX pass sees no writers."""
    _check(_run(["docker", "compose", "stop", *ownership.STOP_SERVICES], repo_root, env),
           "docker compose stop " + " ".join(ownership.STOP_SERVICES))


LURIA_KEY_COPY = "docker/luria-key"     # gitignored and dockerignored; compose binds it at the LURIAKEY path
LURIA_KEY_COPY_VAR = "NEXTSEEK_LURIA_KEY_COPY"


def _sync_luria_key(repo_root: Path, env: dict[str, str], gid: int) -> list[str]:
    """Copy the Luria key (LURIAKEY) to docker/luria-key, mode 0640, group `gid`, and point compose at it
    (NEXTSEEK_LURIA_KEY_COPY in .env). The source is never touched: OpenSSH refuses a group-readable key for its
    owner, and on dev it is ~/.ssh/luria. The copy is rewritten in place (a running bind keeps its inode) when
    the source changes. No key (unset, /dev/null, not a file): the copy goes and compose binds /dev/null."""
    root_env = Path(repo_root) / ".env"
    key = env.get("LURIAKEY") or read_env(root_env).get("LURIAKEY", "")
    copy = Path(repo_root) / LURIA_KEY_COPY
    want = "/dev/null"
    if key and key != "/dev/null" and Path(key).is_file():
        try:
            data = Path(key).read_bytes()
            copy.parent.mkdir(parents=True, exist_ok=True)
            if not copy.is_file() or copy.is_symlink() or copy.read_bytes() != data:
                fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
            os.chown(copy, -1, gid)
            os.chmod(copy, 0o640)
        except OSError as exc:
            return [f"could not copy the Luria key {key} to {copy} ({exc}): make {key} readable by this user "
                    f"and remove {copy} if it is a directory or someone else's file"]
        want = f"./{LURIA_KEY_COPY}"
    else:
        try:
            copy.unlink(missing_ok=True)
        except IsADirectoryError:   # docker made it when the bind ran before startup: compose binds /dev/null now
            pass
    if read_env(root_env).get(LURIA_KEY_COPY_VAR, "/dev/null") != want:
        update_env(root_env, {LURIA_KEY_COPY_VAR: want})
    return []


def ensure_secret_group_read(repo_root: Path, env: dict[str, str]) -> list[str]:
    """Give dmac/local_settings.py group NEXTSEEK_HOST_GID and group read only (g+r,g-wx,o-rwx: 0644 -> 0640; the
    owner's bits are never widened, so 0400 -> 0440), so the app (which has that gid as an extra group) can read
    it, and keep the group-readable copy of the Luria key current (_sync_luria_key).

    Runs on the host as the installing user, no container, and changes no file outside the repo. Returns one
    message per problem (empty = done): a file we do not own gets the command for its owner to run; a file
    with a POSIX ACL is left untouched (a chmod would rewrite the ACL mask). No box has ACLs on these files today."""
    gid = host_gid(repo_root, env)
    if not gid.isdigit():
        return ["NEXTSEEK_HOST_GID is not set (.env), so the secret files cannot be given a group"]
    paths = [Path(repo_root) / "dmac" / "local_settings.py"]
    problems: list[str] = _sync_luria_key(repo_root, env, int(gid))
    for p in paths:
        if not p.is_file():
            continue
        st = p.stat()
        mode = stat.S_IMODE(st.st_mode)
        if st.st_gid == int(gid) and mode & 0o077 == 0o040:
            continue
        try:
            os.getxattr(p, "system.posix_acl_access")
            problems.append(f"{p} has a POSIX ACL: left alone (mode/group not changed); check it by hand")
            continue
        except OSError:
            pass
        fix = f"sudo chgrp {gid} {p} && sudo chmod g+r,g-wx,o-rwx {p}"
        if st.st_uid != os.getuid():
            problems.append(f"{p} is not owned by this user: its owner runs `{fix}`")
            continue
        try:
            os.chown(p, -1, int(gid))
            os.chmod(p, (mode & 0o700) | 0o040)
        except PermissionError:
            problems.append(f"could not set group {gid} on {p} (this user is not in that group): run `{fix}`")
    return problems
