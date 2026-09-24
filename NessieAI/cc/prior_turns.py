"""The previous turns of this chat, staged for a Container-CC turn to read.

2026-09-23 ruling: every follow-up goes to Container-CC, "and ENSURE that container_cc
has access to the previous run's artifacts". Before this, a CC turn saw an NS turn only
through the within-chat digest in its memory file (question, endpoint, counts, first
UIDs) and the ``nextseek-recall`` op (the rows, one turn at a time). It could not see
what the user sees under Search details (the entity resolution, the parser's mode and
intent, the graph Cypher with its explanation and parameters, the Neo4j count, or the
REST request), nor the files the turn offered for download.

``stage_prior_turns`` writes all of it, per turn, into the chat session's own
``_memory/<session>/previous_turns/`` directory, which the engine mounts read-only at
``/data/previous_turns`` (``cc_engine._build_volumes``):

    MANIFEST.md         read first: which turn, which question, which file holds what
    manifest.json       the same, machine-readable
    turn-03/
      search_details.json   entity, parser, graph (cypher, explanation, parameters),
                            neo4j (count, total, truncated), or the REST request
      rows.json             every row the turn returned (the graph rows file, or the
                            REST result), with the cypher that produced them
      rows.csv              the same rows, flattened
      samples.csv           every stored property of the samples those rows name, one row
                            per sample, read once from the graph (for a follow-up that asks
                            for a field the search did not return: sex, genotype, a date)
      <files>               every download the turn offered (reports, workbooks, ...)
    turn-04/                a Container-CC turn: answer.md and the files it published

Scope. Nothing here widens what the user can see:

* the turns come only from this chat session's ``chat_log`` and ``results_history``,
  and the session was resolved for ``request.user`` by the ViewSet;
* an NS file is copied only when ``safe_ns_path`` (``NessieAI.ns.artifacts.
  _safe_artifact_path``, the guard of the download endpoint) resolves it inside the
  outputs roots; a CC file only when it resolves inside this user's own
  ``output/artifacts/<run id>/``, and never through a link;
* the destination is this user's, this session's ``_memory`` subtree, mounted into this
  session's turns only, read-only;
* the rows were scoped when the turn ran (a non-admin's Cypher runs with the scope
  inserted), and re-running a stored Cypher goes back through the graph op, which
  re-scopes it server-side for the same caller;
* ``samples.csv`` is read through the caller's own graph tool (``graph_query``, which
  ``turn.py`` builds on ``tool_neo4j_query`` with this request's scope), so the write
  check and the scope prover apply, and the properties ``graph_scope`` keeps hidden are
  dropped here as well.

Best effort: a turn that cannot be staged is recorded in the manifest with the reason,
and nothing here may fail the user's turn.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: The staging directory's name under ``_memory/<session>/``. ``build_user_dirs``
#: builds the same tail as ``previous_turns_subpath``.
DIRNAME = "previous_turns"
#: Where the agent sees it. ``cc_engine`` mounts it here, read-only.
CONTAINER_PATH = "/data/previous_turns"
MANIFEST_MD = "MANIFEST.md"
MANIFEST_JSON = "manifest.json"
#: The most recent turns staged. The digest in the memory file shows the same number.
MAX_TURNS = 10
#: A file larger than this is listed with its size and not copied.
MAX_FILE_BYTES = 64 * 1024 * 1024
#: File kinds that exist to explain a run (``excel_export._INTERNAL_FILE_KINDS``). The
#: graph debug JSON is read for the entity resolution, not copied.
_INTERNAL_FILE_KINDS = frozenset({"graph", "memory"})
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")
_TURN_DIR = re.compile(r"turn-\d+")

SafePath = Callable[[str], "Path | None"]
#: ``(cypher, parameters) -> result`` in ``tool_neo4j_query``'s shape, held to the caller's scope.
GraphQuery = Callable[[str, dict], dict]

SAMPLES_CSV = "samples.csv"
#: At most this many UIDs are read, the graph turn's own row cap.
MAX_SAMPLE_UIDS = 5000
#: Every property of the samples an NS turn returned (CC-RERUN-FINDINGS fix 1). A whole node,
#: not ``s{.*}``: the scope prover refuses a map of every property for a non-admin.
SAMPLES_CYPHER = ("MATCH (s:Sample) WHERE s.uuid IN $uids "
                  "RETURN s AS sample ORDER BY s.uuid, s.id LIMIT 5000")
#: Never written to samples.csv. ``parent_titles`` and ``parent_title_hashes`` are
#: ``graph_scope.HIDDEN_SAMPLE_PROPERTIES``, which a non-admin may not read: staging drops them
#: itself rather than rely on any other layer. ``search_text`` repeats every other value;
#: ``source_hash`` is the sync's bookkeeping.
_DROPPED_SAMPLE_PROPERTIES = frozenset({"parent_titles", "parent_title_hashes", "search_text",
                                        "source_hash"})
#: The first columns of samples.csv; the rest follow in name order.
_SAMPLE_LEAD_COLUMNS = ("uuid", "id", "type", "title", "project_ids")
#: Where a row names its sample: the graph's ``uuid``, a REST row's ``uid``, the metadata ``UID``.
_UID_KEYS = ("uuid", "uid", "UID")


# --------------------------------------------------------------------------- reading

def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _read_json(path: Path | None) -> Any:
    if path is None:
        return None
    try:
        with open(path, "rb") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _rows_of(payload: Any) -> list:
    """The records of a graph or REST result, whichever shape it has."""
    if isinstance(payload, list):
        return payload
    payload = _as_dict(payload)
    for container in (payload.get("data"), payload):
        if isinstance(container, list):
            return container
        if isinstance(container, dict):
            for key in ("rows", "samples", "nodes", "data"):
                if isinstance(container.get(key), list):
                    return container[key]
    return []


def _flatten(row: Any) -> dict:
    """One CSV row: a JSON:API resource's id, type and attributes, or a flat row."""
    if not isinstance(row, dict):
        return {"value": row}
    if isinstance(row.get("attributes"), dict):
        out = {k: row[k] for k in ("id", "type") if k in row}
        out.update(row["attributes"])
        return out
    return dict(row)


def _cell(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return value


def rows_csv(rows: list, columns: list[str] | None = None) -> str:
    flat = [_flatten(r) for r in rows]
    if columns is None:
        columns = []
        for row in flat:
            for key in row:
                if key not in columns:
                    columns.append(str(key))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in flat:
        writer.writerow([_cell(row.get(c)) for c in columns])
    return buf.getvalue()


def search_details(entry: dict, bundle: dict, *, safe_ns_path: SafePath) -> dict:
    """What the Search details panel showed for one NS turn, as one JSON object.

    ``entity`` is the entity agent's full output when the graph debug file is readable
    (graph turns write it), else the codes the chat_log kept (every turn keeps them).
    """
    parser = _as_dict(bundle.get("parser_plan"))
    graph_plan = _as_dict(bundle.get("graph_plan"))
    graph_result = _as_dict(bundle.get("graph_result"))
    api_plan = _as_dict(bundle.get("api_plan"))

    entity: Any = None
    debug_path = bundle.get("graph_debug_path") or _as_dict(bundle.get("paths")).get("graph_debug_path")
    if isinstance(debug_path, str) and debug_path:
        safe = safe_ns_path(debug_path)
        entity = _as_dict(_read_json(safe)).get("entity_output") if safe else None
    if entity is None:
        entity = entry.get("key_entities")

    details: dict[str, Any] = {
        "turn_id": _int(entry.get("turn_id")),
        "bundle_id": _int(bundle.get("id")),
        "route": "nextseek_query",
        "ts": entry.get("ts") or bundle.get("timestamp"),
        "user_query": bundle.get("user_query") or entry.get("user_query"),
        "mode": bundle.get("mode") or entry.get("mode"),
        "entity": entity,
        "parser": {k: parser.get(k) for k in ("mode", "intent_summary", "target_endpoint", "filters")
                   if parser.get(k) not in (None, "", [], {})} or None,
    }
    if graph_plan.get("cypher"):
        details["graph"] = {"cypher": graph_plan.get("cypher"),
                            "explanation": graph_plan.get("explanation"),
                            "parameters": graph_plan.get("parameters") or {}}
    if graph_result:
        details["neo4j"] = {k: graph_result.get(k)
                            for k in ("ok", "count", "total", "truncated", "limit", "error", "scope")
                            if k in graph_result}
    endpoint = api_plan.get("endpoint") or bundle.get("endpoint")
    if endpoint and endpoint != "neo4j":
        details["api"] = {"endpoint": endpoint,
                          "method": api_plan.get("method") or bundle.get("method"),
                          "request_body": bundle.get("request_body") or api_plan.get("requestBody"),
                          "query_params": bundle.get("query_params") or api_plan.get("queryParameters")}
    reply = bundle.get("terminal_reply") or bundle.get("reply") or entry.get("assistant_reply")
    details["reply"] = reply
    return details


# --------------------------------------------------------------------------- writing

def _copy(src: Path, dst: Path) -> None:
    """Copy unless ``dst`` already holds this exact file (same size and mtime)."""
    try:
        s, d = src.stat(), dst.stat()
        if s.st_size == d.st_size and int(s.st_mtime) == int(d.st_mtime):
            return
    except OSError:
        pass
    shutil.copy2(src, dst)


def _write_text(dst: Path, text: str) -> None:
    try:
        if dst.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dst)


def _file_name(name: Any, fallback: str) -> str:
    name = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = _UNSAFE_NAME.sub("_", name)
    return fallback if name in ("", ".", "..") else name


def _stage_file(src: Path, turn_dir: Path, name: str, label: str,
                files: list[dict], skipped: list[dict]) -> None:
    try:
        size = src.stat().st_size
    except OSError:
        skipped.append({"file": name, "reason": "missing_on_disk"})
        return
    if size > MAX_FILE_BYTES:
        skipped.append({"file": name, "reason": f"too_large ({size} bytes)"})
        return
    dst = turn_dir / name
    if any(f["file"] == name for f in files):
        dst = turn_dir / f"{dst.stem}-{len(files)}{dst.suffix}"
    _copy(src, dst)
    files.append({"file": dst.name, "holds": label, "bytes": size})


def _ns_files(bundle: dict) -> list[tuple[str, str, str]]:
    """``(stored path, display name, label)`` for every download an NS bundle offered."""
    out: list[tuple[str, str, str]] = []
    for entry in bundle.get("files") or []:
        if not isinstance(entry, dict) or entry.get("kind") in _INTERNAL_FILE_KINDS:
            continue
        path = entry.get("path")
        if isinstance(path, str) and path:
            out.append((path, entry.get("filename") or Path(path).name,
                        entry.get("label") or entry.get("key") or "download"))
    raw = bundle.get("raw_result_path") or _as_dict(bundle.get("paths")).get("raw_result_path")
    if isinstance(raw, str) and raw and all(p != raw for p, _, _ in out):
        out.append((raw, Path(raw).name, "Full API result JSON"))
    for key, value in _as_dict(bundle.get("report_saved_files")).items():
        paths = [value] if isinstance(value, str) else (
            list(value) if isinstance(value, (list, tuple)) else [])
        for p in paths:
            if isinstance(p, str) and p and "://" not in p and all(q != p for q, _, _ in out):
                out.append((p, Path(p).name, str(key).replace("_", " ")))
    return out


def _row_uid(row: Any) -> str | None:
    flat = _flatten(row)
    for key in _UID_KEYS:
        value = flat.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def sample_uids(rows: list) -> list[str]:
    """The sample UIDs the rows name, in row order, each once. [] for a count or grouped result."""
    seen: dict[str, None] = {}
    for row in rows:
        uid = _row_uid(row)
        if uid is not None:
            seen.setdefault(uid, None)
    return list(seen)


def _uids_key(uids: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(uids)).encode("utf-8")).hexdigest()


def _sample_rows(result: Any) -> list[dict]:
    """One dict per sample node in a ``SAMPLES_CYPHER`` result, minus the dropped properties."""
    out = []
    for record in _as_dict(result).get("data") or []:
        node = _as_dict(record).get("sample")
        if isinstance(node, dict):
            out.append({k: v for k, v in node.items() if str(k) not in _DROPPED_SAMPLE_PROPERTIES})
    return out


def _sample_columns(rows: list[dict]) -> list[str]:
    keys = {str(k) for row in rows for k in row}
    lead = [k for k in _SAMPLE_LEAD_COLUMNS if k in keys]
    return lead + sorted(keys - set(lead))


def _stage_samples(uids: list[str], turn_dir: Path, graph_query: GraphQuery,
                   previous: dict, files: list[dict], skipped: list[dict]) -> dict:
    """Write ``samples.csv`` for ``uids`` (once per turn: kept while the UID set is the same)."""
    wanted = uids[:MAX_SAMPLE_UIDS]
    key = _uids_key(wanted)
    dst = turn_dir / SAMPLES_CSV
    if previous.get("samples_key") == key and dst.is_file() and not dst.is_symlink():
        count = _int(previous.get("samples_count")) or 0
    else:
        try:
            result = graph_query(SAMPLES_CYPHER, {"uids": wanted})
        except _GraphError:
            result = {"ok": False, "error": "skipped"}
        except Exception:  # noqa: BLE001 - a graph outage must not cost the turn its other files
            logger.warning("prior turns: the samples read failed", exc_info=True)
            result = {"ok": False, "error": "raised"}
        result = _as_dict(result)
        if not result.get("ok"):
            refused = _as_dict(result.get("scope")).get("decision") == "refused"
            skipped.append({"file": SAMPLES_CSV,
                            "reason": "graph_scope_refused" if refused else "graph_error"})
            return {}
        rows = _sample_rows(result)
        if not rows:
            skipped.append({"file": SAMPLES_CSV, "reason": "no_matching_samples"})
            return {}
        count = len({str(r.get("uuid")) for r in rows if r.get("uuid")})
        _write_text(dst, rows_csv(rows, _sample_columns(rows)))
    capped = f" (the first {len(wanted)} of {len(uids)} UIDs)" if len(wanted) < len(uids) else ""
    files.append({"file": SAMPLES_CSV,
                  "holds": f"every stored property of the {count} samples these rows name{capped}, "
                           "one row per sample (uuid, id, type, title, project_ids, then each "
                           "metadata attribute): answer a follow-up about their sex, species, "
                           "genotype, dates or any other attribute from this file"})
    return {"samples_key": key, "samples_count": count}


def _stage_ns_turn(entry: dict, bundle: dict, turn_dir: Path, *, safe_ns_path: SafePath,
                   graph_query: GraphQuery | None = None, previous: dict | None = None) -> dict:
    files: list[dict] = []
    skipped: list[dict] = []
    details = search_details(entry, bundle, safe_ns_path=safe_ns_path)
    _write_text(turn_dir / "search_details.json",
                json.dumps(details, indent=2, default=str, ensure_ascii=False) + "\n")
    files.append({"file": "search_details.json",
                  "holds": "Search details: entity, parser mode and intent, the graph cypher "
                           "with its explanation and parameters and the neo4j count, or the "
                           "REST request"})

    rows: list = []
    graph_result = _as_dict(bundle.get("graph_result"))
    if isinstance(graph_result.get("data"), list):
        rows = graph_result["data"]
        payload = {"cypher": details.get("graph", {}).get("cypher"),
                   "parameters": details.get("graph", {}).get("parameters"),
                   "count": graph_result.get("count"), "total": graph_result.get("total"),
                   "truncated": bool(graph_result.get("truncated")), "rows": rows}
    else:
        raw = bundle.get("raw_result_path") or _as_dict(bundle.get("paths")).get("raw_result_path")
        safe = safe_ns_path(raw) if isinstance(raw, str) and raw else None
        full = _read_json(safe) if safe else bundle.get("api_result_full")
        rows = _rows_of(full)
        total = _as_dict(_as_dict(full).get("data")).get("total") if isinstance(full, dict) else None
        payload = {"api": details.get("api"), "total": total, "rows": rows}
    if rows:
        _write_text(turn_dir / "rows.json",
                    json.dumps(payload, indent=1, default=str, ensure_ascii=False) + "\n")
        files.append({"file": "rows.json",
                      "holds": f"all {len(rows)} rows this turn returned, with the query "
                               "that produced them"})
        _write_text(turn_dir / "rows.csv", rows_csv(rows))
        files.append({"file": "rows.csv", "holds": f"the same {len(rows)} rows as CSV"})

    uids = sample_uids(rows)
    samples: dict = {}
    if uids and graph_query is not None:
        samples = _stage_samples(uids, turn_dir, graph_query, previous or {}, files, skipped)

    for stored, display, label in _ns_files(bundle):
        name = _file_name(display, "download")
        safe = safe_ns_path(stored)
        if safe is None:
            skipped.append({"file": name, "reason": "outside_artifact_root"})
            continue
        if safe.is_symlink() or not safe.is_file():
            skipped.append({"file": name, "reason": "missing_on_disk"})
            continue
        _stage_file(safe, turn_dir, name, label, files, skipped)

    neo4j = _as_dict(details.get("neo4j"))
    return {
        "route": "nextseek_query", "mode": details.get("mode"),
        "count": neo4j.get("count") if neo4j else len(rows) or None,
        "total": neo4j.get("total"), "truncated": neo4j.get("truncated"),
        "sample_uids": len(uids), **samples,
        "files": files, "skipped": skipped,
    }


def _stage_cc_turn(entry: dict, turn_dir: Path, *, cc_artifacts_root: Path | None) -> dict:
    files: list[dict] = []
    skipped: list[dict] = []
    reply = str(entry.get("assistant_reply") or "")
    if reply:
        _write_text(turn_dir / "answer.md", reply + "\n")
        files.append({"file": "answer.md", "holds": "the answer this turn gave"})
    run_id = entry.get("cc_run_id")
    if cc_artifacts_root is not None and isinstance(run_id, str) and _RUN_ID.fullmatch(run_id):
        root = cc_artifacts_root.resolve()
        run_dir = cc_artifacts_root / run_id
        if run_dir.is_dir() and not run_dir.is_symlink():
            found = []
            for candidate in sorted(run_dir.rglob("*")):
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                try:
                    candidate.resolve().relative_to(root / run_id)
                except (ValueError, OSError):
                    skipped.append({"file": candidate.name, "reason": "outside_artifact_root"})
                    continue
                found.append(candidate)
            if len(found) > 1:
                found = [p for p in found if p.name != "artifacts.zip"]
            for path in found:
                _stage_file(path, turn_dir, _file_name(path.name, "artifact"),
                            "a file this turn wrote to /data/scratch and published", files, skipped)
    return {"route": "container_cc", "mode": "cc", "files": files, "skipped": skipped}


def _turns_to_stage(chat_log: list, bundles: dict) -> list[tuple[dict, dict | None]]:
    """``(entry, bundle)`` for the answered engine turns, oldest first."""
    out: list[tuple[dict, dict | None]] = []
    for entry in chat_log or []:
        if not isinstance(entry, dict) or _int(entry.get("turn_id")) is None:
            continue
        if entry.get("status", "completed") != "completed":
            continue
        if entry.get("mode") == "cc" or entry.get("router_choice") == "container_cc":
            out.append((entry, None))
            continue
        bundle = bundles.get(_int(entry.get("bundle_id")))
        if bundle is not None:
            out.append((entry, bundle))
    return out


def _render_manifest(turns: list[dict]) -> str:
    lines = [
        "# Previous turns of this chat",
        "",
        f"Read-only, staged by NExtSEEK before this turn. Newest first; at most {MAX_TURNS} turns.",
        "Start a follow-up here: the rows are in `rows.json` / `rows.csv` (analyse them directly),",
        "every stored property of the samples they name is in `samples.csv` where one is listed, and",
        "the stored cypher is in `search_details.json` (to change the search, hand it to",
        "`nextseek-graph --query` with the one change asked for). Your own earlier Container-CC",
        "turns are here too: their answer and every file they wrote, to read instead of redoing.",
        "",
    ]
    for t in turns:
        head = f"## turn {t['turn_id']} ({t['route']}"
        head += f", {t['mode']})" if t.get("mode") else ")"
        lines.append(f"{head}: {t['user_query']}")
        lines.append("")
        lines.append(f"- folder: `{CONTAINER_PATH}/{t['folder']}/`")
        if t.get("count") is not None:
            extra = []
            if t.get("total") is not None:
                extra.append(f"total {t['total']}")
            if t.get("truncated") is not None:
                extra.append(f"truncated={t['truncated']}")
            lines.append(f"- returned {t['count']} rows" + (f" ({', '.join(extra)})" if extra else ""))
        if t.get("route") == "nextseek_query" and "sample_uids" in t:
            if t["sample_uids"]:
                lines.append(f"- sample UIDs: {t['sample_uids']}, in `rows.csv`")
            elif t.get("count"):
                lines.append("- no sample UIDs: this turn returned a count or grouped rows. The cypher in "
                             "`search_details.json` is the whole definition of its samples: to list them or "
                             "break them down, change only its RETURN and keep every MATCH and WHERE.")
        for f in t["files"]:
            lines.append(f"- `{f['file']}`: {f['holds']}")
        for s in t["skipped"]:
            lines.append(f"- not staged: `{s['file']}` ({s['reason']})")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


MEMORY_HEADER = "## Previous turns of this chat: files and search details"


def memory_pointer(manifest: dict | None) -> str:
    """The lines the per-turn memory file carries when turns were staged; "" when none."""
    turns = (manifest or {}).get("turns") or []
    if not turns:
        return ""
    newest = turns[0]
    return "\n".join([
        MEMORY_HEADER,
        "",
        f"Every answered turn of this chat is staged read-only under `{CONTAINER_PATH}/`. "
        f"Read `{CONTAINER_PATH}/{MANIFEST_MD}` first: it says which turn asked what and "
        "which file holds what.",
        f"The newest is turn {newest['turn_id']} ({newest['route']}): "
        f"`{CONTAINER_PATH}/{newest['folder']}/`.",
        "For a follow-up, start from that turn: `rows.json` / `rows.csv` hold every row it "
        "returned (analyse them directly; do not re-run a search for rows you already have), "
        "`samples.csv` holds every stored property of those samples, "
        "and `search_details.json` holds the cypher it ran (to change the search, hand that "
        "cypher to `nextseek-graph --query` with the one change asked for).",
    ])


class _GraphError(Exception):
    """The graph read failed (not refused): the samples reads left in this staging are skipped."""


def _one_failure_stops(graph_query: GraphQuery) -> GraphQuery:
    """``graph_query``, except that after one read that raised or failed (not a scope refusal) the
    rest of this staging raises at once. Staging runs before the agent starts, so a graph that is
    down costs one failed read per turn, not one per staged NS turn."""
    failed = False

    def query(cypher: str, parameters: dict) -> dict:
        nonlocal failed
        if failed:
            raise _GraphError("an earlier samples read in this staging failed")
        try:
            result = graph_query(cypher, parameters)
        except Exception:
            failed = True
            raise
        result = _as_dict(result)
        if not result.get("ok") and _as_dict(result.get("scope")).get("decision") != "refused":
            failed = True
        return result
    return query


def _previous_entries(dest_dir: Path) -> dict[str, dict]:
    """The last staging's manifest entries by folder: what ``samples.csv`` was read for."""
    previous = _as_dict(_read_json(dest_dir / MANIFEST_JSON))
    return {str(t.get("folder")): t for t in previous.get("turns") or [] if isinstance(t, dict)}


def stage_prior_turns(*, chat_log: list, results_history: list, dest_dir: Path,
                      safe_ns_path: SafePath | None = None,
                      cc_artifacts_root: Path | None = None,
                      graph_query: GraphQuery | None = None,
                      max_turns: int = MAX_TURNS) -> dict | None:
    """Stage the chat's last ``max_turns`` answered turns into ``dest_dir``.

    ``graph_query`` reads the ``samples.csv`` of an NS turn whose rows name samples, once per
    turn (a later staging keeps the file while the UIDs are the same). Without it no
    ``samples.csv`` is written.

    Returns the manifest dict, or None when there is nothing to stage (the directory is
    then emptied, and the caller mounts nothing). Never raises.
    """
    dest_dir = Path(dest_dir)
    try:
        if safe_ns_path is None:
            from NessieAI.ns.artifacts import _safe_artifact_path as safe_ns_path
        bundles = {_int(b.get("id")): b for b in (results_history or [])
                   if isinstance(b, dict) and _int(b.get("id")) is not None}
        wanted = _turns_to_stage(chat_log, bundles)[-max_turns:] if max_turns > 0 else []
        if not wanted:
            if dest_dir.is_dir():
                shutil.rmtree(dest_dir, ignore_errors=True)
            return None
        dest_dir.mkdir(parents=True, exist_ok=True)
        previous = _previous_entries(dest_dir)
        if graph_query is not None:
            graph_query = _one_failure_stops(graph_query)
        keep: set[str] = set()
        turns: list[dict] = []
        for entry, bundle in reversed(wanted):
            folder = f"turn-{_int(entry['turn_id']):02d}"
            keep.add(folder)
            turn_dir = dest_dir / folder
            turn_dir.mkdir(exist_ok=True)
            try:
                if bundle is None:
                    staged = _stage_cc_turn(entry, turn_dir, cc_artifacts_root=cc_artifacts_root)
                else:
                    staged = _stage_ns_turn(entry, bundle, turn_dir, safe_ns_path=safe_ns_path,
                                            graph_query=graph_query,
                                            previous=previous.get(folder))
            except Exception as exc:  # noqa: BLE001 - one odd turn must not lose the rest
                logger.warning("prior turns: turn %s not staged", entry.get("turn_id"), exc_info=True)
                staged = {"route": entry.get("router_choice") or "unknown", "mode": entry.get("mode"),
                          "files": [], "skipped": [{"file": "*", "reason": type(exc).__name__}]}
            keep_files = {f["file"] for f in staged["files"]}
            for stale in turn_dir.iterdir():
                if stale.is_file() and stale.name not in keep_files:
                    stale.unlink()
            turns.append({"turn_id": entry["turn_id"], "folder": folder,
                          "user_query": str(entry.get("user_query") or ""), **staged})
        for child in dest_dir.iterdir():
            if child.is_dir() and _TURN_DIR.fullmatch(child.name) and child.name not in keep:
                shutil.rmtree(child, ignore_errors=True)
        manifest = {"schema_version": "prior_turns/v1", "container_path": CONTAINER_PATH,
                    "turns": turns}
        _write_text(dest_dir / MANIFEST_JSON,
                    json.dumps(manifest, indent=2, default=str, ensure_ascii=False) + "\n")
        _write_text(dest_dir / MANIFEST_MD, _render_manifest(turns))
        for path in [dest_dir, *dest_dir.rglob("*")]:
            try:
                os.chmod(path, 0o755 if path.is_dir() else 0o644)
            except OSError:
                pass
        return manifest
    except Exception:  # noqa: BLE001 - staging must never fail the user's turn
        logger.exception("prior turns: staging failed; the turn runs without them")
        return None
