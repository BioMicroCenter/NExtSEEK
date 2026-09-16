import json

import pytest
from django.contrib.auth import get_user_model

from NessieAI.ns.reingest import launch_record
from nextseek_api.assistant.models_db import PipelineRun

pytestmark = pytest.mark.django_db

ENTRIES = [
    {"d_seq_uid": "D.SEQ-EXAMPLE-1", "nfcore_sample": "CONTROL_REP1",
     "fastq_1": "/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz",
     "fastq_2": "/net/cluster/fastq/CONTROL_REP1_R2.fastq.gz"},
    {"d_seq_uid": None, "nfcore_sample": "TREATED_REP1",
     "fastq_1": "/net/cluster/fastq/TREATED_REP1_R1.fastq.gz", "fastq_2": None},
]


def _call(**kw):
    user = get_user_model().objects.create(username=kw.pop("username", "tester"))
    params = dict(run_dir="/net/cluster/runs/nfcore_rnaseq_fixture",
                  run_name="nfcore_rnaseq_fixture", pipeline="nf-core/rnaseq",
                  revision="3.22.2", slurm_job_id="12345", user_id=user.id,
                  cohort_entries=ENTRIES)
    params.update(kw)
    return launch_record.record_launch(**params)


def test_records_every_entry_including_the_unresolved_one():
    run = _call()
    assert len(run.cohort) == 2
    assert run.uid_for("CONTROL_REP1") == "D.SEQ-EXAMPLE-1"
    assert run.uid_for("TREATED_REP1") is None
    assert run.knows_sample("TREATED_REP1") is True
    assert run.knows_sample("NEVER_RAN") is False


def test_relaunching_the_same_run_dir_updates_rather_than_duplicating():
    _call()
    run = _call(slurm_job_id="99999", username="u2")
    assert PipelineRun.objects.count() == 1
    assert run.slurm_job_id == "99999"


def test_a_failure_to_record_never_propagates():
    # A launch must not fail because bookkeeping failed.
    assert launch_record.record_launch(
        run_dir="", run_name="", pipeline="", revision="", slurm_job_id="",
        user_id=None, cohort_entries=ENTRIES) is None


def test_read_cohort_sidecar_returns_empty_when_absent(tmp_path):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,fastq_1\nS1,/x.fastq.gz\n")
    assert launch_record.read_cohort_sidecar(str(sheet)) == []


def test_read_cohort_sidecar_reads_the_file_beside_the_samplesheet(tmp_path):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,fastq_1\nS1,/x.fastq.gz\n")
    (tmp_path / "cohort.json").write_text(json.dumps(ENTRIES))
    assert launch_record.read_cohort_sidecar(str(sheet)) == ENTRIES


def test_read_cohort_sidecar_survives_malformed_json(tmp_path):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample\nS1\n")
    (tmp_path / "cohort.json").write_text("{not json")
    assert launch_record.read_cohort_sidecar(str(sheet)) == []
