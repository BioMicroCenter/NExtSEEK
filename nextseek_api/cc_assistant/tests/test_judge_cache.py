import uuid

import pytest
from django.contrib.auth import get_user_model

from nextseek_api.assistant.models_db import ChatSession
from nextseek_api.cc_assistant.turn_ledger import record_turn
from nextseek_api.eval.judge_cache import fingerprint, needs_judging, record_failure, record_judgment

pytestmark = pytest.mark.django_db
_V = dict(prompt_version="p1", model_id="m1", schema_version=2)


@pytest.fixture
def eval_row(db):
    user = get_user_model().objects.create_user(
        username=f"judge-{uuid.uuid4().hex[:8]}", password="x"
    )
    s = ChatSession.objects.create(user=user)
    record_turn(str(s.session_id), 1, "container_cc", "baml", "sample_search", "baml")
    from nextseek_api.eval.export import export_rows

    return export_rows()[0]


def test_fingerprint_changes_when_prompt_version_changes(eval_row):
    a = fingerprint(eval_row, **_V)
    b = fingerprint(eval_row, **{**_V, "prompt_version": "p2"})
    assert a != b


def test_fingerprint_changes_when_model_changes(eval_row):
    assert fingerprint(eval_row, **_V) != fingerprint(eval_row, **{**_V, "model_id": "m2"})


def test_already_judged_row_is_not_rejudged(eval_row):
    record_judgment(eval_row, verdict={"ok": True}, **_V)
    assert needs_judging([eval_row], **_V) == []


def test_a_failed_judgment_is_retried_not_skipped(eval_row):
    record_failure(eval_row, error="timeout", **_V)
    assert needs_judging([eval_row], **_V) == [eval_row]


def test_version_bump_invalidates_an_existing_judgment(eval_row):
    record_judgment(eval_row, verdict={"ok": True}, **_V)
    assert needs_judging([eval_row], **{**_V, "prompt_version": "p2"}) == [eval_row]
