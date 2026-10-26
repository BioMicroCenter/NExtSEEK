"""Batch upload: a workbook that inflates past the caps is refused before any parse; saved names are unique (SEC-H12, SEC-H37)."""
import io
import struct
import zipfile
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory, force_authenticate

from nextseek_api.batch_upload.views import BatchUploadViewSet, _save_uploaded_file
from nextseek_api.batch_upload import xlsx_guard
from nextseek_api.batch_upload.xlsx_guard import XlsxExpansionError, check_xlsx_expansion


def _zip(member_bytes, count=1):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n in range(count):
            z.writestr(f"xl/worksheets/sheet{n}.xml", b"0" * member_bytes)
    buf.seek(0)
    return buf


def test_small_workbook_passes_and_stream_is_rewound():
    buf = _zip(50_000)
    check_xlsx_expansion(buf)
    assert buf.tell() == 0


def test_one_huge_member_is_refused():
    with pytest.raises(XlsxExpansionError):
        check_xlsx_expansion(_zip(30 * 1024 * 1024))


def test_many_members_over_the_total_are_refused():
    with pytest.raises(XlsxExpansionError):
        check_xlsx_expansion(_zip(20 * 1024 * 1024, count=3))


def test_high_ratio_inflation_is_refused():
    with pytest.raises(XlsxExpansionError):
        check_xlsx_expansion(_zip(20 * 1024 * 1024))


def _lying_zip(real_bytes):
    """A zip whose central directory states a tiny uncompressed size for a member that really inflates to real_bytes."""
    raw = _zip(real_bytes).getvalue()
    at = raw.index(b"PK\x01\x02")
    patched = raw[:at + 24] + struct.pack("<I", 100) + raw[at + 28:]  # file_size field
    return io.BytesIO(patched)


def test_a_declared_size_smaller_than_the_real_content_is_refused(monkeypatch):
    monkeypatch.setattr(xlsx_guard, "MAX_MEMBER_BYTES", 1024 * 1024)
    with pytest.raises(XlsxExpansionError):
        check_xlsx_expansion(_lying_zip(3 * 1024 * 1024))


def test_an_unsupported_compression_method_is_refused():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_BZIP2) as z:
        z.writestr("a.xml", b"x" * 100)
    with pytest.raises(XlsxExpansionError):
        check_xlsx_expansion(buf)


def test_a_corrupt_member_is_a_refusal_not_a_crash():
    raw = bytearray(_zip(50_000).getvalue())
    raw[40:60] = b"\xff" * 20  # inside the deflate stream
    with pytest.raises(XlsxExpansionError):
        check_xlsx_expansion(io.BytesIO(bytes(raw)))


def test_non_zip_passes_to_the_parser_to_refuse():
    check_xlsx_expansion(io.BytesIO(b"not a zip"))


def _user():
    user = MagicMock()
    user.pk = 1
    user.is_authenticated = True
    user.is_active = True
    user.is_superuser = True
    return user


def _post(action, name):
    view = BatchUploadViewSet.as_view({"post": action})
    request = APIRequestFactory().post(
        f"/nextseek_api/batch_upload/{action}/",
        {"project_id": 1, "file": SimpleUploadedFile("b.xlsx", _zip(40 * 1024 * 1024).read())},
        format="multipart",
    )
    force_authenticate(request, user=_user())
    return view(request)


@pytest.mark.parametrize("action", ["start", "validate"])
@patch("nextseek_api.batch_upload.views._save_uploaded_file")
@patch("nextseek_api.batch_upload.views._resolve_user_context",
       return_value={"contributor_id": 42, "lababbv": "MIT"})
def test_bomb_is_refused_by_start_and_validate_and_never_saved(_ctx, save, action):
    resp = _post(action, "b.xlsx")
    assert resp.status_code == 413
    assert "detail" in resp.data
    save.assert_not_called()


def test_validate_carries_the_upload_throttles():
    from nextseek_api.throttles import UPLOAD_THROTTLES
    from rest_framework.test import APIRequestFactory as F
    view = BatchUploadViewSet.as_view({"post": "validate"})
    assert view.initkwargs.get("throttle_classes") == UPLOAD_THROTTLES or \
        BatchUploadViewSet.validate.kwargs.get("throttle_classes") == UPLOAD_THROTTLES


def test_same_name_in_the_same_second_gets_distinct_paths(tmp_path):
    with patch("nextseek_api.batch_upload.views.state_dir", return_value=str(tmp_path)), \
            patch("nextseek_api.batch_upload.views.time.time", return_value=1000):
        a = _save_uploaded_file(SimpleUploadedFile("same.xlsx", b"a"))
        b = _save_uploaded_file(SimpleUploadedFile("same.xlsx", b"b"))
    assert a != b
    assert open(a, "rb").read() == b"a" and open(b, "rb").read() == b"b"
