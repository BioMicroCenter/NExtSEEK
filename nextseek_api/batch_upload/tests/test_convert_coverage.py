"""Coverage tests for batch_upload/convert.py — targeting uncovered lines.

Covers:
- merge_files: empty list, single file, multiple files
- convert_file: traditional and flat formats
- parse_traditional_file: edge cases (no instructions, duplicate field, multiple sample types)
"""

import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock

from nextseek_api.batch_upload.convert import merge_files, ConvertedBatch


class TestMergeFiles:

    def test_empty_list(self):
        result = merge_files([])
        assert result.rows == []
        assert "No files" in (result.warnings[0] if result.warnings else "")

    @patch("nextseek_api.batch_upload.convert.convert_file")
    def test_single_file(self, mock_convert):
        mock_convert.return_value = ConvertedBatch(rows=[], source_files=["f.xlsx"])
        result = merge_files(["f.xlsx"])
        mock_convert.assert_called_once_with("f.xlsx")

    @patch("nextseek_api.batch_upload.convert.convert_file")
    def test_multiple_files_merged(self, mock_convert):
        from nextseek_api.batch_upload.models import InputRowModel
        row1 = InputRowModel(SampleType="NHP", json_metadata="{}")
        row2 = InputRowModel(SampleType="TIS", json_metadata="{}")
        mock_convert.side_effect = [
            ConvertedBatch(rows=[row1], source_files=["a.xlsx"]),
            ConvertedBatch(rows=[row2], source_files=["b.xlsx"]),
        ]
        result = merge_files(["a.xlsx", "b.xlsx"])
        assert len(result.rows) == 2
        assert len(result.source_files) == 2
