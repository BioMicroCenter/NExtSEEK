"""Coverage for lazy op_registry exports and model extra=forbid."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from nextseek_api.cc_assistant import op_registry
from nextseek_api.cc_assistant.op_registry.models import ArgSpec, OpSpec, Transport


def test_unknown_attr_is_attribute_error():
    with pytest.raises(AttributeError, match="no attribute"):
        getattr(op_registry, "not_a_real_export")


def test_lazy_ops_and_route_constants_load():
    assert op_registry.OPS
    assert "bash" in op_registry.GENERIC_CC_BUILTINS
    assert op_registry.CONTAINER_CC_ROUTE.route_name == "container_cc"
    assert len(op_registry.NEXTSEEK_QUERY_TOOLS) == 8


def test_opspec_rejects_extra_fields_and_missing_required():
    with pytest.raises(ValidationError):
        OpSpec(op_id="x")  # type: ignore[call-arg]
    row = op_registry.OPS[0]
    with pytest.raises(ValidationError):
        OpSpec(**{**row.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError):
        ArgSpec(flag="--mode", extra="nope")  # type: ignore[call-arg]
    assert Transport.viewset.value == "viewset"
