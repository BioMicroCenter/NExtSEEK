"""``_storeSample`` refuses sample id 0: a failed insert must not write link rows or report success.

``DBconnection.storeOneRecord`` answers id 0 when the insert fails and ``DBtable.storeOneRecord`` still reports
status 1, so the legacy sheet upload used to write projects_samples, assays creators and assay_assets rows for
sample 0. Hermetic: the instance is built with ``__new__`` and every collaborator is stubbed.
"""
from unittest import mock

import pytest

from seek.sample import upload


def _table(sample_id, new_sample=True):
    from seek.dbtable_sample import DBtable_sample

    t = DBtable_sample.__new__(DBtable_sample)
    t._notEmptyLine = lambda r: True
    t._verifyRequiredFields = lambda r, h: ("", True)
    t._getRecord = lambda c, r, a, cid: ({"uuid": "U-1"}, new_sample)
    t.storeOneRecord = lambda u, rec: ("stored", 1, sample_id)
    for name in ("_updateSampleProject", "_updateSampleAssetsCreators", "_storeSample_assay_asset",
                 "_setSampleDatafileAssociation"):
        setattr(t, name, mock.Mock(return_value=("", 1)))
    return t


def _call(t):
    with mock.patch.object(upload, "enqueueSampleSync") as enq:
        out = t._storeSample({"username": "u", "user_id": 1}, "ST", {"UID": "U-1"},
                             {"headers_required": []}, [{"a": 1}], {"user_id": 2, "projectid": 3})
    return out, enq


@pytest.mark.parametrize("bad", [0, -1, "abc", None])
def test_id_zero_writes_no_link_rows_and_fails(bad):
    t = _table(bad)
    (msg, status, uid), enq = _call(t)
    assert status == 0
    assert msg.startswith(upload.SAMPLE_ERRORCODE["504"])
    t._updateSampleProject.assert_not_called()
    t._updateSampleAssetsCreators.assert_not_called()
    t._storeSample_assay_asset.assert_not_called()
    enq.assert_not_called()


@pytest.mark.parametrize("good", [42, "42"])
def test_real_id_still_links(good):
    t = _table(good)
    (msg, status, uid), enq = _call(t)
    assert status == 1
    t._updateSampleProject.assert_called_once()
    t._updateSampleAssetsCreators.assert_called_once()
    enq.assert_called_once_with("samples", good)


def test_existing_sample_string_id_is_an_update_not_a_failure():
    # DBtable.storeOneRecord answers str(primarykey) for a sample already in the table.
    t = _table("42", new_sample=False)
    (msg, status, uid), enq = _call(t)
    assert (msg, status, uid) == ("Info: No update on array-sample relationship for old sample id: 42", 1, "U-1")
    t._updateSampleProject.assert_not_called()
    enq.assert_called_once_with("samples", "42")
