"""A sample type with no Parent attribute says so, instead of the generic attribute text."""
from __future__ import annotations

import pytest

from nextseek_api.batch_upload.models import InputRowModel
from nextseek_api.batch_upload.prefetch import clear_caches
from nextseek_api.batch_upload.tests.test_orchestrator_levels import FakeDB, _run_orchestrator


@pytest.fixture(autouse=True)
def _fresh_attribute_cache():
    clear_caches()
    yield
    clear_caches()


def _row(meta, uid="NHP-260101TST-1"):
    return InputRowModel(UID=uid, SampleType="NHP_blood", json_metadata=meta, assay_ids=[])


def test_missing_parent_attribute_names_the_parent_column():
    db = FakeDB(sample_attributes={1: {"AntibodyParent"}})
    result = _run_orchestrator([_row("{}", "NHP-260101TST-0"), _row('{"Parent": "NHP-260101TST-0", "AntibodyParent": "NHP-260101TST-0"}')], db)

    msgs = [e["message"] for e in result["errors"]]
    assert (
        "SampleType NHP_blood has no Parent attribute, so the parent named in AntibodyParent "
        "cannot be linked. Add a Parent attribute to the sample type, or remove the parent column."
    ) in msgs
    assert result["totals"]["failed"] >= 1


def test_unrelated_bad_key_keeps_the_generic_message():
    db = FakeDB(sample_attributes={1: {"Parent"}})
    result = _run_orchestrator([_row('{"Bogus": "x"}')], db)
    assert any("'Bogus' is not a defined attribute" in e["message"] for e in result["errors"])
