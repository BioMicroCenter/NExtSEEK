"""Post-hoc artifact collection for a paired run.

Four sources, four different keys:

    task row + events   task_id       direct
    CC artifacts list   in the events direct
    CC scratch tree     task_id       <user_mount>/scratch/<task_id>, docker cp
                                      off the dmac-cc-users volume; see fact 4
    CC transcript       session_id    zstd blob in CCSessionTranscript
    NS run_root         ns_run_root   event added in plan 3 task 1

Post-hoc rather than inline so it is re-runnable without repaying for the suite.
The known cost is `nextseek_api/cc_assistant/cc_sweep.py`: CC scratch can be
reaped between the turn and the collection. That is why every miss is RECORDED
rather than skipped. If misses turn out to be common, move CC scratch collection
inline; do not start guessing at what was there.

FOUR FACTS ABOUT THE PRODUCT THAT SHAPE THIS MODULE, all verified against the
source rather than assumed. Each is stated where it is acted on as well as here.

1.  An entry is a CASE, not a turn, and 30 of the 127 variants in the paired
    selection run 2-3 turns (158 turns in all). So an arm joins to a LIST of
    task rows, and the answer being graded is the LAST one's.

2.  `run_root` is per chat SESSION, not per turn. DictSessionAdapter.save()
    persists `run_root_dir` into ChatSession.extra_state, the next turn's adapter
    reloads it, and `_ensure_query_log_dir` (orchestrator.py:329-331) returns
    early instead of making a new directory, so turns 2..n append into turn 1's.
    Several task_ids therefore map to ONE run_root. That is correct product
    behaviour; this module de-duplicates on the PATH rather than treating the
    repetition as an error.

3.  `ns_run_root` necessarily lands AFTER `query_complete`, because `run_query`
    emits `query_complete` from inside itself and the join key is emitted from
    the `finally` around the call. Both go to `QueryTask.progress` through
    separate `task.save()` calls, so a collector chained onto the end of a run
    can read the row in the one-save-wide window between them. Hence the single
    retry in `collect`. The timestamp window is deliberately NOT the fallback: it
    is only unambiguous while runs are strictly sequential, and a paired run
    interleaves two engines per question, which is exactly when it is ambiguous.

4.  CC scratch IS addressable from the task_id, and the per-TURN unit is
    `<user_mount>/scratch/<task_id>`. `cc_run_id = str(query_task.task_id)`
    (cc_assistant.py:415) becomes `run_id` in `run_cc_turn`, which mkdirs the
    per-run working dir at cc_engine.py:619-622 and publishes that turn's
    deliverables to `<output_mnt>/artifacts/<run_id>` (cc_engine.py:931). The
    brief pointed at `dirs.scratch_mnt`, which is the whole PER-USER root
    (cc_provision.py:120) shared by every arm in the run -- copying that per arm
    copies one growing tree 127 times. Only the absolute `<user_mount>` prefix
    has to be recovered, and `cc_raw_files` carries it (cc_engine.py:971).

    Two things a reader will otherwise assume and be wrong about:

    * `cc_turn_meta` DOES exist (cc_assistant.py:605) and does reach the row via
      `make_db_event_callback`. It carries model_id / cc_session_id / budget_usd
      / turn_timeout_s and NO path, which is why the brief's
      `cc_turn_meta.scratch_dir` read always came back None.
    * `<scratch_mnt>/<run_id>` is created for every turn but is not where the
      agent works: the container's working_dir is `/home/user`
      (cc_engine.py:137) and the whole per-user scratch is mounted at
      `/data/scratch` (cc_engine.py:128), which is what the plugin writes into
      (_nextseek_runner.py:105). So the per-turn dir is the correct unit to copy
      and is frequently EMPTY, while this turn's actual deliverables are the
      per-turn `output/artifacts/<task_id>/` collected alongside it.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

ARMS = ("ns", "cc")

# WHERE THE COLLECTED TREE LIVES, DERIVED IN ONE PLACE. This module WRITES
# `<run>/artifacts/<variant>/<arm>/`, so this module owns the name; `export`'s CLI
# and the report builder read it back through `artifacts_dir` rather than each
# spelling `/ "artifacts"` for itself.
#
# That is a correctness rule, not a tidiness one. An `export` that runs WITHOUT
# the artifacts tree cannot see a deadline abort at all -- the only evidence is
# the collected, still-non-terminal task row -- so it writes a scored CSV row for
# an arm the report bands ungradable and strips of grade controls. `merge_grades`
# then raises `IncompleteGrading` naming that row, which instructs the operator to
# grade something the page gives them no way to grade. Two independent defaults
# for this path is exactly how the two sides come to disagree.
ARTIFACTS_DIRNAME = "artifacts"


def artifacts_dir(run_dir) -> pathlib.Path:
    """`<run_dir>/artifacts` -- the value `export(..., artifacts_dir=)` wants.

    NOT the collector's `out_dir`, which is the run directory one level up.
    """
    return pathlib.Path(run_dir) / ARTIFACTS_DIRNAME

# Seconds to wait before the ONE retry for a late `ns_run_root`. See fact 3 in
# the module docstring. One retry and no more: a collector that keeps polling
# turns a systematic gap into a hang, and the gap is the thing worth seeing.
RETRY_DELAY_S = 2.0

# Mirrors nextseek_api/cc_assistant/cc_transcript_store.decompress's bomb guard.
# Not imported from there: nothing in nessie_tests imports the product, because
# the harness drives it over HTTP and must stay runnable without Django on the
# path. Copying six lines is the cheaper of the two couplings.
MAX_TRANSCRIPT_BYTES = 256 * 1024 * 1024

_TERMINAL = ("completed", "error")


class CopyFailed(Exception):
    """Raised BY a `Sources.copy_tree` when the copy MECHANISM broke.

    `copy_tree -> bool` cannot distinguish the two outcomes that matter, and only
    one of them is a collection problem: "the source is not there" (cc_sweep
    reaped the scratch, the turn never made a run_root) is a fact about the run,
    while "docker is not running / permission denied / disk full" is a fact about
    the collector. A hundred of the first is a quiet product result; a hundred of
    the second is a broken collection that must not be mistaken for one.

    So the contract splits the bool: return False for ABSENT, raise this for
    FAILED. Additive, so a Sources that only ever returns a bool still works and
    simply reports everything as absent, which is the honest reading of a source
    that declines to say more.
    """


class Sources:
    """The four injected sources. A protocol, stated as a class for its docstring.

    `task_rows(task_ids) -> {task_id: row}`
        `row` is the shape the progress endpoint and `QueryTask` share:
        `{"status": str, "progress": [{"event": str, "data": dict}], "result": dict|None}`.
        Ids with no row are simply absent from the mapping; do not invent rows.

    `cc_transcript(session_id) -> bytes | None`
        The zstd blob for a chat session, decompressing to that session's whole
        `.jsonl`. `CCSessionTranscript` is keyed
        `(chat_session, cc_session_id, turn_id)`, so a multi-turn CC arm has
        SEVERAL rows -- and they must be folded, NOT concatenated. Pre-#68 rows
        hold the CUMULATIVE session file as of that turn (`--resume` appends to
        one file under the per-session cc-state dir and every turn re-read all of
        it); post-#68 rows hold one turn's slice. Concatenating duplicates every
        earlier turn on the pre-#68 shape, and the result is
        still valid jsonl, so nothing downstream notices. Order by `created_at`:
        `turn_id` is `str(query_task.task_id)`, a random UUID in a `CharField`,
        so ordering by it is a shuffle. `sources.merge_transcripts` folds by
        containment, which is right for those rows AND for the disjoint ones a
        wiped cc-state store produces. `session_id` here is the NExtSEEK
        ChatSession id that `make_db_event_callback` setdefaults onto
        `query_complete` -- NOT `cc_session_id`, which is Claude's own and is a
        different value on the same event.

    `copy_tree(src, dest) -> bool`
        True when the tree was copied, False when `src` is not there. Raise
        `CopyFailed` when the copy itself broke -- and raise NOTHING ELSE. Only
        `CopyFailed` is caught below, so any other exception aborts the whole
        collection over one arm; see `_copy_into`.

    The one concrete implementation is `nessie_tests.sources.DockerSources`,
    which reads all three through the running `nextseek` container.
    """

    def task_rows(self, task_ids: list[str]) -> dict[str, dict]: ...
    def cc_transcript(self, session_id: str) -> bytes | None: ...
    def copy_tree(self, src: str, dest: pathlib.Path) -> bool: ...


def decompress_transcript(blob: bytes) -> bytes:
    """zstd -> raw jsonl, with the same hard output cap the product's store uses.

    `zstandard` is imported here rather than at module scope on purpose: it is a
    real dependency of the stack but NOT of the host test lane, and a collector
    that cannot even be imported without it would take the whole unit suite with
    it.
    """
    import zstandard

    out = bytearray()
    with zstandard.ZstdDecompressor().stream_reader(blob) as reader:
        while True:
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            out.extend(chunk)
            if len(out) > MAX_TRANSCRIPT_BYTES:
                raise ValueError(f"transcript exceeds {MAX_TRANSCRIPT_BYTES} bytes")
    return bytes(out)


def _task_ids(entry) -> list[str]:
    """Every turn's task id, in turn order.

    A field, never a regex over `reason`: `reason` is prose written for a human
    and nothing guarantees it carries an id at all.
    """
    return list(getattr(entry, "task_ids", None) or []) if entry is not None else []


def _event(row: dict, name: str) -> dict | None:
    for ev in (row.get("progress") or []):
        if ev.get("event") == name:
            return ev.get("data") or {}
    return None


def _is_terminal(row) -> bool:
    return (row or {}).get("status") in _TERMINAL


def _run_roots(rows: list[dict]) -> list[str]:
    """The DISTINCT run_roots across an arm's turns, in first-seen order.

    Normally exactly one -- see fact 2 in the module docstring. More than one
    means the chat session rotated mid-case, and then both directories really are
    separate evidence and both are kept.
    """
    seen: list[str] = []
    for row in rows:
        root = (_event(row, "ns_run_root") or {}).get("run_root")
        if root and root not in seen:
            seen.append(root)
    return seen


def _dedup(items: list) -> list:
    """Order-preserving de-duplication over unhashable JSON values."""
    out, keys = [], set()
    for item in items:
        key = json.dumps(item, sort_keys=True, default=str)
        if key not in keys:
            keys.add(key)
            out.append(item)
    return out


def _write_json(path: pathlib.Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


# `cc_raw_files` entries are absolute LOGICAL paths built as
# `str(Path(output_logical_root) / "raw" / rel)` (cc_engine.py:971) where
# `output_logical_root` is `dirs.output_mnt` (cc_engine.py:779), and
# `output_mnt` is `f"{user_mount}/scratch"`'s sibling `f"{user_mount}/output"`
# (cc_provision.py:121). So everything before this marker IS `{user_mount}`, and
# `{user_mount}/scratch` is the CC scratch root. `user_mount` itself is
# `{mount_root}/{project_dirname}/{user_id}` (cc_provision.py:110) whose two
# tail segments are `_SEGMENT_RE`-validated (cc_provision.py:14, no slashes), so
# only a `mount_root` containing this literal could produce a false first split
# -- and `mount_root` is the users-volume mount point, not user-supplied.
_RAW_MARKER = "/output/raw/"


# The two ways the prefix can be unavailable. Stated NARROWLY: the join key
# itself (the task_id) is always in hand and the directory layout is known, so
# neither of these is "CC scratch has no join key".
_NO_PREFIX_REASON = (
    "the per-turn scratch dir is <user_mount>/scratch/<task_id> and the task_id "
    "IS known, but no query_complete event anywhere in this run carried a "
    "cc_raw_files entry, which is the only place the absolute <user_mount> "
    "prefix appears (cc_engine.py:971). A turn that wrote nothing under "
    "scratch/raw/ emits none, so a run in which no CC turn produced raw output "
    "has no prefix to recover. Emitting the scratch path on cc_turn_meta "
    "(cc_assistant.py:605, which exists and carries model_id/cc_session_id/"
    "budget_usd/turn_timeout_s but no path) would remove this dependency"
)

_AMBIGUOUS_PREFIX_REASON = (
    "more than one <user_mount> prefix was observed in this run ({found}), so "
    "the arm's own prefix cannot be inferred from the others. A paired run is "
    "one user against one project, so this means the run spanned two -- do not "
    "guess which"
)


def _raw_path_strings(qc: dict) -> list[str]:
    """`cc_raw_files` entries as strings.

    `cc_engine` emits plain path strings, but `evaluate.py` already tolerates the
    `{"path": ...}` dict shape from older payloads, so this does too rather than
    silently contributing nothing on a run that carries them.
    """
    out = []
    for item in (qc.get("cc_raw_files") or []):
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            out.append(item["path"])
    return out


def _user_mounts(rows: list[dict]) -> list[str]:
    """The distinct `{user_mount}` prefixes derivable from these rows."""
    found = []
    for row in rows:
        for path in _raw_path_strings(_event(row, "query_complete") or {}):
            if _RAW_MARKER in path:
                prefix = path.split(_RAW_MARKER, 1)[0]
                if prefix and prefix not in found:
                    found.append(prefix)
    return found


def _recorder(missing: list[dict], pair_id: str, arm: str, outaged: bool):
    """A miss-recorder bound to one arm.

    A factory rather than a closure written inside the loop, so nothing depends
    on when the loop variables are read.
    """
    def miss(what, *, kind, path=None, reason=""):
        missing.append({"id": pair_id, "arm": arm, "what": what, "kind": kind,
                        "path": path, "reason": reason, "outage": outaged})
    return miss


def _copy_into(sources, src: str, target: pathlib.Path, *, miss, what: str,
               absent_reason: str) -> bool:
    """One copy attempt. Records its own miss and NEVER raises.

    The `CopyFailed`/`False` split is the whole point: "the source is not there"
    and "the copy mechanism broke" are different facts and only the second is a
    collection problem.
    """
    try:
        copied = sources.copy_tree(src, target)
    except CopyFailed as exc:
        miss(what, kind="copy_failed", path=src,
             reason=f"the copy itself failed: {exc}")
        return False
    if not copied:
        miss(what, kind="absent", path=src, reason=absent_reason)
        return False
    return True


def _fetch_transcript(sources, sid: str, *, miss):
    """One transcript fetch. Records its own miss and NEVER raises.

    The sibling of `_copy_into`, and for the same reason. This call sits INSIDE
    the per-arm loop, so an exception here does not fail one arm -- it abandons
    the whole collection partway, leaving no `collection.json` and an
    `artifacts/` tree that is half-written rather than absent. `export` warns
    only on an ABSENT tree, so that half-run then exports in silence. One guarded
    call site beside one unguarded one is worse than neither, because the
    invariant reads as held.

    `fetch_failed` is its OWN kind, not `unreadable`. `unreadable` means the
    bytes arrived and would not decode -- a fact about the blob. This means the
    bytes never arrived, which is a fact about the COLLECTOR, and it belongs on
    the same side of that line as `copy_failed`: 127 transcripts missing because
    the container went away must be distinguishable at a glance from a product
    that produced none.

    Deliberately catches `Exception`, not a named class. `Sources` is a protocol
    with no declared exception for this method, so what a given implementation
    raises is not knowable here -- and the point is to survive it, whatever it is.
    """
    try:
        blob = sources.cc_transcript(sid)
    except Exception as exc:
        miss("cc_transcript", kind="fetch_failed", path=sid,
             reason=f"the transcript could not be FETCHED for session_id {sid!r}: "
                    f"{type(exc).__name__}: {exc}. The row may well exist -- this "
                    f"is the collector failing to reach it, not the product "
                    f"failing to write it")
        return None
    if not blob:
        # Recorded HERE rather than by the caller, so the two outcomes of one
        # fetch cannot drift apart, and so a fetch that raised never also
        # reports the absence it never established.
        miss("cc_transcript", kind="absent", path=sid,
             reason=f"no CCSessionTranscript row for session_id {sid!r}")
        return None
    return blob


def collect(manifest, out_dir, sources, outputs_root=None, *,
            retry_delay_s: float = RETRY_DELAY_S, sleep=time.sleep,
            decompress=None) -> dict:
    """Pull every artifact both arms produced into `out_dir/artifacts/<id>/<arm>/`.

    Returns the `collection.json` payload it also writes.

    `outputs_root` is accepted and deliberately unused. It was the hook for the
    timestamp-window fallback, which is NOT implemented: the window is only
    unambiguous while runs are strictly sequential, and a paired run interleaves
    two engines per question. The retry below replaces it. Kept in the signature
    because the plan publishes it; a caller passing it gets today's behaviour
    rather than a TypeError.

    NOTHING here raises on a missing artifact. A collection that aborts partway
    leaves a tree that looks like a short run, which is the failure mode this
    whole module is written against.
    """
    # Looked up through the module rather than bound now, so a test can swap
    # `decompress_transcript` and cover the blob -> session.jsonl wiring in a
    # host lane that has no zstandard.
    decompress = decompress or (lambda blob: decompress_transcript(blob))
    out_dir = pathlib.Path(out_dir)
    art_root = artifacts_dir(out_dir)
    missing: list[dict] = []
    shared_run_roots: list[dict] = []
    arms_seen = arms_outage = turns_seen = run_roots_copied = cc_scratch_copied = 0

    wanted = _dedup([t for p in manifest.pairs for arm in ARMS
                     for t in _task_ids(getattr(p, arm))])
    rows: dict[str, dict] = dict(sources.task_rows(wanted) or {}) if wanted else {}

    # --- the one retry, batched --------------------------------------------
    # An NS arm that has a TERMINAL row and still no run_root is the shape of the
    # race in fact 3. A non-terminal row is not late, it is unfinished, and
    # re-reading it buys a round-trip and the same answer -- which is also why
    # every unit test here can inject a fake source without sleeping.
    retry_ids: list[str] = []
    for pair in manifest.pairs:
        ids = _task_ids(pair.ns)
        ns_rows = [rows[t] for t in ids if t in rows]
        if not ids or _run_roots(ns_rows):
            continue
        if any(_is_terminal(r) for r in ns_rows):
            retry_ids += ids
    retry_ids = _dedup(retry_ids)
    if retry_ids:
        if retry_delay_s:
            sleep(retry_delay_s)
        rows.update(sources.task_rows(retry_ids) or {})

    # run_root -> the "<variant>/<arm>" whose directory holds the one copy.
    owner_of: dict[str, str] = {}

    # The `{user_mount}` prefix, derived ONCE for the whole run. A paired run is
    # one user against one project, so every CC arm shares it -- which matters
    # because only a turn that wrote under scratch/raw/ carries it, and most
    # turns do not. An arm with none borrows the run's, but only when the run
    # has exactly one; two would mean the run spanned two users or projects and
    # there is nothing to borrow.
    cc_rows = [rows[t] for p in manifest.pairs for t in _task_ids(p.cc) if t in rows]
    run_mounts = _user_mounts(cc_rows)
    run_mount = run_mounts[0] if len(run_mounts) == 1 else None

    for pair in manifest.pairs:
        for arm in ARMS:
            entry = getattr(pair, arm)
            if entry is None:
                continue
            arms_seen += 1
            # An outage arm ran no product code, so its empty hands are a fact
            # about Bedrock, not about this collector. Tagged rather than
            # dropped: burying a real systematic gap in 40 outage rows is the
            # same mistake as scoring the outage.
            outaged = bool(getattr(entry, "outage", False))
            if outaged:
                arms_outage += 1
            dest = art_root / pair.id / arm
            dest.mkdir(parents=True, exist_ok=True)

            miss = _recorder(missing, pair.id, arm, outaged)

            ids = _task_ids(entry)
            if not ids:
                miss("task_id", kind="no_join_key",
                     reason="the manifest entry records no task_ids, so nothing can "
                            "be joined to it; the run predates the field")
            arm_rows = []
            for tid in ids:
                row = rows.get(tid)
                if row is None:
                    miss("task_row", kind="absent", path=tid,
                         reason="no assistant_query_task row for this task_id")
                else:
                    arm_rows.append((tid, row))
            turns_seen += len(arm_rows)

            if arm_rows:
                # The LAST turn's row: a follow-up's reply is the answer under
                # grade, and turn 1 of a refine_and_recall case answered a
                # different question.
                _write_json(dest / "task.json", arm_rows[-1][1])
                if len(ids) > 1:
                    # Written only for a multi-turn arm, so its ABSENCE is the
                    # signal that the arm ran exactly one turn.
                    _write_json(dest / "turns.json",
                                [{"task_id": t, "row": r} for t, r in arm_rows])

            just_rows = [r for _t, r in arm_rows]
            if arm == "ns":
                roots = _run_roots(just_rows)
                if not roots:
                    miss("ns_run_root_event", kind="absent",
                         reason="no ns_run_root event on any of this arm's turns; the "
                                "run predates the event, the turn never reached the "
                                "orchestrator, or the emit itself failed")
                for i, root in enumerate(roots):
                    name = "run_root" if i == 0 else f"run_root_{i + 1}"
                    target = dest / name
                    owner = owner_of.get(root)
                    if owner is not None:
                        # De-duplication ACROSS arms. Each case opens its own
                        # session, so this should not happen; if it does, two
                        # variants' evidence is one directory and the grader is
                        # told rather than handed two identical copies.
                        shared_run_roots.append({"run_root": root,
                                                 "copied_under": owner,
                                                 "also_claimed_by": f"{pair.id}/{arm}"})
                        # Named after the slot it stands in for, so an arm with
                        # two already-owned roots does not overwrite its own
                        # first pointer -- the same scheme as run_root_2/.
                        _write_json(dest / f"{name}.shared.json",
                                    {"run_root": root, "copied_under": owner})
                        continue
                    if _copy_into(
                            sources, root, target, miss=miss, what="run_root",
                            absent_reason="the source path is not there; the directory "
                                          "was removed or is not visible from here"):
                        # Ownership is claimed only by a copy that SUCCEEDED.
                        # Claiming it up front would point a later arm's
                        # pointer file at a directory that was never written,
                        # and would record one failure where two arms each hit
                        # one.
                        owner_of[root] = f"{pair.id}/{arm}"
                        run_roots_copied += 1
            else:
                if arm_rows:
                    qcs = [_event(r, "query_complete") or {} for r in just_rows]
                    # Unioned across turns: each CC turn publishes its OWN set and
                    # the arm produced all of them.
                    _write_json(dest / "artifacts.json", {
                        "artifacts": _dedup([a for qc in qcs
                                             for a in (qc.get("artifacts") or [])]),
                        "cc_raw_files": _dedup([f for qc in qcs
                                                for f in (qc.get("cc_raw_files") or [])]),
                    })

                # --- CC scratch + this turn's published deliverable files ----
                # BOTH are per-TURN and BOTH are keyed by the task_id already in
                # hand. `cc_run_id = str(query_task.task_id)`
                # (cc_assistant.py:415) is passed to `run_cc_turn` as `run_id`,
                # which creates the per-run working dir
                # `<scratch_mnt>/<run_id>` (cc_engine.py:619-622) and publishes
                # that turn's deliverables to
                # `<output_mnt>/artifacts/<run_id>` (cc_engine.py:931, keys
                # `"<turn_id>/<rel>"` at :959/:966). Only the `{user_mount}`
                # prefix has to be recovered, and `cc_raw_files` carries it.
                #
                # `<scratch_mnt>/<run_id>`, NOT `<scratch_mnt>`. The latter is
                # the whole PER-USER scratch root (cc_provision.py:120), shared
                # by every CC arm in the run and by the sidecar; copying it per
                # arm copies one growing tree 127 times. The brief named that
                # root, which is the wrong unit.
                own_mounts = _user_mounts(just_rows)
                mount = own_mounts[0] if len(own_mounts) == 1 else run_mount
                if not mount:
                    # Narrow and specific: everything but the prefix is known.
                    ambiguous = len(own_mounts) > 1 or len(run_mounts) > 1
                    miss("cc_scratch",
                         kind="ambiguous_path_prefix" if ambiguous else "no_path_prefix",
                         reason=_AMBIGUOUS_PREFIX_REASON.format(
                             found=sorted(own_mounts or run_mounts))
                         if ambiguous else _NO_PREFIX_REASON)
                else:
                    for tid in ids:
                        if _copy_into(
                                sources, f"{mount}/scratch/{tid}",
                                dest / "cc_scratch" / tid,
                                miss=miss, what="cc_scratch",
                                absent_reason="the per-turn scratch dir is not there; "
                                              "cc_sweep may have reaped it between the "
                                              "turn and now"):
                            cc_scratch_copied += 1
                        _copy_into(
                            sources, f"{mount}/output/artifacts/{tid}",
                            dest / "cc_artifacts" / tid,
                            miss=miss, what="cc_artifact_files",
                            absent_reason="no published-artifact directory for this "
                                          "turn; the turn published no deliverables, "
                                          "or the directory was removed")

                sid = ""
                for _t, row in reversed(arm_rows):
                    sid = ((row.get("result") or {}).get("session_id")
                           or (_event(row, "query_complete") or {}).get("session_id")
                           or "")
                    if sid:
                        break
                blob = _fetch_transcript(sources, sid, miss=miss)
                if blob:
                    try:
                        (dest / "session.jsonl").write_bytes(decompress(blob))
                    except Exception as exc:
                        miss("cc_transcript", kind="unreadable",
                             reason=f"the blob would not decompress: "
                                    f"{type(exc).__name__}: {exc}")

    payload = {
        "pairs": len(manifest.pairs),
        "arms_seen": arms_seen,
        "arms_outage": arms_outage,
        "turns_seen": turns_seen,
        "run_roots_copied": run_roots_copied,
        "cc_scratch_copied": cc_scratch_copied,
        "retried_task_ids": len(retry_ids),
        "shared_run_roots": shared_run_roots,
        "missing": missing,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "collection.json", payload)
    return payload


# --- the command SKILL.md names ----------------------------------------------
# This used to be a refusal, because `collect` needs a CONCRETE `Sources` and
# nothing built one. `nessie_tests.sources.DockerSources` now does, so the
# documented step really collects.
#
# What the refusal was protecting is NOT dropped, only narrowed. An argparse that
# accepted `--run` and exited 0 over a dead container would leave a run directory
# whose `collection.json` records every single artifact missing -- which is
# indistinguishable, on the page, from a product that produced nothing, and the
# operator discovers it after grading 254 arms that read "No reply was recorded",
# if at all. So the CLI still REFUSES, on the three things that make a collection
# meaningless before it starts: no paired manifest, no pairs in it, and a source
# that cannot answer a ping. What it must never do is any of those quietly.

# `decompress_transcript` imports zstandard LATE so the unit suite can import
# this module without it -- which is right, and which also means a host missing
# it does not find out until it has already recorded 127 CC arms' transcripts
# "unreadable", one per arm, buried among the honest misses. Checked once, up
# front, and named as the run-shaped fact it is.
_NO_ZSTANDARD = """\
WARNING: `zstandard` is not importable here, so EVERY CC arm's session.jsonl will
be recorded `unreadable` in collection.json -- not because the product lost the
transcript (the blob is fetched fine) but because this process cannot unpack it.
The rest of the collection is unaffected and is still worth having.

Install it, or re-run this step as:
  uv run --no-project --with zstandard python -m nessie_tests.collect --run <run>
"""


def _summarise(payload: dict) -> list[str]:
    """The run's collection, in the terms an operator has to act on.

    Never a bare total. A `copy_failed` is a fact about the COLLECTOR and an
    `absent` is a fact about the run, and the whole `CopyFailed` split is wasted
    if the summary adds them together at the end.
    """
    kinds: dict[str, int] = {}
    for miss in payload["missing"]:
        kinds[miss["kind"]] = kinds.get(miss["kind"], 0) + 1
    lines = [
        f"  arms          {payload['arms_seen']}"
        f" ({payload['arms_outage']} outage)",
        f"  turns         {payload['turns_seen']} task row(s)",
        f"  run_roots     {payload['run_roots_copied']} copied",
        f"  cc_scratch    {payload['cc_scratch_copied']} copied",
        f"  missing       {len(payload['missing'])}"
        + (": " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()))
           if kinds else ""),
    ]
    # `copy_failed`, `fetch_failed` and `unreadable` are all facts about the
    # COLLECTOR sitting in a list of facts about the run, so each gets called out
    # and none is left to be inferred from a total. `unreadable` had no line at
    # all, and its one diagnosis was the zstandard warning printed on stderr
    # BEFORE a multi-minute collection -- long scrolled off by the time the
    # operator reads "unreadable 127" and an exit 0. A diagnosis has to arrive
    # where the person is looking, which is the end.
    if kinds.get("copy_failed"):
        lines.append(
            f"  !! {kinds['copy_failed']} copy_failed -- that is the COLLECTOR "
            f"failing, not the run: see collection.json")
    if kinds.get("fetch_failed"):
        lines.append(
            f"  !! {kinds['fetch_failed']} transcript fetch_failed -- the rows "
            f"may well EXIST; this is the collector failing to reach them, not "
            f"the product failing to write them. A container that went away "
            f"mid-run looks exactly like this.")
    if kinds.get("unreadable"):
        lines.append(
            f"  !! {kinds['unreadable']} unreadable -- the blobs were FETCHED "
            f"and this process could not unpack them, so that is the COLLECTOR "
            f"too, not a lost transcript.")
        try:
            import zstandard  # noqa: F401
        except ImportError:
            lines.append("     `zstandard` is not importable here; that is why. "
                         "Re-run this step as:")
            lines.append("       uv run --no-project --with zstandard python -m "
                         "nessie_tests.collect --run <run>")
        else:
            lines.append("     `zstandard` IS importable here, so this is not the "
                         "usual cause: read the reasons in collection.json.")
    if payload["arms_seen"] and not payload["turns_seen"]:
        lines.append(
            "  !! not one task row was joined. The manifest's task_ids and the "
            "container's assistant_query_task rows do not meet; this is not a "
            "run in which nothing happened.")
    return lines


def main(argv=None) -> int:
    """Collect one paired run into `<run>/artifacts/`. Returns an exit code."""
    import argparse

    # Imported HERE, not at module scope, for the reason `decompress_transcript`
    # imports zstandard late: the unit suite imports this module constantly and
    # must not drag in the source implementation, and `sources` imports this
    # module back for `CopyFailed`.
    from nessie_tests import bayes_manifest, sources as sources_mod

    ap = argparse.ArgumentParser(
        description="Collect a paired (--bayesian) run's artifacts out of the "
                    "running container into <run>/artifacts/.")
    ap.add_argument("--run", required=True,
                    help=f"the paired run directory: the one holding "
                         f"{bayes_manifest.MANIFEST_NAME}")
    ap.add_argument("--container", default=sources_mod.DEFAULT_CONTAINER,
                    help="the app container the dmac-cc-users volume is mounted "
                         "into (default: %(default)s)")
    ap.add_argument("--host", default="",
                    help='ssh target for the docker daemon; the default "" is '
                         "the LOCAL daemon, which is what a --bayesian run needs "
                         "today. `ssh localhost` is not a fallback")
    ap.add_argument("--user", default="",
                    help="sudo -u target on --host; ignored without --host")
    ap.add_argument("--timeout", type=float,
                    default=sources_mod.DEFAULT_TIMEOUT_S,
                    help="seconds per container round trip (default: %(default)s)")
    args = ap.parse_args(argv)

    run = pathlib.Path(args.run)
    m = bayes_manifest.read_bayes_manifest(run)
    if m is None:
        # The same collision `export` and the report builder refuse: a normal
        # run's `manifest.json` is a different schema that validates as an EMPTY
        # paired manifest rather than raising, so reading it here would write an
        # empty artifacts tree and call the run collected.
        extra = (f"  ({run / 'manifest.json'} exists -- that is a NORMAL run's "
                 f"manifest and a DIFFERENT schema: it validates as an EMPTY "
                 f"paired manifest rather than raising.)\n"
                 if (run / "manifest.json").is_file() else "")
        print(f"no {bayes_manifest.MANIFEST_NAME} in {run}\n{extra}"
              f"Run the suite with --bayesian first.", file=sys.stderr)
        return 2
    if not m.pairs:
        print(f"{run / bayes_manifest.MANIFEST_NAME} records no pairs",
              file=sys.stderr)
        return 2

    sources = sources_mod.DockerSources(
        container=args.container, host=args.host, user=args.user,
        timeout=args.timeout)
    try:
        # BEFORE anything is written. A collection that starts against a dead
        # container still finishes, and finishes looking complete.
        ping = sources.ping()
    except sources_mod.SourcesUnavailable as exc:
        print(f"the collection sources are not reachable, so nothing was "
              f"collected:\n{exc}\n\n"
              f"Container {args.container!r} on "
              f"{args.host or 'the local docker daemon'} must be RUNNING and "
              f"able to reach MySQL. Nothing here starts or rebuilds it.",
              file=sys.stderr)
        return 2

    try:
        import zstandard  # noqa: F401
    except ImportError:
        print(_NO_ZSTANDARD, file=sys.stderr)

    payload = collect(m, run, sources)

    print(f"{len(m.pairs)} pair(s) -> {artifacts_dir(run)}")
    print(f"  source        {args.container} on "
          f"{args.host or 'the local docker daemon'}, "
          f"{ping.get('query_tasks', '?')} task row(s) / "
          f"{ping.get('cc_transcripts', '?')} transcript(s) in reach")
    for line in _summarise(payload):
        print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
