"""Report runners: project sample, protocols, published, and the
plugin-portable ``run_reporter_summary`` orchestrator.

This module is the canonical home for the report-runner subsystem moved out
of ``helpers.py`` during the Phase 2 src/ restructure. The
``helpers.py`` shim still re-exports these names so the plugin-portable
contract (``from chat_nextseek.helpers import run_reporter_summary``) keeps
working.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..artifacts import ArtifactStore
from ..config import live_db_conn
from ..helpers.dates import (
    _normalize_project_id,
    _normalize_years,
    _month_range_to_yymmdd_bounds,
    _day_range_to_yymmdd_bounds,
)
from ..helpers.tools.neo4j import tool_neo4j_query
from .nfcore import top_items
from .outputs import persist_report_file


def _resolve_investigation(config, name) -> "tuple[int, str] | None":
    """Resolve a name to ``(investigation_id, UPPER_title)`` via
    ``config.INVESTIGATION_NAME_TO_ID``. Smart but not over-permissive: exact
    UPPER match, then punctuation-insensitive exact, then whole-token containment
    (map keys >= 3 chars, matched as a full token — never an arbitrary substring).
    Returns ``None`` when the name matches no investigation."""
    inv_map = getattr(config, "INVESTIGATION_NAME_TO_ID", None) or {}
    if not inv_map or not isinstance(name, str):
        return None
    key = name.strip().upper()
    if not key:
        return None
    if key in inv_map:                                   # 1. exact
        return (inv_map[key], key)
    norm = re.sub(r"[^A-Z0-9]", "", key)                 # 2. punctuation-insensitive exact
    if norm:
        for k, v in inv_map.items():
            if re.sub(r"[^A-Z0-9]", "", k) == norm:
                return (v, k)
    tokens = set(re.findall(r"[A-Z0-9]+", key))          # 3. whole-token containment
    for k, v in inv_map.items():
        if len(k) >= 3 and k in tokens:
            return (v, k)
    return None


def _resolve_report_scope(config, project) -> "tuple[str, Any]":
    """
    Resolve a report's requested scope to ``("project", id)``,
    ``("investigation", (id, title))`` or ``("all", None)``.

    Only ``run_project_sample_report`` used to have the investigation fallback; the
    protocols and published runners called ``_normalize_project_id`` bare. The six
    investigations (CSBC, Griffith, Impact, MetNet, SRP, Shoulders) live in
    ``INVESTIGATION_NAME_TO_ID`` while ``PROJECT_NAME_TO_ID`` holds only
    {PUB, PUBLISHED, PUBLISHED DATA}, so every investigation name raised
    ``ValueError("Unknown project 'SRP'")`` and the whole RPPR died — identically in
    baseline task 751 and post-fix task 815 — while the samples-only mode answered
    the same string fine.

    Raises ValueError only when the name is neither a project nor an investigation,
    preserving the original error for genuinely unknown scopes.
    """
    if project is None or (isinstance(project, str) and not project.strip()):
        return ("all", None)
    try:
        project_id = _normalize_project_id(config, project)
    except ValueError:
        investigation = _resolve_investigation(config, project)
        if investigation is None:
            raise
        return ("investigation", investigation)
    if project_id is None:
        return ("all", None)
    return ("project", project_id)


def _tabulate_sample_uuids(uuids: list[str]) -> dict:
    """Shared sample-UUID tabulation (sampletype/lab/year/month counts).
    UID format: ``<SAMPLETYPE>-<YYMMDD><LAB>-<INCREMENT>``. Identical logic to the
    project sample report's inline tabulation, factored out so the investigation
    path produces a byte-identical table shape."""
    uid_re = re.compile(r"^(?P<sampletype>[^-]+)-(?P<yymmdd>\d{6})(?P<lab>[A-Za-z]+)-(?P<inc>\d+)(-\w+)*$")
    st: dict[str, int] = {}
    lab_c: dict[str, int] = {}
    yr: dict[str, int] = {}
    mo: dict[str, int] = {}
    unparsable = 0
    for uid in uuids:
        m = uid_re.match(str(uid))
        if not m:
            unparsable += 1
            continue
        ymd = m.group("yymmdd")
        st[m.group("sampletype")] = st.get(m.group("sampletype"), 0) + 1
        lab_c[m.group("lab")] = lab_c.get(m.group("lab"), 0) + 1
        yr[ymd[:2]] = yr.get(ymd[:2], 0) + 1
        mo[ymd[:4]] = mo.get(ymd[:4], 0) + 1
    return {
        "sampletypes_table": dict(sorted(st.items(), key=lambda kv: (-kv[1], kv[0]))),
        "labs_table": dict(sorted(lab_c.items(), key=lambda kv: (-kv[1], kv[0]))),
        "years_table": dict(sorted(yr.items(), key=lambda kv: (-kv[1], kv[0]))),
        "months_table": dict(sorted(mo.items(), key=lambda kv: kv[0])),
        "unparsable_uids": unparsable,
    }


def _neo4j_investigation_sample_uuids(
    config, inv_title: str,
    years: list[int | str] | None = None,
    month_range: tuple[str, str] | None = None,
    day_range: tuple[str, str] | None = None,
) -> list[str]:
    """Sample UUIDs under an investigation, via Neo4j — the relational DB has no
    populated sample->study->investigation linkage on this instance. Mirrors the
    published-report traversal, scoped by EXACT investigation title (case-insensitive)
    plus the same UUID-substring date filters."""
    conditions = ["toLower(inv.title) = toLower($inv_title)"]
    params: dict = {"inv_title": inv_title}
    if years:
        params["yy_list"] = _normalize_years(years)
        conditions.append("substring(split(s.uuid, '-')[1], 0, 2) IN $yy_list")
    if month_range or day_range:
        start6, end6 = (
            _month_range_to_yymmdd_bounds(month_range) if month_range
            else _day_range_to_yymmdd_bounds(day_range)
        )
        conditions.append(
            "substring(split(s.uuid, '-')[1], 0, 6) >= $d6s "
            "AND substring(split(s.uuid, '-')[1], 0, 6) <= $d6e"
        )
        params["d6s"], params["d6e"] = start6, end6
    cypher = (
        "MATCH (inv:Investigation)<-[:IN_INVESTIGATION]-(study:Study)<-[:IN_STUDY]-(s:Sample) "
        "WHERE " + " AND ".join(conditions) + " "
        "RETURN DISTINCT s.uuid AS uuid"
    )
    print("[REPORTER][NEO4J] Running investigation sample report query", {"cypher": cypher, "params": params})
    res = tool_neo4j_query(config, cypher, params)
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "Neo4j query failed"))
    return [r["uuid"] for r in (res.get("data") or []) if r.get("uuid")]


def _run_investigation_sample_report(
    config, investigation: "tuple[int, str]", project,
    years=None, month_range=None, day_range=None, outputs_root="outputs",
) -> dict:
    """Investigation-scoped sample report (Neo4j-sourced UUIDs + shared tabulation).
    Returns the SAME result shape as run_project_sample_report so the chatter
    formats it identically; adds ``scope``/``investigation_id``/``investigation_title``."""
    inv_id, inv_title = investigation
    uuids = _neo4j_investigation_sample_uuids(config, inv_title, years, month_range, day_range)
    tables = _tabulate_sample_uuids(uuids)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    outputs_root_p = Path(outputs_root)
    outputs_root_p.mkdir(exist_ok=True)
    payload = {
        "scope": "investigation",
        "investigation_id": inv_id,
        "investigation_title": inv_title,
        "generated_at": datetime.now().isoformat(),
        "filters": {"project": project, "years": years, "month_range": month_range, "day_range": day_range},
        "rows_returned": len(uuids),
        "uuids": uuids,
        **tables,
    }
    entry = ArtifactStore(outputs_root_p).write_json(
        key="uuid_report_file", label="Samples report JSON",
        filename=f"investigation_{inv_id}_{ts}.uuids.json", payload=payload, kind="report",
    )
    return {
        "ok": True,
        "project_id": None,
        "scope": "investigation",
        "investigation_id": inv_id,
        "investigation_title": inv_title,
        "rows_returned": len(uuids),
        "uuids_saved": len(uuids),
        # Needed by _scope_report_to_labs. run_reporter_summary drops it again once
        # scoping is done — it can be ~50k strings and must not reach a UI payload,
        # a persisted bundle or an LLM context. The durable copy is uuid_report_file.
        "uuids": uuids,
        "uuid_report_file": entry["path"] if entry else None,
        "uuid_preview": uuids[:10],
        **tables,
        "db_diagnostic": {},
    }


def run_project_sample_report(
    config,
    project: int | str | None,
    years: list[int | str] | None = None,
    month_range: tuple[str, str] | None = None,
    day_range: tuple[str, str] | None = None,
    outputs_root: str | Path = "outputs",
) -> dict:
    """
    Project-scoped sample UUID reporting with optional date filters extracted from UUID.
    If project is None, the report runs across all projects.

    UUID format: SampleType-YYMMDDLAB-Incrementer (e.g., TIS-240422DFC-6)
    Assay-derived/sample-like UIDs may include dots in the sample type (e.g., D.FCS-240306SAS-10).

    Date extraction used in SQL:
      date6 = LEFT(SUBSTRING_INDEX(SUBSTRING_INDEX(s.uuid,'-',2),'-',-1), 6)  -> 'YYMMDD'

    Filters:
      - years: [2024, 2025] -> filters date6 year part ('24','25')
      - month_range: ('2024-01','2025-12') -> date6 BETWEEN '240101' AND '251231'
      - day_range: ('2024-04-22','2024-06-30') -> date6 BETWEEN '240422' AND '240630'

    Output JSON includes:
      - sampletypes_table: counts by sample type
      - labs_table: counts by lab code
      - years_table: counts by YY (e.g., {"24": 123, "25": 456})
      - months_table: counts by YYMM (e.g., {"2401": 50, "2402": 61, ...})
    """
    # Scope resolution: PROJECT first (unchanged path). If the name is not a known
    # project but IS a known investigation, take the investigation-scoped path
    # (Neo4j — the relational DB has no sample->investigation linkage). Any name
    # that is neither still raises the original "Unknown project" ValueError.
    kind, scope = _resolve_report_scope(config, project)
    if kind == "investigation":
        return _run_investigation_sample_report(
            config, scope, project, years, month_range, day_range, outputs_root
        )
    project_id = scope if kind == "project" else None

    conn = live_db_conn(config, env="prod")
    if conn is None:
        return {"ok": False, "error": "DB connection failed"}

    # SQL expression for YYMMDD extracted from UUID
    date6_expr = "LEFT(SUBSTRING_INDEX(SUBSTRING_INDEX(s.uuid, '-', 2), '-', -1), 6)"

    conditions: list[str] = []
    params: list = []

    if project_id is not None:
        conditions.append("ps.project_id = %s")
        params.append(project_id)

    if years:
        yy_list = _normalize_years(years)
        placeholders = ", ".join(["%s"] * len(yy_list))
        conditions.append(f"LEFT({date6_expr}, 2) IN ({placeholders})")
        params.extend(yy_list)

    if month_range:
        start6, end6 = _month_range_to_yymmdd_bounds(month_range)
        conditions.append(f"{date6_expr} BETWEEN %s AND %s")
        params.extend([start6, end6])

    if day_range:
        start6, end6 = _day_range_to_yymmdd_bounds(day_range)
        conditions.append(f"{date6_expr} BETWEEN %s AND %s")
        params.extend([start6, end6])

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    query = f"""
    SELECT
      ps.project_id,
      ps.sample_id,
      s.uuid
    FROM
      seek_production.projects_samples ps
    JOIN
      seek_production.samples s
        ON ps.sample_id = s.id
    WHERE
      {where_clause};
    """.strip()

    outputs_root = Path(outputs_root)
    outputs_root.mkdir(exist_ok=True)

    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        print("[REPORTER][SQL] Running project sample report query", {"query": query, "params": params})
        cursor.execute(query, params)
        rows = cursor.fetchall() or []
        uuids = [r["uuid"] for r in rows if r.get("uuid")]

        # --- Build summary tables from UID parsing ---
        # Format assumed: <SAMPLETYPE>-<YYMMDD><LAB>-<INCREMENT>
        uid_re = re.compile(
            r"^(?P<sampletype>[^-]+)-(?P<yymmdd>\d{6})(?P<lab>[A-Za-z]+)-(?P<inc>\d+)(-\w+)*$"
        )

        sampletype_counts: dict[str, int] = {}
        lab_counts: dict[str, int] = {}
        year_counts: dict[str, int] = {}
        month_counts: dict[str, int] = {}
        unparsable_count = 0

        for uid in uuids:
            m = uid_re.match(str(uid))
            if not m:
                unparsable_count += 1
                continue

            stype = m.group("sampletype")
            lab = m.group("lab")
            yymmdd = m.group("yymmdd")  # "YYMMDD"
            yy = yymmdd[:2]
            yymm = yymmdd[:4]

            sampletype_counts[stype] = sampletype_counts.get(stype, 0) + 1
            lab_counts[lab] = lab_counts.get(lab, 0) + 1
            year_counts[yy] = year_counts.get(yy, 0) + 1
            month_counts[yymm] = month_counts.get(yymm, 0) + 1

        # Sort tables for readability
        sampletypes_table = dict(sorted(sampletype_counts.items(), key=lambda kv: (-kv[1], kv[0])))
        labs_table = dict(sorted(lab_counts.items(), key=lambda kv: (-kv[1], kv[0])))
        years_table = dict(sorted(year_counts.items(), key=lambda kv: (-kv[1], kv[0])))
        # Months are often nicer sorted chronologically; YYMM sorts lexicographically as desired
        months_table = dict(sorted(month_counts.items(), key=lambda kv: kv[0]))

        # --- Write report artifact (JSON) ---
        # Write directly to outputs_root (no reports/ subdirectory)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        project_label = project_id if project_id is not None else "all"
        report_filename = f"project_{project_label}_{ts}.uuids.json"

        payload = {
            "project_id": project_id,
            "generated_at": datetime.now().isoformat(),
            "filters": {
                "project": project,
                "years": years,
                "month_range": month_range,
                "day_range": day_range,
            },
            "rows_returned": len(rows),
            "uuids": uuids,
            "sampletypes_table": sampletypes_table,
            "labs_table": labs_table,
            "years_table": years_table,
            "months_table": months_table,
            "parsing_notes": {
                "uid_format": "<SAMPLETYPE>-<YYMMDD><LAB>-<INCREMENT>",
                "regex": uid_re.pattern,
                "unparsable_uids": unparsable_count,
            },
        }

        report_entry = ArtifactStore(outputs_root).write_json(
            key="uuid_report_file",
            label="Samples report JSON",
            filename=report_filename,
            payload=payload,
            kind="report",
        )
        report_path = report_entry["path"] if report_entry else None

        # When a specific project was requested and zero rows came back,
        # probe whether the project exists in THIS DB and whether it has
        # ANY rows. This lets the chatter distinguish "no samples in the
        # requested period" from "this DB has no data for that project at
        # all" (common when MYSQL_HOST_PROD is aliased to a local dev DB).
        db_diagnostic: dict[str, Any] = {}
        if project_id is not None and len(rows) == 0:
            try:
                probe_cursor = conn.cursor(dictionary=True)
                probe_cursor.execute(
                    "SELECT (SELECT COUNT(*) FROM seek_production.projects "
                    "WHERE id=%s) AS project_exists, "
                    "(SELECT COUNT(*) FROM seek_production.projects_samples "
                    "WHERE project_id=%s) AS total_for_project",
                    [project_id, project_id],
                )
                probe = probe_cursor.fetchone() or {}
                probe_cursor.close()
                project_exists = bool(probe.get("project_exists"))
                total_for_project = int(probe.get("total_for_project") or 0)
                db_diagnostic = {
                    "project_exists_in_db": project_exists,
                    "total_rows_for_project": total_for_project,
                    "likely_missing_data": (not project_exists) or total_for_project == 0,
                }
            except Exception as probe_err:
                db_diagnostic = {"probe_error": repr(probe_err)}

        return {
            "ok": True,
            "project_id": project_id,
            "rows_returned": len(rows),
            "uuids_saved": len(uuids),
            # See _run_investigation_sample_report: required by _scope_report_to_labs,
            # dropped again by run_reporter_summary before the result is serialised.
            "uuids": uuids,
            "uuid_report_file": report_path,
            "uuid_preview": uuids[:10],
            "sampletypes_table": sampletypes_table,
            "labs_table": labs_table,
            "years_table": years_table,
            "months_table": months_table,
            "unparsable_uids": unparsable_count,
            "db_diagnostic": db_diagnostic,
        }

    except Exception as e:
        return {"ok": False, "error": repr(e)}


def run_project_protocols_report(
    config,
    project: int | str | None = None,
    years: list[int | str] | None = None,
    month_range: tuple[str, str] | None = None,
    day_range: tuple[str, str] | None = None,
    outputs_root: str | Path = "outputs",
) -> dict:
    """
    Project-scoped SOP/protocol reporting with optional date filters extracted from title.
    If project is None, runs across all projects.

    Title format: P.<LAB>-<YYMMDD>-<rest>  (e.g. P.SAS-240827-V1_RSTR_BMDM_protocol.docx)

    Date extraction used in SQL:
      date6 = LEFT(SUBSTRING_INDEX(SUBSTRING_INDEX(sop.title, '-', 2), '-', -1), 6)  -> 'YYMMDD'

    Filters:
      - years: [2024, 2025] -> filters date6 year part ('24','25')
      - month_range: ('2024-01','2025-12') -> date6 BETWEEN '240101' AND '251231'
      - day_range: ('2024-04-22','2024-06-30') -> date6 BETWEEN '240422' AND '240630'

    Output includes:
      - labs_table: counts by lab code (e.g. {"SAS": 12, "DFC": 5})
      - years_table: counts by YY
      - months_table: counts by YYMM
    """
    # Protocols have no relational sample->investigation link, so an investigation
    # scope cannot be narrowed here. Report every protocol and say so explicitly
    # rather than raising and killing the whole RPPR (task 815).
    kind, scope = _resolve_report_scope(config, project)
    unsupported_scope = None
    if kind == "investigation":
        inv_id, inv_title = scope
        unsupported_scope = {
            "kind": "investigation",
            "investigation_id": inv_id,
            "investigation_title": inv_title,
            "unsupported": True,
            "reason": "protocols have no relational link to investigations; "
                      "reporting all protocols",
        }
        print(f"[DEBUG][REPORTER] Protocols cannot be scoped to investigation "
              f"'{inv_title}'; reporting all protocols")
        project_id = None
    else:
        project_id = scope if kind == "project" else None

    conn = live_db_conn(config, env="prod")
    if conn is None:
        return {"ok": False, "error": "DB connection failed"}

    # Title format: P.<LAB>-<YYMMDD>-<rest>
    # Second '-'-delimited segment is always YYMMDD
    date6_expr = "LEFT(SUBSTRING_INDEX(SUBSTRING_INDEX(sop.title, '-', 2), '-', -1), 6)"

    conditions: list[str] = []
    params: list = []

    if project_id is not None:
        conditions.append("ps.project_id = %s")
        params.append(project_id)

    if years:
        yy_list = _normalize_years(years)
        placeholders = ", ".join(["%s"] * len(yy_list))
        conditions.append(f"LEFT({date6_expr}, 2) IN ({placeholders})")
        params.extend(yy_list)

    if month_range:
        start6, end6 = _month_range_to_yymmdd_bounds(month_range)
        conditions.append(f"{date6_expr} BETWEEN %s AND %s")
        params.extend([start6, end6])

    if day_range:
        start6, end6 = _day_range_to_yymmdd_bounds(day_range)
        conditions.append(f"{date6_expr} BETWEEN %s AND %s")
        params.extend([start6, end6])

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    query = f"""
    SELECT
      ps.project_id,
      ps.sop_id,
      sop.title
    FROM
      seek_production.projects_sops ps
    JOIN
      seek_production.sops sop
        ON ps.sop_id = sop.id
    WHERE
      {where_clause};
    """.strip()

    outputs_root = Path(outputs_root)
    outputs_root.mkdir(exist_ok=True)

    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        print("[REPORTER][SQL] Running project protocols report query", {"query": query, "params": params})
        cursor.execute(query, params)
        rows = cursor.fetchall() or []
        titles = [r["title"] for r in rows if r.get("title")]

        # Parse: P.<LAB>-<YYMMDD>-<rest>
        title_re = re.compile(r"^P\.(?P<lab>[^-]+)-(?P<yymmdd>\d{6})-(?P<rest>.*)$")

        lab_counts: dict[str, int] = {}
        year_counts: dict[str, int] = {}
        month_counts: dict[str, int] = {}
        unparsable_count = 0

        for title in titles:
            m = title_re.match(str(title))
            if not m:
                unparsable_count += 1
                continue

            lab = m.group("lab")
            yymmdd = m.group("yymmdd")
            yy = yymmdd[:2]
            yymm = yymmdd[:4]

            lab_counts[lab] = lab_counts.get(lab, 0) + 1
            year_counts[yy] = year_counts.get(yy, 0) + 1
            month_counts[yymm] = month_counts.get(yymm, 0) + 1

        labs_table = dict(sorted(lab_counts.items(), key=lambda kv: (-kv[1], kv[0])))
        years_table = dict(sorted(year_counts.items(), key=lambda kv: (-kv[1], kv[0])))
        months_table = dict(sorted(month_counts.items(), key=lambda kv: kv[0]))

        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        project_label = project_id if project_id is not None else "all"
        report_filename = f"project_{project_label}_{ts}.protocols.json"

        payload = {
            "project_id": project_id,
            "generated_at": datetime.now().isoformat(),
            "filters": {
                "project": project,
                "years": years,
                "month_range": month_range,
                "day_range": day_range,
            },
            "rows_returned": len(rows),
            "titles": titles,
            "labs_table": labs_table,
            "years_table": years_table,
            "months_table": months_table,
            "parsing_notes": {
                "title_format": "P.<LAB>-<YYMMDD>-<rest>",
                "regex": title_re.pattern,
                "unparsable_titles": unparsable_count,
            },
        }

        report_entry = ArtifactStore(outputs_root).write_json(
            key="protocols_report",
            label="Protocols report JSON",
            filename=report_filename,
            payload=payload,
            kind="report",
        )
        report_path = report_entry["path"] if report_entry else None

        return {
            "ok": True,
            "summary_mode": "protocols",
            "project_id": project_id,
            "rows_returned": len(rows),
            "titles_saved": len(titles),
            "report_file": report_path,
            "titles_preview": titles[:10],
            "labs_table": labs_table,
            "years_table": years_table,
            "months_table": months_table,
            "unparsable_titles": unparsable_count,
            # Needed by _scope_protocols_to_labs; dropped again by run_reporter_summary.
            "titles": titles,
            **({"scope": unsupported_scope} if unsupported_scope else {}),
        }

    except Exception as e:
        return {"ok": False, "error": repr(e)}


def run_project_published_report(  # noqa: C901
    config,
    project: int | str | None = None,
    years: list[int | str] | None = None,
    month_range: tuple[str, str] | None = None,
    day_range: tuple[str, str] | None = None,
    outputs_root: str | Path = "outputs",
) -> dict:
    """
    Project-scoped published samples and protocols report.

    Published samples and studies are determined via Neo4j: the production graph contains
    only data that has been published/submitted to public repositories.
    Traversal: (inv:Investigation) <-[:IN_INVESTIGATION]- (study:Study) <-[:IN_STUDY]- (s:Sample)
    Filtering by project uses case-insensitive CONTAINS on inv.title (normalized, spaces stripped).
    Date filtering uses the same YYMMDD substring from the sample UUID.

    Published protocols are determined by intersection:
      - Protocols for this project+date in seek_production
      - Protocol titles that also exist in seek_development (dev = published/submitted)

    Returns counts of published samples (by type/lab/year/month), study count, and protocol count.
    """
    # Resolution only — the Cypher below already filters on the raw project string via
    # $proj_hint, so an investigation name works the moment resolution stops raising.
    kind, scope = _resolve_report_scope(config, project)
    project_id = scope if kind == "project" else None
    investigation_scope = scope if kind == "investigation" else None
    outputs_root = Path(outputs_root)
    outputs_root.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    project_label = project_id if project_id is not None else "all"

    # ── 1. Published samples + studies via Neo4j ──────────────────────────────
    samples_result: dict = {}

    # Build Cypher date filters based on UUID substring YYMMDD
    # UUID format: <TYPE>-<YYMMDD><LAB>-<INC>
    cypher_conditions: list[str] = []
    cypher_params: dict = {}

    # Umbrella projects (dev-only opt-in, default off) skip the investigation-
    # title hint and report ALL investigations' samples: a project that contains
    # every investigation matches no single title, so the hint would wrongly
    # return 0 (issue #1 / option 2). Date filters below still apply.
    is_umbrella = getattr(config, "is_umbrella_published_project", None)
    skip_hint = bool(is_umbrella and is_umbrella(project, project_id))
    if project is not None and not skip_hint:
        # Normalize project hint: lowercase, strip spaces for CONTAINS match
        hint = re.sub(r"\s+", "", str(project).lower())
        cypher_conditions.append(
            "replace(toLower(inv.title), ' ', '') CONTAINS $proj_hint"
        )
        cypher_params["proj_hint"] = hint

    if years:
        yy_list = _normalize_years(years)
        cypher_conditions.append("substring(split(s.uuid, '-')[1], 0, 2) IN $yy_list")
        cypher_params["yy_list"] = yy_list

    if month_range:
        start6, end6 = _month_range_to_yymmdd_bounds(month_range)
        cypher_conditions.append(
            "substring(split(s.uuid, '-')[1], 0, 6) >= $date6_start "
            "AND substring(split(s.uuid, '-')[1], 0, 6) <= $date6_end"
        )
        cypher_params["date6_start"] = start6
        cypher_params["date6_end"] = end6

    if day_range:
        start6, end6 = _day_range_to_yymmdd_bounds(day_range)
        cypher_conditions.append(
            "substring(split(s.uuid, '-')[1], 0, 6) >= $date6_start "
            "AND substring(split(s.uuid, '-')[1], 0, 6) <= $date6_end"
        )
        cypher_params["date6_start"] = start6
        cypher_params["date6_end"] = end6

    where_clause = (" WHERE " + " AND ".join(cypher_conditions)) if cypher_conditions else ""
    cypher = (
        f"MATCH (inv:Investigation)<-[:IN_INVESTIGATION]-(study:Study)<-[:IN_STUDY]-(s:Sample)"
        f"{where_clause} "
        f"RETURN s.uuid AS uuid, study.title AS study_title, s.type AS sampletype"
    )

    print("[REPORTER][NEO4J] Running published samples query", {"cypher": cypher, "params": cypher_params})
    neo4j_result = tool_neo4j_query(config, cypher, cypher_params)

    if not neo4j_result.get("ok"):
        samples_result = {"ok": False, "error": neo4j_result.get("error", "Neo4j query failed")}
    else:
        rows = neo4j_result.get("data") or []
        uids = [r["uuid"] for r in rows if r.get("uuid")]
        study_set = {r["study_title"] for r in rows if r.get("study_title")}

        uid_re = re.compile(
            r"^(?P<sampletype>[^-]+)-(?P<yymmdd>\d{6})(?P<lab>[A-Za-z]+)-(?P<inc>\d+)(-\w+)*$"
        )
        sampletype_counts: dict[str, int] = {}
        lab_counts: dict[str, int] = {}
        year_counts: dict[str, int] = {}
        month_counts: dict[str, int] = {}
        unparsable_count = 0

        for uid in uids:
            m = uid_re.match(str(uid))
            if not m:
                unparsable_count += 1
                continue
            stype = m.group("sampletype")
            lab = m.group("lab")
            yymmdd = m.group("yymmdd")
            yy = yymmdd[:2]
            yymm = yymmdd[:4]
            sampletype_counts[stype] = sampletype_counts.get(stype, 0) + 1
            lab_counts[lab] = lab_counts.get(lab, 0) + 1
            year_counts[yy] = year_counts.get(yy, 0) + 1
            month_counts[yymm] = month_counts.get(yymm, 0) + 1

        samples_result = {
            "ok": True,
            "rows_returned": len(uids),
            "study_count": len(study_set),
            "studies": sorted(study_set),
            # Needed by _scope_published_to_labs so study_count/studies can be
            # recomputed from the survivors rather than left contradicting the row
            # count. Dropped again by run_reporter_summary.
            "uuids": uids,
            "uuid_studies": [[r.get("uuid"), r.get("study_title")] for r in rows if r.get("uuid")],
            "sampletypes_table": dict(sorted(sampletype_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            "labs_table": dict(sorted(lab_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            "years_table": dict(sorted(year_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            "months_table": dict(sorted(month_counts.items(), key=lambda kv: kv[0])),
            "unparsable_uids": unparsable_count,
        }

    # ── 2. Published protocols via prod ∩ dev MySQL ───────────────────────────
    protocols_result: dict = {}
    try:
        # Step A: prod titles for this project + date range
        prod_conn = live_db_conn(config, env="prod")
        if prod_conn is None:
            protocols_result = {"ok": False, "error": "Prod DB connection failed"}
        else:
            date6_expr = "LEFT(SUBSTRING_INDEX(SUBSTRING_INDEX(sop.title, '-', 2), '-', -1), 6)"
            prod_conditions: list[str] = []
            prod_params: list = []

            if project_id is not None:
                prod_conditions.append("ps.project_id = %s")
                prod_params.append(project_id)

            if years:
                yy_list = _normalize_years(years)
                placeholders = ", ".join(["%s"] * len(yy_list))
                prod_conditions.append(f"LEFT({date6_expr}, 2) IN ({placeholders})")
                prod_params.extend(yy_list)

            if month_range:
                start6, end6 = _month_range_to_yymmdd_bounds(month_range)
                prod_conditions.append(f"{date6_expr} BETWEEN %s AND %s")
                prod_params.extend([start6, end6])

            if day_range:
                start6, end6 = _day_range_to_yymmdd_bounds(day_range)
                prod_conditions.append(f"{date6_expr} BETWEEN %s AND %s")
                prod_params.extend([start6, end6])

            prod_where = " AND ".join(prod_conditions) if prod_conditions else "1=1"
            prod_query = f"""
SELECT sop.title
FROM seek_production.projects_sops ps
JOIN seek_production.sops sop ON ps.sop_id = sop.id
WHERE {prod_where};
""".strip()

            print("[REPORTER][SQL] Published protocols — prod query", {"query": prod_query, "params": prod_params})
            cursor = prod_conn.cursor(dictionary=True)
            cursor.execute(prod_query, prod_params)
            prod_titles: set[str] = {r["title"] for r in (cursor.fetchall() or []) if r.get("title")}

            # Step B: all dev titles (no project/date filter — dev = published)
            dev_conn = config._connect_db(env="dev")
            if dev_conn is None:
                protocols_result = {"ok": False, "error": "Dev DB connection failed"}
            else:
                dev_query = "SELECT title FROM seek_production.sops WHERE title IS NOT NULL;"
                print("[REPORTER][SQL] Published protocols — dev query")
                dev_cursor = dev_conn.cursor(dictionary=True)
                dev_cursor.execute(dev_query)
                dev_titles: set[str] = {r["title"] for r in (dev_cursor.fetchall() or []) if r.get("title")}

                published_titles = sorted(prod_titles & dev_titles)

                title_re = re.compile(r"^P\.(?P<lab>[^-]+)-(?P<yymmdd>\d{6})-(?P<rest>.*)$")
                lab_counts_p: dict[str, int] = {}
                year_counts_p: dict[str, int] = {}
                month_counts_p: dict[str, int] = {}
                unparsable_p = 0

                for title in published_titles:
                    m = title_re.match(title)
                    if not m:
                        unparsable_p += 1
                        continue
                    lab = m.group("lab")
                    yymmdd = m.group("yymmdd")
                    yy = yymmdd[:2]
                    yymm = yymmdd[:4]
                    lab_counts_p[lab] = lab_counts_p.get(lab, 0) + 1
                    year_counts_p[yy] = year_counts_p.get(yy, 0) + 1
                    month_counts_p[yymm] = month_counts_p.get(yymm, 0) + 1

                protocols_result = {
                    "ok": True,
                    "rows_returned": len(published_titles),
                    "titles": published_titles,
                    "labs_table": dict(sorted(lab_counts_p.items(), key=lambda kv: (-kv[1], kv[0]))),
                    "years_table": dict(sorted(year_counts_p.items(), key=lambda kv: (-kv[1], kv[0]))),
                    "months_table": dict(sorted(month_counts_p.items(), key=lambda kv: kv[0])),
                    "unparsable_titles": unparsable_p,
                }
    except Exception as e:
        protocols_result = {"ok": False, "error": repr(e)}

    # ── 3. Write artifact + return ─────────────────────────────────────────────
    report_filename = f"project_{project_label}_{ts}.published.json"
    payload = {
        "project_id": project_id,
        "generated_at": datetime.now().isoformat(),
        "filters": {"project": project, "years": years, "month_range": month_range, "day_range": day_range},
        "samples": samples_result,
        "protocols": protocols_result,
    }
    report_entry = ArtifactStore(outputs_root).write_json(
        key="published_report",
        label="Published report JSON",
        filename=report_filename,
        payload=payload,
        kind="report",
    )
    report_path = report_entry["path"] if report_entry else None

    ok = samples_result.get("ok", False) or protocols_result.get("ok", False)
    return {
        "ok": ok,
        "summary_mode": "published",
        "project_id": project_id,
        "report_file": report_path,
        "samples": samples_result,
        "protocols": protocols_result,
        **({"scope": {"kind": "investigation",
                      "investigation_id": investigation_scope[0],
                      "investigation_title": investigation_scope[1]}}
           if investigation_scope else {}),
    }


def _lab_of(uid: str) -> str | None:
    """Lab code embedded in a UID (``<TYPE>-<YYMMDD><LAB>-<INC>[-SUFFIX]``)."""
    m = re.match(r"^[^-]+-\d{6}(?P<lab>[A-Za-z]+)-\d+", str(uid))
    return m.group("lab").upper() if m else None


def _scope_report_to_labs(result: dict, lab_codes: list[str]) -> dict:
    """Narrow an all-projects sample report to the requested lab(s).

    ``run_project_sample_report`` runs across EVERY project when ``project`` is
    None, which is correct for "how big is the database" but wrong for "an annual
    progress report for the Kamm project": Kamm is modelled as a LAB, so the
    project never resolved and the RPPR silently covered all 50,886 samples while
    describing itself as Kamm's. The lab code is embedded in every UID, so the
    scope can be applied without a schema change.
    """
    wanted = {c.strip().upper() for c in lab_codes if isinstance(c, str) and c.strip()}
    if not wanted or not isinstance(result, dict):
        return result

    # Never turn a failed report into an empty successful one.
    if not result.get("ok"):
        return result

    # The previous version read result["uuids"] with an `or []` fallback. Neither
    # sample runner returned that key, so every lab-scoped report silently collapsed
    # to zero rows while still reporting success. Fail loudly instead.
    if "uuids" not in result:
        print(
            "[DEBUG][REPORTER][SCOPE] cannot scope to labs "
            f"{sorted(wanted)}: result has no 'uuids' key (keys={sorted(result)}). "
            "Returning the unscoped report."
        )
        scoped = dict(result)
        scoped["scope"] = {
            "kind": "lab",
            "lab_codes": sorted(wanted),
            "error": "report did not carry a uuid list; scope could not be applied",
        }
        return scoped

    uuids = [u for u in (result.get("uuids") or []) if _lab_of(u) in wanted]
    scoped = dict(result)
    scoped.update(_tabulate_sample_uuids(uuids))
    scoped["uuids"] = uuids
    scoped["uuids_saved"] = len(uuids)
    scoped["rows_returned"] = len(uuids)
    # task 812 left the preview listing non-KAM UIDs alongside a zero row count,
    # because `dict(result)` copied it verbatim.
    scoped["uuid_preview"] = uuids[:10]
    scoped["scope"] = {"kind": "lab", "lab_codes": sorted(wanted),
                       "applied_because": "no project id resolved for the requested scope"}
    return scoped


def _lab_of_protocol_title(title: str) -> str | None:
    """Lab code embedded in a protocol title (``P.<LAB>-<YYMMDD>-<rest>``)."""
    m = re.match(r"^P\.(?P<lab>[^-]+)-\d{6}-", str(title))
    return m.group("lab").upper() if m else None


def _scope_protocols_to_labs(result: dict, lab_codes: list[str]) -> dict:
    """Narrow a protocols report to the requested lab(s).

    Only the samples block used to be scoped, so an RPPR for a LAB reported that
    lab's samples next to every protocol in the database.
    """
    wanted = {c.strip().upper() for c in lab_codes if isinstance(c, str) and c.strip()}
    if not wanted or not isinstance(result, dict) or not result.get("ok"):
        return result
    if "titles" not in result:
        print(f"[DEBUG][REPORTER][SCOPE] cannot scope protocols to {sorted(wanted)}: "
              "result has no 'titles' key. Returning the unscoped report.")
        return result

    titles = [t for t in (result.get("titles") or []) if _lab_of_protocol_title(t) in wanted]
    lab_counts: dict[str, int] = {}
    year_counts: dict[str, int] = {}
    month_counts: dict[str, int] = {}
    title_re = re.compile(r"^P\.(?P<lab>[^-]+)-(?P<yymmdd>\d{6})-")
    unparsable = 0
    for title in titles:
        m = title_re.match(str(title))
        if not m:
            unparsable += 1
            continue
        lab_counts[m.group("lab")] = lab_counts.get(m.group("lab"), 0) + 1
        ymd = m.group("yymmdd")
        year_counts[ymd[:2]] = year_counts.get(ymd[:2], 0) + 1
        month_counts[ymd[:4]] = month_counts.get(ymd[:4], 0) + 1

    scoped = dict(result)
    scoped["titles"] = titles
    scoped["titles_saved"] = len(titles)
    scoped["titles_preview"] = titles[:10]
    scoped["rows_returned"] = len(titles)
    scoped["labs_table"] = dict(sorted(lab_counts.items(), key=lambda kv: (-kv[1], kv[0])))
    scoped["years_table"] = dict(sorted(year_counts.items(), key=lambda kv: (-kv[1], kv[0])))
    scoped["months_table"] = dict(sorted(month_counts.items(), key=lambda kv: kv[0]))
    scoped["unparsable_titles"] = unparsable
    scoped["scope"] = {"kind": "lab", "lab_codes": sorted(wanted)}
    return scoped


def _scope_published_to_labs(result: dict, lab_codes: list[str]) -> dict:
    """Narrow a published report to the requested lab(s).

    ``study_count`` and ``studies`` are recomputed from the surviving UIDs. Leaving
    them global while the row count narrowed is what produced task 812's
    self-contradicting block: rows_returned 50179 alongside study_count 42 and 26
    labs including FLY: 15994, all of it labelled a Kamm report.

    UIDs whose lab cannot be parsed are dropped from the scoped set and counted in
    ``unattributable_uids`` rather than silently vanishing.
    """
    wanted = {c.strip().upper() for c in lab_codes if isinstance(c, str) and c.strip()}
    if not wanted or not isinstance(result, dict) or not result.get("ok"):
        return result

    samples = result.get("samples")
    if not isinstance(samples, dict) or not samples.get("ok"):
        return result
    if "uuid_studies" not in samples:
        print(f"[DEBUG][REPORTER][SCOPE] cannot scope published to {sorted(wanted)}: "
              "samples block has no 'uuid_studies' key. Returning the unscoped report.")
        return result

    pairs = samples.get("uuid_studies") or []
    kept = [(u, s) for u, s in pairs if _lab_of(u) in wanted]
    unattributable = sum(1 for u, _ in pairs if _lab_of(u) is None)
    uuids = [u for u, _ in kept]
    study_set = {s for _, s in kept if s}

    scoped_samples = dict(samples)
    scoped_samples.update(_tabulate_sample_uuids(uuids))
    scoped_samples["uuids"] = uuids
    scoped_samples["uuid_studies"] = kept
    scoped_samples["rows_returned"] = len(uuids)
    scoped_samples["study_count"] = len(study_set)
    scoped_samples["studies"] = sorted(study_set)
    scoped_samples["unattributable_uids"] = unattributable

    scoped = dict(result)
    scoped["samples"] = scoped_samples
    scoped["scope"] = {"kind": "lab", "lab_codes": sorted(wanted)}
    return scoped


def reporter_reply_footer(
    config,
    reporter_result: dict,
    saved_files: "dict[str, str] | None",
    summary_mode: str,
) -> list[str]:
    """Build the bullet-list footer appended under the chatter's narrative.

    Two defects this replaces. The footer read top-level ``rows_returned`` and
    ``uuid_report_file``, which an RPPR result does not have — they live under
    ``samples`` — so a working RPPR still printed "Rows returned: 0" and
    "Download report: None" while having generated three files, all present in
    ``saved_files`` and none of them linked.

    And a lab-scoped report never said so. The entity agent resolves
    ``{"labs":["Kamm"],"lab_codes":["KAM"]}``, the plan comes back ``project: null``,
    and a lab scope is substituted silently. The user asked for "the Kamm project"
    and was never told that Kamm is a lab, not one of the six investigations.
    """
    lines: list[str] = []
    saved_files = saved_files or {}

    if summary_mode == "RPPR":
        samples = reporter_result.get("samples") or {}
        rows = samples.get("rows_returned")
    else:
        rows = reporter_result.get("rows_returned")
    lines.append(f"- **Rows returned:** {rows}")

    if summary_mode == "RPPR":
        for key, path in saved_files.items():
            lines.append(f"- **{key.replace('_', ' ').capitalize()}:** `{path}`")
        if not saved_files:
            lines.append("- **Download report:** none generated")
    else:
        lines.append(f"- **Download report:** `{reporter_result.get('uuid_report_file')}`")

    scope = (reporter_result.get("scope")
             or (reporter_result.get("samples") or {}).get("scope")
             or {})
    if isinstance(scope, dict) and scope.get("kind") == "lab":
        codes = ", ".join(scope.get("lab_codes") or [])
        known = sorted(getattr(config, "INVESTIGATION_NAME_TO_ID", None) or {})
        note = (
            f"- **Scope:** this report covers lab {codes}, not a project. "
            "The name you gave is recorded as a lab code rather than an investigation"
        )
        if known:
            note += f"; the investigations are {', '.join(known)}"
        lines.append(note + ".")

    return lines


def _drop_uuid_list(result: dict) -> dict:
    """Strip the full uuid list from a reporter result once scoping has used it.

    The list is an internal hand-off between the sample runners and
    ``_scope_report_to_labs``. Leaving it in place would put up to ~50k strings into
    the debug payload, the metadata bundle and any LLM context built from them.
    ``uuids_saved``, ``uuid_preview`` and ``uuid_report_file`` remain.
    """
    bulk = ("uuids", "uuid_studies", "titles")
    if not isinstance(result, dict):
        return result
    cleaned = {k: v for k, v in result.items() if k not in bulk}
    for block in ("samples", "protocols", "published"):
        inner = cleaned.get(block)
        if isinstance(inner, dict):
            cleaned[block] = {k: v for k, v in inner.items() if k not in bulk}
            # published nests its own samples/protocols blocks one level deeper.
            for nested in ("samples", "protocols"):
                if isinstance(cleaned[block].get(nested), dict):
                    cleaned[block][nested] = {
                        k: v for k, v in cleaned[block][nested].items() if k not in bulk
                    }
    return cleaned


def run_reporter_summary(
    config,
    reporter_plan,
    log_dir: "str | Path | None",
    lab_codes: "list[str] | None" = None,
) -> "tuple[dict, dict[str, str], dict]":
    """
    Execute the summary reporter pipeline (samples / protocols / published / RPPR).

    Returns (reporter_result, saved_files, reporter_summary) where:
      - reporter_result  — raw dict from run_project_*_report helpers
      - saved_files      — {key: file_path} of output files written to log_dir
      - reporter_summary — condensed dict suitable for passing to a chatter/LLM

    This is shared between run_query (orchestrator) and _plan_tool_reporter (agents)
    to avoid duplicating the execution logic.
    """
    project = reporter_plan.project
    if isinstance(project, str) and not project.strip():
        project = None
    years = reporter_plan.years or []
    month_range = reporter_plan.month_range
    day_range = reporter_plan.day_range
    summary_mode = reporter_plan.summary_mode or "samples"

    # A scoped request whose project did not resolve would otherwise silently run
    # across every project. Fall back to the lab codes the entity agent resolved.
    #
    # Read them from the plan when the caller did not pass any: planner/tools.py:305
    # and granular.py:132 both call this without lab_codes, so once scoping worked
    # they would have gone on reporting the whole database. Task 812 shows the
    # reporter already populates reporter_context.lab_codes.
    if not lab_codes:
        ctx = getattr(reporter_plan, "reporter_context", None)
        lab_codes = list(getattr(ctx, "lab_codes", None) or []) if ctx is not None else []
        if lab_codes:
            print(f"[DEBUG][REPORTER] lab_codes not passed; taking {lab_codes} "
                  "from reporter_plan.reporter_context")
    fallback_labs = list(lab_codes or []) if project is None else []
    if fallback_labs:
        print(f"[DEBUG][REPORTER] No project resolved; scoping by lab {fallback_labs}")

    print(f"[DEBUG][REPORTER] Summary mode: {summary_mode}, project: {project}, years: {years}")

    def _guarded(block: str, fn, *a, **kw) -> dict:
        """
        Run one report block, degrading that block alone on failure.

        A single blanket `except` around all three RPPR blocks meant one raising
        runner discarded the *successful* ones too: task 815's protocols failure
        threw away a perfectly good samples block and the whole reply became
        "The reporter agent could not run the project report."
        """
        try:
            return fn(*a, **kw)
        except Exception as e:
            print(f"[DEBUG][REPORTER] {block} block failed: {e!r}")
            return {"ok": False, "error": repr(e), "block": block}

    try:
        if summary_mode == "RPPR":
            samples_result = _scope_report_to_labs(_guarded(
                "samples", run_project_sample_report,
                config, project, years=years, month_range=month_range,
                day_range=day_range, outputs_root=log_dir or "outputs",
            ), fallback_labs)
            protocols_result = _scope_protocols_to_labs(_guarded(
                "protocols", run_project_protocols_report,
                config, project, years=years, month_range=month_range,
                day_range=day_range, outputs_root=log_dir or "outputs",
            ), fallback_labs)
            published_result = _scope_published_to_labs(_guarded(
                "published", run_project_published_report,
                config, project, years=years, month_range=month_range,
                day_range=day_range, outputs_root=log_dir or "outputs",
            ), fallback_labs)
            reporter_result: dict = {
                "ok": samples_result.get("ok"),
                "summary_mode": "RPPR",
                "samples": samples_result,
                "protocols": protocols_result,
                "published": published_result,
                "rows_returned": samples_result.get("rows_returned"),
                "filters": samples_result.get("filters") or {},
            }
        elif summary_mode == "protocols":
            reporter_result = _scope_protocols_to_labs(run_project_protocols_report(
                config, project, years=years, month_range=month_range,
                day_range=day_range, outputs_root=log_dir or "outputs",
            ), fallback_labs)
        elif summary_mode == "published":
            reporter_result = _scope_published_to_labs(run_project_published_report(
                config, project, years=years, month_range=month_range,
                day_range=day_range, outputs_root=log_dir or "outputs",
            ), fallback_labs)
        else:  # "samples" (default)
            reporter_result = _scope_report_to_labs(run_project_sample_report(
                config, project, years=years, month_range=month_range,
                day_range=day_range, outputs_root=log_dir or "outputs",
            ), fallback_labs)
    except Exception as e:
        reporter_result = {"ok": False, "error": repr(e)}

    # The uuid list exists so scoping can run; past this point it is dead weight.
    # reporter_result reaches debug_payload (orchestrator.py:715,769) and
    # build_metadata_bundle, and this can be ~50k strings. The durable copy lives in
    # uuid_report_file, which is what the reply links to.
    reporter_result = _drop_uuid_list(reporter_result)

    # ── Register output files ─────────────────────────────────────────
    saved_files: dict[str, str] = {}

    def _register_file(key: str, path: "str | None") -> None:
        from pathlib import Path as _Path
        if path and _Path(path).exists():
            saved_files[key] = path

    if summary_mode == "RPPR":
        _register_file("samples_report", reporter_result.get("samples", {}).get("uuid_report_file"))
        _register_file("protocols_report", reporter_result.get("protocols", {}).get("report_file"))
        _register_file("published_report", reporter_result.get("published", {}).get("report_file"))
    elif summary_mode == "protocols":
        _register_file("protocols_report", reporter_result.get("report_file"))
    elif summary_mode == "published":
        _register_file("published_report", reporter_result.get("report_file"))
    else:
        _register_file("samples_report", reporter_result.get("uuid_report_file"))

    summary_path = persist_report_file("reporter_result", reporter_result, log_dir or "outputs", kind="report")
    if summary_path:
        saved_files["reporter_result"] = summary_path

    # ── Build condensed reporter_summary for LLM chatter ─────────────
    def _sub_summary(r: dict) -> dict:
        return {
            "rows_returned": r.get("rows_returned"),
            "top_sampletypes": top_items(r.get("sampletypes_table"), 5),
            "top_labs": top_items(r.get("labs_table"), 5),
            "years": top_items(r.get("years_table"), 10),
            "top_months": top_items(r.get("months_table"), 12),
            "db_diagnostic": r.get("db_diagnostic") or {},
            # Without this the chatter cannot tell that one block is lab-scoped and
            # another global, so it reconciles the contradiction by narrating the
            # global one — which is how task 812's prose reported 50,179 records for
            # a report whose samples block held 0.
            "scope": r.get("scope"),
        }

    reporter_summary: dict = {
        "summary_mode": summary_mode,
        "project": project,
        "project_id": reporter_result.get("project_id"),
        "filters": reporter_result.get("filters") or {},
        "scope": reporter_result.get("scope")
                 or (reporter_result.get("samples") or {}).get("scope"),
    }
    if summary_mode == "RPPR":
        reporter_summary["samples"] = _sub_summary(reporter_result.get("samples") or {})
        reporter_summary["protocols"] = {
            "rows_returned": (reporter_result.get("protocols") or {}).get("rows_returned"),
            "top_labs": top_items((reporter_result.get("protocols") or {}).get("labs_table"), 5),
            "years": top_items((reporter_result.get("protocols") or {}).get("years_table"), 10),
            "scope": (reporter_result.get("protocols") or {}).get("scope"),
        }
        reporter_summary["published"] = {
            "samples": _sub_summary((reporter_result.get("published") or {}).get("samples") or {}),
            "protocols_count": ((reporter_result.get("published") or {}).get("protocols") or {}).get("rows_returned"),
            "study_count": ((reporter_result.get("published") or {}).get("samples") or {}).get("study_count"),
            "scope": (reporter_result.get("published") or {}).get("scope"),
        }
    elif summary_mode == "protocols":
        reporter_summary.update({
            "rows_returned": reporter_result.get("rows_returned"),
            "top_labs": top_items(reporter_result.get("labs_table"), 5),
            "years": top_items(reporter_result.get("years_table"), 10),
            "top_months": top_items(reporter_result.get("months_table"), 12),
        })
    elif summary_mode == "published":
        pub_samples = reporter_result.get("samples") or {}
        pub_protocols = reporter_result.get("protocols") or {}
        reporter_summary.update({
            "samples": _sub_summary(pub_samples),
            "study_count": pub_samples.get("study_count"),
            "studies": pub_samples.get("studies"),
            "protocols_count": pub_protocols.get("rows_returned"),
            "top_protocol_labs": top_items(pub_protocols.get("labs_table"), 5),
        })
    else:
        reporter_summary.update(_sub_summary(reporter_result))

    print(f"[DEBUG][REPORTER] result ok={reporter_result.get('ok')}, rows={reporter_result.get('rows_returned')}, files={list(saved_files.keys())}")
    # The reason only ever reached the user's chat reply (orchestrator.py:899). A
    # production MySQL outage on 2026-08-20 left zero trace in docker logs,
    # nextseek.log or django.log while the transcript carried the exception.
    if reporter_result.get("ok") is False:
        print(f"[DEBUG][REPORTER] report failed: {reporter_result.get('error')!r}")
    return reporter_result, saved_files, reporter_summary
