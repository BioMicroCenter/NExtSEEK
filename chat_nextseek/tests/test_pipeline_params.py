import json
from pathlib import Path

import pytest

NFCORE_DIR = Path(__file__).resolve().parent.parent / "src" / "chat_nextseek" / "reports" / "templates" / "nfcore"


def _load(name):
    return json.loads((NFCORE_DIR / f"{name}.json").read_text())


def test_rnaseq_has_curated_params_and_reference_resources():
    doc = _load("rnaseq")
    params = doc["params"]
    assert params["aligner"]["allowed"] == ["star_salmon", "star_rsem", "hisat2"]
    assert params["aligner"]["default"] == "star_salmon"
    assert params["pseudo_aligner"]["default"] == "salmon"
    assert params["gencode"]["type"] == "bool"
    for key, spec in params.items():
        assert "type" in spec and "default" in spec and "steerable" in spec, key
    assert doc["reference_resources"] == ["fasta", "gtf", "star_index", "rsem_index", "salmon_index", "bed12"]


def test_scrnaseq_has_curated_params_and_reference_resources():
    doc = _load("scrnaseq")
    params = doc["params"]
    assert "alevin" in params["aligner"]["allowed"]
    assert params["protocol"]["default"] == "auto"
    assert "expected_cells" not in params
    assert doc["reference_resources"] == ["fasta", "gtf", "salmon_index", "star_index", "txp2gene"]


ALL_PIPELINES = ["rnaseq", "scrnaseq", "atacseq", "chipseq", "sarek", "methylseq", "ampliseq", "fetchngs"]


@pytest.mark.parametrize("key", ALL_PIPELINES)
def test_every_pipeline_json_has_params_and_reference_resources_keys(key):
    doc = _load(key)
    assert isinstance(doc.get("params"), dict)
    assert isinstance(doc.get("reference_resources"), list)


def test_reference_bundles_structure():
    doc = json.loads((NFCORE_DIR / "reference_bundles.json").read_text())
    assert doc["store_root"] is None  # v1: unconfigured
    s2b = doc["species_to_bundle"]
    assert s2b["mouse"] == "GRCm39"
    assert s2b["human"] == "GRCh38"
    assert s2b["human+mouse"] == "hg38_mm39"
    assert doc["bundles"]["GRCh38"]["igenomes_key"] == "GRCh38"
    assert doc["bundles"]["hg38_mm39"]["igenomes_key"] is None  # PDX combo: no iGenomes fallback
    assert "{store_root}" in doc["bundles"]["GRCh38"]["resources"]["fasta"]


from chat_nextseek.seqera import pipeline_params as pp


def test_load_pipeline_context_returns_params_menu():
    ctx = pp.load_pipeline_context("rnaseq")
    assert "aligner" in ctx["params"]
    assert ctx["reference_resources"][0] == "fasta"


def test_resolve_bundle_for_species_is_case_insensitive():
    assert pp.resolve_bundle_for_species("Mus musculus") == "GRCm39"
    assert pp.resolve_bundle_for_species("HUMAN") == "GRCh38"
    assert pp.resolve_bundle_for_species("doesnotexist") is None
    assert pp.resolve_bundle_for_species(None) is None


def test_build_reference_params_igenomes_fallback_when_store_unconfigured():
    params, status = pp.build_reference_params("rnaseq", "GRCm39")
    assert params == {"genome": "GRCm39"}
    assert status == "igenomes_fallback"


def test_build_reference_params_pdx_no_fallback():
    params, status = pp.build_reference_params("rnaseq", "hg38_mm39")
    assert params == {}
    assert status == "unconfigured_no_fallback"


def test_build_reference_params_emits_paths_when_store_configured(monkeypatch):
    bundles = {
        "store_root": "/refs",
        "species_to_bundle": {"mouse": "GRCm39"},
        "bundles": {"GRCm39": {"igenomes_key": "GRCm39", "gencode": True,
                               "resources": {"fasta": "{store_root}/GRCm39/genome.fa",
                                             "gtf": "{store_root}/GRCm39/genes.gtf",
                                             "star_index": "{store_root}/GRCm39/index/star/"}}}}
    monkeypatch.setattr(pp, "load_reference_bundles", lambda: bundles)
    params, status = pp.build_reference_params("rnaseq", "GRCm39")
    assert status == "configured"
    assert params["fasta"] == "/refs/GRCm39/genome.fa"
    assert params["gtf"] == "/refs/GRCm39/genes.gtf"
    assert params["star_index"] == "/refs/GRCm39/index/star/"
    assert "rsem_index" not in params
    assert params["gencode"] is True


def test_build_reference_params_configured_skips_gencode_for_pipeline_without_it(monkeypatch):
    # A bundle's gencode flag must NOT be stamped onto a pipeline that has no gencode param.
    bundles = {
        "store_root": "/refs",
        "species_to_bundle": {"human": "GRCh38"},
        "bundles": {"GRCh38": {"igenomes_key": "GRCh38", "gencode": True,
                               "resources": {"fasta": "{store_root}/GRCh38/genome.fa",
                                             "bwa_index": "{store_root}/GRCh38/bwa/"}}}}
    monkeypatch.setattr(pp, "load_reference_bundles", lambda: bundles)
    params, status = pp.build_reference_params("atacseq", "GRCh38")  # atacseq has no gencode param
    assert status == "configured"
    assert "gencode" not in params
    assert params["fasta"] == "/refs/GRCh38/genome.fa"
    assert params["bwa_index"] == "/refs/GRCh38/bwa/"
