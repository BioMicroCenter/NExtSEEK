"""Pydantic input models for batch-upload CLI JSON files."""
from __future__ import annotations

import pathlib
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(pathlib.Path("build_context/plugins/nextseek/bin")))
from _batch_upload_models import SchemaFile  # noqa: E402


def test_schema_file_accepts_single_dict():
    raw = {
        "sample_type": "TIS",
        "attributes": [
            {"title": "UID", "required": True, "is_title": True},
            {"title": "Name", "required": True},
        ],
    }
    parsed = SchemaFile.model_validate(raw)
    assert len(parsed.root) == 1
    assert parsed.root[0].sample_type == "TIS"
    assert parsed.root[0].attributes[0].title == "UID"


def test_schema_file_accepts_list():
    raw = [
        {
            "sample_type": "TIS",
            "attributes": [{"title": "Name", "required": True, "is_title": False}],
        }
    ]
    parsed = SchemaFile.model_validate(raw)
    assert len(parsed.root) == 1


def test_schema_file_accepts_SampleType_alias_and_extra_attr_fields():
    raw = {
        "SampleType": "TIS",
        "sample_attributes": [
            {
                "title": "Name",
                "required": True,
                "is_title": False,
                "description": "may evolve",
                "position": 3,
            }
        ],
    }
    parsed = SchemaFile.model_validate(raw)
    assert parsed.root[0].sample_type == "TIS"
    dumped = parsed.root[0].attributes[0].model_dump()
    assert dumped["description"] == "may evolve"


def test_schema_file_rejects_missing_title():
    with pytest.raises(ValidationError):
        SchemaFile.model_validate(
            {"sample_type": "TIS", "attributes": [{"required": True}]}
        )


def test_schema_file_rejects_empty_attributes():
    with pytest.raises(ValidationError):
        SchemaFile.model_validate({"sample_type": "TIS", "attributes": []})
