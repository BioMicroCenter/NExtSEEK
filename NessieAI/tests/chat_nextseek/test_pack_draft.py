"""The pack drafter, on sarek 3.10.0's committed schemas and default nf-test snapshot."""
import json
from pathlib import Path

import pytest

from chat_nextseek.seqera.nfcore_schema import clear_cache
from chat_nextseek.seqera.pack_draft import draft, snapshot_paths

FIX = Path(__file__).parent / "fixtures" / "nfcore"


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_cache()
    yield
    clear_cache()


def _fetch(url: str) -> bytes:
    if url.startswith("https://api.github.com/"):
        return json.dumps({"tree": [{"path": "tests/default.nf.test.snap"},
                                    {"path": "tests/default.nf.test"}]}).encode()
    if url.endswith("/tests/default.nf.test.snap"):
        return (FIX / "sarek.default.nf.test.snap").read_bytes()
    return (FIX / f"sarek.{url.rsplit('/', 1)[-1]}").read_bytes()


@pytest.fixture
def drafted():
    return draft("sarek", "3.10.0", _fetch)


def test_catalog_row_comes_from_the_pinned_schemas(drafted):
    row = drafted["catalog_row"]
    assert row["default_revision"] == "3.10.0"
    assert row["reference_cli_flags"] == ["genome", "fasta"]
    assert row["required_columns"] == ["patient", "sample"]
    assert row["samplesheet_input_kind"] == "fastq"


def test_traps_flag_patterns_that_reject_gzip_and_remote_defaults(drafted):
    traps = drafted["traps"]
    assert any(t.startswith("--intervals:") for t in traps), traps
    assert not any(t.startswith("--fasta:") for t in traps), traps
    assert any(t.startswith("--vep_cache: defaults to s3://") for t in traps), traps


def test_menu_skips_reference_and_hidden_params(drafted):
    menu = drafted["params_menu"]
    assert menu["tools"]["type"] == "string"
    assert "genome" not in menu and "fasta" not in menu
    assert all(not name.startswith("igenomes_") for name in menu)


def test_schema_defaults_pass_their_own_schema(drafted):
    assert drafted["default_check_skipped"] is None
    assert drafted["default_check_errors"] == []


def test_snapshot_paths_are_files_not_folders():
    snap = json.loads((FIX / "sarek.default.nf.test.snap").read_text())
    paths = snapshot_paths(snap)
    assert "variant_calling/strelka/test/test.strelka.variants.vcf.gz" in paths
    assert "preprocessing/recalibrated/test/test.recal.cram" in paths
    assert "csv" not in paths and "variant_calling" not in paths


def test_map_skeleton_pins_the_minor_release(drafted):
    skel = drafted["map_skeleton"]
    assert skel["applies_to_versions"] == ">=3.10,<3.11"
    assert "variant_calling" in skel["_files_by_folder"]
