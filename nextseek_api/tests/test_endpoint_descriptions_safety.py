"""The assay PATCH endpoint must not advertise itself as additive.

PATCH /nextseek_api/assays/{uid}/ forwards verbatim to SEEK
(nextseek_api/services/assays.py:196) and JSON:API PATCH on a to-many
relationship replaces the complete list. An example that reads as "add one
sample" therefore describes a destructive write: on assay 8 it removes 48,440
memberships. assistant/write_gate.py is confirmation-only and does not
re-check the endpoint, so no downstream gate catches it.
"""

import json
from pathlib import Path

import pytest

from nextseek_api.endpoint_descriptions import ASSAY_UPDATE_DESC

REPO = Path(__file__).resolve().parents[2]

#: BOTH copies. `test_shared_context_file_is_identical_to_source` in
#: nextseek_api/cc_assistant/tests/test_cc_context_drift_guard.py requires the
#: baked CC copy to equal the source byte for byte, so they change together.
#: The baked one is the copy the CC agent actually reads.
CATALOGS = (
    REPO / "chat_nextseek/src/chat_nextseek/context/min_api_endpoints.json",
    REPO / "docker/cc-runtime/build_context/plugins/nextseek/context/min_api_endpoints.json",
)

# Phrasings that instruct a caller to treat a complete-list PATCH as additive.
ADDITIVE_PHRASES = ("add additional sample", "add samples", "add a sample")


def test_assay_update_desc_does_not_advertise_adding_samples():
    lowered = ASSAY_UPDATE_DESC.lower()
    for phrase in ADDITIVE_PHRASES:
        assert phrase not in lowered, (
            f"ASSAY_UPDATE_DESC contains {phrase!r}. PATCH on assays is a "
            "complete-list replace; that example instructs an agent to delete "
            "every other membership."
        )


def test_assay_update_desc_warns_that_samples_are_replaced():
    lowered = ASSAY_UPDATE_DESC.lower()
    assert "replace" in lowered, (
        "ASSAY_UPDATE_DESC must state that a samples list REPLACES the "
        "existing membership set."
    )


def _assay_patch_entries(rows):
    """Catalog rows describing PATCH on an assay.

    Keyed on the structured `method` and `path` fields, never on a text scan.
    A text scan is wrong twice over: it would false-match the NEW additive
    endpoint, whose own description names PATCH /nextseek_api/assays/{uid}/ in
    order to warn against it, and it would match any row that merely mentions
    the word. The catalog is a flat list of rows with `method` and `path`
    keys; see test_cc_context_drift_guard.py's _advertised_mutations.
    """
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("method", "").upper() == "PATCH" and "/assays/" in row.get("path", ""):
            yield row


@pytest.mark.parametrize("catalog", CATALOGS, ids=["source", "baked"])
def test_agent_catalog_carries_no_additive_assay_patch_example(catalog):
    if not catalog.exists():
        pytest.skip(f"{catalog} not present in this checkout")
    entries = list(_assay_patch_entries(json.loads(catalog.read_text())))
    assert entries, f"no assays PATCH row in {catalog.name}; has the schema moved?"
    for entry in entries:
        blob = json.dumps(entry).lower()
        for phrase in ADDITIVE_PHRASES:
            assert phrase not in blob, (
                f"the assays PATCH row in {catalog} still contains {phrase!r}"
            )
