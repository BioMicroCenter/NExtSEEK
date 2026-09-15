import pytest
from django.contrib.auth import get_user_model

from nextseek_api.assistant.models_db import PipelineRun

pytestmark = pytest.mark.django_db


def _run(**kw):
    user = get_user_model().objects.create(username=kw.pop("username", "tester"))
    return PipelineRun.objects.create(
        run_dir="/net/cluster/runs/nfcore_rnaseq_fixture",
        run_name="nfcore_rnaseq_fixture",
        pipeline="nf-core/rnaseq",
        revision="3.18.0",
        launched_by=user,
        cohort=[{"d_seq_uid": "D.SEQ-EXAMPLE-1", "nfcore_sample": "CONTROL_REP1",
                 "fastq_1": "/net/cluster/fastq/CONTROL_REP1_R1.fastq.gz",
                 "fastq_2": "/net/cluster/fastq/CONTROL_REP1_R2.fastq.gz"}],
        **kw)


def test_uid_for_resolves_a_sample_in_the_cohort():
    assert _run().uid_for("CONTROL_REP1") == "D.SEQ-EXAMPLE-1"


def test_uid_for_returns_none_for_a_sample_not_in_the_cohort():
    assert _run().uid_for("NOT_IN_RUN") is None


def test_run_dir_is_unique():
    _run()
    with pytest.raises(Exception):
        _run(username="other")
