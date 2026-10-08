"""Round 7 (T4): inside an op a first try follows the measured speed of its agent's last 20 first tries."""
from __future__ import annotations

import json

import pytest

from chat_nextseek import call_scope
from chat_nextseek.schemas import call_speed
from chat_nextseek.schemas.schema_helper import call_llm_structured

from .test_call_scope_deadline import FLASH, OPUS, PRO, SONNET, _Config, _Plan, clock, run  # noqa: F401


def _row(agent, model, secs, outcome="ok", **kw):
    r = {"agent": agent, "model": model, "attempt": 1, "outcome": outcome, "elapsed_ms": round(secs * 1000),
         "timeout_seconds": secs}
    r.update(kw)
    return json.dumps(r)


def _ledger(tmp_path, lines):
    (tmp_path / "llm_calls.jsonl").write_text("\n".join(lines) + "\n")
    return str(tmp_path)


def _parser(config):
    return call_llm_structured(config, "q", _Plan, system="s", client=config.anth, model_name=OPUS,
                               agent_label="parser")


def _graph(config):
    return call_llm_structured(config, "q", _Plan, system="s", client=config.gcp, model_name=FLASH,
                               agent_label="graph", log_label="graph_agent")


def _op(s=90):
    return call_scope.scope(deadline_s=s, op=True)


def _stall(run):
    run[1].update({OPUS: "stall", FLASH: "stall", PRO: 6.0, SONNET: 6.0})
    return run[0]


def test_19_rows_keep_the_constants(run, tmp_path):
    d = _ledger(tmp_path, [_row("parser", OPUS, 6)] * 19 + [_row("graph_agent", FLASH, 6)] * 19)
    w = _stall(run)
    with _op():
        _parser(_Config(d))
    with _op():
        _graph(_Config(d))
    assert w[0] == (OPUS, 20) and w[2] == (FLASH, 60)


@pytest.mark.parametrize("med,parser,graph", [(6, 24, 30), (3, 20, 20), (15, 50, 60)])
def test_20_rows_set_the_window(run, tmp_path, med, parser, graph):
    d = _ledger(tmp_path, [_row("parser", OPUS, med)] * 20 + [_row("graph_agent", FLASH, med)] * 20)
    w = _stall(run)
    with _op():
        _parser(_Config(d))
    with _op():
        _graph(_Config(d))
    assert w[0] == (OPUS, parser) and w[2] == (FLASH, graph)


def test_a_slow_graph_median_still_leaves_the_move_reserve(run, tmp_path, clock):
    d = _ledger(tmp_path, [_row("graph_agent", FLASH, 15)] * 20)
    w = _stall(run)
    with _op(60):
        _graph(_Config(d))
    assert w[0] == (FLASH, 40)


def test_only_primary_first_tries_count(run, tmp_path):
    good = [_row("parser", OPUS, 6)] * 20
    noise = [_row("parser", OPUS, 40, attempt=2), _row("parser", OPUS, 40, fallback_from=OPUS),
             _row("parser", OPUS, 40, outcome="deadline"), _row("parser", PRO, 40),
             _row("multi_parser", OPUS, 40), _row("parser", OPUS, 40, outcome="timeout", deadline_capped=True),
             "{torn"]
    d = _ledger(tmp_path, good[:10] + noise + good[10:])
    assert call_speed.recent_first_tries(d, "parser", OPUS) == [6.0] * 20
    w = _stall(run)
    with _op():
        _parser(_Config(d))
    assert w[0] == (OPUS, 24)


def test_a_timeout_counts_at_its_window(tmp_path):
    d = _ledger(tmp_path, [_row("parser", OPUS, 20, outcome="timeout")] * 20)
    assert call_speed.median_first_try(d, "parser", OPUS) == 20.0


def test_an_ns_turn_ignores_the_ledger(run, tmp_path):
    d = _ledger(tmp_path, [_row("parser", OPUS, 3)] * 20 + [_row("graph_agent", FLASH, 3)] * 20)
    w = _stall(run)
    _parser(_Config(d))
    _graph(_Config(d))
    assert w[0] == (OPUS, 50)
    assert FLASH in [m for m, _ in w] and [x for x in w if x[0] == FLASH][0][1] == 60


def test_the_moved_call_keeps_moved_s(run, tmp_path):
    d = _ledger(tmp_path, [_row("parser", OPUS, 6)] * 20)
    w = _stall(run)
    with _op():
        _parser(_Config(d))
    assert w[1][0] == PRO and w[1][1] <= 60


def test_a_missing_ledger_keeps_the_constant_and_never_raises(run, tmp_path):
    w = _stall(run)
    with _op():
        _parser(_Config(str(tmp_path / "nope")))
    assert w[0] == (OPUS, 20)
    assert call_speed.recent_first_tries(None, "parser", OPUS) == []


def test_the_record_carries_the_median(run, tmp_path):
    d = _ledger(tmp_path, [_row("parser", OPUS, 6)] * 20)
    run[1].update({OPUS: 5.0})
    with _op():
        _parser(_Config(d))
    last = json.loads((tmp_path / "llm_calls.jsonl").read_text().splitlines()[-1])
    assert last["op_speed_median_s"] == 6.0
