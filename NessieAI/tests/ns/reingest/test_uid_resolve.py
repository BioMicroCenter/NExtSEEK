import pytest
from django.contrib.auth import get_user_model

from NessieAI.ns.reingest import manifest, uid_resolve
from nextseek_api.assistant.models_db import PipelineRun

pytestmark = pytest.mark.django_db

RUN_DIR = "/net/cluster/runs/nfcore_rnaseq_fixture"
ROWS = [{"sample": "CONTROL_REP1",
         "fastq_1": "/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz", "fastq_2": ""}]


def _none(_path):
    return []


def test_launch_record_wins_and_is_marked_as_such():
    PipelineRun.objects.create(
        run_dir=RUN_DIR, run_name="r", pipeline="nf-core/rnaseq",
        launched_by=get_user_model().objects.create(username="t"),
        cohort=[{"d_seq_uid": "D.SEQ-EXAMPLE-1", "nfcore_sample": "CONTROL_REP1",
                 "fastq_1": "/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz",
                 "fastq_2": None}])
    assert uid_resolve.resolve(ROWS, RUN_DIR, _none) == [
        ("CONTROL_REP1", "D.SEQ-EXAMPLE-1", manifest.RESOLUTION_LAUNCH_RECORD)]


def test_falls_back_to_an_exact_fastq_path_match():
    out = uid_resolve.resolve(ROWS, RUN_DIR, lambda p: ["D.SEQ-EXAMPLE-9"])
    assert out == [("CONTROL_REP1", "D.SEQ-EXAMPLE-9", manifest.RESOLUTION_FASTQ_EXACT)]


def test_two_candidate_parents_is_ambiguous_and_never_guessed():
    out = uid_resolve.resolve(ROWS, RUN_DIR, lambda p: ["D.SEQ-A", "D.SEQ-B"])
    assert out == [("CONTROL_REP1", None, manifest.RESOLUTION_AMBIGUOUS)]


def test_no_candidate_is_unresolved():
    assert uid_resolve.resolve(ROWS, RUN_DIR, _none) == [
        ("CONTROL_REP1", None, manifest.RESOLUTION_UNRESOLVED)]


def test_a_multi_run_sample_is_flagged_multirun_not_resolved():
    rows = [
        {"sample": "S1", "fastq_1": "/net/cluster/fastq/S1_L001_R1.fastq.gz", "fastq_2": ""},
        {"sample": "S1", "fastq_1": "/net/cluster/fastq/S1_L002_R1.fastq.gz", "fastq_2": ""},
    ]
    out = uid_resolve.resolve(rows, RUN_DIR, lambda p: ["D.SEQ-A"])
    assert {r[2] for r in out} == {manifest.RESOLUTION_MULTIRUN}
    assert all(r[1] is None for r in out)


def test_present_in_cohort_with_null_uid_stays_unresolved_without_trying_fastq():
    """knows_sample=True, uid_for=None: the launch already established there
    is nothing to match. A fastq candidate that WOULD match must be ignored --
    this is the step-2 case the brief's original code got wrong by only
    checking `uid_for` truthiness and falling through to the fastq path.
    """
    PipelineRun.objects.create(
        run_dir=RUN_DIR, run_name="r", pipeline="nf-core/rnaseq",
        launched_by=get_user_model().objects.create(username="t"),
        cohort=[{"d_seq_uid": None, "nfcore_sample": "CONTROL_REP1",
                 "fastq_1": "/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz",
                 "fastq_2": None}])
    out = uid_resolve.resolve(ROWS, RUN_DIR, lambda p: ["D.SEQ-WOULD-MATCH-IF-TRIED"])
    assert out == [("CONTROL_REP1", None, manifest.RESOLUTION_UNRESOLVED)]


def test_absent_from_a_known_cohort_falls_back_to_fastq():
    """knows_sample=False for THIS sample (a launch record exists for the run,
    but its cohort never mentions CONTROL_REP1): this is the step-3 case, and
    must still try the fastq fallback -- unlike the null-UID case above.
    """
    PipelineRun.objects.create(
        run_dir=RUN_DIR, run_name="r", pipeline="nf-core/rnaseq",
        launched_by=get_user_model().objects.create(username="t"),
        cohort=[{"d_seq_uid": "D.SEQ-OTHER", "nfcore_sample": "SOME_OTHER_SAMPLE",
                 "fastq_1": "/net/cluster/fastq/OTHER_R1.fastq.gz",
                 "fastq_2": None}])
    out = uid_resolve.resolve(ROWS, RUN_DIR, lambda p: ["D.SEQ-EXAMPLE-9"])
    assert out == [("CONTROL_REP1", "D.SEQ-EXAMPLE-9", manifest.RESOLUTION_FASTQ_EXACT)]
