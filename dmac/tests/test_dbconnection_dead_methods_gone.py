"""DBconnection methods nothing calls (one referenced an undefined name, one an unimported module)."""
from dmac.dbconnection import DBconnection


def test_dead_methods_are_gone():
    for name in ("getStringValueAdditive", "updateNotesViaKeyword", "retrieveAllRecordsIntoExcel",
                 "retrieveAllRecordsIntoExcel_V2", "_DBconnection__mergeStringsAdditive"):
        assert not hasattr(DBconnection, name), name
