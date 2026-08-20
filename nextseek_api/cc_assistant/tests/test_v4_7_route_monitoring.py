"""V4-7 route monitoring and export (Lane C)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nextseek_api.assistant.models_db import ChatSession, TurnLedger
from nextseek_api.cc_assistant.route_monitoring import (
    AlertKind,
    MONITORING_DISCLAIMER,
    build_monitoring_snapshot,
    build_route_monitoring_summary,
    detect_monitoring_alerts,
)
from nextseek_api.eval.export import export_observational_rows, ledger_row_to_observational
from nextseek_api.eval.online_observation import DEFAULT_SELECTION_CAVEAT, PROPENSITY_UNAVAILABLE_REASON
from nextseek_api.eval.router_models_proposal import RouteSource

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="v47user", password="x")


@pytest.fixture
def session(user):
    return ChatSession.objects.create(user=user)


def _obs_row(
    *,
    observation_id: str,
    route: str,
    route_source: RouteSource = RouteSource.baml,
    task_family: str | None = "sample_search",
    assignment_policy: str | None = "baml",
):
    from nextseek_api.eval.online_observation import OnlineObservationalRow

    return OnlineObservationalRow(
        observation_id=observation_id,
        session_id="s1",
        turn_number=int(observation_id.split(":")[-1]) if ":" in observation_id else 0,
        route=route,
        route_source=route_source,
        task_family=task_family,
        assignment_policy=assignment_policy,
        selection_caveat=DEFAULT_SELECTION_CAVEAT,
    )


def test_export_observational_skips_forced_rows(session):
    TurnLedger.objects.create(
        session=session,
        turn_number=1,
        route="container_cc",
        route_source=RouteSource.forced.value,
        task_family="sample_search",
        family_source="corpus",
    )
    TurnLedger.objects.create(
        session=session,
        turn_number=2,
        route="nextseek_query",
        route_source=RouteSource.baml.value,
        task_family="sample_search",
        family_source="baml",
    )
    rows = export_observational_rows()
    assert len(rows) == 1
    assert rows[0].route_source is RouteSource.baml
    assert rows[0].selection_caveat == DEFAULT_SELECTION_CAVEAT
    assert rows[0].propensity_unavailable is True
    assert rows[0].propensity_unavailable_reason == PROPENSITY_UNAVAILABLE_REASON
    assert rows[0].assignment_propensity is None


def test_export_wires_propensity_when_ledger_field_present(session, monkeypatch):
    ledger = TurnLedger.objects.create(
        session=session,
        turn_number=3,
        route="nextseek_query",
        route_source=RouteSource.posterior.value,
        task_family="sample_search",
        family_source="posterior",
    )
    monkeypatch.setattr(ledger, "assignment_propensity", 0.42, raising=False)
    row = ledger_row_to_observational(ledger)
    assert row.assignment_propensity == 0.42
    assert row.propensity_unavailable is False
    assert row.propensity_unavailable_reason is None


def test_monitoring_summary_includes_caveat(session, user):
    TurnLedger.objects.create(
        session=session,
        turn_number=1,
        route="container_cc",
        route_source=RouteSource.heuristic.value,
        task_family="fam",
        family_source="baml",
    )
    row = export_observational_rows()[0]
    text = build_route_monitoring_summary([row])
    assert MONITORING_DISCLAIMER in text
    assert "container_cc" in text
    assert "propensity_unavailable=1/1" in text
    assert "would be better" not in text.lower()


def test_policy_drift_alert():
    baseline = build_monitoring_snapshot(
        [
            _obs_row(observation_id="s1:1", route="container_cc", assignment_policy="baml"),
            _obs_row(observation_id="s1:2", route="container_cc", assignment_policy="baml"),
        ]
    )
    current = build_monitoring_snapshot(
        [
            _obs_row(observation_id="s1:3", route="container_cc", assignment_policy="heuristic"),
            _obs_row(observation_id="s1:4", route="container_cc", assignment_policy="heuristic"),
        ]
    )
    alerts = detect_monitoring_alerts(baseline, current, drift_threshold=0.15)
    assert any(a.kind is AlertKind.policy_drift for a in alerts)


def test_family_mix_shift_alert():
    baseline = build_monitoring_snapshot(
        [
            _obs_row(observation_id="s1:1", route="container_cc", task_family="fam_a"),
            _obs_row(observation_id="s1:2", route="container_cc", task_family="fam_a"),
        ]
    )
    current = build_monitoring_snapshot(
        [
            _obs_row(observation_id="s1:3", route="container_cc", task_family="fam_b"),
            _obs_row(observation_id="s1:4", route="container_cc", task_family="fam_b"),
        ]
    )
    alerts = detect_monitoring_alerts(baseline, current, drift_threshold=0.15)
    assert any(a.kind is AlertKind.family_mix_shift for a in alerts)


def test_missingness_spike_alert():
    baseline = build_monitoring_snapshot(
        [
            _obs_row(observation_id="s1:1", route="container_cc", task_family="fam_a"),
            _obs_row(observation_id="s1:2", route="container_cc", task_family="fam_a"),
        ]
    )
    current = build_monitoring_snapshot(
        [
            _obs_row(observation_id="s1:3", route="container_cc", task_family=None),
            _obs_row(observation_id="s1:4", route="container_cc", task_family=None),
        ]
    )
    alerts = detect_monitoring_alerts(
        baseline, current, missingness_spike_threshold=0.10
    )
    assert any(a.kind is AlertKind.missingness_spike for a in alerts)


def test_route_outcome_change_alert():
    baseline = build_monitoring_snapshot(
        [_obs_row(observation_id="s1:1", route="container_cc")],
        outcome_by_observation_id={"s1:1": "pass"},
    )
    current = build_monitoring_snapshot(
        [_obs_row(observation_id="s1:2", route="container_cc")],
        outcome_by_observation_id={"s1:2": "fail"},
    )
    alerts = detect_monitoring_alerts(
        baseline, current, outcome_shift_threshold=0.20
    )
    assert any(a.kind is AlertKind.route_outcome_change for a in alerts)


def test_summary_includes_alerts_when_baseline_provided():
    baseline_rows = [
        _obs_row(observation_id="s1:1", route="container_cc", assignment_policy="baml"),
        _obs_row(observation_id="s1:2", route="container_cc", assignment_policy="baml"),
    ]
    current_rows = [
        _obs_row(observation_id="s1:3", route="container_cc", assignment_policy="heuristic"),
        _obs_row(observation_id="s1:4", route="container_cc", assignment_policy="heuristic"),
    ]
    text = build_route_monitoring_summary(
        current_rows,
        baseline=build_monitoring_snapshot(baseline_rows),
    )
    assert "Monitoring alerts:" in text
    assert "policy_drift" in text


def test_route_monitoring_module_has_no_publish_imports():
    path = Path(__file__).resolve().parent.parent / "route_monitoring.py"
    tree = ast.parse(path.read_text())
    banned = {
        "publish",
        "activate_generation",
        "run_v14_generation",
        "publish_generation",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name not in banned
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in banned
