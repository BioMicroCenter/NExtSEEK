"""The host seam behind run-harvest --bundle: it opens one file, by name, in the caller's own
chat input folder (where the chat's upload button saves files), and nothing else."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from nextseek_api.services import assistant as assistant_mod


@pytest.fixture
def own_input(monkeypatch, tmp_path):
    """The caller's input folder is <root>/<project>/<username>/input, as build_user_dirs makes it."""
    import NessieAI.cc.cc_provision as provision

    root = tmp_path / "users"
    mine = root / "proj-a" / "alice" / "input"
    mine.mkdir(parents=True)
    theirs = root / "proj-a" / "bob" / "input"
    theirs.mkdir(parents=True)
    (theirs / "secret.tar.gz").write_bytes(b"bob's")
    monkeypatch.setenv("DMAC_USER_ROOT_MOUNT", str(root))
    monkeypatch.setenv("DMAC_CC_USERS_VOLUME", "dmac-cc-users")
    monkeypatch.setattr(assistant_mod, "_request_login", lambda request: ("alice", "pw"))
    monkeypatch.setattr(provision, "resolve_user_project",
                        lambda user, password, **kw: SimpleNamespace(dirname="proj-a", id=1))
    return mine


def _open_bundle():
    from nextseek_api.services.assistant import AssistantViewSet

    request = SimpleNamespace(user=SimpleNamespace(pk=7, username="alice", is_superuser=True))
    return AssistantViewSet()._granular_op_session(request, None, "run-harvest").open_bundle


def test_it_opens_a_file_in_the_callers_own_input_folder(own_input):
    (own_input / "sarek.tar.gz").write_bytes(b"mine")
    with _open_bundle()("sarek.tar.gz") as fh:
        assert fh.read() == b"mine"


@pytest.mark.parametrize("name", ["../../bob/input/secret.tar.gz", "sub/x.tar.gz", "/etc/passwd", "", ".."])
def test_a_name_that_is_not_one_file_name_is_refused(own_input, name):
    with pytest.raises((FileNotFoundError, ValueError)):
        _open_bundle()(name)


def test_a_symlink_in_the_input_folder_is_not_followed(own_input, tmp_path):
    target = tmp_path / "elsewhere.tar.gz"
    target.write_bytes(b"outside")
    os.symlink(target, own_input / "link.tar.gz")
    with pytest.raises(OSError):
        _open_bundle()("link.tar.gz")


def test_a_folder_is_not_a_bundle(own_input):
    (own_input / "folder.tar.gz").mkdir()
    with pytest.raises(OSError):
        _open_bundle()("folder.tar.gz")


def test_a_missing_file_is_not_found(own_input):
    with pytest.raises(FileNotFoundError):
        _open_bundle()("absent.tar.gz")
