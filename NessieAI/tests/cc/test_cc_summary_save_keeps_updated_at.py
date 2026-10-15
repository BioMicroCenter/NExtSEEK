"""A background summary save must not bump the chat's updated_at (that moves it to the top of the sidebar);
a live turn's save still does."""
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

import NessieAI.cc.turn as cc_turn
from nextseek_api.assistant.models_db import ChatSession

pytestmark = pytest.mark.django_db


def _old_session():
    user = get_user_model().objects.create_user("sweep-bump", password="x")
    s = ChatSession.objects.create(user=user, extra_state={})
    old = timezone.now() - timedelta(hours=2)
    ChatSession.objects.filter(pk=s.pk).update(updated_at=old)
    s.refresh_from_db()
    return user, s, old


def test_the_summary_save_leaves_updated_at_alone():
    user, s, old = _old_session()
    cc_turn._persist_summary_standalone(user, s.session_id, {"gist": "g"}, "fp")
    s.refresh_from_db()
    assert s.extra_state["summary"] == {"gist": "g"}
    assert s.updated_at == old


def test_a_live_turn_save_still_bumps_updated_at():
    _, s, old = _old_session()
    cc_turn._merge_extra_state(s, k="v")
    s.refresh_from_db()
    assert s.updated_at > old
