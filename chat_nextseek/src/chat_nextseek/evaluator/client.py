"""BAML client adapter for native evaluator calls.

Wraps the generated ``baml_client`` runtime and translates local retry-context
models into the vendored evaluator input types.
Use this module when invoking BAML or swapping in a test double.
Invariant: all evaluator BAML calls flow through one adapter seam.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .models import EvaluatorRetryContextResponse, EvaluatorRetrySignals


_SEARCH_MODES = {"new_search", "refine_last_search"}
_ENV_KEYS = (
    "OPENAI_API_KEY",
    "GCP_API_KEY",
    "NEXTSEEK_BASE_URL",
    "API_USER",
    "API_PASS",
)
_BAML_CLIENT_DIR = Path(__file__).resolve().parents[2] / "baml_client"


def _build_module_spec(
    fullname: str,
    source_path: Path,
    *,
    is_package: bool,
) -> importlib.machinery.ModuleSpec:
    if source_path.suffix == ".pyc":
        loader = importlib.machinery.SourcelessFileLoader(fullname, str(source_path))
    else:
        loader = importlib.machinery.SourceFileLoader(fullname, str(source_path))
    kwargs = {"loader": loader}
    if is_package:
        kwargs["submodule_search_locations"] = [str(source_path.parent if source_path.name == "__init__.py" else _BAML_CLIENT_DIR)]
    spec = importlib.util.spec_from_file_location(fullname, source_path, **kwargs)
    if spec is None:  # pragma: no cover
        raise ImportError(f"Unable to build import spec for {fullname} from {source_path}")
    return spec


class _BamlPycFinder(importlib.abc.MetaPathFinder):
    """Allow importing vendored BAML modules when only compiled artifacts are present."""

    def find_spec(self, fullname: str, path=None, target=None):
        if fullname != "baml_client" and not fullname.startswith("baml_client."):
            return None

        if fullname == "baml_client":
            if (_BAML_CLIENT_DIR / "__init__.py").exists():
                return _build_module_spec(fullname, _BAML_CLIENT_DIR / "__init__.py", is_package=True)
            matches = sorted((_BAML_CLIENT_DIR / "__pycache__").glob("__init__*.pyc"))
            if matches:
                return _build_module_spec(fullname, matches[0], is_package=True)
            return None

        relative_name = fullname.removeprefix("baml_client.")
        parts = relative_name.split(".")
        module_dir = _BAML_CLIENT_DIR.joinpath(*parts[:-1])
        module_name = parts[-1]

        package_init = module_dir / module_name / "__init__.py"
        if package_init.exists():
            return _build_module_spec(fullname, package_init, is_package=True)

        package_pyc = sorted((module_dir / module_name / "__pycache__").glob("__init__*.pyc"))
        if package_pyc:
            return _build_module_spec(fullname, package_pyc[0], is_package=True)

        source_file = module_dir / f"{module_name}.py"
        if source_file.exists():
            return _build_module_spec(fullname, source_file, is_package=False)

        pyc_matches = sorted((module_dir / "__pycache__").glob(f"{module_name}.cpython-*.pyc"))
        if pyc_matches:
            return _build_module_spec(fullname, pyc_matches[0], is_package=False)
        return None


def _install_baml_import_fallback() -> None:
    if not _BAML_CLIENT_DIR.exists():
        return

    if not any(isinstance(finder, _BamlPycFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _BamlPycFinder())

    module = sys.modules.get("baml_client")
    if module is not None and getattr(module, "__file__", None) is None:
        del sys.modules["baml_client"]


_install_baml_import_fallback()


def _get_default_runtime() -> Any:
    from baml_client.sync_client import b

    return b


def build_baml_env(
    *,
    env: Mapping[str, str | None] | None = None,
    config: Any | None = None,
) -> dict[str, str]:
    """Build the repo-native environment mapping passed into the BAML runtime."""

    resolved: dict[str, str] = {}
    source: Mapping[str, str | None] = env if env is not None else os.environ
    for key in _ENV_KEYS:
        value = source.get(key)
        if value is None and config is not None:
            value = getattr(config, key, None)
        if value:
            resolved[key] = value
    return resolved


def convert_signals(signals: EvaluatorRetrySignals, *, retryable: bool) -> Any:
    """Convert normalized retry signals into the BAML evaluator input shape."""

    from baml_client.types import EvalRetryContext as BamlEvalRetryContext
    from baml_client.types import GraphSignals, PlanSignals, SearchSignals

    search = None
    graph = None
    plan = None

    if signals.path_mode in _SEARCH_MODES:
        search = SearchSignals(
            api_ok=signals.api_ok,
            api_status_code=signals.api_status_code,
            rows_returned=signals.rows_returned,
        )
    elif signals.path_mode == "graph_query":
        graph = GraphSignals(
            graph_ok=signals.graph_ok,
            rows_returned=signals.rows_returned,
        )
    elif signals.path_mode == "plan":
        plan = PlanSignals(
            steps_total=signals.plan_steps_total,
            steps_executed=signals.plan_steps_executed,
            steps_ok=signals.plan_steps_ok,
            steps_failed=signals.plan_steps_failed,
            stop_reason=signals.plan_stop_reason,
        )

    return BamlEvalRetryContext(
        retryable=retryable,
        query_error_present=signals.query_error_present,
        raw_error_excerpt=signals.raw_error_excerpt,
        bundle_present=signals.bundle_present,
        has_artifacts=signals.has_artifacts,
        has_prior_context=signals.has_prior_context,
        search_signals=search,
        graph_signals=graph,
        plan_signals=plan,
    )


def convert_to_baml_input(response: EvaluatorRetryContextResponse) -> Any:
    """Translate the local evaluator contract into the vendored BAML input model."""

    from baml_client.types import (
        AssistantStatus,
        EvalLookup,
        EvalRouting,
        EvalRunMeta,
        EvaluatorInput,
        ExecutionMode,
        PathMode,
    )

    status_map = {
        "pending": AssistantStatus.PENDING,
        "running": AssistantStatus.RUNNING,
        "completed": AssistantStatus.COMPLETED,
        "error": AssistantStatus.ERROR,
        "partial": AssistantStatus.ERROR,
    }
    execution_mode_map = {
        "standard": ExecutionMode.STANDARD,
        "plan": ExecutionMode.PLAN,
    }
    path_mode_map = {
        "new_search": PathMode.NEW_SEARCH,
        "refine_last_search": PathMode.REFINE_LAST_SEARCH,
        "graph_query": PathMode.GRAPH_QUERY,
        "reporter": PathMode.REPORTER,
        "system_question": PathMode.SYSTEM_QUESTION,
        "ask_about_last_results": PathMode.ASK_ABOUT_LAST_RESULTS,
        "unsupported": PathMode.UNSUPPORTED,
        "plan": PathMode.PLAN,
    }

    return EvaluatorInput(
        lookup=EvalLookup(
            task_id=str(response.lookup.task_id) if response.lookup.task_id else None,
            session_id=str(response.lookup.session_id) if response.lookup.session_id else None,
            bundle_id=response.lookup.bundle_id,
            source=response.lookup.source,
        ),
        run=EvalRunMeta(
            status=status_map.get(response.run.status, AssistantStatus.ERROR),
            query=response.run.query,
            reply=response.run.reply,
        ),
        routing=EvalRouting(
            execution_mode=execution_mode_map.get(response.routing.execution_mode, ExecutionMode.STANDARD),
            path_mode=path_mode_map.get(response.routing.path_mode, PathMode.UNSUPPORTED),
            path_subtype=response.routing.path_subtype,
        ),
        retry_context=convert_signals(
            response.retry_context.retry_signals,
            retryable=response.retry_context.retryable,
        ),
    )


@dataclass(frozen=True)
class EvaluatorBamlResult:
    """Combined judgment and retry decision produced by the local BAML runtime."""

    judgment: Any
    retry_decision: Any


class EvaluatorBamlClient:
    """Thin local adapter around the vendored BAML client."""

    def __init__(self, *, baml_runtime: Any | None = None) -> None:
        self._baml_runtime = baml_runtime

    def _runtime_with_env(
        self,
        *,
        env: Mapping[str, str | None] | None = None,
        config: Any | None = None,
    ) -> Any:
        runtime_env = build_baml_env(env=env, config=config)
        if self._baml_runtime is None:
            self._baml_runtime = _get_default_runtime()
        if hasattr(self._baml_runtime, "with_options"):
            return self._baml_runtime.with_options(env=runtime_env)
        return self._baml_runtime

    def judge_response(
        self,
        response: EvaluatorRetryContextResponse,
        *,
        env: Mapping[str, str | None] | None = None,
        config: Any | None = None,
    ) -> Any:
        runtime = self._runtime_with_env(env=env, config=config)
        payload = convert_to_baml_input(response)
        return runtime.JudgeAssistantResponse(input=payload)

    def decide_retry(
        self,
        response: EvaluatorRetryContextResponse,
        judgment: Any,
        *,
        env: Mapping[str, str | None] | None = None,
        config: Any | None = None,
    ) -> Any:
        runtime = self._runtime_with_env(env=env, config=config)
        payload = convert_to_baml_input(response)
        return runtime.DecideRetry(input=payload, judgment=judgment)

    def evaluate(
        self,
        response: EvaluatorRetryContextResponse,
        *,
        env: Mapping[str, str | None] | None = None,
        config: Any | None = None,
    ) -> tuple[Any, Any]:
        judgment = self.judge_response(response, env=env, config=config)
        decision = self.decide_retry(response, judgment, env=env, config=config)
        return judgment, decision
