import dataclasses
import hashlib
import json
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


def _live(source, value):
    """What the live run gets back: people redacted, nothing else."""
    return scrub.scrub_metadata(value) if source == "fetch_reporter_metadata" else value


def _store(source, value):
    """What the tape keeps: the protocol text cut to its markers."""
    return scrub.protocol_markers_found(value, markers={"rna"}) if source == "gather_protocol_text" else value


SCRUB = {"live": _live, "store": _store}


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
    with tp.taped(t, **SCRUB):
        out = _session(f)
        assert f.tools.fetch_reporter_metadata is not originals[0]
    assert (f.tools.fetch_reporter_metadata, f.agent.call_tools) == originals
    return t, out


def test_taped_record_returns_the_live_value_and_stores_the_scrubbed_one(fakes):
    t, out = _record(fakes)
    md, ann, text, uids, sizes, acc, payload, reply = out
    assert md["data"][0]["metadata"]["Scientist"] == "REDACTED"
    assert text == "Prepared by Bob in the Real lab with RNA"        # the live run reads what production reads
    assert t.entries["gather_protocol_text"][0]["value"] == "rna"    # the tape keeps only the markers
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


def test_a_store_refusal_never_reaches_the_live_call_and_taped_raises_at_exit(fakes):
    def refusing(source, value):
        raise ValueError("path in the tape")
    t = tp.Tape(mode="record")
    with pytest.raises(ValueError, match="path in the tape"):
        with tp.taped(t, store=refusing):
            assert fakes.tools.lookup_run_sizes(object(), ["SRR1"]) == {"SRR1": 5}   # the tool got its answer
            assert t.refused and "lookup_run_sizes" not in t.entries                  # nothing kept


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


def test_an_exception_that_takes_other_arguments_comes_back_with_them():
    spec = json.loads(json.dumps(tp.exception_spec(_TwoArgs(1, "two"))))      # through the tape's JSON
    err = tp.rebuild_exception(spec)
    assert type(err) is _TwoArgs and err.args == (1, "two") and str(err) == str(_TwoArgs(1, "two"))
    err = tp.rebuild_exception(tp.exception_spec(FileNotFoundError(2, "No such file")))
    assert type(err) is FileNotFoundError and err.errno == 2 and err.strerror == "No such file"
    err = tp.rebuild_exception(tp.exception_spec(KeyError("UID")))
    assert type(err) is KeyError and str(err) == str(KeyError("UID"))
    raw = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    err = tp.rebuild_exception(json.loads(json.dumps(tp.exception_spec(raw))))
    assert type(err) is UnicodeDecodeError and str(err) == str(raw)


def test_an_exception_that_cannot_be_rebuilt_comes_back_as_a_runtime_error():
    class _Unkept(Exception):
        def __init__(self, a, b):
            super().__init__(a, b)
    # its arguments cannot go on a tape (an object), and it will not take the message alone
    err = tp.rebuild_exception(tp.exception_spec(_Unkept(object(), 2)))
    assert type(err) is RuntimeError and "_Unkept" in str(err)
    err = tp.rebuild_exception({"raise": "no.such.Thing", "message": "m"})
    assert type(err) is RuntimeError and "no.such.Thing" in str(err)


def test_a_one_message_exception_keeps_the_spec_it_always_had():
    assert tp.exception_spec(TimeoutError("digest timed out")) == {"raise": "builtins.TimeoutError",
                                                                    "message": "digest timed out"}


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


def test_a_timeout_marks_the_call_that_timed_out_and_its_late_answer_is_dropped(fakes, monkeypatch):
    """The tool gave up before the worker had even asked the model: the timeout goes on that call's own
    slot (not the newest selection entry, here an earlier answer), and the late answer, when it comes,
    is dropped on purpose: it neither overwrites the timeout, nor adds an entry, nor refuses the tape."""
    from chat_nextseek.pipeline import selection
    release, finished = threading.Event(), threading.Event()

    def late_decide(**kw):
        release.wait(5)            # still before its model call when the tool stops waiting
        try:
            late = types.SimpleNamespace(chat=lambda **k: types.SimpleNamespace(content="late, see /home/u/x"))
            return tp.RecordingClient(late, kw["tape"]).chat()
        finally:
            finished.set()
    monkeypatch.setattr(selection, "decide", late_decide)
    first = types.SimpleNamespace(chat=lambda **k: types.SimpleNamespace(content="FIRST"))
    t = tp.Tape(mode="record")
    with tp.taped(t):
        tp.RecordingClient(first, t).chat()                   # an earlier selection answer
        pool = fakes.tools.concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(selection.decide, tape=t)
        with pytest.raises(fakes.tools.concurrent.futures.TimeoutError):
            future.result(timeout=0.05)
        pool.shutdown(wait=False)
        release.set()
        assert finished.wait(5)
    queue = t.entries["selection"]
    assert [e["value"] if not tp.is_raise(e["value"]) else "timeout" for e in queue] == ["FIRST", "timeout"]
    assert queue[1]["value"].get("waiting") is True and t.refused == []
    r = _replay_of(t)
    with tp.taped(r):
        assert tp.ReplayClient(r).chat().content == "FIRST"
        pool = fakes.tools.concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(selection.decide, tape=r)
        with pytest.raises(fakes.tools.concurrent.futures.TimeoutError):
            future.result(timeout=1)
        pool.shutdown(wait=False)
    assert r.unplayed() == {} and r.mismatches == []


def test_a_worker_that_never_asks_the_model_leaves_no_empty_entry(fakes, monkeypatch):
    from chat_nextseek.pipeline import selection
    monkeypatch.setattr(selection, "decide", lambda **kw: "no model call")
    t = tp.Tape(mode="record")
    with tp.taped(t):
        pool = fakes.tools.concurrent.futures.ThreadPoolExecutor(max_workers=1)
        assert pool.submit(selection.decide, tape=t).result(timeout=1) == "no model call"
        pool.shutdown(wait=False)
    assert "selection" not in t.entries or t.entries["selection"] == []


def test_an_exception_message_with_a_path_or_email_makes_taped_raise_at_exit(fakes, monkeypatch):
    from chat_nextseek.pipeline.sample_digest import DigestError
    for message, needle in (("could not read /Users/alice/data/x.bam", "path"),
                            ("ask bob@example.org", "email")):
        def failing(*a, _m=message, **k):
            raise DigestError(_m)
        monkeypatch.setattr(fakes.tools, "build_sample_digest", failing)
        t = tp.Tape(mode="record")
        with pytest.raises(ValueError, match=f"digest.*{needle}"):
            with tp.taped(t):
                with pytest.raises(DigestError):
                    fakes.tools.build_sample_digest(object(), ["U1"])
    # a private URL is no refusal: it is kept as "<url>"
    monkeypatch.setattr(fakes.tools, "build_sample_digest",
                        lambda *a, **k: (_ for _ in ()).throw(DigestError("fetch https://private.example.org/y")))
    t = tp.Tape(mode="record")
    with tp.taped(t):
        with pytest.raises(DigestError, match="private.example.org"):
            fakes.tools.build_sample_digest(object(), ["U1"])
    assert t.entries["digest"][0]["value"]["message"] == "fetch <url>" and t.refused == []


# ---- bytes, schema fetches and exceptions of the taped I/O functions ----------------------

def test_bytes_round_trip_through_json_without_a_slash():
    import json
    raw = bytes(range(256)) * 3
    enc = tp.encode({"b": raw})
    assert "/" not in enc["b"]["__bytes__"] and "+" not in enc["b"]["__bytes__"]
    assert tp.decode(json.loads(json.dumps(enc))) == {"b": raw}


def test_the_schema_fetcher_is_a_url_map_not_an_ordered_source():
    import importlib
    assert "chat_nextseek.seqera.nfcore_schema._default_fetcher" not in tp.TAPED
    module, _, attr = tp.SCHEMA_FETCHER.rpartition(".")
    assert hasattr(importlib.import_module(module), attr)


FETCH_404 = ("404 Client Error: Not Found for url: "
             "https://raw.githubusercontent.com/nf-core/fetchngs/1.12.0/assets/schema_input.json")


def _schema_world(monkeypatch, bodies=None):
    """A fetcher that serves nextflow_schema.json, 404s the optional schema_input.json, counts its calls."""
    import requests
    from chat_nextseek.seqera import nfcore_schema as ns
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("/nextflow_schema.json"):
            return (bodies or {}).get(url, b'{"title": "a", "definitions": {}}')
        raise requests.HTTPError(FETCH_404, response=types.SimpleNamespace(status_code=404))
    ns.clear_cache()
    monkeypatch.setattr(ns, "_default_fetcher", fetch)
    return ns, calls


def test_schema_fetches_replay_by_url_with_their_404s_and_never_touch_the_network(fakes, monkeypatch):
    ns, calls = _schema_world(monkeypatch)
    t = tp.Tape(mode="record")
    with tp.taped(t, **SCRUB):
        recorded = ns.get_schema("rnaseq", "3.18.0")
    assert recorded == {"nextflow_schema": {"title": "a", "definitions": {}}} and len(calls) == 2
    assert "_default_fetcher" not in t.entries and len(t.entries["schemas"]) == 2
    assert t.refused == [] and "https://" not in json.dumps(t.to_json())      # no URL on the tape
    r = tp.Tape.from_json(t.to_json())
    monkeypatch.setattr(ns, "_default_fetcher", lambda url: (_ for _ in ()).throw(AssertionError("network")))
    with tp.taped(r):
        assert ns.get_schema("rnaseq", "3.18.0") == recorded
    assert r.mismatches == []
    ns.clear_cache()


def test_the_schema_map_ignores_order_and_the_unplayed_check(fakes, monkeypatch):
    ns, _ = _schema_world(monkeypatch)
    t = tp.Tape(mode="record")
    with tp.taped(t, **SCRUB):
        ns.get_schema("rnaseq", "3.18.0")
        ns.get_schema("sarek", "3.5.1")
    r = tp.Tape.from_json(t.to_json())
    with tp.taped(r):                       # the reverse order, and sarek's twin documents never fetched
        ns.get_schema("sarek", "3.5.1")
    assert r.unplayed() == {}
    ns.clear_cache()


def test_a_schema_the_tape_never_fetched_is_collected_as_a_mismatch(fakes, monkeypatch):
    ns, _ = _schema_world(monkeypatch)
    t = tp.Tape(mode="record")
    with tp.taped(t, **SCRUB):
        ns.get_schema("rnaseq", "3.18.0")
    r = tp.Tape.from_json(t.to_json())
    with pytest.raises(tp.TapeMismatch, match="never fetched"):
        with tp.taped(r):
            with pytest.raises(ns.SchemaFetchError):          # get_schema swallows it into its own error...
                ns.get_schema("rnaseq", "3.19.0")             # ...another revision: another URL
    assert any("never fetched" in m for m in r.mismatches)    # ...but the tape remembered it
    ns.clear_cache()


def test_a_404_message_with_a_url_does_not_reach_refused_and_still_reads_as_not_found(fakes, monkeypatch):
    ns, _ = _schema_world(monkeypatch)
    t = tp.Tape(mode="record")
    with tp.taped(t, **SCRUB):
        ns.get_schema("fetchngs", "1.12.0")
    assert t.refused == []
    answers = list(t.entries["schemas"].values())
    errors = [a for a in answers if isinstance(a, dict) and "raise" in a]
    assert len(errors) == 1 and errors[0]["status_code"] == 404 and "https://" not in errors[0]["message"]
    r = tp.Tape.from_json(t.to_json())
    with tp.taped(r):
        assert ns.get_schema("fetchngs", "1.12.0") == {"nextflow_schema": {"title": "a", "definitions": {}}}
    ns.clear_cache()


def test_a_connection_error_naming_the_request_path_does_not_refuse_the_tape(fakes, monkeypatch):
    """urllib3 names the request path bare ("with url: /nf-core/..."): the tape keeps the error without it."""
    import requests
    ns, _ = _schema_world(monkeypatch)
    msg = ("HTTPSConnectionPool(host='raw.githubusercontent.com', port=443): Max retries exceeded with url: "
           "/nf-core/rnaseq/3.18.0/nextflow_schema.json (Caused by NameResolutionError(\"Failed to resolve "
           "'raw.githubusercontent.com' ([Errno -2] Name or service not known)\"))")
    monkeypatch.setattr(ns, "_default_fetcher", lambda url: (_ for _ in ()).throw(requests.ConnectionError(msg)))
    t = tp.Tape(mode="record")
    with tp.taped(t, **SCRUB):
        with pytest.raises(ns.SchemaFetchError, match="nf-core/rnaseq"):     # the live run sees the real message
            ns.get_schema("rnaseq", "3.18.0")
    assert t.refused == []
    (answer,) = t.entries["schemas"].values()
    assert answer["raise"] == "requests.exceptions.ConnectionError" and "Max retries exceeded" in answer["message"]
    assert "/nf-core" not in json.dumps(answer) and "githubusercontent" not in json.dumps(answer)
    r = tp.Tape.from_json(t.to_json())
    with tp.taped(r):
        with pytest.raises(ns.SchemaFetchError, match="Max retries exceeded"):
            ns.get_schema("rnaseq", "3.18.0")
    assert r.mismatches == []
    ns.clear_cache()


def test_a_schema_error_with_arguments_loses_its_urls_in_them_too(fakes, monkeypatch):
    ns, _ = _schema_world(monkeypatch)
    monkeypatch.setattr(ns, "_default_fetcher",
                        lambda url: (_ for _ in ()).throw(_TwoArgs("fetch failed", url)))
    t = tp.Tape(mode="record")
    with tp.taped(t, **SCRUB):
        with pytest.raises(ns.SchemaFetchError):
            ns.get_schema("rnaseq", "3.18.0")
    assert t.refused == [] and "https://" not in json.dumps(t.to_json())
    ns.clear_cache()


def test_bytes_equal_to_a_committed_fixture_are_kept_as_a_sha_and_served_from_the_fixture(fakes, monkeypatch):
    fixture = tp.FIXTURES / "rnaseq.nextflow_schema.json"
    ns, _ = _schema_world(monkeypatch)
    nf_url, input_url = ns.schema_urls("rnaseq", "3.18.0")
    served = {nf_url: fixture.read_bytes(), input_url: (tp.FIXTURES / "rnaseq.schema_input.json").read_bytes()}
    monkeypatch.setattr(ns, "_default_fetcher", lambda u: served[u])
    t = tp.Tape(mode="record")
    with tp.taped(t, **SCRUB):
        ns.get_schema("rnaseq", "3.18.0")
    answer = t.entries["schemas"]["rnaseq/3.18.0/nextflow_schema.json"]
    assert answer == {"sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(), "fixture": "rnaseq.nextflow_schema.json"}
    r = tp.Tape.from_json(t.to_json())
    with tp.taped(r):
        assert ns.get_schema("rnaseq", "3.18.0")["nextflow_schema"] == ns.normalise(json.loads(fixture.read_bytes()))
    # a fixture edited since the recording no longer matches its sha: re-record
    r2 = tp.Tape.from_json(t.to_json())
    r2.entries["schemas"]["rnaseq/3.18.0/nextflow_schema.json"]["sha256"] = "0" * 64
    with pytest.raises(tp.TapeMismatch, match="fixture"):
        with tp.taped(r2):
            ns.get_schema("rnaseq", "3.18.0")
    ns.clear_cache()


def test_taped_clears_the_schema_cache_in_both_modes(fakes, monkeypatch):
    from chat_nextseek.seqera import nfcore_schema as ns
    for mode in ("record", "replay"):
        ns._CACHE[("p", "1")] = {"nextflow_schema": {}}
        t = tp.Tape(mode="record") if mode == "record" else tp.Tape.from_json({"entries": {}})
        with tp.taped(t):
            assert ns._CACHE == {}
        assert ns._CACHE == {}


def test_an_exception_a_taped_function_raised_is_taped_and_raised_again_in_replay(fakes, monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("ena was slow")
    monkeypatch.setattr(fakes.tools, "lookup_run_sizes", boom)
    t = tp.Tape(mode="record")
    with tp.taped(t, **SCRUB):
        with pytest.raises(TimeoutError, match="ena was slow"):
            fakes.tools.lookup_run_sizes(object(), ["SRR1"])
    r = tp.Tape.from_json(t.to_json())
    with tp.taped(r):
        with pytest.raises(TimeoutError, match="ena was slow"):
            fakes.tools.lookup_run_sizes(object(), ["SRR1"])


def test_an_http_status_survives_the_tape():
    import requests
    spec = tp.exception_spec(requests.HTTPError("404", response=types.SimpleNamespace(status_code=404)))
    again = tp.rebuild_exception(spec)
    assert isinstance(again, requests.HTTPError) and again.response.status_code == 404


def test_the_live_value_is_handed_back_as_is_not_round_tripped_through_the_tape(fakes, monkeypatch):
    answer = ("SRR1", 5)                       # a tuple would come back from the tape as a list
    monkeypatch.setattr(fakes.tools, "lookup_run_sizes", lambda *a, **k: answer)
    t = tp.Tape(mode="record")
    with tp.taped(t):
        assert fakes.tools.lookup_run_sizes(object(), ["SRR1"]) is answer
    assert t.entries["lookup_run_sizes"][0]["value"] == ["SRR1", 5]


def test_a_raised_message_is_kept_without_its_request_path_or_host(fakes, monkeypatch):
    msg = ("HTTPSConnectionPool(host='nextseek.example.org', port=443): Max retries exceeded with url: "
           "/api/v1/samples (Caused by ConnectTimeoutError())")

    def down(*a, **k):
        raise ConnectionError(msg)
    monkeypatch.setattr(fakes.tools, "fetch_reporter_metadata", down)
    t = tp.Tape(mode="record")
    with tp.taped(t, **SCRUB):
        with pytest.raises(ConnectionError, match="api/v1"):      # the live run sees the real message
            fakes.tools.fetch_reporter_metadata(object(), ["U1"])
    kept = t.entries["fetch_reporter_metadata"][0]["value"]
    assert t.refused == [] and "/api" not in kept["message"] and "example.org" not in kept["message"]


def test_a_filled_entry_rewrites_urls_before_it_is_judged():
    t = tp.Tape(mode="record")
    entry = t.reserve("selection")
    t.fill(entry, "reads at https://nextseek.example.org/data_files/3 and " + "https://ftp.sra.ebi.ac.uk/vol1/x.fastq.gz")
    assert t.entries["selection"][0]["value"] == "reads at <url> and https://ftp.sra.ebi.ac.uk/vol1/x.fastq.gz"
    assert t.refused == []
