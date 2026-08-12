"""V4-6 ledger provenance on route decisions."""
import uuid

import pytest
from django.contrib.auth import get_user_model

from nextseek_api.assistant.models_db import ChatSession, TurnLedger
from nextseek_api.cc_assistant import router as cc_router
from nextseek_api.services import cc_assistant as svc

pytestmark = pytest.mark.django_db


def _session():
    user = get_user_model().objects.create_user(
        username=f"v46-{uuid.uuid4().hex[:8]}", password="x"
    )
    return ChatSession.objects.create(user=user, extra_state={"chat_log": []})


def test_record_ledger_row_persists_generation_id():
    session = _session()
    decision = cc_router.RouteDecision(
        route="nextseek_query",
        model_class=None,
        model_id=None,
        reasoning="posterior",
        source="posterior",
        task_family="sample_search",
        family_source="baml",
        generation_id=42,
        generation_hash="c" * 64,
    )
    svc._record_ledger_row(session, decision)
    row = TurnLedger.objects.get(session=session, turn_number=1)
    assert row.route_source == "posterior"
    assert row.pinned_generation_id == 42
    assert row.pinned_generation_hash == "c" * 64


def test_ledger_collision_does_not_raise():
    session = _session()
    decision = cc_router.RouteDecision(
        route="container_cc",
        model_class="opus",
        model_id=None,
        reasoning="baml",
        source="baml",
    )
    svc._record_ledger_row(session, decision)
    svc._record_ledger_row(session, decision)  # duplicate turn_number — swallowed
