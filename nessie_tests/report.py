from __future__ import annotations
import html
from pathlib import Path
from nessie_tests.manifest import NessieManifest, cost_summary

_ROW = ("<tr class='{cls}'><td>{id}</td><td>{family}</td><td>{route}</td><td>{engine}</td>"
        "<td>{status}</td><td>{reason}</td></tr>")
_OBS_ROW = ("<tr class='{cls}'><td>{turn}</td><td>{field}</td><td>{expected}</td>"
            "<td>{observed}</td><td>{verdict}</td><td>{reason}</td></tr>")


def _display_status(entry) -> str:
    """What the status column should say.

    An ``xpass`` must never render as ``xfail``: the whole point is that a
    known_fail case stopped failing, and collapsing the two hides that.

    ``outage`` wins over everything, including ``xfail``. It is not a product
    result at all — the provider chain gave up before the turn ran — so both
    "this failed" and "this failed as expected" would be claims the run cannot
    support.

    ``no_assertions`` renders as itself for the same reason, which is why the
    ``xfail`` test below lists ``failed``/``error`` explicitly instead of
    "anything that is not passed": a known_fail case that evaluated zero criteria
    did not fail as expected, it failed to test anything.
    """
    if getattr(entry, "outage", False):
        return "outage"
    if entry.status == "xpass":
        return "xpass"
    if entry.expected_fail and entry.status in ("failed", "error"):
        return "xfail"
    return entry.status


def _obs_class(o) -> str:
    """Three outcomes, three classes — never two.

    ``CriterionObservation.skipped`` carries ``passed=True``, because an
    unevaluable criterion is not evidence in either direction. Reading only
    ``passed`` therefore painted a skip GREEN and folded it into "all passed",
    which is the exact claim the flag was added to make impossible.
    """
    if o.skipped:
        return "skipped"
    return "passed" if o.passed else "failed"


def _observations_label(observations) -> str:
    """The ``<details>`` summary, counting skips apart from passes.

    "N criteria, all passed" about a case whose entire second turn was skipped is
    the line a reader acts on WITHOUT opening the table, and it was wrong for
    exactly the population the per-case vacuity ruling covers:
    ``tree.then_ask_about`` is the only multi-turn variant in any floored family.
    """
    n_failed = sum(1 for o in observations if not o.passed and not o.skipped)
    n_skipped = sum(1 for o in observations if o.skipped)
    parts = [f"{len(observations)} criteria"]
    if n_failed:
        parts.append(f"{n_failed} failed")
    if n_skipped:
        parts.append(f"{n_skipped} skipped")
    if not n_failed and not n_skipped:
        parts.append("all passed")
    return "observed (" + ", ".join(parts) + ")"


def _observations_table(entry) -> str:
    """Expected-vs-observed for EVERY criterion — passing, failing and skipped.

    This used to filter to `not o.passed`, so a passing case rendered nothing at all.
    That blind spot is exactly how the assay count went 324 -> 5 and the MetNet study
    count went 10 -> 51 while both cases stayed green: the report could show that a
    criterion was satisfied, but never what the answer actually was.

    Showing passing rows is what makes review by OUTPUT possible rather than review by
    pass rate. Showing a SKIPPED row AS a skip is what stops the same table telling the
    operator that a turn which asserted nothing asserted everything.

    ``reason`` gets a column because two skips are not interchangeable: "field family
    not observable over HTTP" is unconditional, while "not observable on a
    container_cc turn" is conditional on the route the turn actually took, and only
    the second one is evidence that the case and the router disagree.
    """
    if not entry.observations:
        return ""
    rows = "\n".join(
        _OBS_ROW.format(cls=_obs_class(o),
                        turn=html.escape(o.turn), field=html.escape(o.field),
                        expected=html.escape(f"{o.op} {o.expected!r}"),
                        observed=html.escape(str(o.observed)),
                        # Upper case and spelled out: a colour is no signal at all on
                        # a printed, pasted or grepped report.
                        verdict="SKIPPED" if o.skipped else ("passed" if o.passed else "failed"),
                        reason=html.escape(o.reason or ""))
        for o in entry.observations)
    return (f"<details><summary>{_observations_label(entry.observations)}</summary>"
            "<table border=1 cellpadding=3><tr><th>turn</th><th>field</th>"
            f"<th>expected</th><th>observed</th><th>result</th><th>why</th></tr>"
            f"{rows}</table></details>")


def generate_html(manifest: NessieManifest, out_dir: Path) -> Path:
    rows = "\n".join(
        _ROW.format(cls=("outage" if getattr(e, "outage", False) else e.status),
                    id=html.escape(e.id), family=html.escape(e.family),
                    route=html.escape(e.route or ""), engine=html.escape(e.engine or ""),
                    status=_display_status(e),
                    reason=html.escape(e.reason or ", ".join(e.failed_criteria))
                           + _observations_table(e))
        for e in manifest.entries)
    cost = cost_summary(manifest.entries)
    doc = (f"<html><head><title>nessie {manifest.tier}/{manifest.scope}</title>"
           "<style>.failed{background:#fdd}.passed{background:#dfd}.error{background:#fbb}"
           ".xpass{background:#ffe0b2}.outage{background:#e0e0e0}"
           # Its own colour, not red and not green: nothing was tested, so the
           # row is neither a regression nor a result.
           ".no_assertions{background:#e1bee7}"
           # Same reasoning one level down, for a single criterion rather than a
           # whole case — and it doubles as the class for a `skipped` ENTRY,
           # which had no styling of its own either.
           ".skipped{background:#dceefb}</style></head>"
           f"<body><h1>Nessie tests — tier={manifest.tier} scope={manifest.scope}</h1>"
           f"<p>{manifest.started_at} → {manifest.ended_at}</p>"
           # The report rendered no cost at all, which left the operator's only
           # figure the management command's `$0.0`. `cost_summary` is shared, so
           # this line and the CLI's cannot drift; it says `unmeasured` when the
           # harness stopped polling before `query_complete` and `PARTIAL` when
           # only some cases reported — an NS-routed case never reports one.
           f"<p>cost: {html.escape(cost['cost_display'])}</p>"
           "<table border=1 cellpadding=4><tr><th>id</th><th>family</th><th>route</th>"
           f"<th>engine</th><th>status</th><th>reason</th></tr>{rows}</table></body></html>")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / "report.html"
    p.write_text(doc, encoding="utf-8")
    return p
