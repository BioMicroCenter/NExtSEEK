"""Constraint pins (G-16): binding constraints get re-runnable oracles.
Update a pin ONLY with an enumerated commit-body note explaining why."""
import hashlib
import inspect
from pathlib import Path

import pytest

from nextseek_api.cc_assistant import router as cc_router

_REPO = Path(__file__).resolve().parents[3]

# NOTE: test_route_capabilities_unmodified (a sha256 pin on
# route_capabilities.json) was deleted here. Its premise — "the registry is
# unmodified" — has been deliberately false since Wave 6, it was already
# xfail(strict=False), and so it gated nothing while reading like a guard. The
# registry is *meant* to change; what must hold are its invariants.
#
# Replaced by behavioural coverage in
# nextseek_api/assistant/tests/test_route_capabilities.py, notably
# test_every_user_facing_tool_has_a_task_family — the test that would have caught
# system_agent sitting in nextseek_query.tools with no family behind it.


def test_heuristic_untouched():                      # F-9
    src = inspect.getsource(cc_router._heuristic)
    # sha256 of the function source at dev@8f5479a, computed the same way:
    assert hashlib.sha256(src.encode()).hexdigest() == "5c40f85b6c4fb3d7abe4540ba879e0599bd899a537e19dcb420da17a81d11dc9"


def test_r03_log_block_untouched():                  # R-03
    p = _REPO / "dmac_assistant" / "src" / "dmac_assistant" / "router" / "agent.py"
    src = p.read_text()
    assert "# R-03: never log reasoning text or user_query" in src
    # The router_decision log call must not carry reasoning text or the query.
    # Assert the quoted-KEY forms: the legitimate "reasoning_len" structural
    # fact stays allowed (R-03 permits length, forbids text — verified the
    # region contains "reasoning_len" on the pristine file).
    region = src[src.index('log.info('):src.index('return decision')]
    assert '"router_decision"' in region
    assert '"reasoning"' not in region and '"user_query"' not in region


def test_no_new_migrations():                        # Global Constraint
    dirs = sorted(str(p.relative_to(_REPO))
                  for p in _REPO.glob("nextseek_api/**/migrations/*.py"))
    # Pin updated V4-6: baseline 0a5b052a already carries V4-4/V4-5 ledger+generation migrations.
    assert dirs == [
        "nextseek_api/migrations/0001_initial.py",
        "nextseek_api/migrations/0002_querytask.py",
        "nextseek_api/migrations/0003_chatsession_title.py",
        "nextseek_api/migrations/0004_chatsession_extra_state_state_only.py",
        "nextseek_api/migrations/0005_chatsession_extra_state_column.py",
        "nextseek_api/migrations/0005_ensure_chatsession_extra_state_column.py",
        "nextseek_api/migrations/0006_merge_extra_state_guards.py",
        "nextseek_api/migrations/0007_ccsessiontranscript.py",
        "nextseek_api/migrations/0008_heal_cc_transcript_fk.py",
        "nextseek_api/migrations/0009_normalize_chat_log_turn_ids.py",
        "nextseek_api/migrations/0010_attribute_mutation_job.py",
        "nextseek_api/migrations/0010_turn_ledger.py",
        "nextseek_api/migrations/0011_attribute_async_orchestration.py",
        "nextseek_api/migrations/0011_turn_judgment.py",
        "nextseek_api/migrations/0012_posterior_generation.py",
        "nextseek_api/migrations/0013_family_posterior.py",
        "nextseek_api/migrations/0014_generation_activation_and_reservation.py",
        "nextseek_api/migrations/0015_v4_5_generation_audit_and_turn_pin.py",
        "nextseek_api/migrations/0016_paired_run_registry.py",
        "nextseek_api/migrations/0017_paid_run_state.py",
        "nextseek_api/migrations/0018_turn_ledger_attempted_provenance.py",
        "nextseek_api/migrations/0019_merge_attribute_async_turn_ledger.py",
        # Pin updated for the batch assay-registration endpoint: 0020 creates the
        # single AssayRegistrationJob table and its one declared index, nothing else.
        "nextseek_api/migrations/0020_assayregistrationjob.py",
        "nextseek_api/migrations/__init__.py",
        "nextseek_api/migrations/_cc_transcript_heal.py",
        "nextseek_api/migrations/_chat_log_normalize.py",
        "nextseek_api/migrations/_turn_ledger_heal.py",
    ]
