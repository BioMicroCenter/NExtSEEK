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
  uv run cli.py -st                          # E2E test suite (default ratio 0.33)
  uv run cli.py -ft                          # E2E full run (ratio=1.0)

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


def _prod_base_url() -> str | None:
    """NEXTSEEK_PROD_URL → NEXTSEEK_INTERNAL_BASE_URL → NEXTSEEK_BASE_URL.

    Review follow-up FU5 (2026-07-07): the internal transport URL slots
    between the explicit prod override and the public URL so a manual
    in-container --prod run without NEXTSEEK_PROD_URL self-calls the working
    internal listener instead of the host-published port (dead on
    port-bumped installs). config_map wins over ChatConfig's env resolution,
    so pinning the raw public URL here used to override the fixed resolver.
    Empty internal/public values are skipped, mirroring
    _resolve_nextseek_base_url. Deliberately duplicated in cli.py AND
    evaluator/runner.py — keep both identical
    (tests/evaluator/test_prod_base_url_chain.py locks the parity).
    """
    value = os.getenv("NEXTSEEK_PROD_URL")
    if value is not None:
        return value
    return os.getenv("NEXTSEEK_INTERNAL_BASE_URL") or os.getenv("NEXTSEEK_BASE_URL")


def _prod_base_url_source() -> str:
    """Env var name currently supplying the base URL (keeps
    CONFIG_SOURCE_ENV_NAMES truthful for the chain above)."""
    if os.getenv("NEXTSEEK_PROD_URL") is not None:
        return "NEXTSEEK_PROD_URL"
    if os.getenv("NEXTSEEK_INTERNAL_BASE_URL"):
        return "NEXTSEEK_INTERNAL_BASE_URL"
    return "NEXTSEEK_BASE_URL"


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
                "base_url": _prod_base_url_source(),
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
            # Non-prod runs resolve through _resolve_nextseek_base_url, which
            # prefers the internal transport var — report the actual source.
            "base_url": (
                "NEXTSEEK_INTERNAL_BASE_URL"
                if os.getenv("NEXTSEEK_INTERNAL_BASE_URL")
                else "NEXTSEEK_BASE_URL"
            ),
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
    nextseek_base = _prod_base_url()
    api_user = _prod_value("API_USER_PROD", "API_USER")
    api_pass = os.getenv("API_PASS_PROD")
    if api_pass is None:
        api_pass = os.getenv("API_PASS")
    neo4j_uri = _prod_value("NEO4J_URI_PROD", "NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = _prod_value("NEO4J_USER_PROD", "NEO4J_USER", "neo4j")
    neo4j_database = _prod_value("NEO4J_DATABASE_PROD", "NEO4J_DATABASE", "neo4j")

    if nextseek_base is not None:
        config_map["NEXTSEEK_BASE_URL"] = nextseek_base.rstrip("/")
        config_map["NEXTSEEK_INTERNAL_BASE_URL"] = nextseek_base.rstrip("/")
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



def cmd_smart_test(args: argparse.Namespace) -> int:
    """Route -st (smart test) and -ft (full test) through the new e2e.py runner.

    Legacy flag semantics:
      -st               -> e2e --ratio 0.33 (default sample)
      -ft               -> e2e --ratio full (all variants)
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
    parser.add_argument(
        "-st", "--smart-test",
        action="store_true",
        default=False,
        dest="smart_test",
        help="Run E2E test suite (default ratio 0.33). For advanced flags use: uv run e2e.py --help",
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



    if args.query_plan:
        return cmd_query_plan(args)

    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
