"""The two upload-reingest host seams that write: a SEEK study assay, and parent registrations."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from NessieAI.ns.reingest.upload import AssayCreateRefused
from nextseek_api.services import reingest_assays

REQ = SimpleNamespace(user=SimpleNamespace(pk=7))
LOOK = "nextseek_api.services.reingest_lookups"


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
    assert body == {"counts": {"written": 2}}
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
