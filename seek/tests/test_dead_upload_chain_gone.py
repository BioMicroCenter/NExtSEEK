"""The data-file upload chain and the curl POST helper had no caller; keep them gone."""
from seek.dbtable_data_files import DBtable_data_files
from seek.seekapi import SeekAPI
from seek.seekdb import SeekDB


def test_data_file_upload_chain_is_gone():
    for name in ("apiUploadFile", "uploadDF_toStorage", "uploadDF_storageToSeek"):
        assert not hasattr(DBtable_data_files, name)
    assert not hasattr(SeekDB, "seekupload_dfurl")
    for name in ("apiPost", "callCmdline"):
        assert not hasattr(SeekAPI, name)
