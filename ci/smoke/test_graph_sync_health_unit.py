"""Unit tests for the graph sync health judgement (nextseek_api/graph_sync/health.py) over recorded status bodies.
No stack, no network, no Django: this lane is what proves the module needs none.

    CI_BOX_PROFILE=local PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with requests \
      pytest ci/smoke/test_graph_sync_health_unit.py -q -p no:cacheprovider

The bodies copy the shape the status endpoint answers; SEPT29 is the failure production showed on 2026-09-29 (drain
rows retrying on "Server has gone away" and a failed catalog run) with synthetic keys, ids and times.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nextseek_api.graph_sync import health

LOST = "OperationalError: (2006, 'Server has gone away')"


def row(kind, key, *, overdue, age_s=6000.0, threshold_s=5400, attempts=1, dead=False):
    return {"kind": kind, "key": key, "attempts": attempts, "dead": dead,
            "failing_since": "2026-09-29T17:05:00+00:00", "age_s": age_s, "threshold_s": threshold_s,
            "overdue": overdue, "next_retry_at": None if dead else "2026-09-29T19:05:00+00:00", "error": LOST}


def failed_run(*, overdue, kind="catalog", run_id=7, age_s=7000.0):
    return {"id": run_id, "kind": kind, "status": "failed", "trigger": "loop",
            "finished_at": "2026-09-29T17:00:00+00:00", "age_s": age_s, "threshold_s": 5400,
            "overdue": overdue, "error": LOST}


def drift_run(status, counts=None, drift=None):
    return {"id": 6, "kind": "drift", "status": status, "started_at": "2026-09-29T02:30:00+00:00",
            "finished_at": "2026-09-29T02:32:00+00:00", "watermark_from": None, "watermark_to": None,
            "counts": counts or {"trigger": "loop"}, "drift": drift}


def body(*, rows=(), overdue=None, failed_runs=(), runs=None, limit=20, freshness=None, outbox=None):
    rows = list(rows)
    return {"generated_at": "2026-09-29T19:00:00+00:00", "schema_version": "x", "runs": runs or {},
            "freshness": freshness or {}, "outbox": outbox or {}, "drift": None,
            "failing": {"rows": rows, "total": len(rows),
                        "overdue": sum(1 for r in rows if r["overdue"]) if overdue is None else overdue,
                        "limit": limit},
            "failed_runs": list(failed_runs)}


QUIET = body(runs={"drift": drift_run("ok")})
SEPT29 = body(
    rows=[row("protocol_map", "*", overdue=True), row("assay_map", "*", overdue=True),
          row("samples_of_type", "type:3", overdue=True, attempts=0), row("catalog", "*", overdue=True, attempts=0),
          row("samples_of_type", "type:5", overdue=True), row("samples_of_type", "type:6", overdue=True)],
    failed_runs=[failed_run(overdue=True)],
    runs={"drift": drift_run("ok")},
)


def test_the_module_imports_nothing_outside_the_standard_library():
    """The smoke suite imports it on a host with no Django installed (the module docstring)."""
    tree = ast.parse(Path(health.__file__).read_text(encoding="utf-8"))
    imported = {(node.names[0].name if isinstance(node, ast.Import) else node.module or "").split(".")[0]
                for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))}
    assert imported <= {"__future__"}, imported


def test_a_quiet_box_has_nothing_to_report():
    assert health.missing_parts(QUIET) == []
    assert health.overdue_rows(QUIET) == []
    assert health.overdue_runs(QUIET) == []
    assert health.drift_found(QUIET) == []
    assert health.within_grace(QUIET) == []
    assert health.problems(QUIET) == []


def test_an_old_image_body_is_missing_both_parts():
    old = {k: v for k, v in QUIET.items() if k not in ("failing", "failed_runs")}
    assert health.missing_parts(old) == ["failing", "failed_runs"]
    assert health.overdue_rows(old) == [] and health.overdue_runs(old) == []
    assert health.problems(old)[0].startswith("the status body lacks failing, failed_runs")


def test_the_2026_09_29_failure_is_six_overdue_rows_and_one_failed_run():
    lines = health.overdue_rows(SEPT29)
    assert len(lines) == 6
    assert lines[0] == ("protocol_map *: failing for 100 min against 90 min, attempts 1, "
                        f"next retry 2026-09-29T19:05:00+00:00: {LOST}")
    assert health.overdue_runs(SEPT29) == [
        f"catalog run 7 failed (trigger loop, finished 2026-09-29T17:00:00+00:00, 117 min ago): {LOST}"]
    assert health.drift_found(SEPT29) == []
    assert len(health.problems(SEPT29)) == 7


def test_a_dead_row_says_so():
    (line,) = health.overdue_rows(body(rows=[row("catalog", "*", overdue=True, attempts=8, dead=True)]))
    assert ", attempts 8, dead, next retry none: " in line


def test_overdue_rows_beyond_the_list_are_counted():
    lines = health.overdue_rows(body(rows=[row("catalog", "*", overdue=True)], overdue=3))
    assert lines[-1] == "and 2 more overdue rows beyond the 20 the status lists"


def test_failures_inside_their_grace_are_only_reported():
    b = body(rows=[row("catalog", "*", overdue=False, age_s=600.0)],
             failed_runs=[failed_run(overdue=False, age_s=60.0)])
    assert health.overdue_rows(b) == [] and health.overdue_runs(b) == []
    assert health.problems(b) == []
    lines = health.within_grace(b)
    assert lines[0].startswith("row catalog *: failing for 10 min against 90 min")
    assert lines[1].startswith("run catalog run 7 failed")


def test_drift_found_names_the_failed_checks_six_at_most():
    names = [f"check.{n}" for n in range(8)]
    b = body(runs={"drift": drift_run("drift", counts={"trigger": "loop", "failed_checks": names})})
    assert health.drift_found(b) == [
        "drift run 6 (finished 2026-09-29T02:32:00+00:00) found drift in: "
        "check.0, check.1, check.2, check.3, check.4, check.5 and 2 more"]


def test_drift_found_falls_back_to_the_recorded_checks():
    recorded = {"checks": [{"name": "detection.changed", "pass": False}, {"name": "gate_g", "pass": True}]}
    (line,) = health.drift_found(body(runs={"drift": drift_run("drift", drift=recorded)}))
    assert line.endswith("found drift in: detection.changed")


def test_a_refused_drift_check_is_a_warning_not_a_failure():
    b = body(runs={"drift": drift_run("refused")})
    assert health.drift_found(b) == []
    assert any("refused" in line for line in health.within_grace(b))


def test_stale_jobs_and_dead_rows_name_what_they_count():
    b = body(freshness={"full": {"status": "ok"}, "reconcile": {"status": "never"},
                        "outbox": {"status": "stale", "age_s": 7860.0, "threshold_s": 3600}},
             outbox={"dead": {"samples": 2}})
    assert health.stale_jobs(b) == ["outbox is stale: 2.2 h old against 60 min"]
    assert health.dead_rows(b) == [
        "2 samples rows are dead (at the attempt limit, their work never done); once the cause is fixed, "
        "`manage.py graph_sync --requeue-dead` puts them back"]


def test_problems_lists_every_kind_of_problem_in_order():
    b = body(rows=[row("catalog", "*", overdue=True)], failed_runs=[failed_run(overdue=True)],
             runs={"drift": drift_run("drift", counts={"trigger": "loop", "failed_checks": ["check.0"]})},
             freshness={"outbox": {"status": "stale", "age_s": 4000.0, "threshold_s": 3600}},
             outbox={"dead": {"catalog": 1}})
    lines = health.problems(b)
    assert [line.split(" ")[0] for line in lines] == ["outbox", "1", "catalog", "catalog", "drift"]


def test_the_summary_counts_and_names_the_drift_run():
    assert health.summary(SEPT29) == (
        "failing outbox rows: 6 (6 overdue); failed runs: 1 (1 overdue); latest drift check: run 6 ok")
    assert health.summary(body()) == (
        "failing outbox rows: 0 (0 overdue); failed runs: 0 (0 overdue); latest drift check: none recorded")


def test_long_durations_read_in_hours():
    (line,) = health.overdue_rows(body(rows=[row("full", "slot:2026-W39", overdue=True, age_s=25200.0,
                                                 threshold_s=23400)]))
    assert line.startswith("full slot:2026-W39: failing for 7.0 h against 6.5 h")
