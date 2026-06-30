"""Hermetic: fresh_session on QueryRequest (pydantic only; no Django settings)."""
from nextseek_api.assistant.models_api import QueryRequest


def test_default_is_false():
    req = QueryRequest(query="hi", mode="standard")
    assert req.fresh_session is False


def test_accepts_true():
    req = QueryRequest(query="hi", mode="standard", fresh_session=True)
    assert req.fresh_session is True
