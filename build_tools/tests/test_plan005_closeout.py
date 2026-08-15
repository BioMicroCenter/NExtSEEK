"""Focused tests for the Plan 005 16-row closeout protocol."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from build_tools.plan005_closeout import (
    PROTOCOL_RECORD_IDS,
    ProtocolError,
    load_schema,
    main,
    protocol_manifest,
    protocol_rows,
    validate_protocol_rows,
)


def test_protocol_ids_are_exactly_the_locked_16_in_order():
    rows = protocol_rows()
    assert tuple(row["id"] for row in rows) == PROTOCOL_RECORD_IDS
    assert PROTOCOL_RECORD_IDS[0] == "01-baseline"
    assert PROTOCOL_RECORD_IDS[-1] == "16-final-gate"
    validate_protocol_rows(rows)


def test_schema_pins_the_same_16_ids():
    schema = load_schema()
    consts = [item["const"] for item in schema["properties"]["record_ids"]["prefixItems"]]
    assert tuple(consts) == PROTOCOL_RECORD_IDS
    assert schema["properties"]["rows"]["minItems"] == 16
    assert schema["properties"]["rows"]["maxItems"] == 16


def test_extra_row_is_red():
    rows = protocol_rows()
    extra = deepcopy(rows[-1])
    extra["id"] = "17-bonus"
    with pytest.raises(ProtocolError, match="row count"):
        validate_protocol_rows(rows + [extra])


def test_missing_row_is_red():
    rows = protocol_rows()[:-1]
    with pytest.raises(ProtocolError, match="row count"):
        validate_protocol_rows(rows)


def test_renamed_row_is_red():
    rows = protocol_rows()
    rows[4]["id"] = "05-future-ops"
    with pytest.raises(ProtocolError, match="mismatch"):
        validate_protocol_rows(rows)


def test_duplicate_row_is_red():
    rows = protocol_rows()
    rows[3] = deepcopy(rows[2])
    with pytest.raises(ProtocolError, match="duplicate"):
        validate_protocol_rows(rows)


def test_out_of_order_is_red():
    rows = protocol_rows()
    rows[0], rows[1] = rows[1], rows[0]
    with pytest.raises(ProtocolError, match="out of order"):
        validate_protocol_rows(rows)


def test_network_enablement_is_red():
    rows = protocol_rows()
    rows[1]["network"] = "bridge"
    with pytest.raises(ProtocolError, match="network"):
        validate_protocol_rows(rows)


def test_cli_protocol_json(capsys):
    assert main(["protocol", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["record_ids"] == list(PROTOCOL_RECORD_IDS)


def test_cli_rejects_live_stages():
    assert main(["preflight"]) == 2


def test_pytest_lanes_mount_evidence_and_django_env():
    rows = {row["id"]: row["argv_template"] for row in protocol_rows()}
    for record_id, junit in (
        ("05-future-op", "--junitxml=/evidence/future-op.junit.xml"),
        ("06-audit-a", "--junitxml=/evidence/audit-a.junit.xml"),
        ("07-assistant-route", "--junitxml=/evidence/assistant-route.junit.xml"),
        ("08-build-tools", "--junitxml=/evidence/build-tools.junit.xml"),
    ):
        argv = rows[record_id]
        assert "{writable}:/evidence" in argv
        assert junit in argv
        assert "PYTHONPATH=/repo:/repo/dmac_assistant/src:/repo/chat_nextseek/src" in argv
    for record_id in ("05-future-op", "06-audit-a", "07-assistant-route"):
        assert "DJANGO_SETTINGS_MODULE=dmac.test_settings" in rows[record_id]
    assert "DJANGO_SETTINGS_MODULE=dmac.test_settings" not in rows["08-build-tools"]
