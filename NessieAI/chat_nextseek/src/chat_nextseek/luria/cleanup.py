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
QUEUE_REFRESH_S = 30
DAY = 86400.0

STATUS_DIR = "nessie_status"
LOCK_NAME = "cleanup.lock"
TIME_FMT = "%Y-%m-%dT%H:%M:%SZ"
_ACC_RE = re.compile(r"[A-Za-z0-9]+")
# submitter names a run folder <work name>_<yymmdd>_<hhmmss>_<entry index>
_RUN_SUFFIX_RE = re.compile(r"_\d{6}_\d{6}_\d+$")
# a folder renamed away just before it is removed; one left behind is an interrupted delete.
# Recognised by its whole name only, so a run whose work folder is called .nessie-deleting-x
# is still an ordinary work folder.
TOMBSTONE_PREFIX = ".nessie-deleting-"
_LEFTOVER_RE = re.compile(r"^\.nessie-deleting-.+-\d{14}$")
LEFTOVER_REASON = "left over from an interrupted cleanup"


def is_leftover(name):
    return _LEFTOVER_RE.fullmatch(name) is not None


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
        # unlabelled, launch_label_missing, saved, finished, failed, ended, in_progress or
        # unreadable
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


def newest_activity(run_dir, fallback):
    """When a run with no launched.txt last did anything: the newest mtime of its folder, of
    the SLURM .out/.err logs directly in it and of its .nextflow.log. A run whose label is
    missing may still be running, and writing these. When nothing can be read (the folder
    has gone since the report began), `fallback` is returned."""
    times = []
    for name in [""] + list_children(run_dir):
        if name and not (name.endswith(".out") or name.endswith(".err")
                         or name == ".nextflow.log"):
            continue
        try:
            times.append(os.lstat(os.path.join(run_dir, name)).st_mtime)
        except OSError:
            pass
    if not times:
        return fallback
    return max(times)


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
            run.changed = newest_activity(run_dir, run.changed)
            # The submitter makes nessie_status/ before sbatch: a folder that has it but no
            # launched.txt is a run whose label write failed or has not happened yet.
            if os.path.isdir(status) and not os.path.islink(status):
                run.state = "launch_label_missing"
            return run
        run.work_dir = launched.get("work_dir") or None
        if not run.work_dir:
            raise LabelError("{} names no work_dir".format(launched_path))
        if not launched.get("slurm_job_id"):
            raise LabelError("{} names no slurm_job_id".format(launched_path))
        if queued is not None and launched["slurm_job_id"] in queued:
            run.state = "in_progress"       # also a requeued job that left a finished.txt
            return run
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
        elif queued is None:
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
        recent = now - run.changed < LAUNCH_GRACE_DAYS * DAY
        if run.state in ("unlabelled", "launch_label_missing") and recent:
            return Item("work", path, parent, "keep",
                        "{} is being launched (no labels yet)".format(run.shown))
        if run.state == "launch_label_missing":
            return Item("work", path, parent, "keep",
                        "{} has no launched.txt; check it by hand".format(run.shown))
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
    if not tokens or not all(_ACC_RE.fullmatch(t) for t in tokens):
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


def fastq_item(cache, name, claims, queued, now, problems):
    """The decision for the one FASTQ `name` in fastq_cache/fastq/, or None when the cache
    holds no such file this cleanup manages."""
    fastq = os.path.join(cache, "fastq")
    path = os.path.join(fastq, name)
    accs = fastq_accessions(name)
    if accs is None or os.path.isdir(path):
        return None
    holders = ["job {} ({})".format(job, acc) for acc in accs
               for job in claims.get(acc, []) if is_live(job, queued)]
    if holders:
        item = Item("fastq", path, fastq, "keep", "in use by " + ", ".join(holders))
    else:
        item = _fastq_by_last_use(cache, path, fastq, accs, now, problems)
    md5 = os.path.join(fastq, "md5", name + ".md5")
    if os.path.lexists(md5):
        item.companions.append((md5, os.path.join(fastq, "md5")))
    return item


def fastq_items(cache, claims, queued, now, problems):
    items = []
    for name in list_children(os.path.join(cache, "fastq")):
        item = fastq_item(cache, name, claims, queued, now, problems)
        if item is not None:
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
    """A Run for every folder in runs/ (symlinks skipped; a folder that disappears while the
    runs are being read is skipped)."""
    runs_root = os.path.join(root, "runs")
    runs = []
    for name in list_children(runs_root):
        run_dir = os.path.join(runs_root, name)
        if not os.path.isdir(run_dir) or os.path.islink(run_dir):
            continue
        try:
            runs.append(read_run(run_dir, queued))
        except FileNotFoundError:
            pass
    return runs


def build_report(root, queued, now):
    """(items, problems) for everything under work/ and fastq_cache/. Changes nothing."""
    runs = read_runs(root, queued)
    problems = [run.problem for run in runs if run.problem]
    problems.extend("{}/{} has no launched.txt; its work folder is kept until someone checks it"
                    .format(run.shown, STATUS_DIR) for run in runs
                    if run.state == "launch_label_missing"
                    and now - run.changed >= LAUNCH_GRACE_DAYS * DAY)
    work_root = os.path.join(root, "work")
    items = []
    for name in list_children(work_root):
        path = os.path.join(work_root, name)
        if is_leftover(name):
            items.append(Item("work", path, work_root, "delete", LEFTOVER_REASON))
        else:
            items.append(work_item(path, runs, now))
    cache = os.path.join(root, "fastq_cache")
    for name in list_children(cache):
        if is_leftover(name):
            items.append(Item("fetchngs_work", os.path.join(cache, name), cache, "delete",
                              LEFTOVER_REASON))
    claims = read_claims(cache)
    items.extend(fastq_items(cache, claims, queued, now, problems))
    scratch = fetchngs_work_item(cache, claims, queued)
    if scratch is not None:
        items.append(scratch)
    problems.extend(stale_claim_notes(claims, queued))
    return items, problems


# --- sizes, the lock, deleting ----------------------------------------------------------

def tree_size(path):
    """Bytes under `path`; symlinks are counted as links and never followed."""
    try:
        if os.path.islink(path) or not os.path.isdir(path):
            return os.lstat(path).st_size
    except OSError:
        return 0
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def add_sizes(items):
    for item in items:
        item.size = tree_size(item.path) + sum(tree_size(p) for p, _ in item.companions)


class CacheLock(object):
    """fastq_cache/nessie_status/cleanup.lock, the lock fetchngs_helpers.claim also takes."""

    def __init__(self, cache):
        self.path = os.path.join(cache, STATUS_DIR, LOCK_NAME)
        self._fh = None

    def acquire(self, timeout_s):
        """"taken", or "busy" when another holder kept it for `timeout_s` seconds."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fh = open(self.path, "a")
        deadline = time.time() + timeout_s
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fh = fh
                return "taken"
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    fh.close()
                    raise
                if time.time() >= deadline:
                    fh.close()
                    return "busy"
                time.sleep(1)

    def release(self):
        if self._fh is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


def probe_lock(cache):
    """Whether the download lock works on this filesystem, for the report."""
    if not os.path.isdir(cache):
        return "not tested (no fastq_cache/ yet)"
    lock = CacheLock(cache)
    try:
        state = lock.acquire(0)
    except OSError as exc:
        return "FAILED ({}); --delete will not delete downloads".format(exc)
    lock.release()
    if state == "taken":
        return "taken and released on this node"
    return "busy (a run is starting; try again in a minute)"


def check_inside(path, parent):
    """Raise ValueError unless `path` sits directly inside `parent`."""
    name = os.path.basename(os.path.normpath(path))
    if name in ("", ".", "..") or not same_path(os.path.join(parent, name), path):
        raise ValueError("refusing to delete {}: not directly inside {}".format(path, parent))


def remove_path(path, parent):
    """Delete `path`, which must sit directly inside `parent`. A symlink is removed as a link,
    never followed, and shutil.rmtree does not follow the links inside a folder either."""
    check_inside(path, parent)
    if os.path.islink(path) or not os.path.isdir(path):
        os.unlink(path)
    else:
        shutil.rmtree(path)


def runs_in_progress(root, queued, now):
    """What makes --delete wait with the downloads: Nessie runs in progress or being launched,
    and live download claims. Work folders are not held back by this (each one is re-checked
    against its own runs); the admin runs --delete again once this is empty."""
    busy = []
    for run in read_runs(root, queued):
        if run.state == "in_progress":
            busy.append("{} is in progress".format(run.shown))
        elif (run.state == "unlabelled" and run.work_name
              and now - run.changed < LAUNCH_GRACE_DAYS * DAY):
            busy.append("{} is being launched".format(run.shown))
        elif (run.state == "launch_label_missing"
              and now - run.changed < LAUNCH_GRACE_DAYS * DAY):
            # Only within the grace, so a failed label write cannot block every pass forever.
            busy.append("{} is being launched".format(run.shown))
    for acc, jobs in sorted(read_claims(os.path.join(root, "fastq_cache")).items()):
        busy.extend("job {} is using download {}".format(job, acc)
                    for job in jobs if is_live(job, queued))
    return busy


def recheck(root, item, queued, now):
    """`item`'s decision from fresh labels, claims and queue: the Item as it stands now, or
    None when it is gone or is no longer something this cleanup manages."""
    if not os.path.lexists(item.path):
        return None
    if is_leftover(os.path.basename(item.path)):
        return Item(item.kind, item.path, item.parent, "delete", LEFTOVER_REASON)
    cache = os.path.join(root, "fastq_cache")
    if item.kind == "work":
        return work_item(item.path, read_runs(root, queued), now)
    if item.kind == "fastq":
        return fastq_item(cache, os.path.basename(item.path), read_claims(cache), queued,
                          now, [])
    if item.kind == "fetchngs_work":
        return fetchngs_work_item(cache, read_claims(cache), queued)
    return None


def tombstone_for(path, now):
    """Where a work folder is renamed to before it is removed: the same parent, so the rename
    is atomic and a same-name run that starts mid-delete finds no folder and makes a fresh one."""
    return os.path.join(os.path.dirname(path), "{}{}-{}".format(
        TOMBSTONE_PREFIX, os.path.basename(path), time.strftime("%Y%m%d%H%M%S", time.gmtime(now))))


def _delete_one(root, item, fresh, out, now):
    """Remove `item` (decided delete on re-check as `fresh`). Returns (bytes freed, failures).
    A work folder is first renamed to a tombstone and the tombstone is removed, so nothing
    that starts using the folder's name meanwhile can have files deleted under it. A
    tombstone left by an interrupted pass is removed as it is."""
    shown = os.path.relpath(item.path, root)
    try:
        target = item.path
        if (item.kind in ("work", "fetchngs_work")
                and not is_leftover(os.path.basename(item.path))):
            check_inside(item.path, item.parent)
            target = tombstone_for(item.path, now)
            os.rename(item.path, target)    # a symlink is renamed itself, never followed
        remove_path(target, item.parent)
    except (OSError, ValueError) as exc:
        out("FAILED   {}  {}".format(shown, exc))
        return 0, 1
    freed = item.size
    failed = []
    for path, parent in fresh.companions:
        try:
            if os.path.lexists(path):
                remove_path(path, parent)
        except (OSError, ValueError) as exc:
            freed -= tree_size(path)
            failed.append((path, exc))
    out("DELETED  {:>9}  {}  {}".format(human_size(max(0, freed)), shown, fresh.reason))
    for path, exc in failed:
        out("FAILED   {}  {}".format(os.path.relpath(path, root), exc))
    return max(0, freed), len(failed)


DEFERRED_WORDS = "downloads wait until no Nessie run is in progress"


def _runs_word(n):
    return "{} Nessie run{} in progress".format(n, "" if n == 1 else "s")


def delete_pass(root, planned, queue_reader, clock=time.time, emit=None):
    """Delete what `planned` marks delete. Before each item the pass re-reads the job queue
    (at most every QUEUE_REFRESH_S seconds) and recomputes that item's decision from fresh
    labels. Work folders are deleted while other runs go (the re-check keeps the folder of
    any run that is in progress or being launched). Downloads are deleted only while holding
    the download lock and only when no Nessie run is in progress, checked before each one;
    otherwise every download still to do is logged as SKIPPED and the pass ends.
    `emit(line)` is called as each line is logged.
    Returns (log lines, bytes freed, failures, not_done): `not_done` is "" when everything
    planned was deleted or skipped on re-check, else a sentence saying what was not attempted."""
    log = []

    def out(line):
        log.append(line)
        if emit is not None:
            emit(line)

    queued, _ = queue_reader()
    if queued is None:
        out("STOPPED  job queue could not be read; nothing deleted")
        return log, 0, 0, "Nothing deleted: the job queue could not be read."
    state = {"queued": queued, "read_at": clock(), "new": True}

    def queue_problem():
        """Why the pass must stop before the next item, or None to go ahead."""
        if state["new"]:
            state["new"] = False            # the queue was read just now
        elif clock() - state["read_at"] >= QUEUE_REFRESH_S:
            fresh_queue, _note = queue_reader()
            if fresh_queue is None:
                return "job queue could not be read"
            state["queued"], state["read_at"] = fresh_queue, clock()
        return None

    freed = failures = 0

    def go(items, downloads):
        """Process `items`; "" when all were handled, else what was not attempted."""
        nonlocal freed, failures
        for index, item in enumerate(items):
            reason = queue_problem()
            if reason is not None:
                out("STOPPED  {}; nothing more deleted".format(reason))
                return "Stopped: the {}.".format(reason)
            if downloads:
                busy = runs_in_progress(root, state["queued"], clock())
                if busy:
                    for rest in items[index:]:
                        out("SKIPPED  {}  {}; {}".format(os.path.relpath(rest.path, root),
                                                         busy[0], DEFERRED_WORDS))
                    return ("Downloads not cleaned: {}. Run --delete again once they have "
                            "finished.".format(_runs_word(len(busy))))
            fresh = recheck(root, item, state["queued"], clock())
            if fresh is None or fresh.decision != "delete":
                why = "no longer present" if fresh is None else fresh.reason
                out("SKIPPED  {}  no longer qualifies on re-check ({})".format(
                    os.path.relpath(item.path, root), why))
                continue
            got, bad = _delete_one(root, item, fresh, out, clock())
            freed += got
            failures += bad
        return ""

    targets = [i for i in planned if i.decision == "delete"]
    work = [i for i in targets if i.kind == "work"]
    cached = [i for i in targets if i.kind != "work"]
    not_done = go(work, False)
    if not_done:
        return log, freed, failures, not_done
    if cached:
        lock = CacheLock(os.path.join(root, "fastq_cache"))
        try:
            lock_state = lock.acquire(LOCK_TIMEOUT_S)
        except OSError as exc:
            lock_state = "failed ({})".format(exc)
        if lock_state != "taken":
            for i in cached:
                out("SKIPPED  {}  download lock {}".format(os.path.relpath(i.path, root),
                                                           lock_state))
            not_done = "Downloads not cleaned: the download lock was {}. Run --delete again.".format(
                lock_state)
        else:
            try:
                state["new"] = False
                state["read_at"] = float("-inf")    # the lock wait may have been long
                not_done = go(cached, True)
            finally:
                lock.release()
    return log, freed, failures, not_done


# --- the report and the command line ----------------------------------------------------

def human_size(n):
    if n >= 1024 ** 3:
        return "{:.1f} GB".format(n / 1024.0 ** 3)
    if n >= 1024 ** 2:
        return "{:.1f} MB".format(n / 1024.0 ** 2)
    return "{} KB".format(int(math.ceil(n / 1024.0)))


_SECTIONS = (
    ("delete", "WILL DELETE"),
    ("keep", "KEEPING FOR NOW"),
    ("unlabelled", "UNLABELLED, never deleted by this script; delete by hand only if you are sure"),
)


def format_report(root, items, problems, queue_note, lock_note, busy, now, queue_ok=True):
    lines = ["Nessie cleanup report for {}".format(root),
             "Date: {}".format(format_time(now)),
             "Job queue: {}".format(queue_note),
             "Download lock: {}".format(lock_note)]
    if busy:
        lines.append("Nessie runs in progress: {} (downloads are not deleted until they have "
                     "finished; work folders of finished runs still are)".format(len(busy)))
        lines += ["  " + b for b in busy]
    else:
        lines.append("Nessie runs in progress: none")
    for decision, title in _SECTIONS:
        chosen = [i for i in items if i.decision == decision]
        lines += ["", "{} ({})".format(title, len(chosen))]
        for item in chosen:
            left = "; {} days left".format(item.days_left) if item.days_left is not None else ""
            if busy and decision == "delete" and item.kind != "work":
                left += " (after the runs in progress finish)"
            lines.append("  {:>9}  {}  {}{}".format(human_size(item.size),
                                                    os.path.relpath(item.path, root),
                                                    item.reason, left))
    if problems:
        lines += ["", "PROBLEMS ({})".format(len(problems))]
        lines += ["  " + p for p in problems]
    doomed = [i for i in items if i.decision == "delete"]
    freed = sum(i.size for i in doomed)
    if not queue_ok:
        lines += ["", "Would free {} with --delete, once the job queue can be read."
                  .format(human_size(freed))]
    elif busy:
        now_size = sum(i.size for i in doomed if i.kind == "work")
        lines += ["", "Would free {} now and {} once no Nessie run is in progress.".format(
            human_size(now_size), human_size(freed - now_size))]
    else:
        lines += ["", "Would free {} with --delete.".format(human_size(freed))]
    return "\n".join(lines)


def read_queue():
    """(ids of jobs queued or running, note). The ids are None when squeue cannot be read."""
    try:
        out = subprocess.check_output(["squeue", "-a", "-h", "-o", "%i"], stderr=subprocess.DEVNULL,
                                      universal_newlines=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "COULD NOT BE READ ({}); --delete will not delete anything".format(exc)
    jobs = set(line.strip() for line in out.splitlines() if line.strip())
    return jobs, "read ({} jobs queued or running)".format(len(jobs))


def open_log(root, now):
    """Create cleanup_logs/<yyyy-mm-dd_hhmmss>.txt (a _2, _3 suffix if that second is taken)
    and return (open file, path). Raises OSError when it cannot be created."""
    folder = os.path.join(root, "cleanup_logs")
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S", time.gmtime(now))
    n = 1
    while True:
        path = os.path.join(folder, stamp + ("" if n == 1 else "_{}".format(n)) + ".txt")
        try:
            return open(path, "x", encoding="utf-8"), path
        except FileExistsError:
            n += 1


def default_root():
    """The working path: this script lives in <working path>/bin/."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None, queue_reader=read_queue, clock=time.time):
    parser = argparse.ArgumentParser(
        description="Report, or with --delete remove, the Nessie downloads and work folders "
                    "nobody needs any more.")
    parser.add_argument("--delete", action="store_true",
                        help="delete what the report lists under WILL DELETE")
    parser.add_argument("--root", help="the Luria working path (default: the folder above bin/)")
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root or default_root())
    if not os.path.isdir(os.path.join(root, "runs")):
        sys.stderr.write("{} has no runs/ folder; pass --root <LURIA_WORKING_PATH>\n".format(root))
        return 2
    queued, queue_note = queue_reader()
    now = clock()
    items, problems = build_report(root, queued, now)
    add_sizes(items)
    lock_note = probe_lock(os.path.join(root, "fastq_cache"))
    busy = runs_in_progress(root, queued, now)
    report = format_report(root, items, problems, queue_note, lock_note, busy, now,
                           queue_ok=queued is not None)
    print(report)
    if not args.delete:
        return 0
    if queued is None:
        print("\nNot deleting anything: the job queue could not be read.")
        return 1
    try:
        log_fh, path = open_log(root, clock())
    except OSError as exc:
        print("\nNot deleting anything: the log in cleanup_logs/ could not be created ({})."
              .format(exc))
        return 1
    with log_fh:
        def emit(line):
            log_fh.write(line + "\n")
            log_fh.flush()
            print(line)
            sys.stdout.flush()

        log_fh.write(report + "\n\nDELETE PASS\n")
        log_fh.flush()
        print("\nDELETE PASS")
        sys.stdout.flush()
        _log, freed, failures, not_done = delete_pass(root, items, queue_reader, clock, emit)
        end = "Freed {}.".format(human_size(freed))
        log_fh.write(end + "\n")
        if not_done:
            log_fh.write(not_done + "\n")
    print("{} Log: {}".format(end, path))
    if not_done:
        print(not_done)
    return 1 if failures or not_done else 0


if __name__ == "__main__":
    sys.exit(main())
