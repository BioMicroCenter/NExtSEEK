from unittest.mock import MagicMock, patch

import pytest

from chat_nextseek.pipeline import wizard as nfcore_wizard
from chat_nextseek.schemas import WizardAgentOutput


def _starting_session(pipeline: str | None = "rnaseq"):
    """Build a builder-step session.

    By default the test session has a pipeline already locked in; pass
    ``pipeline=None`` to simulate the slot-fill case where the user hasn't
    picked a pipeline yet (e.g. for advance-gate tests)."""
    return {
        "results_history": [{"bundle_id": 1}],
        "nfcore_wizard": {
            "active": True,
            "step": "builder",
            "pipeline": pipeline,
            "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []},
            "pinned_context": None,
        },
    }


def test_handle_builder_step_stay_merges_selection_updates():
    session = _starting_session()
    config = MagicMock()

    with patch("chat_nextseek.pipeline.wizard._wizard_agent_builder") as wa:
        wa.return_value = WizardAgentOutput(
            action="stay",
            selection_updates={"uids": ["U1", "U2"]},
            reply="Loaded 2 samples.",
        )
        result = nfcore_wizard._handle_builder_step(session, config, "use last search")

    assert result["action"] == "ask"
    assert result["reply"] == "Loaded 2 samples."
    assert session["nfcore_wizard"]["selection"]["uids"] == ["U1", "U2"]
    assert session["nfcore_wizard"]["selection"]["cohort_criteria"] == []
    assert session["nfcore_wizard"]["selection"]["enrichment_fields"] == []
    assert session["nfcore_wizard"]["step"] == "builder"


def test_handle_builder_step_advance_transitions_to_confirm():
    session = _starting_session()
    session["nfcore_wizard"]["selection"]["uids"] = ["U1", "U2"]
    config = MagicMock()

    with patch("chat_nextseek.pipeline.wizard._wizard_agent_builder") as wa, \
         patch("chat_nextseek.pipeline.wizard._check_sequencing_presence",
               return_value={"has_sequencing": True, "sampled": 2, "error": None}):
        wa.return_value = WizardAgentOutput(
            action="advance",
            selection_updates={},
            reply="Building: rnaseq, 2 UIDs. Confirm?",
        )
        result = nfcore_wizard._handle_builder_step(session, config, "build it")

    assert result["action"] == "ask"
    assert session["nfcore_wizard"]["step"] == "confirm"


def test_handle_builder_step_advance_blocked_when_no_sequencing_data():
    """Guardrail: when the selected UIDs have no sequencing samples in their
    lineage, the wizard refuses to advance to confirm and surfaces an
    actionable message instead of the LLM's reply."""
    session = _starting_session()
    session["nfcore_wizard"]["selection"]["uids"] = ["U1", "U2", "U3"]
    config = MagicMock()

    with patch("chat_nextseek.pipeline.wizard._wizard_agent_builder") as wa, \
         patch("chat_nextseek.pipeline.wizard._check_sequencing_presence",
               return_value={"has_sequencing": False, "sampled": 3, "error": None}):
        wa.return_value = WizardAgentOutput(
            action="advance",
            selection_updates={},
            reply="Looks great — confirm to build?",
        )
        result = nfcore_wizard._handle_builder_step(session, config, "build it")

    assert result["action"] == "ask"
    # Stays on builder, not advanced.
    assert session["nfcore_wizard"]["step"] == "builder"
    # User sees the guardrail message, not the LLM's confident reply.
    assert "sequencing" in result["reply"].lower()
    assert "3" in result["reply"]


def test_handle_builder_step_advance_proceeds_when_metadata_fetch_errors():
    """Fail-open: if metadata fetch fails (network/API/auth error), the
    guardrail must NOT block — let the user proceed rather than gating on
    infrastructure flakes."""
    session = _starting_session()
    session["nfcore_wizard"]["selection"]["uids"] = ["U1", "U2"]
    config = MagicMock()

    with patch("chat_nextseek.pipeline.wizard._wizard_agent_builder") as wa, \
         patch("chat_nextseek.pipeline.wizard._check_sequencing_presence",
               return_value={"has_sequencing": True, "sampled": 0, "error": "API down"}):
        wa.return_value = WizardAgentOutput(
            action="advance",
            selection_updates={},
            reply="Confirm?",
        )
        result = nfcore_wizard._handle_builder_step(session, config, "yes")

    assert result["action"] == "ask"
    assert session["nfcore_wizard"]["step"] == "confirm"


def test_check_sequencing_presence_detects_d_seq_in_summary():
    """When the metadata field summary contains a D.SEQ key in by_sample_type,
    _check_sequencing_presence reports has_sequencing=True."""
    config = MagicMock()
    fake_summary = {
        "fetched_for": ["U1", "U2"],
        "by_sample_type": {
            "D.SEQ": {"n_samples": 2, "fields": {}},
            "TIS": {"n_samples": 2, "fields": {}},
        },
        "lineage_edges": [],
    }
    with patch("chat_nextseek.pipeline.wizard._build_metadata_field_summary",
               return_value=fake_summary):
        result = nfcore_wizard._check_sequencing_presence(config, ["U1", "U2"])

    assert result["has_sequencing"] is True
    assert result["sampled"] == 2
    assert result["error"] is None


def test_check_sequencing_presence_returns_false_when_no_d_seq():
    """When by_sample_type has no sequencing types (D.SEQ etc.),
    _check_sequencing_presence reports has_sequencing=False."""
    config = MagicMock()
    fake_summary = {
        "fetched_for": ["U1", "U2", "U3"],
        "by_sample_type": {
            "TIS": {"n_samples": 3, "fields": {}},
            "NHP": {"n_samples": 3, "fields": {}},
        },
        "lineage_edges": [],
    }
    with patch("chat_nextseek.pipeline.wizard._build_metadata_field_summary",
               return_value=fake_summary):
        result = nfcore_wizard._check_sequencing_presence(config, ["U1", "U2", "U3"])

    assert result["has_sequencing"] is False
    assert result["sampled"] == 3
    assert result["error"] is None


def test_check_sequencing_presence_empty_uid_list():
    """Empty UID list returns has_sequencing=False with sampled=0 — caller
    handles this case separately (LLM should never advance with no UIDs)."""
    config = MagicMock()
    result = nfcore_wizard._check_sequencing_presence(config, [])
    assert result == {"has_sequencing": False, "sampled": 0, "error": None}


# ─────────────────────────────────────────────────────────────────────────────
# Slot-fill behavior: pipeline as a builder-step slot
# ─────────────────────────────────────────────────────────────────────────────


def test_pipeline_can_be_set_via_selection_updates():
    """In the slot-fill model, the user can pick a pipeline mid-builder by
    having the LLM emit selection_updates={'pipeline': '<key>'}."""
    session = _starting_session(pipeline=None)
    config = MagicMock()

    with patch("chat_nextseek.pipeline.wizard._wizard_agent_builder") as wa, \
         patch("chat_nextseek.pipeline.wizard._known_pipeline_keys",
               return_value=["rnaseq", "scrnaseq", "sarek"]):
        wa.return_value = WizardAgentOutput(
            action="stay",
            selection_updates={"pipeline": "rnaseq"},
            reply="Locked in rnaseq.",
        )
        result = nfcore_wizard._handle_builder_step(session, config, "let's do rnaseq")

    assert result["action"] == "ask"
    assert session["nfcore_wizard"]["pipeline"] == "rnaseq"
    # Pipeline lives at top-level, NOT inside selection.
    assert "pipeline" not in session["nfcore_wizard"]["selection"]
    assert session["nfcore_wizard"]["step"] == "builder"


def test_invalid_pipeline_rejected_with_helpful_reply():
    """A pipeline key that isn't in the catalog is refused and surfaced to
    the user — the LLM probably hallucinated a typo, so don't commit it."""
    session = _starting_session(pipeline=None)
    config = MagicMock()

    with patch("chat_nextseek.pipeline.wizard._wizard_agent_builder") as wa, \
         patch("chat_nextseek.pipeline.wizard._known_pipeline_keys",
               return_value=["rnaseq", "scrnaseq", "sarek"]):
        wa.return_value = WizardAgentOutput(
            action="stay",
            selection_updates={"pipeline": "nonsense"},
            reply="(LLM unhelpful reply)",
        )
        result = nfcore_wizard._handle_builder_step(session, config, "use foo pipeline")

    assert result["action"] == "ask"
    assert session["nfcore_wizard"]["pipeline"] is None
    assert "nonsense" in result["reply"]
    assert "rnaseq" in result["reply"]  # catalog options surfaced


def test_advance_refused_when_pipeline_missing():
    """The slot-fill advance gate requires both pipeline AND uids before
    transitioning to confirm."""
    session = _starting_session(pipeline=None)
    session["nfcore_wizard"]["selection"]["uids"] = ["U1", "U2"]
    config = MagicMock()

    with patch("chat_nextseek.pipeline.wizard._wizard_agent_builder") as wa, \
         patch("chat_nextseek.pipeline.wizard._known_pipeline_keys",
               return_value=["rnaseq", "scrnaseq"]):
        wa.return_value = WizardAgentOutput(
            action="advance",
            selection_updates={},
            reply="Build it!",
        )
        result = nfcore_wizard._handle_builder_step(session, config, "go")

    assert result["action"] == "ask"
    assert session["nfcore_wizard"]["step"] == "builder"  # NOT advanced
    assert "pipeline" in result["reply"].lower()
    assert "rnaseq" in result["reply"]  # catalog hint


def test_advance_refused_when_uids_missing():
    """If pipeline is set but no UIDs, advance is gated with a sample-set hint."""
    session = _starting_session(pipeline="rnaseq")
    # selection.uids stays []
    config = MagicMock()

    with patch("chat_nextseek.pipeline.wizard._wizard_agent_builder") as wa, \
         patch("chat_nextseek.pipeline.wizard._known_pipeline_keys",
               return_value=["rnaseq", "scrnaseq"]):
        wa.return_value = WizardAgentOutput(
            action="advance",
            selection_updates={},
            reply="Build it!",
        )
        result = nfcore_wizard._handle_builder_step(session, config, "go")

    assert result["action"] == "ask"
    assert session["nfcore_wizard"]["step"] == "builder"
    assert "sample" in result["reply"].lower()


def test_missing_slots_helper_lists_required_keys():
    """_missing_slots reports exactly which required slots are unfilled."""
    state_empty = {"pipeline": None, "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []}}
    assert nfcore_wizard._missing_slots(state_empty) == ["pipeline", "uids"]

    state_partial = {"pipeline": "rnaseq", "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []}}
    assert nfcore_wizard._missing_slots(state_partial) == ["uids"]

    state_full = {"pipeline": "rnaseq", "selection": {"uids": ["U1"], "cohort_criteria": [], "enrichment_fields": []}}
    assert nfcore_wizard._missing_slots(state_full) == []


def test_is_active_resets_old_pipeline_step_state():
    """Sessions parked on step='pipeline' (pre-slot-fill schema) must be
    cleared so they don't get stuck — the new state machine has no such step."""
    session = {
        "nfcore_wizard": {
            "active": True,
            "step": "pipeline",       # old (pre-slot-fill)
            "pipeline": None,
            "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []},
        },
    }
    assert nfcore_wizard.is_active(session) is False
    assert session["nfcore_wizard"] == {}


def test_handle_builder_step_cancel_clears_state():
    session = _starting_session()
    config = MagicMock()

    with patch("chat_nextseek.pipeline.wizard._wizard_agent_builder") as wa:
        wa.return_value = WizardAgentOutput(
            action="cancel",
            selection_updates={},
            reply="Cancelling the nf-core setup; pose that as a fresh question.",
        )
        result = nfcore_wizard._handle_builder_step(session, config, "never mind")

    assert result["action"] == "cancel"
    assert not nfcore_wizard.is_active(session)


def test_handle_builder_step_tool_loop_error_keeps_state_and_explains():
    from chat_nextseek.agents import WizardToolLoopError

    session = _starting_session()
    config = MagicMock()

    with patch("chat_nextseek.pipeline.wizard._wizard_agent_builder",
               side_effect=WizardToolLoopError("looped")):
        result = nfcore_wizard._handle_builder_step(session, config, "hi")

    assert result["action"] == "ask"
    assert ("rephrase" in result["reply"].lower()
            or "stuck" in result["reply"].lower())
    assert session["nfcore_wizard"]["step"] == "builder"
    assert nfcore_wizard.is_active(session)


def test_handle_builder_step_partial_selection_updates_preserve_other_keys():
    session = _starting_session()
    session["nfcore_wizard"]["selection"] = {
        "uids": ["U1"],
        "cohort_criteria": [{"Treatment1": "NDMA"}],
        "enrichment_fields": ["Timepoint"],
    }
    config = MagicMock()

    with patch("chat_nextseek.pipeline.wizard._wizard_agent_builder") as wa:
        wa.return_value = WizardAgentOutput(
            action="stay",
            selection_updates={"enrichment_fields": ["Timepoint", "LibraryStrategy"]},
            reply="Added LibraryStrategy.",
        )
        nfcore_wizard._handle_builder_step(session, config, "add LibraryStrategy")

    assert session["nfcore_wizard"]["selection"]["uids"] == ["U1"]   # unchanged
    assert session["nfcore_wizard"]["selection"]["cohort_criteria"] == [{"Treatment1": "NDMA"}]
    assert session["nfcore_wizard"]["selection"]["enrichment_fields"] == ["Timepoint", "LibraryStrategy"]


def test_steps_ordered_drops_pipeline_step():
    """After the slot-fill refactor, STEPS_ORDERED no longer has its own
    pipeline step — pipeline is a slot inside the builder turn."""
    assert nfcore_wizard.STEPS_ORDERED == ["builder", "confirm"]


def test_is_active_resets_old_schema_state():
    session = {
        "nfcore_wizard": {
            "active": True,
            "step": "samples",       # old shape
            "uids": ["U1"],          # top-level (old)
            "cohort_criteria": [],   # top-level (old)
        },
    }
    assert nfcore_wizard.is_active(session) is False
    assert session["nfcore_wizard"] == {}


def test_is_active_accepts_new_schema_state():
    session = {
        "nfcore_wizard": {
            "active": True, "step": "builder",
            "pipeline": "rnaseq",
            "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []},
        },
    }
    assert nfcore_wizard.is_active(session) is True


def test_start_initializes_builder_step_directly():
    """A fresh wizard skips the old pipeline step and lands on builder
    immediately so the user can fill any slot first."""
    config = MagicMock()
    config.WIZARD_AGENT_SYSTEM_PROMPT = "x"
    session = {}
    with patch("chat_nextseek.pipeline.wizard.format_available_pipelines",
               return_value="rnaseq, scrnaseq"):
        result = nfcore_wizard.start(
            session, config,
            user_query="build an nf-core samplesheet",
            parser_plan=None,
            reporter_plan=None,
        )
    state = session["nfcore_wizard"]
    assert state["step"] == "builder"
    assert state["selection"] == {"uids": [], "cohort_criteria": [], "enrichment_fields": []}
    assert state["pinned_context"] is None
    # CRITICAL: old top-level keys MUST NOT be present (else is_active()'s
    # old-schema guard immediately wipes the state).
    assert "uids" not in state
    assert "cohort_criteria" not in state
    assert "enrichment_fields" not in state
    # Intro renders the slot checklist and invites the user to start anywhere.
    reply = result["reply"]
    assert "Pipeline" in reply
    assert "Samples" in reply
    assert "Cohort criteria" in reply
    assert "Enrichment fields" in reply
    assert "Where would you like to start" in reply


def test_start_then_is_active_remains_active():
    """End-to-end: a fresh wizard started today must be is_active==True
    immediately afterward (regression test for the Task 9 / Task 10 coupling)."""
    config = MagicMock()
    config.WIZARD_AGENT_SYSTEM_PROMPT = "x"
    session = {}
    with patch("chat_nextseek.pipeline.wizard.format_available_pipelines",
               return_value="rnaseq, scrnaseq"):
        nfcore_wizard.start(
            session, config,
            user_query="x",
            parser_plan=None,
            reporter_plan=None,
        )
    assert nfcore_wizard.is_active(session) is True


def test_restart_action_resets_to_builder_step():
    """After a restart action, the wizard returns to STEP_BUILDER (the new
    canonical fresh state) with an empty selection and remains active."""
    session = {
        "results_history": [],
        "nfcore_wizard": {
            "active": True, "step": "confirm",
            "pipeline": "rnaseq",
            "selection": {"uids": ["U1"], "cohort_criteria": [{"x": "y"}], "enrichment_fields": ["Z"]},
            "pinned_context": None,
        },
    }
    config = MagicMock()
    config.WIZARD_AGENT_SYSTEM_PROMPT = "x"

    # Simulate a 'restart' decision coming from wizard_agent (called inside handle_turn for
    # non-builder steps). handle_turn() returns the restart-handling result.
    with patch("chat_nextseek.agents.wizard_agent") as wa:
        wa.return_value = WizardAgentOutput(action="restart", extracted={}, reply="ok, starting over")
        with patch("chat_nextseek.pipeline.wizard._step_context", return_value={}), \
             patch("chat_nextseek.pipeline.wizard.format_available_pipelines",
                   return_value="rnaseq, scrnaseq"):
            result = nfcore_wizard.handle_turn(session, config, "start over")

    assert result["action"] == "ask"
    state = session["nfcore_wizard"]
    assert state["step"] == "builder"
    assert state["pipeline"] is None
    assert state["selection"] == {"uids": [], "cohort_criteria": [], "enrichment_fields": []}
    assert state["pinned_context"] is None
    # CRITICAL: must still be active after restart (regression guard).
    assert nfcore_wizard.is_active(session) is True
    # And no old-shape keys leaked in.
    assert "uids" not in state
    assert "cohort_criteria" not in state
    # The restart reply should re-emit the slot checklist so the user sees the fresh canvas.
    assert "Where would you like to start" in result["reply"]


# ─────────────────────────────────────────────────────────────────────────────
# build_execution_params — reads from state["selection"], not top-level keys
# ─────────────────────────────────────────────────────────────────────────────


def test_build_execution_params_reads_from_new_selection_shape():
    """build_execution_params must pull uids/cohort_criteria/enrichment_fields
    from state['selection'], not from top-level keys."""
    state = {
        "active": True, "step": "confirm",
        "pipeline": "rnaseq",
        "selection": {
            "uids": ["U1", "U2"],
            "cohort_criteria": [{"Treatment1": "NDMA"}],
            "enrichment_fields": ["Timepoint"],
        },
        "pinned_context": None,
    }
    params = nfcore_wizard.build_execution_params(state)
    assert params["uids"] == ["U1", "U2"]
    assert params["report_type"] == "NFCORE_RNASEQ"
    # One cohort criterion → one cohort dict.
    assert len(params["pre_supplied_cohorts"]) == 1
    cohort = params["pre_supplied_cohorts"][0]
    assert cohort["cohort_criterion"] == {"Treatment1": "NDMA"}
    assert cohort["enrichment_metadata_fields"] == ["Timepoint"]


def test_build_execution_params_single_cohort_when_no_criteria():
    """When cohort_criteria is empty, a single-cohort default is produced."""
    state = {
        "pipeline": "rnaseq",
        "selection": {"uids": ["U1"], "cohort_criteria": [], "enrichment_fields": []},
    }
    params = nfcore_wizard.build_execution_params(state)
    assert len(params["pre_supplied_cohorts"]) == 1
    assert params["pre_supplied_cohorts"][0]["cohort_criterion"] == {}
    assert params["uids"] == ["U1"]


def test_snapshot_for_chat_log_uses_new_selection_shape():
    session = {
        "nfcore_wizard": {
            "active": True, "step": "builder",
            "pipeline": "rnaseq",
            "selection": {
                "uids": ["U1", "U2", "U3"],
                "cohort_criteria": [{"Treatment1": "NDMA"}],
                "enrichment_fields": ["Timepoint"],
            },
            "pinned_context": {"source": "last_search", "bundle_id": 7},
        },
    }
    snap = nfcore_wizard.snapshot_for_chat_log(session)
    assert snap["active"] is True
    assert snap["step"] == "builder"
    assert snap["pipeline"] == "rnaseq"
    assert snap["uid_count"] == 3
    assert snap["cohort_count"] == 1
    assert snap["enrichment_count"] == 1


def test_handle_builder_step_passes_history_messages_from_chat_log():
    """_handle_builder_step pulls wizard turns from chat_log, expands them,
    and passes them to _wizard_agent_builder as history_messages.

    Falls back to assistant_reply_preview when assistant_reply is absent
    (back-compat with older chat_log turns)."""
    from chat_nextseek.pipeline.wizard import _handle_builder_step

    session = {
        "chat_log": [
            {"turn_id": 1, "mode": "new_search",
             "user_query": "find ndma mice", "assistant_reply_preview": "found 195"},
            {"turn_id": 2, "mode": "nfcore_wizard", "wizard_state": {"step": "pipeline"},
             "user_query": "lets build nfcore", "assistant_reply_preview": "pick a pipeline"},
            {"turn_id": 3, "mode": "nfcore_wizard", "wizard_state": {"step": "builder"},
             "user_query": "rnaseq", "assistant_reply_preview": "great, rnaseq"},
        ],
        "nfcore_wizard": {
            "active": True, "step": "builder", "pipeline": "rnaseq",
            "selection": {"uids": [], "cohort_criteria": [], "enrichment_fields": []},
        },
    }
    captured = {}

    def fake_builder(*, config, session, user_text, **kwargs):
        captured["history_messages"] = kwargs.get("history_messages")
        captured["user_text"] = user_text
        from chat_nextseek.schemas.wizard import WizardAgentOutput
        return WizardAgentOutput(action="stay", selection_updates={}, reply="ok")

    with patch("chat_nextseek.pipeline.wizard._wizard_agent_builder",
               side_effect=fake_builder):
        _handle_builder_step(session, MagicMock(), "what fields exist?")

    assert captured["user_text"] == "what fields exist?"
    history = captured["history_messages"]
    assert history is not None and len(history) == 4
    assert history[0] == {"role": "user", "content": "lets build nfcore"}
    assert history[3] == {"role": "assistant", "content": "great, rnaseq"}


def test_wizard_builder_history_prefers_full_assistant_reply_over_preview():
    """Regression: the builder must see the FULL assistant_reply (not the
    280-char preview). The preview truncates mid-list of UIDs, which caused
    the LLM to re-derive (and drift from) prior commitments — observed in
    session 05963e55... where 10 proposed UIDs were swapped for a different
    10 by turn 8 because the preview cut off at UID 6."""
    from chat_nextseek.pipeline.wizard import _wizard_builder_history_messages

    long_reply = (
        "Here are the 10 UIDs: NHP-1, NHP-2, NHP-3, NHP-4, NHP-5, NHP-6, NHP-7, "
        "NHP-8, NHP-9, NHP-10 — these are the ones we're locking in."
    )
    truncated_preview = long_reply[:280] + "..."  # ~simulates the 280-char cap
    session = {
        "chat_log": [
            {
                "turn_id": 1,
                "mode": "nfcore_wizard",
                "user_query": "give me 10 UIDs",
                "assistant_reply": long_reply,
                "assistant_reply_preview": truncated_preview,
            },
        ],
    }
    messages = _wizard_builder_history_messages(session)
    assert len(messages) == 2
    # The assistant message must be the FULL reply, not the (would-be) truncated preview.
    assert messages[1]["content"] == long_reply
    assert "NHP-10" in messages[1]["content"]


def test_wizard_builder_history_falls_back_to_preview_when_full_reply_missing():
    """Older chat_log turns may only have assistant_reply_preview; the helper
    must still surface them (back-compat)."""
    from chat_nextseek.pipeline.wizard import _wizard_builder_history_messages

    session = {
        "chat_log": [
            {
                "turn_id": 1,
                "mode": "nfcore_wizard",
                "user_query": "hi",
                "assistant_reply_preview": "preview only",
                # no assistant_reply key
            },
        ],
    }
    messages = _wizard_builder_history_messages(session)
    assert len(messages) == 2
    assert messages[1]["content"] == "preview only"
