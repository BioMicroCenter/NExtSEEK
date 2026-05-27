from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .models import EvaluatorRunRecord, EvaluatorRunSummary


RUN_INDEX_FILENAME = "runs.json"
RUNS_DIRNAME = "runs"


class EvaluatorRunQuery(BaseModel):
    """Filter options for the local evaluator runs listing."""

    status: str | None = None
    mode: str | None = None
    has_bundle: bool | None = None
    limit: int = Field(default=50, ge=1, le=500)

    model_config = ConfigDict(extra="forbid")


class EvaluatorRunRepository(Protocol):
    """Filesystem-backed contract used by later evaluator workflow tasks."""

    def write_run(self, record: EvaluatorRunRecord) -> Path: ...

    def read_run(self, task_id: UUID) -> EvaluatorRunRecord: ...

    def list_runs(self, query: EvaluatorRunQuery | None = None) -> list[EvaluatorRunSummary]: ...


def get_xdg_state_home() -> Path:
    """Resolve the effective XDG state home for evaluator persistence."""

    raw = os.environ.get("XDG_STATE_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local" / "state"


def get_evaluator_state_root(*, state_root: str | Path | None = None) -> Path:
    """Return the dedicated state root for local evaluator workflow records."""

    if state_root is not None:
        return Path(state_root).expanduser()
    return get_xdg_state_home() / "chat_nextseek" / "evaluator"


def get_run_records_dir(*, state_root: str | Path | None = None) -> Path:
    """Return the directory that stores per-run JSON records."""

    return get_evaluator_state_root(state_root=state_root) / RUNS_DIRNAME


def get_run_index_path(*, state_root: str | Path | None = None) -> Path:
    """Return the canonical index path for local evaluator runs."""

    return get_evaluator_state_root(state_root=state_root) / RUN_INDEX_FILENAME


def get_run_record_path(task_id: UUID | str, *, state_root: str | Path | None = None) -> Path:
    """Return the canonical per-run JSON file path for the given task id."""

    return get_run_records_dir(state_root=state_root) / f"{task_id}.json"
