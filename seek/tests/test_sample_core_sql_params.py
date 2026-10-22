"""SampleCore._updateSampleProject binds its values instead of formatting them into the SQL."""

from unittest.mock import MagicMock, patch

from seek.sample.core import SampleCore


def test_update_sample_project_binds_its_values():
    conn = MagicMock()
    with patch("seek.sample.core.MySQLdb.connect", return_value=conn):
        SampleCore.__new__(SampleCore)._updateSampleProject({"projectid": 5}, 7)

    args = conn.cursor.return_value.execute.call_args.args
    assert args[1] == (5, 7)
    assert "%s" in args[0] and not any(c.isdigit() for c in args[0])
