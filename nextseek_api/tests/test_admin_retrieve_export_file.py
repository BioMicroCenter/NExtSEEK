"""
``POST /nextseek_api/samples/retrieve/`` (and its ``admin/samples/retrieve/`` alias) with ``output_format=excel`` leaves no workbook where ``/media/`` serves.

The export used to be written to ``MEDIA_ROOT/download/download-samples-<YYYY-MM-DD_HH-MM>.xlsx`` and left there, and
that path was served by name alone, so the file's name was the only thing protecting it, and two exports made in the
same minute shared one path, which crossed two callers' responses. The workbook is now a private temporary file with a random
name outside ``MEDIA_ROOT``, removed as soon as the response holds it open; the download name is unchanged.

Hermetic: the rows and the workbook writer are stubbed; MEDIA_ROOT is a temporary directory.
"""

import os
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from django.test import override_settings
from rest_framework.test import APIRequestFactory

WORKBOOK = b"PK\x03\x04a-workbook"


@pytest.fixture
def media(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    with override_settings(MEDIA_ROOT=str(root)):
        yield root


def _export(written, superuser=True):
    from nextseek_api.services import sample_retrieve as sr
    from nextseek_api.views import AdminSampleViewSet

    request = APIRequestFactory().post("/")
    request.user = SimpleNamespace(is_authenticated=True, is_staff=True, is_superuser=superuser)
    request.data = {"identifiers": ["TIS-1"], "output_format": "excel"}
    viewset = AdminSampleViewSet()
    viewset.format_kwarg, viewset.kwargs, viewset.request = None, {}, request

    result = sr.RetrieveResult(
        frame=pd.DataFrame([{"id": 1, "sample_type_id": 1, "uuid": "TIS-1", "json_metadata": "{}"}]),
        requested_uids=["TIS-1"], unresolved_numeric=0, lineage_complete=True)

    def _write(_df, path, notice=None):
        written.append(path)
        with open(path, "wb") as fh:
            fh.write(WORKBOOK)

    dbs = MagicMock()
    dbs.sampleRetrievalData.side_effect = _write
    with patch.object(sr, "resolve_seek_auth", return_value=(("member", "pw"), {})), \
            patch.object(sr, "_caller_scope", return_value=sr.Scope(superuser, None, () if superuser else (2,))), \
            patch.object(sr, "retrieve_samples", return_value=result), \
            patch("seek.dbtable_sample.DBtable_sample", return_value=dbs):
        return viewset.admin_retrieve_samples(request)


def _inside(path, root):
    root = os.path.realpath(str(root))
    return os.path.commonpath([os.path.realpath(path), root]) == root


@pytest.mark.parametrize("superuser", [True, False], ids=["superuser", "member"])
def test_the_excel_export_leaves_no_workbook_under_media(media, superuser):
    written = []

    response = _export(written, superuser)
    body = b"".join(response.streaming_content)

    assert response.status_code == 200
    assert body == WORKBOOK
    assert re.search(r'filename="download-samples-\d{4}-\d\d-\d\d_\d\d-\d\d\.xlsx"', response["Content-Disposition"])
    (path,) = written
    assert not _inside(path, media)
    assert not os.path.exists(path), "the workbook must not outlive the response"
    assert [p for p in media.rglob("*") if p.is_file()] == []


def test_two_excel_exports_in_the_same_minute_never_share_a_file(media):
    written = []

    first = b"".join(_export(written).streaming_content)
    second = b"".join(_export(written).streaming_content)

    assert first == second == WORKBOOK
    assert len(written) == 2 and written[0] != written[1]
