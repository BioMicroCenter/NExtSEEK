"""Hermetic unit tests for the Django-free CC session helpers (Step 1b resume).

No Django, no Docker, no spend — pure logic + tmp filesystem.
"""
from pathlib import Path

from nextseek_api.cc_assistant import cc_session


# --- resume_id_from_state -----------------------------------------------------

def test_resume_id_returns_stored_cc_session_id():
    assert cc_session.resume_id_from_state({"cc_session_id": "U1"}) == "U1"


def test_resume_id_none_when_absent_or_empty_or_not_mapping():
    assert cc_session.resume_id_from_state({}) is None
    assert cc_session.resume_id_from_state({"cc_session_id": ""}) is None
    assert cc_session.resume_id_from_state({"cc_session_id": 123}) is None
    assert cc_session.resume_id_from_state(None) is None


# --- make_session_sniffer -----------------------------------------------------

def test_sniffer_forwards_every_event_unchanged():
    seen = []
    wrapped = cc_session.make_session_sniffer(
        lambda e, d: seen.append((e, d)), lambda sid: None
    )
    wrapped("agent_started", {"agent": "container_cc"})
    wrapped("query_complete", {"reply": "hi", "cc_session_id": "U1"})
    assert seen == [
        ("agent_started", {"agent": "container_cc"}),
        ("query_complete", {"reply": "hi", "cc_session_id": "U1"}),
    ]


def test_sniffer_captures_cc_session_id_once_per_change():
    captured = []
    wrapped = cc_session.make_session_sniffer(
        lambda e, d: None, lambda sid: captured.append(sid)
    )
    wrapped("agent_started", {"agent": "container_cc"})        # no id
    wrapped("tool", {"cc_session_id": "U1"})                   # capture U1
    wrapped("query_complete", {"cc_session_id": "U1"})         # same -> no dup
    wrapped("query_complete", {"cc_session_id": "U2"})         # rotated -> capture U2
    assert captured == ["U1", "U2"]


def test_sniffer_ignores_missing_or_blank_id():
    captured = []
    wrapped = cc_session.make_session_sniffer(
        lambda e, d: None, lambda sid: captured.append(sid)
    )
    wrapped("query_complete", {"reply": "x"})
    wrapped("query_complete", {"cc_session_id": ""})
    assert captured == []


# --- store_has_transcripts ----------------------------------------------------

def test_store_has_transcripts_false_when_missing(tmp_path):
    assert cc_session.store_has_transcripts(tmp_path / "nope") is False


def test_store_has_transcripts_false_when_projects_empty(tmp_path):
    (tmp_path / "projects").mkdir()
    assert cc_session.store_has_transcripts(tmp_path) is False


def test_store_has_transcripts_true_with_a_jsonl(tmp_path):
    d = tmp_path / "projects" / "-home-user"
    d.mkdir(parents=True)
    (d / "U1.jsonl").write_text("{}")
    assert cc_session.store_has_transcripts(tmp_path) is True
