from nessie_tests import http_driver as hd


def _seq_get_progress(sequence):
    calls = {"n": 0}
    def get_progress(task_id):
        i = min(calls["n"], len(sequence) - 1)
        calls["n"] += 1
        return sequence[i]
    get_progress.calls = calls
    return get_progress


def _post(task_id="t1", session_id="s1"):
    def post_query(body):
        post_query.body = body
        return {"task_id": task_id, "session_id": session_id}
    return post_query


NO_ROUTE = {"status": "running", "progress": []}
ROUTED = {"status": "running", "progress": [
    {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None, "source": "baml", "reasoning": ""}}]}
DONE = {"status": "completed", "progress": ROUTED["progress"] + [
    {"event": "query_complete", "data": {"reply": "ok", "debug": {"parser_plan": {"mode": "new_search"}}}}]}


def test_route_tier_stops_at_route_decided():
    gp = _seq_get_progress([NO_ROUTE, ROUTED, DONE])
    res = hd.drive("q", tier="route", post_query=_post(), get_progress=gp,
                   sleep=lambda s: None, clock=lambda: 0.0)
    assert res.aborted_early is True
    assert res.route_obs.route == "nextseek_query"
    assert gp.calls["n"] == 2  # stopped as soon as route appeared, not to completion


def test_full_tier_polls_to_completion():
    gp = _seq_get_progress([NO_ROUTE, ROUTED, DONE])
    res = hd.drive("q", tier="full", post_query=_post(), get_progress=gp,
                   sleep=lambda s: None, clock=lambda: 0.0)
    assert res.aborted_early is False
    assert res.status == "completed"
    assert res.route_obs.parser_mode == "new_search"


def test_body_shape_and_session_threading():
    p = _post()
    hd.drive("hello", tier="route", post_query=p, get_progress=_seq_get_progress([ROUTED]),
             session_id="sess9", sleep=lambda s: None, clock=lambda: 0.0)
    assert p.body == {"query": "hello", "mode": "standard", "session_id": "sess9",
                      "fresh_session": True}


# ── per-case session isolation ────────────────────────────────────────────

def test_force_new_requests_a_fresh_session():
    p = _post()
    hd.drive("hello", tier="route", post_query=p, get_progress=_seq_get_progress([ROUTED]),
             force_new=True, sleep=lambda s: None, clock=lambda: 0.0)
    assert p.body == {"query": "hello", "mode": "standard", "force_new": True,
                      "fresh_session": True}


def test_force_new_is_ignored_once_a_session_exists():
    # Continuation turns must stay in the case's session, never open a new one.
    p = _post()
    hd.drive("hello", tier="route", post_query=p, get_progress=_seq_get_progress([ROUTED]),
             session_id="sess9", force_new=True, sleep=lambda s: None, clock=lambda: 0.0)
    assert p.body == {"query": "hello", "mode": "standard", "session_id": "sess9",
                      "fresh_session": True}


def test_fresh_session_is_sent_by_default_on_every_turn():
    """The CC memory layer is USER-scoped, so `force_new` does not cover it.

    `_session_metas` filters `ChatSession.objects.filter(user=user)`
    (cc_assistant.py:169), so a brand-new ChatSession still gets a rendered
    ~/.claude/CLAUDE.md distilled from that user's recent CC sessions -- in a
    benchmark, from OTHER CASES OF THE SAME RUN.

    Measured, not hypothetical: across the two 2026-08-06 paired runs all 152 CC
    arms carried digests of up to 5 other arms while every NS arm carried none,
    so the paired comparison was not like-for-like. Five `refrec` variants were
    handed "Out of the 408 NHP samples, 46 samples are CD8-depleted" before being
    asked it.

    Asserted on a CONTINUATION turn as well as a seed, because the flag has to
    ride on every turn: the server default is False (models_api.py:19) and a turn
    that omits it re-injects the memory the seed turn suppressed.
    """
    p = _post()
    hd.drive("hello", tier="route", post_query=p, get_progress=_seq_get_progress([ROUTED]),
             sleep=lambda s: None, clock=lambda: 0.0)
    assert p.body["fresh_session"] is True

    p = _post()
    hd.drive("follow up", tier="route", post_query=p,
             get_progress=_seq_get_progress([ROUTED]),
             session_id="sess9", sleep=lambda s: None, clock=lambda: 0.0)
    assert p.body["fresh_session"] is True


def test_fresh_session_can_be_turned_off_to_measure_the_memory_feature():
    """Opt-out exists so the memory layer itself remains testable.

    Omitted entirely rather than sent as False: the server default is already
    False, and sending it would make the wire body differ from what a normal
    client sends when it wants memory.
    """
    p = _post()
    hd.drive("hello", tier="route", post_query=p, get_progress=_seq_get_progress([ROUTED]),
             fresh_session=False, sleep=lambda s: None, clock=lambda: 0.0)
    assert "fresh_session" not in p.body


# ── transient socket errors must not kill a case ──────────────────────────

def _flaky_get_progress(sequence):
    """Yield items; an item that is an Exception instance gets raised."""
    calls = {"n": 0}
    def get_progress(task_id):
        i = min(calls["n"], len(sequence) - 1)
        calls["n"] += 1
        item = sequence[i]
        if isinstance(item, Exception):
            raise item
        return item
    get_progress.calls = calls
    return get_progress


def test_transient_poll_error_is_retried_not_raised():
    gp = _flaky_get_progress([TimeoutError("timed out"), NO_ROUTE, DONE])
    res = hd.drive("q", tier="full", post_query=_post(), get_progress=gp,
                   sleep=lambda s: None, clock=lambda: 0.0)
    assert res.status == "completed"
    assert res.poll_errors == 1


def test_a_down_endpoint_still_raises_so_it_reads_as_infra():
    """Retrying must not paper over a dead endpoint.

    A blip is worth absorbing; a genuinely unreachable server should surface as
    a case `error` (infrastructure) rather than a silent timeout that the suite
    might mistake for a product failure.
    """
    import pytest
    gp = _flaky_get_progress([ConnectionError("endpoint down")])
    with pytest.raises(ConnectionError):
        hd.drive("q", tier="full", post_query=_post(), get_progress=gp,
                 sleep=lambda s: None, clock=lambda: 0.0)
    assert gp.calls["n"] == hd.MAX_CONSECUTIVE_POLL_ERRORS


def test_blips_interspersed_with_successes_reset_the_budget():
    seq = []
    for _ in range(3):
        seq += [TimeoutError("blip"), NO_ROUTE]
    seq.append(DONE)
    gp = _flaky_get_progress(seq)
    res = hd.drive("q", tier="full", post_query=_post(), get_progress=gp,
                   sleep=lambda s: None, clock=lambda: 0.0)
    assert res.status == "completed"
    assert res.poll_errors == 3
