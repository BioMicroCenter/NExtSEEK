"""startup.lib.ownership (pure argv/targets) and startup.steps.volumes.ensure_app_ownership."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from startup.lib import ownership
from startup.lib.ownership import Target
from startup.steps import volumes

IMG = "nextseek-nextseek:latest"
NOT_OWNED = r"\( ! -user 10001 -o ! -group 10001 \)"
H = "1000"
NOT_OWNED_BIND = r"\( ! -user 10001 -o ! -group 1000 -o \( -type d ! -perm -2070 \) \)"


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
    got = ownership.targets(_cfg(tmp_path), tmp_path, H)
    assert [(t.kind, t.source, t.gid) for t in got] == [
        ("volume", "nextseek_batch_upload_state", "10001"),
        ("volume", "p1-nextseek-media", "10001"),          # once, though both services mount it
        ("bind", str(tmp_path / "outputs"), "1000"),
        ("volume", "p1-dmac-cc-users", "10001"),
        ("bind", str(tmp_path / "rel/logs"), "1000"),
    ]
    assert ownership.image(_cfg(tmp_path)) == IMG


def test_fix_argv_volume_and_bind_exact() -> None:
    sh = f"find /v -xdev {NOT_OWNED} -exec chown -h 10001:10001 {{}} +"
    bind_sh = (f"find /v -xdev {NOT_OWNED_BIND} -exec chown -h 10001:1000 {{}} +; "
               "find /v -xdev -type d ! -perm -2070 -exec chmod g+rwxs {} +; "
               "find /v -xdev -type f ! -perm -040 -exec chmod g+r {} +")
    head = ["docker", "run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "sh"]
    assert ownership.fix_argv(Target("volume", "p1-nextseek-media", "/media", "10001"), IMG) == [*head, "-v", "p1-nextseek-media:/v", IMG, "-c", sh]
    assert ownership.fix_argv(Target("bind", "/srv/logs", "/app/logs", "1000"), IMG) == [*head, "-v", "/srv/logs:/v:z", IMG, "-c", bind_sh]


def test_check_argv_mounts_read_only_and_counts() -> None:
    v = ownership.check_argv(Target("volume", "vol", "/x", "10001"), IMG)
    b = ownership.check_argv(Target("bind", "/srv/logs", "/x", "1000"), IMG)
    assert v[v.index("-v") + 1] == "vol:/v:ro"
    assert b[b.index("-v") + 1] == "/srv/logs:/v:ro,z"
    assert f"find /v -xdev {NOT_OWNED} | wc -l" in v[-1] and "chown" not in v[-1]
    assert f"find /v -xdev {NOT_OWNED_BIND} | wc -l" in b[-1]


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
        counts = volumes.ensure_app_ownership(tmp_path, {"NEXTSEEK_HOST_GID": H}, check_only=True, say=lambda *a: said.append(a))
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
        counts = volumes.ensure_app_ownership(tmp_path, {"NEXTSEEK_HOST_GID": H}, check_only=False)
    assert set(counts.values()) == {None}
    assert all("chown -h 10001:" in c[-1] for c in ran[1:]) and len(ran) == 6


def test_pre_switchover_pass_skips_the_cc_users_volume(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    ran: list[list[str]] = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        return MagicMock(returncode=0, stdout=cfg if cmd[:3] == ["docker", "compose", "config"] else "", stderr="")

    with patch("startup.steps.volumes.subprocess.run", side_effect=fake_run):
        counts = volumes.ensure_app_ownership(tmp_path, {"NEXTSEEK_HOST_GID": H}, check_only=False, skip_cc_users=True)
    assert "p1-dmac-cc-users" not in counts and len(counts) == 4


def test_a_missing_host_gid_is_refused_when_a_bind_is_a_target(tmp_path: Path) -> None:
    import pytest
    from startup.lib.docker_ops import DockerOpsError

    cfg = _cfg(tmp_path)
    with patch("startup.steps.volumes.subprocess.run",
               return_value=MagicMock(returncode=0, stdout=cfg, stderr="")):
        with pytest.raises(DockerOpsError, match="NEXTSEEK_HOST_GID"):
            volumes.ensure_app_ownership(tmp_path, {}, check_only=True)


def test_ensure_secret_group_read(tmp_path: Path) -> None:
    import os
    (tmp_path / "dmac").mkdir()
    ls = tmp_path / "dmac" / "local_settings.py"
    ls.write_text("x"); ls.chmod(0o644)
    key = tmp_path / "key"; key.write_text("k"); key.chmod(0o600)
    gid = os.getgid()
    problems = volumes.ensure_secret_group_read(tmp_path, {"NEXTSEEK_HOST_GID": str(gid), "LURIAKEY": str(key)})
    assert problems == []
    assert oct(ls.stat().st_mode & 0o777) == "0o640" and oct(key.stat().st_mode & 0o777) == "0o640"
    assert ls.stat().st_gid == gid
    # /dev/null and unset are skipped; a file owned by someone else is reported with the command to run
    assert volumes.ensure_secret_group_read(tmp_path, {"NEXTSEEK_HOST_GID": str(gid), "LURIAKEY": "/dev/null"}) == []
    other = tmp_path / "other"; other.write_text("k"); other.chmod(0o600)
    with patch("startup.steps.volumes.os.getuid", return_value=os.getuid() + 1):
        p = volumes.ensure_secret_group_read(tmp_path, {"NEXTSEEK_HOST_GID": str(gid), "LURIAKEY": str(other)})
    assert len(p) == 1 and "chgrp" in p[0] and "chmod 640" in p[0] and str(other) in p[0]
    assert oct(other.stat().st_mode & 0o777) == "0o600"


def test_ensure_secret_group_read_leaves_a_file_with_an_acl_alone(tmp_path: Path) -> None:
    (tmp_path / "dmac").mkdir()
    ls = tmp_path / "dmac" / "local_settings.py"
    ls.write_text("x"); ls.chmod(0o644)
    with patch("startup.steps.volumes.os.getxattr", return_value=b"acl"):
        p = volumes.ensure_secret_group_read(tmp_path, {"NEXTSEEK_HOST_GID": "1000"})
    assert len(p) == 1 and "ACL" in p[0] and oct(ls.stat().st_mode & 0o777) == "0o644"
