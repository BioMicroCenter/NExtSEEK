"""Coverage tests for nextseek_api/models.py — targeting uncovered lines.

Covers:
- _resolve_sop_uid_to_id()
- SopPatchRequest.to_seek_payload() with uid resolution
- DataFileCreateRequest.to_seek_payload()
- DataFilePatchRequest.to_seek_payload() with relationships
- ProjectCreateRequest.to_seek_payload() with relationships
- InvestigationUpdateRequest.to_seek_payload() with relationships
- AssayUpdateRequest.to_seek_payload() with relationships
- SampleTypeUpdateRequest.to_seek_payload() with relationships
- SampleCreateRequest.to_seek_payload() with db_resolver
- SampleUpdateRequest.to_seek_payload() with db_resolver
- SampleFilterParams.to_upstream_kwargs() edge cases
- RetrieveRequest validators
- _rebuild_schema_rag_models()
- SeekContentBlobListRequest.accept_header()
"""

import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# _resolve_sop_uid_to_id
# ---------------------------------------------------------------------------

class TestResolveSopUidToId:

    @patch("nextseek_api.models.DBtable_sops")
    def test_resolves_single_record(self, mock_cls):
        """Single record returned -> returns its id as str."""
        from nextseek_api.models import _resolve_sop_uid_to_id
        mock_instance = MagicMock()
        mock_instance.queryRecordsByConstraint.return_value = [{"id": 42}]
        mock_cls.return_value = mock_instance
        assert _resolve_sop_uid_to_id("SOP-001") == "42"

    @patch("nextseek_api.models.DBtable_sops")
    def test_returns_none_for_multiple_records(self, mock_cls):
        from nextseek_api.models import _resolve_sop_uid_to_id
        mock_instance = MagicMock()
        mock_instance.queryRecordsByConstraint.return_value = [{"id": 1}, {"id": 2}]
        mock_cls.return_value = mock_instance
        assert _resolve_sop_uid_to_id("SOP-001") is None

    @patch("nextseek_api.models.DBtable_sops")
    def test_returns_none_for_empty_result(self, mock_cls):
        from nextseek_api.models import _resolve_sop_uid_to_id
        mock_instance = MagicMock()
        mock_instance.queryRecordsByConstraint.return_value = []
        mock_cls.return_value = mock_instance
        assert _resolve_sop_uid_to_id("SOP-001") is None

    @patch("nextseek_api.models.DBtable_sops")
    def test_returns_none_on_exception(self, mock_cls):
        from nextseek_api.models import _resolve_sop_uid_to_id
        mock_cls.side_effect = Exception("DB error")
        assert _resolve_sop_uid_to_id("SOP-001") is None

    @patch("nextseek_api.models.DBtable_sops")
    def test_returns_none_when_id_is_none(self, mock_cls):
        from nextseek_api.models import _resolve_sop_uid_to_id
        mock_instance = MagicMock()
        mock_instance.queryRecordsByConstraint.return_value = [{"id": None}]
        mock_cls.return_value = mock_instance
        assert _resolve_sop_uid_to_id("SOP-001") is None


# ---------------------------------------------------------------------------
# SopPatchRequest.to_seek_payload
# ---------------------------------------------------------------------------

class TestSopUpdateRequestPayload:

    def test_payload_with_id(self):
        from nextseek_api.models import SopUpdateRequest
        req = SopUpdateRequest(data={"type": "sops", "id": "5"})
        payload = req.to_seek_payload()
        assert payload["data"]["id"] == "5"

    @patch("nextseek_api.models._resolve_sop_uid_to_id", return_value="99")
    def test_payload_with_uid_resolved(self, mock_resolve):
        from nextseek_api.models import SopUpdateRequest
        req = SopUpdateRequest(data={"type": "sops", "uid": "SOP-TITLE"})
        payload = req.to_seek_payload()
        assert payload["data"]["id"] == "99"

    @patch("nextseek_api.models._resolve_sop_uid_to_id", return_value=None)
    def test_payload_with_uid_unresolved(self, mock_resolve):
        from nextseek_api.models import SopUpdateRequest
        req = SopUpdateRequest(data={"type": "sops", "uid": "SOP-UNKNOWN"})
        payload = req.to_seek_payload()
        # When uid cannot be resolved, id is not set
        assert "id" not in payload["data"] or payload["data"].get("id") is None

    def test_payload_with_numeric_uid(self):
        """Numeric UID treated as SEEK id."""
        from nextseek_api.models import SopUpdateRequest
        req = SopUpdateRequest(data={"type": "sops", "uid": "123"})
        payload = req.to_seek_payload()
        assert payload["data"]["id"] == "123"

    def test_payload_with_attributes(self):
        from nextseek_api.models import SopUpdateRequest
        req = SopUpdateRequest(data={
            "type": "sops",
            "id": "5",
            "attributes": {"title": "new title"},
        })
        payload = req.to_seek_payload()
        assert payload["data"]["attributes"]["title"] == "new title"

    def test_payload_with_relationships(self):
        from nextseek_api.models import SopUpdateRequest
        req = SopUpdateRequest(data={
            "type": "sops",
            "id": "5",
            "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
        })
        payload = req.to_seek_payload()
        assert "relationships" in payload["data"]


# ---------------------------------------------------------------------------
# DataFileCreateRequest.to_seek_payload
# ---------------------------------------------------------------------------

class TestDataFileCreateRequestPayload:

    def test_basic_payload(self):
        from nextseek_api.models import DataFileCreateRequest
        req = DataFileCreateRequest(data={
            "type": "data_files",
            "attributes": {
                "title": "test.csv",
                "content_blobs": [{"original_filename": "test.csv", "content_type": "text/csv"}],
            },
            "relationships": {
                "projects": {"data": [{"id": "1", "type": "projects"}]},
            },
        })
        payload = req.to_seek_payload()
        assert payload["data"]["type"] == "data_files"
        assert payload["data"]["attributes"]["title"] == "test.csv"


# ---------------------------------------------------------------------------
# DataFileUpdateRequest.to_seek_payload
# ---------------------------------------------------------------------------

class TestDataFileUpdateRequestPayload:

    def test_payload_with_attrs_and_relationships(self):
        from nextseek_api.models import DataFileUpdateRequest
        req = DataFileUpdateRequest(data={
            "type": "data_files",
            "id": "10",
            "attributes": {"title": "updated.csv"},
            "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
        })
        payload = req.to_seek_payload()
        assert payload["data"]["type"] == "data_files"
        assert payload["data"]["attributes"]["title"] == "updated.csv"
        assert "relationships" in payload["data"]

    def test_payload_without_optional_fields(self):
        from nextseek_api.models import DataFileUpdateRequest
        req = DataFileUpdateRequest(data={"type": "data_files", "id": "10"})
        payload = req.to_seek_payload()
        assert payload["data"]["id"] == "10"
        assert payload["data"]["type"] == "data_files"


# ---------------------------------------------------------------------------
# ProjectCreateRequest.to_seek_payload
# ---------------------------------------------------------------------------

class TestProjectCreateRequestPayload:

    def test_payload_with_relationships(self):
        from nextseek_api.models import ProjectCreateRequest
        req = ProjectCreateRequest(data={
            "type": "projects",
            "attributes": {"title": "Test Project"},
            "relationships": {"organisms": {"data": [{"id": "1", "type": "organisms"}]}},
        })
        payload = req.to_seek_payload()
        assert "relationships" in payload["data"]
        assert payload["data"]["type"] == "projects"


# ---------------------------------------------------------------------------
# InvestigationUpdateRequest.to_seek_payload
# ---------------------------------------------------------------------------

class TestInvestigationUpdateRequestPayload:

    def test_payload_with_relationships(self):
        from nextseek_api.models import InvestigationUpdateRequest
        req = InvestigationUpdateRequest(data={
            "type": "investigations",
            "id": "7",
            "attributes": {"title": "Updated Investigation"},
            "relationships": {"creators": {"data": [{"id": "1", "type": "people"}]}},
        })
        payload = req.to_seek_payload()
        assert payload["data"]["type"] == "investigations"
        assert "relationships" in payload["data"]

    def test_payload_without_relationships(self):
        from nextseek_api.models import InvestigationUpdateRequest
        req = InvestigationUpdateRequest(data={
            "type": "investigations",
            "id": "7",
        })
        payload = req.to_seek_payload()
        assert "relationships" not in payload["data"]


# ---------------------------------------------------------------------------
# AssayUpdateRequest.to_seek_payload
# ---------------------------------------------------------------------------

class TestAssayUpdateRequestPayload:

    def test_payload_with_relationships(self):
        from nextseek_api.models import AssayUpdateRequest
        req = AssayUpdateRequest(data={
            "type": "assays",
            "id": "3",
            "relationships": {"organisms": {"data": [{"id": "1", "type": "organisms"}]}},
        })
        payload = req.to_seek_payload()
        assert payload["data"]["type"] == "assays"
        assert "relationships" in payload["data"]


# ---------------------------------------------------------------------------
# SampleTypeUpdateRequest.to_seek_payload
# ---------------------------------------------------------------------------

class TestSampleTypeUpdateRequestPayload:

    def test_payload_with_relationships(self):
        from nextseek_api.models import SampleTypeUpdateRequest
        req = SampleTypeUpdateRequest(data={
            "type": "sample_types",
            "id": "2",
            "relationships": {"projects": {"data": [{"id": "1", "type": "projects"}]}},
        })
        payload = req.to_seek_payload()
        assert payload["data"]["type"] == "sample_types"
        assert "relationships" in payload["data"]


# ---------------------------------------------------------------------------
# SampleCreateRequest.to_seek_payload with db_resolver
# ---------------------------------------------------------------------------

class TestSampleCreateRequestPayload:

    def test_payload_with_db_resolver_single_ref(self):
        """db_resolver maps non-numeric ids in single reference."""
        from nextseek_api.models import SampleCreateRequest

        def resolver(rtype, rid):
            if rtype == "sample_types" and rid == "NHP_blood":
                return 42
            return None

        req = SampleCreateRequest(data={
            "type": "samples",
            "attributes": {"title": "Test Sample"},
            "relationships": {
                "sample_type": {"data": {"id": "NHP_blood", "type": "sample_types"}},
            },
        })
        payload = req.to_seek_payload(db_resolver=resolver)
        assert payload["data"]["relationships"]["sample_type"]["data"]["id"] == "42"

    def test_payload_with_db_resolver_list_ref(self):
        """db_resolver maps non-numeric ids in list references."""
        from nextseek_api.models import SampleCreateRequest

        def resolver(rtype, rid):
            if rtype == "projects" and rid == "proj-alpha":
                return 99
            return None

        req = SampleCreateRequest(data={
            "type": "samples",
            "attributes": {"title": "Test Sample"},
            "relationships": {
                "sample_type": {"data": {"id": "1", "type": "sample_types"}},
                "projects": {"data": [
                    {"id": "proj-alpha", "type": "projects"},
                    {"id": "3", "type": "projects"},
                ]},
            },
        })
        payload = req.to_seek_payload(db_resolver=resolver)
        proj_data = payload["data"]["relationships"]["projects"]["data"]
        assert proj_data[0]["id"] == "99"  # resolved
        assert proj_data[1]["id"] == "3"   # numeric, untouched

    def test_payload_without_resolver(self):
        from nextseek_api.models import SampleCreateRequest
        req = SampleCreateRequest(data={
            "type": "samples",
            "attributes": {"title": "Test Sample"},
            "relationships": {
                "sample_type": {"data": {"id": "1", "type": "sample_types"}},
            },
        })
        payload = req.to_seek_payload()
        assert payload["data"]["type"] == "samples"

    def test_payload_with_resolver_exception(self):
        """db_resolver that throws should be caught (best-effort)."""
        from nextseek_api.models import SampleCreateRequest

        def bad_resolver(rtype, rid):
            raise RuntimeError("DB down")

        req = SampleCreateRequest(data={
            "type": "samples",
            "attributes": {"title": "Test Sample"},
            "relationships": {
                "sample_type": {"data": {"id": "NHP", "type": "sample_types"}},
            },
        })
        # Should not raise
        payload = req.to_seek_payload(db_resolver=bad_resolver)
        assert payload is not None


# ---------------------------------------------------------------------------
# SampleUpdateRequest.to_seek_payload with db_resolver
# ---------------------------------------------------------------------------

class TestSampleUpdateRequestPayload:

    def test_payload_with_db_resolver_single_ref(self):
        from nextseek_api.models import SampleUpdateRequest

        def resolver(rtype, rid):
            if rtype == "sample_types" and rid == "NHP":
                return 10
            return None

        req = SampleUpdateRequest(data={
            "type": "samples",
            "id": "5",
            "relationships": {
                "sample_type": {"data": {"id": "NHP", "type": "sample_types"}},
            },
        })
        payload = req.to_seek_payload(db_resolver=resolver)
        assert payload["data"]["relationships"]["sample_type"]["data"]["id"] == "10"

    def test_payload_with_db_resolver_list_ref(self):
        from nextseek_api.models import SampleUpdateRequest

        def resolver(rtype, rid):
            if rtype == "projects" and rid == "my-proj":
                return 77
            return None

        req = SampleUpdateRequest(data={
            "type": "samples",
            "id": "5",
            "relationships": {
                "projects": {"data": [
                    {"id": "my-proj", "type": "projects"},
                ]},
            },
        })
        payload = req.to_seek_payload(db_resolver=resolver)
        assert payload["data"]["relationships"]["projects"]["data"][0]["id"] == "77"

    def test_payload_with_resolver_exception(self):
        from nextseek_api.models import SampleUpdateRequest

        def bad_resolver(rtype, rid):
            raise RuntimeError("fail")

        req = SampleUpdateRequest(data={
            "type": "samples",
            "id": "5",
            "relationships": {
                "sample_type": {"data": {"id": "NHP", "type": "sample_types"}},
            },
        })
        payload = req.to_seek_payload(db_resolver=bad_resolver)
        assert payload is not None


# ---------------------------------------------------------------------------
# SampleFilterParams.to_upstream_kwargs edge cases
# ---------------------------------------------------------------------------

class TestSampleAdvancedSearchRequest:

    def test_sampletype_name_resolution_unresolvable(self):
        """Non-numeric sampletype with resolver that returns None."""
        from nextseek_api.models import SampleAdvancedSearchRequest
        params = SampleAdvancedSearchRequest(
            sampletype="UnknownType",
            filter_searchText="test",
        )
        kwargs = params.to_db_filters(sampletype_resolver=lambda x: None)
        assert kwargs["sampletype_id"] == 0
        assert kwargs["sampletype_ids"] == []

    def test_sampletype_name_resolution_success(self):
        """Non-numeric sampletype with resolver that returns an id."""
        from nextseek_api.models import SampleAdvancedSearchRequest
        params = SampleAdvancedSearchRequest(
            sampletype="NHP_blood",
            filter_searchText="test",
        )
        kwargs = params.to_db_filters(sampletype_resolver=lambda x: "42")
        assert kwargs["sampletype_id"] == 42
        assert kwargs["sampletype_ids"] == [42]

    def test_attribute_single_string(self):
        """Single string attribute is normalized to lowercase list."""
        from nextseek_api.models import SampleAdvancedSearchRequest
        params = SampleAdvancedSearchRequest(
            attribute="Name",
            filter_searchText="test",
        )
        kwargs = params.to_db_filters()
        assert kwargs["attribute_list"] == ["name"]

    def test_attribute_list_multiple(self):
        """List of attribute strings is normalized to lowercase."""
        from nextseek_api.models import SampleAdvancedSearchRequest
        params = SampleAdvancedSearchRequest(
            attribute=["Name", "Date"],
            filter_searchText="test",
        )
        kwargs = params.to_db_filters()
        assert kwargs["attribute_list"] == ["name", "date"]


# ---------------------------------------------------------------------------
# RetrieveRequest validators
# ---------------------------------------------------------------------------

class TestRetrieveRequestValidators:

    def test_top_k_less_than_1_raises(self):
        from nextseek_api.models import RetrieveRequest
        with pytest.raises(Exception):
            RetrieveRequest(session_id="abc", query="test", top_k=0)

    def test_filter_by_without_filter_terms_raises(self):
        from nextseek_api.models import RetrieveRequest
        with pytest.raises(Exception):
            RetrieveRequest(session_id="abc", query="test", filter_by="METHOD")


# ---------------------------------------------------------------------------
# _rebuild_schema_rag_models
# ---------------------------------------------------------------------------

class TestRebuildSchemaRagModels:

    def test_rebuild_succeeds(self):
        from nextseek_api.models import _rebuild_schema_rag_models
        # Should not raise
        _rebuild_schema_rag_models()

    @patch("nextseek_api.models.RetrieveResponse.model_rebuild", side_effect=ImportError("no module"))
    def test_rebuild_handles_import_error(self, mock_rebuild):
        """Test that ImportError in _rebuild_schema_rag_models is handled."""
        from nextseek_api.models import _rebuild_schema_rag_models
        # The function catches ImportError internally; this test covers the except branch
        # by patching at a different level
        _rebuild_schema_rag_models()


# ---------------------------------------------------------------------------
# SeekContentBlobListRequest.accept_header
# ---------------------------------------------------------------------------

class TestSeekContentBlobReadRequestAcceptHeader:

    def test_json_format(self):
        from nextseek_api.models import SeekContentBlobReadRequest
        req = SeekContentBlobReadRequest(
            path={"asset_types": "data_files", "id": 1, "blob_id": 10},
            output_format="json",
        )
        assert req.accept_header() == "application/json"

    def test_csv_format(self):
        from nextseek_api.models import SeekContentBlobReadRequest
        req = SeekContentBlobReadRequest(
            path={"asset_types": "data_files", "id": 1, "blob_id": 10},
            output_format="csv",
        )
        assert req.accept_header() == "text/csv"

    def test_default_format(self):
        from nextseek_api.models import SeekContentBlobReadRequest
        req = SeekContentBlobReadRequest(
            path={"asset_types": "data_files", "id": 1, "blob_id": 10},
        )
        assert req.accept_header() == "*/*"


# ---------------------------------------------------------------------------
# BatchDelete* shared HTTP models
# ---------------------------------------------------------------------------

class TestBatchDeleteStartRequest:

    def test_scalar_identifier_normalizes_to_list(self):
        from nextseek_api.models import BatchDeleteStartRequest
        req = BatchDeleteStartRequest(sample_identifiers="NHP-001")
        assert req.sample_identifiers == ["NHP-001"]

    def test_mixed_identifiers_preserved_as_strings(self):
        from nextseek_api.models import BatchDeleteStartRequest
        req = BatchDeleteStartRequest(sample_identifiers=["NHP-001", "123", "NHP-002"])
        assert req.sample_identifiers == ["NHP-001", "123", "NHP-002"]

    def test_empty_normalized_identifier_input_rejected(self):
        from nextseek_api.models import BatchDeleteStartRequest
        with pytest.raises(Exception):
            BatchDeleteStartRequest(sample_identifiers=[" ", ""])

    def test_extra_fields_rejected(self):
        from nextseek_api.models import BatchDeleteStartRequest
        with pytest.raises(Exception):
            BatchDeleteStartRequest(sample_identifiers=["NHP-001"], unexpected=True)

    def test_non_list_non_string_identifier_input_rejected(self):
        from nextseek_api.models import BatchDeleteStartRequest
        with pytest.raises(Exception):
            BatchDeleteStartRequest(sample_identifiers=123)

    def test_non_mapping_input_rejected(self):
        from nextseek_api.models import BatchDeleteStartRequest
        with pytest.raises(Exception):
            BatchDeleteStartRequest.model_validate("NHP-001")


class TestBatchDeleteStartResponse:

    def test_defaults_status_to_queued(self):
        from nextseek_api.models import BatchDeleteStartResponse
        resp = BatchDeleteStartResponse(job_id="job-123")
        assert resp.status == "queued"


class TestBatchDeleteStatusResponse:

    def test_defaults_meta_and_allows_result_none(self):
        from nextseek_api.models import BatchDeleteStatusResponse
        resp = BatchDeleteStatusResponse(job_id="job-123", state="PENDING")
        assert resp.meta == {}
        assert resp.result is None

    def test_rejects_invalid_state(self):
        from nextseek_api.models import BatchDeleteStatusResponse
        with pytest.raises(Exception):
            BatchDeleteStatusResponse(job_id="job-123", state="queued")


class TestBatchDeleteResponse:

    def test_validates_aggregate_only_output(self):
        from nextseek_api.models import BatchDeleteResponse
        resp = BatchDeleteResponse(summary={"requested": 2, "deleted": 1, "failed": 1})
        assert resp.summary.requested == 2
        assert resp.report_available is False

    def test_does_not_expose_report_format(self):
        from nextseek_api.models import BatchDeleteResponse
        with pytest.raises(Exception):
            BatchDeleteResponse(
                summary={"requested": 1},
                report_available=True,
                report_name="delete-summary.csv",
                report_format="csv",
            )
