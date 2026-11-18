"""nf-core/fastquorum 2.0.0: the read-structure checks, the emitted samplesheet, a launch, and
the duplex_seq rule. Every rule is read off the pinned files in fixtures/nfcore/."""
import json
import warnings
from pathlib import Path

import pytest

from chat_nextseek.seqera.param_atlas import evaluate_data_driven_param, load_param_atlas

FIXTURES = Path(__file__).parent / "fixtures" / "nfcore"


@pytest.fixture(autouse=True)
def _pinned_schema(monkeypatch):
    from chat_nextseek.seqera import schema_check

    def get(pipeline, revision):
        return {"nextflow_schema": json.loads((FIXTURES / f"{pipeline}.nextflow_schema.json").read_text())}
    monkeypatch.setattr(schema_check, "get_schema", get)


def _row(sample="A", rs="8M1S+T 8M1S+T", **extra):
    return {"sample": sample, "read_structure": rs, **extra}


@pytest.mark.parametrize("rows, count, expect", [
    ([_row()], False, []),
    ([_row(rs="12M+T +T")], False, []),
    ([_row(rs="5M2S+T 5M2S+T +B")], False, []),
    ([_row(rs="+T +T +B +M")], False, []),
    ([_row(rs="")], False, ["no read_structure"]),
    ([_row(rs=None)], False, ["no read_structure"]),
    ([_row(rs="8X+T 8M+T")], False, ["not an fgbio read structure"]),
    ([_row(rs="+T8M")], False, ["not an fgbio read structure"]),
    ([_row(rs="+T +T")], False, ["names no UMI"]),
    ([_row(), _row(rs="10M1S+T 10M1S+T")], False, ["different read structures"]),
    ([_row("A"), _row("B", rs="12M+T +T")], False, []),
    ([_row(fastq_1="/n/a_1.fq.gz", fastq_2="/n/a_2.fq.gz")], True, []),
    ([_row(fastq_1="/n/a_1.fq.gz")], True, ["2 segment(s) but the row has 1 FASTQ"]),
    ([_row(fastq_1="/n/a_1.fq.gz")], False, []),
    ([_row()], True, []),   # no FASTQ yet (an accession to fetch): not counted
])
def test_read_structure_errors(rows, count, expect):
    """assets/schema_input.json requires read_structure; docs/usage.md: one segment per FASTQ, and
    the same structure on every row of one library (they are merged before grouping)."""
    from chat_nextseek.pipeline.agent_tools import _fastquorum_read_structure_errors
    errors = _fastquorum_read_structure_errors(rows, count_fastqs=count)
    assert len(errors) == len(expect) and all(e in got for e, got in zip(expect, errors)), errors


def _state(rows):
    return {"resolved": {"uids": [r["sample"] for r in rows], "accessions": []},
            "accession_file_paths": {r["sample"]: {
                "Link_PrimaryData": f"/net/x/{r['sample']}_R1.fastq.gz",
                "Link_SecondaryData": f"/net/x/{r['sample']}_R2.fastq.gz"} for r in rows},
            "bundle_key": "GRCh38"}


def _write(tmp_path, state, rows):
    from chat_nextseek.pipeline import agent_tools
    return json.loads(agent_tools.tool_write_samplesheet(
        object(), state, {"pipeline_key": "fastquorum", "cohorts": [{"label": "c", "rows": rows}]},
        str(tmp_path)))


def test_write_samplesheet_stops_for_a_missing_read_structure(tmp_path):
    rows = [_row("A", rs="")]
    out = _write(tmp_path, _state(rows), rows)
    assert out["ok"] is False and "ask the user" in out["errors"][0], out


def test_a_fastquorum_launch_writes_read_structure_and_passes_the_pinned_schema(tmp_path):
    import yaml
    from chat_nextseek.pipeline import agent_tools

    rows = [_row("A"), _row("B")]
    state = _state(rows)
    assert _write(tmp_path, state, rows)["ok"] is True
    header = open(state["artifacts"]["samplesheet"]).readline().strip().split(",")
    assert {"sample", "fastq_1", "fastq_2", "read_structure"} <= set(header), header
    out = json.loads(agent_tools.tool_configure_run(
        object(), state, {"pipeline_key": "fastquorum", "params": {"duplex_seq": True}}, str(tmp_path)))
    assert out["ok"] is True, out
    assert out["schema_check"]["status"] == "ok"
    params = yaml.safe_load(open(out["params_yml"]))
    assert params["duplex_seq"] is True and "mode" not in params


def test_the_menu_does_not_offer_mode():
    """--mode ht writes its final consensus to filtering/align_consensus_bam/ while
    consensus_filtering/filtered/ holds an unmapped BAM of the same name as rd's final one
    (workflows/fastquorum.nf, conf/modules.config), so the reingest map registers rd only."""
    from NessieAI.paths import CHAT_NEXTSEEK_DIR
    template = json.loads((CHAT_NEXTSEEK_DIR / "src/chat_nextseek/reports/templates/nfcore/fastquorum.json")
                          .read_text())
    assert "mode" not in template["params"]


@pytest.fixture(scope="module")
def duplex_seq():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return load_param_atlas()["pipelines"]["fastquorum"]["params"]["duplex_seq"]


@pytest.mark.parametrize("signals, value, verdict", [
    # the mouse panel cohort: LibraryStrategy OTHER, the panel named in LibraryDesign
    ({"LibraryDesign": "Duplex Sequencing Mouse Mutagenesis Panel v2.0", "LibraryStrategy": "OTHER"},
     "true", "derived_uncorroborated"),
    # the human hybrid-capture cohort: the duplex protocol, Targeted Capture / Hybrid Selection
    ({"Protocol": "Duplex-sequencing", "LibraryStrategy": "Targeted Capture"}, "true", "derived_uncorroborated"),
    ({"LibraryDesign": "Duplex Sequencing panel", "Protocol": "Duplex-sequencing"}, "true", "corroborated"),
    # groundtruth 230303ESS / 230306ESS: SequencingType "Duplex Sequencing"
    ({"SequencingType": "Duplex Sequencing", "LibraryStrategy": "Targeted Capture"}, "true",
     "derived_uncorroborated"),
    ({"LibraryDesign": "single-strand UMI panel"}, None, "conflict"),
    ({"Protocol": "Duplex-sequencing", "Name": "simplex library"}, None, "conflict"),
    ({"LibraryStrategy": "Targeted Capture"}, None, "absent"),
])
def test_duplex_seq_comes_from_the_metadata_or_is_asked(duplex_seq, signals, value, verdict):
    """nextflow_schema.json: duplex_seq defaults to true and switches CallDuplexConsensusReads
    and the Paired strategy on (docs/usage.md "Main Options"), so a single-strand UMI library left
    at the default is called as duplex. A duplex library says so; anything else is asked."""
    out = evaluate_data_driven_param(duplex_seq, signals)
    assert (out["value"], out["verdict"]) == (value, verdict), out
