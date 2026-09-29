"""
Single-operator surfaces run as graph admin only by an explicit opt-in, never by default.

Spec docs/superpowers/specs/2026-09-18-graph-cypher-scope.md sections 3.2 (S10), 4.2 and 11.4: the chat_nextseek CLI
(``--graph-admin``, or ``CHAT_NEXTSEEK_GRAPH_ADMIN=1``), the MCP server, the Streamlit app, the evaluator CLI and its
demo server, and the operator's venue check put ``operator_scope_from_env(<source>)`` on their own config. Without the
opt-in they carry no scope: every graph query refuses and falls back, and the catalog is redacted; the CLI says so
on stderr. Nothing a served process runs reads the variable.

No model, database or network is reached: each surface's config class and runner are replaced.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from NessieAI import paths
from chat_nextseek.graph_scope import OPERATOR_OPT_IN_ENV, SCOPE_ATTR, GraphScope, scope_of

REPO = paths.NESSIE_ROOT.parent
CHAT = paths.CHAT_NEXTSEEK_DIR
VENUE_CHECK = REPO / "scripts" / "graph_search" / "nessie_venue_check.py"
VENUE_SH = REPO / "scripts" / "graph_search" / "nessie_venue.sh"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _no_scope(config) -> bool:
    """Carries the attribute, set to None: the surface decided, and decided no scope."""
    return hasattr(config, SCOPE_ATTR) and getattr(config, SCOPE_ATTR) is None


class _FakeConfig:
    """Stands in for ChatConfig: what the surfaces read, nothing that reaches a network."""

    def __init__(self, config_map=None, **_):
        self.config_map = config_map
        self.SESSION_DB_TYPE = "sqlite"
        self.SESSION_DB_PATH = ":memory:"


# --------------------------------------------------------------------------- #
# The CLI
# --------------------------------------------------------------------------- #

@pytest.fixture
def cli():
    return _load(CHAT / "cli.py", "_cli_scope_under_test")


def _cli_run(cli, monkeypatch, argv, *, entry="run_query"):
    seen = {}

    def runner(session, config, text, *a, **k):
        seen["config"] = config
        seen["kwargs"] = k
        return {"reply": "ok"}

    monkeypatch.setattr("chat_nextseek.config.ChatConfig", _FakeConfig)
    monkeypatch.setattr("chat_nextseek.session.SQLiteSessionState", lambda path, user: {})
    monkeypatch.setattr(f"chat_nextseek.orchestrator.{entry}", runner)
    assert cli.main(argv) == 0
    return seen


@pytest.mark.parametrize("argv, entry", [(["-q", "how many"], "run_query"), (["-qp", "how many"], "run_query_plan")])
def test_the_cli_without_the_opt_in_carries_no_scope_and_says_so(cli, monkeypatch, capsys, argv, entry):
    monkeypatch.delenv(OPERATOR_OPT_IN_ENV, raising=False)

    seen = _cli_run(cli, monkeypatch, argv, entry=entry)

    assert _no_scope(seen["config"])
    err = [line for line in capsys.readouterr().err.splitlines() if "--graph-admin" in line]
    assert len(err) == 1
    assert "fall back" in err[0] and "redacted" in err[0]


@pytest.mark.parametrize("argv, entry", [(["-q", "how many", "--graph-admin"], "run_query"),
                                         (["-qp", "how many", "--graph-admin"], "run_query_plan")])
def test_the_cli_flag_makes_the_operator_admin(cli, monkeypatch, capsys, argv, entry):
    monkeypatch.delenv(OPERATOR_OPT_IN_ENV, raising=False)

    seen = _cli_run(cli, monkeypatch, argv, entry=entry)

    assert scope_of(seen["config"]) == GraphScope.admin("cli")
    assert "--graph-admin" not in capsys.readouterr().err


@pytest.mark.parametrize("value, admin", [("1", True), ("true", False), ("yes", False), ("0", False), ("", False)])
def test_the_cli_reads_the_variable_as_exactly_one(cli, monkeypatch, value, admin):
    monkeypatch.setenv(OPERATOR_OPT_IN_ENV, value)

    seen = _cli_run(cli, monkeypatch, ["-q", "how many"])

    assert (scope_of(seen["config"]) == GraphScope.admin("cli")) is admin


def test_the_cli_leaves_the_orchestrators_keyword_alone(cli, monkeypatch):
    """The CLI's scope is on its own config; the orchestrator keeps it because graph_scope is not passed."""
    monkeypatch.setenv(OPERATOR_OPT_IN_ENV, "1")

    seen = _cli_run(cli, monkeypatch, ["-q", "how many"])

    assert "graph_scope" not in seen["kwargs"]


def test_the_streamlit_launch_passes_the_flag_as_the_variable(cli, monkeypatch):
    monkeypatch.delenv(OPERATOR_OPT_IN_ENV, raising=False)
    calls = []
    monkeypatch.setattr(cli, "_run", lambda command, env=None, **k: calls.append(env) or 0)

    assert cli.main(["-s", "--graph-admin"]) == 0
    assert cli.main(["-s"]) == 0

    assert calls[0][OPERATOR_OPT_IN_ENV] == "1"
    assert OPERATOR_OPT_IN_ENV not in (calls[1] or {})


# --------------------------------------------------------------------------- #
# The MCP server
# --------------------------------------------------------------------------- #

@pytest.fixture
def mcp_server():
    pytest.importorskip("mcp.server.fastmcp")
    return _load(CHAT / "mcp_server.py", "_mcp_scope_under_test")


@pytest.mark.parametrize("value, expected", [("1", GraphScope.admin("mcp")), (None, None), ("true", None)])
def test_the_mcp_config_opts_in_only_by_the_variable(mcp_server, monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(OPERATOR_OPT_IN_ENV, raising=False)
    else:
        monkeypatch.setenv(OPERATOR_OPT_IN_ENV, value)
    monkeypatch.setattr(mcp_server, "ChatConfig", _FakeConfig)
    monkeypatch.setattr(mcp_server, "_config", None)

    config = mcp_server._cfg()

    assert hasattr(config, SCOPE_ATTR)
    assert scope_of(config) == expected
    assert mcp_server._cfg() is config, "one process-wide config"


# --------------------------------------------------------------------------- #
# The Streamlit app (read by its source: importing it starts Streamlit)
# --------------------------------------------------------------------------- #

def test_the_app_config_is_scoped_by_the_variable():
    tree = ast.parse((CHAT / "app.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_get_config")
    ret = next(n for n in ast.walk(fn) if isinstance(n, ast.Return))
    assert ast.unparse(ret.value) == "with_scope(ChatConfig(), operator_scope_from_env('app'))"


# --------------------------------------------------------------------------- #
# The evaluator CLI and its demo server
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("value, expected", [("1", GraphScope.admin("evaluator")), (None, None)])
def test_the_evaluator_config_opts_in_only_by_the_variable(monkeypatch, value, expected):
    from chat_nextseek.evaluator import runner

    if value is None:
        monkeypatch.delenv(OPERATOR_OPT_IN_ENV, raising=False)
    else:
        monkeypatch.setenv(OPERATOR_OPT_IN_ENV, value)
    monkeypatch.setattr("chat_nextseek.config.ChatConfig", _FakeConfig)

    config = runner._build_chat_config(False, "gcp")

    assert hasattr(config, SCOPE_ATTR)
    assert scope_of(config) == expected
    assert config.config_map["MODEL_MODE"] == "gcp"


@pytest.mark.parametrize("value, expected", [("1", GraphScope.admin("evaluator")), (None, None)])
def test_the_demo_server_default_config_opts_in_only_by_the_variable(monkeypatch, value, expected):
    pytest.importorskip("aiohttp")
    pytest.importorskip("aiohttp_sse")
    from chat_nextseek.evaluator.demo import server

    if value is None:
        monkeypatch.delenv(OPERATOR_OPT_IN_ENV, raising=False)
    else:
        monkeypatch.setenv(OPERATOR_OPT_IN_ENV, value)
    monkeypatch.setattr(server, "ChatConfig", _FakeConfig)

    app = server.create_app(workflow=MagicMock())

    config = app["evaluator"]._config
    assert hasattr(config, SCOPE_ATTR)
    assert scope_of(config) == expected


def test_a_config_handed_to_the_demo_server_keeps_its_own_scope(monkeypatch):
    pytest.importorskip("aiohttp")
    pytest.importorskip("aiohttp_sse")
    from chat_nextseek.evaluator.demo import server

    given = SimpleNamespace(**{SCOPE_ATTR: GraphScope.admin("evaluator")})
    app = server.create_app(config=given, workflow=MagicMock())

    assert app["evaluator"]._config is given


# --------------------------------------------------------------------------- #
# The venue check
# --------------------------------------------------------------------------- #

@pytest.fixture
def venue():
    return _load(VENUE_CHECK, "_venue_check_scope_under_test")


def test_the_venue_check_config_is_admin_only_by_the_variable(venue):
    singleton = SimpleNamespace(NEO4J_URI="bolt://x")

    admin = venue.venue_graph_config(singleton, {OPERATOR_OPT_IN_ENV: "1"})
    plain = venue.venue_graph_config(singleton, {})

    assert scope_of(admin) == GraphScope.admin("venue-check")
    assert _no_scope(plain)
    assert admin is not singleton and plain is not singleton
    assert not hasattr(singleton, SCOPE_ATTR)


def test_the_venue_script_exports_the_variable_for_the_check_step_only():
    text = VENUE_SH.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if OPERATOR_OPT_IN_ENV in line and not line.lstrip().startswith("#")]
    assert len(lines) == 1, lines
    body = text[text.index("cmd_check() {"):]
    body = body[: body.index("\n}\n")]
    assert f"-e {OPERATOR_OPT_IN_ENV}=1" in body


# --------------------------------------------------------------------------- #
# Nothing else reads the variable
# --------------------------------------------------------------------------- #

ALLOWED_READERS = {
    "NessieAI/chat_nextseek/src/chat_nextseek/graph_scope.py",
    "NessieAI/chat_nextseek/cli.py",
    "NessieAI/chat_nextseek/mcp_server.py",
    "NessieAI/chat_nextseek/app.py",
    "NessieAI/chat_nextseek/src/chat_nextseek/evaluator/runner.py",
    "NessieAI/chat_nextseek/src/chat_nextseek/evaluator/demo/server.py",
    "scripts/graph_search/nessie_venue_check.py",
    "scripts/graph_search/nessie_venue.sh",
}


def test_only_the_single_operator_surfaces_read_the_opt_in():
    found = set()
    for root in ("NessieAI", "nextseek_api", "dmac", "seek", "scripts", "startup", "ci", "docker"):
        base = REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix not in (".py", ".sh") or "tests" in path.parts or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if OPERATOR_OPT_IN_ENV in text or "operator_scope_from_env" in text:
                found.add(str(path.relative_to(REPO)))
    assert found == ALLOWED_READERS
