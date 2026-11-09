"""One HTML page for a pipeline-scenario run: totals, then every story by family."""
from __future__ import annotations

import html
from collections import defaultdict
from pathlib import Path

_COLOR = {"PASS": "#1a7f37", "PARTIAL": "#9a6700", "FAIL": "#cf222e"}


def _pipeline(outcome: dict) -> str:
    chosen = outcome.get("pipeline_key") or (outcome.get("selection") or {}).get("pipelines") or ""
    return ", ".join(chosen) if isinstance(chosen, list) else str(chosen)


def write_report(out: Path, results: list[dict]) -> Path:
    by_family: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_family[r["story"]["family"]].append(r)
    totals = {g: sum(1 for r in results if r["score"]["grade"] == g) for g in _COLOR}
    rows = []
    for family in sorted(by_family):
        rows.append(f"<h2>{html.escape(family)}</h2><table><tr><th>story</th><th>grade</th><th>route</th>"
                    "<th>pipeline</th><th>reasons</th></tr>")
        for r in by_family[family]:
            g = r["score"]["grade"]
            rows.append(
                f"<tr><td>{html.escape(r['story']['id'])}<br><small>{html.escape(r['story']['question'])}</small></td>"
                f"<td style='color:{_COLOR.get(g, '#000')}'><b>{html.escape(g)}</b></td>"
                f"<td>{html.escape(str(r['outcome']['route']))}</td>"
                f"<td>{html.escape(_pipeline(r['outcome']))}</td>"
                f"<td>{html.escape('; '.join(r['score']['reasons']))}</td></tr>")
        rows.append("</table>")
    page = ("<!doctype html><meta charset='utf-8'><title>Pipeline scenarios</title>"
            "<style>body{font:14px system-ui;margin:16px}td,th{border-bottom:1px solid #ddd;padding:4px 8px;"
            "text-align:left;vertical-align:top}table{border-collapse:collapse;width:100%}</style>"
            f"<h1>Pipeline scenarios</h1><p>{html.escape(str(totals))}</p>" + "".join(rows))
    path = out / "report.html"
    path.write_text(page, encoding="utf-8")
    return path
