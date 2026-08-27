"""Hermetic mutant-killers for Gate 3C.5 cost ledger."""
from __future__ import annotations

import json
from types import SimpleNamespace
import sys
import types

import pytest

from nextseek_api.cc_assistant import step7_llm_cost_ledger as ledger


def test_ledger_path_uses_django_base_dir_and_override(tmp_path, monkeypatch):
    monkeypatch.delenv("DJANGO_BASE_DIR", raising=False)
    assert ledger.ledger_path().as_posix().endswith("outputs/_ledger.jsonl")
    monkeypatch.setenv("DJANGO_BASE_DIR", str(tmp_path))
    assert ledger.ledger_path() == tmp_path / "outputs" / "_ledger.jsonl"
    assert ledger.ledger_path(tmp_path / "x") == tmp_path / "x" / "outputs" / "_ledger.jsonl"


def test_entry_usd_priced_and_unpriced():
    assert ledger.entry_usd({"model": "gemini-2.5-flash", "in": 10, "out": 2}) == pytest.approx(
        10 * 0.30e-6 + 2 * 2.50e-6
    )
    with pytest.raises(ValueError, match="UNPRICED MODEL"):
        ledger.entry_usd({"model": "nope", "in": 1, "out": 1})


def test_read_ledger_skips_blank_invalid_and_non_dict(tmp_path):
    path = tmp_path / "outputs" / "_ledger.jsonl"
    assert ledger.read_ledger_lines(path) == []
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n{not-json\n"
        + json.dumps(["list"])
        + "\n"
        + json.dumps({"model": "gemini-2.5-flash", "in": 1000000, "out": 0})
        + "\n",
        encoding="utf-8",
    )
    lines = ledger.read_ledger_lines(path)
    assert lines == [{"model": "gemini-2.5-flash", "in": 1000000, "out": 0}]
    assert ledger.read_delta(0, 1, path) == lines
    assert ledger.total_usd(lines) == pytest.approx(0.30)


def test_record_accepts_dict_and_object_usage(tmp_path):
    path = tmp_path / "outputs" / "_ledger.jsonl"
    ledger._record("gemini-2.5-flash", {"prompt_tokens": 3, "completion_tokens": 1}, path)
    ledger._record("gemini-2.5-flash", SimpleNamespace(prompt_tokens=2, completion_tokens=4), path)
    ledger._record("gemini-2.5-flash", None, path)
    rows = ledger.read_ledger_lines(path)
    assert rows[0]["in"] == 3 and rows[0]["out"] == 1
    assert rows[1]["in"] == 2 and rows[1]["out"] == 4
    assert rows[2]["in"] is None and rows[2]["out"] is None


def test_install_wraps_chat_and_is_idempotent(monkeypatch, tmp_path):
    class Client:
        model = "gemini-2.5-flash"

        def chat(self, *a, **k):
            return SimpleNamespace(usage={"prompt_tokens": 1, "completion_tokens": 1})

    fake_llm = SimpleNamespace(
        OpenAIClient=Client, GeminiClient=None, AnthropicClient=Client, BedrockClient=Client
    )
    monkeypatch.setitem(sys.modules, "chat_nextseek", types.ModuleType("chat_nextseek"))
    monkeypatch.setitem(sys.modules, "chat_nextseek.llm_clients", fake_llm)
    monkeypatch.setattr(ledger, "_INSTALLED", False)
    ledger._ORIG_CHAT.clear()
    ledger.install(base_dir=tmp_path)
    first = dict(ledger._ORIG_CHAT)
    ledger.install(base_dir=tmp_path)
    assert ledger._ORIG_CHAT == first
    resp = Client().chat(model="gemini-2.5-flash")
    assert resp.usage["prompt_tokens"] == 1
    lines = ledger.read_ledger_lines(tmp_path / "outputs" / "_ledger.jsonl")
    assert lines and lines[0]["model"] == "gemini-2.5-flash"
    ledger.uninstall()
    assert ledger._INSTALLED is False
    ledger.uninstall()


def test_maybe_install_from_env_and_budget_guard(monkeypatch, tmp_path):
    monkeypatch.delenv("STEP7_LLM_LEDGER", raising=False)
    monkeypatch.setattr(ledger, "install", lambda **k: (_ for _ in ()).throw(AssertionError("must not install")))
    ledger.maybe_install_from_env()
    called = {}
    monkeypatch.setenv("STEP7_LLM_LEDGER", "1")
    monkeypatch.setattr(ledger, "install", lambda **k: called.setdefault("ok", True))
    ledger.maybe_install_from_env()
    assert called["ok"] is True
    ledger.budget_guard(1.0, 2.0, op="nextseek-query")
    with pytest.raises(RuntimeError, match="budget cap"):
        ledger.budget_guard(2.01, 2.0, op="nextseek-query")


def test_build_extraction_entry_single_and_multi_model(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_text(
        json.dumps({"model": "gemini-2.5-flash", "in": 10, "out": 0})
        + "\n"
        + json.dumps({"model": "gemini-3.5-flash", "in": 0, "out": 2})
        + "\n",
        encoding="utf-8",
    )
    one = tmp_path / "one.jsonl"
    one.write_text(json.dumps({"model": "gemini-2.5-flash", "in": 1, "out": 0}) + "\n")
    entry = ledger.build_extraction_entry(
        "nextseek-query", call_id="c1", ledger_line_start=0, ledger_line_end=1, path=one
    )
    assert entry["model"] == "gemini-2.5-flash"
    assert entry["tokens_in"] == 1
    mixed = ledger.build_extraction_entry(
        "nextseek-report", call_id="c2", ledger_line_start=0, ledger_line_end=2, path=path
    )
    assert mixed["model"] == ["gemini-2.5-flash", "gemini-3.5-flash"]
    with pytest.raises(ValueError, match="no ledger lines"):
        ledger.build_extraction_entry("op", call_id="c3", ledger_line_start=9, ledger_line_end=10, path=path)
