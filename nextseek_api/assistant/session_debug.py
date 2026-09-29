"""Engine-agnostic inventory of one chat session, for admin debugging.

Every diagnosis this module automates was previously done by hand: ssh to the
box, ``docker cp`` a probe script into the container, ``docker exec`` it, read
the output, delete the script. The facts that actually cracked those cases were
mundane -- how many bundles a session holds, whether ``updated_at`` ever moved,
and how big the three JSON columns got.

SIZES AND PATHS, NEVER PAYLOADS. Serialising a multi-megabyte ``api_result``
into a debug response recreates the class of bug this module exists to
diagnose, so bulk content is reported by size and reached through ``include``.

Nothing here filters. ``AssistantViewSet.get_session`` deliberately hides
non-answer turns (PD-6) because it feeds the chat UI; those turns are precisely
the ones worth debugging, so this module shows every one.
"""
from __future__ import annotations

import os
from typing import Any, Iterable

from NessieAI.ns.bundle_download import _size

#: Rough MySQL thresholds worth flagging. A JSON column at or past these has
#: already caused a live incident: a filesort over ``sort_buffer_size`` (1038)
#: and a write over ``max_allowed_packet`` (2006).
#: How long a pending/running task must sit untouched before it reads as an
#: orphan rather than a turn in flight. Comfortably above the CC per-turn
#: wall-clock ceiling, so a long agent run is never mistaken for a stall.
STALE_TASK_SECONDS = 1800

SORT_BUFFER_BYTES = 262_144
MAX_PACKET_BYTES = 4 * 1024 * 1024

INCLUDABLE = frozenset({"transcripts", "bundles", "progress", "last_debug", "all"})


def _now():
    from django.utils import timezone
    return timezone.now()


def _wants(include: Iterable[str], key: str) -> bool:
    inc = set(include or ())
    return "all" in inc or key in inc


def _stat_entry(row: dict[str, Any]) -> dict[str, Any]:
    """Resolve one file row against disk NOW, never trusting what was written."""
    row["exists"], row["size_bytes"] = False, None
    try:
        path = row.get("path")
        if path and os.path.isfile(path):
            row["exists"] = True
            row["size_bytes"] = os.path.getsize(path)
    except OSError:
        pass
    return row


def _file_entries(bundles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every manifest entry across every bundle, resolved against disk NOW.

    ``build_file_manifest_entry`` only writes an entry when the path exists at
    write time, so an entry whose file is gone means it was removed afterwards
    -- worth knowing, and only visible by stat-ing at read time.
    """
    out: list[dict[str, Any]] = []
    for bundle in bundles:
        if not isinstance(bundle, dict):
            continue
        # The API result is stored ONCE, on disk, with only a pointer in MySQL
        # (see load_api_result_full). It is the largest thing a turn produces and
        # it is not a manifest entry, so it has to be picked up separately or the
        # inventory silently omits the payload most worth locating.
        raw = (bundle.get("raw_result_path")
               or (bundle.get("paths") or {}).get("raw_result_path"))
        if raw:
            out.append(_stat_entry({
                "bundle_id": bundle.get("id"), "key": "raw_result_path",
                "label": "API result (payload pointer)", "filename": os.path.basename(raw),
                "mime": "application/json", "kind": "payload_pointer", "path": raw,
            }))
        for entry in bundle.get("files") or []:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path") or ""
            out.append(_stat_entry({
                "bundle_id": bundle.get("id"),
                "key": entry.get("key"),
                "label": entry.get("label"),
                "filename": entry.get("filename"),
                "mime": entry.get("mime"),
                "kind": entry.get("kind"),
                "path": path,
            }))
    return out


def _warnings(*, session, bundles, chat_log, tasks, sizes, files,
              transcripts) -> list[dict[str, str]]:
    """Derived checks that name the smell instead of leaving it to be eyeballed.

    Each entry encodes a failure mode already seen in production.
    """
    warns: list[dict[str, str]] = []

    # The c0062000 signature. Do NOT test updated_at == created_at: auto_now and
    # auto_now_add fire microseconds apart, so they are never exactly equal, and
    # a tolerance would misread any genuinely fast turn. Whether the turn's
    # output landed is the thing that actually matters, so ask that directly.
    if any(t["status"] == "completed" for t in tasks) and not bundles and not chat_log:
        detail = ("A task reports status=completed but the session holds no bundle "
                  "and no chat_log entry, so the turn ran and its result was never "
                  "persisted.")
        if session.updated_at and session.created_at:
            moved = (session.updated_at - session.created_at).total_seconds()
            detail += f" updated_at moved {moved:.3f}s past created_at."
        warns.append({
            "code": "completed_task_but_session_never_saved",
            "detail": detail,
        })

    for col in ("results_history_bytes", "extra_state_bytes", "last_debug_bytes"):
        n = sizes.get(col) or 0
        if n >= MAX_PACKET_BYTES:
            warns.append({
                "code": "column_over_max_allowed_packet",
                "detail": f"{col}={n} is at or over a typical max_allowed_packet "
                          f"({MAX_PACKET_BYTES}); writes can fail with error 2006.",
            })
        elif n >= SORT_BUFFER_BYTES:
            warns.append({
                "code": "column_over_sort_buffer",
                "detail": f"{col}={n} is over sort_buffer_size ({SORT_BUFFER_BYTES}); "
                          f"a query that sorts on it can fail with error 1038.",
            })

    dangling = [f for f in files
                if f["kind"] == "payload_pointer" and not f["exists"]]
    if dangling:
        warns.append({
            "code": "payload_pointer_missing_on_disk",
            "detail": f"{len(dangling)} bundle(s) record a raw_result_path whose file "
                      f"is gone, so the turn's API result is unrecoverable.",
        })

    missing = [f for f in files
               if not f["exists"] and f["kind"] != "payload_pointer"]
    if missing:
        warns.append({
            "code": "manifest_entry_missing_on_disk",
            "detail": f"{len(missing)} manifest entr(ies) point at a path that does "
                      f"not exist now; the file was removed after the turn wrote it.",
        })

    # Only a task that has STOPPED MOVING is worth reporting. Warning on any
    # running task fires on every live session, which is exactly when someone is
    # watching this endpoint, so the signal has to be the age of the last update
    # rather than the status alone. Measured on production 2026-09-07: two tasks
    # updated 0.0 and 1.1 minutes earlier were healthy turns in flight.
    stale = [t for t in tasks
             if t["status"] in ("running", "pending")
             and (t["stale_for_s"] or 0) > STALE_TASK_SECONDS]
    if stale:
        warns.append({
            "code": "task_stalled",
            "detail": f"{len(stale)} task(s) are still pending/running but have not "
                      f"been updated for over {STALE_TASK_SECONDS // 60} minutes, "
                      f"so they are orphans rather than turns in flight.",
        })

    if len(bundles) != len(chat_log) and (bundles or chat_log):
        warns.append({
            "code": "bundle_chatlog_count_mismatch",
            "detail": f"{len(bundles)} bundle(s) but {len(chat_log)} chat_log "
                      f"entr(ies); wizard and failed turns legitimately produce no "
                      f"bundle, so this is a lead rather than a defect.",
        })

    cc_turns = [e for e in chat_log
                if isinstance(e, dict) and e.get("cc_traces")]
    if cc_turns and not transcripts:
        warns.append({
            "code": "cc_turn_without_transcript",
            "detail": "A CC turn ran but no transcript row was stored. Two failure "
                      "shapes persist nothing deliberately, so this is expected for "
                      "an agent that died before producing records.",
        })

    return warns


def collect(session, *, include: Iterable[str] = ()) -> dict[str, Any]:
    """Inventory one session. ``include`` opts into the bulk payloads."""
    # Imported here so the module stays importable without Django app loading.
    from nextseek_api.assistant.models_db import (
        CCSessionTranscript, QueryTask, TurnLedger,
    )

    bundles = [b for b in (session.results_history or []) if isinstance(b, dict)]
    extra_state = session.extra_state or {}
    chat_log = [e for e in (extra_state.get("chat_log") or []) if isinstance(e, dict)]

    tasks = [
        {
            "task_id": str(t.task_id),
            "status": t.status,
            "query": t.query,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            "duration_s": (
                round((t.updated_at - t.created_at).total_seconds(), 3)
                if t.created_at and t.updated_at else None
            ),
            # Event NAMES only: the payloads are the bulk this endpoint avoids.
            "progress_events": [
                p.get("event", "") for p in (t.progress or []) if isinstance(p, dict)
            ],
            "stale_for_s": (
                round((_now() - t.updated_at).total_seconds(), 1)
                if t.updated_at else None
            ),
            "progress": (t.progress or []) if _wants(include, "progress") else None,
            "result_bytes": _size(t.result) if t.result else 0,
        }
        for t in QueryTask.objects.filter(session=session).order_by("created_at")
    ]

    ledger = [
        {
            "turn_number": r.turn_number,
            # The QueryTask UUID this turn ran as, the join key to "tasks" above;
            # None on rows written before the ledger carried the link.
            "task_id": str(r.query_task.task_id) if r.query_task_id else None,
            "route": r.route,
            "route_source": r.route_source,
            "task_family": r.task_family,
            "attempted_route": r.attempted_route,
            "attempted_source": r.attempted_source,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in TurnLedger.objects.filter(session=session)
        .select_related("query_task").order_by("turn_number")
    ]

    transcripts = []
    for row in CCSessionTranscript.objects.filter(chat_session=session).order_by("created_at"):
        entry = {
            "cc_session_id": row.cc_session_id,
            "turn_id": row.turn_id,
            "uncompressed_size": row.uncompressed_size,
            "compressed_size": len(bytes(row.blob or b"")),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "url": (f"/nextseek_api/cc-assistant/transcript/{session.session_id}/"
                    f"{row.turn_id}/?cc_session_id={row.cc_session_id}"),
        }
        if _wants(include, "transcripts"):
            from django.conf import settings
            from NessieAI.cc.cc_transcript_store import decompress
            cap = getattr(settings, "CC_TRANSCRIPT_MAX_BYTES", 256 * 1024 * 1024)
            try:
                entry["jsonl"] = decompress(bytes(row.blob), max_bytes=cap).decode(
                    "utf-8", "replace")
            except Exception as exc:  # a corrupt blob must not 500 the inventory
                entry["error"] = f"{type(exc).__name__}: {exc}"
        transcripts.append(entry)

    bundles_by_id = {b.get("id"): b for b in bundles}
    turns = []
    # Walk chat_log when there is one, else the bundles: a turn that failed
    # before writing a bundle exists only in chat_log, and a legacy turn only
    # in results_history. NOTHING is filtered out.
    if chat_log:
        for i, entry in enumerate(chat_log):
            bid = entry.get("bundle_id")
            bundle = bundles_by_id.get(bid)
            turns.append({
                "index": i,
                "bundle_id": bid,
                "turn_id": entry.get("turn_id"),
                "mode": entry.get("mode"),
                "ts": entry.get("ts"),
                "router_choice": entry.get("router_choice"),
                "status": entry.get("status"),
                "error": entry.get("error"),
                "user_query": entry.get("user_query", ""),
                "reply_preview": (
                    entry.get("assistant_reply")
                    or entry.get("assistant_reply_preview")
                    or ""
                )[:280],
                "has_bundle": bundle is not None,
                "bundle_bytes": _size(bundle) if bundle else 0,
                "artifact_keys": [
                    f.get("key") for f in (bundle.get("files") or [])
                    if isinstance(f, dict)
                ] if bundle else [],
                "has_cc_traces": bool(entry.get("cc_traces")),
                "bundle": bundle if _wants(include, "bundles") else None,
            })
    else:
        for bundle in bundles:
            turns.append({
                "index": None,
                "bundle_id": bundle.get("id"),
                "turn_id": None,
                "mode": bundle.get("mode"),
                "ts": bundle.get("ts"),
                "router_choice": None,
                "status": None,
                "error": None,
                "user_query": bundle.get("query", ""),
                "reply_preview": (bundle.get("terminal_reply")
                                  or bundle.get("reply") or "")[:280],
                "has_bundle": True,
                "bundle_bytes": _size(bundle),
                "artifact_keys": [f.get("key") for f in (bundle.get("files") or [])
                                  if isinstance(f, dict)],
                "has_cc_traces": False,
                "bundle": bundle if _wants(include, "bundles") else None,
            })

    files = _file_entries(bundles)
    sizes = {
        "results_history_bytes": _size(session.results_history or []),
        "extra_state_bytes": _size(extra_state),
        "last_debug_bytes": _size(session.last_debug or {}),
        "largest_bundle_bytes": max((_size(b) for b in bundles), default=0),
        "cc_transcript_uncompressed_total": sum(
            t["uncompressed_size"] or 0 for t in transcripts),
    }

    return {
        "session": {
            "session_id": str(session.session_id),
            "title": session.title,
            "user": {"id": session.user_id,
                     "username": getattr(session.user, "username", None)},
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
            "counts": {
                "bundles": len(bundles),
                "chat_log_entries": len(chat_log),
                "tasks": len(tasks),
                "ledger_turns": len(ledger),
                "cc_transcripts": len(transcripts),
                "files": len(files),
            },
        },
        "sizes": sizes,
        "turns": turns,
        "tasks": tasks,
        "ledger": ledger,
        "transcripts": transcripts,
        "files": files,
        "last_debug": (session.last_debug or {}) if _wants(include, "last_debug") else None,
        "warnings": _warnings(session=session, bundles=bundles, chat_log=chat_log,
                              tasks=tasks, sizes=sizes, files=files,
                              transcripts=transcripts),
    }
