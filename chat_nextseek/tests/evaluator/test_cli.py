from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO

from chat_nextseek.evaluator import runner


def _run_cli(argv: list[str]) -> tuple[int, str]:
    buf = StringIO()
    with redirect_stdout(buf):
        code = runner.main(argv)
    return code, buf.getvalue()


def test_cli_eval_list_outputs_json(monkeypatch, evaluator_workflow, dummy_config):
    evaluator_workflow.evaluate_bundle(
        {
            "session_id": "00000000-0000-0000-0000-000000000001",
            "user_id": 1,
            "results_history": [],
            "last_debug": {},
        },
        {
            "id": 1,
            "mode": "new_search",
            "user_query": "Find NDMA mice",
            "api_result_full": {"ok": True, "status_code": 200, "data": {"total": 2}},
        },
    )
    monkeypatch.setattr(runner, "_build_chat_config", lambda use_prod, mode_override=None: dummy_config)
    monkeypatch.setattr(runner, "_build_evaluator_workflow", lambda *, config: evaluator_workflow)

    code, output = _run_cli(["--eval-list", "--eval-status", "completed", "--eval-limit", "5"])
    assert code == 0
    assert '"status": "completed"' in output


def test_cli_eval_source_context(monkeypatch, evaluator_workflow, fake_session, fake_bundle_source, dummy_config):
    monkeypatch.setattr(runner, "_build_chat_config", lambda use_prod, mode_override=None: dummy_config)
    monkeypatch.setattr(runner, "_build_evaluator_workflow", lambda *, config: evaluator_workflow)
    monkeypatch.setattr(runner, "_load_session_for_evaluator_source", lambda config, source: fake_session)

    source = f"bundle:{fake_bundle_source.session_id}:{fake_bundle_source.bundle_id}"
    code, output = _run_cli(["--eval-source", source, "--eval-context"])
    assert code == 0
    assert '"lookup"' in output


def test_cli_rejects_multiple_eval_actions(monkeypatch, evaluator_workflow, dummy_config):
    monkeypatch.setattr(runner, "_build_chat_config", lambda use_prod, mode_override=None: dummy_config)
    monkeypatch.setattr(runner, "_build_evaluator_workflow", lambda *, config: evaluator_workflow)

    code, output = _run_cli(
        [
            "--eval-source",
            "task:00000000-0000-0000-0000-000000000123",
            "--eval-context",
            "--eval-run",
        ]
    )
    assert code == 2
    assert "Choose only one" in output


def test_resolve_evaluator_mode_defaults_to_gcp(monkeypatch):
    monkeypatch.delenv("NEXTSEEK_EVALUATOR_MODE", raising=False)
    monkeypatch.delenv("NEXTSEEK_MODE", raising=False)
    assert runner._resolve_evaluator_mode() == "gcp"


def test_resolve_evaluator_mode_prefers_explicit_override(monkeypatch):
    monkeypatch.setenv("NEXTSEEK_MODE", "anth")
    assert runner._resolve_evaluator_mode("gcp:lite") == "gcp:lite"
