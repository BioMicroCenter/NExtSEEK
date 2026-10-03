"""cc_sweep_staging delivers a drop folder only into the scratch of the user whose turn made it (W3-2).

The folder name is the turn's ``pass_hash`` (``sha256`` of the raw pass, ``cc_staging.staging_folder_for``), so the
CC turn table names its owner. A folder with no turn row is swept as before.
"""
from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from NessieAI.cc import safe_fs
from nextseek_api.tests.turn_pass_support import make_turn, make_user

pytestmark = pytest.mark.django_db

PROJECT = "42-proj"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(safe_fs, "_AGENT_ROOTS", {})
    monkeypatch.setenv("DMAC_USER_ROOT_MOUNT", str(tmp_path / "users"))
    monkeypatch.setenv("DMAC_CC_USERS_VOLUME", "dmac-cc-users")


def _sweep(folder, user_id):
    with patch("NessieAI.cc.cc_staging.sweep_user_staging", return_value=SimpleNamespace(delivered=[])) as sweep:
        call_command("cc_sweep_staging", "--user-id", user_id, "--staging-folder", folder, "--project", PROJECT,
                     stdout=io.StringIO())
    return sweep


def test_a_folder_is_never_delivered_to_another_user():
    turn, _ = make_turn(make_user("owner"))
    make_user("other-user")
    with patch("NessieAI.cc.cc_staging.sweep_user_staging") as sweep, pytest.raises(CommandError) as exc:
        call_command("cc_sweep_staging", "--user-id", "other-user", "--staging-folder", turn.pass_hash,
                     "--project", PROJECT)
    sweep.assert_not_called()
    assert "other-user" not in str(exc.value) and "owner" not in str(exc.value)


def test_the_owner_gets_the_folder():
    turn, _ = make_turn(make_user("owner"))
    _sweep(turn.pass_hash, "owner").assert_called_once()


def test_a_folder_with_no_turn_row_is_swept_as_before():
    _sweep("a" * 64, "owner").assert_called_once()
