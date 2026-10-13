"""error_from_response: a coded body wins over the bare-status mapping, so an outage reads as try again."""
from __future__ import annotations

import pathlib
import sys

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path("build_context/plugins/nextseek/bin")))
from _op_road import error_from_response  # noqa: E402


def _resp(status, body=None):
    return httpx.Response(status, json=body) if body is not None else httpx.Response(status, content=b"")


@pytest.mark.parametrize("code,exit_code", [("TRANSPORT_ERROR", 7), ("BUSY", 10)])
def test_a_403_carrying_a_retryable_code_keeps_that_code(code, exit_code):
    err = error_from_response(_resp(403, {"code": code, "message": "try again shortly"}))
    assert err.code == code and err.exit_code == exit_code


def test_a_503_with_the_transport_code():
    assert error_from_response(_resp(503, {"code": "TRANSPORT_ERROR", "message": "m"})).code == "TRANSPORT_ERROR"


def test_a_bare_403_is_still_a_refusal():
    assert error_from_response(_resp(403, {"detail": "nope"})).code == "PASS_NOT_ALLOWED"
    assert error_from_response(_resp(403)).code == "PASS_NOT_ALLOWED"


def test_a_bare_503_is_transport():
    assert error_from_response(_resp(503)).code == "TRANSPORT_ERROR"
