"""The turn pass itself: only a hash is stored, the login is held encrypted and bound to its row, revocation and
the clean-up empty it. Piece 1 of SPEC-APPROACH-1."""
import hashlib
import time
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from nextseek_api.assistant import turn_pass
from nextseek_api.assistant.models_db import CCTurn, ChatSession, QueryTask
from nextseek_api.tests.turn_pass_support import PASSWORD, make_turn, make_user

pytestmark = pytest.mark.django_db


def test_the_row_keeps_only_the_hash_of_the_pass():
    turn, raw = make_turn()
    assert len(raw) == 43
    assert turn.pass_hash == hashlib.sha256(raw.encode("ascii")).hexdigest()
    row = CCTurn.objects.values().get(pk=turn.pk)
    stored = repr(row).encode("utf-8") + bytes(row["login_ciphertext"]) + bytes(row["login_nonce"])
    assert raw.encode("ascii") not in stored


def test_the_login_round_trips_with_awkward_characters():
    user = make_user("ü-user")
    turn, _raw = make_turn(user, login=("ü-user", PASSWORD))
    assert turn_pass.login_for(CCTurn.objects.get(pk=turn.pk)) == ("ü-user", PASSWORD)


def test_the_login_is_ciphertext_not_plaintext():
    turn, _raw = make_turn()
    blob = bytes(CCTurn.objects.get(pk=turn.pk).login_ciphertext)
    assert PASSWORD.encode("utf-8") not in blob
    assert turn.user.username.encode("utf-8") not in blob


def test_every_row_gets_its_own_random_nonce():
    first, _ = make_turn(make_user("a"))
    second, _ = make_turn(make_user("b"))
    assert len(bytes(first.login_nonce)) == 12
    assert bytes(first.login_nonce) != bytes(second.login_nonce)


def test_the_ciphertext_is_bound_to_its_own_task_and_user():
    first, _ = make_turn(make_user("a"))
    second, _ = make_turn(make_user("b"))
    CCTurn.objects.filter(pk=second.pk).update(
        login_nonce=first.login_nonce, login_ciphertext=first.login_ciphertext,
    )
    with pytest.raises(turn_pass.TurnPassError):
        turn_pass.login_for(CCTurn.objects.get(pk=second.pk))


def test_another_secret_key_cannot_read_the_login():
    turn, _ = make_turn()
    with override_settings(SECRET_KEY="a-different-secret"):
        with pytest.raises(turn_pass.TurnPassError):
            turn_pass.login_for(CCTurn.objects.get(pk=turn.pk))


def test_no_pass_is_issued_without_a_secret_key():
    user = make_user()
    chat = ChatSession.objects.create(user=user)
    task = QueryTask.objects.create(session=chat, user=user, query="q", status="running")
    with override_settings(SECRET_KEY=""):
        with pytest.raises(turn_pass.TurnPassError):
            turn_pass.issue_pass(task=task, chat=chat, user=user, login=("u", "p"))
    assert CCTurn.objects.count() == 0


def test_find_turn_finds_by_hash_and_refuses_anything_malformed():
    turn, raw = make_turn()
    assert turn_pass.find_turn(raw).pk == turn.pk
    for bad in (raw[:-1], raw + "\n", raw + "A", "x" * 43, "", None, raw.replace(raw[0], "+", 1)):
        assert turn_pass.find_turn(bad) is None


def test_a_fresh_pass_has_a_provisional_expiry_until_the_deadline_is_set():
    turn, _ = make_turn(deadline_in=None)
    assert timezone.now() < turn.expires_at <= timezone.now() + turn_pass.PROVISIONAL_TTL
    assert turn.deadline_at is None


def test_set_deadline_expires_the_pass_sixty_seconds_after_the_deadline():
    turn, _ = make_turn(deadline_in=None)
    deadline = time.time() + 180
    turn_pass.set_deadline(turn, deadline)
    row = CCTurn.objects.get(pk=turn.pk)
    assert abs(row.deadline_at.timestamp() - deadline) < 0.001
    assert row.expires_at - row.deadline_at == timedelta(seconds=60)
    assert (turn.deadline_at, turn.expires_at) == (row.deadline_at, row.expires_at)


def test_revoke_wipes_the_login_and_is_idempotent():
    turn, _ = make_turn()
    turn_pass.revoke(turn)
    first = CCTurn.objects.get(pk=turn.pk).revoked_at
    turn_pass.revoke(turn)
    row = CCTurn.objects.get(pk=turn.pk)
    assert row.revoked_at == first is not None
    assert row.login_nonce is None and row.login_ciphertext is None
    assert turn.revoked_at == first and turn.login_ciphertext is None
    with pytest.raises(turn_pass.TurnPassError):
        turn_pass.login_for(row)


def test_wipe_expired_empties_only_live_rows_past_their_expiry():
    # The live row first: issuing the second pass would otherwise already wipe the expired one.
    live, _ = make_turn(make_user("new"))
    expired, _ = make_turn(make_user("old"), deadline_in=-120)
    assert turn_pass.wipe_expired() == 1
    gone = CCTurn.objects.get(pk=expired.pk)
    assert gone.revoked_at is not None and gone.login_ciphertext is None
    kept = CCTurn.objects.get(pk=live.pk)
    assert kept.revoked_at is None and kept.login_ciphertext is not None
    assert turn_pass.wipe_expired() == 0


def test_issuing_a_pass_wipes_the_rows_a_dead_worker_left():
    """A worker killed mid-turn never reaches either revocation: the next issue wipes its row."""
    dead, _ = make_turn(make_user("dead"), deadline_in=-120)
    make_turn(make_user("next"))
    assert CCTurn.objects.get(pk=dead.pk).login_ciphertext is None


def test_the_management_command_runs_the_same_clean_up(capsys):
    expired, _ = make_turn(deadline_in=-120)
    call_command("wipe_turn_passes")
    assert "wiped 1" in capsys.readouterr().out
    assert CCTurn.objects.get(pk=expired.pk).login_ciphertext is None


def test_find_turn_never_returns_a_revoked_or_expired_pass():
    revoked, raw_r = make_turn(make_user("r"))
    turn_pass.revoke(revoked)
    assert turn_pass.find_turn(raw_r) is None
    expired, raw_e = make_turn(make_user("e"), deadline_in=-120)
    assert turn_pass.find_turn(raw_e) is None


def test_the_ciphertext_is_bound_to_its_task_for_the_same_user():
    user = make_user("same")
    first, _ = make_turn(user)
    second, _ = make_turn(user)
    CCTurn.objects.filter(pk=second.pk).update(
        login_nonce=first.login_nonce, login_ciphertext=first.login_ciphertext,
    )
    with pytest.raises(turn_pass.TurnPassError):
        turn_pass.login_for(CCTurn.objects.get(pk=second.pk))


def test_the_ciphertext_is_bound_to_its_user():
    turn, _ = make_turn(make_user("a"))
    CCTurn.objects.filter(pk=turn.pk).update(user=make_user("b"))
    with pytest.raises(turn_pass.TurnPassError):
        turn_pass.login_for(CCTurn.objects.get(pk=turn.pk))


def test_a_pass_whose_deadline_never_got_set_is_wiped_after_the_provisional_ttl():
    stuck, _ = make_turn(make_user("stuck"), deadline_in=None)
    past = timezone.now() - turn_pass.PROVISIONAL_TTL - timedelta(seconds=1)
    CCTurn.objects.filter(pk=stuck.pk).update(expires_at=past)
    make_turn(make_user("next"))
    assert CCTurn.objects.get(pk=stuck.pk).login_ciphertext is None
