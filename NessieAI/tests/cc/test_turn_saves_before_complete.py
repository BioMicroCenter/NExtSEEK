from nextseek_api.assistant.session_adapter import DictSessionAdapter


class _FakeSession:
    def __init__(self):
        self.results_history, self.last_debug, self.extra_state = [], {}, {}
        self.saved = 0
        self.db = {"results_history": [], "last_debug": {}, "extra_state": {}}

    def refresh_from_db(self, fields=None):
        for f in (fields or self.db):
            setattr(self, f, self.db[f])

    def save(self, update_fields=None):
        self.saved += 1
        for f in ("results_history", "last_debug", "extra_state"):
            self.db[f] = getattr(self, f)


def test_reload_sees_a_bundle_saved_after_construction():
    s = _FakeSession()
    a = DictSessionAdapter(s)
    s.db["results_history"] = [{"id": 1, "user_query": "earlier"}]
    a.reload()
    assert [b["id"] for b in a.get("results_history")] == [1]


def test_ns_query_complete_is_sent_after_the_session_is_saved():
    from NessieAI.cc import turn as cc_turn
    order = []
    class _A:
        def save(self): order.append("save")
    def send(ev, data): order.append(ev)
    wrapped = cc_turn._save_before_complete(send, _A())
    wrapped("agent_started", {})
    wrapped("query_complete", {"reply": "x"})
    assert order == ["agent_started", "save", "query_complete"]


def test_start_task_reloads_first_and_saves_before_the_ns_query_complete(monkeypatch):
    """Wiring: the turn reads fresh state before routing and the NS engine's send_event is the wrapped one."""
    from types import SimpleNamespace
    from NessieAI.cc import turn as cc_turn
    from NessieAI.router import router as cc_router

    order = []

    class _Thread:
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()

    class _Adapter(dict):
        def reload(self):
            order.append("reload")

        def save(self):
            order.append("save")

    def _decide(*a, **k):
        order.append("route")
        return SimpleNamespace(route=cc_router.ROUTE_NS, model_class=None, model_id=None,
                               source="forced", reasoning="test")

    def _fake_run_query(adapter, config, query, send_event, credentials=None, **kw):
        send_event("query_complete", {"reply": "ok"})

    monkeypatch.setattr(cc_turn, "threading", SimpleNamespace(Thread=_Thread))
    monkeypatch.setattr(cc_turn, "_select_chat_config", lambda request, r: SimpleNamespace())
    monkeypatch.setattr(cc_turn, "_eval_config", lambda config, user, r: config)
    monkeypatch.setattr(cc_turn, "_decide_route", _decide)
    monkeypatch.setattr(cc_turn, "_record_ledger_row", lambda *a, **k: None)
    monkeypatch.setattr(cc_turn, "_auto_title_if_unset", lambda *a, **k: None)
    monkeypatch.setattr(cc_turn, "_emit_ns_run_root", lambda *a, **k: None)
    monkeypatch.setattr(cc_turn, "run_query", _fake_run_query)

    cc_turn.start_task(
        SimpleNamespace(user=SimpleNamespace(username="u", is_superuser=False)),
        SimpleNamespace(query="q", mode="standard", max_turn_length_s=None), force_cc=False,
        chat_session=SimpleNamespace(extra_state={}, session_id="s-1", results_history=[]),
        query_task=SimpleNamespace(task_id="t-1"),
        send_event=lambda ev, data: order.append(ev),
        adapter=_Adapter(), api_user="caller", api_pass="caller-pw",
        resolved_session_id="s-1",
    )

    assert order.index("reload") < order.index("route")
    assert order.index("save") < order.index("query_complete")
