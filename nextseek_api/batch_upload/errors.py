"""Error handling system for batch upload pipeline."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


# ── enums ──────────────────────────────────────────────────────────────────


class ErrorType(Enum):
    VALIDATION_JSON = "VALIDATION_JSON"
    VALIDATION_UID_MISMATCH = "validation_uid_mismatch"
    VALIDATION_METADATA_SHAPE = "validation_metadata_shape"
    AMBIGUOUS_IDENTITY = "ambiguous_identity"
    VALIDATION_ASSAY = "VALIDATION_ASSAY"
    VALIDATION_SAMPLE_TYPE = "VALIDATION_SAMPLE_TYPE"
    DB_CONSTRAINT = "DB_CONSTRAINT"
    DB_CONN = "DB_CONN"
    DUPLICATE = "DUPLICATE"
    PARENT_FAILED = "PARENT_FAILED"
    CYCLE_UNRESOLVABLE = "CYCLE_UNRESOLVABLE"
    UNKNOWN = "UNKNOWN"


class Severity(Enum):
    CRITICAL = "CRITICAL"
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


# ── severity mapping ──────────────────────────────────────────────────────


_SEVERITY_MAP: Dict[ErrorType, Severity] = {
    ErrorType.DB_CONN: Severity.CRITICAL,
    ErrorType.DB_CONSTRAINT: Severity.ERROR,
    ErrorType.UNKNOWN: Severity.ERROR,
    ErrorType.VALIDATION_JSON: Severity.WARNING,
    ErrorType.VALIDATION_UID_MISMATCH: Severity.ERROR,
    ErrorType.VALIDATION_METADATA_SHAPE: Severity.ERROR,
    ErrorType.AMBIGUOUS_IDENTITY: Severity.ERROR,
    ErrorType.VALIDATION_SAMPLE_TYPE: Severity.WARNING,
    ErrorType.VALIDATION_ASSAY: Severity.INFO,
    ErrorType.DUPLICATE: Severity.INFO,
    ErrorType.PARENT_FAILED: Severity.ERROR,
    ErrorType.CYCLE_UNRESOLVABLE: Severity.ERROR,
}


def classify_severity(error_type: ErrorType) -> Severity:
    """Return the severity for a given error type."""
    return _SEVERITY_MAP.get(error_type, Severity.ERROR)


# ── data classes ──────────────────────────────────────────────────────────


@dataclass
class RowError:
    row_index: int
    uid: Optional[str]
    error_type: ErrorType
    message: str
    severity: Severity


# ── exceptions ────────────────────────────────────────────────────────────


class JsonNormalizationError(Exception):
    """Raised when JSON metadata cannot be parsed or normalized."""


# ── collector ─────────────────────────────────────────────────────────────


class ErrorCollector:
    """Accumulates errors across the upload pipeline."""

    def __init__(self) -> None:
        self._errors: List[RowError] = []
        self._by_uid: Dict[str, List[RowError]] = {}
        self._by_type: Dict[ErrorType, int] = {}

    def add(
        self,
        row_index: int,
        uid: Optional[str],
        error_type: ErrorType,
        message: str,
        severity: Optional[Severity] = None,
    ) -> None:
        sev = severity or classify_severity(error_type)
        err = RowError(
            row_index=row_index,
            uid=uid,
            error_type=error_type,
            message=message,
            severity=sev,
        )
        self._errors.append(err)
        if uid:
            self._by_uid.setdefault(uid, []).append(err)
        self._by_type[error_type] = self._by_type.get(error_type, 0) + 1

    def errors_for_uid(self, uid: str) -> List[RowError]:
        return self._by_uid.get(uid, [])

    def all_errors(self) -> List[RowError]:
        return list(self._errors)

    def count_by_type(self) -> Dict[ErrorType, int]:
        return dict(self._by_type)

    def has_critical(self) -> bool:
        return self._by_type.get(ErrorType.DB_CONN, 0) > 0


class _ThreadSafeErrorCollector:
    """Thread-safe proxy wrapping an ErrorCollector with a lock."""

    def __init__(self, collector: ErrorCollector, lock: Optional[threading.Lock] = None) -> None:
        self._collector = collector
        self._lock = lock or threading.Lock()

    def add(
        self,
        row_index: int,
        uid: Optional[str],
        error_type: ErrorType,
        message: str,
        severity: Optional[Severity] = None,
    ) -> None:
        with self._lock:
            self._collector.add(row_index, uid, error_type, message, severity)

    def errors_for_uid(self, uid: str) -> List[RowError]:
        with self._lock:
            return self._collector.errors_for_uid(uid)

    def all_errors(self) -> List[RowError]:
        with self._lock:
            return self._collector.all_errors()

    def count_by_type(self) -> Dict[ErrorType, int]:
        with self._lock:
            return self._collector.count_by_type()

    def has_critical(self) -> bool:
        with self._lock:
            return self._collector.has_critical()


def _classify_validation_error(message: str) -> ErrorType:
    """Classify a Pydantic validation error message into an ErrorType."""
    lower = message.lower()
    if "json_metadata.uid is" in lower:
        return ErrorType.VALIDATION_UID_MISMATCH
    if (
        "json_metadata must be a json object" in lower
        or "json_metadata is not valid json" in lower
    ):
        return ErrorType.VALIDATION_METADATA_SHAPE
    if "ambiguous identity match:" in lower:
        return ErrorType.AMBIGUOUS_IDENTITY
    if "json" in lower:
        return ErrorType.VALIDATION_JSON
    if "assay" in lower:
        return ErrorType.VALIDATION_ASSAY
    if "sampletype" in lower or "sample type" in lower or "sample_type" in lower:
        return ErrorType.VALIDATION_SAMPLE_TYPE
    return ErrorType.UNKNOWN
