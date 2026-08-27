from __future__ import annotations

import importlib.util
from pathlib import Path
import re

import tomllib

from chat_nextseek.evaluator.client import (
    EvaluatorBamlResult,
    EvaluatorBamlClient,
    _get_default_runtime,
    build_baml_env,
    convert_signals,
    convert_to_baml_input,
)
from chat_nextseek.evaluator.models import (
    EvaluatorLookup,
    EvaluatorRawPayloads,
    EvaluatorRetryContext,
    EvaluatorRetryContextResponse,
    EvaluatorRetrySignals,
    EvaluatorRouting,
    EvaluatorRunMeta,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeBamlRuntime:
    def __init__(self):
        self.last_env = None

    def with_options(self, *, env):
        self.last_env = env
        return self

    def JudgeAssistantResponse(self, *, input):
        return {"kind": "judgment", "path_mode": input.routing.path_mode}

    def DecideRetry(self, *, input, judgment):
        return {"kind": "decision", "judgment": judgment, "retryable": input.retry_context.retryable}


class _BareRuntime:
    def JudgeAssistantResponse(self, *, input):
        return {"kind": "judgment", "path_mode": input.routing.path_mode}

    def DecideRetry(self, *, input, judgment):
        return {"kind": "decision", "judgment": judgment, "retryable": input.retry_context.retryable}


def _sample_response() -> EvaluatorRetryContextResponse:
    return EvaluatorRetryContextResponse(
        lookup=EvaluatorLookup(source="bundle", session_id="00000000-0000-0000-0000-000000000001", bundle_id=7),
        run=EvaluatorRunMeta(status="completed", query="find ndma mice", reply="Found 3 samples."),
        routing=EvaluatorRouting(execution_mode="standard", path_mode="new_search"),
        retry_context=EvaluatorRetryContext(
            retryable=True,
            retry_signals=EvaluatorRetrySignals(
                assistant_status="completed",
                bundle_present=True,
                path_mode="new_search",
                api_ok=True,
                api_status_code=200,
                rows_returned=3,
            ),
        ),
        raw=EvaluatorRawPayloads(bundle={"id": 7}),
    )


def test_baml_client_import_shape():
    import baml_client
    from baml_client import sync_client, types

    generators = (REPO_ROOT / "src/chat_nextseek/evaluator/baml_src/generators.baml").read_text(encoding="utf-8")
    expected = re.search(r'version "([^"]+)"', generators)
    assert expected is not None
    assert baml_client.__version__ == expected.group(1)
    assert hasattr(sync_client, "b")
    assert hasattr(types, "EvaluatorInput")


def test_repo_native_env_mapping():
    clients_baml = (REPO_ROOT / "src/chat_nextseek/evaluator/baml_src/clients.baml").read_text(encoding="utf-8")
    client_py = (REPO_ROOT / "src/chat_nextseek/evaluator/client.py").read_text(encoding="utf-8")

    assert "GCP_API_KEY" in clients_baml
    assert "GOOGLE_API_KEY" not in clients_baml
    assert importlib.util.find_spec("baml_client.inlinedbaml") is not None
    assert "SEEK_USER" not in client_py
    assert "SEEK_PASSWORD" not in client_py


def test_baml_runtime_dependency_is_available():
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    assert isinstance(dependencies, list)
    assert importlib.util.find_spec("baml_py") is not None


def test_convert_to_baml_input():
    payload = convert_to_baml_input(_sample_response())

    assert payload.routing.path_mode.value == "NEW_SEARCH"
    assert payload.run.status.value == "COMPLETED"
    assert payload.retry_context.search_signals.rows_returned == 3


def test_evaluator_client_uses_repo_native_env(monkeypatch):
    runtime = _FakeBamlRuntime()
    client = EvaluatorBamlClient(baml_runtime=runtime)

    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("GCP_API_KEY", "gcp-key")
    monkeypatch.setenv("NEXTSEEK_BASE_URL", "https://example.test")
    monkeypatch.setenv("API_USER", "user")
    monkeypatch.setenv("API_PASS", "pass")

    judgment, decision = client.evaluate(_sample_response())

    assert runtime.last_env == build_baml_env()
    assert judgment["kind"] == "judgment"
    assert decision["kind"] == "decision"


def test_build_baml_env_uses_config_fallback():
    class _Config:
        OPENAI_API_KEY = "openai-key"
        GCP_API_KEY = "gcp-key"
        NEXTSEEK_BASE_URL = "https://example.test"
        API_USER = "user"
        API_PASS = "pass"

    assert build_baml_env(env={}, config=_Config()) == {
        "OPENAI_API_KEY": "openai-key",
        "GCP_API_KEY": "gcp-key",
        "NEXTSEEK_BASE_URL": "https://example.test",
        "API_USER": "user",
        "API_PASS": "pass",
    }


# --- Review follow-up FU5 hygiene (2026-07-07): no .baml source dereferences
# NEXTSEEK_BASE_URL today, but the key is kept for forward-compat — so it must
# carry the RESOLVED transport URL (ChatConfig attribute, internal-preferred),
# never the raw public env var, which is dead on port-bumped installs.


def test_build_baml_env_base_url_prefers_config_attr_over_raw_env():
    class _Config:
        NEXTSEEK_BASE_URL = "http://127.0.0.1:8000"  # resolver output

    env = {
        "NEXTSEEK_BASE_URL": "http://127.0.0.1:8001",  # public, port-bumped
        "API_USER": "user",
    }
    resolved = build_baml_env(env=env, config=_Config())
    assert resolved["NEXTSEEK_BASE_URL"] == "http://127.0.0.1:8000"
    assert resolved["API_USER"] == "user"  # other keys keep env-first order


def test_build_baml_env_base_url_prefers_internal_env_without_config():
    env = {
        "NEXTSEEK_INTERNAL_BASE_URL": "http://127.0.0.1:8000",
        "NEXTSEEK_BASE_URL": "http://127.0.0.1:8001",
    }
    assert build_baml_env(env=env)["NEXTSEEK_BASE_URL"] == "http://127.0.0.1:8000"


def test_build_baml_env_base_url_public_fallback_unchanged():
    env = {"NEXTSEEK_BASE_URL": "http://127.0.0.1:8001"}
    assert build_baml_env(env=env)["NEXTSEEK_BASE_URL"] == "http://127.0.0.1:8001"


def test_convert_signals_handles_graph_and_plan_modes():
    graph = convert_signals(
        EvaluatorRetrySignals(path_mode="graph_query", graph_ok=True, rows_returned=2),
        retryable=True,
    )
    plan = convert_signals(
        EvaluatorRetrySignals(
            path_mode="plan",
            plan_steps_total=3,
            plan_steps_executed=2,
            plan_steps_ok=2,
            plan_steps_failed=0,
            plan_stop_reason="done",
        ),
        retryable=False,
    )

    assert graph.graph_signals.graph_ok is True
    assert plan.plan_signals.steps_total == 3
    assert plan.retryable is False


def test_default_runtime_and_bare_runtime_paths():
    from baml_client.sync_client import b

    assert _get_default_runtime() is b

    client = EvaluatorBamlClient(baml_runtime=_BareRuntime())
    judgment = client.judge_response(_sample_response(), env={})
    decision = client.decide_retry(_sample_response(), judgment, env={})
    result = EvaluatorBamlResult(judgment=judgment, retry_decision=decision)

    assert result.judgment["kind"] == "judgment"
    assert result.retry_decision["kind"] == "decision"
