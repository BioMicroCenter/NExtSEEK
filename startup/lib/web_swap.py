"""Gap-free replacement of the `web` container (SPEC-PLAN section 3).

`./startup.sh rebuild` calls swap() after it has recreated the worker. The old web
container keeps serving while a second one starts on the new image; nginx is pointed
at one container by name through a pin file, switched with a graceful reload, and the
old container is then stopped (its stop_grace_period lets running chat turns finish).

The mechanism was proved on a toy stack (compose 5.4, nginx 1.31.2):
`up -d --no-deps --no-recreate --scale web=2 web` adds web-N+1 on the new image and
leaves the old one, container-name DNS works for the pin, `nginx -s reload` is
graceful, and a plain `up -d` afterwards leaves the survivor alone.

Every command goes through one injected `run(cmd) -> result` (returncode, stdout,
stderr), so the tests drive the whole sequence with a fake docker. The pin exists
only during a swap; every failure path either leaves it on a running container or
removes it, and `doctor` reports one that a killed swap left behind.
"""
from __future__ import annotations

import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from startup.lib.docker_ops import _build_env

NGINX = "nextseek_nginx"
HEALTHY_TIMEOUT_S = 180
PIN_DIR = Path("docker") / "nginx-upstream"
PIN_FILE = "pin.conf"
PY = "/app/.venv/bin/python"
LOGIN_URL = "http://127.0.0.1:8000/login/"

#: Run inside the NEW web container: one GET /login/ per gunicorn process in
#: parallel (every worker gets compiled and cached before nginx sends it a user),
#: all of them 200 within 10 s. ponytail: counts every process whose command line
#: names gunicorn (master and the uv wrapper too, at most 2 extra requests); under
#: daphne it finds none and sends 2.
WARM_SCRIPT = """\
import glob, sys, threading, urllib.request
n = 0
for p in glob.glob('/proc/[0-9]*/cmdline'):
    try:
        n += b'gunicorn' in open(p, 'rb').read()
    except OSError:
        pass
n = min(max(n, 2), 32)
bad = []
def get():
    try:
        with urllib.request.urlopen(sys.argv[1], timeout=10) as r:
            if r.status != 200:
                bad.append(r.status)
    except Exception as e:
        bad.append(repr(e))
ts = [threading.Thread(target=get) for _ in range(n)]
[t.start() for t in ts]
[t.join(12) for t in ts]
if bad or any(t.is_alive() for t in ts):
    print('warm-up failed:', bad or 'timeout')
    sys.exit(1)
print('warm', n)
"""


class SwapError(RuntimeError):
    """The swap could not finish. The message says what the operator does next."""


def make_runner(project_dir: str | Path, env: dict[str, str]) -> Callable[[Sequence[str]], subprocess.CompletedProcess]:
    def run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
        return subprocess.run(list(cmd), cwd=str(project_dir), env=_build_env(env),
                              capture_output=True, text=True)
    return run


def pin_path(repo_root: Path) -> Path:
    return Path(repo_root) / PIN_DIR / PIN_FILE


def leftover_pin(repo_root: Path) -> str | None:
    """The container name a pin file names, or None when there is no pin."""
    path = pin_path(repo_root)
    if not path.is_file():
        return None
    text = path.read_text()
    return text.split('"')[1] if '"' in text else text.strip() or "(empty)"


def refuse_leftover_pin(repo_root: Path) -> None:
    """Raise when a killed swap left a pin: nginx may name a container that is gone, and replacing web in any way
    (swap, start, recreate) would report success while every request answers 502."""
    name = leftover_pin(repo_root)
    if name is not None:
        raise SwapError(
            f"docker/nginx-upstream/pin.conf names {name}: a swap was killed part-way. "
            "Delete the file and reload nginx (`docker compose exec nextseek_nginx nginx -s reload`), "
            "then rebuild again.")


def migrations_pending(run) -> tuple[bool, str]:
    """True when the NEW image has migrations the database lacks (or the check itself fails).

    Old code must not run on a new schema, so either answer sends the deploy to
    stop-first. Runs in a throwaway container of the new image.
    """
    r = run(["docker", "compose", "run", "--rm", "--no-deps", "--entrypoint", PY,
             "nextseek", "manage.py", "migrate", "--check"])
    if r.returncode == 0:
        return False, ""
    return True, ((r.stdout or "") + (r.stderr or "")).strip()[-400:]


def _ok(run, cmd, what: str) -> str:
    r = run(cmd)
    if r.returncode != 0:
        raise SwapError(f"{what} failed: {((r.stderr or '') + (r.stdout or '')).strip()[-300:]}")
    return (r.stdout or "").strip()


def _reload(run) -> None:
    _ok(run, ["docker", "compose", "exec", "-T", NGINX, "nginx", "-t"], "nginx config test")
    _ok(run, ["docker", "compose", "exec", "-T", NGINX, "nginx", "-s", "reload"], "nginx reload")


def _pin(run, repo_root: Path, name: str | None) -> None:
    path = pin_path(repo_root)
    if name:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'set $upstream "{name}";\n')
    else:
        path.unlink(missing_ok=True)
    _reload(run)


DRAIN_MARGIN_S = 30   # web's stop_grace_period must outlast the drain by this much (gunicorn's own stop)


def _seconds(value: str) -> int:
    total = 0
    for n, unit in re.findall(r"(\d+)([hms])", str(value)):
        total += int(n) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


def drain_problem(repo_root: Path) -> str | None:
    """A message when the NEXTSEEK_DRAIN_SECONDS set in .env (or docker/nextseek.env) is more than web's
    stop_grace_period allows: Docker would SIGKILL web before it cuts the turns it could not finish."""
    import yaml
    from startup.lib.env import read_env

    repo_root = Path(repo_root)
    configured = {**read_env(repo_root / "docker" / "nextseek.env"), **read_env(repo_root / ".env")}.get(
        "NEXTSEEK_DRAIN_SECONDS", "")
    compose = repo_root / "docker-compose.yml"
    if not configured.strip().isdigit() or not compose.exists():
        return None
    grace = _seconds(((yaml.safe_load(compose.read_text()).get("services") or {}).get("web") or {})
                     .get("stop_grace_period", ""))
    limit = grace - DRAIN_MARGIN_S
    if grace and int(configured) > limit:
        return (f"NEXTSEEK_DRAIN_SECONDS={configured} is above {limit} (web's stop_grace_period {grace} s "
                f"minus {DRAIN_MARGIN_S} s): the entrypoint clamps it to {limit}. Lower it or raise the grace period.")
    return None


def swap(run, repo_root: Path, *, say=print, warn=print, sleep=time.sleep,
         clock=time.monotonic, healthy_timeout: float = HEALTHY_TIMEOUT_S,
         utcnow=lambda: datetime.now(timezone.utc)) -> str:
    """Replace the running web container. Returns "swapped" or "started"."""
    repo_root = Path(repo_root)
    refuse_leftover_pin(repo_root)
    if (problem := drain_problem(repo_root)):
        warn(problem)
    up_web = ["docker", "compose", "up", "-d", "--no-deps", "web"]
    ids = _ok(run, ["docker", "compose", "ps", "-q", "web"], "listing web containers").split()
    if len(ids) > 1:
        raise SwapError(
            f"more than one web container is running ({len(ids)}): a swap was killed part-way. "
            "Stop and remove the extra one (`docker ps --filter name=web`, `docker stop` then "
            "`docker rm` the older), delete docker/nginx-upstream/pin.conf, run "
            "`docker compose exec nextseek_nginx nginx -s reload`, then rebuild again.")
    if not ids:
        say("no web container is running: starting one (no swap needed)")
        _ok(run, up_web, "starting web")
        return "started"
    if not run(["docker", "compose", "ps", "-q", "--status=running", NGINX]).stdout.strip():
        say("the front door is down, so nobody is being served: replacing web in place")
        _ok(run, [*up_web[:-1], "--force-recreate", "web"], "recreating web")
        return "started"

    old = ids[0]
    old_name = _ok(run, ["docker", "inspect", "-f", "{{.Name}}", old], "reading the old container name").lstrip("/")
    new = None
    committed = False   # True once the pin names the new container: it stays on a failure
    try:
        _pin(run, repo_root, old_name)
        say(f"pinned the front door to {old_name}")
        _ok(run, ["docker", "compose", "up", "-d", "--no-deps", "--no-recreate",
                  "--scale", "web=2", "web"], "starting the second web container")
        now = _ok(run, ["docker", "compose", "ps", "-q", "web"], "listing web containers").split()
        fresh = [i for i in now if i != old]
        if not fresh:
            raise SwapError("the second web container did not appear")
        new = fresh[0]
        new_name = _ok(run, ["docker", "inspect", "-f", "{{.Name}}", new], "reading the new container name").lstrip("/")
        say(f"{new_name} starting on the new image")
        _wait_healthy(run, new, new_name, sleep, clock, healthy_timeout)
        r = run(["docker", "exec", new, PY, "-c", WARM_SCRIPT, LOGIN_URL])
        if r.returncode != 0:
            raise SwapError(f"{new_name} did not warm up: {(r.stdout or r.stderr or '').strip()[-200:]}")
        say(f"{new_name} healthy and warm")
        committed = True
        try:
            _pin(run, repo_root, new_name)
        except SwapError:
            committed = False
            _pin(run, repo_root, old_name)   # back to the container we know is serving
            raise
        say(f"nginx switched to {new_name} at {utcnow().isoformat(timespec='milliseconds').replace('+00:00', 'Z')}")
        say(f"front door switched to {new_name}; stopping {old_name} (running chat turns finish first)")
        r = run(["docker", "stop", old])
        if r.returncode != 0:
            raise SwapError(
                f"docker stop {old_name} failed ({(r.stderr or '').strip()[-200:]}). The front door already "
                f"serves {new_name}; stop and remove {old_name} by hand, then delete "
                "docker/nginx-upstream/pin.conf and reload nginx.")
        _ok(run, ["docker", "rm", old], f"removing {old_name}")
        _pin(run, repo_root, None)
        say(f"swap done: {new_name} serves, pin removed")
        return "swapped"
    except BaseException:
        if not committed:
            if new:
                run(["docker", "rm", "-f", new])
            try:
                _pin(run, repo_root, None)
            except SwapError as exc:   # the file is gone; nginx falls back to the service name on its next reload
                warn(f"could not reload nginx while unpinning: {exc}")
        raise


def _wait_healthy(run, cid: str, name: str, sleep, clock, timeout: float) -> None:
    deadline = clock() + timeout
    while True:
        status = _ok(run, ["docker", "inspect", "-f", "{{.State.Health.Status}}", cid], "reading health")
        if status == "healthy":
            return
        if status == "unhealthy":
            raise SwapError(f"{name} is unhealthy; removed it, the old container still serves")
        if clock() >= deadline:
            raise SwapError(f"{name} not healthy after {int(timeout)} s; removed it, the old container still serves")
        sleep(1)
