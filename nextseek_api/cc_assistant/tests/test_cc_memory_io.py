"""Hermetic: atomic memory-file write + copy-on-change transcript staging."""
from pathlib import Path

from nextseek_api.cc_assistant import cc_memory, cc_memory_io


def _meta(sid, host_path):
    return cc_memory.SessionMeta(session_id=sid, updated_at=1.0, fingerprint=None,
                                 summary={"gist": "g", "items": []},
                                 transcript_path=str(host_path), changed=False)


def test_write_memory_file_writes_then_clears(tmp_path):
    dest = tmp_path / "_memory" / "S1" / "CLAUDE.md"
    out = cc_memory_io.write_memory_file(dest, "# memory\n")
    assert out == dest and dest.read_text() == "# memory\n"
    assert cc_memory_io.write_memory_file(dest, "") is None
    assert not dest.exists()


def test_stage_transcripts_copies_and_prunes(tmp_path):
    src_a = tmp_path / "A.jsonl"; src_a.write_text('{"a":1}\n')
    src_b = tmp_path / "B.jsonl"; src_b.write_text('{"b":2}\n')
    staging = tmp_path / "stage"
    win = [_meta("A", src_a), _meta("B", src_b)]
    out = cc_memory_io.stage_transcripts(win, staging)
    assert out == staging
    assert (staging / "A.jsonl").read_text() == '{"a":1}\n'
    assert (staging / "B.jsonl").read_text() == '{"b":2}\n'
    out2 = cc_memory_io.stage_transcripts([_meta("A", src_a)], staging)
    assert (staging / "A.jsonl").exists()
    assert not (staging / "B.jsonl").exists()


def test_stage_transcripts_none_when_empty(tmp_path):
    assert cc_memory_io.stage_transcripts([], tmp_path / "stage") is None
