"""Evaluator workflow: judgment and retry decision per query.

``EvaluatorWorkflow`` wraps the BAML client and consumes normalized
bundle/task payloads from the evaluator adapters.
Use it for programmatic evaluation, listing, or retry execution outside the CLI.
Invariant: ``_evaluate_normalized()`` records failures on ``record.error``,
and retries run only when both ``should_retry`` and ``retry_query`` are set.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

from .client import EvaluatorBamlClient
from .contracts import (
    EvaluatorRunQuery,
    EvaluatorRunRepository,
    get_run_index_path,
    get_run_record_path,
    get_run_records_dir,
)
from .models import (
    EvaluatorRunRecord,
    EvaluatorRunSummary,
    EvaluatorRunsListResponse,
    RetryRequest,
    RetryResponse,
)
from .normalization import normalize_from_bundle, normalize_from_task


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_uuid(value: str | UUID) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _model_dump(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_model_dump(item) for item in value]
    return value


def parse_source_reference(source: str) -> tuple[str, dict[str, Any]]:
    kind, _, payload = source.partition(":")
    if kind == "bundle":
        session_id, sep, bundle_id = payload.partition(":")
        if not session_id or not sep or not bundle_id:
            raise ValueError("Bundle sources must look like bundle:<session_id>:<bundle_id>.")
        return ("bundle", {"session_id": _coerce_uuid(session_id), "bundle_id": int(bundle_id)})
    if kind == "task":
        if not payload:
            raise ValueError("Task sources must look like task:<task_id>.")
        return ("task", {"task_id": _coerce_uuid(payload)})
    raise ValueError(f"Unsupported evaluator source {source!r}.")


class InMemorySessionState(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FileEvaluatorRunRepository(EvaluatorRunRepository):
    def __init__(self, *, state_root: str | Path | None = None) -> None:
        self._state_root = Path(state_root).expanduser() if state_root is not None else None
        self._lock = RLock()

    def _records_dir(self) -> Path:
        path = get_run_records_dir(state_root=self._state_root)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _index_path(self) -> Path:
        path = get_run_index_path(state_root=self._state_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _record_path(self, task_id: UUID) -> Path:
        path = get_run_record_path(task_id, state_root=self._state_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _read_all_records(self) -> list[EvaluatorRunRecord]:
        with self._lock:
            records: list[EvaluatorRunRecord] = []
            for path in sorted(self._records_dir().glob("*.json")):
                records.append(EvaluatorRunRecord.model_validate_json(path.read_text(encoding="utf-8")))
            records.sort(key=lambda item: item.created_at, reverse=True)
            return records

    def _write_index(self) -> None:
        with self._lock:
            summaries = [self._to_summary(record) for record in self._read_all_records()]
            payload = EvaluatorRunsListResponse(count=len(summaries), results=summaries)
            self._index_path().write_text(payload.model_dump_json(indent=2), encoding="utf-8")

    def _to_summary(self, record: EvaluatorRunRecord) -> EvaluatorRunSummary:
        return EvaluatorRunSummary(
            task_id=record.task_id,
            session_id=record.session_id,
            status=record.status,
            query=record.query,
            has_bundle=record.source_bundle_id is not None,
            user_id=record.user_id,
            created_at=record.created_at,
        )

    def write_run(self, record: EvaluatorRunRecord) -> Path:
        with self._lock:
            path = self._record_path(record.task_id)
            path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
            self._write_index()
            return path

    def read_run(self, task_id: UUID) -> EvaluatorRunRecord:
        with self._lock:
            path = self._record_path(task_id)
            if not path.exists():
                raise FileNotFoundError(f"No evaluator run record found for task {task_id}.")
            return EvaluatorRunRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def list_runs(self, query: EvaluatorRunQuery | Mapping[str, Any] | None = None) -> list[EvaluatorRunSummary]:
        with self._lock:
            parsed = query if isinstance(query, EvaluatorRunQuery) else EvaluatorRunQuery.model_validate(query or {})
            records = self._read_all_records()
            summaries = [self._to_summary(record) for record in records]
            if parsed.status:
                summaries = [item for item in summaries if item.status == parsed.status]
            if parsed.mode:
                task_ids = {record.task_id for record in records if record.mode == parsed.mode}
                summaries = [item for item in summaries if item.task_id in task_ids]
            if parsed.has_bundle is not None:
                summaries = [item for item in summaries if item.has_bundle is parsed.has_bundle]
            return summaries[: parsed.limit]


def _unsupported_judgment(path_mode: str) -> dict[str, Any]:
    return {
        "correctness": "FAIL",
        "completeness": "FAIL",
        "routing_quality": "FAIL",
        "reasoning": f"Unsupported evaluator path {path_mode}; retry is disabled for this run.",
    }


def _unsupported_decision(path_mode: str) -> dict[str, Any]:
    return {
        "verdict": "FAIL",
        "should_retry": False,
        "retry_query": None,
        "suggestions": None,
        "reasoning": f"Unsupported evaluator path {path_mode}; explicit retry is unavailable.",
    }


class EvaluatorWorkflow:
    def __init__(
        self,
        *,
        repository: EvaluatorRunRepository | None = None,
        baml_client: EvaluatorBamlClient | Any | None = None,
        config: Any | None = None,
        state_root: str | Path | None = None,
    ) -> None:
        self._repository = repository or FileEvaluatorRunRepository(state_root=state_root)
        self._baml = baml_client
        self._config = config

    @property
    def repository(self) -> EvaluatorRunRepository:
        return self._repository

    def get_retry_context_for_bundle(
        self,
        *,
        session_id: UUID | str,
        bundle_id: int,
        session: Mapping[str, Any],
    ):
        resolved_session_id = _coerce_uuid(session_id)
        bundle = next(
            (
                item
                for item in (session.get("results_history") or [])
                if item.get("id") == bundle_id
            ),
            None,
        )
        if bundle is None:
            raise ValueError(f"Bundle id {bundle_id} was not found in session {resolved_session_id}.")
        return normalize_from_bundle(dict(session), dict(bundle))

    def get_retry_context_for_task(
        self,
        *,
        task_id: UUID | str,
        session: Mapping[str, Any] | None = None,
        bundle: Mapping[str, Any] | None = None,
    ):
        record = self._repository.read_run(_coerce_uuid(task_id))
        return normalize_from_task(
            record,
            session=dict(session or {}),
            bundle=dict(bundle or {}),
        )

    def list_runs(
        self,
        store: EvaluatorRunRepository | None = None,
        *,
        status: str | None = None,
        limit: int = 50,
        has_bundle: bool | None = None,
        mode: str | None = None,
    ) -> list[dict[str, Any]]:
        repository = store or self._repository
        query = EvaluatorRunQuery(status=status, mode=mode, has_bundle=has_bundle, limit=limit)
        return [item.model_dump(mode="json") for item in repository.list_runs(query)]

    def _get_baml_client(self) -> Any:
        if self._baml is None:
            self._baml = EvaluatorBamlClient()
        return self._baml

    def _evaluate_normalized(self, normalized, *, source_label: str) -> tuple[EvaluatorRunRecord, dict[str, Any]]:
        record = EvaluatorRunRecord(
            session_id=normalized.lookup.session_id or uuid4(),
            source_task_id=normalized.lookup.task_id,
            source_session_id=normalized.lookup.session_id,
            source_bundle_id=normalized.lookup.bundle_id,
            mode=normalized.routing.execution_mode,
            status="running",
            query=normalized.run.query or "",
            reply=normalized.run.reply,
            user_id=normalized.run.user_id or 0,
            lookup=normalized.lookup,
            normalized_payload=normalized.model_dump(mode="json"),
        )
        self._repository.write_run(record)

        try:
            if normalized.routing.path_mode == "unsupported":
                judgment = _unsupported_judgment(normalized.routing.path_mode)
                decision = _unsupported_decision(normalized.routing.path_mode)
            else:
                judgment, decision = self._get_baml_client().evaluate(normalized, config=self._config)
            record.status = "completed"
            record.updated_at = _utcnow()
            record.evaluation_payload = {
                "normalized": normalized.model_dump(mode="json"),
                "judgment": _model_dump(judgment),
                "retry_decision": _model_dump(decision),
            }
            record.error = None
        except Exception as exc:
            judgment = _unsupported_judgment(normalized.routing.path_mode)
            decision = _unsupported_decision(normalized.routing.path_mode)
            record.status = "failed"
            record.updated_at = _utcnow()
            record.error = str(exc)
            record.evaluation_payload = {
                "normalized": normalized.model_dump(mode="json"),
                "judgment": _model_dump(judgment),
                "retry_decision": _model_dump(decision),
            }

        self._repository.write_run(record)
        payload = {
            "source": source_label,
            "run_record": record.model_dump(mode="json"),
            "evaluation": {
                **normalized.model_dump(mode="json"),
                "judgment": _model_dump(judgment),
                "retry_decision": _model_dump(decision),
            },
            "retry_executed": False,
            "retry_result": None,
        }
        return record, payload

    def evaluate_context(self, normalized, *, source_label: str = "context") -> dict[str, Any]:
        _, payload = self._evaluate_normalized(normalized, source_label=source_label)
        return payload

    def evaluate_bundle(self, session: Mapping[str, Any], bundle: Mapping[str, Any]) -> dict[str, Any]:
        normalized = normalize_from_bundle(dict(session), dict(bundle))
        _, payload = self._evaluate_normalized(normalized, source_label="bundle")
        return payload

    def evaluate_task(
        self,
        task: EvaluatorRunRecord | Mapping[str, Any],
        *,
        session: Mapping[str, Any] | None = None,
        bundle: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_from_task(task, session=dict(session or {}), bundle=dict(bundle or {}))
        _, payload = self._evaluate_normalized(normalized, source_label="task")
        return payload

    def _execute_retry(
        self,
        *,
        normalized,
        decision_payload: dict[str, Any],
        credentials: dict[str, str] | None = None,
        config: Any | None = None,
    ) -> dict[str, Any] | None:
        if not decision_payload.get("should_retry") or not decision_payload.get("retry_query"):
            return None
        active_config = config or self._config
        if active_config is None:
            raise ValueError("Retry execution requires a ChatConfig instance.")

        from chat_nextseek.orchestrator import run_query, run_query_plan

        retry_query = decision_payload["retry_query"]
        retry_session_id = uuid4()
        retry_task_id = uuid4()
        source_session_id = normalized.lookup.session_id or retry_session_id
        temp_session = InMemorySessionState(
            {
                "session_id": str(retry_session_id),
                "results_history": [],
                "last_files": [],
            }
        )

        runner = run_query_plan if normalized.routing.execution_mode == "plan" else run_query
        result = runner(temp_session, active_config, retry_query, credentials=credentials)
        credential_source = "service_account"
        if (credentials or {}).get("api_user") or getattr(active_config, "API_USER", None):
            credential_source = "basic_auth"

        retry_response = RetryResponse(
            task_id=retry_task_id,
            session_id=retry_session_id,
            source_task_id=normalized.lookup.task_id,
            source_session_id=source_session_id,
            source_bundle_id=normalized.lookup.bundle_id,
            credential_source=credential_source,
        )
        retry_evaluation = None
        retry_bundle_id = result.get("bundle_id")
        if retry_bundle_id is not None:
            retry_normalized = self.get_retry_context_for_bundle(
                session_id=retry_session_id,
                bundle_id=retry_bundle_id,
                session=temp_session,
            )
            retry_evaluation = self.evaluate_context(retry_normalized, source_label="bundle")
        return {
            "retry_response": retry_response.model_dump(mode="json"),
            "result": {
                "reply": result.get("reply"),
                "bundle_id": result.get("bundle_id"),
                "files": result.get("files") or [],
            },
            "evaluation": retry_evaluation["evaluation"] if retry_evaluation else None,
            "run_record": retry_evaluation["run_record"] if retry_evaluation else None,
        }

    def execute_retry_for_context(
        self,
        *,
        normalized,
        decision_payload: dict[str, Any],
        credentials: dict[str, str] | None = None,
        config: Any | None = None,
    ) -> dict[str, Any] | None:
        return self._execute_retry(
            normalized=normalized,
            decision_payload=decision_payload,
            credentials=credentials,
            config=config,
        )

    def evaluate_source(
        self,
        source: str,
        *,
        session: Mapping[str, Any] | None = None,
        session_lookup: Any | None = None,
        execute_retry: bool = False,
        config: Any | None = None,
        credentials: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        kind, payload = parse_source_reference(source)
        if kind == "bundle":
            session_id = payload["session_id"]
            bundle_id = payload["bundle_id"]
            resolved_session = session
            if resolved_session is None:
                if session_lookup is None:
                    raise ValueError("Bundle sources require a session payload or a session_lookup callback.")
                resolved_session = session_lookup(session_id)
            normalized = self.get_retry_context_for_bundle(
                session_id=session_id,
                bundle_id=bundle_id,
                session=resolved_session,
            )
        else:
            normalized = self.get_retry_context_for_task(task_id=payload["task_id"], session=session)

        record, result = self._evaluate_normalized(normalized, source_label=kind)
        if execute_retry:
            decision = result["evaluation"]["retry_decision"]
            retry_request = RetryRequest(
                task_id=normalized.lookup.task_id,
                session_id=normalized.lookup.session_id,
                bundle_id=normalized.lookup.bundle_id,
                query=decision["retry_query"] or normalized.run.query or "",
                mode=normalized.routing.execution_mode,
            )
            retry_result = self._execute_retry(
                normalized=normalized,
                decision_payload=decision,
                credentials=credentials,
                config=config or self._config,
            )
            result["retry_executed"] = retry_result is not None
            result["retry_result"] = retry_result
            record.retry_request = retry_request.model_dump(mode="json")
            record.retry_result = retry_result
            record.updated_at = _utcnow()
            self._repository.write_run(record)
            result["run_record"] = record.model_dump(mode="json")
        return result
