"""scrub_stored_sample_properties never reads or rewrites a CC file through a link (step 1).

Its cc_transcript_files store rewrites transcripts under <project>/<user>/cc-state/<session>/, the agent's own
folder. A folder there that is a link must not redirect the read, the rewrite or the listing.
"""
from __future__ import annotations

import os
import stat

import pytest

from NessieAI.cc import safe_fs
from nextseek_api.management.commands import scrub_stored_sample_properties as cmd
from NessieAI.tests.cc.agent_folder_canary import Canary


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    monkeypatch.setattr(safe_fs, "_AGENT_ROOTS", {})


@pytest.fixture
def cc_root(tmp_path, monkeypatch):
    root = tmp_path / "users"
    (root / "2-proj" / "member" / "cc-state" / "sess" / "projects").mkdir(parents=True)
    monkeypatch.setenv("DMAC_USER_ROOT_MOUNT", str(root))
    return root


def _projects(cc_root):
    return cc_root / "2-proj" / "member" / "cc-state" / "sess" / "projects"


def test_reading_a_cc_file_never_goes_through_a_linked_folder(cc_root, tmp_path):
    canary = Canary(tmp_path)
    canary.link_dir(_projects(cc_root) / "-home-user")
    read = cmd._read_regular(_projects(cc_root) / "-home-user" / "secret.jsonl")
    assert isinstance(read, str), f"a file behind a linked folder must not be read: {read!r}"
    canary.assert_untouched()


def test_rewriting_a_cc_file_never_goes_through_a_linked_folder(cc_root, tmp_path):
    canary = Canary(tmp_path)
    canary.link_dir(_projects(cc_root) / "-home-user")
    st = os.lstat(canary.file)
    with pytest.raises(OSError):
        cmd._replace_file(_projects(cc_root) / "-home-user" / "secret.jsonl", b"{}\n", mode=st.st_mode,
                          uid=st.st_uid, gid=st.st_gid, atime_ns=st.st_atime_ns, mtime_ns=st.st_mtime_ns)
    canary.assert_untouched()


def test_a_cc_file_is_still_rewritten_with_its_mode_owner_and_times(cc_root):
    path = _projects(cc_root) / "-home-user" / "t.jsonl"
    path.parent.mkdir()
    path.write_bytes(b'{"a":1}\n')
    os.chmod(path, 0o640)
    os.utime(path, ns=(1_000_000_000, 2_000_000_000))
    st = os.lstat(path)
    cmd._replace_file(path, b'{"b":2}\n', mode=st.st_mode, uid=st.st_uid, gid=st.st_gid,
                      atime_ns=st.st_atime_ns, mtime_ns=st.st_mtime_ns)
    after = os.lstat(path)
    assert path.read_bytes() == b'{"b":2}\n'
    assert (stat.S_IMODE(after.st_mode), after.st_uid, after.st_gid, after.st_mtime_ns) == (
        0o640, st.st_uid, st.st_gid, 2_000_000_000)
    assert sorted(p.name for p in path.parent.iterdir()) == ["t.jsonl"]


def test_listing_a_cc_tree_never_enters_a_linked_folder(cc_root, tmp_path):
    canary = Canary(tmp_path)
    canary.link_dir(_projects(cc_root) / "-home-user")
    (_projects(cc_root).parent / "settings.json").write_text("{}")
    run = cmd.Run(include_admins=False, limit=None, batch_size=10, max_file_bytes=1 << 20,
                  min_transcript_age_s=0, admin_ids=frozenset(), admin_usernames=frozenset())
    listed = [rid for _path, _admin, rid in cmd._cc_files(cmd._agent_session_dirs)(run, cmd.Tally())]
    assert listed == ["2-proj/member/cc-state/sess/settings.json"]
    canary.assert_untouched()
