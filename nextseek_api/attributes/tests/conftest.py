# Plain imports, not `pytest_plugins`: pytest refuses that variable in a conftest below the rootdir, which made the
# whole folder a collection error whenever it was collected with other paths (as CI does). Imported fixtures
# register for this folder only.
from nextseek_api.attributes.tests.attribute_fixtures import (  # noqa: F401
    _attribute_default_db_environment_sync,
    attribute_broker_lane,
    attribute_faults,
    disposable_attribute_db,
    rails_like_workload,
    sql_telemetry,
)
from nextseek_api.attributes.tests.auth_boundary import seek_auth_boundary  # noqa: F401

import os
from pathlib import Path

import pytest

_HERE = Path(__file__).parent

# Tests that need the author's evidence rig are skipped without it (ci/CLAUDE.md: a test that needs a live service
# skips itself without that service). Folder-wide: every test using the disposable database.
_NEEDS_RIG = (
    ("ATTRIBUTE_TEST_DB_HOST", "needs the disposable MariaDB the evidence rig starts (ATTRIBUTE_TEST_DB_HOST); CI has none"),
)
# By <file>::<test>: these read evidence-rig inputs. Where the variable is set they run for real.
_RIG_INPUT = {
    "test_openapi.py::test_machine_schema_contract_matches_generated_components": (
        "ATTRIBUTE_EVIDENCE_RUN_ROOT", "reads an author-machine file (SCHEMA_CONTRACT in test_openapi.py:9)"),
    "test_performance_metadata.py::test_metadata_matrix_has_exact_162_cells_and_810_runs": (
        "ATTRIBUTE_EVIDENCE_RUN_ROOT", "reads the benchmark run root (ATTRIBUTE_EVIDENCE_RUN_ROOT)"),
    "test_metadata_benchmark.py::test_chunk_selector_produces_hash_bound_artifact": (
        "ATTRIBUTE_EVIDENCE_RUN_ROOT", "reads the benchmark run root (ATTRIBUTE_EVIDENCE_RUN_ROOT)"),
    "test_real_boundary_contract.py::test_fault_controller_only_accepts_frozen_points": (
        "ATTRIBUTE_TEST_FAULT_CONTROL", "needs ATTRIBUTE_TEST_FAULT_CONTROL from the evidence rig"),
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        path = Path(str(item.path))
        if _HERE not in path.parents:
            continue
        key = f"{path.relative_to(_HERE).as_posix()}::{item.originalname or item.name}"
        if "disposable_attribute_db" in item.fixturenames:
            var, why = _NEEDS_RIG[0]
            item.add_marker(pytest.mark.skipif(var not in os.environ, reason=why))
        if key in _RIG_INPUT:
            var, why = _RIG_INPUT[key]
            item.add_marker(pytest.mark.skipif(var not in os.environ, reason=why))
