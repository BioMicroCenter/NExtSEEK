"""The journal (tool spec 7.1): a line is on disk before its write; a torn last line is ignored and reported."""
import json
import os

import pytest

from nextseek_api.studies import journal as j


def test_append_writes_one_json_line_with_seq_time_run_and_step(tmp_path):
    jr = j.Journal(tmp_path / j.JOURNAL_FILE, run_id="run-1")
    line = jr.append("study", "intent", target_key="k", payload={"a": 1})
    assert line["seq"] == 1 and line["run_id"] == "run-1" and line["at"].endswith("Z")
    [read] = [json.loads(x) for x in (tmp_path / j.JOURNAL_FILE).read_text().splitlines()]
    assert read == line


def test_a_line_is_on_disk_before_the_write_it_precedes(tmp_path):
    jr = j.Journal(tmp_path / j.JOURNAL_FILE, run_id="run-1")

    def write_that_raises():
        jr.append("clone", "intent", target_key="k", source_assay_id=101)
        raise RuntimeError("the POST died")

    with pytest.raises(RuntimeError):
        write_that_raises()
    lines, bad = j.read_journal(tmp_path / j.JOURNAL_FILE)
    assert bad == 0 and lines[-1]["event"] == "intent" and lines[-1]["source_assay_id"] == 101


def test_append_fsyncs(tmp_path, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(j.os, "fsync", lambda fd: (synced.append(fd), real(fd)))
    j.Journal(tmp_path / j.JOURNAL_FILE, run_id="r").append("run", "start")
    assert synced


def test_a_torn_last_line_is_ignored_reported_and_closed_before_the_next(tmp_path):
    path = tmp_path / j.JOURNAL_FILE
    jr = j.Journal(path, run_id="r")
    jr.append("run", "start")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"seq": 2, "step": "study", "ev')
    lines, bad = j.read_journal(path)
    assert (len(lines), bad) == (1, 1)
    again = j.Journal(path, run_id="r")
    again.append("study", "intent", target_key="k")
    lines, bad = j.read_journal(path)
    assert [l["seq"] for l in lines] == [1, 2] and bad == 1


@pytest.mark.parametrize("field", ["password", "Password", "authorization", "secret", "credential"])
def test_a_credential_field_is_refused(tmp_path, field):
    with pytest.raises(ValueError, match="never journaled"):
        j.Journal(tmp_path / j.JOURNAL_FILE, run_id="r").append("run", "start", **{field: "x"})


def test_state_reads_every_step(tmp_path):
    jr = j.Journal(tmp_path / j.JOURNAL_FILE, run_id="r")
    jr.append("run", "start", login="op", person_id=5)
    jr.append("study", "intent", target_key="t1", payload={})
    jr.append("study", "done", target_key="t1", seek_id=101)
    jr.append("study", "intent", target_key="t2", payload={})
    jr.append("clone", "intent", target_key="t1", source_assay_id=11, payload={})
    jr.append("clone", "adopted", target_key="t1", source_assay_id=11, seek_id=501)
    jr.append("map", "intent", pairs=[[501, 900]])
    jr.append("map", "done", rows=[[7, 501, 900]])
    jr.append("links", "intent", unit=1, digest="d", inserts=[], deleted_rows=[])
    jr.append("links", "prepared", unit=1, inserted=[], outbox="in_transaction")
    jr.append("links", "committed", unit=1)
    jr.append("links", "intent", unit=2, digest="d", inserts=[], deleted_rows=[])
    jr.append("pubs", "intent", rows=[[3, "{}", "{\"DOI\": \"x\"}"]])
    jr.append("undo", "done", part="unit", unit=1)
    state = j.journal_state(j.read_journal(tmp_path / j.JOURNAL_FILE)[0])
    assert state.started
    assert state.studies["t1"] == {"intent": {}, "seek_id": 101, "how": "done"}
    assert state.studies["t2"] == {"intent": {}, "seek_id": None, "how": None}
    assert state.clones[("t1", 11)]["seek_id"] == 501 and state.clones[("t1", 11)]["how"] == "adopted"
    assert state.map_pairs == [[501, 900]] and state.map_rows == [[7, 501, 900]]
    assert state.units[1]["committed"] and state.units[2]["prepared"] is None
    assert state.pubs_rows == {3: ("{}", "{\"DOI\": \"x\"}")} and not state.pubs_done
    assert state.undone_units == {1} and not state.undone
