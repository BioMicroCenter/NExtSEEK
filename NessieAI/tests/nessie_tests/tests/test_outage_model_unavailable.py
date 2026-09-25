"""A turn that ended because the AI models were unavailable is an outage, as before.

Since 2026-09-25 (fix 5) an NS turn whose models did not answer no longer replies with
the raw ``All provider fallbacks exhausted ...`` message: the reply is the operator's
plain text and the ``query_error`` event says ``reason: "model_unavailable"``, with the
raw message in ``detail``. The Container-CC route uses the same reason. The detector
must keep classifying those turns the way it classified the old ones, and keep
recognising the old text in stored runs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from NessieAI.tests.nessie_tests import evaluate, outage

OLD_REPLY = ("**The request could not be completed.**\n\nAll provider fallbacks exhausted � agent "
             "'parser': ServiceUnavailableException")
NS_TRIED_TWO = ("The AI models we use were unavailable (we tried a second one as well), so I could not finish "
                "your question. Please ask again in a few minutes.")
NS_NO_SECOND = ("The AI models we use were unavailable, so I could not finish your question. Please ask again "
                "in a few minutes.")


def test_the_old_reply_is_still_an_outage():
    assert outage.is_provider_outage(OLD_REPLY) is True


@pytest.mark.parametrize("reply", [NS_TRIED_TWO, NS_NO_SECOND], ids=["tried-two", "no-second"])
def test_the_new_ns_reply_is_an_outage(reply):
    assert outage.is_provider_outage(reply) is True
    assert evaluate.classify_turn_status(False, reply) == "error"
    assert evaluate.classify_turn_status(True, reply) == "error"


@pytest.mark.parametrize("item", [
    {"error": "The AI models we use were unavailable.", "reason": "model_unavailable", "detail": "503"},
    {"error": "Claude could not reach its model.", "reason": "model_unavailable", "detail": "API Error: 529"},
    {"event": "query_error", "data": {"reason": "model_unavailable", "error": "x"}},
], ids=["ns-event-data", "cc-event-data", "progress-event"])
def test_a_query_error_with_reason_model_unavailable_is_an_outage(item):
    assert outage.is_provider_outage(item) is True


def test_a_dict_still_carrying_the_old_text_is_an_outage():
    assert outage.is_provider_outage({"error": "All provider fallbacks exhausted: agent 'x': 503"}) is True


@pytest.mark.parametrize("item", [
    {"error": "Container-CC turn exceeded the 600s limit and was stopped.", "reason": "exec_timeout"},
    {"reason": "something_else"},
    {"event": "query_error", "data": {"error": "Unrecoverable LLM error"}},
    {},
    None,
    42,
    "found 139 samples",
    # The planner's own failure replies are not an outage: they are an unsupported plan.
    ("The AI model that plans the search did not respond in time, so I have not run your question. This is a "
     "temporary problem on our side, not a problem with your question. Please ask again in a minute."),
], ids=["cc-timeout", "other-reason", "fatal-400", "empty", "none", "int", "answer", "planner-timeout"])
def test_everything_else_is_not_an_outage(item):
    assert outage.is_provider_outage(item) is False


def test_the_reason_value_matches_the_product():
    """The harness is dependency-free and keeps its own copy of the reason; it must be the
    product's. Skipped where the product package is not importable (the host lane)."""
    failure_replies = pytest.importorskip("chat_nextseek.failure_replies")

    assert outage.MODEL_UNAVAILABLE_REASON == failure_replies.MODEL_UNAVAILABLE_REASON


def test_the_ns_text_marker_is_the_products_reply():
    failure_replies = pytest.importorskip("chat_nextseek.failure_replies")

    for reply in (failure_replies.MODELS_UNAVAILABLE_TRIED_TWO_REPLY, failure_replies.MODELS_UNAVAILABLE_REPLY):
        assert outage.is_provider_outage(reply) is True


def test_the_outage_reason_still_names_the_old_marker_and_the_new_reason():
    assert outage.PROVIDER_OUTAGE_MARKER in outage.OUTAGE_REASON
    assert outage.MODEL_UNAVAILABLE_REASON in outage.OUTAGE_REASON
    assert outage.OUTAGE_REASON.startswith("provider outage")


CC_REPLIES = [
    "The AI model was unavailable during this turn, so I could not finish your question. Please ask again.",
    "The AI model was unavailable during this turn (it stopped part way). Please ask again in a few minutes.",
]


@pytest.mark.parametrize("reply", CC_REPLIES, ids=["cc-a", "cc-b"])
def test_the_cc_unavailability_text_is_an_outage(reply):
    """Both CC variants share this prefix; text-only callers classify it with no event data."""
    from NessieAI.tests.nessie_tests import export

    assert outage.is_provider_outage(reply) is True
    assert evaluate.classify_turn_status(False, reply) == "error"
    assert export.classify_error(reply) == export.ERROR_OUTAGE


def test_the_ns_text_is_an_outage_to_classify_error_too():
    from NessieAI.tests.nessie_tests import export

    assert export.classify_error(NS_NO_SECOND) == export.ERROR_OUTAGE


def test_the_markers_are_the_two_engines_prefixes():
    assert set(outage.MODEL_UNAVAILABLE_REPLY_MARKERS) == {
        "The AI models we use were unavailable",
        "The AI model was unavailable during this turn",
    }


# --------------------------------------------------------------------------- #
# A turn that ended only in `query_error`.
#
# A Container-CC turn whose model was unavailable sends no `query_complete` at all:
# its only terminal event is the `query_error` carrying `reason: "model_unavailable"`,
# so the reply is None and a scorer reading the reply alone saw a product red. The
# turn's last `query_error` data is read too, reason first and then its texts.
# --------------------------------------------------------------------------- #

CC_UNAVAILABLE = {"error": CC_REPLIES[0], "reason": "model_unavailable",
                  "detail": "API Error: 529 overloaded", "agent": "container_cc",
                  "model_fallback": []}
CC_TIME_LIMIT = {"error": "Container-CC turn exceeded the 600s limit and was stopped.",
                 "reason": "exec_timeout", "agent": "container_cc"}


@pytest.mark.parametrize("passed", [False, True], ids=["criteria-failed", "criteria-passed"])
def test_a_turn_that_ended_only_in_a_model_unavailable_query_error_is_error(passed):
    assert evaluate.classify_turn_status(passed, None, CC_UNAVAILABLE) == "error"


def test_the_reason_decides_even_when_no_text_carries_a_marker():
    data = {"error": "Something went wrong.", "reason": "model_unavailable", "detail": "HTTP 503"}
    assert evaluate.classify_turn_status(False, None, data) == "error"


@pytest.mark.parametrize("data", [
    {"error": "Internal pipeline error", "detail": "All provider fallbacks exhausted: agent 'graph': 503"},
    {"error": CC_REPLIES[1]},
    {"error": NS_NO_SECOND, "agent": "parser"},
], ids=["old-phrase-in-detail", "cc-text-no-reason", "ns-text-no-reason"])
def test_without_the_reason_the_query_error_texts_are_read(data):
    assert evaluate.classify_turn_status(False, None, data) == "error"


@pytest.mark.parametrize("data", [
    CC_TIME_LIMIT,
    {"error": "Internal pipeline error", "agent": "unknown"},
    {"error": "Could not resolve your SEEK project. Please try again shortly.", "agent": "container_cc"},
    {},
    None,
], ids=["cc-time-limit", "internal-error", "project-resolution", "empty", "none"])
def test_a_query_error_that_is_not_an_outage_leaves_the_status_alone(data):
    assert evaluate.classify_turn_status(False, None, data) == "failed"
    assert evaluate.classify_turn_status(True, "found 139 samples", data) == "passed"


def test_an_ns_outage_that_still_ends_in_a_query_complete_reply_is_error_as_before():
    assert evaluate.classify_turn_status(False, NS_TRIED_TWO) == "error"
    assert evaluate.classify_turn_status(False, NS_TRIED_TWO, None) == "error"


def test_last_query_error_is_the_data_of_the_turn_s_last_query_error_event():
    payload = {"progress": [
        {"event": "query_error", "data": {"error": "first"}},
        {"event": "search_complete", "data": {"ok": True}},
        {"event": "query_error", "data": CC_UNAVAILABLE}]}
    assert evaluate.last_query_error(payload) == CC_UNAVAILABLE
    assert evaluate.last_query_error({"progress": [
        {"event": "query_complete", "data": {"reply": "r"}}]}) is None
    assert evaluate.last_query_error({}) is None


# The same, through the runner: the scorer that decides the manifest entry.

_ROUTED_CC = {"event": "route_decided", "data": {"route": "container_cc", "model_class": "opus",
                                                  "source": "baml", "reasoning": ""}}
_ROUTED_NS = {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None,
                                                  "source": "baml", "reasoning": ""}}
CC_OUTAGE_ONLY_ERROR = {"status": "error", "progress": [
    _ROUTED_CC, {"event": "query_error", "data": CC_UNAVAILABLE}]}
CC_TIMED_OUT = {"status": "error", "progress": [
    _ROUTED_CC, {"event": "query_error", "data": CC_TIME_LIMIT}]}
# The NS fatal handler sends the query_error and then answers with the plain text.
NS_OUTAGE_BOTH = {"status": "completed", "progress": [
    _ROUTED_NS,
    {"event": "query_error", "data": {"error": NS_TRIED_TWO, "reason": "model_unavailable",
                                      "detail": "All provider fallbacks exhausted: agent 'parser': 503",
                                      "agent": "parser", "fatal": True}},
    {"event": "query_complete", "data": {"reply": NS_TRIED_TWO, "debug": {"fatal_error": "503"}}}]}


def _variant(vid, *criteria, turns=1):
    from NessieAI.tests.e2e.catalog import Turn, Variant
    return Variant(family="system_question", id=vid, name="n",
                   tags=["nessie", "overlay", "full"], requires_env=[],
                   turns=[Turn(label=f"t{i}", query=f"q{i}", pass_criteria=list(criteria))
                          for i in range(turns)])


def _run_suite(tmp_path, monkeypatch, variant, payloads, **kw):
    from NessieAI.tests.nessie_tests import runner

    payloads = iter(payloads)
    posted = []

    def post_query(body):
        posted.append(body)
        return {"task_id": f"t{len(posted)}", "session_id": "s"}

    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [variant])
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        corpus_path=Path(__file__).resolve().parents[1] / "corpus.json", out_dir=tmp_path,
        post_query=post_query, get_progress=lambda tid: next(payloads),
        sleep=lambda s: None, clock=lambda: 0.0, **kw)
    return runner, m, posted


_REPLIED = {"field": "last_reply", "op": "nonempty"}


def test_a_cc_turn_that_ended_only_in_a_model_unavailable_query_error_is_an_outage(tmp_path, monkeypatch):
    runner, m, posted = _run_suite(tmp_path, monkeypatch, _variant("cc.down", _REPLIED, turns=2),
                                   [CC_OUTAGE_ONLY_ERROR, CC_OUTAGE_ONLY_ERROR])

    entry = next(e for e in m.entries if e.id == "cc.down")
    assert entry.status == "error" and entry.outage is True, "a CC outage was scored as a product red"
    assert entry.reason == evaluate.OUTAGE_REASON
    assert entry.failed_criteria == []
    assert runner.gate_failed(m) == 0
    assert [e.id for e in runner.classify_entries(m)["outage"]] == ["cc.down"]
    assert len(posted) == 1, "the case kept driving turns after the model was unavailable"


def test_a_cc_turn_stopped_at_its_time_limit_is_still_a_red(tmp_path, monkeypatch):
    """Non-vacuity: only the model_unavailable reason (or an outage text) is exempt."""
    runner, m, _ = _run_suite(tmp_path, monkeypatch, _variant("cc.slow", _REPLIED), [CC_TIMED_OUT])

    entry = next(e for e in m.entries if e.id == "cc.slow")
    assert entry.status == "failed" and entry.outage is False
    assert runner.gate_failed(m) == 1


def test_an_ns_outage_that_ends_in_both_events_is_still_an_outage(tmp_path, monkeypatch):
    runner, m, _ = _run_suite(tmp_path, monkeypatch, _variant("ns.down", _REPLIED), [NS_OUTAGE_BOTH])

    entry = next(e for e in m.entries if e.id == "ns.down")
    assert entry.status == "error" and entry.outage is True
    assert runner.gate_failed(m) == 0


def test_the_turn_payload_keeps_the_query_error(tmp_path):
    """engine_compare reads the stored payloads, so the event that says why travels with them."""
    import json

    from NessieAI.tests.nessie_tests import runner

    entry = runner.run_case(
        _variant("cc.down", _REPLIED), tier="full",
        post_query=lambda body: {"task_id": "t1", "session_id": "s"},
        get_progress=lambda tid: CC_OUTAGE_ONLY_ERROR, payload_dir=tmp_path / "payloads",
        sleep=lambda s: None, clock=lambda: 0.0)

    assert entry.outage is True
    doc = json.loads((tmp_path / "payloads" / "cc.down" / "t0.json").read_text())
    assert doc["query_error"] == CC_UNAVAILABLE
    assert doc["query_complete"] == {}


def test_a_turn_that_sent_no_query_error_stores_none(tmp_path):
    import json

    from NessieAI.tests.nessie_tests import runner

    runner.run_case(
        _variant("ns.down", _REPLIED), tier="full",
        post_query=lambda body: {"task_id": "t1", "session_id": "s"},
        get_progress=lambda tid: {"status": "completed", "progress": [
            _ROUTED_NS, {"event": "query_complete", "data": {"reply": "408 samples"}}]},
        payload_dir=tmp_path / "payloads", sleep=lambda s: None, clock=lambda: 0.0)

    doc = json.loads((tmp_path / "payloads" / "ns.down" / "t0.json").read_text())
    assert doc["query_error"] is None


def test_a_consistency_member_that_ended_only_in_a_query_error_outages_the_group(tmp_path, monkeypatch):
    from NessieAI.tests.nessie_tests import runner

    group = {"id": "cons.cc_down", "queries": ["a", "b"], "assert": {"same_count": True}}
    monkeypatch.setattr(runner.corpus, "select", lambda *a, **k: [])
    monkeypatch.setattr("NessieAI.tests.nessie_tests.corpus.load_consistency_groups", lambda p: [group])

    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="full", scope="all",
        corpus_path=Path(__file__).resolve().parents[1] / "corpus.json", out_dir=tmp_path,
        post_query=lambda body: {"task_id": "t", "session_id": "s"},
        get_progress=lambda tid: CC_OUTAGE_ONLY_ERROR,
        sleep=lambda s: None, clock=lambda: 0.0, run_consistency=True)

    entry = next(e for e in m.entries if e.id == "cons.cc_down")
    assert entry.status == "error" and entry.outage is True
    assert not any("could not be resolved" in fc for fc in entry.failed_criteria)
