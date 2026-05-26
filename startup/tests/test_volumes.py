"""Tests for startup.steps.volumes."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from startup.steps.volumes import REQUIRED_VOLUMES, volume_names_for_prefix, ensure_volumes


def test_required_volumes_has_six_names() -> None:
    assert len(REQUIRED_VOLUMES) == 6
    assert "seek-filestore" in REQUIRED_VOLUMES
    assert "seek-mysql-db" in REQUIRED_VOLUMES
    assert "seek-solr-data" in REQUIRED_VOLUMES
    assert "seek-cache" in REQUIRED_VOLUMES
    assert "nextseek-static-files" in REQUIRED_VOLUMES
    assert "neo4j-data" in REQUIRED_VOLUMES


def test_volume_names_for_prefix_empty() -> None:
    names = volume_names_for_prefix("")
    assert names == REQUIRED_VOLUMES


def test_volume_names_for_prefix_test() -> None:
    names = volume_names_for_prefix("test-")
    assert "test-seek-filestore" in names
    assert "test-neo4j-data" in names
    assert all(n.startswith("test-") for n in names)


@patch("startup.steps.volumes.volume_exists")
@patch("startup.steps.volumes.volume_create")
def test_ensure_volumes_creates_missing(mock_create: MagicMock, mock_exists: MagicMock) -> None:
    mock_exists.return_value = False
    created = ensure_volumes("test-")
    assert mock_create.call_count == 6
    assert len(created) == 6


@patch("startup.steps.volumes.volume_exists")
@patch("startup.steps.volumes.volume_create")
def test_ensure_volumes_skips_existing(mock_create: MagicMock, mock_exists: MagicMock) -> None:
    mock_exists.return_value = True
    created = ensure_volumes("")
    assert mock_create.call_count == 0
    assert created == []
