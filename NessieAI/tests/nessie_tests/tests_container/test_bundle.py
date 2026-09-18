import pytest
from NessieAI.tests.nessie_tests import bundle

RICH = {"memory_payload": {"data": {"samples": [
    {"id": 1, "uuid": "u1", "sample_type": "D.SEQ", "json_metadata": {"species": "Mus"}}]}}}
THIN = {"memory_payload": {"data": {"samples": [
    {"id": 1, "uuid": "u1", "sample_type": "NHP", "sample_type_description": "monkey"}]}}}
GRAPH = {"memory_payload": None, "graph_result": {"data": [
    {"id": 1, "uuid": "u1", "type": "D.SEQ"}]}}


def test_richness_rich_bundle():
    s = bundle.richness_summary(RICH)
    assert s["has_json_metadata"] is True and s["row_count"] == 1


def test_richness_thin_get_parents():
    s = bundle.richness_summary(THIN)
    assert s["has_json_metadata"] is False and s["has_extra_keys"] is False


def test_richness_graph_null_payload():
    s = bundle.richness_summary(GRAPH)
    assert s["memory_payload_null"] is True and s["row_count"] == 1


@pytest.mark.django_db
def test_summary_for_session_reads_orm():
    from django.contrib.auth import get_user_model
    from nextseek_api.assistant.models_db import ChatSession
    u = get_user_model().objects.create(username="nessie_t")
    sess = ChatSession.objects.create(user=u, results_history=[THIN, RICH])
    s = bundle.summary_for_session(sess.session_id)
    assert s["has_json_metadata"] is True  # latest bundle is RICH


@pytest.mark.django_db
def test_preflight_passes_inside_a_configured_django():
    """8.4: the check the runner makes before the first full-tier turn. Inside the
    app (where `manage.py nessie` runs) Django is already set up, so the preflight
    must configure nothing and read the real table without error."""
    bundle.preflight()


def test_the_reader_carries_the_preflight_the_runner_looks_for():
    assert bundle.summary_for_session.preflight is bundle.preflight
