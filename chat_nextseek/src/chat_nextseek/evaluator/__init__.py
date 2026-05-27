"""Native NExtSEEK evaluator package.

Runs evaluator judgments and retry decisions against orchestrator runs.
Entry points live in :mod:`chat_nextseek.evaluator.runner` and
:mod:`chat_nextseek.evaluator.workflow`.
Invariants: evaluator-internal changes stay inside this package and
the generated ``src/baml_client/`` client remains regenerable output.
"""
from .contracts import (
    RUN_INDEX_FILENAME,
    RUNS_DIRNAME,
    EvaluatorRunQuery,
    EvaluatorRunRepository,
    get_evaluator_state_root,
    get_run_index_path,
    get_run_record_path,
    get_run_records_dir,
    get_xdg_state_home,
)
from .models import (
    EvaluatorLookup,
    EvaluatorRawPayloads,
    EvaluatorRetryContext,
    EvaluatorRetryContextResponse,
    EvaluatorRetrySignals,
    EvaluatorRouting,
    EvaluatorRunMeta,
    EvaluatorRunRecord,
    EvaluatorRunSummary,
    EvaluatorRunsListResponse,
    RetryRequest,
    RetryResponse,
)

try:
    from .client import EvaluatorBamlClient, EvaluatorBamlResult, build_baml_env, convert_to_baml_input
except ImportError:  # pragma: no cover - exercised when BAML runtime is unavailable
    EvaluatorBamlClient = None
    EvaluatorBamlResult = None
    build_baml_env = None
    convert_to_baml_input = None

from .normalization import build_retry_context, classify_path, normalize_from_bundle, normalize_from_task
from .workflow import EvaluatorWorkflow, FileEvaluatorRunRepository, InMemorySessionState, parse_source_reference

__all__ = [
    "RUN_INDEX_FILENAME",
    "RUNS_DIRNAME",
    "EvaluatorLookup",
    "EvaluatorRawPayloads",
    "EvaluatorRetryContext",
    "EvaluatorRetryContextResponse",
    "EvaluatorRetrySignals",
    "EvaluatorRouting",
    "EvaluatorRunMeta",
    "EvaluatorRunQuery",
    "EvaluatorRunRecord",
    "EvaluatorRunRepository",
    "EvaluatorRunSummary",
    "EvaluatorRunsListResponse",
    "EvaluatorWorkflow",
    "FileEvaluatorRunRepository",
    "InMemorySessionState",
    "RetryRequest",
    "RetryResponse",
    "build_retry_context",
    "classify_path",
    "get_evaluator_state_root",
    "get_run_index_path",
    "get_run_record_path",
    "get_run_records_dir",
    "get_xdg_state_home",
    "normalize_from_bundle",
    "normalize_from_task",
    "parse_source_reference",
]

if EvaluatorBamlClient is not None:
    __all__.extend(
        [
            "EvaluatorBamlClient",
            "EvaluatorBamlResult",
            "build_baml_env",
            "convert_to_baml_input",
        ]
    )
