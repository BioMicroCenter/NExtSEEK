"""The batch path must refresh the sample-attribute cache.

Without this wiring, refresh_sample_type_attributes_cache is dead code and a
newly added sample-type attribute stays invisible to /curate-qc and to the
upload until the gunicorn workers are restarted.

The stamp semantics themselves (drop on change, keep when unchanged) are covered
by TestRefreshSampleTypeAttributesCache in test_prefetch.py. This file covers only
the wiring: that _run_pre_insert_stages -- the single path shared by validate
(validation.py:230) and upload (orchestrator.py:584) -- actually calls it.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from nextseek_api.batch_upload import prefetch as prefetch_module
from nextseek_api.batch_upload.orchestrator import _run_pre_insert_stages
from nextseek_api.batch_upload.prefetch import clear_caches as _clear_prefetch_caches
from nextseek_api.batch_upload.tests.test_orchestrator_levels import (
    FakeConnection,
    FakeDB,
)


@pytest.fixture(autouse=True)
def _isolate_prefetch_caches():
    _clear_prefetch_caches()
    yield
    _clear_prefetch_caches()


@contextmanager
def _fake_conn(db):
    yield FakeConnection(db)


def test_batch_path_refreshes_the_attribute_cache():
    """A stale attribute set from a previous run must not survive into a new batch."""
    # Simulate this worker having cached a pre-change attribute set. The sentinel is
    # a title the database never returns, so if it is still there afterwards the
    # entry survived the batch rather than being re-read.
    prefetch_module._SAMPLE_TYPE_ATTRIBUTES_CACHE[1] = {"StaleAttributeFromBeforeTheWrite"}

    rows = [
        {"UID": "NHP-260101TST-1", "SampleType": "NHP_blood",
         "json_metadata": "{}", "assay_ids": []},
    ]
    with patch("nextseek_api.batch_upload.orchestrator.get_connection",
               lambda: _fake_conn(FakeDB())):
        _run_pre_insert_stages(
            xlsx_paths=[], project_id=1, contributor_id=1,
            rows=rows, run_name_check=False, run_dag=False,
            mutate_project_links=False,
        )

    cached = prefetch_module._SAMPLE_TYPE_ATTRIBUTES_CACHE.get(1, set())
    assert "StaleAttributeFromBeforeTheWrite" not in cached, (
        "the batch path did not refresh the attribute cache, so a schema change "
        f"stays invisible until the workers restart (cache still holds {cached})"
    )
