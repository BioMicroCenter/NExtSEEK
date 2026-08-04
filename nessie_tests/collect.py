"""Post-hoc artifact collection for a paired run.

Four sources, four different keys:

    task row + events   task_id       direct
    CC artifacts list   in the events direct
    CC scratch tree     scratch_dir   docker cp off the dmac-cc-users volume
    CC transcript       session_id    zstd blob in CCSessionTranscript
    NS run_root         ns_run_root   event added in plan 3 task 1

Post-hoc rather than inline so it is re-runnable without repaying for the suite.
The known cost is `nextseek_api/cc_assistant/cc_sweep.py`: CC scratch can be
reaped between the turn and the collection. That is why every miss is RECORDED
rather than skipped. If misses turn out to be common, move CC scratch collection
inline; do not start guessing at what was there.

THREE FACTS ABOUT THE PRODUCT THAT SHAPE THIS MODULE, all verified rather than
assumed. Each is stated where it is acted on as well as here.

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
"""
from __future__ import annotations

import json
import pathlib
import time

ARMS = ("ns", "cc")

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
        The zstd blob for a chat session. `CCSessionTranscript` is keyed
        `(chat_session, cc_session_id, turn_id)`, so a multi-turn CC arm has
        SEVERAL rows; an implementation should concatenate them in turn order,
        which is exactly what a session `.jsonl` is. `session_id` here is the
        NExtSEEK ChatSession id that `make_db_event_callback` setdefaults onto
        `query_complete` -- NOT `cc_session_id`, which is Claude's own and is a
        different value on the same event.

    `copy_tree(src, dest) -> bool`
        True when the tree was copied, False when `src` is not there. Raise
        `CopyFailed` when the copy itself broke; see that exception.
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


def _recorder(missing: list[dict], pair_id: str, arm: str, outaged: bool):
    """A miss-recorder bound to one arm.

    A factory rather than a closure written inside the loop, so nothing depends
    on when the loop variables are read.
    """
    def miss(what, *, kind, path=None, reason=""):
        missing.append({"id": pair_id, "arm": arm, "what": what, "kind": kind,
                        "path": path, "reason": reason, "outage": outaged})
    return miss


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
    art_root = out_dir / "artifacts"
    missing: list[dict] = []
    shared_run_roots: list[dict] = []
    arms_seen = arms_outage = turns_seen = run_roots_copied = 0

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
                    target = dest / ("run_root" if i == 0 else f"run_root_{i + 1}")
                    owner = owner_of.get(root)
                    if owner is not None:
                        # De-duplication ACROSS arms. Each case opens its own
                        # session, so this should not happen; if it does, two
                        # variants' evidence is one directory and the grader is
                        # told rather than handed two identical copies.
                        shared_run_roots.append({"run_root": root,
                                                 "copied_under": owner,
                                                 "also_claimed_by": f"{pair.id}/{arm}"})
                        _write_json(dest / "run_root.shared.json",
                                    {"run_root": root, "copied_under": owner})
                        continue
                    try:
                        copied = sources.copy_tree(root, target)
                    except CopyFailed as exc:
                        miss("run_root", kind="copy_failed", path=root,
                             reason=f"the copy itself failed: {exc}")
                    else:
                        if copied:
                            # Ownership is claimed only by a copy that SUCCEEDED.
                            # Claiming it up front would point a later arm's
                            # run_root.shared.json at a directory that was never
                            # written, and would record one failure where two
                            # arms each hit one.
                            owner_of[root] = f"{pair.id}/{arm}"
                            run_roots_copied += 1
                        else:
                            miss("run_root", kind="absent", path=root,
                                 reason="the source path is not there; the directory "
                                        "was removed or is not visible from here")
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

                scratch = None
                for row in just_rows:
                    scratch = (_event(row, "cc_turn_meta") or {}).get("scratch_dir")
                    if scratch:
                        break
                if not scratch:
                    # NOT a silent skip. VERIFIED: no `cc_turn_meta` event exists
                    # anywhere in the product, and `run_id` (the scratch dir's
                    # key) never reaches an event either, so the CC scratch tree
                    # currently has NO join key at all. The brief read the path
                    # from that event and recorded nothing when it came back
                    # None, which made a whole source quietly vanish. Either add
                    # an event carrying the path (the shape of plan 3 task 1) or
                    # drop cc_scratch from the export -- but do not let it look
                    # like the turn produced no scratch.
                    miss("cc_scratch", kind="no_join_key",
                         reason="no event carries the scratch path: nothing in the "
                                "product emits cc_turn_meta, and run_id never reaches "
                                "an event either, so there is no key to copy by")
                else:
                    try:
                        copied = sources.copy_tree(scratch, dest / "cc_scratch")
                    except CopyFailed as exc:
                        miss("cc_scratch", kind="copy_failed", path=scratch,
                             reason=f"the copy itself failed: {exc}")
                    else:
                        if not copied:
                            miss("cc_scratch", kind="absent", path=scratch,
                                 reason="the scratch path is not there; cc_sweep may "
                                        "have reaped it between the turn and now")

                sid = ""
                for _t, row in reversed(arm_rows):
                    sid = ((row.get("result") or {}).get("session_id")
                           or (_event(row, "query_complete") or {}).get("session_id")
                           or "")
                    if sid:
                        break
                blob = sources.cc_transcript(sid)
                if not blob:
                    miss("cc_transcript", kind="absent",
                         reason=f"no CCSessionTranscript row for session_id {sid!r}")
                else:
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
        "retried_task_ids": len(retry_ids),
        "shared_run_roots": shared_run_roots,
        "missing": missing,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "collection.json", payload)
    return payload
