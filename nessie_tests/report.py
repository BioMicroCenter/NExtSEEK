from __future__ import annotations
import html
from pathlib import Path
from nessie_tests.manifest import NessieManifest

_ROW = ("<tr class='{cls}'><td>{id}</td><td>{family}</td><td>{route}</td><td>{engine}</td>"
        "<td>{status}</td><td>{reason}</td></tr>")
_OBS_ROW = "<tr class='{cls}'><td>{turn}</td><td>{field}</td><td>{expected}</td><td>{observed}</td></tr>"


def _display_status(entry) -> str:
    """What the status column should say.

    An ``xpass`` must never render as ``xfail``: the whole point is that a
    known_fail case stopped failing, and collapsing the two hides that.

    ``outage`` wins over everything, including ``xfail``. It is not a product
    result at all — the provider chain gave up before the turn ran — so both
    "this failed" and "this failed as expected" would be claims the run cannot
    support.
    """
    if getattr(entry, "outage", False):
        return "outage"
    if entry.status == "xpass":
        return "xpass"
    if entry.expected_fail and entry.status in ("failed", "error"):
        return "xfail"
    return entry.status


def _observations_table(entry) -> str:
    """Expected-vs-observed for EVERY criterion, passing and failing.

    This used to filter to `not o.passed`, so a passing case rendered nothing at all.
    That blind spot is exactly how the assay count went 324 -> 5 and the MetNet study
    count went 10 -> 51 while both cases stayed green: the report could show that a
    criterion was satisfied, but never what the answer actually was.

    Showing passing rows is what makes review by OUTPUT possible rather than review by
    pass rate.
    """
    if not entry.observations:
        return ""
    n_failed = sum(1 for o in entry.observations if not o.passed)
    rows = "\n".join(
        _OBS_ROW.format(cls="failed" if not o.passed else "passed",
                        turn=html.escape(o.turn), field=html.escape(o.field),
                        expected=html.escape(f"{o.op} {o.expected!r}"),
                        observed=html.escape(str(o.observed)))
        for o in entry.observations)
    label = (f"observed ({len(entry.observations)} criteria, {n_failed} failed)"
             if n_failed else f"observed ({len(entry.observations)} criteria, all passed)")
    return (f"<details><summary>{label}</summary>"
            "<table border=1 cellpadding=3><tr><th>turn</th><th>field</th>"
            f"<th>expected</th><th>observed</th></tr>{rows}</table></details>")


def generate_html(manifest: NessieManifest, out_dir: Path) -> Path:
    rows = "\n".join(
        _ROW.format(cls=("outage" if getattr(e, "outage", False) else e.status),
                    id=html.escape(e.id), family=html.escape(e.family),
                    route=html.escape(e.route or ""), engine=html.escape(e.engine or ""),
                    status=_display_status(e),
                    reason=html.escape(e.reason or ", ".join(e.failed_criteria))
                           + _observations_table(e))
        for e in manifest.entries)
    doc = (f"<html><head><title>nessie {manifest.tier}/{manifest.scope}</title>"
           "<style>.failed{background:#fdd}.passed{background:#dfd}.error{background:#fbb}"
           ".xpass{background:#ffe0b2}.outage{background:#e0e0e0}</style></head>"
           f"<body><h1>Nessie tests — tier={manifest.tier} scope={manifest.scope}</h1>"
           f"<p>{manifest.started_at} → {manifest.ended_at}</p>"
           "<table border=1 cellpadding=4><tr><th>id</th><th>family</th><th>route</th>"
           f"<th>engine</th><th>status</th><th>reason</th></tr>{rows}</table></body></html>")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / "report.html"
    p.write_text(doc, encoding="utf-8")
    return p
