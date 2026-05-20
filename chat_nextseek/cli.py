"""
CLI for NExtSEEK Chat Assistant.

Streamlit UI:
  uv run cli.py -s                            # default mixed profile
  uv run cli.py -s -m gcp                     # pure GCP (pro for parser/report_writer)
  uv run cli.py -s -m anth                    # Anthropic via AWS Bedrock
  uv run cli.py -s -m aws:opus                # Opus 4.6 for all agents

Standalone query:
  uv run cli.py -q "Find mice treated with NDMA"          # default mode
  uv run cli.py -m oai -q "Find mice treated with NDMA"   # OpenAI mode
  uv run cli.py -qp "Find mice in the GBM study"          # planner pipeline (multi-step)

Test harness:
  uv run cli.py -t -r 260127                  # comparison report from reference run
  uv run cli.py -t code                       # scan for unused functions
  uv run cli.py -runtest test_case_1          # run test suite across all models
  uv run cli.py -runtest test_case_1 -m gcp  # run test suite for GCP only

E2E test suite (routes through e2e.runner.run_main):
  uv run cli.py -st                           # default ratio 0.33 sample of catalog variants
  uv run cli.py -ft                           # full run (ratio=1.0, all variants)
  uv run e2e.py --help                        # advanced flags: --seed, --family, --variant, --rerun, --report, ...
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from contextlib import contextmanager
from typing import Sequence

from dotenv import load_dotenv


load_dotenv()


def _run(command: Sequence[str], env: dict[str, str] | None = None) -> int:
    """Execute a subprocess command, mirroring it to stdout and preserving the exit code."""
    print(f"[cli] Running: {shlex.join(command)}")
    try:
        result = subprocess.run(command, check=False, env=env)
        return result.returncode
    except KeyboardInterrupt:
        print("\n[cli] Interrupted")
        return 130


def _prod_value(prod_key: str, fallback_key: str, default: str | None = None) -> str | None:
    """Prefer a production-specific env var, falling back to the standard key when unset."""
    value = os.getenv(prod_key)
    if value is not None:
        return value
    value = os.getenv(fallback_key)
    if value is not None:
        return value
    return default


def _prod_source_name(prod_key: str, fallback_key: str) -> str:
    """Return the env var name currently supplying a prod override value."""
    if os.getenv(prod_key) is not None:
        return prod_key
    return fallback_key


def _build_prod_source_env_names(enabled: bool) -> dict[str, object]:
    """Describe which env var names were used to source API and Neo4j settings."""
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
    """Build ChatConfig overrides that swap standard keys to production values."""
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


def _build_prod_subprocess_env(enabled: bool) -> dict[str, str] | None:
    """Inject production values into the normal env var names for subprocess-based entry points."""
    if not enabled:
        return None

    env = os.environ.copy()
    for key, value in _build_prod_config_map(True).items():
        if isinstance(value, str):
            env[key] = value
    env["CHAT_NEXTSEEK_CONFIG_SOURCE_ENV_NAMES"] = json.dumps(_build_prod_source_env_names(True))
    return env


@contextmanager
def _prod_env_override(enabled: bool):
    """Temporarily map the standard env var names to production values in-process."""
    if not enabled:
        yield
        return

    overrides = _build_prod_config_map(True)
    previous = {key: os.environ.get(key) for key, value in overrides.items() if isinstance(value, str)}
    previous["CHAT_NEXTSEEK_CONFIG_SOURCE_ENV_NAMES"] = os.environ.get("CHAT_NEXTSEEK_CONFIG_SOURCE_ENV_NAMES")
    try:
        for key, value in overrides.items():
            if isinstance(value, str):
                os.environ[key] = value
        os.environ["CHAT_NEXTSEEK_CONFIG_SOURCE_ENV_NAMES"] = json.dumps(_build_prod_source_env_names(True))
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _build_streamlit_command(
    mode: str | None,
    extra_args: list[str],
) -> list[str]:
    """Assemble the `streamlit run app.py` command, forwarding mode and trailing args."""
    base = ["uv", "run", "--", "streamlit", "run", "app.py"]
    app_cli: list[str] = []
    if mode:
        app_cli.extend(["-m", mode])
    for arg in extra_args:
        if arg == "--" and not app_cli:
            continue
        app_cli.append(arg)
    if app_cli:
        return base + ["--"] + app_cli
    return base


def _reset_query_logging_session(session) -> None:
    """Force a fresh per-run log directory and snapshot for each standalone CLI invocation."""
    session["run_root_dir"] = None
    session["log_dir"] = None
    session["console_log_path"] = None
    session["chat_log_path"] = None
    session["api_log_path"] = None
    session["prompts_log_path"] = None
    session["config_snapshot_logged"] = False


def cmd_run(args: argparse.Namespace) -> int:
    """Handle the default CLI path: Streamlit launch or a single standalone query."""
    if args.streamlit:
        command = _build_streamlit_command(
            args.mode,
            args.extra or [],
        )
        return _run(command, env=_build_prod_subprocess_env(bool(args.prod)))

    # Standalone mode — import heavy modules only when needed
    from chat_nextseek.config import ChatConfig
    from chat_nextseek.orchestrator import run_query
    from chat_nextseek.session import SQLiteSessionState, MySQLSessionState

    config = ChatConfig(config_map=_build_prod_config_map(bool(args.prod)))

    if config.SESSION_DB_TYPE == "sqlite":
        session = SQLiteSessionState(config.SESSION_DB_PATH, "cli-user")
    elif config.SESSION_DB_TYPE == "mysql":
        session = MySQLSessionState(
            {
                "user": config.SESSION_DB_USER,
                "password": config.SESSION_DB_PASSWORD,
                "host": config.SESSION_DB_HOST,
                "database": config.SESSION_DB_NAME,
                "port": config.SESSION_DB_PORT,
            },
            "cli-user",
        )
    else:
        print(f"[cli] Unknown SESSION_DB_TYPE: {config.SESSION_DB_TYPE!r}")
        return 1

    _reset_query_logging_session(session)
    result = run_query(session, config, args.query)
    print(f"\n{result['reply']}")

    files = result.get("files") or []
    if files:
        print("\n[OUTPUT FILES]")
        for f in files:
            print(f"  {f['label']}: {f['path']}")

    return 0


def cmd_test(args: argparse.Namespace) -> int:
    """Proxy to `test.py` for report generation or static code scans."""
    cmd = ["uv", "run", "test.py"]

    # Check for positional test mode in extra args (e.g., -t code)
    test_mode = args.test_mode
    extra = args.extra or []
    if extra and extra[0] in ("report", "code"):
        test_mode = extra[0]

    if test_mode:
        cmd.extend(["-m", test_mode])
    if args.report:
        cmd.extend(["-i", args.report])
    return _run(cmd, env=_build_prod_subprocess_env(bool(args.prod)))


def cmd_query_plan(args: argparse.Namespace) -> int:
    """Run a query through the planner pipeline (multi-step capable)."""
    from chat_nextseek.config import ChatConfig
    from chat_nextseek.orchestrator import run_query_plan
    from chat_nextseek.session import SQLiteSessionState, MySQLSessionState

    config = ChatConfig(config_map=_build_prod_config_map(bool(args.prod)))

    if config.SESSION_DB_TYPE == "sqlite":
        session = SQLiteSessionState(config.SESSION_DB_PATH, "cli-user")
    elif config.SESSION_DB_TYPE == "mysql":
        session = MySQLSessionState(
            {
                "user": config.SESSION_DB_USER,
                "password": config.SESSION_DB_PASSWORD,
                "host": config.SESSION_DB_HOST,
                "database": config.SESSION_DB_NAME,
                "port": config.SESSION_DB_PORT,
            },
            "cli-user",
        )
    else:
        print(f"[cli] Unknown SESSION_DB_TYPE: {config.SESSION_DB_TYPE!r}")
        return 1

    _reset_query_logging_session(session)
    result = run_query_plan(session, config, args.query_plan)
    print(f"\n{result['reply']}")

    files = result.get("files") or []
    if files:
        print("\n[OUTPUT FILES]")
        for f in files:
            print(f"  {f['label']}: {f['path']}")

    return 0



def cmd_runtest(args: argparse.Namespace) -> int:
    """Run a test suite across models."""
    from test import run_test_suite

    suite_name = args.runtest
    models = None
    if args.mode:
        models = [args.mode]
    only = getattr(args, "smart_only", None)

    with _prod_env_override(bool(args.prod)):
        return run_test_suite(suite_name, models, planner=False, only=only)


def cmd_smart_test(args: argparse.Namespace) -> int:
    """Route -st (smart test) and -ft (full test) through the new e2e.py runner.

    Legacy flag semantics:
      -st               -> e2e --ratio 0.33 (default sample)
      -ft               -> e2e --ratio full (all variants)
      --both, -p, -i      -> removed (planner pipeline retiring; use e2e.py for advanced flags)
      --only              -> re-scoped to -runtest (no longer applies to -st)
    """
    from pathlib import Path
    from e2e.runner import run_main

    catalog_path = Path(__file__).parent / "e2e" / "catalog.json"
    ratio = 1.0 if getattr(args, "full_test", False) else 0.33
    profile = args.mode or os.environ.get("NEXTSEEK_MODE", "mixed")
    with _prod_env_override(bool(args.prod)):
        return run_main(catalog_path, ratio=ratio, profile=profile)


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level CLI parser and register all supported execution modes."""
    parser = argparse.ArgumentParser(
        description="NExtSEEK Chat Assistant CLI",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "\n"
            "  Streamlit UI:\n"
            "    uv run cli.py -s                           default mixed profile (GCP flash + Anth Opus for parser/report_writer)\n"
            "    uv run cli.py -s -m gcp                    pure GCP (flash for most, pro-preview for parser/report_writer)\n"
            "    uv run cli.py -s -m anth                   Anthropic via AWS Bedrock (Sonnet + Opus for parser)\n"
            "    uv run cli.py -s -m aws:opus               Opus 4.6 for all agents\n"
            "    uv run cli.py -s -m aws:son                Sonnet 4.6 for all agents\n"
            "    uv run cli.py -s -m aws:ds                 DeepSeek V3.2 for all agents\n"
            "\n"
            "  Standalone query:\n"
            "    uv run cli.py -q 'Find mice treated with NDMA'         default mode\n"
            "    uv run cli.py -m oai -q 'Find mice treated with NDMA'  OpenAI mode\n"
            "\n"
            "  Planner pipeline (multi-step, Opus+thinking planner):\n"
            "    uv run cli.py -qp 'Find mice in the GBM study treated with NDMA'\n"
            "\n"
            "  Test harness:\n"
            "    uv run cli.py -t -r 260127                 generate comparison report from reference run YYMMDD folder\n"
            "    uv run cli.py -t code                      scan for unused functions across source files\n"
            "\n"
            "  Test suites:\n"
            "    uv run cli.py -runtest test_case_1           run suite across all models\n"
            "    uv run cli.py -runtest test_case_1 -m gcp    run suite for GCP only\n"
            "\n"
            "  E2E test suite (routes through e2e.runner.run_main):\n"
            "    uv run cli.py -st                            default ratio 0.33 sample of catalog variants\n"
            "    uv run cli.py -ft                            full run (ratio=1.0, all variants)\n"
            "    uv run e2e.py --help                         advanced flags (--seed, --family, --variant, --rerun, --report, ...)\n"
        ),
    )

    # Run mode flags
    parser.add_argument(
        "-s", "--streamlit",
        action="store_true",
        default=False,
        help="Launch Streamlit UI (default: standalone mode).",
    )
    parser.add_argument(
        "-m", "--mode",
        choices=["mixed", "oai", "gcp", "anth", "aws:son", "aws:opus", "aws:ds", "aws:qwen-nxt", "aws:glm"],
        default=None,
        help=(
            "LLM backend / agent routing profile. Omit for the default mixed profile.\n"
            "  gcp        GCP Gemini (flash for most agents, pro-preview for parser + report_writer)\n"
            "  anth       Anthropic via AWS Bedrock (Sonnet for most, Opus+thinking for parser)\n"
            "  oai        OpenAI\n"
            "  aws:son    AWS Bedrock — Claude Sonnet 4.6 for all agents\n"
            "  aws:opus   AWS Bedrock — Claude Opus 4.6 for all agents\n"
            "  aws:ds     AWS Bedrock — DeepSeek V3.2 for all agents\n"
            "  aws:qwen-nxt  AWS Bedrock — Qwen3 80B for all agents\n"
            "  aws:glm    AWS Bedrock — GLM-4.7 for all agents"
        ),
    )
    parser.add_argument(
        "-prod", "--prod",
        action="store_true",
        default=False,
        help=(
            "Use production NExtSEEK/Neo4j credentials from *_PROD env vars.\n"
            "Maps NEXTSEEK_BASE_URL/API_USER/API_PASS/NEO4J_* to their *_PROD variants."
        ),
    )
    parser.add_argument(
        "-q", "--query",
        default=None,
        metavar="QUERY",
        help="Run a single query in standalone mode (no Streamlit UI).",
    )
    parser.add_argument(
        "-qp", "--query-plan",
        dest="query_plan",
        default=None,
        metavar="QUERY",
        help="Run a query through the planner pipeline (Opus+thinking planner, multi-step capable).",
    )
    # Test mode flags
    parser.add_argument(
        "-t", "--test",
        action="store_true",
        default=False,
        help="Run the test harness. Use with -r YYMMDD for a comparison report, or pass 'code' as positional arg for unused-function scan.",
    )
    parser.add_argument(
        "--test-mode",
        choices=["report", "code"],
        default="report",
        help="Test harness mode: 'report' generates a comparison Excel from reference runs, 'code' scans for unused functions.",
    )
    parser.add_argument(
        "-r", "--report",
        type=str,
        metavar="YYMMDD",
        help="Reference run date folder to use when generating a comparison report (e.g. 260127).",
    )
    parser.add_argument(
        "-runtest",
        type=str,
        metavar="SUITE",
        help="Run a named test suite (e.g. test_case_1) across all models, or just the model specified by -m.",
    )
    parser.add_argument(
        "-st", "--smart-test",
        action="store_true",
        default=False,
        dest="smart_test",
        help="Run E2E test suite (default ratio 0.33). For advanced flags use: uv run e2e.py --help",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        dest="smart_only",
        metavar="K1,K2,...",
        help=(
            "Comma-separated case-key prefixes to run when using -runtest "
            "(e.g. 'W3,W4' matches W3_* and W4_* in wizard_e2e)."
        ),
    )
    parser.add_argument(
        "-ft", "--full-test",
        action="store_true",
        default=False,
        dest="full_test",
        help="Run full E2E (ratio=1.0). Equivalent to: uv run e2e.py --ratio full",
    )

    parser.add_argument(
        "extra",
        nargs=argparse.REMAINDER,
        help="Extra args forwarded to app.py when running in Streamlit mode.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI args and dispatch to the selected command handler."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.smart_test or args.full_test:
        return cmd_smart_test(args)

    if args.runtest:
        return cmd_runtest(args)

    if args.test:
        return cmd_test(args)

    if args.query_plan:
        return cmd_query_plan(args)

    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
