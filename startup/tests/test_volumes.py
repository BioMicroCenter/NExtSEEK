"""Tests for startup.steps.volumes."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from startup.steps.volumes import (
    REQUIRED_VOLUMES,
    volume_names_for_prefix,
    ensure_volumes,
    ensure_cc_staging_dir,
)


def test_required_volumes_has_seven_names() -> None:
    assert len(REQUIRED_VOLUMES) == 7
    assert "seek-filestore" in REQUIRED_VOLUMES
    assert "seek-mysql-db" in REQUIRED_VOLUMES
    assert "seek-solr-data" in REQUIRED_VOLUMES
    assert "seek-cache" in REQUIRED_VOLUMES
    assert "nextseek-static-files" in REQUIRED_VOLUMES
    assert "neo4j-data" in REQUIRED_VOLUMES
    assert "dmac-cc-users" in REQUIRED_VOLUMES


def test_dmac_cc_users_volume_is_bootstrapped_like_seek_filestore() -> None:
    # G7-10: the CC user-tree volume follows the exact same external-volume
    # bootstrap pattern as the six SEEK/NExtSEEK volumes — `./startup.sh
    # install` (ensure_volumes) creates it; no manual host mkdir/sudo/chmod.
    assert "dmac-cc-users" in REQUIRED_VOLUMES
    assert "seek-filestore" in REQUIRED_VOLUMES


def test_volume_names_for_prefix_empty() -> None:
    names = volume_names_for_prefix("")
    assert names == REQUIRED_VOLUMES


def test_volume_names_for_prefix_test() -> None:
    names = volume_names_for_prefix("test-")
    assert "test-seek-filestore" in names
    assert "test-neo4j-data" in names
    assert "test-dmac-cc-users" in names
    assert all(n.startswith("test-") for n in names)


@patch("startup.steps.volumes.volume_exists")
@patch("startup.steps.volumes.volume_create")
def test_ensure_volumes_creates_missing(mock_create: MagicMock, mock_exists: MagicMock) -> None:
    mock_exists.return_value = False
    created = ensure_volumes("test-")
    assert mock_create.call_count == 7
    assert len(created) == 7
    assert "test-dmac-cc-users" in created


@patch("startup.steps.volumes.volume_exists")
@patch("startup.steps.volumes.volume_create")
def test_ensure_volumes_skips_existing(mock_create: MagicMock, mock_exists: MagicMock) -> None:
    mock_exists.return_value = True
    created = ensure_volumes("")
    assert mock_create.call_count == 0
    assert created == []


# --------------------------------------------------------------------------
# Step 2c (iter-1 M-1): `_staging` bootstrap. Docker's Engine refuses a
# container-create whose VolumeOptions.Subpath backing dir is absent inside
# the volume, and compose `restart:` does NOT retry create failures -- so
# the `_staging` dir (Task 14's future sidecar subpath mount) must exist
# before any `docker compose up` of the sidecar is ever attempted. This runs
# as part of `./startup.sh install` (no new operator step).
# --------------------------------------------------------------------------

@patch("startup.steps.volumes.bootstrap_staging_dir")
def test_ensure_cc_staging_dir_bootstraps_prefixed_dmac_cc_users_volume(
    mock_bootstrap: MagicMock,
) -> None:
    ensure_cc_staging_dir("test-")
    mock_bootstrap.assert_called_once_with("test-dmac-cc-users")


@patch("startup.steps.volumes.bootstrap_staging_dir")
def test_ensure_cc_staging_dir_empty_prefix(mock_bootstrap: MagicMock) -> None:
    ensure_cc_staging_dir("")
    mock_bootstrap.assert_called_once_with("dmac-cc-users")


def test_remove_volumes_removes_every_instance_volume() -> None:
    from startup.steps import volumes

    with patch.object(volumes, "volume_exists", return_value=True), \
         patch.object(volumes, "volume_users", return_value=[]), \
         patch.object(volumes, "volume_remove") as rm:
        removed = volumes.remove_volumes("t-")
    assert removed == volumes.volume_names_for_prefix("t-")
    assert [c.args[0] for c in rm.call_args_list] == removed


def test_remove_volumes_refuses_while_a_container_uses_one_and_removes_nothing() -> None:
    import pytest
    from startup.lib.docker_ops import DockerOpsError
    from startup.steps import volumes

    users = lambda name: ["stray"] if name == "t-neo4j-data" else []
    with patch.object(volumes, "volume_exists", return_value=True), \
         patch.object(volumes, "volume_users", side_effect=users), \
         patch.object(volumes, "volume_remove") as rm:
        with pytest.raises(DockerOpsError, match="t-neo4j-data"):
            volumes.remove_volumes("t-")
    rm.assert_not_called()


def test_remove_volumes_skips_volumes_that_are_already_gone() -> None:
    from startup.steps import volumes

    with patch.object(volumes, "volume_exists", return_value=False), \
         patch.object(volumes, "volume_users", return_value=[]), \
         patch.object(volumes, "volume_remove") as rm:
        assert volumes.remove_volumes("") == []
    rm.assert_not_called()
