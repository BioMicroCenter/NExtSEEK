import uuid

import pytest
from django.contrib.auth import get_user_model

from nextseek_api.assistant.models_db import ChatSession, TurnLedger
from nextseek_api.cc_assistant.turn_ledger import LedgerCollision, record_turn

pytestmark = pytest.mark.django_db


def _session():
    user = get_user_model().objects.create_user(
        username=f"writer-{uuid.uuid4().hex[:8]}", password="x"
    )
    return ChatSession.objects.create(user=user)


def test_record_turn_persists_a_row():
    s = _session()
    row = record_turn(str(s.session_id), 1, "nextseek_query", "baml", "sample_search", "baml")
    assert TurnLedger.objects.filter(pk=row.pk).exists()


def test_concurrent_same_turn_number_raises_collision_not_integrity_error():
    s = _session()
    record_turn(str(s.session_id), 1, "container_cc", "baml", "sample_search", "baml")
    with pytest.raises(LedgerCollision):
        record_turn(str(s.session_id), 1, "container_cc", "baml", "sample_search", "baml")


def test_null_family_is_allowed_with_a_source_recorded():
    s = _session()
    row = record_turn(str(s.session_id), 2, "container_cc", "heuristic", None, None)
    assert row.task_family is None
    assert row.route_source == "heuristic"
    assert row.family_source is None
