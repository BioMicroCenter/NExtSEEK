import dataclasses
import hashlib
import types

import pytest

from NessieAI.tests.pipeline_scenarios import scrub
from NessieAI.tests.pipeline_scenarios import tape as tp


@dataclasses.dataclass
class _Res:
    accession: str
    runs: list


def test_encode_decode_round_trips_dataclasses_and_containers():
    v = {"a": [_Res("SRR1", [{"x": 1}])], "b": None}
    enc = tp.encode(v)
    assert enc["a"][0]["__dataclass__"].endswith("test_tape._Res")
    assert tp.decode(enc) == v


def test_record_then_play_in_order_per_source():
    t = tp.Tape(mode="record")
    t.record("f", ("<object>", ["U1"]), {}, {"ok": True})
    t.record("f", ("<object>", ["U2"]), {}, {"ok": False})
    r = tp.Tape.from_json(t.to_json())
    assert r.play("f", ("<object>", ["U1"]), {}) == {"ok": True}
    assert r.play("f", ("<object>", ["U2"]), {}) == {"ok": False}
    with pytest.raises(tp.TapeMismatch, match="more often"):
        r.play("f", ("<object>", ["U3"]), {})


def test_play_with_other_arguments_is_a_mismatch():
    t = tp.Tape(mode="record")
    t.record("f", ("<object>", ["U1"]), {}, 1)
    r = tp.Tape.from_json(t.to_json())
    with pytest.raises(tp.TapeMismatch, match="other arguments"):
        r.play("f", ("<object>", ["U9"]), {})


def test_objects_are_keyed_by_place_not_type():
    class A: ...
    class B: ...
    assert tp._args_key((A(), [1]), {}) == tp._args_key((B(), [1]), {})


def test_replay_client_serves_recorded_model_answers():
    t = tp.Tape(mode="record")
    t.record("selection", (), {}, '{"pipelines": ["rnaseq"], "reason": "r"}')
    t.record("agent", (), {}, {"stop_reason": "end_turn", "content": [{"type": "text", "text": "hi"}]})
    r = tp.Tape.from_json(t.to_json())
    client = tp.ReplayClient(r)
    assert client.chat(model="m", messages=[]).content == '{"pipelines": ["rnaseq"], "reason": "r"}'
    assert r.play("agent", (), {})["content"][0]["text"] == "hi"


def test_unkeyed_sources_cover_the_ones_whose_arguments_change_in_replay():
    assert {"agent", "annotate_metadata_with_sampletypes", "gather_protocol_text"} <= tp.UNKEYED


def test_every_taped_target_exists():
    import importlib
    for target in tp.TAPED:
        module, _, attr = target.rpartition(".")
        assert hasattr(importlib.import_module(module), attr), target


def test_a_mismatch_is_remembered_even_if_the_caller_swallows_it():
    t = tp.Tape(mode="record")
    t.record("f", ("<object>", ["U1"]), {}, 1)
    r = tp.Tape.from_json(t.to_json())
    with pytest.raises(tp.TapeMismatch):
        r.play("f", ("<object>", ["U9"]), {})
    with pytest.raises(tp.TapeMismatch):
        r.play("f", ("<object>", ["U1"]), {})
    assert len(r.mismatches) == 2 and "other arguments" in r.mismatches[0] and "more often" in r.mismatches[1]


def test_unplayed_counts_entries_never_played():
    t = tp.Tape(mode="record")
    for v in (1, 2, 3):
        t.record("f", (), {}, v)
    t.record("g", (), {}, 1)
    r = tp.Tape.from_json(t.to_json())
    r.play("f", (), {})
    assert r.unplayed() == {"f": 2, "g": 1}


def test_args_key_sorts_a_set_of_mixed_types():
    assert tp._args_key(({1, "a", (2, 3)},), {}) == tp._args_key(({"a", (2, 3), 1},), {})


# ---- taped() end to end, over fakes -------------------------------------------------------

@dataclasses.dataclass
class _Ctx:
    text: str = "PAYLOAD"

    def to_prompt_text(self, sections=None):
        return self.text

    def size_report(self, sections=None):
        return {"est_tokens": 1}


@pytest.fixture()
def fakes(monkeypatch):
    from chat_nextseek.pipeline import agent, agent_tools
    calls = []

    def metadata(config, uids, *a, **k):
        calls.append("fetch")
        return {"data": [{"metadata": {"UID": uids[0], "Scientist": "Bob Real"}}]}

    monkeypatch.setattr(agent_tools, "fetch_reporter_metadata", metadata)
    monkeypatch.setattr(agent_tools, "annotate_metadata_with_sampletypes", lambda *a, **k: {"annotated": a[1]})
    monkeypatch.setattr(agent_tools, "gather_protocol_text", lambda *a, **k: "Prepared by Bob in the Real lab with RNA")
    monkeypatch.setattr(agent_tools, "expand_uid_prefixes", lambda config, uids: [u + "-1" for u in uids])
    monkeypatch.setattr(agent_tools, "lookup_run_sizes", lambda *a, **k: {"SRR1": 5})
    monkeypatch.setattr(agent_tools, "resolve_accessions", lambda *a, **k: [_Res("SRR1", [{"x": 1}])])
    monkeypatch.setattr(agent_tools, "build_selection_context", lambda *a, **k: _Ctx())
    monkeypatch.setattr(agent_tools, "build_sample_digest", lambda *a, **k: {"digest": 1})
    monkeypatch.setattr(agent, "call_tools", lambda *a, **k: {"stop_reason": "end_turn", "log": a[0]})
    return types.SimpleNamespace(agent=agent, tools=agent_tools, calls=calls)


def _scrubber(source, value):
    if source == "fetch_reporter_metadata":
        return scrub.scrub_metadata(value)
    if source == "gather_protocol_text":
        return scrub.protocol_markers_found(value, markers={"rna"})
    return value


def _session(f, log="/tmp/rec-1/log"):
    """The calls one story makes, through the module names the tools use."""
    md = f.tools.fetch_reporter_metadata(object(), ["U1"])
    ann = f.tools.annotate_metadata_with_sampletypes(object(), md)
    text = f.tools.gather_protocol_text(object(), ann)
    uids = f.tools.expand_uid_prefixes(object(), ["U"])
    sizes = f.tools.lookup_run_sizes(object(), ["SRR1"])
    acc = f.tools.resolve_accessions(object(), ["SRR1"])
    ctx = f.tools.build_selection_context(object(), uids, sections=["a"])
    payload = ctx.to_prompt_text(["a"])
    reply = f.agent.call_tools(log)
    return md, ann, text, uids, sizes, acc, payload, reply


def _record(f):
    t = tp.Tape(mode="record")
    originals = f.tools.fetch_reporter_metadata, f.agent.call_tools
    with tp.taped(t, _scrubber):
        out = _session(f)
        assert f.tools.fetch_reporter_metadata is not originals[0]
    assert (f.tools.fetch_reporter_metadata, f.agent.call_tools) == originals
    return t, out


def test_taped_record_stores_and_returns_the_scrubbed_value(fakes):
    t, out = _record(fakes)
    md, ann, text, uids, sizes, acc, payload, reply = out
    assert md["data"][0]["metadata"]["Scientist"] == "REDACTED"
    assert text == "rna"
    assert acc == [_Res("SRR1", [{"x": 1}])]
    assert "Bob" not in str(t.to_json()) and "Real" not in str(t.to_json())
    assert t.entries["payload"][0]["value"] == hashlib.sha256(b"PAYLOAD").hexdigest()
    assert len(t.entries["payload"]) == 1


def test_taped_replay_serves_the_recording_and_drops_arguments_of_unkeyed_sources(fakes):
    t, _ = _record(fakes)
    r = tp.Tape.from_json(t.to_json())
    fakes.calls.clear()
    with tp.taped(r):
        md, ann, text, uids, sizes, acc, payload, reply = _session(fakes, log="/tmp/other-run/log")
    assert fakes.calls == []
    assert uids == ["U-1"] and sizes == {"SRR1": 5} and text == "rna"
    assert md["data"][0]["metadata"]["Scientist"] == "REDACTED"
    assert reply == {"stop_reason": "end_turn", "log": "/tmp/rec-1/log"}
    assert payload.startswith("<replayed selection payload sha256=")
    assert r.unplayed() == {} and r.mismatches == []


def test_taped_replay_raises_at_exit_when_a_mismatch_was_swallowed(fakes):
    t, _ = _record(fakes)
    r = tp.Tape.from_json(t.to_json())
    with pytest.raises(tp.TapeMismatch, match="more often"):
        with tp.taped(r):
            _session(fakes)
            try:
                fakes.tools.lookup_run_sizes(object(), ["SRR1"])
            except AssertionError:
                pass
    r2 = tp.Tape.from_json(t.to_json())
    with pytest.raises(tp.TapeMismatch, match="other arguments"):
        with tp.taped(r2):
            try:
                fakes.tools.expand_uid_prefixes(object(), ["DIFFERENT"])
            except Exception:
                pass


def test_taped_replay_raises_at_exit_when_an_entry_was_never_played(fakes):
    t, _ = _record(fakes)
    r = tp.Tape.from_json(t.to_json())
    with pytest.raises(tp.TapeMismatch, match="never played"):
        with tp.taped(r):
            fakes.tools.fetch_reporter_metadata(object(), ["U1"])


def test_taped_record_raises_at_exit_when_a_scrubber_refused_and_the_caller_swallowed_it(fakes):
    def refusing(source, value):
        raise ValueError("path in the tape")
    t = tp.Tape(mode="record")
    with pytest.raises(ValueError, match="path in the tape"):
        with tp.taped(t, refusing):
            try:
                fakes.tools.lookup_run_sizes(object(), ["SRR1"])
            except Exception:
                pass


def test_recording_client_keeps_the_selection_answer_and_passes_calls_through():
    class Live:
        def chat(self, **kw):
            return types.SimpleNamespace(content="ANSWER")

        def chat_with_tools(self, **kw):
            return "tools"
        extra = 7
    t = tp.Tape(mode="record")
    c = tp.RecordingClient(Live(), t)
    assert c.chat(model="m").content == "ANSWER" and c.chat_with_tools() == "tools" and c.extra == 7
    assert tp.ReplayClient(tp.Tape.from_json(t.to_json())).chat().content == "ANSWER"
