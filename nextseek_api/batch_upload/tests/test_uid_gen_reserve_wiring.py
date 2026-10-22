"""Validation must not reserve UID indexes; an upload must."""
from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.batch_upload.orchestrator import _run_pre_insert_stages

ROW = {"SampleType": "NHP", "json_metadata": '{"Name":"X"}'}


@pytest.mark.parametrize("mutate, expected", [(False, False), (True, True)])
def test_run_uid_gen_reserve_follows_mutate_project_links(mutate, expected):
    with patch("nextseek_api.batch_upload.orchestrator.get_connection") as conn, patch(
        "nextseek_api.batch_upload.orchestrator.run_uid_gen", return_value=([], {"uids_generated": 0})
    ) as gen:
        conn.return_value.__enter__ = MagicMock()
        conn.return_value.__exit__ = MagicMock(return_value=False)
        _run_pre_insert_stages(
            [], 1, 1, rows=[ROW], run_name_check=False, run_dag=False, mutate_project_links=mutate
        )
    assert gen.call_args.kwargs["reserve"] is expected
