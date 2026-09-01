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

from nextseek_api.assay_registration.schemas import (
    RegistrationRequest,
    RegistrationRow,
    RowResult,
)
from nextseek_api.assay_registration.views import AssayRegistrationViewSet
from nextseek_api.endpoint_descriptions import ASSAY_UPDATE_DESC
from nextseek_api.permissions import IsSuperUser

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


REGISTRATION_ROW = ("POST", "/nextseek_api/assay-registrations/")


@pytest.mark.parametrize("catalog", CATALOGS, ids=["source", "baked"])
def test_agent_catalog_advertises_the_additive_registration_endpoint(catalog):
    """The PATCH warning routes agents here, so the row has to exist.

    Both copies: the baked one is what the CC agent reads, and
    test_shared_context_file_is_identical_to_source keeps them equal.
    """
    if not catalog.exists():
        pytest.skip(f"{catalog} not present in this checkout")
    rows = json.loads(catalog.read_text())
    advertised = {(r.get("method", "").upper(), r.get("path", "")) for r in rows}
    assert REGISTRATION_ROW in advertised, (
        f"{catalog.name} does not advertise {REGISTRATION_ROW}, but the assays "
        "PATCH description tells the agent to use it"
    )


@pytest.mark.parametrize("catalog", CATALOGS, ids=["source", "baked"])
def test_the_registration_row_says_it_cannot_delete(catalog):
    if not catalog.exists():
        pytest.skip(f"{catalog} not present in this checkout")
    rows = json.loads(catalog.read_text())
    [row] = [r for r in rows
             if (r.get("method", "").upper(), r.get("path", "")) == REGISTRATION_ROW]
    description = row["description"].lower()
    assert "additive" in description
    assert "cannot remove" in description


#: The request/response vocabulary the registration row teaches, mapped to the
#: model that actually defines each name.
#:
#: The two tests above prove the row EXISTS and that it says it is additive.
#: Neither can tell a usable row from an unusable one: a row with the right
#: method, the right path and the word "additive" still burns the agent's turn
#: if it names a key the endpoint does not accept. Both request models are
#: `extra="forbid"` (assay_registration/schemas.py), so a wrong key is a 422
#: with nothing in it that points back at the catalog that caused it.
#:
#: Each name is checked in BOTH directions, which is the point of keying on the
#: live model rather than a literal list: the catalog must teach the name, and
#: the model must still define it. A rename on either side lands here.
#:
#: `assay` is the weak member: the catalog side of its check is nearly
#: tautological, since "assay" is also an ordinary word in this description.
#: It stays because its model side is not weak: dropping the field from
#: RegistrationRow still fails this test.
ADVERTISED_FIELDS = {
    "sample_uid": RegistrationRow,
    "assay": RegistrationRow,
    "assay_id": RegistrationRow,
    "dry_run": RegistrationRequest,
    # The receipt promise. `written` is licensed by reading this back from the
    # database, so a description that misnames it promises a field no response
    # carries.
    "assay_assets_id": RowResult,
}


@pytest.mark.parametrize("catalog", CATALOGS, ids=["source", "baked"])
def test_the_registration_row_teaches_only_real_field_names(catalog):
    if not catalog.exists():
        pytest.skip(f"{catalog} not present in this checkout")
    rows = json.loads(catalog.read_text())
    [row] = [r for r in rows
             if (r.get("method", "").upper(), r.get("path", "")) == REGISTRATION_ROW]
    description = row["description"]
    for name, model in ADVERTISED_FIELDS.items():
        assert name in model.model_fields, (
            f"{model.__name__} no longer has a {name!r} field, so the "
            f"{catalog.name} description teaches a key the endpoint rejects."
        )
        assert name in description, (
            f"the registration row in {catalog.name} does not name {name!r}. "
            f"It is a live {model.__name__} field and the description is the "
            "only thing the agent reads before composing a body."
        )


@pytest.mark.parametrize("catalog", CATALOGS, ids=["source", "baked"])
def test_the_registration_row_states_the_permission_the_viewset_enforces(catalog):
    """The row must not offer the endpoint to callers it will 403.

    Pinned against the ViewSet rather than asserted on its own: if the
    permission is ever relaxed, this fails and the catalog gets corrected,
    instead of the catalog quietly staying wrong in the other direction.
    """
    if not catalog.exists():
        pytest.skip(f"{catalog} not present in this checkout")
    assert IsSuperUser in AssayRegistrationViewSet.permission_classes, (
        "AssayRegistrationViewSet no longer requires a superuser; the catalog "
        "description says it does."
    )
    rows = json.loads(catalog.read_text())
    [row] = [r for r in rows
             if (r.get("method", "").upper(), r.get("path", "")) == REGISTRATION_ROW]
    assert "superuser" in row["description"].lower(), (
        f"the registration row in {catalog.name} does not say the endpoint is "
        "superuser-only, but AssayRegistrationViewSet enforces IsSuperUser."
    )
