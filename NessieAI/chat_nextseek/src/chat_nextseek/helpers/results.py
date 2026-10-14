"""API result shaping/normalization helpers. Moved from helpers.py during the Phase 2 src/ restructure."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .text import strip_html
from .uid_check import UID_RE
from ..artifacts import load_api_result_full

# tool_nextseek_api_request hard-defaults this. A search matching more rows than this
# silently returns one page, so it must be disclosed rather than left implicit.
DEFAULT_API_PAGE_SIZE = 1000


#: `POST samples/retrieve/` (and its alias `admin/samples/retrieve/`) answers `{total_samples,
#: total_sample_types, total_children, failed_uids, data: [{sample_type, n_samples, samples: [...]}],
#: lineage_complete}` (`SampleRetrieveResponse`, `nextseek_api/models.py`). `data["data"]` is a list,
#: so every row-counting and row-previewing helper below "recognised" it and then
#: counted and previewed the sample TYPES rather than the samples.
_GROUP_LABEL_KEY = "sample_type"
_GROUP_RECORDS_KEY = "samples"


def _grouped_records(data: object) -> list[dict] | None:
    """The groups of a grouped-by-sample-type body, or None for any other shape.

    Deliberately exact rather than structural: it matches only a list whose every
    member carries both a ``sample_type`` label and a ``samples`` list. A SEEK
    JSON:API passthrough also puts a list at ``data["data"]`` (its members carry
    ``id``/``type``/``attributes``), and that one must keep its existing handling.
    """
    if not isinstance(data, dict):
        return None
    groups = data.get("data")
    if not isinstance(groups, list) or not groups:
        return None
    for group in groups:
        if not isinstance(group, dict):
            return None
        if _GROUP_LABEL_KEY not in group or not isinstance(group.get(_GROUP_RECORDS_KEY), list):
            return None
    return groups


def _flatten_groups(groups: list[dict]) -> list[dict]:
    """Records from every group, round-robin, each tagged with its group label.

    Round-robin, not group order, so a preview of a mixed lineage shows every type
    present. B7 is the reply that reported 1,904 lineage records and showed none of
    the PAT samples the user asked about; a preview drawn from the first group only
    would reproduce that.
    """
    tagged = [
        [
            {_GROUP_LABEL_KEY: group.get(_GROUP_LABEL_KEY), **record}
            for record in group.get(_GROUP_RECORDS_KEY) or []
            if isinstance(record, dict)
        ]
        for group in groups
    ]
    out: list[dict] = []
    for index in range(max((len(rows) for rows in tagged), default=0)):
        for rows in tagged:
            if index < len(rows):
                out.append(rows[index])
    return out


def _is_empty(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _drop_empty(record: dict) -> dict:
    """The record without its empty fields (None, "", [], {}), one level into a nested dict too: a retrieve record
    holds every attribute under ``metadata`` (R8). The group label stays."""
    out = {}
    for k, v in record.items():
        if isinstance(v, dict):
            v = {ik: iv for ik, iv in v.items() if not _is_empty(iv)}
        if k == _GROUP_LABEL_KEY or not _is_empty(v):
            out[k] = v
    return out


#: Named records shown in full in a retrieve preview; any more are listed by UID and type in ``also_named`` (R8).
_PINNED_MAX = 20


def _record_uid(record: dict) -> str:
    return next((str(record[f]) for f in ("uid", "UID", "uuid", "sample_uid", "id") if record.get(f) is not None), "?")


def _named_identifiers(api_plan: dict | None) -> list[str]:
    """Identifiers the request named (a retrieve body's ``identifiers``), lower-cased."""
    body = (api_plan or {}).get("requestBody")
    ids = body.get("identifiers") if isinstance(body, dict) else None
    if not isinstance(ids, list):
        return []
    return [str(i).strip().lower() for i in ids if str(i).strip()]


def _record_keys(record: dict) -> set[str]:
    """Every identifier a record answers to: its uid fields and its SEEK id."""
    keys = set()
    for field in ("uid", "UID", "uuid", "sample_uid", "id"):
        value = record.get(field)
        if value is not None and not isinstance(value, (dict, list)):
            keys.add(re.sub(r"<[^>]+>", "", str(value)).strip().lower())
    return keys


def _order_named_first(flat: list[dict], named: list[str]) -> tuple[list[dict], int]:
    """Records the request named first (in the order named), then the rest. Returns the order and the named count."""
    if not named:
        return flat, 0
    rank = {n: i for i, n in enumerate(named)}
    hits = []
    rest = []
    for record in flat:
        found = [rank[k] for k in _record_keys(record) if k in rank]
        (hits if found else rest).append((min(found), record) if found else record)
    hits.sort(key=lambda pair: pair[0])
    return [r for _, r in hits] + rest, len(hits)


def _group_record_count(groups: list[dict]) -> int:
    return sum(len(group.get(_GROUP_RECORDS_KEY) or []) for group in groups)


def api_row_count(api_result: dict | None) -> int | None:
    """How many rows an API result actually returned, or None if not countable.

    ``api_result_meta`` used to carry only ok/status_code/url, so nothing
    downstream — the debug panel, the chat log, or the test harness — could tell
    a search that returned 195 rows from one that returned 0. Both looked like
    ``ok: true``. Counting here makes the OUTCOME of a search observable rather
    than just its plumbing.
    """
    if not isinstance(api_result, dict):
        return None
    data = api_result.get("data")
    if isinstance(data, list):
        return len(data)
    if not isinstance(data, dict):
        return None
    groups = _grouped_records(data)
    if groups is not None:
        return _group_record_count(groups)
    # advanced_search/new_search pin rows under 'samples'; graph under 'nodes'.
    for key in ("samples", "rows", "nodes", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return len(value)
    # A totals-only payload (e.g. a count query) still reports a number.
    for key in ("total", "total_samples", "total_nodes", "count"):
        value = data.get(key)
        if isinstance(value, int):
            return value
    return None


def _result_total(api_result: dict | None) -> int | None:
    """The API's reported grand total, independent of how many rows came back."""
    if not isinstance(api_result, dict):
        return None
    data = api_result.get("data")
    if not isinstance(data, dict):
        return None
    for key in ("total", "total_samples", "total_nodes"):
        value = data.get(key)
        if isinstance(value, int):
            return value
    return None


def _rows_actually_returned(api_result: dict | None) -> int | None:
    """Length of the returned row list, or None when the payload carries no list."""
    if not isinstance(api_result, dict):
        return None
    data = api_result.get("data")
    if isinstance(data, list):
        return len(data)
    if not isinstance(data, dict):
        return None
    groups = _grouped_records(data)
    if groups is not None:
        return _group_record_count(groups)
    for key in ("samples", "rows", "nodes", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return len(value)
    return None


def build_result_disclosure(
    api_result: dict | None,
    api_plan: dict | None = None,
    preview_rows: int | None = None,
) -> dict:
    """
    Flags describing how the rows the chatter sees differ from what the user asked for.

    Three silences this removes.

    1. ``tool_nextseek_api_request`` hard-defaults ``page_size: 1000``, so a search
       matching 2,057 records returns 1,000 rows and reports total 2,057. The chatter
       faithfully said "2,057 records" while row_count was 1000, and nothing said the
       rows were a page.
    2. The LLM-context slim keeps 5 rows. ``/nextseek_api/assays/`` is a SEEK JSON:API
       passthrough whose body carries no total at all, so after slimming the chatter
       held 5 rows and no other number — and answered "You have access to 5 assays"
       for a 324-row result (task 833). Reporting the preview length as the answer was
       the only thing it could do with what it was given.
    3. When the retry ladder substitutes a different search text, the rows are not the
       user's terms at all.

    None of these is a hallucination — the defect is that nothing told the chatter.
    """
    disclosure: dict = {}

    total = _result_total(api_result)
    rows = _rows_actually_returned(api_result)
    if rows is not None:
        disclosure["rows_returned"] = rows
    if isinstance(api_plan, dict):
        page_size = (api_plan.get("queryParameters") or {}).get("page_size")
        if page_size is None and rows is not None:
            page_size = DEFAULT_API_PAGE_SIZE
        if page_size is not None:
            disclosure["page_size"] = page_size

    # Capped by the API's page size: more matched than came back.
    if total is not None and rows is not None and total > rows:
        disclosure["result_capped"] = True
        disclosure["total_matching"] = total

    # Capped by the LLM-context slim: more came back than the chatter can see.
    if preview_rows is not None and rows is not None and preview_rows < rows:
        disclosure["preview_rows"] = preview_rows
        disclosure["result_capped"] = True
        disclosure.setdefault("total_matching", total if total is not None else rows)

    if isinstance(api_plan, dict) and api_plan.get("retry_substituted_search"):
        disclosure["search_text_substituted"] = api_plan["retry_substituted_search"]

    return disclosure


def api_result_meta_truncation(api_result: dict | None, api_plan: dict | None = None) -> dict:
    """The subset of ``build_result_disclosure`` that belongs on ``api_result_meta``.

    T1.10 put ``result_capped`` / ``total_matching`` into the SLIMMED LLM payload, so
    the chatter is told when it is holding a page rather than a result. But
    ``api_result_meta`` is what the nessie harness asserts on, and it carries only
    ``row_count``. So no criterion can distinguish a complete 1,000-row answer from a
    1,000-row page of 1,179, and none ever has: ``advanced.find_me_mice`` reported
    exactly the hard-coded ``DEFAULT_API_PAGE_SIZE`` in the seed-0 run and scored green.

    This mirrors what the graph side already gained (``count`` / ``total`` /
    ``truncated``), which is what made the 10,688 cap checkable rather than invisible.

    Deliberately ignores the LLM-context preview cap: that one describes what the
    chatter could see, not what the API returned, and the harness asserts on the API.
    """
    disclosure = build_result_disclosure(api_result, api_plan)
    return {
        "total_matching": disclosure.get("total_matching"),
        "truncated": bool(disclosure.get("result_capped", False)),
    }


def build_api_result_meta(api_result: dict | None, api_plan: dict | None = None,
                          *, bundle_id: int | None = None) -> dict:
    """The full ``debug_payload['api_result_meta']`` block.

    Assembled here rather than inline in the orchestrator so the truncation fields
    have a seam a test can reach. The graph side's equivalent was written inline and
    went two waves without a unit test.
    """
    api_result = api_result if isinstance(api_result, dict) else {}
    return {
        "ok": api_result.get("ok"),
        "status_code": api_result.get("status_code"),
        "url": api_result.get("url"),
        # An ok:true search that returned nothing is not a success; make the row
        # count visible so callers can tell those apart.
        "row_count": api_row_count(api_result),
        "bundle_id": bundle_id,
        **api_result_meta_truncation(api_result, api_plan),
    }


def slim_api_result_for_llm(
    api_result: dict,
    max_rows: int = 5,
    max_chars: int = 5000,
    api_plan: dict | None = None,
) -> dict:
    """
    Trim API results to keep LLM prompts small while preserving key totals and a few example rows.
    Caps row count and total serialized size, substituting a preview with truncation metadata when oversized.

    ``api_plan`` is optional and only used to disclose capping and search-text
    substitution; omitting it preserves the previous behaviour exactly.
    """
    data = api_result.get("data", {})
    new_data = data
    preview_items = None
    preview_key = None
    preview_rows: int | None = None
    #: The key that says the preview is not the whole list, so the size shrink below
    #: can set it. Whichever branch builds a preview names its own.
    truncated_key: str | None = None

    groups = _grouped_records(data)
    if groups is not None:
        # The grouped body, rebuilt so that both numbers in it mean samples.
        #
        # Measured before this branch existed, on a 101-sample / 3-type lineage:
        # `max_rows` trimmed 3 groups to 3 groups, so the size cap never bound and
        # the "slimmed" payload came out LARGER than the raw one (28,232 -> 28,342
        # chars) for a budget of 5,000. `rows_returned` was 3.
        #
        # What replaces it is the two facts a reply actually needs and could not
        # reach: how many samples of each type there are (B7's missing sentence,
        # "1,904 records, 40 of them PAT"), and a preview whose every record names
        # the type it came from (B8's fix, "name types from the query that ran").
        flat = [_drop_empty(r) for r in _flatten_groups(groups)]
        # Records the request named (retrieve by UID) always lead the preview and
        # are never cut by the row cap, so a second named record cannot drop out.
        # A UID named without its suffix comes back under its stored spelling (`resolved_as`), and is pinned as that.
        resolved = {str(k).strip().lower(): str(v).strip().lower()
                    for k, v in ((data.get("resolved_as") if isinstance(data, dict) else None) or {}).items()}
        flat, pinned = _order_named_first(flat, [resolved.get(n, n) for n in _named_identifiers(api_plan)])
        shown, also_named = flat, []
        if pinned > _PINNED_MAX:
            also_named = [f"{_record_uid(r)} ({r.get(_GROUP_LABEL_KEY)})" for r in flat[_PINNED_MAX:pinned]]
            shown, pinned = flat[:_PINNED_MAX] + flat[pinned:], _PINNED_MAX
        preview_key = "samples_preview"
        preview_items = shown[:max(max_rows, pinned)]
        preview_rows = len(preview_items)
        new_data = {
            **{k: v for k, v in data.items() if k != "data"},
            "samples_by_type": {
                str(group.get(_GROUP_LABEL_KEY)): (
                    group.get("n_samples")
                    if isinstance(group.get("n_samples"), int)
                    else len(group.get(_GROUP_RECORDS_KEY) or [])
                )
                for group in groups
            },
            "samples_preview": preview_items,
            "samples_truncated": len(preview_items) < len(flat),
            **({"also_named": "also named, not shown in full: " + ", ".join(also_named)} if also_named else {}),
        }
        truncated_key = "samples_truncated"
    elif isinstance(data, dict):
        if isinstance(data.get("rows"), list):
            preview_key = "rows"
        elif isinstance(data.get("nodes"), list):
            preview_key = "nodes"
        elif isinstance(data.get("data"), list):
            preview_key = "data"

        if preview_key:
            preview_items = data[preview_key][:max_rows]
            new_data = {**data, preview_key: preview_items}

    slimmed = {**api_result, "data": new_data}

    text = json.dumps(slimmed, default=str)
    if len(text) > max_chars and groups is None:
        total = None
        total_key = "total"
        if isinstance(new_data, dict):
            for key in ("total", "total_samples", "total_nodes"):
                if new_data.get(key) is not None:
                    total = new_data.get(key)
                    total_key = key
                    break
        # Keep a small preview even when truncating for size
        preview_items = preview_items if preview_items is not None else []
        preview_label = f"{preview_key}_preview" if preview_key else "items_preview"
        # `<list>_truncated` is built from the ORIGINAL key ("rows_truncated"), not
        # from the relabelled preview key, which is how it has always read.
        truncated_key = f"{preview_key or 'items'}_truncated"
        slimmed = {
            **{k: v for k, v in api_result.items() if k != "data"},
            "data": {
                total_key: total,
                preview_label: preview_items,
                truncated_key: True,
                "note": f"Result truncated for LLM context (>{max_chars} chars).",
            },
        }
        preview_key = preview_label

    # Relabelling the keys is not trimming. The branch above rewrote the envelope
    # and kept every previewed row whole, so a result whose individual rows are
    # large stayed over budget: nine 3 KB rows slimmed to 15,314 chars against a
    # 5,000-char cap. Drop previewed rows until it fits, never below one -- an
    # empty preview leaves the chatter with a total and nothing to name, which is
    # the state that produces a generic reply.
    if preview_key:
        slimmed, preview_rows = _shrink_preview_to_budget(
            slimmed, preview_key, max_chars, truncated_key=truncated_key,
            pinned=pinned if groups is not None else 0,
        )

    # Row counts come from the ORIGINAL result; `preview_rows` is what actually
    # survived into the payload the chatter reads. Passing both is what lets the
    # chatter say "324, showing 5" instead of "5".
    if preview_rows is None:
        preview_rows = _rows_actually_returned(slimmed)
    slimmed.update(build_result_disclosure(api_result, api_plan, preview_rows=preview_rows))
    slimmed.update(_rest_list_flags(api_result, api_plan))
    return slimmed


#: The two REST answers that are a single record or a whole list rather than a sample page (R4, U3b).
_PEOPLE_LIST = "/nextseek_api/people/"
_SOPS_RECORD = "/nextseek_api/sops/"


def _rest_list_flags(api_result: dict, api_plan: dict | None) -> dict:
    """``full_list_attached`` + ``rows_returned`` for the people list (the table under the reply holds every row, so
    the chatter gives the total and never "the first N"), with ``match_terms`` + ``matching_rows`` when the request
    looks for names; ``download_links`` for one SOP record (the file it offers)."""
    endpoint = str((api_plan or {}).get("endpoint") or "").split("?", 1)[0]
    data = api_result.get("data") if isinstance(api_result, dict) else None
    if endpoint.rstrip("/") + "/" == _PEOPLE_LIST:
        rows = data.get("data") if isinstance(data, dict) else data
        if isinstance(rows, list):
            flags = {"full_list_attached": True, "rows_returned": len(rows)}
            terms = [str(t) for t in (api_plan or {}).get("match_terms") or [] if str(t).strip()]
            if terms:
                # Review F8: an account matches a term when its name holds every word of it, any case.
                flags["match_terms"] = terms
                flags["matching_rows"] = [
                    {"id": row.get("id"), "title": title} for row in rows if isinstance(row, dict)
                    for title in [str((row.get("attributes") or {}).get("title") or "")]
                    if title and any(all(w in title.lower() for w in t.lower().split()) for t in terms)]
            return flags
    elif endpoint.startswith(_SOPS_RECORD.rstrip("/") + "/") and isinstance(data, dict):
        record = data.get("data") if isinstance(data.get("data"), dict) else data
        links = _download_links(record)
        return {"download_links": links} if links else {}
    return {}


def _download_links(record: dict) -> list[dict]:
    """The file(s) an SOP record offers: each content blob's name and link, as the record states them."""
    blobs = ((record.get("attributes") or {}).get("content_blobs")) or []
    out = []
    for blob in blobs if isinstance(blobs, list) else []:
        if not isinstance(blob, dict):
            continue
        link = blob.get("link") or (blob.get("links") or {}).get("download") or (blob.get("links") or {}).get("self")
        if link:
            # SEEK serves the blob resource at the link and the file at <link>/download (content_blobs.py, review N8).
            link = re.sub(r"(/content_blobs/\d+)/?$", r"\1/download", str(link))
            out.append({"file": blob.get("original_filename") or blob.get("title"), "link": link})
    return out


def _shrink_preview_to_budget(
    slimmed: dict,
    preview_key: str,
    max_chars: int,
    *,
    truncated_key: str | None = None,
    pinned: int = 0,
) -> tuple[dict, int | None]:
    """Drop previewed rows until the payload fits, keeping at least one.

    Returns the payload and how many rows survived. A single row larger than the
    whole budget is kept and the payload stays over it: one real record the chatter
    can quote beats a preview of none.

    Anything dropped here is dropped AFTER the caller decided whether the preview
    was complete, so ``truncated_key`` is re-asserted: a payload that silently lost
    records while still claiming to hold all of them is the quiet lie these flags
    exist to close.
    """
    data = slimmed.get("data")
    if not isinstance(data, dict) or not isinstance(data.get(preview_key), list):
        return slimmed, None

    items = list(data[preview_key])
    while len(items) > max(1, pinned) and len(json.dumps(slimmed, default=str)) > max_chars:
        items = items[:-1]
        data = {**data, preview_key: items,
                "note": f"Result truncated for LLM context (>{max_chars} chars)."}
        if truncated_key:
            data[truncated_key] = True
        slimmed = {**slimmed, "data": data}
    # Pinned (named) records are never dropped: trim their long text instead.
    for cap in (400, 200, 100, 50):
        if not pinned or len(json.dumps(slimmed, default=str)) <= max_chars:
            break
        items = [_trim_long_fields(r, cap) if i < pinned else r for i, r in enumerate(items)]
        slimmed = {**slimmed, "data": {**slimmed["data"], preview_key: items, "fields_trimmed": True}}
    return slimmed, len(items)


def _trim_long_fields(record: dict, cap: int) -> dict:
    """Long text cut to ``cap``, one level into a nested dict too (a retrieve record's ``metadata``, R8)."""
    def trim(v):
        return v[:cap] + "...[trimmed]" if isinstance(v, str) and len(v) > cap else v
    return {k: ({ik: trim(iv) for ik, iv in v.items()} if isinstance(v, dict) else trim(v)) for k, v in record.items()}


def collect_bundle_files(bundle: dict) -> list[tuple[str, str]]:
    """Return `(label, path)` pairs for saved bundle artifacts that still exist on disk."""
    paths: list[tuple[str, str]] = []

    manifest_files = bundle.get("files") or []
    for entry in manifest_files:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        label = entry.get("label") or entry.get("key") or "artifact"
        if isinstance(path, str) and path and Path(path).exists():
            paths.append((str(label), path))

    if paths:
        return paths

    raw = bundle.get("raw_result_path")
    if raw and Path(raw).exists():
        paths.append(("API result (full JSON)", raw))

    graph = bundle.get("graph_debug_path")
    if graph and Path(graph).exists():
        paths.append(("Graph query debug JSON", graph))

    plan_debug = bundle.get("plan_debug_path")
    if plan_debug and Path(plan_debug).exists():
        paths.append(("Plan debug JSON", plan_debug))

    for step_id, raw_path in (bundle.get("raw_result_paths") or {}).items():
        if raw_path and Path(raw_path).exists():
            paths.append((f"API result (step {step_id})", raw_path))

    for step_id, graph_path in (bundle.get("graph_debug_paths") or {}).items():
        if graph_path and Path(graph_path).exists():
            paths.append((f"Graph debug (step {step_id})", graph_path))

    saved = bundle.get("report_saved_files") or {}
    for key, path in saved.items():
        if isinstance(path, str) and path and Path(path).exists():
            paths.append((key, path))
        elif isinstance(path, list):
            for i, p in enumerate(path):
                if isinstance(p, str) and p and Path(p).exists():
                    paths.append((f"{key}[{i}]", p))

    return paths


def normalize_api_result_for_memory(api_result: dict, min_rows_for_norm: int = 1) -> dict:
    """
    Normalize raw API rows into a compact structure the memory agent can consume reliably.
    Extracts uid/sample_type/assays/json_metadata, falling back to raw data when structure is unexpected or too sparse.
    """
    data = api_result.get("data")
    if not isinstance(data, dict):
        print("[DEBUG][MEMORY_NORM] data is not a dict; falling back to raw data.")
        return {"fallback": True, "raw_data": data}

    rows = data.get("rows")
    if not isinstance(rows, list):
        print("[DEBUG][MEMORY_NORM] No 'rows' list in data; falling back to raw data for this endpoint.")
        return {"fallback": True, "raw_data": data}

    normalized_rows = []
    for r in rows:
        if not isinstance(r, dict):
            continue

        uid = r.get("uuid")
        if not uid:
            uid = strip_html(r.get("uid"))

        sample_type = r.get("sample_type") or r.get("sampletype")
        assays = r.get("assays")
        source_id = r.get("id")

        meta = r.get("json_metadata")
        if not isinstance(meta, dict):
            meta = {}

        normalized_rows.append(
            {
                "uid": uid,
                "sample_type": sample_type,
                "assays": assays,
                "source_id": source_id,
                "metadata": meta,
            }
        )

    if len(normalized_rows) < min_rows_for_norm and len(rows) > 0:
        print(
            f"[DEBUG][MEMORY_NORM] Normalization produced only {len(normalized_rows)} rows "
            f"from {len(rows)} raw rows; falling back to raw data."
        )
        return {"fallback": True, "raw_data": data}

    print(
        f"[DEBUG][MEMORY_NORM] Normalized {len(normalized_rows)} rows "
        f"from {len(rows)} raw rows for memory agent."
    )

    return {
        "total": data.get("total"),
        "rows": normalized_rows,
        "note": "Normalized for memory agent; metadata flattened from json_metadata; HTML removed.",
    }


def _bundle_rows(bundle: dict) -> list:
    """A results bundle's rows, wherever its turn stored them.

    The follow-up agent's reader first (a plan's last step, a graph search's ``graph_result``,
    then the REST payload), so the pipeline agent reads the same memory of a search as every
    other agent: it read only the REST payload, and "run rnaseq on those mice" after a graph
    search answered "no pinned search results". Then a body that nests its rows under ``data``.
    """
    from ..agents.followup import _stored_rows  # here, not at the top: the agents package imports helpers

    rows = _stored_rows(bundle)
    if rows:
        return rows
    data = load_api_result_full(bundle).get("data")
    nested = data.get("data") if isinstance(data, dict) else data
    return nested if isinstance(nested, list) else []


def _row_uid(row) -> str:
    """A row's sample UID, '' when it has none: a UID column, else a graph column holding a UID under
    the name the query gave it (``s.uuid``). Never another value, so a breakdown row yields nothing."""
    if isinstance(row, str):
        return re.sub(r"<[^>]+>", "", row).strip()
    if not isinstance(row, dict):
        return ""
    for key in ("uid", "UID", "uuid", "sample_uid"):
        if row.get(key):
            return re.sub(r"<[^>]+>", "", str(row[key])).strip()
    for value in row.values():
        if isinstance(value, str) and UID_RE.fullmatch(value.strip()):
            return value.strip()
    return ""


def summarize_pinned_bundle(session) -> str:
    """One-line summary of the user's most recent results bundle. '' when none.

    Moved from pipeline/agent.py — generic results-history knowledge.
    """
    history = session.get("results_history") or []
    if not isinstance(history, list) or not history:
        return ""
    last = history[-1]
    if not isinstance(last, dict):
        return ""
    user_q = last.get("user_query") or ""
    return f"last search: query={user_q!r}, ~{len(_bundle_rows(last))} rows"


def uids_from_last_search(session) -> list[str]:
    """Sample UIDs from the most recent results bundle that has any.

    Walks results_history in reverse; reads each bundle's rows (``_bundle_rows``: graph, plan
    and REST alike), falls back to reporter_plan.uids then parser_plan.filters.uids.
    Moved from pipeline/agent.py.
    """
    history = session.get("results_history") or []
    if not isinstance(history, list):
        return []
    for bundle in reversed(history):
        if not isinstance(bundle, dict):
            continue
        out = [uid for uid in map(_row_uid, _bundle_rows(bundle)) if uid]
        if not out:
            rp = bundle.get("reporter_plan")
            plan_uids = rp.get("uids") if isinstance(rp, dict) else None
            if not (isinstance(plan_uids, list) and plan_uids):
                pp = bundle.get("parser_plan")
                filt = pp.get("filters") if isinstance(pp, dict) else None
                plan_uids = filt.get("uids") if isinstance(filt, dict) else None
            for uid in plan_uids or []:
                if isinstance(uid, str) and uid.strip():
                    out.append(re.sub(r"<[^>]+>", "", uid).strip())
        if out:
            return out
    return []
