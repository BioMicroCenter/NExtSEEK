"""Render the graph_search benchmark's report.md from bench.py's results.json (standard library only).

    python3 scripts/graph_search/bench_report.py [--results PATH] [--parity PATH] [--out PATH]

``--results`` defaults to ``$GS_WORK/runs/B2/results.json``, ``--out`` to ``report.md`` beside it, and ``--parity`` to
``$GS_WORK/runs/E6/parity.json`` when that file exists (pass the live rerun's parity.json after gate L).

One section per shape (query), with a table per arm family:

- HTTP (A and G): per configuration, mode, arm and account, the status counts, ``total``, p50, p95 and range of wall
  time, median response bytes, G's median database timings from ``debug_meta``, and a non-superuser's p50 against
  demo's on the same query and run.
- The id-set difference against A: from the same HTTP run (page 1; the whole set when both totals fit on one page)
  and from parity.json (every id, per scope).
- S (the SQL control): the count and the page, count and engine forms' first run, p50, p95 and range, and the index
  each plan used.
- Memory: peak RSS growth of one request per arm, from ``bench.py memory``.

Then the configuration of every run (MySQL buffer pool, Neo4j page cache and the rest) and the design's pass criteria
(section 9) where the numbers decide them. Percentiles are nearest-rank over the timed requests that answered 200.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUPERUSER = "demo"
PAGE_SIZE = 100
BREAKING = ("keyword_all_types", "or_retry_three_terms")
SECONDS_LIMIT_MS = 10_000
ACCOUNT_SCOPE_NAME = {SUPERUSER: "superuser"}


def pct(values, p: float):
    """Nearest-rank percentile; None for no values."""
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return values[max(0, math.ceil(p / 100 * len(values)) - 1)]


def fmt_ms(value) -> str:
    if value is None:
        return ""
    return f"{value:,.1f}" if value < 100 else f"{value:,.0f}"


def fmt_n(value) -> str:
    if value is None:
        return ""
    return f"{value:,}" if isinstance(value, int) else str(value)


def spread(values) -> str:
    """p50 / p95 / range of timings in ms."""
    values = [v for v in values if v is not None]
    if not values:
        return ""
    lo, hi = min(values), max(values)
    rng = fmt_ms(lo) if lo == hi else f"{fmt_ms(lo)} to {fmt_ms(hi)}"
    return f"{fmt_ms(pct(values, 50))} / {fmt_ms(pct(values, 95))} / {rng}"


def _default(path_env: str, *parts: str) -> Path | None:
    base = os.environ.get(path_env)
    return Path(base, *parts) if base else None


# ---------------------------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------------------------

def load_parity(path: Path | None) -> dict:
    """{(query, account): parity row}, accounts by the names parity.py gives its scopes."""
    if not path or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    names = {}
    for scope in data.get("scopes") or []:
        for name in scope.get("names") or []:
            names[scope["key"]] = names.get(scope["key"], []) + [name]
    out = {"_meta": {"path": str(path), "generated_at": data.get("generated_at"), "summary": data.get("summary")}}
    for row in data.get("compat") or []:
        for name in names.get(row["scope"], []):
            account = SUPERUSER if name == "superuser" else name
            out[(row["query"], account)] = row
    return out


def timed(cell: dict) -> list[dict]:
    return [s for s in cell.get("samples") or [] if not s.get("warmup")]


def ok_ms(cell: dict) -> list[float]:
    return [s["wall_ms"] for s in timed(cell) if s.get("status") == 200 and s.get("wall_ms") is not None]


def statuses(cell: dict) -> str:
    counts: dict[str, int] = {}
    for s in timed(cell):
        key = str(s.get("status"))
        counts[key] = counts.get(key, 0) + 1
    return ", ".join(f"{k} x{v}" for k, v in sorted(counts.items()))


def cell_total(cell: dict):
    totals = {s.get("total") for s in cell.get("samples") or [] if s.get("status") == 200}
    if not totals:
        return None
    return totals.pop() if len(totals) == 1 else "varies: " + ", ".join(fmt_n(t) for t in sorted(totals))


# ---------------------------------------------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------------------------------------------

def http_rows(q: str, runs: list[dict]) -> list[str]:
    lines = []
    for run in runs:
        cells = [c for c in run.get("cells") or [] if c["query"] == q]
        demo_p50 = {c["arm"]: pct(ok_ms(c), 50) for c in cells if c["account"] == SUPERUSER}
        for c in cells:
            ms = ok_ms(c)
            db = [s.get("db") or {} for s in timed(c) if s.get("status") == 200 and s.get("db")]
            db_text = " / ".join(fmt_ms(statistics.median([d[k] for d in db if d.get(k) is not None]))
                                 if any(d.get(k) is not None for d in db) else ""
                                 for k in ("cypher_ms", "count_ms", "hydrate_ms")) if db else ""
            size = [s["bytes"] for s in timed(c) if s.get("bytes") is not None]
            ratio = ""
            if c["account"] != SUPERUSER and ms and demo_p50.get(c["arm"]):
                ratio = f"{pct(ms, 50) / demo_p50[c['arm']]:.2f}x"
            mode = run["mode"]
            if mode == "cold":
                pos = next((s.get("since_restart") for s in timed(c)), None)
                mode = f"cold ({pos} since restart)"
            elif mode == "concurrent" and c.get("makespan_ms"):
                mode = f"concurrent x{c.get('clients')} (makespan {', '.join(fmt_ms(m) for m in c['makespan_ms'])})"
            lines.append(f"| {run['config_label']} | {mode} | {c['arm']} | {c['account']} | {statuses(c)} | "
                         f"{fmt_n(cell_total(c))} | {spread(ms)} | {fmt_n(int(statistics.median(size))) if size else ''}"
                         f" | {db_text} | {ratio} |")
    if not lines:
        return []
    return ["| Configuration | Mode | Arm | Account | Status | Total | p50 / p95 / range ms | Bytes | "
            "G db ms (cypher / count / hydrate) | p50 vs demo |",
            "|---|---|---|---|---|---|---|---|---|---|"] + lines


def difference_rows(q: str, http_runs: list[dict], parity: dict, accounts: list[str]) -> list[str]:
    lines = []
    for run in http_runs:
        if run["mode"] != "warm":
            continue
        cells = {(c["arm"], c["account"]): c for c in run.get("cells") or [] if c["query"] == q}
        for account in accounts:
            a, g = cells.get(("A", account)), cells.get(("G", account))
            if not g:
                continue
            if not a:
                text = "no A arm in this run"
                a_total = ""
            else:
                a_total = fmt_n(cell_total(a))
                a_ids, g_ids = set(a.get("page_ids") or []), set(g.get("page_ids") or [])
                whole = (isinstance(cell_total(a), int) and isinstance(cell_total(g), int)
                         and cell_total(a) <= len(a.get("page_ids") or []) + 0
                         and cell_total(g) <= len(g.get("page_ids") or []))
                text = (f"{len(a_ids - g_ids)} / {len(g_ids - a_ids)}"
                        + (" (whole set)" if whole else " (page 1 only; A's row order is not id order)"))
            p = parity.get((q, account))
            ptext = ""
            if p:
                if p.get("status") == "compared":
                    ptext = (f"{fmt_n(p.get('a_only_n'))} / {fmt_n(p.get('g_only_n'))} "
                             f"(A {fmt_n(p.get('a_total'))}, G {fmt_n(p.get('g_total'))}, "
                             f"{p.get('undeclared_n', 0)} undeclared)")
                else:
                    ptext = f"{p.get('status')}: {str(p.get('error', ''))[:80]}"
            lines.append(f"| {run['config_label']} | {account} | {a_total} | {fmt_n(cell_total(g))} | {text} | {ptext} |")
    if not lines and parity:
        for account in accounts:
            p = parity.get((q, account))
            if p and p.get("status") == "compared":
                lines.append(f"| (parity only) | {account} | {fmt_n(p.get('a_total'))} | {fmt_n(p.get('g_total'))} | | "
                             f"{fmt_n(p.get('a_only_n'))} / {fmt_n(p.get('g_only_n'))} |")
    if not lines:
        return []
    return ["| Configuration | Account | A total | G total | This run, A not G / G not A | "
            "Parity, A not G / G not A |", "|---|---|---|---|---|---|"] + lines


def _form(cell: dict, form: str) -> tuple[str, str]:
    entry = (cell.get("forms") or {}).get(form) or {}
    samples = entry.get("samples") or []
    if not samples:
        return "", ""
    first = samples[0]
    warm = [s["ms"] for s in samples[1:] if s.get("status") == "ok"]
    bad = [s for s in samples if s.get("status") != "ok"]
    text = f"{fmt_ms(first['ms'])} / {spread(warm)}" if warm else fmt_ms(first["ms"])
    if bad:
        text += f" ({bad[0].get('status')}: {str(bad[0].get('error', ''))[:60]})"
    keys = sorted({str(r.get("key")) for r in entry.get("explain") or [] if r.get("table") in ("A", "u", None)})
    return text, ", ".join(keys)


def s_rows(q: str, runs: list[dict], parity: dict) -> list[str]:
    lines = []
    for run in runs:
        for c in (c for c in run.get("cells") or [] if c["query"] == q):
            if c.get("error"):
                lines.append(f"| {run['config_label']} | {c['account']} | error: {c['error'][:100]} | | | | | |")
                continue
            count = next((s.get("value") for s in ((c.get("forms") or {}).get("count") or {}).get("samples") or []
                          if s.get("status") == "ok"), None)
            p = parity.get((q, c["account"])) or {}
            page, page_key = _form(c, "page")
            cnt, cnt_key = _form(c, "count")
            engine, _ = _form(c, "engine")
            rows = next((s.get("rows") for s in ((c.get("forms") or {}).get("engine") or {}).get("samples") or []
                         if s.get("status") == "ok"), None)
            stage = " (Python stage follows)" if c.get("python_stage") else ""
            lines.append(f"| {run['config_label']} | {c['account']} | {fmt_n(count)}{stage} | {fmt_n(p.get('a_total'))} "
                         f"| {page} | {cnt} | {engine}"
                         f"{'' if rows is None else f', {fmt_n(rows)} row' + ('' if rows == 1 else 's')} "
                         f"| {page_key or 'none'} ; {cnt_key or 'none'} |")
    if not lines:
        return []
    return ["| Configuration | Account | S count | A total (parity) | Page: first / p50 / p95 / range ms | "
            "Count: first / p50 / p95 / range ms | Engine (A's SQL, every row): first / p50 / p95 / range ms | "
            "Index (page ; count) |",
            "|---|---|---|---|---|---|---|---|"] + lines


def memory_rows(q: str, runs: list[dict]) -> list[str]:
    lines = []
    for run in runs:
        for c in (c for c in run.get("cells") or [] if c.get("query") == q):
            growth = c.get("growth_mb")
            kind = c.get("status_kind")
            note = (f"over the {fmt_n(c.get('budget_mb'))} MiB budget, so growth is a lower bound"
                    + (f" ({c['memory_error']})" if c.get("memory_error") else "")
                    if kind == "over_budget" else str(c.get("error") or "")[:100])
            before, after = c.get("rss_before_mb"), c.get("rss_after_mb")
            lines.append(f"| {run['config_label']} | {c.get('arm')} | {c.get('account')} | {kind} | {c.get('status')} | "
                         f"{fmt_n(c.get('total'))} | {'' if growth is None else f'{growth:,.1f}'} | "
                         f"{'' if before is None else f'{before:,.1f}'} / {'' if after is None else f'{after:,.1f}'} | "
                         f"{fmt_ms(c.get('wall_ms'))} | {note} |")
    if not lines:
        return []
    return ["| Configuration | Arm | Account | Result | Status | Total | Peak RSS growth MiB | Peak RSS before / after MiB "
            "| Wall ms | Note |", "|---|---|---|---|---|---|---|---|---|---|"] + lines


def config_text(run: dict) -> str:
    cfg = run.get("config") or {}
    parts = [f"{k}={v}" for k, v in (cfg.get("given") or {}).items()]
    if run["kind"] == "http":
        parts.append(f"base URL {cfg.get('base_url')}")
        parts.append(f"arms {', '.join(cfg.get('arms') or [])}")
        if cfg.get("clients"):
            parts.append(f"{cfg['clients']} clients x {cfg.get('rounds')} rounds")
        else:
            parts.append(f"{cfg.get('warmups')} warm-up + {cfg.get('runs')} runs")
        for name, info in (cfg.get("containers") or {}).items():
            if not info.get("inspected"):
                parts.append(f"{name}: not inspected")
                continue
            detail = [f"limit {info['memory_limit_mb']:,} MiB" if info.get("memory_limit_mb") else "no limit"]
            detail += info.get("memory_env") or []
            detail += info.get("memory_cmd") or []
            parts.append(f"{name}: {'; '.join(detail)}")
    elif run["kind"] == "sql":
        index = cfg.get("index") or {}
        parts.append(f"MySQL {cfg.get('mysql_version')} on {cfg.get('mysql_host')}, buffer pool "
                     f"{fmt_n(cfg.get('mysql_buffer_pool_mb'))} MiB, {fmt_n(cfg.get('samples'))} samples")
        parts.append(f"{index.get('name')}: " + ("created in " + fmt_ms(index.get("create_ms")) + " ms"
                                                  if index.get("created") else
                                                  "present" if index.get("present") else "absent"))
        parts.append(f"{cfg.get('runs')} runs per form (the first is the first run)")
    elif run["kind"] == "memory":
        parts.append(f"budget {fmt_n(cfg.get('budget_mb'))} MiB of address space per child, "
                     f"page size {cfg.get('page_size')}")
        cap = str(cfg.get("container_memory_max") or "")
        parts.append("lane container memory.max " + (f"{int(cap) // (1024 * 1024):,} MiB" if cap.isdigit() else cap))
    return "; ".join(parts)


# ---------------------------------------------------------------------------------------------------------------
# Pass criteria (design section 9)
# ---------------------------------------------------------------------------------------------------------------

def criteria(queries: list[str], http_runs: list[dict], memory_runs: list[dict], parity: dict) -> list[str]:
    lines = ["| Criterion | Configuration | Result |", "|---|---|---|"]
    summary = (parity.get("_meta") or {}).get("summary")
    if summary:
        lines.append(f"| Parity outside the declared differences | parity.json | "
                     f"{'PASS' if summary.get('gate_pass') else 'FAIL'}: {summary.get('undeclared_differences')} "
                     f"undeclared, {summary.get('compared')} of {summary.get('compat_pairs')} pairs compared |")
    else:
        lines.append("| Parity outside the declared differences | | no parity file |")

    warm = [r for r in http_runs if r["mode"] == "warm"]
    labels = list(dict.fromkeys(r["config_label"] for r in warm))
    for label in labels:
        cells = [c for r in warm if r["config_label"] == label for c in r.get("cells") or []]
        a_cells = {(c["query"], c["account"]): c for c in cells if c["arm"] == "A"}
        verdicts = []
        for c in (c for c in cells if c["arm"] == "G" and c["kind"] == "compat"):
            a = a_cells.get((c["query"], c["account"]))
            g_ms = ok_ms(c)
            if not a or not ok_ms(a) or not g_ms:
                continue
            a_ms = ok_ms(a)
            fine = pct(g_ms, 50) < pct(a_ms, 50) and pct(g_ms, 95) < pct(a_ms, 95)
            verdicts.append((c["query"], c["account"], fine))
        if verdicts:
            failed = [f"{q} as {acct}" for q, acct, fine in verdicts if not fine]
            lines.append(f"| G's p50 and p95 below A's on every shape (warm) | {label} | "
                         f"{'PASS' if not failed else 'FAIL'}: {len(verdicts) - len(failed)} of {len(verdicts)} "
                         f"shape-account cells" + (f"; not below A: {', '.join(failed)}" if failed else "") + " |")
        else:
            lines.append(f"| G's p50 and p95 below A's on every shape (warm) | {label} | not measured: no A arm |")
        for q in BREAKING:
            for arm in ("G", "A"):
                qcells = [c for c in cells if c["query"] == q and c["arm"] == arm]
                slow = [pct(ok_ms(c), 95) for c in qcells if ok_ms(c)]
                if not slow:
                    continue
                worst = max(slow)
                verdict = "PASS" if worst < SECONDS_LIMIT_MS else "FAIL"
                lines.append(f"| {q} in seconds, not tens of seconds ({arm}) | {label} | "
                             f"{verdict if arm == 'G' else 'for comparison'}: slowest p95 {fmt_ms(worst)} ms |")

    g_mem = [c for r in memory_runs for c in r.get("cells") or [] if c.get("arm") == "G"]
    if g_mem:
        ok = [c for c in g_mem if c.get("status_kind") == "ok" and c.get("growth_mb") is not None]
        growth = ", ".join(f"{c['query']} {c['growth_mb']:,.1f} MiB at total {fmt_n(c.get('total'))}" for c in ok)
        a_mem = [c for r in memory_runs for c in r.get("cells") or [] if c.get("arm") == "A"]
        a_text = "; A: " + ", ".join(
            f"{c['query']} {c['growth_mb']:,.1f} MiB ({c.get('status_kind')})" if c.get("growth_mb") is not None
            else f"{c['query']} ({c.get('status_kind')})" for c in a_mem) if a_mem else ""
        lines.append(f"| G's peak RSS growth bounded by the page size whatever the match count | memory | "
                     f"{'every G request answered' if len(ok) == len(g_mem) else 'a G request failed'}: {growth}"
                     f"{a_text} (the operator judges the bound) |")
    return lines


# ---------------------------------------------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------------------------------------------

def render(results: dict, parity: dict, queries: list[dict], results_path: Path) -> str:
    runs = results.get("runs") or []
    http_runs = [r for r in runs if r["kind"] == "http"]
    sql_runs = [r for r in runs if r["kind"] == "sql"]
    memory_runs = [r for r in runs if r["kind"] == "memory"]
    seen = list(dict.fromkeys(c.get("query") for r in runs for c in r.get("cells") or []))
    by_name = {q["name"]: q for q in queries}
    order = [q["name"] for q in queries if q["name"] in seen] + [n for n in seen if n not in by_name]
    accounts = list(dict.fromkeys([SUPERUSER] + [c["account"] for r in http_runs + sql_runs
                                                 for c in r.get("cells") or []]))
    meta = parity.get("_meta") or {}
    lines = [
        "# graph_search benchmark",
        "",
        f"- Rendered {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} from `{results_path.name}` "
        f"({len(runs)} run{'' if len(runs) == 1 else 's'}: "
        f"{len(http_runs)} HTTP, {len(sql_runs)} S arm, {len(memory_runs)} memory).",
        "- Parity: " + (f"`{Path(meta['path']).name}` generated {meta.get('generated_at')}." if meta
                        else "no parity file."),
        "- Arms: A is advanced_search, G is graph_search, S is advanced_search's SQL with ORDER BY A.id LIMIT 100 "
        "and an index on samples.sample_type_id (its count is the SQL stage's; advanced_search's Python stage may drop "
        "rows after it). S's engine form is the statement as advanced_search runs it, every row streamed: A's SQL time.",
        "- Times are milliseconds. p50 and p95 are nearest-rank over the timed requests that answered 200 (with 5 "
        "timed requests, p95 is the slowest). Cold rows are single requests; only the first after a restart is cold.",
        "",
        "## Pass criteria (design section 9)",
        "",
    ]
    lines += criteria(order, http_runs, memory_runs, parity)
    lines += ["", "## Shapes", ""]
    for name in order:
        q = by_name.get(name, {})
        lines += [f"### {name} ({q.get('kind', 'unknown')})", ""]
        if q.get("body") is not None:
            body = json.dumps(q["body"], ensure_ascii=False)
            lines += [f"`{body if len(body) <= 300 else body[:300] + ' ...'}`", ""]
        compat = q.get("kind") != "graph_only"  # a graph-only shape has no A side to differ from
        for title, table in (("HTTP", http_rows(name, http_runs)),
                             ("Id-set difference against A",
                              difference_rows(name, http_runs, parity, accounts) if compat else []),
                             ("S, the SQL control", s_rows(name, sql_runs, parity)),
                             ("Memory, one request per arm", memory_rows(name, memory_runs))):
            if table:
                lines += [f"{title}:", ""] + table + [""]
    lines += ["## Runs and configuration", "", "| Run | Kind | Mode | Configuration | Started | Finished | Cells | Settings |",
              "|---|---|---|---|---|---|---|---|"]
    for r in runs:
        lines.append(f"| `{r['run_id']}` | {r['kind']} | {r['mode']} | {r['config_label']} | {r.get('started_at')} | "
                     f"{r.get('finished_at', 'interrupted' if r.get('interrupted') else '')} | "
                     f"{len(r.get('cells') or [])} | {config_text(r)} |")
    return "\n".join(lines) + "\n"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--results", type=Path, default=_default("GS_WORK", "runs", "B2", "results.json"))
    p.add_argument("--parity", type=Path, default=_default("GS_WORK", "runs", "E6", "parity.json"))
    p.add_argument("--queries", type=Path, default=HERE / "queries.json")
    p.add_argument("--out", type=Path, default=None, help="default: report.md beside the results")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.results:
        raise SystemExit("set GS_WORK or pass --results")
    results = json.loads(args.results.read_text(encoding="utf-8"))
    parity = load_parity(args.parity)
    queries = json.loads(args.queries.read_text(encoding="utf-8")) if args.queries.exists() else []
    out = args.out or args.results.with_name("report.md")
    out.write_text(render(results, parity, queries, args.results), encoding="utf-8")
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
