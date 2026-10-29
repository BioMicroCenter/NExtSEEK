"""A run whose release writes no pipeline_info/params*.json gets its params from Nessie's
launch files instead.

nf-core/atacseq 2.1.2 predates the nf-core template's params dump (its -profile test run,
fixture `atacseq/2.1.2-local-test`, has none), so without this every `$params.genome` and
`$params.aligner` in its map resolved to nothing on a real run. Nessie launches with
`--outdir .`, so its `params.yml` (the curated params) and `run.sh` (whose `nextflow run`
line passes `--genome` and the reference flags) sit beside `pipeline_info/`.
"""
import json
import shutil
from pathlib import Path

import pytest
import yaml

from chat_nextseek.luria.run_script import render_run_script
from NessieAI.ns.reingest import harvest, mapper, maps, parsers

pytestmark = pytest.mark.django_db

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "reingest_runs" / "atacseq" / "2.1.2-local-test"
ATTRS = json.loads((Path(__file__).resolve().parents[1] / "fixtures"
                    / "sampletype_attributes.json").read_text())["sample_types"]


def _run_sh(**overrides) -> str:
    """Nessie's own run.sh for an atacseq run, with a fetchngs pre-step: rendered by the
    launcher's template, so a template change that this parser cannot read fails here."""
    kwargs = dict(job_name="nfcore_atacseq", pipeline="nf-core/atacseq", revision="2.1.2",
                  run_dir="/net/x/runs/nfcore_atacseq_261029", genome="GRCh38",
                  work_dir="/net/x/work/nfcore_atacseq", singularity_cache="/net/x/singularity_cache",
                  resources={}, refs_root="/net/x/refs", needs_fetch=True,
                  fastq_cache="/net/x/fastq_cache", fetchngs_revision="1.12.0",
                  reference_cli_flags=["genome", "fasta", "gtf"])
    kwargs.update(overrides)
    return render_run_script(**kwargs)


def test_the_launch_params_are_the_params_file_overlaid_with_the_cli_flags():
    params = parsers.parse_launch_params(
        yaml.safe_dump({"aligner": "bowtie2", "read_length": 50, "genome": "GRCm39"}), _run_sh())
    assert params["genome"] == "GRCh38"          # the CLI flag wins over the params file
    assert params["aligner"] == "bowtie2"         # curated params come from params.yml
    assert params["read_length"] == 50
    assert params["input"] == "samplesheet.csv"   # the pipeline's command, not fetchngs' ids.csv
    assert params["outdir"] == "."
    assert params["fasta"].startswith("/net/x/refs/")


def test_a_flag_with_no_value_is_true_and_either_file_alone_is_read():
    script = "nextflow run nf-core/x -profile singularity \\\n  -params-file params.yml \\\n" \
             "  --genome GRCh38 --igenomes_ignore \\\n  -w /w -resume\n"
    assert parsers.parse_launch_params(None, script) == {"genome": "GRCh38", "igenomes_ignore": True}
    assert parsers.parse_launch_params("aligner: bwa\n", None) == {"aligner": "bwa"}
    assert parsers.parse_launch_params("- not a mapping\n", "echo hi\n") == {}


def _atacseq_run(tmp_path, files: dict[str, str]):
    root = tmp_path / "run"
    shutil.copytree(FIXTURE, root)
    for name, text in files.items():
        (root / name).write_text(text)
    inventory = json.loads((root / "inventory.json").read_text())
    uids: dict[str, str] = {}
    run = harvest.harvest_local(
        str(root), inventory=inventory, run_dir="/RUN",
        lookup_by_fastq=lambda path, types=None: [uids.setdefault(path, f"D.SEQ-TEST-{len(uids) + 1}")],
        sample_type_lookup=lambda found: {u: "D.SEQ" for u in found})
    result = mapper.apply(run, maps.load("atacseq"),
                          context={"has_attribute": lambda st, a: a in ATTRS.get(st, ())})
    return run, result


def test_atacseq_2_1_2_gets_its_genome_and_aligner_from_nessies_launch_files(tmp_path):
    run, result = _atacseq_run(tmp_path, {"params.yml": "aligner: bwa\nread_length: 50\n",
                                          "run.sh": _run_sh()})
    assert run.sources["params"] == "params.yml + run.sh"
    assert run.params["genome"] == "GRCh38"
    genomes = {r.sample_type: r.attributes["Genome"].value for r in result.rows if "Genome" in r.attributes}
    assert genomes and set(genomes.values()) == {"GRCh38"}
    assert "D.SEQ" in genomes
    aligners = {r.attributes["Aligner"].value for r in result.rows
                if r.sample_type == "A.ALN" and "Aligner" in r.attributes}
    assert aligners == {"bwa"}


def test_without_launch_files_the_run_has_no_params_as_before(tmp_path):
    run, result = _atacseq_run(tmp_path, {})
    assert run.params == {} and "params" not in run.sources
    assert not [r for r in result.rows if "Genome" in r.attributes]


def test_a_params_json_wins_over_the_launch_files(tmp_path):
    root = tmp_path / "run"
    (root / "pipeline_info").mkdir(parents=True)
    (root / "pipeline_info" / "params_2026-10-29_00-00-00.json").write_text(json.dumps({"genome": "GRCm39"}))
    (root / "params.yml").write_text("aligner: bwa\n")
    (root / "run.sh").write_text(_run_sh())
    run = harvest.harvest_local(str(root), run_dir="/RUN")
    assert run.params == {"genome": "GRCm39"}
    assert run.sources["params"].startswith("pipeline_info/params_")
