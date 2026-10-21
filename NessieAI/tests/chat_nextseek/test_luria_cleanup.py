"""The weekly Luria cleanup: what it decides for each labelled and unlabelled item.

Each test builds a fake LURIA_WORKING_PATH in tmp_path. Times are relative to the real
clock (NOW) because "a run folder created in the last 2 days" reads real mtimes.
"""
import ast
import os
import time
from pathlib import Path

import pytest

from chat_nextseek.luria import cleanup as cl
from chat_nextseek.luria import fetchngs_helpers as fh

DAY = 86400
NOW = float(int(time.time()))
R = "foo_261001_120000_0"
FQ = "SRX1_SRR1_1.fastq.gz"


def ago(days):
    return cl.format_time(NOW - days * DAY)


def _set_mtime(path, days):
    stamp = NOW - days * DAY
    os.utime(str(path), (stamp, stamp))


@pytest.fixture
def root(tmp_path):
    for name in ("runs", "work", "fastq_cache/fastq"):
        (tmp_path / name).mkdir(parents=True)
    return tmp_path


def add_run(root, name, *, job="100", finished=None, finished_days=None, saved_days=None,
            out_days=None, labelled=True, changed_days=30):
    run_dir = root / "runs" / name
    status = run_dir / "nessie_status"
    status.mkdir(parents=True)
    work = cl._RUN_SUFFIX_RE.sub("", name)
    if labelled:
        (status / "launched.txt").write_text(
            f"run_dir: {run_dir}\nwork_dir: {root / 'work' / work}\nslurm_job_id: {job}\n"
            f"launched_at: {ago(40)}\n")
    if finished:
        (status / "finished.txt").write_text(
            f"result: {finished}\nexit_code: {0 if finished == 'succeeded' else 1}\n"
            f"finished_at: {ago(finished_days)}\n")
    if saved_days is not None:
        (status / "saved.txt").write_text(f"saved_at: {ago(saved_days)}\npipeline_run_id: 1\n")
    if out_days is not None:
        out = run_dir / f"{work}.out"
        out.write_text("log\n")
        _set_mtime(out, out_days)
    _set_mtime(run_dir, changed_days)
    return run_dir


def add_work(root, name, size=1000):
    folder = root / "work" / name
    folder.mkdir()
    (folder / "task.bin").write_bytes(b"x" * size)
    return folder


def add_fastq(root, name, *, last_used_days=None, accs=(), md5=False):
    path = root / "fastq_cache" / "fastq" / name
    path.write_bytes(b"x" * 100)
    if last_used_days is not None:
        folder = root / "fastq_cache" / "nessie_status" / "last_used"
        folder.mkdir(parents=True, exist_ok=True)
        for acc in accs:
            (folder / f"{acc}.txt").write_text(f"last_used_at: {ago(last_used_days)}\n")
    if md5:
        (path.parent / "md5").mkdir(exist_ok=True)
        (path.parent / "md5" / (name + ".md5")).write_text("abc\n")
    return path


def add_claim(root, acc, job):
    folder = root / "fastq_cache" / "nessie_status" / "in_use" / acc
    folder.mkdir(parents=True, exist_ok=True)
    (folder / job).write_text("claimed_at: x\n")


def decide(root, queued=frozenset()):
    q = None if queued is None else set(queued)
    items, problems = cl.build_report(str(root), q, NOW)
    return ({os.path.relpath(i.path, str(root)): (i.decision, i.reason, i.days_left)
             for i in items}, problems)


# --- work folders -----------------------------------------------------------------------

def test_work_of_a_saved_run_waits_seven_days(root):
    add_run(root, R, finished="succeeded", finished_days=12, saved_days=9)
    add_work(root, "foo")
    add_run(root, "bar_261001_120000_0", finished="succeeded", finished_days=5, saved_days=3)
    add_work(root, "bar")
    got, _ = decide(root)
    assert got["work/foo"][0] == "delete"
    assert "results saved 9 days ago (runs/foo_261001_120000_0)" in got["work/foo"][1]
    assert got["work/bar"][0] == "keep" and got["work/bar"][2] == 4


def test_work_of_a_finished_unsaved_run_waits_thirty_days(root):
    add_run(root, R, finished="succeeded", finished_days=31)
    add_work(root, "foo")
    add_run(root, "bar_261001_120000_0", finished="succeeded", finished_days=10)
    add_work(root, "bar")
    got, _ = decide(root)
    assert got["work/foo"][0] == "delete" and "results not saved" in got["work/foo"][1]
    assert got["work/bar"][0] == "keep" and got["work/bar"][2] == 20


def test_work_of_a_failed_run_waits_thirty_days(root):
    add_run(root, R, finished="failed", finished_days=31)
    add_work(root, "foo")
    add_run(root, "bar_261001_120000_0", finished="failed", finished_days=29)
    add_work(root, "bar")
    got, _ = decide(root)
    assert got["work/foo"][0] == "delete" and "run failed 31 days ago" in got["work/foo"][1]
    assert got["work/bar"][0] == "keep" and got["work/bar"][2] == 1


def test_a_queued_run_keeps_its_work(root):
    add_run(root, R, job="555")
    add_work(root, "foo")
    got, _ = decide(root, queued={"555"})
    assert got["work/foo"][0] == "keep" and "in progress" in got["work/foo"][1]


def test_a_run_that_ended_without_a_label_counts_from_its_log(root):
    add_run(root, R, job="555", out_days=31)
    add_work(root, "foo")
    add_run(root, "bar_261001_120000_0", job="556", out_days=5)
    add_work(root, "bar")
    got, _ = decide(root, queued=set())
    assert got["work/foo"][0] == "delete" and "without a finished label" in got["work/foo"][1]
    assert got["work/bar"][0] == "keep"


def test_an_unreadable_queue_treats_unfinished_runs_as_in_progress(root):
    add_run(root, R, job="555", out_days=60)
    add_work(root, "foo")
    got, _ = decide(root, queued=None)
    assert got["work/foo"][0] == "keep"


def test_a_shared_work_folder_waits_for_every_run(root):
    add_run(root, "foo_261001_120000_0", finished="succeeded", finished_days=40, saved_days=30)
    later = add_run(root, "foo_261015_120000_0", job="777")
    add_work(root, "foo")
    got, _ = decide(root, queued={"777"})
    assert got["work/foo"][0] == "keep"
    (later / "nessie_status" / "saved.txt").write_text(f"saved_at: {ago(2)}\n")
    got, _ = decide(root, queued=set())
    assert got["work/foo"][0] == "keep" and got["work/foo"][2] == 5   # the latest run decides


def test_unlabelled_work_is_only_listed(root):
    add_work(root, "legacy")
    add_run(root, "legacy_250101_120000_0", labelled=False, changed_days=200)
    got, _ = decide(root)
    assert got["work/legacy"][0] == "unlabelled"


def test_a_run_being_launched_keeps_its_work(root):
    add_run(root, R, finished="succeeded", finished_days=40, saved_days=30)
    add_run(root, "foo_261020_120000_0", labelled=False, changed_days=0)
    add_work(root, "foo")
    got, _ = decide(root)
    assert got["work/foo"][0] == "keep" and "being launched" in got["work/foo"][1]


def test_an_unreadable_label_keeps_the_work_and_is_reported(root):
    run_dir = add_run(root, R, finished="succeeded", finished_days=40)
    (run_dir / "nessie_status" / "finished.txt").write_text("garbage without a colon\n")
    add_work(root, "foo")
    got, problems = decide(root)
    assert got["work/foo"][0] == "keep"
    assert any("finished.txt" in p for p in problems)


# --- downloads --------------------------------------------------------------------------

def test_a_fastq_idle_thirty_days_is_deleted_with_its_md5(root):
    add_fastq(root, FQ, last_used_days=31, accs=["SRX1"], md5=True)
    items, _ = cl.build_report(str(root), set(), NOW)
    (item,) = [i for i in items if i.kind == "fastq"]
    assert item.decision == "delete" and "last used 31 days ago" in item.reason
    assert [os.path.basename(p) for p, _ in item.companions] == [FQ + ".md5"]


def test_a_recently_used_fastq_is_kept(root):
    add_fastq(root, FQ, last_used_days=10, accs=["SRR1"])
    got, _ = decide(root)
    assert got["fastq_cache/fastq/" + FQ][0] == "keep"
    assert got["fastq_cache/fastq/" + FQ][2] == 20


def test_a_fastq_in_use_is_kept_whatever_its_age(root):
    add_fastq(root, FQ, last_used_days=90, accs=["SRR1"])
    add_claim(root, "SRR1", "4821")
    got, _ = decide(root, queued={"4821"})
    assert got["fastq_cache/fastq/" + FQ][0] == "keep"
    assert "job 4821" in got["fastq_cache/fastq/" + FQ][1]


def test_a_claim_on_either_accession_keeps_the_file(root):
    add_fastq(root, FQ, last_used_days=90, accs=["SRR1"])
    add_claim(root, "SRX1", "9")
    got, _ = decide(root, queued={"9"})
    assert got["fastq_cache/fastq/" + FQ][0] == "keep"


def test_a_stale_claim_is_ignored_and_reported(root):
    add_fastq(root, FQ, last_used_days=40, accs=["SRR1"])
    add_claim(root, "SRR1", "4821")
    got, problems = decide(root, queued=set())
    assert got["fastq_cache/fastq/" + FQ][0] == "delete"
    assert any("4821" in p and "no longer queued" in p for p in problems)


def test_an_unreadable_queue_honours_every_claim(root):
    add_fastq(root, FQ, last_used_days=40, accs=["SRR1"])
    add_claim(root, "SRR1", "4821")
    got, _ = decide(root, queued=None)
    assert got["fastq_cache/fastq/" + FQ][0] == "keep"


def test_a_fastq_no_labelled_run_used_is_unlabelled(root):
    add_fastq(root, FQ)
    got, _ = decide(root)
    assert got["fastq_cache/fastq/" + FQ][0] == "unlabelled"


def test_other_files_in_the_cache_are_not_managed(root):
    (root / "fastq_cache" / "fastq" / "notes.txt").write_text("x")
    (root / "fastq_cache" / "metadata").mkdir()
    (root / "fastq_cache" / "metadata" / "SRR1.runinfo_ftp.tsv").write_text("x")
    got, _ = decide(root)
    assert got == {}


def test_downloader_scratch_is_cleared_only_when_no_download_runs(root):
    (root / "fastq_cache" / "work" / "ab").mkdir(parents=True)
    got, _ = decide(root, queued=set())
    assert got["fastq_cache/work"][0] == "delete"
    add_claim(root, "SRR1", "9")
    got, _ = decide(root, queued={"9"})
    assert got["fastq_cache/work"][0] == "keep"


def test_empty_downloader_scratch_is_not_listed(root):
    (root / "fastq_cache" / "work").mkdir()
    got, _ = decide(root)
    assert "fastq_cache/work" not in got


def test_fastq_accessions_follow_the_rule_fill_matches_by(tmp_path):
    names = ["SRX6818190_SRR10085181_1.fastq.gz", "SRR1_2.fastq.gz", "SRR1.fastq.gz",
             "SRX1_SRR1.fastq.gz", "bad-name_1.fastq.gz", "notes.txt"]
    (tmp_path / "fastq").mkdir()
    for name in names:
        (tmp_path / "fastq" / name).write_text("x")
    fill_view = set(fh._cached_runs(str(tmp_path)))
    ours = {tuple(cl.fastq_accessions(n)) for n in names if cl.fastq_accessions(n)}
    assert ours == fill_view


# --- labels and the script itself -------------------------------------------------------

def test_read_label_missing_file_is_none_and_bad_lines_raise(tmp_path):
    assert cl.read_label(str(tmp_path / "nope.txt")) is None
    bad = tmp_path / "bad.txt"
    bad.write_text("no colon here\n")
    with pytest.raises(cl.LabelError):
        cl.read_label(str(bad))


def test_label_time_round_trips():
    assert cl.parse_time(cl.format_time(86400.0)) == 86400.0
    with pytest.raises(cl.LabelError):
        cl.label_time({"saved_at": "yesterday"}, "saved_at", "saved.txt")


def test_lock_is_the_one_claims_take():
    assert (cl.STATUS_DIR, cl.LOCK_NAME) == (fh.STATUS_DIR, fh.LOCK_NAME)


def test_script_stays_python36_and_standard_library_only():
    source = Path(cl.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, feature_version=(3, 6))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= {"argparse", "calendar", "errno", "fcntl", "math", "os", "re",
                        "shutil", "subprocess", "sys", "time"}
    for banned in ("from __future__", "dataclass", "capture_output", "text=True"):
        assert banned not in source, banned
