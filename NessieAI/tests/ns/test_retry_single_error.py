"""run_retry sends one query_error when the orchestrator already sent one and raised."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from NessieAI.ns import retry


def _run(run_query):
    events = []
    adapter = MagicMock()
    req = SimpleNamespace(mode="standard", query="q")
    with patch.object(retry, "_get_orchestrator", return_value=(run_query, run_query)), \
            patch.object(retry.settings, "NEXTSEEK_CHAT_CONFIG", object(), create=True):
        retry.run_retry(adapter=adapter, req=req, send_event=lambda t, d: events.append((t, d)),
                        api_user="u", api_pass="p", session_id_str="s")
    return events


def test_one_query_error_when_the_orchestrator_raises():
    def run_query(adapter, config, query, send_event, **kw):
        send_event("query_error", {"error": "503 UNAVAILABLE"})
        raise RuntimeError("boom")
    errs = [e for e in _run(run_query) if e[0] == "query_error"]
    assert len(errs) == 1 and errs[0][1]["error"] == "503 UNAVAILABLE"


def test_generic_error_when_none_was_sent():
    def run_query(adapter, config, query, send_event, **kw):
        raise RuntimeError("boom")
    errs = [e for e in _run(run_query) if e[0] == "query_error"]
    assert [e[1]["error"] for e in errs] == ["Internal pipeline error"]
