"""NEXTSEEK base-URL resolution: internal transport URL wins over public URL.

chat_nextseek's REST self-calls execute inside the nextseek container, which
listens on :8000 regardless of the published host port. NEXTSEEK_BASE_URL is
the PUBLIC url (derives from NEXTSEEK_HOSTNAME = host-published port, bumped
when 8000 is busy on the host); NEXTSEEK_INTERNAL_BASE_URL is the
container-internal transport URL rendered by startup. ChatConfig must prefer
the internal URL when present, else fall back to the public one (Step 7d
greenfield bug: self-calls to the bumped host port -> connection refused).
"""
from __future__ import annotations

from chat_nextseek.config import _resolve_nextseek_base_url


def test_internal_url_preferred_over_public(monkeypatch):
    monkeypatch.setenv("NEXTSEEK_INTERNAL_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("NEXTSEEK_BASE_URL", "http://127.0.0.1:8001")
    assert _resolve_nextseek_base_url() == "http://127.0.0.1:8000"


def test_falls_back_to_public_when_internal_unset(monkeypatch):
    monkeypatch.delenv("NEXTSEEK_INTERNAL_BASE_URL", raising=False)
    monkeypatch.setenv("NEXTSEEK_BASE_URL", "https://nextseek-dev.mit.edu")
    assert _resolve_nextseek_base_url() == "https://nextseek-dev.mit.edu"


def test_falls_back_when_internal_empty(monkeypatch):
    monkeypatch.setenv("NEXTSEEK_INTERNAL_BASE_URL", "")
    monkeypatch.setenv("NEXTSEEK_BASE_URL", "https://nextseek-dev.mit.edu")
    assert _resolve_nextseek_base_url() == "https://nextseek-dev.mit.edu"


def test_none_when_neither_set(monkeypatch):
    monkeypatch.delenv("NEXTSEEK_INTERNAL_BASE_URL", raising=False)
    monkeypatch.delenv("NEXTSEEK_BASE_URL", raising=False)
    assert _resolve_nextseek_base_url() is None


def test_trailing_slash_stripped_from_either_source(monkeypatch):
    monkeypatch.setenv("NEXTSEEK_INTERNAL_BASE_URL", "http://127.0.0.1:8000/")
    monkeypatch.setenv("NEXTSEEK_BASE_URL", "http://127.0.0.1:8001/")
    assert _resolve_nextseek_base_url() == "http://127.0.0.1:8000"
    monkeypatch.delenv("NEXTSEEK_INTERNAL_BASE_URL")
    assert _resolve_nextseek_base_url() == "http://127.0.0.1:8001"
