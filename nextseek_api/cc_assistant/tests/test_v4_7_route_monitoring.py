"""V4-7 route monitoring and export (Lane C)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nextseek_api.assistant.models_db import ChatSession, TurnLedger
from nextseek_api.cc_assistant.route_monitoring import MONITORING_DISCLAIMER, build_route_monitoring_summary
from nextseek_api.eval.export import export_observational_rows, ledger_row_to_observational
from nextseek_api.eval.online_observation import DEFAULT_SELECTION_CAVEAT
from nextseek_api.eval.router_models_proposal import RouteSource

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="v47user", password="x")


@pytest.fixture
def session(user):
    return ChatSession.objects.create(user=user)


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
    assert "would be better" not in text.lower()


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
