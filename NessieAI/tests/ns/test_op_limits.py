"""Per-op limits (approach 1, piece 2): the table, and the turn's deadline capping it."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from NessieAI.ns import granular
from NessieAI.ns.op_limits import ANSWER_RESERVE_S, MIN_USABLE_S, OP_LIMITS_S, SIDECAR_ROAD_CAP_S, op_limit_s


def test_every_op_has_a_limit():
    assert set(OP_LIMITS_S) == set(granular._HANDLERS)


@pytest.mark.parametrize("op, seconds", [
    ("graph", 90.0), ("aggregate", 90.0), ("parse", 90.0), ("entity", 55.0), ("api-read", 55.0),
    ("generate-submission", 150.0), ("report", 150.0),
    ("graph-schema", 60.0), ("run-ls", 60.0), ("build-upload-xlsx", 60.0),
])
def test_the_ruled_limits(op, seconds):
    assert OP_LIMITS_S[op] == seconds


def test_with_no_deadline_the_limit_is_the_table():
    assert op_limit_s("report", None, 1_000.0) == 150.0


@pytest.mark.parametrize("elapsed, expected", [(0.0, 90.0), (45.0, 90.0), (100.0, 35.0), (130.0, 5.0), (170.0, -35.0)])
def test_the_turn_deadline_caps_the_limit(elapsed, expected):
    start = 1_800_000_000.0
    assert op_limit_s("graph", start + 180.0, start + elapsed) == pytest.approx(expected)


def test_the_reserves_are_the_ruled_ones():
    assert (ANSWER_RESERVE_S, MIN_USABLE_S) == (45.0, 20.0)


def test_the_sidecar_road_keeps_its_ops_inside_the_sidecars_wait():
    client = Path(granular.__file__).resolve().parents[1] / "docker" / "ns-sidecar" / "app" / "ns_client.py"
    sidecar_s = float(re.search(r"^_TIMEOUT = ([0-9.]+)", client.read_text(), re.M).group(1))
    assert SIDECAR_ROAD_CAP_S == 55.0 < sidecar_s
