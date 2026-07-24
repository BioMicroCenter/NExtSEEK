from __future__ import annotations
import html
from pathlib import Path
from nessie_tests.manifest import NessieManifest

_ROW = "<tr class='{cls}'><td>{id}</td><td>{family}</td><td>{route}</td><td>{engine}</td><td>{status}</td><td>{reason}</td></tr>"


def generate_html(manifest: NessieManifest, out_dir: Path) -> Path:
    rows = "\n".join(
        _ROW.format(cls=e.status, id=html.escape(e.id), family=html.escape(e.family),
                    route=html.escape(e.route or ""), engine=html.escape(e.engine or ""),
                    status=("xfail" if e.expected_fail else e.status),
                    reason=html.escape(e.reason or ", ".join(e.failed_criteria)))
        for e in manifest.entries)
    doc = (f"<html><head><title>nessie {manifest.tier}/{manifest.scope}</title>"
           "<style>.failed{background:#fdd}.passed{background:#dfd}.error{background:#fbb}</style></head>"
           f"<body><h1>Nessie tests — tier={manifest.tier} scope={manifest.scope}</h1>"
           f"<p>{manifest.started_at} → {manifest.ended_at}</p>"
           "<table border=1 cellpadding=4><tr><th>id</th><th>family</th><th>route</th>"
           f"<th>engine</th><th>status</th><th>reason</th></tr>{rows}</table></body></html>")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / "report.html"
    p.write_text(doc, encoding="utf-8")
    return p
