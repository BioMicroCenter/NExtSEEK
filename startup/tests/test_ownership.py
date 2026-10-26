"""startup.lib.ownership (pure argv/targets) and startup.steps.volumes.ensure_app_ownership."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from startup.lib import ownership
from startup.lib.ownership import Target
from startup.steps import volumes

IMG = "nextseek-nextseek:latest"
NOT_OWNED = r"\( ! -user 1001 -o ! -group 1001 \)"


def _cfg(tmp: Path) -> str:
    (tmp / "local_settings.py").write_text("x")
    (tmp / "outputs").mkdir(exist_ok=True)
    return json.dumps({
        "services": {
            "nextseek": {"image": IMG, "volumes": [
                {"type": "volume", "source": "batch_upload_state", "target": "/var/celery"},
                {"type": "volume", "source": "nextseek-media", "target": "/media"},
                {"type": "bind", "source": str(tmp / "outputs"), "target": "/app/outputs"},
                {"type": "bind", "source": str(tmp / "local_settings.py"), "target": "/app/dmac/local_settings.py"},
                {"type": "bind", "source": str(tmp / "themes"), "target": "/app/themes/NextSeek"},
            ]},
            "web": {"image": IMG, "volumes": [
                {"type": "volume", "source": "nextseek-media", "target": "/media"},
                {"type": "volume", "source": "dmac-cc-users", "target": "/dmac/users"},
                {"type": "volume", "source": "nextseek-static-files", "target": "/static", "read_only": True},
                {"type": "bind", "source": "/dev/null", "target": "/key", "read_only": True},
                {"type": "bind", "source": "rel/logs", "target": "/app/logs"},
            ]},
            "cc-spawner": {"image": "x", "volumes": [{"type": "volume", "source": "spawner-only", "target": "/s"}]},
        },
        "volumes": {
            "batch_upload_state": {"name": "nextseek_batch_upload_state"},
            "nextseek-media": {"name": "p1-nextseek-media"},
            "dmac-cc-users": {"name": "p1-dmac-cc-users"},
            "nextseek-static-files": {"name": "p1-nextseek-static-files"},
        },
    })


def test_targets_writable_only_prefixed_names_and_binds_under_repo_root(tmp_path: Path) -> None:
    got = ownership.targets(_cfg(tmp_path), tmp_path)
    assert [(t.kind, t.source) for t in got] == [
        ("volume", "nextseek_batch_upload_state"),
        ("volume", "p1-nextseek-media"),          # once, though both services mount it
        ("bind", str(tmp_path / "outputs")),
        ("volume", "p1-dmac-cc-users"),
        ("bind", str(tmp_path / "rel/logs")),
    ]
    assert ownership.image(_cfg(tmp_path)) == IMG


def test_fix_argv_volume_and_bind_exact() -> None:
    sh = f"find /v -xdev {NOT_OWNED} -exec chown -h 1001:1001 {{}} +"
    head = ["docker", "run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "sh"]
    assert ownership.fix_argv(Target("volume", "p1-nextseek-media", "/media"), IMG) == [*head, "-v", "p1-nextseek-media:/v", IMG, "-c", sh]
    assert ownership.fix_argv(Target("bind", "/srv/logs", "/app/logs"), IMG) == [*head, "-v", "/srv/logs:/v:z", IMG, "-c", sh]


def test_check_argv_mounts_read_only_and_counts() -> None:
    v = ownership.check_argv(Target("volume", "vol", "/x"), IMG)
    b = ownership.check_argv(Target("bind", "/srv/logs", "/x"), IMG)
    assert v[v.index("-v") + 1] == "vol:/v:ro"
    assert b[b.index("-v") + 1] == "/srv/logs:/v:ro,z"
    assert f"find /v -xdev {NOT_OWNED} | wc -l" in v[-1] and "chown" not in v[-1]


def test_ensure_app_ownership_runs_check_per_target_and_returns_counts(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    ran: list[list[str]] = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        if cmd[:3] == ["docker", "compose", "config"]:
            return MagicMock(returncode=0, stdout=cfg, stderr="")
        return MagicMock(returncode=0, stdout=f"{len(ran)}\n", stderr="")

    said: list[tuple] = []
    with patch("startup.steps.volumes.subprocess.run", side_effect=fake_run):
        counts = volumes.ensure_app_ownership(tmp_path, {}, check_only=True, say=lambda *a: said.append(a))
    assert list(counts.values()) == [2, 3, 4, 5, 6]
    assert all(c[-1].startswith("n=$(find") for c in ran[1:])
    assert [s[0] for s in said] == list(counts)


def test_ensure_app_ownership_fix_mode_runs_chown(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    ran: list[list[str]] = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        return MagicMock(returncode=0, stdout=cfg if cmd[:3] == ["docker", "compose", "config"] else "", stderr="")

    with patch("startup.steps.volumes.subprocess.run", side_effect=fake_run):
        counts = volumes.ensure_app_ownership(tmp_path, {}, check_only=False)
    assert set(counts.values()) == {None}
    assert all("chown -h 1001:1001" in c[-1] for c in ran[1:]) and len(ran) == 6
