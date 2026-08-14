"""Independent server safety audit for Plan 005 Task 5."""
from __future__ import annotations

import pytest

from nextseek_api.assistant.write_gate import WriteBlockedError, build_gate, load_allowlist
from nextseek_api.cc_assistant.op_registry import OPS
from nextseek_api.cc_assistant.op_registry.derive import (
    READ_SAFE_JSON,
    audit_read_safe_json_schema,
    derive_handler_sidecar_ops,
    derive_read_safe_pairs_from_json,
    derive_read_safe_pairs_from_ops,
    derive_sidecar_ops_from_ops,
    derive_transport_minus_write_gate,
    derive_write_gate_policy_union,
    derive_write_gate_read_class_ops,
    derive_write_gate_sidecar_ops,
    derive_ws_contract_sidecar_ops,
    find_op,
)
from nextseek_api.cc_assistant.op_registry.models import GateClass, Transport

# Transport-truth sidecar ops present in handlers/ws_contract but absent from
# write_gate.SIDECAR_OPS (documented audit debt; do not enlarge write_gate here).
KNOWN_TRANSPORT_ONLY_OPS = frozenset({"run-ls", "build-upload-xlsx"})


def test_sidecar_transport_sources_agree_by_set_equality():
    ops_sidecar = derive_sidecar_ops_from_ops(OPS)
    ws_ops = derive_ws_contract_sidecar_ops()
    handler_ops = derive_handler_sidecar_ops()
    assert ops_sidecar == ws_ops
    assert ops_sidecar == handler_ops


def test_write_gate_declared_set_equals_read_class_plus_api_ops():
    declared = derive_write_gate_sidecar_ops()
    read_class = derive_write_gate_read_class_ops()
    policy_union = derive_write_gate_policy_union()
    assert declared == read_class | {"api-read", "api-write"}
    assert declared == policy_union


def test_write_gate_sidcar_ops_is_not_transport_truth():
    transport_only = derive_transport_minus_write_gate(OPS)
    assert transport_only == KNOWN_TRANSPORT_ONLY_OPS
    assert derive_write_gate_sidecar_ops() != derive_sidecar_ops_from_ops(OPS)


def test_transport_only_ops_are_read_class_in_opspec_but_unknown_to_gate():
    gate = build_gate(load_allowlist())
    for runner_key in sorted(KNOWN_TRANSPORT_ONLY_OPS):
        op = next(o for o in OPS if o.runner_key == runner_key)
        assert op.transport is Transport.sidecar
        assert op.gate_class is GateClass.read
        with pytest.raises(WriteBlockedError, match="unknown op"):
            gate(runner_key, None, None, False)


def test_api_read_opspec_safety_data_matches_enforced_allowlist():
    audit_read_safe_json_schema(READ_SAFE_JSON)
    json_pairs = derive_read_safe_pairs_from_json()
    ops_pairs = derive_read_safe_pairs_from_ops(OPS)
    assert ops_pairs == json_pairs

    api_read = find_op(OPS, "api-read")
    assert api_read.gate_class is GateClass.read
    for endpoint in api_read.read_safe_endpoints:
        assert endpoint.source.strip()
        assert endpoint.rationale.strip()

    gate = build_gate(load_allowlist())
    for endpoint, method in sorted(json_pairs):
        gate("api-read", endpoint, method, False)


def test_api_write_opspec_safety_data_matches_enforced_confirmation_gate():
    api_write = find_op(OPS, "api-write")
    assert api_write.gate_class is GateClass.write_confirm
    assert api_write.allowlist.auto_runnable is False
    assert any(arg.flag == "--confirmed-write" and arg.required for arg in api_write.argv)

    gate = build_gate(load_allowlist())
    gate("api-write", None, None, True)


@pytest.mark.parametrize(
    "confirmed_write",
    [
        False,
        1,
        "true",
        [True],
        {"confirmed": True},
    ],
)
def test_api_write_rejects_non_singleton_boolean_true(confirmed_write):
    gate = build_gate(load_allowlist())
    with pytest.raises(WriteBlockedError, match="confirmed_write"):
        gate("api-write", None, None, confirmed_write)


def test_api_write_rejects_custom_truthy_object():
    class _Truthy:
        def __bool__(self) -> bool:
            return True

    gate = build_gate(load_allowlist())
    with pytest.raises(WriteBlockedError, match="confirmed_write"):
        gate("api-write", None, None, _Truthy())


def test_api_read_blocks_non_allowlisted_endpoint_method_pair():
    gate = build_gate(load_allowlist())
    with pytest.raises(WriteBlockedError, match="not in read_safe_endpoints"):
        gate("api-read", "/nextseek_api/samples/", "POST", False)


def test_write_gate_blocks_unknown_op_label():
    gate = build_gate(load_allowlist())
    with pytest.raises(WriteBlockedError, match="unknown op"):
        gate("totally-unknown-op", None, None, True)


def test_write_gate_read_class_ops_pass_without_confirmation():
    gate = build_gate(load_allowlist())
    for runner_key in sorted(derive_write_gate_read_class_ops()):
        gate(runner_key, None, None, False)
