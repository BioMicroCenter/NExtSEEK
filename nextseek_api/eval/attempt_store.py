"""Content-addressed judgment attempt store with hash-verified replay."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import orjson

__all__ = [
    "AttemptRecord",
    "AttemptStore",
    "AttemptStoreError",
    "HashWithoutBytesError",
]


class AttemptStoreError(ValueError):
    """Base error for attempt store violations."""


class HashWithoutBytesError(AttemptStoreError):
    """Raised when a hash reference lacks retrievable payload bytes."""


@dataclass(frozen=True)
class AttemptRecord:
    attempt_id: str
    arm_id: str
    call_index: int
    input_fingerprint: str
    model_id: str
    prompt_version: str
    evaluator_version: str
    provider_request_id: str | None
    started_at: str
    ended_at: str
    retry_of: str | None
    token_input: int | None
    token_output: int | None
    cost_usd: float | None
    status: str
    error_class: str | None
    request_sha256: str
    response_sha256: str
    request_path: str
    response_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "arm_id": self.arm_id,
            "call_index": self.call_index,
            "input_fingerprint": self.input_fingerprint,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "evaluator_version": self.evaluator_version,
            "provider_request_id": self.provider_request_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "retry_of": self.retry_of,
            "token_input": self.token_input,
            "token_output": self.token_output,
            "cost_usd": self.cost_usd,
            "status": self.status,
            "error_class": self.error_class,
            "request_sha256": self.request_sha256,
            "response_sha256": self.response_sha256,
            "request_path": self.request_path,
            "response_path": self.response_path,
        }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AttemptStore:
    """Filesystem-backed content-addressed attempt store."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.payloads = self.root / "payloads"
        self.index = self.root / "index"
        self.payloads.mkdir(parents=True, exist_ok=True)
        self.index.mkdir(parents=True, exist_ok=True)
        self._seen_ids: set[str] = set()
        for path in self.index.glob("*.json"):
            data = orjson.loads(path.read_bytes())
            self._seen_ids.add(data["attempt_id"])

    def _write_payload(self, data: bytes) -> tuple[str, Path]:
        digest = _sha256_bytes(data)
        rel = Path(digest[:2]) / digest
        path = self.payloads / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
        return digest, path.relative_to(self.root)

    def write_attempt(
        self,
        *,
        arm_id: str,
        call_index: int,
        input_fingerprint: str,
        model_id: str,
        prompt_version: str,
        evaluator_version: str,
        request_bytes: bytes,
        response_bytes: bytes,
        status: str,
        error_class: str | None = None,
        provider_request_id: str | None = None,
        retry_of: str | None = None,
        token_input: int | None = None,
        token_output: int | None = None,
        cost_usd: float | None = None,
        attempt_id: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
    ) -> AttemptRecord:
        if call_index not in (0, 1, 2):
            raise AttemptStoreError(f"call_index must be 0..2, got {call_index}")
        attempt_id = attempt_id or str(uuid.uuid4())
        if attempt_id in self._seen_ids:
            raise AttemptStoreError(f"duplicate attempt_id {attempt_id}")
        if not request_bytes or not response_bytes:
            raise AttemptStoreError("request and response bytes are required")
        req_sha, req_rel = self._write_payload(request_bytes)
        resp_sha, resp_rel = self._write_payload(response_bytes)
        record = AttemptRecord(
            attempt_id=attempt_id,
            arm_id=arm_id,
            call_index=call_index,
            input_fingerprint=input_fingerprint,
            model_id=model_id,
            prompt_version=prompt_version,
            evaluator_version=evaluator_version,
            provider_request_id=provider_request_id,
            started_at=started_at or _utc_now_iso(),
            ended_at=ended_at or _utc_now_iso(),
            retry_of=retry_of,
            token_input=token_input,
            token_output=token_output,
            cost_usd=cost_usd,
            status=status,
            error_class=error_class,
            request_sha256=req_sha,
            response_sha256=resp_sha,
            request_path=str(req_rel),
            response_path=str(resp_rel),
        )
        idx_path = self.index / f"{attempt_id}.json"
        idx_path.write_bytes(orjson.dumps(record.to_dict(), option=orjson.OPT_INDENT_2))
        self._seen_ids.add(attempt_id)
        return record

    def read_payload(self, sha256: str) -> bytes:
        rel = Path(sha256[:2]) / sha256
        path = self.payloads / rel
        if not path.exists():
            raise HashWithoutBytesError(f"no retrievable bytes for hash {sha256}")
        data = path.read_bytes()
        if _sha256_bytes(data) != sha256:
            raise AttemptStoreError(f"hash mismatch for {sha256}")
        return data

    def load_attempt(self, attempt_id: str) -> AttemptRecord:
        path = self.index / f"{attempt_id}.json"
        if not path.exists():
            raise AttemptStoreError(f"unknown attempt_id {attempt_id}")
        data = orjson.loads(path.read_bytes())
        return AttemptRecord(**data)

    def verify_attempt(self, attempt_id: str) -> AttemptRecord:
        record = self.load_attempt(attempt_id)
        req = self.read_payload(record.request_sha256)
        resp = self.read_payload(record.response_sha256)
        if _sha256_bytes(req) != record.request_sha256:
            raise AttemptStoreError("request hash verification failed")
        if _sha256_bytes(resp) != record.response_sha256:
            raise AttemptStoreError("response hash verification failed")
        return record

    def list_arm_attempts(self, arm_id: str) -> list[AttemptRecord]:
        out: list[AttemptRecord] = []
        for path in sorted(self.index.glob("*.json")):
            data = orjson.loads(path.read_bytes())
            if data["arm_id"] == arm_id:
                out.append(AttemptRecord(**data))
        return sorted(out, key=lambda r: (r.call_index, r.started_at))

    def export_manifest(self) -> list[dict[str, Any]]:
        manifest: list[dict[str, Any]] = []
        for path in sorted(self.index.glob("*.json")):
            manifest.append(orjson.loads(path.read_bytes()))
        return manifest
