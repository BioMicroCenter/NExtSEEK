"""
Tests for nextseek_api/serializers.py

Covers the missing lines (30-36, 44-47, 50-61) plus validation tests for
request serializers that may not yet be exercised.

- SamplesSerializer.to_representation (json_metadata parsing + file links)
- DatafilesSerializer.__getFileLink
- DatafilesSerializer.to_representation (download link resolution)
- SampleNodeSerializer / SampleTreeSerializer validation
- AdminRetrieveRequestSerializer validation
- DataGridFilterRuleSerializer validation
- SampleRetrieveRequestSerializer validation
- SampleAdvancedRetrieveRequestSerializer validation / defaults
- SamplesByChildTypesRequestSerializer validation
- SampleUIDItemSerializer / SampleUIDListSerializer validation
"""

import json
from unittest.mock import patch, MagicMock

import pytest

from nextseek_api.serializers import (
    SamplesSerializer,
    DatafilesSerializer,
    SampleNodeSerializer,
    SampleTreeSerializer,
    AdminRetrieveRequestSerializer,
    DataGridFilterRuleSerializer,
    SampleRetrieveRequestSerializer,
    SampleAdvancedRetrieveRequestSerializer,
    SamplesByChildTypesRequestSerializer,
    SampleUIDItemSerializer,
    SampleUIDListSerializer,
)


# ===========================================================================
# SamplesSerializer.to_representation
# ===========================================================================
class TestSamplesSerializerToRepresentation:
    @patch("nextseek_api.serializers.DBtable_data_files")
    def test_to_representation(self, MockDBtable):
        """to_representation should parse json_metadata, add id, and call retrieveFileLinks."""
        mock_dbdf = MockDBtable.return_value
        mock_dbdf.retrieveFileLinks.return_value = {
            "name": "sample1",
            "id": 99,
            "file_link": "https://example.com/file",
        }
        # Build a fake instance with json_metadata and id
        instance = MagicMock()
        instance.id = 99
        instance.json_metadata = json.dumps({"name": "sample1"})

        serializer = SamplesSerializer()
        result = serializer.to_representation(instance)

        assert result["id"] == 99
        assert result["file_link"] == "https://example.com/file"
        MockDBtable.assert_called_once_with("DEFAULT")
        mock_dbdf.retrieveFileLinks.assert_called_once()
        # The dict passed should have had 'id' set before retrieveFileLinks
        call_arg = mock_dbdf.retrieveFileLinks.call_args[0][0]
        assert call_arg["id"] == 99


# ===========================================================================
# DatafilesSerializer.__getFileLink  (private, but covers lines 44-47)
# ===========================================================================
class TestDatafilesSerializerGetFileLink:
    @patch("nextseek_api.serializers.DBtable_data_files")
    def test_get_file_link_has_bug(self, MockDBtable):
        """__getFileLink references undefined 'uid' (source bug on line 45).
        Verify it raises NameError — covers lines 43-45."""
        mock_dbdf = MockDBtable.return_value

        serializer = DatafilesSerializer()
        with pytest.raises(NameError):
            serializer._DatafilesSerializer__getFileLink("UID-123")


# ===========================================================================
# DatafilesSerializer.to_representation
# ===========================================================================
class TestDatafilesSerializerToRepresentation:
    @patch("nextseek_api.serializers.DBtable_data_files")
    def test_to_representation(self, MockDBtable):
        mock_dbdf = MockDBtable.return_value
        mock_dbdf.downloadDF_fromStorage.return_value = (
            "Download ready",
            200,
            "https://storage.example.com/file.pdf",
        )

        instance = MagicMock()
        instance.id = 42
        instance.title = "D.IMG-260101MIT-1"

        serializer = DatafilesSerializer()
        result = serializer.to_representation(instance)

        assert result["id"] == 42
        assert result["title"] == "D.IMG-260101MIT-1"
        assert result["link"] == "https://storage.example.com/file.pdf"
        assert result["msg"] == "Download ready"
        assert result["status"] == 200
        MockDBtable.assert_called_once_with("DEFAULT")
        mock_dbdf.downloadDF_fromStorage.assert_called_once_with(
            None, "D.IMG-260101MIT-1"
        )


# ===========================================================================
# SampleNodeSerializer
# ===========================================================================
class TestSampleNodeSerializer:
    def test_valid(self):
        data = {
            "id": "NHP-260101MIT-1",
            "uuid": "abc-def",
            "type": "NHP_blood",
            "color": "#ff0000",
            "parentIds": ["NHP-260101MIT-0"],
        }
        s = SampleNodeSerializer(data=data)
        assert s.is_valid(), s.errors
        assert s.validated_data["id"] == "NHP-260101MIT-1"

    def test_missing_required(self):
        s = SampleNodeSerializer(data={"id": "x"})
        assert not s.is_valid()
        assert "uuid" in s.errors


# ===========================================================================
# SampleTreeSerializer
# ===========================================================================
class TestSampleTreeSerializer:
    def test_valid_list(self):
        data = [
            {
                "id": "A",
                "uuid": "u1",
                "type": "t",
                "color": "c",
                "parentIds": [],
            },
            {
                "id": "B",
                "uuid": "u2",
                "type": "t",
                "color": "c",
                "parentIds": ["A"],
            },
        ]
        s = SampleTreeSerializer(data=data)
        assert s.is_valid(), s.errors
        assert len(s.validated_data) == 2


# ===========================================================================
# AdminRetrieveRequestSerializer
# ===========================================================================
class TestAdminRetrieveRequestSerializer:
    def test_empty_is_valid(self):
        s = AdminRetrieveRequestSerializer(data={})
        assert s.is_valid()

    def test_with_retrieval_uids(self):
        s = AdminRetrieveRequestSerializer(data={"retrieval_uids": ["UID1", "UID2"]})
        assert s.is_valid()

    def test_with_uids(self):
        s = AdminRetrieveRequestSerializer(data={"uids": ["UID1"]})
        assert s.is_valid()

    def test_with_text(self):
        s = AdminRetrieveRequestSerializer(data={"retrieval_uids_text": "UID1 UID2"})
        assert s.is_valid()

    def test_blank_text_is_valid(self):
        s = AdminRetrieveRequestSerializer(data={"retrieval_uids_text": ""})
        assert s.is_valid()


# ===========================================================================
# DataGridFilterRuleSerializer
# ===========================================================================
class TestDataGridFilterRuleSerializer:
    def test_valid(self):
        data = {"field": "sample_type_id", "op": "equal", "value": "12"}
        s = DataGridFilterRuleSerializer(data=data)
        assert s.is_valid()

    def test_blank_value_is_valid(self):
        data = {"field": "f", "op": "o", "value": ""}
        s = DataGridFilterRuleSerializer(data=data)
        assert s.is_valid()

    def test_missing_field(self):
        s = DataGridFilterRuleSerializer(data={"op": "equal", "value": "1"})
        assert not s.is_valid()
        assert "field" in s.errors


# ===========================================================================
# SampleRetrieveRequestSerializer
# ===========================================================================
class TestSampleRetrieveRequestSerializer:
    def test_empty_is_valid(self):
        s = SampleRetrieveRequestSerializer(data={})
        assert s.is_valid()

    def test_with_filter_rules(self):
        data = {
            "filterRules": [{"field": "f", "op": "o", "value": "v"}],
            "sampletype_id": 5,
            "page": 1,
            "rows": 10,
        }
        s = SampleRetrieveRequestSerializer(data=data)
        assert s.is_valid()

    def test_page_min_value(self):
        s = SampleRetrieveRequestSerializer(data={"page": 0})
        assert not s.is_valid()
        assert "page" in s.errors

    def test_rows_min_value(self):
        s = SampleRetrieveRequestSerializer(data={"rows": 0})
        assert not s.is_valid()
        assert "rows" in s.errors


# ===========================================================================
# SampleAdvancedRetrieveRequestSerializer
# ===========================================================================
class TestSampleAdvancedRetrieveRequestSerializer:
    def test_minimal(self):
        s = SampleAdvancedRetrieveRequestSerializer(data={"sampletype_id": 1})
        assert s.is_valid(), s.errors
        assert s.validated_data["attribute"] == "none"
        assert s.validated_data["project_id"] == 0

    def test_all_fields(self):
        data = {
            "sampletype_id": 3,
            "attribute": "temperature",
            "filter_rule": "between",
            "filter_valueFrom": "10",
            "filter_valueTo": "20",
            "project_id": 5,
        }
        s = SampleAdvancedRetrieveRequestSerializer(data=data)
        assert s.is_valid(), s.errors

    def test_missing_required_sampletype(self):
        s = SampleAdvancedRetrieveRequestSerializer(data={})
        assert not s.is_valid()
        assert "sampletype_id" in s.errors


# ===========================================================================
# SamplesByChildTypesRequestSerializer
# ===========================================================================
class TestSamplesByChildTypesRequestSerializer:
    def test_empty_is_valid(self):
        s = SamplesByChildTypesRequestSerializer(data={})
        assert s.is_valid()

    def test_with_titles(self):
        s = SamplesByChildTypesRequestSerializer(
            data={"sample_type_titles": ["NHP_blood", "NHP_tissue"]}
        )
        assert s.is_valid()

    def test_with_ids(self):
        s = SamplesByChildTypesRequestSerializer(data={"sample_type_ids": [1, 2]})
        assert s.is_valid()

    def test_null_titles_is_valid(self):
        s = SamplesByChildTypesRequestSerializer(data={"sample_type_titles": None})
        assert s.is_valid()


# ===========================================================================
# SampleUIDItemSerializer / SampleUIDListSerializer
# ===========================================================================
class TestSampleUIDItemSerializer:
    def test_valid(self):
        s = SampleUIDItemSerializer(data={"id": "NHP-1", "uuid": "abc"})
        assert s.is_valid()

    def test_missing_uuid(self):
        s = SampleUIDItemSerializer(data={"id": "NHP-1"})
        assert not s.is_valid()


class TestSampleUIDListSerializer:
    def test_valid_list(self):
        data = [
            {"id": "NHP-1", "uuid": "a"},
            {"id": "NHP-2", "uuid": "b"},
        ]
        s = SampleUIDListSerializer(data=data)
        assert s.is_valid()
        assert len(s.validated_data) == 2
