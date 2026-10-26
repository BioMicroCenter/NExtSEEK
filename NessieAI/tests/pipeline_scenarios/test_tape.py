import dataclasses

import pytest

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
