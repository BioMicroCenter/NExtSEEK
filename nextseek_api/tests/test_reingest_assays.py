"""The two upload-reingest host seams that write: a SEEK study assay, and parent registrations."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from NessieAI.ns.reingest.upload import AssayCreateRefused
from nextseek_api.services import reingest_assays

REQ = SimpleNamespace(user=SimpleNamespace(pk=7))
LOOK = "nextseek_api.services.reingest_lookups"
TITLE = "Genome Alignment - Data Linked"
# The real scope helper, kept before the autouse fixture below replaces it per test.
_REAL_CALLER_SCOPE = getattr(reingest_assays, "_caller_scope", None)


@pytest.fixture(autouse=True)
def _unscoped_by_default():
    """Most tests are not about project scope: they run as an unscoped caller and find
    no untagged assay. The scope tests below patch these again."""
    with patch.object(reingest_assays, "_caller_scope", return_value=None), \
         patch(f"{LOOK}.untagged_assay_ids_in_study_strict", return_value=[]):
        yield


def _call(**kw):
    return reingest_assays.create_study_assay(
        REQ, study_id=51, internal_assay_title="Genome Alignment",
        assay_title="Genome Alignment - Data Linked", **kw)


@patch(f"{LOOK}.assay_ids_in_study_strict", return_value=[468])
def test_an_existing_assay_is_reused_and_nothing_is_created(_ids):
    with patch.object(reingest_assays, "_client") as client:
        assert _call() == 468
    client.assert_not_called()


@patch(f"{LOOK}.assay_ids_in_study_strict", return_value=[1, 2])
def test_two_existing_assays_refuse(_ids):
    with pytest.raises(AssayCreateRefused):
        _call()


@patch(f"{LOOK}.internal_assay_id_strict", return_value=34)
@patch(f"{LOOK}.assay_ids_in_study_strict", return_value=[])
def test_a_missing_assay_is_created_tagged_and_synced(_ids, _iid):
    client = MagicMock()
    client.create_assay.return_value = (json.dumps({"data": {"id": "900"}}).encode(), 201, {}, None)
    links = MagicMock()
    with patch.object(reingest_assays, "_client", return_value=client), \
         patch.object(reingest_assays, "_link_model", return_value=links), \
         patch("nextseek_api.graph_sync.hooks.enqueue") as enqueue:
        assert _call() == 900
    payload = client.create_assay.call_args[0][1]
    attrs = payload["data"]["attributes"]
    assert attrs["title"] == "Genome Alignment - Data Linked"
    assert attrs["assay_class"] == {"key": "EXP"}
    assert attrs["assay_type"]["uri"].endswith("#Experimental_assay_type")
    assert payload["data"]["relationships"]["study"]["data"] == {"type": "studies", "id": "51"}
    links.objects.create.assert_called_once_with(assay_id=900, internal_assay_id=34)
    assert [c.args[0] for c in enqueue.call_args_list] == ["assay_map", "isa"]


@patch(f"{LOOK}.internal_assay_id_strict", return_value=34)
@patch(f"{LOOK}.assay_ids_in_study_strict", return_value=[])
@pytest.mark.parametrize("status", [401, 403, 422])
def test_seek_refusing_the_create_is_a_user_facing_refusal(_ids, _iid, status):
    client = MagicMock()
    client.create_assay.return_value = (b"{}", status, {}, None)
    with patch.object(reingest_assays, "_client", return_value=client):
        with pytest.raises(AssayCreateRefused) as caught:
            _call()
    assert str(status) in str(caught.value)


@patch(f"{LOOK}.internal_assay_id_strict", return_value=None)
@patch(f"{LOOK}.assay_ids_in_study_strict", return_value=[])
def test_no_house_assay_refuses_before_seek_is_asked(_ids, _iid):
    with patch.object(reingest_assays, "_client") as client:
        with pytest.raises(AssayCreateRefused):
            _call()
    client.assert_not_called()


def test_register_parents_sends_one_row_per_pair():
    with patch("nextseek_api.assay_registration.service.register",
               return_value=({"counts": {"written": 2}}, 200)) as register:
        body = reingest_assays.register_parents(REQ, [
            {"parent_uid": "D.SEQ-1", "assay_id": 315}, {"parent_uid": "D.SEQ-2", "assay_id": 315}])
    assert body["counts"]["written"] == 2 and body["counts"]["failed"] == 0
    payload, request = register.call_args[0]
    assert [(r.sample_uid, r.assay_id) for r in payload.registrations] == \
        [("D.SEQ-1", 315), ("D.SEQ-2", 315)]
    assert payload.dry_run is False and request is REQ


def test_the_upload_reingest_session_carries_both_seams_bound_to_the_request():
    from nextseek_api.services.assistant import AssistantViewSet

    session = AssistantViewSet()._granular_op_session(REQ, None, "upload-reingest")
    with patch.object(reingest_assays, "create_study_assay", return_value=9) as create, \
         patch.object(reingest_assays, "register_parents", return_value={"counts": {}}) as register:
        assert session.create_study_assay(study_id=1, internal_assay_title="t", assay_title="a") == 9
        assert session.register_parents([{"parent_uid": "U", "assay_id": 2}]) == {"counts": {}}
    create.assert_called_once_with(REQ, study_id=1, internal_assay_title="t", assay_title="a")
    register.assert_called_once_with(REQ, [{"parent_uid": "U", "assay_id": 2}])


# --- SEC-001: a reused or adopted assay, and every registered pair, stay in the caller's projects.

@patch(f"{LOOK}.assay_ids_in_study_strict", return_value=[468])
def test_a_reused_assay_outside_the_callers_projects_is_refused(_ids):
    with patch.object(reingest_assays, "_caller_scope", return_value=(3,)), \
         patch.object(reingest_assays, "_assay_projects", return_value={468: {9}}) as projects, \
         patch.object(reingest_assays, "_client") as client:
        with pytest.raises(AssayCreateRefused) as caught:
            _call()
    projects.assert_called_once_with([468])
    client.assert_not_called()
    assert "not in one of your projects" in str(caught.value)


@patch(f"{LOOK}.assay_ids_in_study_strict", return_value=[468])
def test_a_reused_assay_in_the_callers_projects_is_returned(_ids):
    with patch.object(reingest_assays, "_caller_scope", return_value=(3, 9)), \
         patch.object(reingest_assays, "_assay_projects", return_value={468: {9}}):
        assert _call() == 468


@patch(f"{LOOK}.assay_ids_in_study_strict", return_value=[468])
def test_an_empty_scope_refuses_a_reused_assay(_ids):
    with patch.object(reingest_assays, "_caller_scope", return_value=()), \
         patch.object(reingest_assays, "_assay_projects", return_value={468: {9}}):
        with pytest.raises(AssayCreateRefused):
            _call()


def _pairs():
    return [{"parent_uid": "D.SEQ-1", "assay_id": 315}, {"parent_uid": "D.SEQ-2", "assay_id": 316},
            {"parent_uid": "D.SEQ-3", "assay_id": 315}]


def test_pairs_outside_the_callers_projects_are_not_sent_and_come_back_failed():
    service_body = {"overall_status": "succeeded",
                    "counts": {"submitted": 2, "written": 2, "already_present": 0,
                               "skipped": 0, "failed": 0},
                    "rows": [{"sample_uid": "D.SEQ-1", "assay_id": 315, "status": "written"},
                             {"sample_uid": "D.SEQ-3", "assay_id": 315, "status": "written"}]}
    with patch.object(reingest_assays, "_caller_scope", return_value=(3,)), \
         patch.object(reingest_assays, "_assay_projects",
                      return_value={315: {3}, 316: {9}}) as projects, \
         patch("nextseek_api.assay_registration.service.register",
               return_value=(service_body, 200)) as register:
        body = reingest_assays.register_parents(REQ, _pairs())
    projects.assert_called_once_with([315, 316])
    payload, _request = register.call_args[0]
    assert [(r.sample_uid, r.assay_id) for r in payload.registrations] == \
        [("D.SEQ-1", 315), ("D.SEQ-3", 315)]
    assert body["counts"]["written"] == 2
    assert body["counts"]["failed"] == 1
    [dropped] = [r for r in body["rows"] if r["status"] == "failed"]
    assert dropped == {"sample_uid": "D.SEQ-2", "assay_id": 316, "status": "failed",
                       "error": {"code": "permission_denied", "message": "not in your projects"}}


def test_a_superuser_is_unscoped_and_every_pair_is_sent():
    admin = SimpleNamespace(user=SimpleNamespace(pk=1, is_superuser=True, username="admin"))
    with patch.object(reingest_assays, "_caller_scope", new=_REAL_CALLER_SCOPE), \
         patch.object(reingest_assays, "_assay_projects") as projects, \
         patch("nextseek_api.assay_registration.service.register",
               return_value=({"counts": {"written": 3}, "rows": []}, 200)) as register:
        body = reingest_assays.register_parents(admin, _pairs())
    projects.assert_not_called()
    assert len(register.call_args[0][0].registrations) == 3
    assert body["counts"]["failed"] == 0


def test_an_empty_scope_drops_and_reports_every_pair():
    # A user with no SEEK login resolves to no projects (the real scope helper).
    nobody = SimpleNamespace(user=SimpleNamespace(pk=8, is_superuser=False, username=""))
    with patch.object(reingest_assays, "_caller_scope", new=_REAL_CALLER_SCOPE), \
         patch.object(reingest_assays, "_assay_projects", return_value={}), \
         patch("nextseek_api.assay_registration.service.register") as register:
        body = reingest_assays.register_parents(nobody, _pairs())
    register.assert_not_called()
    assert body["counts"]["failed"] == 3 and body["counts"]["written"] == 0
    assert [(r["sample_uid"], r["status"], r["error"]["code"]) for r in body["rows"]] == [
        ("D.SEQ-1", "failed", "permission_denied"), ("D.SEQ-2", "failed", "permission_denied"),
        ("D.SEQ-3", "failed", "permission_denied")]


# --- A queued (asynchronous) registration is reported as not made yet, never as written.

def test_a_queued_registration_marks_every_sent_pair_as_queued():
    accepted = {"mode": "asynchronous", "job_id": "j", "status_url": "/x",
                "counts": {"submitted": 3, "written": 0, "already_present": 0,
                           "skipped": 0, "failed": 0}}
    with patch("nextseek_api.assay_registration.service.register", return_value=(accepted, 202)):
        body = reingest_assays.register_parents(REQ, _pairs())
    assert body["counts"]["written"] == 0 and body["counts"]["failed"] == 3
    assert {r["error"]["code"] for r in body["rows"]} == {"queued"}
    assert all("background" in r["error"]["message"] for r in body["rows"])
    assert [r["sample_uid"] for r in body["rows"]] == ["D.SEQ-1", "D.SEQ-2", "D.SEQ-3"]


# --- An unconfirmed SEEK create is adopted when it landed, refused when it cannot be found.

def _created_link_and_sync():
    links = MagicMock()
    return (patch.object(reingest_assays, "_link_model", return_value=links),
            patch("nextseek_api.graph_sync.hooks.enqueue"), links)


@patch(f"{LOOK}.internal_assay_id_strict", return_value=34)
@patch(f"{LOOK}.assay_ids_in_study_strict", return_value=[])
def test_a_pre_existing_untagged_assay_is_adopted_without_asking_seek(_ids, _iid):
    link_patch, enqueue_patch, links = _created_link_and_sync()
    with patch(f"{LOOK}.untagged_assay_ids_in_study_strict", return_value=[470]) as untagged, \
         patch.object(reingest_assays, "_client") as client, link_patch, enqueue_patch as enqueue:
        assert _call() == 470
    untagged.assert_called_once_with(51, TITLE)
    client.assert_not_called()
    links.objects.create.assert_called_once_with(assay_id=470, internal_assay_id=34)
    assert [c.args[0] for c in enqueue.call_args_list] == ["assay_map", "isa"]


@patch(f"{LOOK}.internal_assay_id_strict", return_value=34)
@patch(f"{LOOK}.assay_ids_in_study_strict", return_value=[])
def test_two_untagged_assays_of_that_title_refuse_before_seek_is_asked(_ids, _iid):
    with patch(f"{LOOK}.untagged_assay_ids_in_study_strict", return_value=[470, 471]), \
         patch.object(reingest_assays, "_client") as client:
        with pytest.raises(AssayCreateRefused):
            _call()
    client.assert_not_called()


@patch(f"{LOOK}.internal_assay_id_strict", return_value=34)
@patch(f"{LOOK}.assay_ids_in_study_strict", return_value=[])
def test_an_out_of_scope_untagged_assay_is_not_adopted(_ids, _iid):
    link_patch, enqueue_patch, links = _created_link_and_sync()
    with patch(f"{LOOK}.untagged_assay_ids_in_study_strict", return_value=[470]), \
         patch.object(reingest_assays, "_caller_scope", return_value=(3,)), \
         patch.object(reingest_assays, "_assay_projects", return_value={470: {9}}), \
         patch.object(reingest_assays, "_client") as client, link_patch, enqueue_patch:
        with pytest.raises(AssayCreateRefused):
            _call()
    client.assert_not_called()
    links.objects.create.assert_not_called()


@patch(f"{LOOK}.internal_assay_id_strict", return_value=34)
@patch(f"{LOOK}.assay_ids_in_study_strict", return_value=[])
def test_a_500_whose_assay_landed_is_adopted_and_its_graph_rows_are_held_back(_ids, _iid):
    from nextseek_api.graph_sync import hooks

    client = MagicMock()
    client.create_assay.return_value = (b"gateway", 500, {}, None)
    link_patch, enqueue_patch, links = _created_link_and_sync()
    with patch(f"{LOOK}.untagged_assay_ids_in_study_strict", side_effect=[[], [901]]), \
         patch.object(reingest_assays, "_client", return_value=client), \
         link_patch, enqueue_patch as enqueue:
        assert _call() == 901
    links.objects.create.assert_called_once_with(assay_id=901, internal_assay_id=34)
    calls = [(c.args[0], c.kwargs.get("delay_s", 0)) for c in enqueue.call_args_list]
    assert calls[:2] == [("assay_map", hooks.UNCONFIRMED_DELAY_S),
                         ("isa", hooks.UNCONFIRMED_DELAY_S)]
    assert calls[2:] == [("assay_map", 0), ("isa", 0)]


@patch(f"{LOOK}.internal_assay_id_strict", return_value=34)
@patch(f"{LOOK}.assay_ids_in_study_strict", return_value=[])
def test_a_timeout_with_no_assay_found_afterwards_is_refused_safe_to_retry(_ids, _iid):
    import requests
    from nextseek_api.graph_sync import hooks

    client = MagicMock()
    client.create_assay.side_effect = requests.Timeout("slow")
    link_patch, enqueue_patch, links = _created_link_and_sync()
    with patch(f"{LOOK}.untagged_assay_ids_in_study_strict", side_effect=[[], []]), \
         patch.object(reingest_assays, "_client", return_value=client), \
         link_patch, enqueue_patch as enqueue:
        with pytest.raises(AssayCreateRefused) as caught:
            _call()
    reason = str(caught.value)
    assert "did not confirm" in reason and "safe to try again" in reason
    assert "slow" not in reason and "Timeout" not in reason
    links.objects.create.assert_not_called()
    assert [(c.args[0], c.kwargs.get("delay_s")) for c in enqueue.call_args_list] == [
        ("assay_map", hooks.UNCONFIRMED_DELAY_S), ("isa", hooks.UNCONFIRMED_DELAY_S)]


@patch(f"{LOOK}.internal_assay_id_strict", return_value=34)
@patch(f"{LOOK}.assay_ids_in_study_strict", return_value=[])
def test_an_outage_on_the_recheck_is_refused_safe_to_retry(_ids, _iid):
    client = MagicMock()
    client.create_assay.return_value = (b"", 504, {}, None)
    with patch(f"{LOOK}.untagged_assay_ids_in_study_strict",
               side_effect=[[], RuntimeError("db down")]), \
         patch.object(reingest_assays, "_client", return_value=client), \
         patch("nextseek_api.graph_sync.hooks.enqueue"):
        with pytest.raises(AssayCreateRefused) as caught:
            _call()
    assert "safe to try again" in str(caught.value) and "db down" not in str(caught.value)


@patch(f"{LOOK}.internal_assay_id_strict", return_value=34)
@patch(f"{LOOK}.assay_ids_in_study_strict", return_value=[])
def test_a_2xx_without_an_id_is_adopted_when_exactly_one_assay_landed(_ids, _iid):
    client = MagicMock()
    client.create_assay.return_value = (b"{}", 201, {}, None)
    link_patch, enqueue_patch, links = _created_link_and_sync()
    with patch(f"{LOOK}.untagged_assay_ids_in_study_strict", side_effect=[[], [902]]), \
         patch.object(reingest_assays, "_client", return_value=client), \
         link_patch, enqueue_patch:
        assert _call() == 902
    links.objects.create.assert_called_once_with(assay_id=902, internal_assay_id=34)


@patch(f"{LOOK}.internal_assay_id_strict", return_value=34)
@patch(f"{LOOK}.assay_ids_in_study_strict", return_value=[])
def test_a_2xx_without_an_id_and_two_matches_is_refused(_ids, _iid):
    client = MagicMock()
    client.create_assay.return_value = (b"{}", 201, {}, None)
    link_patch, enqueue_patch, links = _created_link_and_sync()
    with patch(f"{LOOK}.untagged_assay_ids_in_study_strict", side_effect=[[], [902, 903]]), \
         patch.object(reingest_assays, "_client", return_value=client), \
         link_patch, enqueue_patch:
        with pytest.raises(AssayCreateRefused):
            _call()
    links.objects.create.assert_not_called()


# --- Pairs go in chunks no larger than the synchronous threshold, so none is queued.

def test_pairs_are_sent_in_synchronous_chunks_and_their_results_merged(settings):
    settings.ASSAY_REGISTRATION_SYNC_ROW_THRESHOLD = 2

    def register(payload, request):
        assert len(payload.registrations) <= 2
        rows = [{"sample_uid": r.sample_uid, "assay_id": r.assay_id, "status": "written"}
                for r in payload.registrations]
        return ({"overall_status": "succeeded",
                 "counts": {"submitted": len(rows), "written": len(rows), "already_present": 0,
                            "skipped": 0, "failed": 0}, "rows": rows}, 200)

    with patch("nextseek_api.assay_registration.service.register",
               side_effect=register) as service:
        body = reingest_assays.register_parents(REQ, _pairs())
    assert [len(c.args[0].registrations) for c in service.call_args_list] == [2, 1]
    assert body["counts"] == {"submitted": 3, "written": 3, "already_present": 0,
                              "skipped": 0, "failed": 0}
    assert [r["sample_uid"] for r in body["rows"]] == ["D.SEQ-1", "D.SEQ-2", "D.SEQ-3"]
    assert all(r["status"] == "written" for r in body["rows"])


def test_a_queued_chunk_is_still_reported_as_not_made(settings):
    settings.ASSAY_REGISTRATION_SYNC_ROW_THRESHOLD = 2
    answers = iter([
        ({"counts": {"submitted": 2, "written": 2, "already_present": 0, "skipped": 0,
                     "failed": 0},
          "rows": [{"sample_uid": "D.SEQ-1", "assay_id": 315, "status": "written"},
                   {"sample_uid": "D.SEQ-2", "assay_id": 316, "status": "written"}]}, 200),
        ({"mode": "asynchronous", "job_id": "j", "counts": {"submitted": 1}}, 202)])
    with patch("nextseek_api.assay_registration.service.register",
               side_effect=lambda payload, request: next(answers)):
        body = reingest_assays.register_parents(REQ, _pairs())
    assert body["counts"]["written"] == 2 and body["counts"]["failed"] == 1
    assert body["counts"]["submitted"] == 3
    [queued] = [r for r in body["rows"] if r["status"] == "failed"]
    assert queued["sample_uid"] == "D.SEQ-3" and queued["error"]["code"] == "queued"
