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


def test_transcript_line_counts_skips_an_unreadable_file(tmp_path):
    """It runs on the turn's hot path; one bad file must not raise."""
    good = tmp_path / "good.jsonl"
    good.write_bytes(_jsonl("1"))
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(_jsonl("1", "2"))
    os.chmod(bad, 0o000)
    try:
        counts = cc_engine._transcript_line_counts(tmp_path)
    finally:
        os.chmod(bad, 0o644)
    # root (as the container runs) can read a 0o000 file, so accept either the
    # skip or the successful read — what must NOT happen is an exception.
    assert counts.get(str(good)) == 1
    assert set(counts) <= {str(good), str(bad)}


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
# run_cc_turn wiring — which captured member reaches which sink
# --------------------------------------------------------------------------


def _run_cc_turn_ast():
    src = Path(cc_engine.__file__).with_suffix(".py").read_text()
    tree = ast.parse(src)
    return next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "run_cc_turn"
    )


def _calls(fn, *, name=None, attr=None):
    """Every ``ast.Call`` in ``fn`` to a bare name or to an ``x.attr``."""
    out = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if name is not None and isinstance(func, ast.Name) and func.id == name:
            out.append(node)
        elif attr is not None and isinstance(func, ast.Attribute) and func.attr == attr:
            out.append(node)
    return out


def test_the_debug_trace_still_parses_the_FULL_session():
    """Deliberate, and pinned so a later refactor cannot silently swap the two
    members. Slicing the summariser's input would change every Debug-panel
    trace's ``steps``, ``transcript_line_count`` and ``turn_count`` — a separate
    defect with its own blast radius, out of scope for #68 (see SPEC.md)."""
    calls = _calls(_run_cc_turn_ast(), attr="parse_transcript")

    assert len(calls) == 1, "expected exactly one parse_transcript call"
    assert ast.unparse(calls[0].args[0]) == "captured.session"


def test_the_two_per_turn_sinks_get_the_TURN_slice():
    """The ``raw/transcript-<run_id>.jsonl`` copy and the ``CCSessionTranscript``
    blob are both keyed per TURN, so both must hold one turn's records.

    Stated as "no per-turn sink is handed a ``.session`` member" rather than as
    an exact call list: the failure-path fallback adds a second capture with its
    own local name, and that is correct as long as it too writes the slice.
    """
    fn = _run_cc_turn_ast()

    written = [ast.unparse(c.args[0]) for c in _calls(fn, attr="write_bytes")]
    assert "captured.turn" in written, (
        f"the success path's raw/ copy must write the slice, got {written}"
    )
    assert not [a for a in written if a.endswith(".session")], (
        f"a per-turn raw/ copy must never be handed the whole session: {written}"
    )

    payloads = _calls(fn, name="TurnCompletePayload")
    assert len(payloads) == 1
    keywords = {k.arg: k.value for k in payloads[0].keywords}
    assert ast.unparse(keywords["raw_jsonl"]) == "captured.turn"


def test_the_pre_spawn_snapshot_uses_the_same_root_expression_as_the_read():
    """``_transcript_line_counts`` keys are NOT normalised — they are whatever
    ``str(path)`` the caller's root produced. A root spelled differently here
    than the one ``_read_turn_transcript`` resolves would miss every lookup and
    silently store the whole cumulative session again, with no error."""
    calls = _calls(_run_cc_turn_ast(), name="_transcript_line_counts")

    assert len(calls) == 1
    assert ast.unparse(calls[0].args[0]) == (
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
