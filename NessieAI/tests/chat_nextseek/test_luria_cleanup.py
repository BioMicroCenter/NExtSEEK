"""The weekly Luria cleanup: what it decides for each labelled and unlabelled item.

Each test builds a fake LURIA_WORKING_PATH in tmp_path. Times are relative to the real
clock (NOW) because "a run folder created in the last 2 days" reads real mtimes.
"""
import ast
import os
import subprocess
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
        job_line = "" if job is None else f"slurm_job_id: {job}\n"
        (status / "launched.txt").write_text(
            f"run_dir: {run_dir}\nwork_dir: {root / 'work' / work}\n{job_line}"
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


@pytest.mark.parametrize("job", [None, ""])
def test_a_label_naming_no_job_id_keeps_the_work_and_is_reported(root, job):
    # Without a job id the run would look ended and its work folder would age out.
    add_run(root, R, job=job, out_days=60)
    add_work(root, "foo")
    got, problems = decide(root)
    assert got["work/foo"][0] == "keep"
    assert any("names no slurm_job_id" in p for p in problems)


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


# --- the lock, the report and the delete pass -------------------------------------------

def run_main(root, *args, queued=frozenset()):
    q = None if queued is None else set(queued)
    return cl.main(["--root", str(root), *args], queue_reader=lambda: (q, "test queue"),
                   clock=lambda: NOW)


def test_lock_reports_busy_while_held(tmp_path):
    lock = cl.CacheLock(str(tmp_path))
    assert lock.acquire(0) == "taken"
    other = cl.CacheLock(str(tmp_path))
    assert other.acquire(0) == "busy"
    lock.release()
    assert other.acquire(0) == "taken"
    other.release()


def test_report_mode_deletes_nothing(root, capsys):
    add_run(root, R, finished="failed", finished_days=40)
    work = add_work(root, "foo")
    add_work(root, "legacy")
    fq = add_fastq(root, FQ, last_used_days=1, accs=["SRR1"])
    assert run_main(root) == 0
    out = capsys.readouterr().out
    assert "WILL DELETE (1)" in out and "work/foo" in out
    assert "UNLABELLED" in out and "work/legacy" in out
    assert "Download lock: taken and released" in out
    assert "Nessie runs in progress: none" in out
    assert "Would free" in out
    assert work.exists() and fq.exists() and not (root / "cleanup_logs").exists()


def test_delete_removes_only_what_the_report_lists_and_logs_it(root, capsys):
    add_run(root, R, finished="failed", finished_days=40)
    old = add_work(root, "foo")
    add_run(root, "bar_261001_120000_0", finished="failed", finished_days=3)
    recent = add_work(root, "bar")
    legacy = add_work(root, "legacy")
    fq = add_fastq(root, FQ, last_used_days=31, accs=["SRR1"], md5=True)
    assert run_main(root, "--delete") == 0
    assert not old.exists() and recent.exists() and legacy.exists()
    assert not fq.exists() and not (fq.parent / "md5" / (FQ + ".md5")).exists()
    assert (root / "runs" / R).exists()
    (log,) = list((root / "cleanup_logs").iterdir())
    text = log.read_text()
    assert "DELETED" in text and "work/foo" in text and "fastq_cache/fastq/" + FQ in text


def test_delete_refuses_when_the_queue_cannot_be_read(root, capsys):
    add_run(root, R, finished="failed", finished_days=40)
    work = add_work(root, "foo")
    assert run_main(root, "--delete", queued=None) == 1
    assert work.exists()
    assert "Not deleting anything" in capsys.readouterr().out


def test_delete_waits_while_a_run_is_in_progress(root, capsys):
    add_run(root, R, finished="failed", finished_days=40)
    work = add_work(root, "foo")
    add_run(root, "bar_261020_120000_0", job="555")
    assert run_main(root, "--delete", queued={"555"}) == 1
    assert work.exists() and not (root / "cleanup_logs").exists()
    out = capsys.readouterr().out
    assert "Nessie runs in progress: 1" in out
    assert "Not deleting anything while Nessie runs are in progress" in out
    assert "runs/bar_261020_120000_0 is in progress" in out


def test_delete_waits_while_a_run_is_being_launched(root, capsys):
    add_run(root, R, finished="failed", finished_days=40)
    work = add_work(root, "foo")
    add_run(root, "bar_261020_120000_0", labelled=False, changed_days=0)
    assert run_main(root, "--delete") == 1
    assert work.exists()
    assert "runs/bar_261020_120000_0 is being launched" in capsys.readouterr().out


def test_delete_waits_while_a_download_is_in_use(root, capsys):
    fq = add_fastq(root, FQ, last_used_days=40, accs=["SRR1"])
    add_claim(root, "SRR2", "9")
    assert run_main(root, "--delete", queued={"9"}) == 1
    assert fq.exists()
    assert "job 9 is using download SRR2" in capsys.readouterr().out


def test_runs_in_progress_ignores_finished_and_old_unlabelled_runs(root):
    add_run(root, R, finished="succeeded", finished_days=1)
    add_run(root, "legacy_250101_120000_0", labelled=False, changed_days=200)
    add_claim(root, "SRR1", "4821")                       # stale: job not queued
    assert cl.runs_in_progress(str(root), set(), NOW) == []


def test_delete_rechecks_each_item_first(root):
    fq = add_fastq(root, FQ, last_used_days=31, accs=["SRR1"])
    items, _ = cl.build_report(str(root), {"77"}, NOW)
    add_claim(root, "SRR1", "77")            # a run claims it after the report was built
    log, freed, failures, stopped = cl.delete_pass(
        str(root), items, lambda: ({"77"}, "q"), clock=lambda: NOW)
    assert fq.exists() and freed == 0 and failures == 0
    assert any(line.startswith("STOPPED") or line.startswith("SKIPPED") for line in log)


def plan(root, queued=frozenset()):
    items, _ = cl.build_report(str(root), set(queued), NOW)
    cl.add_sizes(items)
    return items


def test_a_new_in_progress_run_of_the_same_name_stops_the_pass(root):
    add_run(root, R, finished="failed", finished_days=40)
    work = add_work(root, "foo")
    items = plan(root)
    add_run(root, "foo_261020_120000_0", job="555")          # same work name, now queued
    log, freed, failures, stopped = cl.delete_pass(
        str(root), items, lambda: ({"555"}, "q"), clock=lambda: NOW)
    assert stopped and freed == 0 and work.exists()
    assert log == ["STOPPED  runs/foo_261020_120000_0 is in progress; nothing more deleted"]


def test_a_new_finished_run_of_the_same_name_skips_the_folder(root):
    add_run(root, R, finished="failed", finished_days=40)
    work = add_work(root, "foo")
    items = plan(root)
    add_run(root, "foo_261020_120000_0", finished="failed", finished_days=1)
    log, freed, failures, stopped = cl.delete_pass(
        str(root), items, lambda: (set(), "q"), clock=lambda: NOW)
    assert not stopped and failures == 0 and freed == 0 and work.exists()
    assert len(log) == 1 and log[0].startswith("SKIPPED  work/foo  no longer qualifies on re-check (")
    assert "run failed 1 days ago" in log[0]


def test_a_run_starting_mid_pass_stops_the_pass(root, monkeypatch):
    monkeypatch.setattr(cl, "QUEUE_REFRESH_S", 0)
    add_run(root, R, finished="failed", finished_days=40)
    add_run(root, "bar_261001_120000_0", finished="failed", finished_days=40)
    foo, bar = add_work(root, "foo"), add_work(root, "bar")
    items = plan(root)
    calls = []

    def reader():
        calls.append(1)
        if len(calls) == 1:
            return set(), "q"
        if len(calls) == 2:
            add_run(root, "baz_261020_120000_0", job="555")
        return {"555"}, "q"

    log, freed, failures, stopped = cl.delete_pass(str(root), items, reader, clock=lambda: NOW)
    assert stopped and failures == 0
    assert not bar.exists() and foo.exists()                 # sorted: bar first, then stopped
    assert log[0].startswith("DELETED") and "work/bar" in log[0]
    assert log[1] == "STOPPED  runs/baz_261020_120000_0 is in progress; nothing more deleted"
    assert len(log) == 2


def test_delete_pass_refuses_when_its_first_queue_read_fails(root):
    add_run(root, R, finished="failed", finished_days=40)
    work = add_work(root, "foo")
    items = plan(root)
    log, freed, failures, stopped = cl.delete_pass(
        str(root), items, lambda: (None, "bad"), clock=lambda: NOW)
    assert stopped and work.exists()
    assert log == ["STOPPED  job queue could not be read; nothing deleted"]


def test_the_queue_going_unreadable_mid_pass_stops_the_pass(root, monkeypatch):
    monkeypatch.setattr(cl, "QUEUE_REFRESH_S", 0)
    add_run(root, R, finished="failed", finished_days=40)
    add_run(root, "bar_261001_120000_0", finished="failed", finished_days=40)
    foo, bar = add_work(root, "foo"), add_work(root, "bar")
    items = plan(root)
    answers = iter([(set(), "q"), (None, "bad")])
    log, freed, failures, stopped = cl.delete_pass(
        str(root), items, lambda: next(answers), clock=lambda: NOW)
    assert stopped and not bar.exists() and foo.exists()
    assert log[-1] == "STOPPED  job queue could not be read; nothing more deleted"


def test_a_busy_download_lock_skips_the_downloads_and_fails_the_run(root, monkeypatch, capsys):
    monkeypatch.setattr(cl, "LOCK_TIMEOUT_S", 0)
    fq = add_fastq(root, FQ, last_used_days=31, accs=["SRR1"])
    holder = cl.CacheLock(str(root / "fastq_cache"))
    assert holder.acquire(0) == "taken"
    try:
        assert run_main(root, "--delete") == 1
    finally:
        holder.release()
    assert fq.exists()
    (log,) = list((root / "cleanup_logs").iterdir())
    assert "SKIPPED  fastq_cache/fastq/" + FQ + "  download lock busy" in log.read_text()


def test_a_failed_removal_is_logged_and_the_rest_still_go(root, monkeypatch):
    add_run(root, R, finished="failed", finished_days=40)
    add_run(root, "bar_261001_120000_0", finished="failed", finished_days=40)
    foo, bar = add_work(root, "foo"), add_work(root, "bar")
    real = cl.remove_path

    def flaky(path, parent):
        if os.path.basename(path) == "bar":
            raise OSError("disk says no")
        return real(path, parent)

    monkeypatch.setattr(cl, "remove_path", flaky)
    assert run_main(root, "--delete") == 1
    assert bar.exists() and not foo.exists()
    (log,) = list((root / "cleanup_logs").iterdir())
    text = log.read_text()
    assert "FAILED   work/bar  disk says no" in text and "DELETED" in text and "work/foo" in text


def test_a_failed_md5_removal_still_logs_the_fastq_as_deleted(root, monkeypatch):
    fq = add_fastq(root, FQ, last_used_days=31, accs=["SRR1"], md5=True)
    real = cl.remove_path

    def flaky(path, parent):
        if path.endswith(".md5"):
            raise OSError("md5 stuck")
        return real(path, parent)

    monkeypatch.setattr(cl, "remove_path", flaky)
    items = plan(root)
    log, freed, failures, stopped = cl.delete_pass(
        str(root), items, lambda: (set(), "q"), clock=lambda: NOW)
    assert not fq.exists() and freed == 100 and failures == 1
    assert log[0].startswith("DELETED") and "fastq_cache/fastq/" + FQ in log[0]
    assert log[1].startswith("FAILED   fastq_cache/fastq/md5/" + FQ + ".md5  md5 stuck")


def test_the_log_is_written_as_the_pass_goes(root, monkeypatch):
    add_run(root, R, finished="failed", finished_days=40)
    add_run(root, "bar_261001_120000_0", finished="failed", finished_days=40)
    add_work(root, "foo"), add_work(root, "bar")
    real = cl.remove_path
    seen = []

    def interrupted(path, parent):
        if seen:
            raise KeyboardInterrupt
        seen.append(path)
        return real(path, parent)

    monkeypatch.setattr(cl, "remove_path", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_main(root, "--delete")
    (log,) = list((root / "cleanup_logs").iterdir())
    text = log.read_text()
    assert "DELETE PASS" in text and "Nessie cleanup report" in text
    assert "DELETED" in text and "work/bar" in text


def test_a_second_log_in_the_same_second_gets_a_suffix(root):
    first, path1 = cl.open_log(str(root), NOW)
    second, path2 = cl.open_log(str(root), NOW)
    first.close(), second.close()
    assert path1 != path2 and path2.endswith("_2.txt")


def test_delete_does_nothing_if_the_log_cannot_be_created(root, monkeypatch, capsys):
    add_run(root, R, finished="failed", finished_days=40)
    work = add_work(root, "foo")

    def broken(root_, now):
        raise PermissionError("read-only")

    monkeypatch.setattr(cl, "open_log", broken)
    assert run_main(root, "--delete") == 1
    assert work.exists() and "read-only" in capsys.readouterr().out


def test_delete_removes_downloader_scratch(root):
    scratch = root / "fastq_cache" / "work" / "ab"
    scratch.mkdir(parents=True)
    (scratch / "x.bin").write_bytes(b"x" * 10)
    assert run_main(root, "--delete") == 0
    assert not (root / "fastq_cache" / "work").exists()
    (log,) = list((root / "cleanup_logs").iterdir())
    assert "DELETED" in log.read_text() and "fastq_cache/work" in log.read_text()


def test_read_queue_discards_squeue_stderr(monkeypatch):
    seen = {}

    def fake(cmd, **kwargs):
        seen.update(kwargs)
        return "12\n34\n"

    monkeypatch.setattr(cl.subprocess, "check_output", fake)
    jobs, _note = cl.read_queue()
    assert jobs == {"12", "34"} and seen["stderr"] is subprocess.DEVNULL


def test_the_report_says_what_the_free_space_waits_for(root, capsys):
    add_run(root, R, finished="failed", finished_days=40)
    add_work(root, "foo")
    run_main(root)
    assert "with --delete." in capsys.readouterr().out
    add_run(root, "bar_261020_120000_0", job="555")
    run_main(root, queued={"555"})
    assert "with --delete, once no Nessie run is in progress." in capsys.readouterr().out
    run_main(root, queued=None)
    assert "with --delete, once the job queue can be read." in capsys.readouterr().out


def test_delete_never_follows_a_symlink(root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "keep.txt").write_text("precious")
    add_run(root, R, finished="failed", finished_days=40)
    work = add_work(root, "foo")
    (work / "link").symlink_to(outside, target_is_directory=True)
    add_run(root, "bar_261001_120000_0", finished="failed", finished_days=40)
    (root / "work" / "bar").symlink_to(outside, target_is_directory=True)
    assert run_main(root, "--delete") == 0
    assert not os.path.lexists(str(work)) and not os.path.lexists(str(root / "work" / "bar"))
    assert (outside / "keep.txt").read_text() == "precious"


def test_remove_path_refuses_anything_outside_its_folder(root):
    (root / "runs" / "x").mkdir()
    with pytest.raises(ValueError):
        cl.remove_path(str(root / "runs" / "x"), str(root / "work"))
    assert (root / "runs" / "x").exists()


def test_a_missing_runs_folder_is_an_error(tmp_path):
    assert cl.main(["--root", str(tmp_path)], queue_reader=lambda: (set(), "q"),
                   clock=lambda: NOW) == 2
