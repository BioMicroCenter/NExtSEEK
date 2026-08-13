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

Hermetic: tmp_path + stdlib only, no docker, no DB.
"""

import os
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
