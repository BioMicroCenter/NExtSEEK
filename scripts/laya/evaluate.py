"""Offline report for the laya router (SPEC s9): report.json plus an HTML page.

Input: scored rows (jsonl) for the fine-tune, and optionally for zero-shot laya, one row per question:
  {"probabilities": {route: p}, "teacher_route": today's BAML route (after the policy's follow-up step),
   "truth_route": str|null, "either": bool, "slice": "train|calib|prompt_seen|heldout", "family": str, "entity": str,
   "history": [...]  (non-empty = a follow-up), "negation": bool, "followup_cc": bool,
   "latency_ms": float, "baml_s": float, "baml_routes": [routes of repeated BAML runs, optional]}
and the calibration json (temperature, threshold). The gate here is the live one: calibrated confidence >= the
threshold, top route not `unrelated`, and not a follow-up that the follow-up guard sends to container_cc.
Prompt-seen rows are reported in their own block and counted in no bar.
Held-out results by slice go to the operator only: this script is what reads those rows.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from NessieAI.router import laya_common  # noqa: E402

ROUTES = ("nextseek_query", "container_cc", "unrelated")
BARS = {"agreement": 0.97, "fast_path": 0.60}
BAML_COST = 0.017          # median RouteQuery, CURRENT-ROUTING 1.7
AUDIT = 0.05


def wilson(k: int, n: int, z: float = 1.96) -> dict:
    if n == 0:
        return {"k": 0, "n": 0, "rate": None, "lo": None, "hi": None}
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"k": k, "n": n, "rate": p, "lo": max(0.0, c - h), "hi": min(1.0, c + h)}


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    i = (len(xs) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def decide(row, T: float, threshold: float):
    """(laya route, gate passed)."""
    p = laya_common.apply_temperature(row["probabilities"], T)
    top = max(p, key=p.get)
    return top, (p[top] >= threshold and top != "unrelated" and not row.get("followup_cc"))


def _right(row, route):
    if row.get("either"):
        return route in ("nextseek_query", "container_cc")
    return route == row["truth_route"] if row.get("truth_route") else None


def _self_agreement(rows):
    k = n = 0
    for r in rows:
        runs = r.get("baml_routes") or []
        for i in range(len(runs)):
            for j in range(i + 1, len(runs)):
                n += 1
                k += runs[i] == runs[j]
    return wilson(k, n)


def metrics(rows, T: float, threshold: float) -> dict:
    d = [(r, *decide(r, T, threshold)) for r in rows]
    passed = [x for x in d if x[2]]
    out = {"n": len(rows), "fast_path": wilson(len(passed), len(rows)),
           "agreement": wilson(sum(r["teacher_route"] == top for r, top, _ in passed), len(passed)),
           "agreement_by_baml_route": {
               rt: wilson(sum(r["teacher_route"] == top for r, top, _ in passed if r["teacher_route"] == rt),
                          sum(1 for r, _, _ in passed if r["teacher_route"] == rt)) for rt in ROUTES}}
    truth = [(r, top if ok else r["teacher_route"]) for r, top, ok in d if r.get("truth_route") or r.get("either")]
    out["accuracy"] = {"scored": len(truth),
                       "cascade": wilson(sum(_right(r, fin) for r, fin in truth), len(truth)),
                       "baml": wilson(sum(_right(r, r["teacher_route"]) for r, _ in truth), len(truth))}
    out["latency_ms"] = {"p50": pct([r.get("latency_ms") for r in rows], 0.5), "p95": pct([r.get("latency_ms") for r in rows], 0.95)}
    out["baml_s"] = {"p50": pct([r.get("baml_s") for r in rows], 0.5), "p95": pct([r.get("baml_s") for r in rows], 0.95)}
    out["baml_self_agreement"] = _self_agreement(rows)
    rate = out["fast_path"]["rate"] or 0.0
    out["cost_saved_per_turn_usd"] = BAML_COST * rate - BAML_COST * AUDIT * rate
    return out


def slices(rows):
    yield "family", lambda r: r.get("family")
    yield "entity", lambda r: r.get("entity")
    yield "route", lambda r: r.get("teacher_route")
    yield "followups", lambda r: "yes" if r.get("history") else None
    yield "howto_docs", lambda r: "yes" if r.get("entity") == "HOWTO_DOCS" else None
    yield "negation", lambda r: "yes" if r.get("negation") else None


def variant_report(rows, T: float, threshold: float) -> dict:
    scored = [r for r in rows if r.get("slice") != "prompt_seen"]
    seen = [r for r in rows if r.get("slice") == "prompt_seen"]
    overall = metrics(scored, T, threshold)
    by = {}
    for name, key in slices(scored):
        groups = {}
        for r in scored:
            if key(r) is not None:
                groups.setdefault(key(r), []).append(r)
        by[name] = {g: metrics(rs, T, threshold) for g, rs in sorted(groups.items())}
    agree, fast = overall["agreement"], overall["fast_path"]
    acc = overall["accuracy"]
    bars = {"agreement_97": bool(agree["n"] and agree["rate"] >= BARS["agreement"]),
            "cascade_not_worse_than_baml": acc["cascade"]["k"] >= acc["baml"]["k"],
            "fast_path_60": bool(fast["n"] and fast["rate"] >= BARS["fast_path"])}
    bars["all"] = all(bars.values())
    return {"overall": overall, "slices": by, "prompt_seen_not_counted": metrics(seen, T, threshold) if seen else None,
            "bars": bars}


def build_report(fine_rows, calibration: dict, zero_rows=None, zero_calibration=None) -> dict:
    rep = {"calibration": {k: calibration[k] for k in ("revision", "temperature", "threshold")},
           "finetune": variant_report(fine_rows, calibration["temperature"], calibration["threshold"])}
    if zero_rows is not None:
        zc = zero_calibration or calibration
        rep["zeroshot"] = variant_report(zero_rows, zc["temperature"], zc["threshold"])
    return rep


def _cell(w):
    return "n/a" if w["rate"] is None else f"{w['rate']:.1%} ({w['k']}/{w['n']}, {w['lo']:.1%} to {w['hi']:.1%})"


def render_html(rep: dict) -> str:
    h = ["<!doctype html><meta charset=utf-8><title>laya offline report</title>",
         "<style>body{font:14px sans-serif;margin:2em;max-width:70em}table{border-collapse:collapse}"
         "td,th{border:1px solid #bbb;padding:.3em .6em;text-align:left}.ok{background:#dfd}.no{background:#fdd}</style>",
         f"<h1>laya offline report</h1><p>revision {html.escape(str(rep['calibration']['revision']))}, "
         f"temperature {rep['calibration']['temperature']}, threshold {rep['calibration']['threshold']}</p>"]
    for name in ("finetune", "zeroshot"):
        if name not in rep:
            continue
        v = rep[name]
        h.append(f"<h2>{name}</h2><table><tr><th>bar</th><th>result</th></tr>")
        for b, ok in v["bars"].items():
            h.append(f"<tr class={'ok' if ok else 'no'}><td>{b}</td><td>{'pass' if ok else 'FAIL'}</td></tr>")
        h.append("</table><h3>Overall and slices</h3><table><tr><th>slice</th><th>n</th><th>fast path</th>"
                 "<th>agreement</th><th>cascade correct</th><th>BAML correct</th></tr>")
        rows = [("overall", v["overall"])] + [(f"{k}: {g}", m) for k, gs in v["slices"].items() for g, m in gs.items()]
        for label, m in rows:
            h.append(f"<tr><td>{html.escape(label)}</td><td>{m['n']}</td><td>{_cell(m['fast_path'])}</td>"
                     f"<td>{_cell(m['agreement'])}</td><td>{_cell(m['accuracy']['cascade'])}</td>"
                     f"<td>{_cell(m['accuracy']['baml'])}</td></tr>")
        h.append("</table>")
    return "\n".join(h)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--finetune", required=True)
    ap.add_argument("--zeroshot")
    ap.add_argument("--calibration", default=str(REPO / "NessieAI/router/laya_calibration.json"))
    ap.add_argument("--out-dir", required=True, help="outside every git repo: the report quotes slices of held-out rows")
    a = ap.parse_args(argv)
    out = pathlib.Path(a.out_dir).expanduser().resolve()
    if REPO in [out, *out.parents]:
        sys.exit("refusing to write a held-out report inside the public repo")
    rd = lambda p: [json.loads(ln) for ln in pathlib.Path(p).read_text().splitlines() if ln.strip()]  # noqa: E731
    cal = json.loads(pathlib.Path(a.calibration).read_text())
    rep = build_report(rd(a.finetune), cal, rd(a.zeroshot) if a.zeroshot else None)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    (out / "report.html").write_text(render_html(rep), encoding="utf-8")
    print("bars:", rep["finetune"]["bars"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
