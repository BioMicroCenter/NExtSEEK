# NessieAI/tests/ns/reingest/test_build_records.py
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from NessieAI.ns.reingest import build_records


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(build_records, "_ROOT", str(tmp_path / "builds"))
    return tmp_path


def _write(root, user_id=7, name="wb.xlsx", data=b"PK\x03\x04 bytes"):
    path = root / name
    path.write_bytes(data)
    return build_records.write(
        path=str(path), artifact_key="reingest_A_ALN", sample_type="A.ALN", mode="new",
        manifest_id="abc", disposition="SOFT_FLAG", open_warnings=["row 0: x"],
        row_count=1, project_id=14, project_note="", answers_digest="d", user_id=user_id)


def test_the_public_record_hides_the_server_path_and_ids_by_hash(root):
    record = _write(root)
    assert "path" not in record
    assert record["build_id"] == build_records.sha256_of(str(root / "wb.xlsx"))
    assert build_records.load(record["build_id"], 7)["path"].endswith("wb.xlsx")


def test_another_users_build_is_not_found(root):
    record = _write(root, user_id=7)
    with pytest.raises(build_records.BuildRecordError):
        build_records.load(record["build_id"], 8)


def test_an_anonymous_build_can_never_be_loaded(root):
    record = _write(root, user_id=None)
    with pytest.raises(build_records.BuildRecordError):
        build_records.load(record["build_id"], 0)


@pytest.mark.parametrize("bad", ["../../etc/passwd", "ABC", "", "a" * 63])
def test_a_malformed_build_id_is_refused(root, bad):
    with pytest.raises(build_records.BuildRecordError):
        build_records.load(bad, 7)


def test_project_lookup_returns_distinct_ids_and_raises_on_outage():
    from nextseek_api.services import reingest_lookups as lk
    cursor = MagicMock()
    cursor.fetchall.return_value = [(14,)]
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    with patch("django.db.connection", conn):
        assert lk.project_ids_for_uids_strict(["D.SEQ-EXAMPLE-1"]) == [14]
        assert lk.project_ids_for_uids_strict([]) == []
        conn.cursor.side_effect = Exception("down")
        with pytest.raises(RuntimeError):
            lk.project_ids_for_uids_strict(["D.SEQ-EXAMPLE-1"])
