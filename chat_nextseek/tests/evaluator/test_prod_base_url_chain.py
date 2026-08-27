"""--prod base-URL fallback chain (review follow-up FU5, 2026-07-07).

With --prod enabled and NEXTSEEK_PROD_URL unset, cli.py and evaluator/runner.py
used to pin the PUBLIC ``NEXTSEEK_BASE_URL`` into
``config_map["NEXTSEEK_BASE_URL"]``. config_map wins over all of ChatConfig's
env resolution (env fills gaps only), so the pin overrode the
internal-preferred ``_resolve_nextseek_base_url`` — actively re-breaking REST
self-calls on port-bumped installs during manual in-container --prod runs.

The chain is now ``NEXTSEEK_PROD_URL → NEXTSEEK_INTERNAL_BASE_URL →
NEXTSEEK_BASE_URL`` (empty internal/public values skipped, mirroring the
resolver), and CONFIG_SOURCE_ENV_NAMES reporting stays truthful. Both files
duplicate the helpers, so both are exercised.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from chat_nextseek.evaluator import runner


def _load_cli_module():
    path = Path(__file__).resolve().parents[2] / "cli.py"
    spec = importlib.util.spec_from_file_location("_cli_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_cli = _load_cli_module()


@pytest.fixture(params=[_cli, runner], ids=["cli", "runner"])
def mod(request):
    return request.param


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "NEXTSEEK_PROD_URL",
        "NEXTSEEK_INTERNAL_BASE_URL",
        "NEXTSEEK_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


class TestProdConfigMapBaseUrl:
    def test_internal_wins_over_public_when_prod_url_unset(self, mod, monkeypatch):
        monkeypatch.setenv("NEXTSEEK_INTERNAL_BASE_URL", "http://127.0.0.1:8000/")
        monkeypatch.setenv("NEXTSEEK_BASE_URL", "http://127.0.0.1:8001")
        cm = mod._build_prod_config_map(True)
        assert cm["NEXTSEEK_BASE_URL"] == "http://127.0.0.1:8000"

    def test_prod_url_still_wins_over_internal(self, mod, monkeypatch):
        monkeypatch.setenv("NEXTSEEK_PROD_URL", "https://nextseek.example.edu")
        monkeypatch.setenv("NEXTSEEK_INTERNAL_BASE_URL", "http://127.0.0.1:8000")
        monkeypatch.setenv("NEXTSEEK_BASE_URL", "http://127.0.0.1:8001")
        cm = mod._build_prod_config_map(True)
        assert cm["NEXTSEEK_BASE_URL"] == "https://nextseek.example.edu"

    def test_public_fallback_when_internal_unset(self, mod, monkeypatch):
        monkeypatch.setenv("NEXTSEEK_BASE_URL", "http://127.0.0.1:8001")
        cm = mod._build_prod_config_map(True)
        assert cm["NEXTSEEK_BASE_URL"] == "http://127.0.0.1:8001"

    def test_empty_internal_skipped(self, mod, monkeypatch):
        monkeypatch.setenv("NEXTSEEK_INTERNAL_BASE_URL", "")
        monkeypatch.setenv("NEXTSEEK_BASE_URL", "http://127.0.0.1:8001")
        cm = mod._build_prod_config_map(True)
        assert cm["NEXTSEEK_BASE_URL"] == "http://127.0.0.1:8001"

    def test_no_pin_when_nothing_set(self, mod):
        cm = mod._build_prod_config_map(True)
        assert "NEXTSEEK_BASE_URL" not in cm


class TestSourceNameReporting:
    def test_reports_internal_when_it_supplies_the_value(self, mod, monkeypatch):
        monkeypatch.setenv("NEXTSEEK_INTERNAL_BASE_URL", "http://127.0.0.1:8000")
        monkeypatch.setenv("NEXTSEEK_BASE_URL", "http://127.0.0.1:8001")
        names = mod._build_prod_source_env_names(True)
        assert names["api"]["base_url"] == "NEXTSEEK_INTERNAL_BASE_URL"

    def test_reports_prod_url_when_set(self, mod, monkeypatch):
        monkeypatch.setenv("NEXTSEEK_PROD_URL", "https://nextseek.example.edu")
        monkeypatch.setenv("NEXTSEEK_INTERNAL_BASE_URL", "http://127.0.0.1:8000")
        names = mod._build_prod_source_env_names(True)
        assert names["api"]["base_url"] == "NEXTSEEK_PROD_URL"

    def test_reports_public_when_only_it_is_set(self, mod, monkeypatch):
        monkeypatch.setenv("NEXTSEEK_BASE_URL", "http://127.0.0.1:8001")
        names = mod._build_prod_source_env_names(True)
        assert names["api"]["base_url"] == "NEXTSEEK_BASE_URL"

    def test_disabled_mode_reports_resolver_source(self, mod, monkeypatch):
        """Non-prod runs resolve through _resolve_nextseek_base_url, which
        prefers the internal var — the report must say so."""
        monkeypatch.setenv("NEXTSEEK_INTERNAL_BASE_URL", "http://127.0.0.1:8000")
        names = mod._build_prod_source_env_names(False)
        assert names["api"]["base_url"] == "NEXTSEEK_INTERNAL_BASE_URL"

    def test_disabled_mode_reports_public_without_internal(self, mod):
        names = mod._build_prod_source_env_names(False)
        assert names["api"]["base_url"] == "NEXTSEEK_BASE_URL"


class TestProdInjectionUsesWinningVar:
    """R1 permanent (2026-07-08): --prod injects the prod URL into the resolver's
    WINNING var (NEXTSEEK_INTERNAL_BASE_URL) as well as NEXTSEEK_BASE_URL, at the
    single canonical builder _build_prod_config_map."""

    def test_config_map_emits_prod_under_both_base_url_vars(self, mod, monkeypatch):
        monkeypatch.setenv("NEXTSEEK_PROD_URL", "http://prod.example")
        monkeypatch.setenv("NEXTSEEK_INTERNAL_BASE_URL", "http://127.0.0.1:8000")
        monkeypatch.setenv("NEXTSEEK_BASE_URL", "http://127.0.0.1:8001")
        cm = mod._build_prod_config_map(True)
        assert cm["NEXTSEEK_BASE_URL"] == "http://prod.example"
        assert cm["NEXTSEEK_INTERNAL_BASE_URL"] == "http://prod.example"

    def test_naive_injection_site_without_pop_still_resolves_prod(self, monkeypatch):
        import os

        monkeypatch.setenv("NEXTSEEK_PROD_URL", "http://prod.example")
        monkeypatch.setenv("NEXTSEEK_INTERNAL_BASE_URL", "http://127.0.0.1:8000")
        env = os.environ.copy()
        env.update({k: v for k, v in _cli._build_prod_config_map(True).items() if isinstance(v, str)})
        resolved = env.get("NEXTSEEK_INTERNAL_BASE_URL") or env.get("NEXTSEEK_BASE_URL")
        assert resolved == "http://prod.example"

    def test_reported_base_url_source_matches_resolved_value(self, monkeypatch):
        import json as _json
        import os

        monkeypatch.setenv("NEXTSEEK_PROD_URL", "http://prod.example")
        monkeypatch.setenv("NEXTSEEK_INTERNAL_BASE_URL", "http://127.0.0.1:8000")
        env = _cli._build_prod_subprocess_env(True)
        resolved = env.get("NEXTSEEK_INTERNAL_BASE_URL") or env.get("NEXTSEEK_BASE_URL")
        assert resolved == "http://prod.example"
        reported = _json.loads(env["CHAT_NEXTSEEK_CONFIG_SOURCE_ENV_NAMES"])
        assert reported["api"]["base_url"] == "NEXTSEEK_PROD_URL"
        assert os.environ[reported["api"]["base_url"]].rstrip("/") == resolved


class TestHelperParity:
    def test_cli_and_runner_base_url_helpers_stay_identical(self):
        """The helpers are deliberately duplicated in cli.py and runner.py;
        a fix landing in only one file resurrects the bug in the other."""
        import inspect

        for name in ("_prod_base_url", "_prod_base_url_source"):
            cli_src = inspect.getsource(getattr(_cli, name))
            runner_src = inspect.getsource(getattr(runner, name))
            assert cli_src == runner_src, f"{name} drifted between cli.py and runner.py"
