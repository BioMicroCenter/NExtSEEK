import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from nextseek_api.assistant.models_db import ChatSession, TurnLedger

pytestmark = pytest.mark.django_db


def _session():
    user = get_user_model().objects.create_user(
        username=f"ledger-{uuid.uuid4().hex[:8]}", password="x"
    )
    return ChatSession.objects.create(user=user)


def test_ledger_row_is_addressable_by_session_and_turn():
    s = _session()
    TurnLedger.objects.create(
        session=s,
        turn_number=1,
        route="nextseek_query",
        route_source="baml",
        task_family="sample_search",
        family_source="baml",
    )
    row = TurnLedger.objects.get(session=s, turn_number=1)
    assert row.route == "nextseek_query"
    assert row.task_family == "sample_search"


def test_duplicate_turn_number_in_one_session_is_rejected():
    s = _session()
    TurnLedger.objects.create(
        session=s,
        turn_number=1,
        route="container_cc",
        route_source="forced",
        task_family=None,
        family_source=None,
    )
    with pytest.raises(IntegrityError):
        TurnLedger.objects.create(
            session=s,
            turn_number=1,
            route="container_cc",
            route_source="forced",
            task_family=None,
            family_source=None,
        )


def test_same_turn_number_in_different_sessions_is_allowed():
    a, b = _session(), _session()
    TurnLedger.objects.create(
        session=a,
        turn_number=1,
        route="nextseek_query",
        route_source="heuristic",
        task_family=None,
        family_source=None,
    )
    TurnLedger.objects.create(
        session=b,
        turn_number=1,
        route="nextseek_query",
        route_source="heuristic",
        task_family=None,
        family_source=None,
    )
    assert TurnLedger.objects.filter(turn_number=1).count() == 2
