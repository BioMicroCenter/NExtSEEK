from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from chat_nextseek.evaluator import runner


def _run_cli(argv: list[str]) -> tuple[int, str]:
    buf = StringIO()
    with redirect_stdout(buf):
        code = runner.main(argv)
    return code, buf.getvalue()


def _parse_output_paths(output: str) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for line in output.strip().splitlines():
        key, value = line.split(": ", 1)
        paths[key] = Path(value)
    return paths


def _fake_run_query(session, config, user_text, credentials=None, send_event=None):
    history = session.get("results_history", [])
    bundle_id = len(history) + 1
    history.append(
        {
            "id": bundle_id,
            "mode": "new_search",
            "user_query": user_text,
            "api_result_full": {"ok": True, "status_code": 200, "data": {"total": 0, "results": []}},
        }
    )
    session["results_history"] = history
    return {"reply": f"answer: {user_text}", "bundle_id": bundle_id, "files": []}


def test_batch_cli_writes_report(monkeypatch, evaluator_workflow, dummy_config, tmp_path):
    query_file = tmp_path / "queries.txt"
    query_file.write_text("Find me mice treated with NDMA\n", encoding="utf-8")

    monkeypatch.setattr(runner, "_build_chat_config", lambda use_prod, mode_override=None: dummy_config)
    monkeypatch.setattr(runner, "_build_evaluator_workflow", lambda *, config: evaluator_workflow)
    monkeypatch.setattr("chat_nextseek.evaluator.reports.run_query", _fake_run_query)
    monkeypatch.setattr("chat_nextseek.orchestrator.run_query", _fake_run_query)

    code, output = _run_cli(["--eval-batch", str(query_file), "--eval-batch-out", str(tmp_path)])
    assert code == 1

    paths = _parse_output_paths(output)
    report_path = paths["json"]
    html_path = paths["html"]
    assert report_path.exists()
    assert html_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["queries_total"] == 1
    assert payload["queries_failed"] == 1
    assert payload["queries_retried"] == 0
    assert payload["infra_failures"] == 0
    assert payload["run_status"] == "completed_with_failures"


def test_batch_cli_skips_blank_and_comment_lines(monkeypatch, evaluator_workflow, dummy_config, tmp_path):
    query_file = tmp_path / "queries.txt"
    query_file.write_text("\n# comment\nFind me mice treated with NDMA\n", encoding="utf-8")

    monkeypatch.setattr(runner, "_build_chat_config", lambda use_prod, mode_override=None: dummy_config)
    monkeypatch.setattr(runner, "_build_evaluator_workflow", lambda *, config: evaluator_workflow)
    monkeypatch.setattr("chat_nextseek.evaluator.reports.run_query", _fake_run_query)
    monkeypatch.setattr("chat_nextseek.orchestrator.run_query", _fake_run_query)

    code, output = _run_cli(["--eval-batch", str(query_file), "--eval-batch-out", str(tmp_path)])
    assert code == 1

    report_path = _parse_output_paths(output)["json"]
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["queries_total"] == 1
    assert payload["queries_failed"] == 1


def test_batch_cli_writes_html_for_json_input(monkeypatch, evaluator_workflow, dummy_config, tmp_path):
    query_file = tmp_path / "queries.json"
    query_file.write_text(
        json.dumps([{"id": "Q1", "question": "Find me mice treated with NDMA"}]),
        encoding="utf-8",
    )

    monkeypatch.setattr(runner, "_build_chat_config", lambda use_prod, mode_override=None: dummy_config)
    monkeypatch.setattr(runner, "_build_evaluator_workflow", lambda *, config: evaluator_workflow)
    monkeypatch.setattr("chat_nextseek.evaluator.reports.run_query", _fake_run_query)
    monkeypatch.setattr("chat_nextseek.orchestrator.run_query", _fake_run_query)

    code, output = _run_cli(["--eval-batch", str(query_file), "--eval-batch-out", str(tmp_path)])
    assert code == 1

    paths = _parse_output_paths(output)
    report_path = paths["json"]
    html_path = paths["html"]
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert payload["run_id"] in html
    assert payload["queries_failed"] == 1
    assert "__EMBEDDED_DATA__" not in html


def test_batch_cli_reads_testing_json_full_test(monkeypatch, evaluator_workflow, dummy_config, tmp_path):
    query_file = tmp_path / "testing.json"
    query_file.write_text(
        json.dumps(
            {
                "full_test": {
                    "tests": [
                        {"id": "Search-Basic-1", "query": "Find me mice treated with NDMA"},
                        {"id": "Search-Basic-2", "query": "Show me GBM samples"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(runner, "_build_chat_config", lambda use_prod, mode_override=None: dummy_config)
    monkeypatch.setattr(runner, "_build_evaluator_workflow", lambda *, config: evaluator_workflow)
    monkeypatch.setattr("chat_nextseek.evaluator.reports.run_query", _fake_run_query)
    monkeypatch.setattr("chat_nextseek.orchestrator.run_query", _fake_run_query)

    code, output = _run_cli(["--eval-batch", str(query_file), "--eval-batch-out", str(tmp_path)])
    assert code == 1

    report_path = _parse_output_paths(output)["json"]
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["queries_total"] == 2
    assert payload["queries_failed"] == 2


def test_batch_cli_limit_applies(monkeypatch, evaluator_workflow, dummy_config, tmp_path):
    query_file = tmp_path / "queries.txt"
    query_file.write_text("one\ntwo\nthree\n", encoding="utf-8")

    monkeypatch.setattr(runner, "_build_chat_config", lambda use_prod, mode_override=None: dummy_config)
    monkeypatch.setattr(runner, "_build_evaluator_workflow", lambda *, config: evaluator_workflow)
    monkeypatch.setattr("chat_nextseek.evaluator.reports.run_query", _fake_run_query)
    monkeypatch.setattr("chat_nextseek.orchestrator.run_query", _fake_run_query)

    code, output = _run_cli(
        ["--eval-batch", str(query_file), "--eval-batch-limit", "2", "--eval-batch-out", str(tmp_path)]
    )
    assert code == 1

    report_path = _parse_output_paths(output)["json"]
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["queries_total"] == 2
    assert payload["queries_failed"] == 2


def test_batch_cli_returns_nonzero_when_exceptions_exist(monkeypatch, evaluator_workflow, dummy_config, tmp_path):
    query_file = tmp_path / "queries.txt"
    query_file.write_text("bad query\n", encoding="utf-8")

    def no_bundle(session, config, user_text, credentials=None, send_event=None):
        return {"reply": "no bundle"}

    monkeypatch.setattr(runner, "_build_chat_config", lambda use_prod, mode_override=None: dummy_config)
    monkeypatch.setattr(runner, "_build_evaluator_workflow", lambda *, config: evaluator_workflow)
    monkeypatch.setattr("chat_nextseek.evaluator.reports.run_query", no_bundle)
    monkeypatch.setattr("chat_nextseek.orchestrator.run_query", no_bundle)

    code, output = _run_cli(["--eval-batch", str(query_file), "--eval-batch-out", str(tmp_path)])
    assert code == 1

    paths = _parse_output_paths(output)
    report_path = paths["json"]
    html_path = paths["html"]
    assert report_path.exists()
    assert html_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["queries_exceptions"] == 1
    assert payload["infra_failures"] == 0
    assert payload["success"] is False
    assert payload["run_status"] == "crashed"


def test_async_batch_cli_writes_state_and_report(monkeypatch, evaluator_workflow, dummy_config, tmp_path):
    query_file = tmp_path / "queries.txt"
    query_file.write_text("first\nsecond\n", encoding="utf-8")

    monkeypatch.setattr(runner, "_build_chat_config", lambda use_prod, mode_override=None: dummy_config)
    monkeypatch.setattr(runner, "_build_evaluator_workflow", lambda *, config: evaluator_workflow)
    monkeypatch.setattr("chat_nextseek.evaluator.reports.run_query", _fake_run_query)
    monkeypatch.setattr("chat_nextseek.orchestrator.run_query", _fake_run_query)

    code, output = _run_cli(
        [
            "--eval-batch",
            str(query_file),
            "--eval-batch-async",
            "--eval-batch-out",
            str(tmp_path),
            "--eval-batch-concurrency",
            "2",
        ]
    )
    assert code == 1

    paths = _parse_output_paths(output)
    assert paths["state"].exists()
    assert paths["json"].exists()
    assert paths["html"].exists()

    state = json.loads(paths["state"].read_text(encoding="utf-8"))
    assert state["status"] == "completed_with_failures"
    assert state["success"] is False
    assert state["completed_queries"] == 2
