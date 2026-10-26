"""nf-core/methylseq's own "@" tokens: ConversionMethod and MethylationProtocol from the
run's boolean params (`special_library_types` in the pinned 4.2.0 nextflow_schema.json)."""
import json
from pathlib import Path

import pytest

from NessieAI.ns.reingest import manifest, pipelines

SCHEMA = (Path(__file__).resolve().parents[2] / "chat_nextseek" / "fixtures" / "nfcore"
          / "methylseq.nextflow_schema.json")


def _resolve(token: str, params: dict):
    run = manifest.RunManifest(run_dir="/RUN", params=params)
    return pipelines.tokens_for("nf-core/methylseq")[token](run, None, {})


@pytest.mark.parametrize("params, label", [
    ({}, "Bisulfite"),
    ({"taps": False, "em_seq": False}, "Bisulfite"),
    ({"taps": True}, "TAPS"),
    ({"em_seq": True}, "EM-seq"),
    ({"taps": True, "em_seq": True}, "TAPS"),
    ({"em_seq": "true"}, "EM-seq"),
])
def test_conversion_method(params, label):
    assert _resolve("conversion_method", params) == label


@pytest.mark.parametrize("params, label", [
    ({}, "WGBS"),
    ({"rrbs": False, "pbat": False, "single_cell": False, "nomeseq": False}, "WGBS"),
    ({"rrbs": True}, "RRBS"),
    ({"pbat": True}, "PBAT"),
    ({"single_cell": True}, "single-cell"),
    ({"nomeseq": True}, "NOMe-seq"),
    ({"rrbs": True, "pbat": True}, "RRBS"),
    ({"pbat": "true"}, "PBAT"),
    ({"rrbs": "false"}, "WGBS"),
])
def test_methylation_protocol(params, label):
    assert _resolve("methylation_protocol", params) == label


def test_the_params_read_are_the_pinned_schemas():
    schema = json.loads(SCHEMA.read_text())
    names = {name for group in (schema.get("$defs") or schema.get("definitions")).values()
             for name in group.get("properties", {})}
    from NessieAI.ns.reingest.pipelines import methylseq
    assert set(methylseq.FLAGS_READ) <= names


def test_the_tokens_are_methylseqs_own():
    assert "conversion_method" in pipelines.token_names_for("nf-core/methylseq")
    assert "conversion_method" not in pipelines.token_names_for("nf-core/rnaseq")
