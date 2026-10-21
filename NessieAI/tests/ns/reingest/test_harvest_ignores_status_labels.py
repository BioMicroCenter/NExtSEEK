"""The nessie_status/ labels inside a run folder never reach a reingest manifest."""
import fnmatch
import shutil
from pathlib import Path

import pytest

from NessieAI.ns.reingest import harvest

pytestmark = pytest.mark.django_db
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "reingest_runs" / "rnavar" / "1.3.0"
LABELS = ("README.txt", "launched.txt", "finished.txt", "saved.txt")


def _harvest(root):
    return harvest.harvest_local(str(root), lookup_by_fastq=lambda p, types=None: [],
                                 run_dir="/w/runs/r_1").model_dump()


def test_a_labelled_run_harvests_like_an_unlabelled_one(tmp_path):
    plain = tmp_path / "plain"
    shutil.copytree(FIXTURE, plain)
    labelled = tmp_path / "labelled"
    shutil.copytree(FIXTURE, labelled)
    (labelled / "nessie_status").mkdir()
    for name in LABELS:
        (labelled / "nessie_status" / name).write_text("key: value\n")
    assert _harvest(labelled) == _harvest(plain)


def test_no_harvest_glob_matches_a_label():
    globs = tuple(harvest.GENERIC_GLOBS) + tuple(harvest.inventory_globs())
    for name in LABELS:
        path = f"nessie_status/{name}"
        assert not any(fnmatch.fnmatch(path, g) for g in globs), path
