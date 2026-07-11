from __future__ import annotations

import json
import pathlib
import sys

import httpx
import orjson
import polars as pl
import pytest

sys.path.insert(0, str(pathlib.Path("build_context/plugins/nextseek/bin")))
import _batch_upload_payload as bp


SCHEMA = [
    {
        "sample_type": "TIS",
        "attributes": [
            {"title": "UID", "required": True, "is_title": True},
            {"title": "Name", "required": True, "is_title": False},
            {"title": "Scientist", "required": True, "is_title": False},
            {"title": "Parent", "required": False, "is_title": False},
            {"title": "Treatment", "required": False, "is_title": False},
        ],
    }
]


def _build(tmp_path, rows, **kwargs) -> pl.DataFrame:
    paths = bp.build(
        rows,
        tmp_path,
        sample_type_attributes=kwargs.pop("sample_type_attributes", SCHEMA),
        id_to_title=kwargs.pop("id_to_title", {9: "RNA-seq", 2: "Imaging"}),
        resolved_current=kwargs.pop("resolved_current", {}),
        **kwargs,
    )
    assert [pathlib.Path(path).name for path in paths] == ["payload_flat.xlsx"]
    return pl.read_excel(paths[0], sheet_name="Samples", engine="calamine")


def _metadata(row: dict) -> dict:
    return orjson.loads(row["json_metadata"])


def _assay_ids(row: dict) -> list[int]:
    return orjson.loads(row["assay_ids"])


def _assay_titles(row: dict) -> list[str]:
    return orjson.loads(row["assay_titles"])


def _record(df: pl.DataFrame, index: int = 0) -> dict:
    return {column: df[column][index] for column in df.columns}


def test_blank_uid_create_required_attrs_pass(tmp_path):
    df = _build(
        tmp_path,
        [
            {
                "UID": "",
                "SampleType": "TIS",
                "attributes": {
                    "Name": "sample-1",
                    "Scientist": "Curator",
                    "Parent": "PARENT-1",
                },
            }
        ],
    )

    row = _record(df)
    assert row["UID"] in ("", None)
    assert _metadata(row) == {
        "Name": "sample-1",
        "Scientist": "Curator",
        "Parent": "PARENT-1",
    }
    assert "assay_ids" not in _metadata(row)
    assert "assay_titles" not in _metadata(row)
    assert "assay_titles" in df.columns
    assert any(column.startswith(bp.REVIEW_PREFIX) for column in df.columns)
    assert row[f"{bp.REVIEW_PREFIX}Name"] == "sample-1"
    assert row[f"{bp.REVIEW_PREFIX}Scientist"] == "Curator"
    assert row[f"{bp.REVIEW_PREFIX}Parent"] == "PARENT-1"


def test_create_missing_required_refuse(tmp_path):
    with pytest.raises(ValueError, match="required_missing:Name"):
        _build(
            tmp_path,
            [{"UID": "", "SampleType": "TIS", "attributes": {"Scientist": "Curator"}}],
        )


def test_partial_update_present_attrs_pass(tmp_path):
    df = _build(
        tmp_path,
        [
            {
                "UID": "D.IMG-230913ENG-1757-PUB",
                "SampleType": "TIS",
                "attributes": {"Treatment": "drug"},
            }
        ],
    )

    assert _metadata(_record(df)) == {
        "Treatment": "drug",
        "UID": "D.IMG-230913ENG-1757-PUB",
    }


def test_uid_only_row_refuse(tmp_path):
    with pytest.raises(ValueError, match="non_empty"):
        _build(
            tmp_path,
            [{"UID": "D.IMG-230913ENG-1757-PUB", "SampleType": "TIS", "attributes": {}}],
        )


def test_invented_attr_via_build_refuse(tmp_path):
    with pytest.raises(ValueError, match="invented_attribute:TIS:MadeUp"):
        _build(
            tmp_path,
            [
                {
                    "UID": "",
                    "SampleType": "TIS",
                    "attributes": {"Name": "sample", "Scientist": "Curator", "MadeUp": "x"},
                }
            ],
        )


def test_carry_forward(tmp_path):
    df = _build(
        tmp_path,
        [
            {
                "UID": "D.IMG-230913ENG-1757-PUB",
                "SampleType": "TIS",
                "attributes": {"Treatment": "drug"},
                "assay_ids": [9],
            }
        ],
        resolved_current={"D.IMG-230913ENG-1757-PUB": {351}},
        id_to_title={351: "Comet Chip Analysis - Data Attached", 9: "RNA-seq"},
    )

    row = _record(df)
    assert _assay_ids(row) == [9, 351]
    assert _assay_titles(row) == ["RNA-seq", "Comet Chip Analysis - Data Attached"]
    assert 351 in _assay_ids(row)


def test_carry_both_forward(tmp_path):
    title = "Comet Chip Analysis - Data Attached"
    df = _build(
        tmp_path,
        [
            {
                "UID": "D.IMG-230913ENG-1757-PUB",
                "SampleType": "TIS",
                "attributes": {"Treatment": "drug"},
            }
        ],
        resolved_current={"D.IMG-230913ENG-1757-PUB": {351, 260}},
        id_to_title={351: title, 260: title},
    )

    row = _record(df)
    assert _assay_ids(row) == [260, 351]
    assert _assay_titles(row) == [title, title]


def test_create_side_assay_path(tmp_path):
    df = _build(
        tmp_path,
        [
            {
                "UID": "",
                "SampleType": "TIS",
                "attributes": {"Name": "sample", "Scientist": "Curator"},
                "assay_ids": [9, 2],
            }
        ],
        resolved_current={"ignored": {351}},
        id_to_title={9: "RNA-seq", 2: "Imaging", 351: "Current"},
    )

    row = _record(df)
    assert _assay_ids(row) == [2, 9]
    assert _assay_titles(row) == ["Imaging", "RNA-seq"]


def test_update_present_blank_refuse(tmp_path):
    with pytest.raises(ValueError, match="present_blank:Treatment"):
        _build(
            tmp_path,
            [
                {
                    "UID": "D.IMG-230913ENG-1757-PUB",
                    "SampleType": "TIS",
                    "attributes": {"Parent": "PARENT-1", "Treatment": ""},
                }
            ],
        )


def test_assay_only_pass(tmp_path):
    df = _build(
        tmp_path,
        [
            {
                "UID": "D.IMG-230913ENG-1757-PUB",
                "SampleType": "TIS",
                "attributes": {},
                "assay_ids": [9],
            }
        ],
        resolved_current={"D.IMG-230913ENG-1757-PUB": {351}},
        id_to_title={351: "Current", 9: "RNA-seq"},
    )

    row = _record(df)
    assert _metadata(row) == {"UID": "D.IMG-230913ENG-1757-PUB"}
    assert _assay_ids(row) == [9, 351]


def test_assay_ids_titles_length_consistency(tmp_path):
    df = _build(
        tmp_path,
        [
            {
                "UID": "",
                "SampleType": "TIS",
                "attributes": {"Name": "sample", "Scientist": "Curator"},
                "assay_ids": [9],
            }
        ],
        id_to_title={9: "RNA-seq"},
    )
    row = _record(df)
    assert len(_assay_ids(row)) == len(_assay_titles(row)) == 1

    with pytest.raises(ValueError, match="assay_title_missing:9"):
        _build(
            tmp_path,
            [
                {
                    "UID": "",
                    "SampleType": "TIS",
                    "attributes": {"Name": "sample", "Scientist": "Curator"},
                    "assay_ids": [9],
                }
            ],
            id_to_title={},
        )


def test_no_merge_leak_from_visibility_result(tmp_path):
    visibility = {
        "json_metadata": {
            "UID": "D.IMG-230913ENG-1757-PUB",
            "Name": "old",
            "Scientist": "old-scientist",
            "Parent": "old-parent",
        }
    }
    df = _build(
        tmp_path,
        [
            {
                "UID": visibility["json_metadata"]["UID"],
                "SampleType": "TIS",
                "attributes": {"Treatment": "drug"},
            }
        ],
    )

    assert _metadata(_record(df)) == {
        "UID": "D.IMG-230913ENG-1757-PUB",
        "Treatment": "drug",
    }


def test_no_cap_3000_rows_and_review_columns(tmp_path):
    rows = [
        {
            "UID": "",
            "SampleType": "TIS",
            "attributes": {"Name": f"sample-{idx}", "Scientist": "Curator"},
        }
        for idx in range(3000)
    ]

    df = _build(tmp_path, rows)

    assert df.height == 3000
    assert f"{bp.REVIEW_PREFIX}do_not_edit" in df.columns


def test_build_does_no_network_retrieve(tmp_path, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("payload build must not create httpx clients or retrieve")

    monkeypatch.setattr(httpx, "Client", explode)
    df = _build(
        tmp_path,
        [
            {
                "UID": "D.IMG-230913ENG-1757-PUB",
                "SampleType": "TIS",
                "attributes": {"Treatment": "drug"},
            }
        ],
        resolved_current={"D.IMG-230913ENG-1757-PUB": {351}},
        id_to_title={351: "Current"},
    )

    assert _assay_ids(_record(df)) == [351]


def test_normalize_rows_raises_on_uid_mismatch():
    with pytest.raises(ValueError, match="uid_mismatch"):
        bp.normalize_rows(
            [
                {
                    "UID": "UID-1",
                    "SampleType": "TIS",
                    "attributes": {"UID": "UID-2", "Treatment": "drug"},
                }
            ]
        )
    with pytest.raises(ValueError, match="uid_mismatch"):
        bp.normalize_rows(
            [
                {
                    "UID": "",
                    "SampleType": "TIS",
                    "attributes": {"UID": "UID-2", "Treatment": "drug"},
                }
            ]
        )


def test_schema_missing_refuse(tmp_path):
    with pytest.raises(ValueError, match="schema_missing"):
        _build(
            tmp_path,
            [{"UID": "", "SampleType": "TIS", "attributes": {"Name": "sample"}}],
            sample_type_attributes=[],
        )


def test_json_metadata_uses_orjson_bytes_equivalent(tmp_path):
    df = _build(
        tmp_path,
        [
            {
                "UID": "",
                "SampleType": "TIS",
                "attributes": {"Name": "sample", "Scientist": "Curator"},
            }
        ],
    )

    assert json.loads(_record(df)["json_metadata"]) == {
        "Name": "sample",
        "Scientist": "Curator",
    }
