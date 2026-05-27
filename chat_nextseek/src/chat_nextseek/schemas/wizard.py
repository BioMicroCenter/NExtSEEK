"""Schema for the nf-core wizard's per-turn LLM dispatcher.

The wizard runs each user turn through `wizard_agent`, which decides whether
the user has supplied the current step's answer, is still exploring, or wants
to cancel/restart. The `extracted` dict's shape varies by step — the wizard
module knows what keys to look up.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WizardAgentOutput(BaseModel):
    """One LLM call's decision for an nf-core wizard turn.

    Steps and their expected fields:
      - "pipeline":   extracted = {"pipeline": "rnaseq"}
      - "builder":    selection_updates = {"uids": [...], "cohort_criteria": [...],
                                           "enrichment_fields": [...]}  (partial)
      - "confirm":    extracted = {"confirmed": true}

    For action="advance" from the builder step, `selection_updates` is the
    final commit before transitioning to confirm; the reply is the
    pre-confirm summary the user must approve.
    """

    action: Literal["advance", "stay", "cancel", "restart"] = Field(
        description=(
            "advance = user has clearly answered this step; extract their answer "
            "and move to the next step. stay = user is asking, exploring, or "
            "their answer was ambiguous — respond to them and remain on this "
            "step. cancel = user wants out. restart = user wants to start over."
        )
    )
    extracted: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Step-specific extracted answer. Only populate when action='advance'. "
            "See class docstring for per-step shape."
        ),
    )
    selection_updates: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Builder-step partial selection update merged into "
            "session['nfcore_wizard']['selection']. Only populated by the "
            "builder step's finalize_turn tool call. Keys are 'uids', "
            "'cohort_criteria', 'enrichment_fields'; partial keys are merged, "
            "missing keys are unchanged."
        ),
    )
    reply: str = Field(
        description=(
            "The full assistant message to show the user this turn. When "
            "action='advance', this should confirm the captured answer and pose "
            "the next step's question. When action='stay', it should respond to "
            "the user's exploration and re-pose this step's question."
        ),
    )
    notes: str = Field(
        default="",
        description="Short internal reasoning trace (for debugging, not shown).",
    )

    model_config = ConfigDict(extra="ignore")
