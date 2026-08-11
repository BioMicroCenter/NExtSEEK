"""Tests for content-addressed attempt store."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from nextseek_api.eval.attempt_store import (  # noqa: E402
    AttemptStore,
    AttemptStoreError,
    HashWithoutBytesError,
)


def test_round_trip_write_verify(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path)
    rec = store.write_attempt(
        arm_id="arm-1",
        call_index=0,
        input_fingerprint="fp1",
        model_id="mock",
        prompt_version="v1",
        evaluator_version="v1",
        request_bytes=b'{"q":1}',
        response_bytes=b'{"a":1}',
        status="succeeded",
    )
    verified = store.verify_attempt(rec.attempt_id)
    assert verified.request_sha256 == rec.request_sha256


def test_hash_without_bytes_rejected(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path)
    with pytest.raises(HashWithoutBytesError):
        store.read_payload("0" * 64)


def test_duplicate_attempt_id_rejected(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path)
    kwargs = dict(
        arm_id="arm-1",
        call_index=0,
        input_fingerprint="fp1",
        model_id="mock",
        prompt_version="v1",
        evaluator_version="v1",
        request_bytes=b"req",
        response_bytes=b"resp",
        status="succeeded",
        attempt_id="fixed-id",
    )
    store.write_attempt(**kwargs)
    with pytest.raises(AttemptStoreError):
        store.write_attempt(**kwargs)


def test_invalid_call_index_rejected(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path)
    with pytest.raises(AttemptStoreError):
        store.write_attempt(
            arm_id="arm-1",
            call_index=3,
            input_fingerprint="fp1",
            model_id="mock",
            prompt_version="v1",
            evaluator_version="v1",
            request_bytes=b"req",
            response_bytes=b"resp",
            status="succeeded",
        )
