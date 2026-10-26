import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import _nextseek_runner as runner  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("NEXTSEEK_DRY_RUN", raising=False)


@pytest.fixture
def sent(monkeypatch):
    seen = []
    monkeypatch.setattr(runner, "_call_op", lambda op, body, **kw: seen.append((op, body)) or {"run_id": "r1"})
    return seen


def test_paper_reingest_start_is_registered_under_its_runner_key():
    assert runner._DISPATCH["paper-reingest-start"] is runner._dispatch_paper_reingest_start


def test_without_a_paper_it_is_a_validation_error(capsys, sent):
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_paper_reingest_start(SimpleNamespace(paper="", project=None))
    assert exc.value.code == 3
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "VALIDATION"
    assert sent == []


def test_a_project_that_is_not_an_id_is_a_validation_error(capsys, sent):
    with pytest.raises(SystemExit) as exc:
        runner._dispatch_paper_reingest_start(SimpleNamespace(paper="38000001", project="cancer"))
    assert exc.value.code == 3
    assert sent == []


def test_forwards_the_paper_and_the_project_as_an_integer(sent):
    assert runner._dispatch_paper_reingest_start(SimpleNamespace(paper="38000001", project="4")) == {"run_id": "r1"}
    runner._dispatch_paper_reingest_start(SimpleNamespace(paper="10.1/x", project=None))
    assert sent == [("paper-reingest-start", {"paper": "38000001", "project": 4}),
                    ("paper-reingest-start", {"paper": "10.1/x"})]
