from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

from chat_nextseek.evaluator import (
    EvaluatorRetryContextResponse,
    EvaluatorRunSummary,
    RetryRequest,
    RetryResponse,
    get_evaluator_state_root,
    get_run_index_path,
    get_run_record_path,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
EXTERNAL_SCHEMA_PATH = (
    Path("/Users/taishajoseph/Documents/Projects/NextSeekEval/src/schemas/evaluator.py")
)


def _load_external_schema_module():
    spec = importlib.util.spec_from_file_location("external_evaluator_schema", EXTERNAL_SCHEMA_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_package_exports():
    assert EvaluatorRetryContextResponse.__name__ == "EvaluatorRetryContextResponse"
    assert RetryRequest.__name__ == "RetryRequest"
    assert RetryResponse.__name__ == "RetryResponse"
    assert EvaluatorRunSummary.__name__ == "EvaluatorRunSummary"


def test_external_schema_parity():
    external = _load_external_schema_module()

    for model_name in (
        "EvaluatorRetryContextResponse",
        "RetryRequest",
        "RetryResponse",
        "EvaluatorRunSummary",
    ):
        internal_fields = getattr(__import__("chat_nextseek.evaluator", fromlist=[model_name]), model_name).model_fields
        external_fields = getattr(external, model_name).model_fields
        assert set(internal_fields) == set(external_fields)
        assert {name for name, field in internal_fields.items() if field.is_required()} == {
            name for name, field in external_fields.items() if field.is_required()
        }


def test_evaluator_state_root_defaults_to_xdg(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = get_evaluator_state_root()

    assert root == tmp_path / "state" / "chat_nextseek" / "evaluator"
    assert get_run_index_path() == root / "runs.json"


def test_evaluator_state_root_respects_override(tmp_path: Path):
    root = get_evaluator_state_root(state_root=tmp_path / "custom-root")
    assert root == tmp_path / "custom-root"


def test_run_record_path_uses_canonical_file_name(tmp_path: Path):
    task_id = uuid4()
    path = get_run_record_path(task_id, state_root=tmp_path)
    assert path == tmp_path / "runs" / f"{task_id}.json"
