"""Fail-fast local rollback tagging for every first-party rebuild."""
import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from startup.steps.rollback_tags import RollbackTagError, create_verified


NOW = datetime.datetime(2026, 8, 14, 12, 34, 56)


@patch("startup.steps.rollback_tags.subprocess.run")
def test_create_verified_tags_before_build_with_matching_identity(mock_run: MagicMock) -> None:
    def dispatch(args, **kwargs):
        if args[:4] == ["git", "-C", "/repo", "rev-parse"]:
            return MagicMock(returncode=0, stdout="abc1234\n", stderr="")
        if args[:3] == ["docker", "image", "inspect"]:
            return MagicMock(returncode=0, stdout="sha256:feedface\n", stderr="")
        if args[:2] == ["docker", "tag"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        raise AssertionError(args)

    mock_run.side_effect = dispatch
    tags = create_verified(
        ["nextseek-nextseek:latest", "dmac-assistant:poc"],
        Path("/repo"),
        now=NOW,
    )
    assert [tag.tag for tag in tags] == [
        "nextseek-nextseek:pre-20260814T123456-abc1234",
        "dmac-assistant:pre-20260814T123456-abc1234",
    ]
    assert all(tag.image_id == "sha256:feedface" for tag in tags)


@patch("startup.steps.rollback_tags.subprocess.run")
def test_missing_source_fails_before_any_tag(mock_run: MagicMock) -> None:
    def dispatch(args, **kwargs):
        if args[0] == "git":
            return MagicMock(returncode=0, stdout="abc1234\n", stderr="")
        return MagicMock(returncode=1, stdout="", stderr="No such image")

    mock_run.side_effect = dispatch
    with pytest.raises(RollbackTagError, match="cannot inspect rollback source"):
        create_verified(["missing:latest"], Path("/repo"), now=NOW)
    assert not any(call.args[0][:2] == ["docker", "tag"] for call in mock_run.call_args_list)


@patch("startup.steps.rollback_tags.subprocess.run")
def test_identity_mismatch_fails_closed(mock_run: MagicMock) -> None:
    inspect_ids = iter(["sha256:old\n", "sha256:wrong\n"])

    def dispatch(args, **kwargs):
        if args[0] == "git":
            return MagicMock(returncode=0, stdout="abc1234\n", stderr="")
        if args[:3] == ["docker", "image", "inspect"]:
            return MagicMock(returncode=0, stdout=next(inspect_ids), stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = dispatch
    with pytest.raises(RollbackTagError, match="identity mismatch"):
        create_verified(["nextseek-nextseek:latest"], Path("/repo"), now=NOW)
