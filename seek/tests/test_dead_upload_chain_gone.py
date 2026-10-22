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


def test_sample_api_mixin_is_gone():
    import importlib.util
    from seek.dbtable_sample import DBtable_sample
    assert importlib.util.find_spec("seek.sample.api") is None
    for name in ("apiUploadSamples", "apiInsertSample", "updateSampleDFurl", "searchFileInSample"):
        assert not hasattr(DBtable_sample, name)
