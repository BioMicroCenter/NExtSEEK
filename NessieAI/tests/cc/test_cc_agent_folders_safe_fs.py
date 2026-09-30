"""safe_fs: Django's file operations in folders an agent or the sidecar can write (step 1).

Every test plants a link (or a FIFO, or a folder) where the operation would otherwise follow it, and checks that
the canary in this test's own temp folder is untouched and unread.
"""
from __future__ import annotations

import errno
import os
import stat
import threading
from pathlib import Path

import pytest

from NessieAI.cc import safe_fs
from NessieAI.tests.cc.agent_folder_canary import Canary


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    # The registry of agent roots is process-wide; each test starts with none.
    monkeypatch.setattr(safe_fs, "_AGENT_ROOTS", {})


@pytest.fixture
def canary(tmp_path):
    return Canary(tmp_path)


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "agent"
    (r / "a" / "b").mkdir(parents=True)
    (r / "a" / "b" / "t.jsonl").write_bytes(b"one\n")
    return r


# -- open_dir ----------------------------------------------------------------------------------------------

def test_open_dir_walks_real_folders(root):
    fd = safe_fs.open_dir(root, ("a", "b"))
    try:
        assert sorted(os.listdir(fd)) == ["t.jsonl"]
    finally:
        os.close(fd)


def test_open_dir_refuses_a_link_at_any_step(root, canary):
    canary.link_dir(root / "a" / "c")
    with pytest.raises(safe_fs.UnsafePath):
        safe_fs.open_dir(root, ("a", "c"))
    with pytest.raises(safe_fs.UnsafePath):
        safe_fs.open_dir(root / "a" / "c")
    canary.assert_untouched()


def test_open_dir_refuses_a_file_where_a_folder_should_be(root):
    with pytest.raises(safe_fs.UnsafePath):
        safe_fs.open_dir(root, ("a", "b", "t.jsonl"))


def test_open_dir_create_makes_missing_steps_but_never_follows_one(root, canary):
    fd = safe_fs.open_dir(root, ("new", "deeper"), create=True)
    os.close(fd)
    assert (root / "new" / "deeper").is_dir()
    canary.link_dir(root / "planted")
    with pytest.raises(safe_fs.UnsafePath):
        safe_fs.open_dir(root, ("planted", "x"), create=True)
    assert not (canary.dir / "x").exists()
    canary.assert_untouched()


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "nul\x00"])
def test_open_dir_rejects_names_that_are_not_one_step(root, bad):
    with pytest.raises(safe_fs.UnsafePath):
        safe_fs.open_dir(root, (bad,))


def test_open_dir_missing_root_is_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        safe_fs.open_dir(tmp_path / "nope")


# -- trusted roots -----------------------------------------------------------------------------------------

def test_a_root_inside_a_registered_agent_root_is_refused(root, tmp_path):
    safe_fs.register_agent_root(root)
    calls = (
        lambda: safe_fs.open_dir(root / "a"),
        lambda: safe_fs.read_file(root / "a" / "b", "t.jsonl"),
        lambda: list(safe_fs.iter_files(root / "a")),
        lambda: safe_fs.write_file_atomic(root / "a", "x.txt", b"x"),
        lambda: safe_fs.copy_out(root / "a" / "b", "t.jsonl", tmp_path / "out" / "t.jsonl"),
    )
    for call in calls:
        with pytest.raises(safe_fs.UnsafePath):
            call()
    assert not (root / "a" / "x.txt").exists()
    assert not (tmp_path / "out").exists()


def test_a_registered_root_and_a_django_folder_above_it_still_work(root):
    safe_fs.register_agent_root(root)
    assert safe_fs.read_file(root, "a/b/t.jsonl") == b"one\n"
    assert [rel for rel, _st in safe_fs.iter_files(root, ("a", "b"))] == ["a/b/t.jsonl"]
    assert safe_fs.read_file(root.parent, "agent/a/b/t.jsonl") == b"one\n"


def test_a_link_that_used_to_sit_inside_a_root_is_now_caught(root, canary):
    canary.link_dir(root / "a" / "c")
    # Unregistered, the old call shape would trust the link as an earlier component of its root: the gap the
    # registry closes. (A test never pins reading through a link, so that step is not asserted.)
    safe_fs.register_agent_root(root)
    with pytest.raises(safe_fs.UnsafePath):
        safe_fs.read_file(root / "a" / "c" / "sub", "deep.jsonl")  # the old shape: refused as a root
    with pytest.raises(safe_fs.UnsafePath):
        safe_fs.read_file(root, "a/c/sub/deep.jsonl")  # the new shape: the link is a step in rel
    canary.assert_untouched()


def test_registering_refuses_a_root_inside_another_and_paths_that_are_not_plain(root):
    assert safe_fs.register_agent_root(root) == root
    assert safe_fs.register_agent_root(str(root) + "/") == root, "registering again is fine"
    with pytest.raises(safe_fs.UnsafePath):
        safe_fs.register_agent_root(root / "a")
    for bad in ("relative/dir", str(root) + "/../x", str(root) + "/./a", str(root) + "//a", "/nul\x00"):
        with pytest.raises(safe_fs.UnsafePath):
            safe_fs.register_agent_root(bad)
    assert list(safe_fs._AGENT_ROOTS) == [str(root)]


@pytest.mark.parametrize("bad", ["relative", "/tmp/../etc", "/tmp/./x", "/tmp//x"])
def test_a_root_must_be_absolute_and_plain(bad):
    with pytest.raises(safe_fs.UnsafePath):
        safe_fs.open_dir(bad)


def test_agent_root_of_finds_the_registered_root_at_or_above_a_path(root):
    assert safe_fs.agent_root_of(root / "a") is None
    safe_fs.register_agent_root(root)
    assert safe_fs.agent_root_of(root) == root
    assert safe_fs.agent_root_of(root / "a" / "b" / "t.jsonl") == root
    assert safe_fs.agent_root_of(root.parent) is None


def test_the_registry_is_bounded_and_a_root_registered_again_stays(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_fs, "_MAX_AGENT_ROOTS", 2)
    a, b, c = (tmp_path / name for name in ("a", "b", "c"))
    safe_fs.register_agent_root(a)
    safe_fs.register_agent_root(b)
    safe_fs.register_agent_root(a)
    safe_fs.register_agent_root(c)
    assert list(safe_fs._AGENT_ROOTS) == [str(a), str(c)]


def test_the_resolvers_register_the_mount_roots(tmp_path):
    from NessieAI.cc import cc_provision, cc_staging
    from NessieAI.cc.cc_config import CCPaths

    users = tmp_path / "users"
    dirs = cc_provision.build_user_dirs(CCPaths(users_volume="dmac-cc-users", user_root_mount=str(users)),
                                        "proj", "alice", session_id="sess", run_id="r1")
    staging = cc_staging.staging_root_for(str(users))
    assert set(safe_fs._AGENT_ROOTS) == {dirs.cc_state_mnt, dirs.run_scratch_mnt, str(staging)}
    for inside in (Path(dirs.cc_state_mnt) / "projects", Path(dirs.run_scratch_mnt) / "raw", staging / "abc"):
        with pytest.raises(safe_fs.UnsafePath):
            safe_fs.open_dir(inside)
    # Folders above them are Django's and stay usable as roots (the recovery sweep, the scrub command).
    assert safe_fs._check_root(Path(dirs.scratch_mnt)) == dirs.scratch_mnt
    assert safe_fs._check_root(users) == str(users)


# -- read_file ---------------------------------------------------------------------------------------------

def test_read_file_reads_a_regular_file(root):
    assert safe_fs.read_file(root, "a/b/t.jsonl") == b"one\n"
    assert safe_fs.read_file(root, Path("a") / "b" / "t.jsonl") == b"one\n"


def test_read_file_never_reads_through_a_link(root, canary):
    canary.link_file(root / "a" / "b" / "leak.jsonl")
    canary.link_dir(root / "a" / "linked")
    for rel in ("a/b/leak.jsonl", "a/linked/secret.jsonl"):
        with pytest.raises(safe_fs.UnsafePath):
            safe_fs.read_file(root, rel)
    canary.assert_untouched()


def test_read_file_refuses_a_fifo_without_waiting(root):
    os.mkfifo(root / "a" / "pipe.jsonl")
    outcome: list = []

    def read():
        try:
            safe_fs.read_file(root, "a/pipe.jsonl")
        except safe_fs.UnsafePath as exc:
            outcome.append(exc)

    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive(), "reading a FIFO must not wait for a writer"
    assert outcome and isinstance(outcome[0], safe_fs.UnsafePath)


def test_read_file_refuses_a_folder(root):
    with pytest.raises(safe_fs.UnsafePath):
        safe_fs.read_file(root, "a/b")


def test_read_file_caps_the_size(root):
    (root / "big.bin").write_bytes(b"x" * 100)
    assert safe_fs.read_file(root, "big.bin", max_bytes=100) == b"x" * 100
    with pytest.raises(OSError) as info:
        safe_fs.read_file(root, "big.bin", max_bytes=99)
    assert info.value.errno == errno.EFBIG
    assert not isinstance(info.value, safe_fs.UnsafePath)


@pytest.mark.parametrize("grown_to, cap, expect", [(10, 4, "EFBIG"), (3, 4, b"abc")])
def test_read_file_cap_is_exact_for_a_file_that_grows_after_fstat(root, monkeypatch, grown_to, cap, expect):
    """2 bytes at fstat, more by the read: past the cap is EFBIG, under it the whole file (never a prefix)."""
    (root / "g.bin").write_bytes(b"ab")
    real = safe_fs._open_for_read

    def open_then_grow(r, rel):
        fd, st = real(r, rel)
        (root / "g.bin").write_bytes(b"abcdefghij"[:grown_to])
        return fd, st

    monkeypatch.setattr(safe_fs, "_open_for_read", open_then_grow)
    if expect == "EFBIG":
        with pytest.raises(OSError) as info:
            safe_fs.read_file(root, "g.bin", max_bytes=cap)
        assert info.value.errno == errno.EFBIG
    else:
        assert safe_fs.read_file(root, "g.bin", max_bytes=cap) == expect


@pytest.mark.parametrize("bad", ["/etc/passwd", "../x", "a/../../x", "", "a//b"])
def test_read_file_rejects_paths_that_leave_the_root(root, bad):
    with pytest.raises(safe_fs.UnsafePath):
        safe_fs.read_file(root, bad)


def test_read_file_missing_is_file_not_found(root):
    with pytest.raises(FileNotFoundError):
        safe_fs.read_file(root, "a/b/none.jsonl")


# -- write_file_atomic -------------------------------------------------------------------------------------

def test_write_file_atomic_writes_with_the_exact_mode(root):
    old = os.umask(0o077)
    try:
        safe_fs.write_file_atomic(root, "a/b/t.jsonl", b"two\n", mode=0o666)
    finally:
        os.umask(old)
    path = root / "a" / "b" / "t.jsonl"
    assert path.read_bytes() == b"two\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o666
    assert sorted(p.name for p in path.parent.iterdir()) == ["t.jsonl"], "no temporary file is left"


def test_write_file_atomic_replaces_a_link_instead_of_writing_through_it(root, canary):
    target = canary.link_file(root / "a" / "CLAUDE.md")
    safe_fs.write_file_atomic(root, "a/CLAUDE.md", b"memory\n")
    assert not target.is_symlink() and target.read_bytes() == b"memory\n"
    canary.assert_untouched()


def test_write_file_atomic_never_writes_below_a_linked_folder(root, canary):
    canary.link_dir(root / "a" / "linked")
    with pytest.raises(safe_fs.UnsafePath):
        safe_fs.write_file_atomic(root, "a/linked/secret.jsonl", b"overwrite\n")
    canary.assert_untouched()


def test_write_file_atomic_skips_a_planted_temporary_name(root, canary, monkeypatch):
    tokens = iter(["planted00000000", "fresh000000000a"])
    monkeypatch.setattr(safe_fs.secrets, "token_hex", lambda n: next(tokens))
    canary.link_file(root / "a" / ".safe_fs.planted00000000.tmp")
    safe_fs.write_file_atomic(root, "a/out.txt", b"data\n")
    assert (root / "a" / "out.txt").read_bytes() == b"data\n"
    canary.assert_untouched()


def test_write_file_atomic_refuses_a_folder_at_the_name(root):
    with pytest.raises(safe_fs.UnsafePath):
        safe_fs.write_file_atomic(root, "a/b", b"x")
    assert (root / "a" / "b" / "t.jsonl").read_bytes() == b"one\n"
    assert [p.name for p in (root / "a").iterdir()] == ["b"], "the temporary file is removed"


# -- iter_files --------------------------------------------------------------------------------------------

def test_iter_files_lists_plain_files_in_name_order_and_skips_the_rest(root, canary):
    (root / "a" / "z.jsonl").write_bytes(b"z")
    (root / "a" / "note.txt").write_bytes(b"n")
    canary.link_file(root / "a" / "leak.jsonl")
    canary.link_dir(root / "a" / "linked")
    os.mkfifo(root / "a" / "pipe.jsonl")
    assert [rel for rel, _st in safe_fs.iter_files(root)] == ["a/note.txt", "a/z.jsonl", "a/b/t.jsonl"]
    assert [rel for rel, _st in safe_fs.iter_files(root, suffix=".jsonl")] == ["a/z.jsonl", "a/b/t.jsonl"]
    canary.assert_untouched()


def test_iter_files_walks_rel_parts_from_the_root_and_never_through_a_link(root, canary):
    assert [rel for rel, _st in safe_fs.iter_files(root, ("a", "b"), suffix=".jsonl")] == ["a/b/t.jsonl"]
    canary.link_dir(root / "a" / "c")
    with pytest.raises(safe_fs.UnsafePath):
        list(safe_fs.iter_files(root, ("a", "c")))
    with pytest.raises(FileNotFoundError):
        list(safe_fs.iter_files(root, ("a", "missing")))
    canary.assert_untouched()


def test_iter_files_gives_the_lstat(root):
    [(rel, st)] = list(safe_fs.iter_files(root / "a" / "b"))
    assert rel == "t.jsonl" and st.st_size == 4 and stat.S_ISREG(st.st_mode)


def test_iter_files_refuses_a_linked_root_and_reports_a_missing_one(tmp_path, canary):
    canary.link_dir(tmp_path / "projects")
    with pytest.raises(safe_fs.UnsafePath):
        list(safe_fs.iter_files(tmp_path / "projects"))
    with pytest.raises(FileNotFoundError):
        list(safe_fs.iter_files(tmp_path / "missing"))
    canary.assert_untouched()


def test_iter_files_stops_at_the_depth_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_fs, "_MAX_DEPTH", 2)
    deep = tmp_path / "r" / "1" / "2" / "3"
    deep.mkdir(parents=True)
    for folder in (tmp_path / "r" / "1", tmp_path / "r" / "1" / "2", deep):
        (folder / "f").write_bytes(b"x")
    assert [rel for rel, _st in safe_fs.iter_files(tmp_path / "r")] == ["1/f", "1/2/f"]


# -- copy_out ----------------------------------------------------------------------------------------------

def test_copy_out_copies_keeps_times_and_drops_the_mode(root, tmp_path):
    src = root / "a" / "b" / "t.jsonl"
    os.chmod(src, 0o755)
    os.utime(src, ns=(1_000_000_000, 2_000_000_000))
    dest = tmp_path / "out" / "deep" / "t.jsonl"
    safe_fs.copy_out(root, "a/b/t.jsonl", dest)
    assert dest.read_bytes() == b"one\n"
    assert dest.stat().st_mtime_ns == 2_000_000_000
    assert stat.S_IMODE(dest.stat().st_mode) & 0o7111 == 0, "the agent's mode bits are not copied"


def test_copy_out_never_reads_through_a_link(root, canary, tmp_path):
    canary.link_file(root / "a" / "leak.csv")
    canary.link_dir(root / "a" / "linked")
    for rel in ("a/leak.csv", "a/linked/secret.jsonl"):
        with pytest.raises(safe_fs.UnsafePath):
            safe_fs.copy_out(root, rel, tmp_path / "out" / "x")
    assert not (tmp_path / "out" / "x").exists()
    canary.assert_untouched()
    canary.assert_unread(tmp_path / "out")


@pytest.mark.parametrize("bad", ["projects", b"projects"])
def test_rel_parts_as_one_string_is_a_type_error(root, bad):
    with pytest.raises(TypeError):
        safe_fs.open_dir(root, bad)
    with pytest.raises(TypeError):
        next(safe_fs.iter_files(root, bad))
