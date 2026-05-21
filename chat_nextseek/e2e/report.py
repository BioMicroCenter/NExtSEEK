"""HTML report generation. One page, family-grouped, per-variant cards."""
from __future__ import annotations

import html as _html
from collections import defaultdict
from pathlib import Path

from e2e.manifest import Manifest, ManifestEntry


def _h(v) -> str:
    return _html.escape(str(v) if v is not None else "")


def _status_color(status: str) -> str:
    return {"passed": "#10b981", "failed": "#ef4444", "skipped": "#94a3b8"}.get(status, "#64748b")


def _variant_card(e: ManifestEntry) -> str:
    color = _status_color(e.status)
    detail = ""
    if e.failed_criteria:
        detail = "<ul>" + "".join(f"<li>{_h(c)}</li>" for c in e.failed_criteria) + "</ul>"
    elif e.reason:
        detail = f'<div style="color:#64748b">{_h(e.reason)}</div>'
    return (
        f'<div style="border-left:4px solid {color};padding:6px 12px;margin:4px 0;background:#f8fafc">'
        f'<div><b>{_h(e.id)}</b> '
        f'<span style="color:{color}">[{_h(e.status.upper())}]</span> '
        f'<span style="color:#94a3b8;font-size:11px">{_h(e.elapsed_s)}s</span></div>'
        f'{detail}'
        f'</div>'
    )


def _family_section(family: str, entries: list[ManifestEntry]) -> str:
    n_pass = sum(1 for e in entries if e.status == "passed")
    n_total = len(entries)
    cards = "".join(_variant_card(e) for e in entries)
    return (
        f'<section style="margin:16px 0">'
        f'<h2>{_h(family)} <span style="color:#64748b;font-size:14px">{n_pass}/{n_total}</span></h2>'
        f'{cards}'
        f'</section>'
    )


def generate_html_report(manifest: Manifest, out_dir: Path) -> Path:
    """Generate report.html under out_dir. Returns the path."""
    by_family: dict[str, list[ManifestEntry]] = defaultdict(list)
    for e in manifest.variants:
        by_family[e.family].append(e)

    n_pass = sum(1 for e in manifest.variants if e.status == "passed")
    n_total = len(manifest.variants)

    sections = "".join(_family_section(name, entries) for name, entries in sorted(by_family.items()))

    html = (
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<title>e2e — seed {manifest.seed}</title>'
        f'<style>body{{font-family:system-ui,sans-serif;max-width:900px;margin:24px auto;color:#1e293b}}</style>'
        f'</head><body>'
        f'<h1>chat_nextseek E2E Run</h1>'
        f'<div style="color:#64748b">seed={manifest.seed} ratio={manifest.ratio} profile={manifest.profile}</div>'
        f'<div style="font-size:18px;margin:12px 0"><b>{n_pass}/{n_total}</b> variants passed</div>'
        f'{sections}'
        f'</body></html>'
    )

    path = out_dir / "report.html"
    path.write_text(html, encoding="utf-8")
    return path
