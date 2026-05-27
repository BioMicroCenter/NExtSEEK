from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_plan_evaluator_agent_symbol_present():
    text = (REPO_ROOT / "src/chat_nextseek/agents.py").read_text(encoding="utf-8")
    assert "def plan_evaluator_agent(" in text


def test_plan_evaluator_output_symbol_present():
    text = (REPO_ROOT / "src/chat_nextseek/schemas/planner.py").read_text(encoding="utf-8")
    assert "class PlanEvaluatorOutput(BaseModel):" in text


def test_no_runtime_external_evaluator_api_calls():
    for rel in [
        "src/chat_nextseek/evaluator/client.py",
        "src/chat_nextseek/evaluator/workflow.py",
        "src/chat_nextseek/evaluator/normalization.py",
    ]:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "assistant/query/async/" not in text
        assert "evaluator/retry/" not in text
