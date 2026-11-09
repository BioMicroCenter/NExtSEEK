"""The generate-submission op returns the reporter's warnings (a library the lab marked "do not publish") in its
result, because the Container-CC road drops the reply text and forwards only the result."""
from __future__ import annotations

import sys
import types

from NessieAI.ns import granular


def _run(monkeypatch, reporter_result):
    fake = types.ModuleType("chat_nextseek.portable")
    fake.generate_report_outputs = lambda **kw: (
        reporter_result, {"all_samples": {"report_type": "GEO", "report": {"samples": []}}},
        {"merged_report": "/tmp/m.json"}, "reply")
    fake.report_writer_agent = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "chat_nextseek.portable", fake)
    return granular._generate_submission({"type": "GEO", "uids": "D.1,D.2"}, object(), None, None, None, "/tmp")


def test_do_not_publish_warning_is_in_the_op_result(monkeypatch):
    out = _run(monkeypatch, {"reports": [], "warnings": ['1 libraries are marked "do not publish".']})
    assert out["warnings"] == ['1 libraries are marked "do not publish".']
    assert out["saved_files"] == {"merged_report": "/tmp/m.json"}


def test_no_warning_key_when_there_is_none(monkeypatch):
    assert "warnings" not in _run(monkeypatch, {"reports": []})
