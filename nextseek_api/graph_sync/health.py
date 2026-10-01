"""Pure judgement of the graph sync status body: what counts as a problem, and the line that says so.

Standard library only, and it must stay that way. The smoke suite imports it on a host with no Django installed
(``ci/smoke/test_graph_sync_status.py``, run under ``uv run --no-project``), and ``manage.py graph_sync_health``
imports it in the app container to judge the same body on every box, production included. One module, so both say
the same thing. ``ci/smoke/test_graph_sync_health_unit.py`` pins it against recorded bodies in that Django-free lane.

Each function takes the decoded body of GET /nextseek_api/admin/graph-sync/status/ (``build_status``) and returns
lines, one per problem; an empty list means healthy. What counts is decided by the endpoint (``state.py``): a failing
row is an open outbox row that has failed since it last succeeded and that no worker is retrying; it is overdue once
it has been failing longer than its back-off plus 30 minutes, when its retry has come due and failed too. A failed run
is the latest full, reconcile, catalog or drift run when it ended failed or abandoned, or a full sync or reconcile
its data refused, overdue on the same clock. Drift is the latest drift run's own verdict.
"""
from __future__ import annotations

NEW_PARTS = ("failing", "failed_runs")
DRIFT_NAMES_SHOWN = 6
DRIFT_REMEDY = (". It stays red until a newer drift run: after the cause is fixed, run "
                "`manage.py graph_sync --drift` in the app container")
FRESHNESS_JOBS = ("full", "reconcile", "drift", "outbox")
# The DERIVED_FROM label classes only the operator's approval writes (labels.CHANGED, labels.CLEARED).
LABEL_CLASSES_AWAITING_APPROVAL = ("changed", "cleared")
_HOURS_FROM_S = 2 * 3600          # a duration this long or longer reads in hours


def _duration(seconds) -> str:
    if seconds is None:
        return "an unknown time"
    seconds = float(seconds)
    if seconds < _HOURS_FROM_S:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} h"


def _row_line(row: dict) -> str:
    dead = ", dead" if row.get("dead") else ""
    return (f"{row.get('kind')} {row.get('key')}: failing for {_duration(row.get('age_s'))} against "
            f"{_duration(row.get('threshold_s'))}, attempts {row.get('attempts')}{dead}, "
            f"next retry {row.get('next_retry_at') or 'none'}: {row.get('error') or 'no error recorded'}")


def _run_line(run: dict) -> str:
    return (f"{run.get('kind')} run {run.get('id')} {run.get('status')} (trigger {run.get('trigger') or '?'}, "
            f"finished {run.get('finished_at') or 'never'}, {_duration(run.get('age_s'))} ago): "
            f"{run.get('error') or 'no error recorded'}")


def missing_parts(body: dict) -> list[str]:
    """The failure parts a body lacks: a box running an app image older than these checks."""
    return [part for part in NEW_PARTS if part not in body]


def stale_jobs(body: dict) -> list[str]:
    """One line per job the status reports stale: a sync or a drift check that stopped, or a drain that left a row
    waiting an hour."""
    freshness = body.get("freshness") or {}
    lines = []
    for job in FRESHNESS_JOBS:
        part = freshness.get(job) or {}
        if part.get("status") == "stale":
            lines.append(f"{job} is stale: {_duration(part.get('age_s'))} old against "
                         f"{_duration(part.get('threshold_s'))}")
    return lines


def dead_rows(body: dict) -> list[str]:
    """One line per kind with rows at the attempt limit: work tried to the end and never done."""
    dead = (body.get("outbox") or {}).get("dead") or {}
    return [f"{count} {kind} rows are dead (at the attempt limit, their work never done); once the cause is fixed, "
            "`manage.py graph_sync --requeue-dead` puts them back"
            for kind, count in sorted(dead.items())]


def overdue_rows(body: dict) -> list[str]:
    """One line per overdue failing row the status lists, and one more counting those beyond its list."""
    failing = body.get("failing") or {}
    lines = [_row_line(r) for r in failing.get("rows") or [] if r.get("overdue")]
    hidden = int(failing.get("overdue") or 0) - len(lines)
    if hidden > 0:
        lines.append(f"and {hidden} more overdue rows beyond the {failing.get('limit')} the status lists")
    return lines


def overdue_runs(body: dict) -> list[str]:
    """One line per kind whose latest run failed or was abandoned longer ago than its retry allows."""
    return [_run_line(r) for r in body.get("failed_runs") or [] if r.get("overdue")]


def _drift_names(run: dict) -> list[str]:
    names = list((run.get("counts") or {}).get("failed_checks") or [])
    if names:
        return [str(n) for n in names]
    checks = (run.get("drift") or {}).get("checks")
    if isinstance(checks, list):
        return [str(c.get("name", "?")) for c in checks if isinstance(c, dict) and not c.get("pass")]
    return []


def drift_found(body: dict) -> list[str]:
    """One line when the latest drift run found drift, naming its failed checks."""
    run = (body.get("runs") or {}).get("drift") or {}
    if run.get("status") != "drift":
        return []
    names = _drift_names(run)
    shown = ", ".join(names[:DRIFT_NAMES_SHOWN]) or "no named check"
    if len(names) > DRIFT_NAMES_SHOWN:
        shown += f" and {len(names) - DRIFT_NAMES_SHOWN} more"
    return [f"drift run {run.get('id')} (finished {run.get('finished_at') or '?'}) found drift in: {shown}"
            + DRIFT_REMEDY]


def _part(value, key):
    return value.get(key) if isinstance(value, dict) else None


def label_changes_awaiting_approval(body: dict) -> list[str]:
    """One line when the latest drift run counted DERIVED_FROM labels that differ from the rule and that only the
    operator's approval writes (``LABEL_CLASSES_AWAITING_APPROVAL``), read from that run's own record
    (``stats.gate_g.lineage_labels.classes``, gate G check 9). None without such counts. Reported, never failed:
    whether to write them is the operator's decision."""
    run = _part(body.get("runs"), "drift") or {}
    lineage = _part(_part(_part(run.get("drift"), "stats"), "gate_g"), "lineage_labels")
    classes = _part(lineage, "classes") or {}
    counts = {name: int(classes.get(name) or 0) for name in LABEL_CLASSES_AWAITING_APPROVAL}
    if not any(counts.values()):
        return []
    shown = ", ".join(f"{name} {n}" for name, n in counts.items())
    return [f"drift run {run.get('id')} counted DERIVED_FROM labels awaiting approval: {shown} "
            "(its check 9.lineage.labels_differ_from_rule lists examples)"]


def within_grace(body: dict) -> list[str]:
    """What is failing but not yet overdue, a drift check that refused to compare, and label changes awaiting the
    operator's approval: reported, never failed."""
    lines = [f"row {_row_line(r)}" for r in (body.get("failing") or {}).get("rows") or [] if not r.get("overdue")]
    lines += [f"run {_run_line(r)}" for r in body.get("failed_runs") or [] if not r.get("overdue")]
    run = (body.get("runs") or {}).get("drift") or {}
    if run.get("status") == "refused":
        lines.append(f"drift run {run.get('id')} refused to compare this graph "
                     "(a graph below the writer's schema version)")
    return lines + label_changes_awaiting_approval(body)


def problems(body: dict) -> list[str]:
    """Every problem, in this order: an older body, stale jobs, dead rows, overdue failing rows, overdue failed runs,
    drift. What ``manage.py graph_sync_health`` fails on; the smoke suite asks for each part in a test of its own."""
    missing = missing_parts(body)
    older = [f"the status body lacks {', '.join(missing)}: the app image predates these checks"] if missing else []
    return (older + stale_jobs(body) + dead_rows(body) + overdue_rows(body) + overdue_runs(body)
            + drift_found(body))


def summary(body: dict) -> str:
    """One line of counts, the head of the startup health line."""
    failing = body.get("failing") or {}
    runs = body.get("failed_runs") or []
    drift = (body.get("runs") or {}).get("drift") or {}
    drift_text = f"run {drift.get('id')} {drift.get('status')}" if drift else "none recorded"
    return (f"failing outbox rows: {failing.get('total', 0)} ({failing.get('overdue', 0)} overdue); "
            f"failed runs: {len(runs)} ({sum(1 for r in runs if r.get('overdue'))} overdue); "
            f"latest drift check: {drift_text}")
