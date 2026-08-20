"""#68 part 2 — the per-turn transcript slice primitives.

``--resume`` appends EVERY turn of a chat to ONE session ``.jsonl``, and
``run_cc_turn`` reads that whole file into a per-turn ``CCSessionTranscript``
row: row N holds turns 1..N, so the stored bytes grow quadratically in turn
count. ``_jsonl_line_count`` / ``_turn_slice`` / ``_transcript_line_counts``
are the three pure functions that let a caller snapshot the store's line counts
BEFORE spawn and afterwards keep only the records this turn appended.

The boundary is a LINE INDEX, not a byte offset, and
``test_slice_survives_a_scrub_that_a_byte_offset_would_not`` is the test that
pins that decision: ``_scrub_secret_bytes`` (#72) replaces secrets with
``b"<REDACTED>"``, which carries no newline, so the scrub is line-count
preserving but NOT length preserving. Any byte offset recorded before the turn
is invalidated by the scrub of the bytes ahead of it.

``_read_turn_transcript`` is the capture helper built on those three, and the
AST tests at the bottom pin which of its two members reaches which sink in
``run_cc_turn``: the per-turn sinks get the SLICE, the Debug-panel trace keeps
the FULL session.

Hermetic: tmp_path + stdlib only, no docker, no DB.
"""

import ast
import os
import time as _real_time
from pathlib import Path

import pytest

from nextseek_api.cc_assistant import cc_engine, cc_summary

PW = "hunter2-s3cr3t"
ENV = {"NEXTSEEK_PASSWORD": PW}


def _jsonl(*payloads: str) -> bytes:
    """A well-formed jsonl blob (one record per line, trailing newline)."""
    return b"".join(
        ('{"type":"user","n":%d,"content":"%s"}\n' % (i, p)).encode()
        for i, p in enumerate(payloads)
    )


# --------------------------------------------------------------------------
# _jsonl_line_count — must agree with cc_summary.parse_transcript
# --------------------------------------------------------------------------

def test_line_count_empty_input_is_zero():
    assert cc_engine._jsonl_line_count(b"") == 0


def test_line_count_agrees_with_parse_transcript():
    """The guard against the two line-splitting conventions drifting apart.

    ``parse_transcript`` is the codebase's established convention (split on
    ``b"\\n"``, drop ONE trailing empty element, keep interior blanks). If the
    two ever disagree, a slice boundary recorded by one and applied by the
    other is off by a record.
    """
    cases = [
        b"",                                    # empty
        b'{"a":1}',                             # no trailing newline
        b'{"a":1}\n',                           # trailing newline
        b'{"a":1}\n{"b":2}\n',                  # two records
        b'{"a":1}\n{"b":2}',                    # two, no trailing newline
        b'{"a":1}\n\n{"b":2}\n',                # interior blank line is COUNTED
        b'{"a":1}\nnot json\n',                 # malformed line is COUNTED
        b'{"a":1}\n\n',                         # trailing blank + newline: one dropped
        _jsonl("x", "y", "z"),
    ]
    for raw in cases:
        assert cc_engine._jsonl_line_count(raw) == \
            cc_summary.parse_transcript(raw).line_count, raw


# --------------------------------------------------------------------------
# _turn_slice
# --------------------------------------------------------------------------

def test_turn_slice_prior_zero_returns_input_unchanged():
    raw = _jsonl("a", "b", "c")
    assert cc_engine._turn_slice(raw, 0) == raw
    assert cc_engine._turn_slice(raw, -3) == raw


def test_turn_slice_drops_exactly_the_first_n_lines():
    raw = _jsonl("a", "b", "c", "d")
    sliced = cc_engine._turn_slice(raw, 2)
    assert cc_engine._jsonl_line_count(sliced) == 2
    parsed = cc_summary.parse_transcript(sliced)
    # still valid jsonl: every line parsed to a real record, not "_type": "unparsed"
    assert [r.get("n") for r in parsed.records] == [2, 3]
    assert b'"n":0' not in sliced and b'"n":1' not in sliced
    assert sliced.endswith(b"\n")


def test_turn_slice_returns_full_input_when_prior_ge_line_count():
    """The shrink / rewrite / no-op guard.

    Returning the FULL input (not ``b""``) is deliberate and load-bearing: it
    gives the invariant "the slice is empty only if the input is empty", which
    is what stops a later caller from writing a transcript row with an empty
    blob when the file was rewritten, truncated, or the turn appended nothing.
    """
    raw = _jsonl("a", "b")
    assert cc_engine._turn_slice(raw, 2) == raw          # exactly equal
    assert cc_engine._turn_slice(raw, 5) == raw          # recorded count too high
    assert cc_engine._turn_slice(b"", 3) == b""          # empty in, empty out


def test_turn_slice_is_length_monotone():
    """Slicing at 1 then re-slicing the result at 0 is a no-op."""
    raw = _jsonl("a", "b", "c")
    once = cc_engine._turn_slice(raw, 1)
    assert cc_engine._turn_slice(once, 0) == once
    assert len(once) < len(raw)
    assert cc_engine._jsonl_line_count(once) == 2


def test_turn_slice_round_trips_with_its_prefix():
    """``prior + turn`` reconstructs the original bytes exactly."""
    raw = _jsonl("a", "b", "c", "d", "e")
    lines = raw.split(b"\n")[:-1]
    for n in range(1, len(lines)):
        prior = b"\n".join(lines[:n]) + b"\n"
        turn = cc_engine._turn_slice(raw, n)
        assert prior + turn == raw, n


def test_turn_slice_normalises_a_missing_trailing_newline():
    """A killed agent can leave the final line truncated (the whole point of #68
    is the turns that did NOT succeed). The slice is persisted as a standalone
    jsonl blob, so it ends in a newline even when the source did not — while the
    unsliced fallback still returns the source bytes byte-for-byte."""
    raw = b'{"n":0}\n{"n":1}'
    assert cc_engine._turn_slice(raw, 1) == b'{"n":1}\n'
    assert cc_engine._turn_slice(raw, 0) == raw       # fallback: untouched
    assert cc_engine._turn_slice(raw, 9) == raw       # fallback: untouched


def test_slice_survives_a_scrub_that_a_byte_offset_would_not():
    """THE test that pins the line-vs-byte decision (#72 x #68).

    Turn 1 leaked the password; turn 2 did not. A caller recording a BYTE
    offset at the end of turn 1 would have recorded it against the DIRTY bytes,
    but ``_scrub_secret_bytes`` rewrites turn 1's line to ``<REDACTED>`` — a
    different length — so that offset no longer lands on a record boundary. The
    LINE index is unaffected because ``<REDACTED>`` carries no newline.
    """
    turn1 = ('{"type":"user","content":"NEXTSEEK_PASSWORD=%s"}\n' % PW).encode()
    turn2 = b'{"type":"user","content":"second turn, clean"}\n'
    dirty = turn1 + turn2

    prior_lines = cc_engine._jsonl_line_count(turn1)      # snapshot BEFORE the turn
    prior_bytes = len(turn1)                              # what a byte offset would be
    assert prior_lines == 1

    clean = cc_engine._scrub_secret_bytes(dirty, ENV)
    assert PW.encode() not in clean
    assert b"<REDACTED>" in clean
    # the scrub is line-count preserving but NOT length preserving
    assert cc_engine._jsonl_line_count(clean) == cc_engine._jsonl_line_count(dirty)
    assert len(clean) != len(dirty)

    sliced = cc_engine._turn_slice(clean, prior_lines)
    assert sliced == turn2
    assert PW.encode() not in sliced
    assert b"<REDACTED>" not in sliced          # turn 1 is gone entirely

    # and the byte offset would have sliced mid-record instead
    assert clean[prior_bytes:] != turn2


# --------------------------------------------------------------------------
# _transcript_line_counts
# --------------------------------------------------------------------------

def test_transcript_line_counts_none_and_missing_dir(tmp_path):
    assert cc_engine._transcript_line_counts(None) == {}
    assert cc_engine._transcript_line_counts("") == {}
    assert cc_engine._transcript_line_counts(tmp_path / "nope") == {}
    # a FILE where a dir was expected is also not fatal
    lone = tmp_path / "a.jsonl"
    lone.write_bytes(_jsonl("x"))
    assert cc_engine._transcript_line_counts(lone) == {}


def test_transcript_line_counts_walks_a_nested_store(tmp_path):
    a = tmp_path / "-data-projects-alpha" / "sess-a.jsonl"
    b = tmp_path / "-data-projects-beta" / "deep" / "sess-b.jsonl"
    for p in (a, b):
        p.parent.mkdir(parents=True, exist_ok=True)
    a.write_bytes(_jsonl("1", "2", "3"))
    b.write_bytes(_jsonl("1"))
    (tmp_path / "-data-projects-alpha" / "notes.txt").write_text("ignored")

    counts = cc_engine._transcript_line_counts(tmp_path)
    assert counts == {str(a): 3, str(b): 1}
    # accepts a str root as well as a Path
    assert cc_engine._transcript_line_counts(str(tmp_path)) == counts


def test_transcript_line_counts_skips_symlinks(tmp_path):
    real = tmp_path / "real.jsonl"
    real.write_bytes(_jsonl("1", "2"))
    link = tmp_path / "link.jsonl"
    link.symlink_to(real)
    assert cc_engine._transcript_line_counts(tmp_path) == {str(real): 2}


def test_transcript_line_counts_skips_an_unreadable_file(tmp_path, monkeypatch):
    """It runs on the turn's hot path; one bad file must not raise.

    Injected at the OS boundary, NOT with ``chmod 0o000``. The only lane this
    suite runs in is the container, which runs as euid 0, and root reads a
    0o000 file happily — so a permissions fixture here demonstrates nothing:
    it passes just as well against a ``_transcript_line_counts`` with no
    ``except OSError`` in it at all. Raising from ``Path.read_bytes`` for the
    one file exercises the handler itself, and the assertion is exact (the good
    file counted, the bad one absent) rather than a set containment that both
    outcomes satisfy.

    THE WALK ORDER IS PINNED, and that is the point of the ``rglob`` patch.
    This test has to distinguish the INNER per-file handler from the OUTER
    whole-walk one, and only the inner one skips-and-continues. Delete the
    inner handler alone and the outer returns the counts gathered so far — which
    equals ``{good: 1}`` precisely when the walk reached ``good.jsonl`` first.
    Real ``rglob`` yields in ``os.scandir`` order, which is neither creation
    nor sort order, so an unpinned version of this test passes or fails on
    which name the filesystem happens to hand back first. Yielding ``bad``
    first makes the inner-handler-only mutant return ``{}`` every time.
    """
    good = tmp_path / "good.jsonl"
    good.write_bytes(_jsonl("1"))
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(_jsonl("1", "2"))

    monkeypatch.setattr(Path, "rglob", lambda self, pattern: iter([bad, good]))

    real_read_bytes = Path.read_bytes

    def _selective(self, *args, **kwargs):
        if self == bad:
            raise PermissionError(13, "Permission denied", str(bad))
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", _selective)

    counts = cc_engine._transcript_line_counts(tmp_path)

    assert counts == {str(good): 1}, (
        "the readable file must still be counted and the unreadable one skipped"
    )


def test_transcript_line_counts_survives_an_unreadable_ROOT(tmp_path, monkeypatch):
    """``Path.is_dir()`` swallows ENOENT/ENOTDIR/ELOOP but RE-RAISES EACCES, so
    a root whose parent directory cannot be traversed used to escape past the
    ``{}`` guard entirely — contradicting the docstring's "must never be the
    reason a turn fails". It runs before the agent is even spawned, so an
    escape here kills the turn before it starts."""
    def _denied(self):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "is_dir", _denied)

    assert cc_engine._transcript_line_counts(tmp_path) == {}


# --------------------------------------------------------------------------
# _read_turn_transcript — locate + read + scrub + slice, in one place
# --------------------------------------------------------------------------


class _FakeTime:
    """Stands in for the ``time`` module inside ``cc_engine`` so the retry
    loop's back-off is observable and free. Only ``sleep``/``time`` are used on
    this code path."""

    def __init__(self):
        self.sleeps: list[float] = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def time(self):
        return _real_time.time()


def _store(tmp_path, *, body=b"", mtime=None, name="sess.jsonl"):
    """Build a cc-state store and return ``(cc_state_mnt, session_path)``.

    Mirrors the real layout: the session jsonl lives under
    ``<cc_state_mnt>/projects/<mangled-cwd>/<session-uuid>.jsonl``.
    """
    cc_state = tmp_path / "cc-state"
    path = cc_state / "projects" / "-data-projects-proj" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return str(cc_state), path


def test_read_turn_transcript_without_a_cc_state_mount_is_empty():
    for falsy in (None, ""):
        assert cc_engine._read_turn_transcript(
            falsy, turn_start=10_000.0, prior_lines={}, environment=ENV
        ) == cc_engine.CapturedTranscript(b"", b"")


def test_read_turn_transcript_missing_projects_dir_returns_without_sleeping(
    tmp_path, monkeypatch
):
    """Turn 1 of a chat has no store yet. Sleeping 3 x 0.2s for a directory that
    does not exist is pure latency on the reply."""
    fake = _FakeTime()
    monkeypatch.setattr(cc_engine, "time", fake)

    got = cc_engine._read_turn_transcript(
        str(tmp_path / "cc-state"), turn_start=10_000.0, prior_lines={}, environment=ENV
    )

    assert got == cc_engine.CapturedTranscript(b"", b"")
    assert fake.sleeps == []


def test_read_turn_transcript_ignores_a_session_older_than_this_turn(
    tmp_path, monkeypatch
):
    """A store holding only PREVIOUS turns' files must read as "nothing yet",
    not as this turn's transcript."""
    fake = _FakeTime()
    monkeypatch.setattr(cc_engine, "time", fake)
    turn_start = 10_000.0
    mnt, _ = _store(tmp_path, body=_jsonl("stale"), mtime=turn_start - 5)

    got = cc_engine._read_turn_transcript(
        mnt, turn_start=turn_start, prior_lines={}, environment=ENV
    )

    assert got == cc_engine.CapturedTranscript(b"", b"")
    # retried three times, and did NOT sleep after the LAST attempt
    assert fake.sleeps == [0.2, 0.2]


def test_read_turn_transcript_never_sleeps_after_its_final_attempt(
    tmp_path, monkeypatch
):
    fake = _FakeTime()
    monkeypatch.setattr(cc_engine, "time", fake)
    turn_start = 10_000.0
    mnt, _ = _store(tmp_path, body=_jsonl("stale"), mtime=turn_start - 5)

    cc_engine._read_turn_transcript(
        mnt, turn_start=turn_start, prior_lines={}, environment=ENV, attempts=1
    )

    assert fake.sleeps == []


def test_read_turn_transcript_slices_a_resumed_session(tmp_path):
    """The #68 headline. ``--resume`` appends turn 2 to turn 1's file; the row
    for turn 2 must hold turn 2, not turns 1..2.

    ``prior_lines`` is built here by ``_transcript_line_counts`` off the SAME
    root expression the helper resolves internally — that agreement is what
    makes the lookup hit. A root spelled differently would miss every key and
    silently degrade back to storing the whole cumulative session.
    """
    turn_start = 10_000.0
    prior = _jsonl("turn-1 a", "turn-1 b")
    mnt, path = _store(tmp_path, body=prior, mtime=turn_start - 50)

    pre = cc_engine._transcript_line_counts(Path(mnt) / "projects")
    assert pre == {str(path): 2}

    new = b'{"type":"user","n":9,"content":"turn-2 only"}\n'
    path.write_bytes(prior + new)
    os.utime(path, (turn_start + 1, turn_start + 1))

    got = cc_engine._read_turn_transcript(
        mnt, turn_start=turn_start, prior_lines=pre, environment=ENV
    )

    assert got.session == prior + new       # the trace's input: everything
    assert got.turn == new                  # the row's input: this turn only
    assert cc_engine._jsonl_line_count(got.turn) == 1


def test_read_turn_transcript_scrubs_both_members(tmp_path):
    """#72 x #68: the scrub runs ONCE over the whole file and the slice is taken
    from the scrubbed bytes, so neither member can carry the password. Scrubbing
    after slicing would be a second full pass for the same result."""
    turn_start = 10_000.0
    prior = ('{"n":0,"content":"NEXTSEEK_PASSWORD=%s"}\n' % PW).encode()
    new = ('{"n":1,"content":"leaked again: %s"}\n' % PW).encode()
    mnt, path = _store(tmp_path, body=prior + new, mtime=turn_start + 1)

    got = cc_engine._read_turn_transcript(
        mnt, turn_start=turn_start, prior_lines={str(path): 1}, environment=ENV
    )

    assert PW.encode() not in got.session
    assert PW.encode() not in got.turn
    assert got.session.count(b"<REDACTED>") == 2
    assert got.turn.count(b"<REDACTED>") == 1
    assert b'"n":0' not in got.turn


def test_read_turn_transcript_degrades_to_the_whole_session_on_a_key_miss(tmp_path):
    """A file the pre-spawn snapshot never saw reads as ``prior_lines = 0``.
    Storing too much beats storing nothing."""
    turn_start = 10_000.0
    body = _jsonl("a", "b", "c")
    mnt, _ = _store(tmp_path, body=body, mtime=turn_start + 1)

    got = cc_engine._read_turn_transcript(
        mnt, turn_start=turn_start,
        prior_lines={"/some/other/session.jsonl": 2}, environment=ENV,
    )

    assert got.session == body
    assert got.turn == body


def test_read_turn_transcript_turn_is_empty_only_if_the_session_is(tmp_path):
    """``_turn_slice``'s invariant, asserted at the caller's boundary — this is
    what stops a transcript row being written with an empty blob when the file
    was rewritten, truncated, or the turn appended nothing."""
    turn_start = 10_000.0
    body = _jsonl("a", "b")
    mnt, path = _store(tmp_path, body=body, mtime=turn_start + 1)

    for stale in (2, 5, 99):
        got = cc_engine._read_turn_transcript(
            mnt, turn_start=turn_start, prior_lines={str(path): stale}, environment=ENV
        )
        assert got.session == body
        assert got.turn == body, stale


def test_read_turn_transcript_swallows_an_unreadable_session(tmp_path, monkeypatch):
    """It is called from the reply path (and, from Task 4, from the finally):
    a read error must degrade to "no transcript", never kill the turn."""
    turn_start = 10_000.0
    mnt, _ = _store(tmp_path, body=_jsonl("a"), mtime=turn_start + 1)

    def _boom(self, *args, **kwargs):
        raise OSError("vanished mid-turn")

    monkeypatch.setattr(Path, "read_bytes", _boom)

    got = cc_engine._read_turn_transcript(
        mnt, turn_start=turn_start, prior_lines={}, environment=ENV
    )

    assert got == cc_engine.CapturedTranscript(b"", b"")


def test_turn_is_attributable_marks_the_whole_file_recovery(tmp_path):
    """``_turn_slice``'s "storing too much beats storing nothing" fallback is
    right for a turn that COMPLETED and wrong for one that FAILED, where "the
    agent appended nothing" is an expected state and the recovered bytes are the
    PRIOR turns'. The helper cannot pick for both callers, so it reports which
    case this is and each caller decides. ``turn`` itself is unchanged.
    """
    turn_start = 10_000.0
    body = _jsonl("a", "b")
    mnt, path = _store(tmp_path, body=body, mtime=turn_start + 1)

    def _read(prior):
        return cc_engine._read_turn_transcript(
            mnt, turn_start=turn_start, prior_lines=prior, environment=ENV)

    # grew this turn -> a genuine slice
    grew = _read({str(path): 1})
    assert grew.turn_is_attributable is True
    assert grew.turn == _jsonl("a", "b").split(b"\n")[1] + b"\n"

    # did NOT grow -> the whole file came back, and it is NOT this turn's
    for stale in (2, 5, 99):
        got = _read({str(path): stale})
        assert got.turn == body, stale          # unchanged recovery behaviour
        assert got.turn_is_attributable is False, stale

    # a fresh session -- the snapshot saw no records for this file -- IS this
    # turn's, whether by an explicit 0 or by a key the snapshot never held.
    assert _read({str(path): 0}).turn_is_attributable is True
    assert _read({}).turn_is_attributable is True
    assert _read({"/some/other/session.jsonl": 2}).turn_is_attributable is True


def test_an_empty_capture_keeps_its_two_field_spelling():
    """The default keeps ``CapturedTranscript(b"", b"")`` a valid and EQUAL
    spelling of "nothing captured" — every early return in the helper, and every
    test that compares against it, relies on that."""
    assert cc_engine.CapturedTranscript(b"", b"") == \
        cc_engine.CapturedTranscript(b"", b"", True)
    assert cc_engine.CapturedTranscript(b"", b"").turn_is_attributable is True


def test_read_turn_transcript_swallows_an_unreadable_PROJECTS_ROOT(
    tmp_path, monkeypatch
):
    """The same EACCES hole ``_transcript_line_counts`` had, one function away.

    ``Path.is_dir()`` swallows ENOENT/ENOTDIR/ELOOP but RE-RAISES EACCES, and
    the probe sat ahead of the ``try`` — so an unreadable parent directory
    escaped a function whose docstring promises to return
    ``CapturedTranscript(b"", b"")`` rather than raise. Contained in practice,
    since both callers wrap it, but on the SUCCESS path the escape lands in the
    persist ``try`` and costs the user a reply they had already earned.
    """
    turn_start = 10_000.0
    mnt, _ = _store(tmp_path, body=_jsonl("a"), mtime=turn_start + 1)

    def _denied(self):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "is_dir", _denied)

    got = cc_engine._read_turn_transcript(
        mnt, turn_start=turn_start, prior_lines={}, environment=ENV
    )

    assert got == cc_engine.CapturedTranscript(b"", b"")


def test_read_turn_transcript_swallows_a_failure_in_the_LOCATE_step(
    tmp_path, monkeypatch
):
    """Not just the read (#68 Task 4). ``_newest_jsonl_under`` walks the store
    with ``rglob`` and then calls ``p.stat()`` twice per candidate, none of it
    under a handler of its own — so a transcript unlinked between the walk and
    the stat (the agent's own rotation, a concurrent sweep, a sibling turn)
    raises ``OSError`` out of the SEARCH rather than out of the read.

    This helper is called from ``run_cc_turn``'s ``finally``, where an escape
    would skip the #72/#76 transcript scrub that follows it, so the guard has to
    cover the whole locate-and-read, not only the last step of it.
    """
    turn_start = 10_000.0
    mnt, _ = _store(tmp_path, body=_jsonl("a"), mtime=turn_start + 1)

    def _vanished(self, *args, **kwargs):
        raise OSError("transcript unlinked mid-scan")

    monkeypatch.setattr(Path, "rglob", _vanished)

    got = cc_engine._read_turn_transcript(
        mnt, turn_start=turn_start, prior_lines={}, environment=ENV
    )

    assert got == cc_engine.CapturedTranscript(b"", b"")


# --------------------------------------------------------------------------
# _write_raw_turn_copy — the one raw/ writer both terminal paths share
# --------------------------------------------------------------------------


def test_write_raw_turn_copy_writes_under_raw_and_creates_the_dir(tmp_path):
    out = cc_engine._write_raw_turn_copy(tmp_path / "output", "aa11", b'{"n":1}\n')

    assert out == tmp_path / "output" / "raw" / "transcript-aa11.jsonl"
    assert out.read_bytes() == b'{"n":1}\n'


def test_write_raw_turn_copy_rejects_a_run_id_that_would_escape_raw(tmp_path):
    """Why the basename check alone is vestigial, demonstrated.

    ``Path.name`` is a single component by construction and this one always
    ends ``.jsonl``, so ``_safe_relpath`` on it can never fail — yet a
    ``run_id`` of ``a/b`` yields the perfectly "safe" basename
    ``transcript-b.jsonl`` while landing the file in ``<output_mnt>/raw/a/``.
    The parent check is the one that fires.

    Unreachable through ``run_cc_turn``, which calls ``_validate_user_id(run_id)``
    before any of this and rejects ``/`` outright. Defence in depth, so it is
    tested where it lives rather than through the engine.
    """
    with pytest.raises(ValueError):
        cc_engine._write_raw_turn_copy(tmp_path / "output", "a/b", b"x")

    assert not (tmp_path / "output").exists(), (
        "validate-then-mkdir: a path about to be rejected must not leave a "
        "directory behind"
    )


# --------------------------------------------------------------------------
# run_cc_turn wiring — which captured member reaches which sink
# --------------------------------------------------------------------------


def _cc_engine_ast():
    return ast.parse(Path(cc_engine.__file__).with_suffix(".py").read_text())


def _run_cc_turn_ast(tree=None):
    """``run_cc_turn``'s node. Pass ``tree`` when the caller also needs
    module-scope nodes: node identity is only comparable within ONE parse."""
    tree = _cc_engine_ast() if tree is None else tree
    return next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "run_cc_turn"
    )


def _calls(node, *, name=None, attr=None):
    """Every ``ast.Call`` under ``node`` to a bare name or to an ``x.attr``."""
    out = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if name is not None and isinstance(func, ast.Name) and func.id == name:
            out.append(child)
        elif attr is not None and isinstance(func, ast.Attribute) and func.attr == attr:
            out.append(child)
    return out


def _arg_src(call, index: int) -> str:
    """``ast.unparse`` of positional arg ``index``, or a readable sentinel.

    Guarded rather than indexed. A call rewritten with fewer positional
    arguments than an assertion below expects should make that assertion FAIL
    with its own message; indexing raises ``IndexError`` out of the test body
    instead, which turns a real regression into an unreadable error.
    """
    if index >= len(call.args):
        return f"<no positional arg {index}>"
    return ast.unparse(call.args[index])


# ``_write_raw_turn_copy(output_mnt, run_id, payload)`` — the one helper both
# terminal paths use for the ``raw/transcript-<run_id>.jsonl`` copy.
_RAW_COPY = "_write_raw_turn_copy"
_PAYLOAD_ARG = 2


def test_the_debug_trace_still_parses_the_FULL_session():
    """Deliberate, and pinned so a later refactor cannot silently swap the two
    members. Slicing the summariser's input would change every Debug-panel
    trace's ``steps``, ``transcript_line_count`` and ``turn_count`` — a separate
    defect with its own blast radius, out of scope for #68 (see SPEC.md)."""
    calls = _calls(_run_cc_turn_ast(), attr="parse_transcript")

    assert len(calls) == 1, "expected exactly one parse_transcript call"
    assert _arg_src(calls[0], 0) == "captured.session"


def test_the_SUCCESS_path_raw_copy_gets_that_turns_slice():
    """Bound to the ``if captured.turn:`` body, not to the function at large.

    There are TWO ``raw/`` copies in ``run_cc_turn`` now — the success path's
    and the #68 failure fallback's — so a bare "``captured.turn`` appears
    somewhere among the payloads" is satisfied by either one, and the success
    path could be swapped to ``captured.session`` with no assertion noticing.
    This one names the branch it is about.
    """
    fn = _run_cc_turn_ast()

    gates = [n for n in ast.walk(fn)
             if isinstance(n, ast.If) and ast.unparse(n.test) == "captured.turn"]
    assert len(gates) == 1, "the success path's one `if captured.turn:` gate"

    calls = [c for stmt in gates[0].body for c in _calls(stmt, name=_RAW_COPY)]
    assert len(calls) == 1, (
        f"expected exactly one {_RAW_COPY} call on the success path, got {len(calls)}"
    )
    assert _arg_src(calls[0], _PAYLOAD_ARG) == "captured.turn", (
        "the success path's raw/ copy is named for run_id, so it must hold that "
        f"run's records: got {_arg_src(calls[0], _PAYLOAD_ARG)}"
    )


def test_no_per_turn_sink_is_handed_the_WHOLE_session():
    """The path-agnostic half. The ``raw/transcript-<run_id>.jsonl`` copy and
    the ``CCSessionTranscript`` blob are both keyed per TURN, so whatever writes
    them must be handed a ``.turn``, never a ``.session``.

    Stated over the call sites rather than as an exact list of source strings:
    a third terminal path growing its own capture under its own local name is
    fine, as long as it too writes the slice.

    The ``write_bytes`` assertion below is KNOWINGLY over-broad: it bans every
    ``write_bytes`` in ``run_cc_turn``, not only a transcript one, so an
    unrelated future write would trip it and read the failure message as being
    about transcripts. Accepted — narrowing it means guessing at the target,
    and ``run_cc_turn`` has no other ``write_bytes`` today.
    """
    fn = _run_cc_turn_ast()

    copies = _calls(fn, name=_RAW_COPY)
    assert len(copies) == 2, (
        "the success path and the #68 failure fallback, both through the one "
        f"helper that carries the basename check and the mkdir; got {len(copies)}"
    )
    payloads = [_arg_src(c, _PAYLOAD_ARG) for c in copies]
    assert not [p for p in payloads if p.endswith(".session")], payloads
    assert all(p.endswith(".turn") for p in payloads), payloads

    direct = [_arg_src(c, 0) for c in _calls(fn, attr="write_bytes")]
    assert direct == [], (
        f"a raw/ transcript copy must go through {_RAW_COPY} — that is where "
        f"the basename check and the mkdir live, and inlining it again is how "
        f"the two paths drifted apart in the first place; got {direct}"
    )

    turn_complete = _calls(fn, name="TurnCompletePayload")
    assert len(turn_complete) == 1
    keywords = {k.arg: k.value for k in turn_complete[0].keywords}
    assert ast.unparse(keywords["raw_jsonl"]) == "captured.turn"

    stores = _calls(fn, attr="store_transcript")
    assert len(stores) == 1, "the #68 fallback's one row write"
    store_kw = {k.arg: k.value for k in stores[0].keywords}
    assert ast.unparse(store_kw["raw_jsonl"]) == "fallback.turn"


def test_turn_is_attributable_is_read_ONLY_from_the_finally():
    """Nothing else may consult the flag — the success path least of all.

    ``_turn_slice``'s whole-file recovery ("storing too much beats storing
    nothing") is deliberate and CORRECT for a turn that completed: such a turn
    definitionally appended something, so a non-increasing line count can only
    mean a stale snapshot, and the recovery is what keeps that turn's row from
    being empty. ``turn_is_attributable`` exists solely so the FAILURE path can
    DECLINE that recovery, where "the agent appended nothing" is a first-class
    expected state and the recovered bytes are an earlier turn's.

    A success path that started gating on the flag would silently switch off the
    recovery — no error, no test, just rows quietly missing from turns whose
    snapshot went stale. Until now only a docstring said not to.
    """
    tree = _cc_engine_ast()
    fn = _run_cc_turn_ast(tree)

    reads = [n for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and n.attr == "turn_is_attributable"]
    # Not vacuous-by-accident: if the flag stops being read at all, the #68
    # fallback has stopped declining the misattributed recovery and this test
    # must say so rather than pass on an empty list.
    assert reads, "nothing reads turn_is_attributable any more"

    tries = [n for n in fn.body if isinstance(n, ast.Try) and n.finalbody]
    assert len(tries) == 1, "run_cc_turn's one top-level try/finally"
    in_finally = {id(n) for stmt in tries[0].finalbody for n in ast.walk(stmt)}
    assert all(id(n) in in_finally for n in reads), (
        "turn_is_attributable is read outside run_cc_turn's finally (line(s) "
        f"{[n.lineno for n in reads if id(n) not in in_finally]}); only the #68 "
        "failure fallback may decline the whole-file recovery"
    )

    gate = [n for n in ast.walk(fn) if isinstance(n, ast.If)
            and "query_complete" in ast.unparse(n.test)
            and "on_turn_complete" in ast.unparse(n.test)]
    assert len(gate) == 1, "the query_complete persist gate"
    in_gate = {id(n) for stmt in gate[0].body for n in ast.walk(stmt)}
    assert not [n for n in reads if id(n) in in_gate], (
        "the query_complete gate must NOT consult turn_is_attributable: doing so "
        "would disable the whole-file recovery for exactly the turns it is right for"
    )


def test_the_pre_spawn_snapshot_uses_the_same_root_expression_as_the_read():
    """``_transcript_line_counts`` keys are NOT normalised — they are whatever
    ``str(path)`` the caller's root produced. A root spelled differently here
    than the one ``_read_turn_transcript`` resolves would miss every lookup and
    silently store the whole cumulative session again, with no error."""
    calls = _calls(_run_cc_turn_ast(), name="_transcript_line_counts")

    assert len(calls) == 1
    assert _arg_src(calls[0], 0) == (
        "Path(dirs.cc_state_mnt) / 'projects' if dirs.cc_state_mnt else None"
    )


def test_the_locals_the_finally_reads_are_bound_before_the_try():
    """``pre_turn_lines`` and ``transcript_persisted`` are read by the finally
    (that is where #68's fallback capture lives). Binding them INSIDE the try
    means any exception raised before the assignment makes the finally itself
    raise ``UnboundLocalError`` — replacing the real failure with a bogus one.

    ``pre_turn_lines`` must additionally precede the spawn: it is a PRE-turn
    count, and one taken after the agent started would already include some of
    the records this turn is meant to keep.
    """
    fn = _run_cc_turn_ast()
    tries = [n for n in fn.body if isinstance(n, ast.Try) and n.finalbody]
    assert len(tries) == 1, "run_cc_turn's one top-level try/finally"

    first: dict[str, int] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            bound = getattr(target, "id", None)
            if bound in ("pre_turn_lines", "transcript_persisted"):
                first[bound] = min(first.get(bound, node.lineno), node.lineno)

    assert set(first) == {"pre_turn_lines", "transcript_persisted"}
    assert first["pre_turn_lines"] < tries[0].lineno
    assert first["transcript_persisted"] < tries[0].lineno

    spawn = _calls(fn, name="_spawn_with_stale_name_retry")
    assert spawn, "no spawn call found"
    assert first["pre_turn_lines"] < spawn[0].lineno


def test_transcript_persisted_is_set_only_after_the_write_returns():
    """Not before the call and not in the ``except``: the flag means "a row
    exists", so setting it around a call that raised would make the failure-path
    fallback skip the very turn whose row is missing."""
    fn = _run_cc_turn_ast()
    calls = _calls(fn, name="on_turn_complete")
    assert len(calls) == 1

    def _sets_true(node):
        return (
            isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == "transcript_persisted" for t in node.targets)
            and isinstance(node.value, ast.Constant) and node.value.value is True
        )

    guarding = [
        t for t in ast.walk(fn)
        if isinstance(t, ast.Try)
        and any(node is calls[0] for stmt in t.body for node in ast.walk(stmt))
    ]
    assert guarding, "the on_turn_complete call must stay inside its own try"
    # the innermost such try — the outer one wraps the whole turn
    innermost = max(guarding, key=lambda t: t.lineno)

    in_body = [n for stmt in innermost.body for n in ast.walk(stmt) if _sets_true(n)]
    assert len(in_body) == 1, (
        "exactly one transcript_persisted = True belongs in the persist try's "
        "BODY, right after the call: putting it after the try/except would set "
        "it even when the handler swallowed a failed persist"
    )
    assert in_body[0].lineno > calls[0].lineno, "set it AFTER the call returns"

    handled = [
        n for h in ast.walk(fn) if isinstance(h, ast.ExceptHandler)
        for stmt in h.body for n in ast.walk(stmt) if _sets_true(n)
    ]
    assert not handled, "an except handler must not mark a failed persist as done"
