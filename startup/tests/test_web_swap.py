"""The gap-free web swap (startup/lib/web_swap.py), run against a fake docker.

The fake answers each command by its shape and records them in order, so the tests
state the sequence the mechanism proved on the laptop toy (compose 5.4, nginx 1.31.2):
pin the old container, scale a second one up on the new image, wait healthy, warm it,
pin the new one, stop and remove the old, unpin.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from startup.lib import web_swap
from startup.lib.web_swap import SwapError

OLD, NEW = "a" * 12, "b" * 12


class FakeDocker:
    """Records every command; answers by shape. `fail` is a substring of the joined command."""

    def __init__(self, repo: Path, *, web_ids=(OLD,), health=("starting", "healthy"),
                 fail=None, nginx_running=True, warm_rc=0) -> None:
        self.repo = repo
        self.calls: list[list[str]] = []
        self.pins: list[str | None] = []   # the pin file's content after each reload
        self.web = list(web_ids)
        self.health = list(health)
        self.fail = fail
        self.nginx_running = nginx_running
        self.warm_rc = warm_rc

    def __call__(self, cmd):
        cmd = list(cmd)
        self.calls.append(cmd)
        joined = " ".join(cmd)
        if self.fail and self.fail in joined:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        out = ""
        if cmd[:4] == ["docker", "compose", "ps", "-q"] and cmd[-1] == "web":
            out = "\n".join(self.web)
        elif "nextseek_nginx" in cmd and "--status=running" in cmd:
            out = "nginxid" if self.nginx_running else ""
        elif cmd[:3] == ["docker", "inspect", "-f"] and cmd[3] == "{{.Name}}":
            out = "/nextseek-web-1" if cmd[4] == OLD else "/nextseek-web-2"
        elif cmd[:3] == ["docker", "inspect", "-f"] and "Health" in cmd[3]:
            out = self.health.pop(0) if len(self.health) > 1 else self.health[0]
        elif "--scale" in cmd:
            self.web.append(NEW)
        elif cmd[:2] == ["docker", "rm"] and cmd[2] in self.web:
            self.web.remove(cmd[2])
        elif cmd[:2] == ["docker", "exec"] and "-c" in cmd:
            return SimpleNamespace(returncode=self.warm_rc, stdout="", stderr="slow")
        elif cmd[-2:] == ["-s", "reload"]:
            pin = web_swap.pin_path(self.repo)
            self.pins.append(pin.read_text() if pin.exists() else None)
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    def verbs(self):
        """Compact names of the steps, for order checks."""
        names = []
        for c in self.calls:
            j = " ".join(c)
            if "-s reload" in j:
                names.append("reload")
            elif "nginx -t" in j:
                names.append("nginx-test")
            elif "--scale" in j:
                names.append("scale")
            elif c[:2] == ["docker", "stop"]:
                names.append(f"stop:{c[2]}")
            elif c[:2] == ["docker", "rm"]:
                names.append(f"rm:{c[2]}")
            elif "-c" in c and c[:2] == ["docker", "exec"]:
                names.append("warm")
        return names


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "docker" / "nginx-upstream").mkdir(parents=True)
    return tmp_path


def run_swap(fake: FakeDocker, repo: Path, **kw):
    return web_swap.swap(fake, repo, say=lambda m: None, warn=lambda m: None,
                         sleep=lambda s: None, **kw)


def test_swap_happy_path_orders_the_steps_and_ends_unpinned(repo: Path) -> None:
    fake = FakeDocker(repo)
    assert run_swap(fake, repo) == "swapped"
    assert fake.verbs() == [
        "nginx-test", "reload",          # pin the old
        "scale", "warm",                 # new one up, then warmed
        "nginx-test", "reload",          # pin the new
        f"stop:{OLD}", f"rm:{OLD}",      # old drains and goes
        "nginx-test", "reload",          # unpin
    ]
    assert fake.pins == ['set $upstream "nextseek-web-1";\n',
                         'set $upstream "nextseek-web-2";\n',
                         None]
    assert not web_swap.pin_path(repo).exists()


def test_the_nginx_switch_is_stamped_in_utc(repo: Path) -> None:
    import datetime as dt
    said = []
    web_swap.swap(FakeDocker(repo), repo, say=said.append, warn=lambda m: None, sleep=lambda s: None,
                  utcnow=lambda: dt.datetime(2026, 10, 26, 12, 0, 1, 250000, tzinfo=dt.timezone.utc))
    assert "nginx switched to nextseek-web-2 at 2026-10-26T12:00:01.250Z" in said


def test_scale_uses_no_recreate_so_the_old_container_stays(repo: Path) -> None:
    fake = FakeDocker(repo)
    run_swap(fake, repo)
    scale = next(c for c in fake.calls if "--scale" in c)
    assert scale == ["docker", "compose", "up", "-d", "--no-deps", "--no-recreate",
                     "--scale", "web=2", "web"]


def test_no_web_container_is_a_plain_up(repo: Path) -> None:
    fake = FakeDocker(repo, web_ids=())
    assert run_swap(fake, repo) == "started"
    assert fake.calls[-1] == ["docker", "compose", "up", "-d", "--no-deps", "web"]
    assert not web_swap.pin_path(repo).exists()
    assert fake.verbs() == []


def test_two_web_containers_refuse_with_the_fix(repo: Path) -> None:
    fake = FakeDocker(repo, web_ids=(OLD, NEW))
    with pytest.raises(SwapError, match="more than one web container"):
        run_swap(fake, repo)
    assert fake.verbs() == []           # nothing touched
    assert not web_swap.pin_path(repo).exists()


def test_nginx_down_replaces_web_in_place(repo: Path) -> None:
    """No front door means no traffic to protect and nothing to reload."""
    fake = FakeDocker(repo, nginx_running=False)
    assert run_swap(fake, repo) == "started"
    assert fake.calls[-1] == ["docker", "compose", "up", "-d", "--no-deps",
                              "--force-recreate", "web"]


def test_scale_failure_unpins(repo: Path) -> None:
    fake = FakeDocker(repo, fail="--scale")
    with pytest.raises(SwapError):
        run_swap(fake, repo)
    assert not web_swap.pin_path(repo).exists()
    assert fake.pins[-1] is None        # and nginx was told


def test_never_healthy_removes_the_new_container_and_unpins(repo: Path) -> None:
    fake = FakeDocker(repo, health=("starting",))
    clock = iter(range(0, 10_000, 30))
    with pytest.raises(SwapError, match="not healthy"):
        web_swap.swap(fake, repo, say=lambda m: None, warn=lambda m: None,
                      sleep=lambda s: None, clock=lambda: next(clock))
    assert ["docker", "rm", "-f", NEW] in fake.calls
    assert f"stop:{OLD}" not in fake.verbs()
    assert not web_swap.pin_path(repo).exists()


def test_unhealthy_status_fails_at_once(repo: Path) -> None:
    fake = FakeDocker(repo, health=("unhealthy",))
    with pytest.raises(SwapError, match="unhealthy"):
        run_swap(fake, repo)
    assert ["docker", "rm", "-f", NEW] in fake.calls
    assert not web_swap.pin_path(repo).exists()


def test_failed_warm_up_removes_the_new_container_and_unpins(repo: Path) -> None:
    fake = FakeDocker(repo, warm_rc=1)
    with pytest.raises(SwapError, match="warm"):
        run_swap(fake, repo)
    assert ["docker", "rm", "-f", NEW] in fake.calls
    assert f"stop:{OLD}" not in fake.verbs()
    assert not web_swap.pin_path(repo).exists()


def test_failed_stop_of_old_leaves_the_pin_on_the_running_new_container(repo: Path) -> None:
    fake = FakeDocker(repo, fail=f"docker stop {OLD}")
    with pytest.raises(SwapError, match="stop"):
        run_swap(fake, repo)
    assert web_swap.pin_path(repo).read_text() == 'set $upstream "nextseek-web-2";\n'


def test_nginx_test_failure_unpins_and_stops_before_scaling(repo: Path) -> None:
    fake = FakeDocker(repo, fail="nginx -t")
    with pytest.raises(SwapError):
        run_swap(fake, repo)
    assert "scale" not in fake.verbs()
    assert not web_swap.pin_path(repo).exists()


def test_leftover_pin_is_reported(repo: Path) -> None:
    assert web_swap.leftover_pin(repo) is None
    web_swap.pin_path(repo).write_text('set $upstream "nextseek-web-1";\n')
    assert web_swap.leftover_pin(repo) == "nextseek-web-1"


def test_leftover_pin_is_refused_before_a_swap_starts(repo: Path) -> None:
    web_swap.pin_path(repo).write_text('set $upstream "nextseek-web-1";\n')
    fake = FakeDocker(repo)
    with pytest.raises(SwapError, match="pin"):
        run_swap(fake, repo)
    assert fake.verbs() == []


@pytest.mark.parametrize("state", [{"web_ids": ()}, {"nginx_running": False}])
def test_leftover_pin_is_refused_when_web_would_be_replaced_in_place(repo: Path, state) -> None:
    """No web running, or the front door down: the in-place branches must not report success while nginx stays
    pinned to a container that is gone."""
    web_swap.pin_path(repo).write_text('set $upstream "nextseek-web-3";\n')
    fake = FakeDocker(repo, **state)
    with pytest.raises(SwapError, match="pin.conf names nextseek-web-3"):
        run_swap(fake, repo)
    assert not [c for c in fake.calls if c[:3] == ["docker", "compose", "up"]]
    assert web_swap.pin_path(repo).exists()


def test_migrations_pending_reads_the_exit_code() -> None:
    seen = []

    def fake(cmd):
        seen.append(list(cmd))
        return SimpleNamespace(returncode=1, stdout="Your models have changes", stderr="")

    pending, why = web_swap.migrations_pending(fake)
    assert pending is True and "Your models" in why
    assert seen[0] == ["docker", "compose", "run", "--rm", "--no-deps", "--entrypoint",
                       "/app/.venv/bin/python", "nextseek", "manage.py", "migrate", "--check"]
    clean, _ = web_swap.migrations_pending(lambda c: SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert clean is False


def test_warm_script_passes_against_a_live_server_and_fails_without_one() -> None:
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ok = subprocess.run([sys.executable, "-c", web_swap.WARM_SCRIPT,
                             f"http://127.0.0.1:{srv.server_port}/login/"], capture_output=True)
        assert ok.returncode == 0, ok.stderr
    finally:
        srv.shutdown()
    bad = subprocess.run([sys.executable, "-c", web_swap.WARM_SCRIPT,
                          f"http://127.0.0.1:{srv.server_port}/login/"], capture_output=True)
    assert bad.returncode != 0
