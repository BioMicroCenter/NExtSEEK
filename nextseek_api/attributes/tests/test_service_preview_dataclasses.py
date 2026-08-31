"""_preview must accept what the planner actually produces.

Two classes share the name `AutomaticChange`:

    planner.py:166   @dataclass AutomaticChange              <- what plan_mutation builds
    schemas.py:370   class AutomaticChange(ContractModel)    <- what service.py imports

`ContractModel` sets `strict=True` (schemas.py:68), so pydantic will not duck-type an
arbitrary object: it requires a dict or a genuine instance of itself. Handing it the
planner's dataclass raises, and the caller sees a 500:

    ValidationError: Input should be a valid dictionary or instance of AutomaticChange
    [type=model_type, input_value=AutomaticChange(kind='pos...'), input_type=AutomaticChange]

The same applies to `item.errors`, which is `tuple[PlanError, ...]` -- so a mutation that
produces plan errors also 500s instead of returning them, which is the more damaging half.

Found on production only after the collation fix (fcc27af7) let planning complete for the
first time. It is not environment-specific: any plan with a non-empty automatic_changes or
errors fails identically anywhere.
"""
from __future__ import annotations

from types import SimpleNamespace

from nextseek_api.attributes import service
from nextseek_api.attributes.planner import (
    AutomaticChange as PlannerAutomaticChange,
    PlanError,
)


def _plan(*, automatic_changes=(), errors=(), status="planned"):
    """A minimal plan shaped like plan_mutation's output for one sample type."""
    item = SimpleNamespace(
        sample_type_id=1,
        sample_type_title="BLD",
        status=status,
        counts={},
        preview_records=[],
        hypothetical_preview_records=[],
        automatic_changes=automatic_changes,
        errors=errors,
    )
    return SimpleNamespace(types=[item], predicted_mode="synchronous", active_threshold=1000)


def test_preview_accepts_the_planner_automatic_change_dataclass():
    """A position renumber (pos 8 -> 7) is what creating an attribute actually emits."""
    plan = _plan(automatic_changes=(
        PlannerAutomaticChange("position_changed", 5, "Existing Attribute", "pos", 8, 7),
    ))

    response = service._preview(plan)

    changes = response.outcomes[0].automatic_changes
    assert len(changes) == 1
    assert changes[0].kind == "position_changed"
    assert changes[0].attribute_title == "Existing Attribute"
    assert changes[0].previous_value == 8
    assert changes[0].new_value == 7


def test_preview_accepts_the_planner_plan_error_dataclass():
    """Without this, a legitimate rejection is reported as a 500 rather than its own error."""
    plan = _plan(
        errors=(PlanError("attribute_not_found", "no such attribute", 0, 1),),
        status="failed",
    )

    response = service._preview(plan)

    errors = response.outcomes[0].errors
    assert len(errors) == 1
    assert errors[0].code == "attribute_not_found"
    assert errors[0].message == "no such attribute"
    assert errors[0].target_index == 0
    assert errors[0].attribute_index == 1
