"""CLI and high-level runner for the evaluator.

``build_parser()`` defines the public CLI surface and ``main()`` dispatches
batch, demo, and source-evaluation flows into workflow/report helpers.
Invariant: CLI flag names, choices, and defaults stay backward-compatible,
and batch commands emit normalized JSON plus HTML artifacts.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Sequence


_VALID_EVALUATOR_MODES = {
    "oai",
    "gcp",
    "mixed",
    "gcp:current",
    "gcp:lite",
    "anth",
    "anth:current",
    "anth:lite",
    "aws:son",
    "aws:opus",
    "aws:ds",
    "aws:qwen-nxt",
    "aws:glm",
}


def _prod_value(prod_key: str, fallback_key: str, default: str | None = None) -> str | None:
    value = os.getenv(prod_key)
    if value is not None:
        return value
    value = os.getenv(fallback_key)
    if value is not None:
        return value
    return default


def _prod_source_name(prod_key: str, fallback_key: str) -> str:
    if os.getenv(prod_key) is not None:
        return prod_key
    return fallback_key


def _build_prod_source_env_names(enabled: bool) -> dict[str, object]:
    if enabled:
        neo4j_password_key = (
            "NEO4J_PASSWORD_PROD"
            if os.getenv("NEO4J_PASSWORD_PROD") is not None
            else ("NEO4J_PASSSWORD_PROD" if os.getenv("NEO4J_PASSSWORD_PROD") is not None else "NEO4J_PASSWORD")
        )
        return {
            "api": {
                "base_url": _prod_source_name("NEXTSEEK_PROD_URL", "NEXTSEEK_BASE_URL"),
                "api_user": _prod_source_name("API_USER_PROD", "API_USER"),
                "api_pass": "API_PASS_PROD" if os.getenv("API_PASS_PROD") is not None else "API_PASS",
            },
            "graph_db": {
                "neo4j_uri": _prod_source_name("NEO4J_URI_PROD", "NEO4J_URI"),
                "neo4j_user": _prod_source_name("NEO4J_USER_PROD", "NEO4J_USER"),
                "neo4j_password": neo4j_password_key,
                "neo4j_database": _prod_source_name("NEO4J_DATABASE_PROD", "NEO4J_DATABASE"),
            },
        }

    return {
        "api": {
            "base_url": "NEXTSEEK_BASE_URL",
            "api_user": "API_USER",
            "api_pass": "API_PASS",
        },
        "graph_db": {
            "neo4j_uri": "NEO4J_URI",
            "neo4j_user": "NEO4J_USER",
            "neo4j_password": "NEO4J_PASSWORD",
            "neo4j_database": "NEO4J_DATABASE",
        },
    }


def _build_prod_config_map(enabled: bool) -> dict[str, str]:
    if not enabled:
        return {}

    neo4j_password = (
        os.getenv("NEO4J_PASSWORD_PROD")
        or os.getenv("NEO4J_PASSSWORD_PROD")
        or os.getenv("NEO4J_PASSWORD")
    )

    config_map: dict[str, str] = {}
    nextseek_base = _prod_value("NEXTSEEK_PROD_URL", "NEXTSEEK_BASE_URL")
    api_user = _prod_value("API_USER_PROD", "API_USER")
    api_pass = os.getenv("API_PASS_PROD")
    if api_pass is None:
        api_pass = os.getenv("API_PASS")
    neo4j_uri = _prod_value("NEO4J_URI_PROD", "NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = _prod_value("NEO4J_USER_PROD", "NEO4J_USER", "neo4j")
    neo4j_database = _prod_value("NEO4J_DATABASE_PROD", "NEO4J_DATABASE", "neo4j")

    if nextseek_base is not None:
        config_map["NEXTSEEK_BASE_URL"] = nextseek_base.rstrip("/")
    if api_user is not None:
        config_map["API_USER"] = api_user
    if api_pass is not None:
        config_map["API_PASS"] = api_pass
    if neo4j_uri is not None:
        config_map["NEO4J_URI"] = neo4j_uri
    if neo4j_user is not None:
        config_map["NEO4J_USER"] = neo4j_user
    if neo4j_password is not None:
        config_map["NEO4J_PASSWORD"] = neo4j_password
    if neo4j_database is not None:
        config_map["NEO4J_DATABASE"] = neo4j_database
    config_map["CONFIG_SOURCE_ENV_NAMES"] = _build_prod_source_env_names(True)
    return config_map


def _resolve_evaluator_mode(mode_override: str | None = None) -> str:
    candidate = (
        mode_override
        or os.getenv("NEXTSEEK_EVALUATOR_MODE")
        or os.getenv("NEXTSEEK_MODE")
        or "gcp"
    ).strip().lower()
    if candidate not in _VALID_EVALUATOR_MODES:
        raise ValueError(f"Unsupported evaluator mode {candidate!r}.")
    return candidate


def _build_chat_config(use_prod: bool, mode_override: str | None = None):
    from chat_nextseek.config import ChatConfig

    config_map = _build_prod_config_map(bool(use_prod))
    config_map["MODEL_MODE"] = _resolve_evaluator_mode(mode_override)
    return ChatConfig(config_map=config_map)


def _create_session_store(config, session_id: str = "cli-user"):
    from chat_nextseek.session import MySQLSessionState, SQLiteSessionState

    if config.SESSION_DB_TYPE == "sqlite":
        return SQLiteSessionState(config.SESSION_DB_PATH, session_id)
    if config.SESSION_DB_TYPE == "mysql":
        return MySQLSessionState(
            {
                "user": config.SESSION_DB_USER,
                "password": config.SESSION_DB_PASSWORD,
                "host": config.SESSION_DB_HOST,
                "database": config.SESSION_DB_NAME,
                "port": config.SESSION_DB_PORT,
            },
            session_id,
        )
    raise ValueError(f"Unknown SESSION_DB_TYPE: {config.SESSION_DB_TYPE!r}")


def _build_evaluator_workflow(*, config):
    from .workflow import EvaluatorWorkflow

    return EvaluatorWorkflow(config=config)


def _load_session_for_evaluator_source(config, source: str):
    from .workflow import parse_source_reference

    kind, payload = parse_source_reference(source)
    if kind != "bundle":
        return None
    return _create_session_store(config, str(payload["session_id"]))


def _build_retry_credentials(config) -> dict[str, str] | None:
    credentials: dict[str, str] = {}
    if getattr(config, "API_USER", None):
        credentials["api_user"] = config.API_USER
    if getattr(config, "API_PASS", None):
        credentials["api_pass"] = config.API_PASS
    return credentials or None


def _render_batch_html(report_path: Path) -> Path:
    from . import dashboard
    from .dashboard.render import render

    html_path = report_path.with_suffix(".html")
    template_ref = importlib_resources.files(dashboard).joinpath("dashboard-template.html")
    with importlib_resources.as_file(template_ref) as template_path:
        render(report_path, template_path, html_path)
    return html_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m chat_nextseek.evaluator",
        description=(
            "Native NExtSEEK evaluator. Runs judgment + retry decisions against "
            "orchestrator query results, writes JSON + HTML batch reports, and "
            "supports resumable async batches."
        ),
        epilog=(
            "Examples:\n"
            "  # Run 5 queries from testing.json against the dev API\n"
            "  python -m chat_nextseek.evaluator --eval-batch testing.json --eval-batch-limit 5\n"
            "\n"
            "  # Full async batch with concurrency=5, resumable\n"
            "  python -m chat_nextseek.evaluator --eval-batch testing.json --eval-batch-async\n"
            "\n"
            "  # Resume a previous async run\n"
            "  python -m chat_nextseek.evaluator --eval-batch-resume outputs/evaluator/batch-<id>.json\n"
            "\n"
            "  # Serve the demo dashboard\n"
            "  python -m chat_nextseek.evaluator --eval-demo\n"
            "\n"
            "See src/chat_nextseek/evaluator/docs/cli.md for full reference."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--prod", action="store_true", help="Use production-backed API and graph settings")
    parser.add_argument("--planner", action="store_true", help="Use planner pipeline for batch/demo runs")
    parser.add_argument(
        "--mode",
        help="Evaluator model mode override (defaults to gcp for evaluator workflows).",
    )
    parser.add_argument("--eval-batch", type=Path, help="Path to query list (.txt/.json) or testing.json")
    parser.add_argument(
        "--eval-batch-out",
        type=Path,
        default=Path("outputs/evaluator"),
        help="Output directory for evaluator JSON and HTML reports",
    )
    parser.add_argument(
        "--eval-batch-async",
        action="store_true",
        help="Run batch evaluation with bounded async execution and persisted resume state.",
    )
    parser.add_argument(
        "--eval-batch-limit",
        type=int,
        default=None,
        help="Optional max number of queries to run from the batch input.",
    )
    parser.add_argument(
        "--eval-batch-concurrency",
        type=int,
        default=5,
        help="Concurrency limit for --eval-batch-async.",
    )
    parser.add_argument(
        "--eval-batch-resume",
        type=Path,
        help="Resume an async batch run from a persisted batch-*.json state file.",
    )
    parser.add_argument("--eval-demo", action="store_true", help="Run the evaluator demo server")
    parser.add_argument("--eval-demo-port", type=int, default=8080, help="Port for --eval-demo")
    parser.add_argument("--eval-demo-html", type=Path, help="Optional HTML file override for --eval-demo")
    parser.add_argument("--eval-source", help="Evaluator source reference, e.g. bundle:<session>:<bundle_id>")
    parser.add_argument("--eval-context", action="store_true", help="Print normalized evaluator context")
    parser.add_argument("--eval-run", action="store_true", help="Evaluate a source without executing retry")
    parser.add_argument("--eval-retry", action="store_true", help="Evaluate a source and execute retry")
    parser.add_argument("--eval-list", action="store_true", help="List persisted evaluator runs")
    parser.add_argument("--eval-status", help="Optional status filter for --eval-list")
    parser.add_argument("--eval-limit", type=int, default=20, help="Maximum number of runs to return")
    parser.add_argument(
        "--eval-has-bundle",
        action="store_true",
        help="With --eval-list, only return runs created from a bundle source",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from .reports import (
        finalize_async_batch_state,
        limit_queries,
        load_queries,
        run_batch_evaluation,
        run_batch_evaluation_async,
        write_report,
    )
    from .workflow import parse_source_reference

    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        config = _build_chat_config(bool(args.prod), args.mode)
        workflow = _build_evaluator_workflow(config=config)

        if args.eval_demo:
            from .demo.server import run_demo_server

            return run_demo_server(
                config=config,
                workflow=workflow,
                html_path=args.eval_demo_html,
                port=args.eval_demo_port,
                planner=bool(args.planner),
            )

        if args.eval_batch or args.eval_batch_resume:
            queries = None
            if args.eval_batch is not None:
                queries = limit_queries(load_queries(args.eval_batch), args.eval_batch_limit)

            if args.eval_batch_async:
                state, state_path, report = asyncio.run(
                    run_batch_evaluation_async(
                        queries,
                        output_dir=args.eval_batch_out,
                        config=config,
                        workflow=workflow,
                        credentials=_build_retry_credentials(config),
                        planner=bool(args.planner),
                        concurrency=args.eval_batch_concurrency,
                        resume_path=args.eval_batch_resume,
                        input_path=args.eval_batch,
                        mode=_resolve_evaluator_mode(args.mode),
                    )
                )
                async_output_dir = Path(state.output_dir)
                report_path = write_report(report, async_output_dir)
                html_path = _render_batch_html(report_path)
                finalize_async_batch_state(
                    state,
                    report=report,
                    report_path=report_path,
                    html_path=html_path,
                    state_path=state_path,
                )
                print(f"state: {state_path}")
                print(f"json: {report_path}")
                print(f"html: {html_path}")
                print(f"status: {report.run_status}")
                print(f"infra_failures: {report.infra_failures}")
                return 0 if report.success else 1

            if args.eval_batch_resume is not None:
                print("[evaluator] --eval-batch-resume requires --eval-batch-async.")
                return 2
            if queries is None:
                print("[evaluator] --eval-batch is required unless --eval-batch-resume is used with --eval-batch-async.")
                return 2

            report = run_batch_evaluation(
                queries,
                config=config,
                workflow=workflow,
                credentials=_build_retry_credentials(config),
                planner=bool(args.planner),
            )
            report_path = write_report(report, args.eval_batch_out)
            html_path = _render_batch_html(report_path)
            print(f"json: {report_path}")
            print(f"html: {html_path}")
            print(f"status: {report.run_status}")
            print(f"infra_failures: {report.infra_failures}")
            return 0 if report.success else 1

        if args.eval_list:
            payload = workflow.list_runs(
                status=args.eval_status,
                limit=args.eval_limit,
                has_bundle=True if args.eval_has_bundle else None,
            )
            print(json.dumps(payload, indent=2, default=str))
            return 0

        if not args.eval_source:
            print("[evaluator] --eval-source is required unless --eval-list, --eval-batch, or --eval-demo is used.")
            return 2

        selected_actions = [args.eval_context, args.eval_run, args.eval_retry]
        if sum(bool(flag) for flag in selected_actions) > 1:
            print("[evaluator] Choose only one of --eval-context, --eval-run, or --eval-retry.")
            return 2
        if not any(selected_actions):
            args.eval_context = True

        session = _load_session_for_evaluator_source(config, args.eval_source)
        kind, payload = parse_source_reference(args.eval_source)
        if args.eval_context:
            if kind == "bundle":
                response = workflow.get_retry_context_for_bundle(
                    session_id=payload["session_id"],
                    bundle_id=payload["bundle_id"],
                    session=session,
                )
            else:
                response = workflow.get_retry_context_for_task(task_id=payload["task_id"])
            print(response.model_dump_json(indent=2))
            return 0

        result = workflow.evaluate_source(
            args.eval_source,
            session=session,
            execute_retry=bool(args.eval_retry),
            config=config,
            credentials=_build_retry_credentials(config),
        )
        print(json.dumps(result, indent=2, default=str))
        return 0
    except Exception as exc:
        print(f"[evaluator] Command failed: {exc}")
        return 1


__all__ = [
    "_build_chat_config",
    "_build_evaluator_workflow",
    "_build_retry_credentials",
    "_create_session_store",
    "_load_session_for_evaluator_source",
    "_resolve_evaluator_mode",
    "build_parser",
    "main",
]
