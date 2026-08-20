import uuid

import pytest
from django.contrib.auth import get_user_model

from nextseek_api.assistant.models_db import ChatSession
from nextseek_api.cc_assistant.turn_ledger import record_turn
from nextseek_api.eval.export import EVAL_ROW_SCHEMA_VERSION, export_rows

pytestmark = pytest.mark.django_db


def _session():
    user = get_user_model().objects.create_user(
        username=f"export-{uuid.uuid4().hex[:8]}", password="x"
    )
    return ChatSession.objects.create(user=user)


def test_schema_is_versioned_and_not_the_legacy_14_column_shape():
    assert EVAL_ROW_SCHEMA_VERSION >= 3


def test_every_row_carries_route_and_family_as_separate_columns():
    s = _session()
    record_turn(str(s.session_id), 1, "container_cc", "baml", "sample_search", "baml")
    row = export_rows()[0]
    assert row.route == "container_cc"
    assert row.task_family == "sample_search"


def test_forced_turns_are_distinguishable_from_router_chosen_turns():
    s = _session()
    record_turn(str(s.session_id), 1, "container_cc", "forced", "sample_search", "corpus")
    row = export_rows()[0]
    assert row.route_source == "forced"
    assert row.family_source == "corpus"


def test_export_is_incremental_by_watermark():
    s = _session()
    a = record_turn(str(s.session_id), 1, "nextseek_query", "baml", "sample_search", "baml")
    record_turn(str(s.session_id), 2, "nextseek_query", "sticky", "sample_search", "baml")
    assert len(export_rows(since=a.created_at)) == 1
