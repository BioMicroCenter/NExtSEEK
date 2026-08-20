"""Regression tests for V4-2 extra=forbid manifest contracts."""

import json

import pytest
from pydantic import ValidationError

from nessie_tests import bayes_manifest as bm
from nessie_tests.manifest import NessieManifestEntry


def _entry(vid="x.y"):
    return NessieManifestEntry(id=vid, family="f", tier="full", status="passed")


def test_bayes_manifest_rejects_unknown_top_level_key():
    payload = {
        "schema_version": "bayes_manifest/v1",
        "run_meta": {"mode": "bayesian"},
        "pairs": [],
        "unexpected_key": True,
    }
    with pytest.raises(ValidationError):
        bm.BayesManifest.model_validate(payload)


def test_bayes_pair_rejects_unknown_key():
    payload = {
        "id": "a.b",
        "family": "f",
        "hibayes_subtype": None,
        "ns": _entry().model_dump(),
        "cc": _entry().model_dump(),
        "bogus": 1,
    }
    with pytest.raises(ValidationError):
        bm.BayesPair.model_validate(payload)


def test_producer_written_manifest_validates_strict(tmp_path):
    m = bm.BayesManifest(
        schema_version="bayes_manifest/v1",
        run_meta={"mode": "bayesian"},
        pairs=[bm.BayesPair(id="x.y", family="f", hibayes_subtype=None, ns=_entry(), cc=_entry())],
    )
    bm.write_bayes_manifest(m, tmp_path)
    raw = json.loads((tmp_path / bm.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert raw["schema_version"] == "bayes_manifest/v1"
    loaded = bm.BayesManifest.model_validate(raw)
    assert loaded.schema_version == "bayes_manifest/v1"
