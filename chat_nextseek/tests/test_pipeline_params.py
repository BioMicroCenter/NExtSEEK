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
