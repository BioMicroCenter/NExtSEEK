#!/usr/bin/env python3
"""Weekly cleanup of Nessie's downloads and work folders on Luria.

Staged verbatim to <LURIA_WORKING_PATH>/bin/nessie_cleanup.py by chat_nextseek.luria.submitter
on every submit, so the copy on Luria is always current. An admin runs it by hand, as the
cluster account Nessie submits with (no other account can delete those files):

    python3 nessie_cleanup.py            # report only
    python3 nessie_cleanup.py --delete   # delete what the report lists under WILL DELETE

It decides from the plain-text labels Nessie writes (runs/*/nessie_status/ and
fastq_cache/nessie_status/) and from Luria's job queue. It deletes only inside work/ and
fastq_cache/, never runs/, and never anything unlabelled. The rules, the labels and the weekly
procedure are in chat_nextseek/luria/README.md in the NExtSEEK repository.

Standard library only, and Python 3.6 compatible: the login node's python3 may be old.
"""
import argparse
import calendar
import errno
import fcntl
import math
import os
import re
import shutil
import subprocess
import sys
import time

SAVED_WAIT_DAYS = 7
IDLE_WAIT_DAYS = 30
LAUNCH_GRACE_DAYS = 2
LOCK_TIMEOUT_S = 60
DAY = 86400.0

STATUS_DIR = "nessie_status"
LOCK_NAME = "cleanup.lock"
TIME_FMT = "%Y-%m-%dT%H:%M:%SZ"
_ACC_RE = re.compile(r"^[A-Za-z0-9]+$")
# submitter names a run folder <work name>_<yymmdd>_<hhmmss>_<entry index>
_RUN_SUFFIX_RE = re.compile(r"_\d{6}_\d{6}_\d+$")


class LabelError(Exception):
    """A label file exists but cannot be read or parsed."""


# --- labels -----------------------------------------------------------------------------

def parse_time(text):
    return float(calendar.timegm(time.strptime(text.strip(), TIME_FMT)))


def format_time(epoch):
    return time.strftime(TIME_FMT, time.gmtime(epoch))


def read_label(path):
    """The `key: value` label at `path` as a dict, or None when there is no such file."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise LabelError("could not read {}: {}".format(path, exc))
    label = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise LabelError("{} is not 'key: value' lines: {!r}".format(path, line))
        label[key.strip()] = value.strip()
    return label


def label_time(label, key, path):
    try:
        return parse_time(label[key])
    except (KeyError, ValueError):
        raise LabelError("{} has no valid {}".format(path, key))


# --- small helpers ----------------------------------------------------------------------

def list_children(folder):
    """Sorted names directly inside `folder`; [] when it does not exist."""
    try:
        return sorted(os.listdir(folder))
    except (FileNotFoundError, NotADirectoryError):
        return []


def same_path(a, b):
    """True when `a` and `b` name the same entry. Their parent folders are compared resolved,
    so a symlinked working path still matches; the entry itself is never followed."""
    a, b = os.path.normpath(a), os.path.normpath(b)
    return (os.path.basename(a) == os.path.basename(b)
            and os.path.realpath(os.path.dirname(a)) == os.path.realpath(os.path.dirname(b)))


def days_ago(epoch, now):
    return int(max(0.0, now - epoch) // DAY)


class Item(object):
    """One thing the cleanup could delete, and the decision about it."""

    def __init__(self, kind, path, parent, decision, reason, days_left=None):
        self.kind = kind            # "work", "fastq" or "fetchngs_work"
        self.path = path
        self.parent = parent        # the only folder this item may be deleted from
        self.decision = decision    # "delete", "keep" or "unlabelled"
        self.reason = reason
        self.days_left = days_left
        self.companions = []        # (path, parent) pairs deleted with it: a FASTQ's md5
        self.size = 0


def _due_item(kind, path, parent, due, reason, now):
    if now >= due:
        return Item(kind, path, parent, "delete", reason)
    return Item(kind, path, parent, "keep", reason, days_left=int(math.ceil((due - now) / DAY)))


# --- runs and their work folders --------------------------------------------------------

class Run(object):
    """One runs/<name>_<yymmdd>_<hhmmss>_<n>/ folder and what its labels say."""

    def __init__(self, run_dir):
        self.run_dir = run_dir
        name = os.path.basename(run_dir)
        self.work_name = _RUN_SUFFIX_RE.sub("", name) if _RUN_SUFFIX_RE.search(name) else None
        self.changed = os.lstat(run_dir).st_mtime
        self.work_dir = None
        # unlabelled, saved, finished, failed, ended, in_progress or unreadable
        self.state = "unlabelled"
        self.since = None
        self.wait_days = None
        self.problem = None

    @property
    def due(self):
        return self.since + self.wait_days * DAY

    @property
    def shown(self):
        return "runs/" + os.path.basename(self.run_dir)


def last_activity(run_dir, launched):
    """When a run that ended without finished.txt last did anything: its newest SLURM .out
    log, else its launched_at, else its folder's mtime."""
    times = []
    for name in list_children(run_dir):
        path = os.path.join(run_dir, name)
        if name.endswith(".out") and os.path.isfile(path) and not os.path.islink(path):
            times.append(os.lstat(path).st_mtime)
    if times:
        return max(times)
    try:
        return parse_time(launched.get("launched_at", ""))
    except ValueError:
        return os.lstat(run_dir).st_mtime


def read_run(run_dir, queued):
    """The Run for `run_dir`. `queued` is the set of queued job ids, or None when unknown,
    in which case a run with no finished.txt counts as in progress."""
    run = Run(run_dir)
    status = os.path.join(run_dir, STATUS_DIR)
    launched_path = os.path.join(status, "launched.txt")
    saved_path = os.path.join(status, "saved.txt")
    finished_path = os.path.join(status, "finished.txt")
    try:
        launched = read_label(launched_path)
        if launched is None:
            return run
        run.work_dir = launched.get("work_dir") or None
        if not run.work_dir:
            raise LabelError("{} names no work_dir".format(launched_path))
        saved = read_label(saved_path)
        finished = read_label(finished_path)
        if saved is not None:
            run.state, run.wait_days = "saved", SAVED_WAIT_DAYS
            run.since = label_time(saved, "saved_at", saved_path)
        elif finished is not None:
            result = finished.get("result")
            if result not in ("succeeded", "failed"):
                raise LabelError("{} has no valid result".format(finished_path))
            run.state = "finished" if result == "succeeded" else "failed"
            run.since = label_time(finished, "finished_at", finished_path)
            run.wait_days = IDLE_WAIT_DAYS
        elif queued is None or launched.get("slurm_job_id", "") in queued:
            run.state = "in_progress"
        else:
            run.state, run.wait_days = "ended", IDLE_WAIT_DAYS
            run.since = last_activity(run_dir, launched)
    except LabelError as exc:
        run.state, run.problem = "unreadable", str(exc)
    return run


_DONE_WORDS = {
    "saved": "results saved {days} days ago",
    "finished": "run finished {days} days ago, results not saved",
    "failed": "run failed {days} days ago",
    "ended": "run ended {days} days ago without a finished label",
}


def work_item(path, runs, now):
    """The decision for one work/<name>/ folder, from every run that used it."""
    parent = os.path.dirname(path)
    name = os.path.basename(path)
    related = [r for r in runs
               if (r.work_dir and same_path(r.work_dir, path)) or r.work_name == name]
    for run in related:
        if run.state == "unreadable":
            return Item("work", path, parent, "keep",
                        "a label of {} could not be read".format(run.shown))
        if run.state == "unlabelled" and now - run.changed < LAUNCH_GRACE_DAYS * DAY:
            return Item("work", path, parent, "keep",
                        "{} is being launched (no labels yet)".format(run.shown))
        if run.state == "in_progress":
            return Item("work", path, parent, "keep", "{} is in progress".format(run.shown))
    done = [r for r in related if r.state in _DONE_WORDS]
    if not done:
        return Item("work", path, parent, "unlabelled", "no Nessie label names this folder")
    last = max(done, key=lambda r: r.due)
    reason = "{} ({})".format(_DONE_WORDS[last.state].format(days=days_ago(last.since, now)),
                              last.shown)
    return _due_item("work", path, parent, last.due, reason, now)


# --- downloads --------------------------------------------------------------------------

def fastq_accessions(filename):
    """The accessions a fetchngs FASTQ belongs to: the whole id tokens of its name, the rule
    fetchngs_helpers.fill matches by. None when it is not a FASTQ this cleanup manages."""
    if not filename.endswith(".fastq.gz"):
        return None
    tokens = filename[:-len(".fastq.gz")].split("_")
    if len(tokens) > 1 and tokens[-1] in ("1", "2"):
        tokens.pop()
    if not tokens or not all(_ACC_RE.match(t) for t in tokens):
        return None
    return tokens


def read_claims(cache):
    """{accession: [ids of the jobs holding it]} from fastq_cache/nessie_status/in_use/."""
    in_use = os.path.join(cache, STATUS_DIR, "in_use")
    claims = {}
    for acc in list_children(in_use):
        jobs = list_children(os.path.join(in_use, acc))
        if jobs:
            claims[acc] = jobs
    return claims


def is_live(job, queued):
    """A claim counts unless its job is known to be gone; an unknown queue honours them all."""
    return queued is None or job in queued


def _fastq_by_last_use(cache, path, parent, accs, now, problems):
    times = []
    for acc in accs:
        label_path = os.path.join(cache, STATUS_DIR, "last_used", acc + ".txt")
        try:
            label = read_label(label_path)
            if label is not None:
                times.append(label_time(label, "last_used_at", label_path))
        except LabelError as exc:
            problems.append(str(exc))
            return Item("fastq", path, parent, "keep", "a last_used label could not be read")
    if not times:
        return Item("fastq", path, parent, "unlabelled", "no labelled run has used it")
    last = max(times)
    return _due_item("fastq", path, parent, last + IDLE_WAIT_DAYS * DAY,
                     "last used {} days ago".format(days_ago(last, now)), now)


def fastq_items(cache, claims, queued, now, problems):
    fastq = os.path.join(cache, "fastq")
    items = []
    for name in list_children(fastq):
        path = os.path.join(fastq, name)
        accs = fastq_accessions(name)
        if accs is None or os.path.isdir(path):
            continue
        holders = ["job {} ({})".format(job, acc) for acc in accs
                   for job in claims.get(acc, []) if is_live(job, queued)]
        if holders:
            item = Item("fastq", path, fastq, "keep", "in use by " + ", ".join(holders))
        else:
            item = _fastq_by_last_use(cache, path, fastq, accs, now, problems)
        md5 = os.path.join(fastq, "md5", name + ".md5")
        if os.path.lexists(md5):
            item.companions.append((md5, os.path.join(fastq, "md5")))
        items.append(item)
    return items


def fetchngs_work_item(cache, claims, queued):
    """fetchngs' own Nextflow work folder: scratch, needed only while a download runs."""
    work = os.path.join(cache, "work")
    if not list_children(work):
        return None
    if any(is_live(job, queued) for jobs in claims.values() for job in jobs):
        return Item("fetchngs_work", work, cache, "keep", "a download may be in progress")
    return Item("fetchngs_work", work, cache, "delete", "downloader scratch; no download in progress")


def stale_claim_notes(claims, queued):
    if queued is None:
        return []
    return ["stale 'in use' entry: {} held by job {}, which is no longer queued (ignored)"
            .format(acc, job) for acc, jobs in sorted(claims.items()) for job in jobs
            if job not in queued]


def read_runs(root, queued):
    """A Run for every folder in runs/ (symlinks skipped)."""
    runs_root = os.path.join(root, "runs")
    return [read_run(os.path.join(runs_root, name), queued) for name in list_children(runs_root)
            if os.path.isdir(os.path.join(runs_root, name))
            and not os.path.islink(os.path.join(runs_root, name))]


def build_report(root, queued, now):
    """(items, problems) for everything under work/ and fastq_cache/. Changes nothing."""
    runs = read_runs(root, queued)
    problems = [run.problem for run in runs if run.problem]
    work_root = os.path.join(root, "work")
    items = [work_item(os.path.join(work_root, name), runs, now)
             for name in list_children(work_root)]
    cache = os.path.join(root, "fastq_cache")
    claims = read_claims(cache)
    items.extend(fastq_items(cache, claims, queued, now, problems))
    scratch = fetchngs_work_item(cache, claims, queued)
    if scratch is not None:
        items.append(scratch)
    problems.extend(stale_claim_notes(claims, queued))
    return items, problems
