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
    # no file chmod: a 0600 log stays 0600 (setgid folders + the app's umask 002 make new files group-readable)
    bind_sh = (f"find /v -xdev {NOT_OWNED_BIND} -exec chown -h 10001:1000 {{}} +; "
               "find /v -xdev -type d ! -perm -2070 -exec chmod g+rwxs {} +")
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
        if cmd[:3] == ["docker", "volume", "inspect"]:
            return MagicMock(returncode=0, stdout="[]", stderr="")
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
        if cmd[:3] == ["docker", "volume", "inspect"]:
            return MagicMock(returncode=0, stdout="[]", stderr="")
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
        if cmd[:3] == ["docker", "volume", "inspect"]:
            return MagicMock(returncode=0, stdout="[]", stderr="")
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
    gid = os.getgid()
    problems = volumes.ensure_secret_group_read(tmp_path, {"NEXTSEEK_HOST_GID": str(gid)})
    assert problems == []
    assert oct(ls.stat().st_mode & 0o777) == "0o640" and ls.stat().st_gid == gid
    # a file owned by someone else is reported with the command for its owner, and left as it is
    ls.chmod(0o600)
    with patch("startup.steps.volumes.os.getuid", return_value=os.getuid() + 1):
        p = volumes.ensure_secret_group_read(tmp_path, {"NEXTSEEK_HOST_GID": str(gid)})
    assert len(p) == 1 and "chgrp" in p[0] and str(ls) in p[0]
    assert oct(ls.stat().st_mode & 0o777) == "0o600"


def test_the_luria_key_is_copied_never_chmodded(tmp_path: Path) -> None:
    """OpenSSH refuses a 0640 key for its owner, so the source (~/.ssh/luria on dev) is never touched: the app reads
    a repo-local copy (0640, group H) that compose binds at the same container path."""
    import os
    from startup.lib.env import read_env
    gid = os.getgid()
    ssh = tmp_path / "home" / ".ssh"; ssh.mkdir(parents=True)
    key = ssh / "luria"; key.write_text("k1"); key.chmod(0o600)
    repo = tmp_path / "repo"; repo.mkdir()
    env = {"NEXTSEEK_HOST_GID": str(gid), "LURIAKEY": str(key)}
    assert volumes.ensure_secret_group_read(repo, env) == []
    copy = repo / "docker" / "luria-key"
    assert oct(key.stat().st_mode & 0o777) == "0o600"                      # the source is untouched
    assert copy.read_text() == "k1" and oct(copy.stat().st_mode & 0o777) == "0o640" and copy.stat().st_gid == gid
    assert read_env(repo / ".env")["NEXTSEEK_LURIA_KEY_COPY"] == "./docker/luria-key"
    inode = copy.stat().st_ino
    key.write_text("k2")                                                   # a rotated key is picked up, in place
    assert volumes.ensure_secret_group_read(repo, env) == []
    assert copy.read_text() == "k2" and copy.stat().st_ino == inode
    # no key: the copy goes and compose binds /dev/null as before
    assert volumes.ensure_secret_group_read(repo, {"NEXTSEEK_HOST_GID": str(gid), "LURIAKEY": "/dev/null"}) == []
    assert not copy.exists() and read_env(repo / ".env")["NEXTSEEK_LURIA_KEY_COPY"] == "/dev/null"


def test_ensure_secret_group_read_leaves_a_file_with_an_acl_alone(tmp_path: Path) -> None:
    (tmp_path / "dmac").mkdir()
    ls = tmp_path / "dmac" / "local_settings.py"
    ls.write_text("x"); ls.chmod(0o644)
    with patch("startup.steps.volumes.os.getxattr", return_value=b"acl"):
        p = volumes.ensure_secret_group_read(tmp_path, {"NEXTSEEK_HOST_GID": "1000"})
    assert len(p) == 1 and "ACL" in p[0] and oct(ls.stat().st_mode & 0o777) == "0o644"


def _answers(image_user: str, running: dict[str, list[str]]):
    """A fake docker: compose config, the app image's user, and per service the running containers' users."""
    cfg = json.dumps({"services": {"nextseek": {"image": IMG}}})

    def fake_run(cmd, **kw):
        out = ""
        if cmd[:3] == ["docker", "compose", "config"]:
            out = cfg
        elif cmd[:3] == ["docker", "image", "inspect"]:
            out = image_user + "\n"
        elif cmd[:3] == ["docker", "compose", "ps"]:
            out = "\n".join(f"{cmd[-1]}-{i}" for i in range(len(running.get(cmd[-1], []))))
        elif cmd[:2] == ["docker", "inspect"]:
            svc, i = cmd[-1].rsplit("-", 1)
            out = running[svc][int(i)] + "\n"
        return MagicMock(returncode=0, stdout=out, stderr="")
    return fake_run


def test_before_the_switchover_is_any_app_user_but_10001(tmp_path: Path) -> None:
    cases = [
        # old root image at the tag, old root containers running: the review's ""/"" case
        ("", {"web": [""], "nextseek": [""]}, True),
        ("0", {"nextseek": ["root"]}, True),
        ("10001:10001", {"web": [""], "nextseek": ["10001:10001"]}, True),   # one old web still up
        ("", {}, True),                                                    # stopped, old (or rolled-back) image
        ("10001:10001", {"web": ["10001:10001"], "nextseek": ["10001:10001"]}, False),
        ("10001:10001", {}, False),                                        # stopped mid-switchover: safe to fix
    ]
    for image_user, running, want in cases:
        with patch("startup.steps.volumes.subprocess.run", side_effect=_answers(image_user, running)):
            assert volumes.before_switchover(tmp_path, {}) is want, (image_user, running)
    # the old comparison read the old image at the tag before the app build and called ""/"" no change
    with patch("startup.steps.volumes.subprocess.run", side_effect=_answers("", {"web": [""], "nextseek": [""]})):
        assert volumes.app_user_change(tmp_path, {}) is False


def test_peer_images_must_run_as_the_app_uid(tmp_path: Path) -> None:
    cfg = json.dumps({"services": {"nextseek": {"image": IMG}, "cc-agent": {"image": "dmac-assistant:poc"},
                                   "nextseek-sidecar": {"image": "nextseek-ns-sidecar:latest"}}})
    ran: list[list[str]] = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        if cmd[:3] == ["docker", "compose", "config"]:
            return MagicMock(returncode=0, stdout=cfg, stderr="")
        return MagicMock(returncode=0, stdout="10001\n" if "dmac-assistant:poc" in cmd else "1001\n", stderr="")

    with patch("startup.steps.volumes.subprocess.run", side_effect=fake_run):
        bad = volumes.peers_not_on_app_uid(tmp_path, {})
    assert ["docker", "run", "--rm", "--pull", "never", "--network", "none", "--entrypoint", "id",
            "dmac-assistant:poc", "-u"] in ran
    assert len(bad) == 1 and "nextseek-sidecar" in bad[0] and "1001" in bad[0]


def test_a_volume_that_does_not_exist_yet_is_skipped_not_created(tmp_path: Path) -> None:
    """`docker run -v <name>:/v` would create it without compose's labels (prod has no nextseek-media,
    nextseek-throttle or batch_upload_state yet); the image's 10001 mount point owns it on its first mount."""
    cfg = _cfg(tmp_path)
    ran: list[list[str]] = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        if cmd[:3] == ["docker", "volume", "inspect"]:
            return MagicMock(returncode=0 if cmd[-1] != "p1-nextseek-media" else 1, stdout="", stderr="")
        return MagicMock(returncode=0, stdout=cfg if cmd[:3] == ["docker", "compose", "config"] else "0\n", stderr="")

    for check in (False, True):
        ran.clear()
        with patch("startup.steps.volumes.subprocess.run", side_effect=fake_run):
            counts = volumes.ensure_app_ownership(tmp_path, {"NEXTSEEK_HOST_GID": H}, check_only=check)
        assert "p1-nextseek-media" not in counts and len(counts) == 4
        assert not any("p1-nextseek-media:/v" in " ".join(c) for c in ran if c[:2] == ["docker", "run"])


def test_the_group_read_step_sets_only_group_bits() -> None:
    """g+r,g-wx,o-rwx: never widens the owner's bits (0400 stays owner read-only) and never adds group write."""
    import os
    import tempfile
    gid = os.getgid()
    for before, after in ((0o644, 0o640), (0o600, 0o640), (0o400, 0o440), (0o664, 0o640), (0o640, 0o640)):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "dmac").mkdir()
            ls = Path(d) / "dmac" / "local_settings.py"
            ls.write_text("x"); ls.chmod(before)
            assert volumes.ensure_secret_group_read(Path(d), {"NEXTSEEK_HOST_GID": str(gid)}) == []
            assert ls.stat().st_mode & 0o777 == after, (oct(before), oct(ls.stat().st_mode & 0o777))
