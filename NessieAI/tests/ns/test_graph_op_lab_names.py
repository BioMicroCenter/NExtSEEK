"""Round 2: the Container-CC graph op names labs the way it names sample types (``type_names``).

Invented labs only. The chain (``run_graph_question``) is faked; ``config.LABS`` is the app's SEEK institution list.
"""
from types import SimpleNamespace

from NessieAI.ns import granular

LABS = [{"code": "QQZ", "name": "Quill", "affiliation": "Example University"},
        {"code": "ZZY", "name": "Zephyr", "affiliation": ""}]


def _run(monkeypatch, rows, config):
    answer = granular.GraphAnswer(plan={}, result={"ok": True, "data": rows}, fallback=None, parser_plan=None,
                                  cypher=None)
    monkeypatch.setattr(granular, "run_graph_question", lambda query, **kw: answer)
    return granular._graph({"query": "which lab"}, config, None, lambda *a: None, lambda *a: None, None)


def test_a_lab_column_with_a_known_code_gets_lab_names(monkeypatch):
    out = _run(monkeypatch, [{"uid": "A-1", "lab": "QQZ"}, {"uid": "A-2", "lab": "zzy"}], SimpleNamespace(LABS=LABS))
    assert out["lab_names"] == {"QQZ": "Quill lab (Example University)", "ZZY": "Zephyr lab"}


def test_an_investigator_column_gets_lab_names(monkeypatch):
    out = _run(monkeypatch, [{"investigator": "QQZ", "n": 3}], SimpleNamespace(LABS=LABS))
    assert out["lab_names"] == {"QQZ": "Quill lab (Example University)"}


def test_a_type_column_holding_the_same_code_gets_none(monkeypatch):
    out = _run(monkeypatch, [{"type": "QQZ", "n": 3}], SimpleNamespace(LABS=LABS))
    assert "lab_names" not in out


def test_no_labs_or_no_known_code_gives_none(monkeypatch):
    assert "lab_names" not in _run(monkeypatch, [{"lab": "QQZ"}], SimpleNamespace(LABS=None))
    assert "lab_names" not in _run(monkeypatch, [{"lab": "NOPE"}], SimpleNamespace(LABS=LABS))
    assert "lab_names" not in _run(monkeypatch, [], SimpleNamespace(LABS=LABS))
