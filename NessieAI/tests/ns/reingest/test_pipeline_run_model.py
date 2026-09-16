import pytest
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError

from nextseek_api.assistant.models_db import PipelineRun

pytestmark = pytest.mark.django_db

_DEFAULT_COHORT = [{"d_seq_uid": "D.SEQ-EXAMPLE-1", "nfcore_sample": "CONTROL_REP1",
                     "fastq_1": "/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz",
                     "fastq_2": "/net/cluster/fastq/CONTROL_REP1_R2.fastq.gz"}]


def _run(**kw):
    user = get_user_model().objects.create(username=kw.pop("username", "tester"))
    run_dir = kw.pop("run_dir", "/net/cluster/runs/nfcore_rnaseq_fixture")
    cohort = kw.pop("cohort", _DEFAULT_COHORT)
    return PipelineRun.objects.create(
        run_dir=run_dir,
        run_name="nfcore_rnaseq_fixture",
        pipeline="nf-core/rnaseq",
        revision="3.18.0",
        launched_by=user,
        cohort=cohort,
        **kw)


def test_uid_for_resolves_a_sample_in_the_cohort():
    assert _run().uid_for("CONTROL_REP1") == "D.SEQ-EXAMPLE-1"


def test_uid_for_returns_none_for_a_sample_not_in_the_cohort():
    assert _run().uid_for("NOT_IN_RUN") is None


def test_uid_for_and_knows_sample_distinguish_null_uid_from_absent_sample():
    run = _run(
        run_dir="/net/cluster/runs/nfcore_rnaseq_fixture_unresolved",
        cohort=[
            {"d_seq_uid": "D.SEQ-EXAMPLE-1", "nfcore_sample": "CONTROL_REP1",
             "fastq_1": "/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz",
             "fastq_2": "/net/cluster/fastq/CONTROL_REP1_R2.fastq.gz"},
            {"d_seq_uid": None, "nfcore_sample": "UNKNOWN_REP1",
             "fastq_1": "/net/cluster/fastq/UNKNOWN_REP1_R1.fastq.gz",
             "fastq_2": "/net/cluster/fastq/UNKNOWN_REP1_R2.fastq.gz"},
        ],
    )

    # uid_for: resolved sample, null-UID sample, and absent sample all
    # exercised on the same cohort.
    assert run.uid_for("CONTROL_REP1") == "D.SEQ-EXAMPLE-1"
    assert run.uid_for("UNKNOWN_REP1") is None
    assert run.uid_for("NOT_IN_RUN") is None

    # knows_sample must tell the null-UID entry and the absent sample apart,
    # even though uid_for returns None for both.
    assert run.knows_sample("UNKNOWN_REP1") is True
    assert run.knows_sample("NOT_IN_RUN") is False


def test_run_dir_is_unique():
    _run()
    with pytest.raises(IntegrityError):
        _run(username="other")
