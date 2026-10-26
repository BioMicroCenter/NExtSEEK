import dataclasses
import hashlib
import threading
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


# ---- recordings that ended selection early must replay -----------------------------------

class _TwoArgs(Exception):
    def __init__(self, a, b):
        super().__init__(a, b)


def test_exceptions_are_taped_by_class_name_and_message_and_rebuilt():
    spec = tp.exception_spec(ValueError("no good"))
    assert spec == {"raise": "builtins.ValueError", "message": "no good"}
    err = tp.rebuild_exception(spec)
    assert type(err) is ValueError and str(err) == "no good"


def test_an_exception_that_takes_other_arguments_comes_back_as_a_runtime_error():
    err = tp.rebuild_exception(tp.exception_spec(_TwoArgs(1, 2)))
    assert type(err) is RuntimeError and "_TwoArgs" in str(err)
    err = tp.rebuild_exception({"raise": "no.such.Thing", "message": "m"})
    assert type(err) is RuntimeError and "no.such.Thing" in str(err)


def _replay_of(t):
    return tp.Tape.from_json(t.to_json())


def test_a_digest_error_is_taped_and_replays_clean(fakes, monkeypatch):
    from chat_nextseek.pipeline.sample_digest import DigestError

    def failing(*a, **k):
        raise DigestError("these samples have no reads")
    monkeypatch.setattr(fakes.tools, "build_sample_digest", failing)
    t = tp.Tape(mode="record")
    with tp.taped(t):
        with pytest.raises(DigestError, match="no reads"):
            fakes.tools.build_sample_digest(object(), ["U1"])
    assert t.entries["digest"][0]["value"]["raise"].endswith("DigestError")
    r = _replay_of(t)
    with tp.taped(r):
        with pytest.raises(DigestError, match="no reads"):
            fakes.tools.build_sample_digest(object(), ["U1"])
    assert r.unplayed() == {} and r.mismatches == []


def test_a_successful_digest_is_taped_as_ok(fakes):
    t2 = tp.Tape(mode="record")
    with tp.taped(t2):
        fakes.tools.build_sample_digest(object(), ["U1"])
    assert t2.entries["digest"][0]["value"] == {"ok": True}
    r = _replay_of(t2)
    with tp.taped(r):
        assert fakes.tools.build_sample_digest(object(), ["U1"]) == {}


def test_a_payload_too_large_error_is_taped_and_replays_clean(fakes, monkeypatch):
    from chat_nextseek.pipeline.selection_context import PayloadTooLargeError

    def too_large(*a, **k):
        raise PayloadTooLargeError("past the ceiling")
    monkeypatch.setattr(fakes.tools, "build_selection_context", too_large)
    t = tp.Tape(mode="record")
    with tp.taped(t):
        with pytest.raises(PayloadTooLargeError):
            fakes.tools.build_selection_context(object(), ["U1"])
    r = _replay_of(t)
    with tp.taped(r):
        with pytest.raises(PayloadTooLargeError, match="past the ceiling"):
            fakes.tools.build_selection_context(object(), ["U1"])
    assert r.unplayed() == {} and r.mismatches == []


def test_a_failing_to_prompt_text_is_taped_and_replays_clean(fakes, monkeypatch):
    from chat_nextseek.pipeline.selection_context import PayloadTooLargeError

    class Bad(_Ctx):
        def to_prompt_text(self, sections=None):
            raise PayloadTooLargeError("rendering too large")
    monkeypatch.setattr(fakes.tools, "build_selection_context", lambda *a, **k: Bad())
    t = tp.Tape(mode="record")
    with tp.taped(t):
        with pytest.raises(PayloadTooLargeError):
            fakes.tools.build_selection_context(object(), ["U1"]).to_prompt_text(["a"])
    r = _replay_of(t)
    with tp.taped(r):
        with pytest.raises(PayloadTooLargeError, match="rendering"):
            fakes.tools.build_selection_context(object(), ["U1"])
    assert r.unplayed() == {}


def test_a_failing_selection_call_is_taped_and_replays_clean(fakes):
    class Live:
        def chat(self, **kw):
            raise ConnectionError("model unreachable")
    t = tp.Tape(mode="record")
    with tp.taped(t):
        with pytest.raises(ConnectionError, match="unreachable"):
            tp.RecordingClient(Live(), t).chat(model="m")
    r = _replay_of(t)
    with tp.taped(r):
        with pytest.raises(ConnectionError, match="unreachable"):
            tp.ReplayClient(r).chat(model="m")
    assert r.unplayed() == {} and r.mismatches == []


def test_a_digest_that_outlasts_the_tools_wait_is_taped_as_a_timeout_and_replays_as_one(fakes, monkeypatch):
    release = threading.Event()

    def slow(*a, **k):
        release.wait(5)
        return {"late": True}
    monkeypatch.setattr(fakes.tools, "build_sample_digest", slow)
    t = tp.Tape(mode="record")
    with tp.taped(t):
        pool = fakes.tools.concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(fakes.tools.build_sample_digest, object(), ["U1"])
        with pytest.raises(fakes.tools.concurrent.futures.TimeoutError):
            future.result(timeout=0.05)
        pool.shutdown(wait=False)
        release.set()
        pool2 = fakes.tools.concurrent.futures.ThreadPoolExecutor(max_workers=1)
        pool2.submit(lambda: None).result(timeout=1)   # an unrelated call is untouched
    import time
    time.sleep(0.2)        # the abandoned worker finishes late; it must not overwrite the timeout
    spec = t.entries["digest"][0]["value"]
    assert spec["raise"].endswith("TimeoutError") and spec.get("waiting") is True
    r = _replay_of(t)
    with tp.taped(r):
        pool = fakes.tools.concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(fakes.tools.build_sample_digest, object(), ["U1"])
        with pytest.raises(fakes.tools.concurrent.futures.TimeoutError):
            future.result(timeout=1)
        pool.shutdown(wait=False)
    assert r.unplayed() == {} and r.mismatches == []


def test_a_selection_that_outlasts_the_tools_wait_replays_as_a_timeout(fakes, monkeypatch):
    from chat_nextseek.pipeline import selection
    release = threading.Event()

    def slow_decide(**kw):
        tp.RecordingClient(types.SimpleNamespace(chat=lambda **k: release.wait(5)), kw["tape"]).chat()
    monkeypatch.setattr(selection, "decide", slow_decide)
    t = tp.Tape(mode="record")
    with tp.taped(t):
        pool = fakes.tools.concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(selection.decide, tape=t)
        with pytest.raises(fakes.tools.concurrent.futures.TimeoutError):
            future.result(timeout=0.05)
        pool.shutdown(wait=False)
    release.set()
    spec = t.entries["selection"][0]["value"]
    assert spec.get("waiting") is True
    r = _replay_of(t)
    with tp.taped(r):
        pool = fakes.tools.concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(selection.decide, tape=r)
        with pytest.raises(fakes.tools.concurrent.futures.TimeoutError):
            future.result(timeout=1)
        pool.shutdown(wait=False)
    assert r.unplayed() == {}


def test_an_exception_message_with_a_path_or_email_makes_taped_raise_at_exit(fakes, monkeypatch):
    from chat_nextseek.pipeline.sample_digest import DigestError
    for message, needle in (("could not read /Users/alice/data/x.bam", "path"),
                            ("ask bob@example.org", "email"),
                            ("fetch https://private.example.org/y", "url")):
        def failing(*a, _m=message, **k):
            raise DigestError(_m)
        monkeypatch.setattr(fakes.tools, "build_sample_digest", failing)
        t = tp.Tape(mode="record")
        with pytest.raises(ValueError, match=f"digest.*{needle}"):
            with tp.taped(t):
                with pytest.raises(DigestError):
                    fakes.tools.build_sample_digest(object(), ["U1"])
