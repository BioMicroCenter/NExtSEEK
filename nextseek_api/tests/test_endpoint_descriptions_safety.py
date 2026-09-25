"""The assay PATCH endpoint must not advertise itself as additive.

PATCH /nextseek_api/assays/{uid}/ forwards verbatim to SEEK
(nextseek_api/services/assays.py:196) and JSON:API PATCH on a to-many
relationship replaces the complete list. An example that reads as "add one
sample" therefore describes a destructive write: on assay 8 it removes 48,440
memberships. NessieAI/ns/write_gate.py is confirmation-only and does not
re-check the endpoint, so no downstream gate catches it.
"""

import json
import re

import pytest

from NessieAI import paths
from NessieAI.tests.cc.image_context import image_context_source
from nextseek_api.assay_registration.schemas import (
    RegistrationAcceptedResponse,
    RegistrationRequest,
    RegistrationRow,
    RowResult,
)
from nextseek_api.assay_registration.views import AssayRegistrationViewSet
from nextseek_api.endpoint_descriptions import ASSAY_UPDATE_DESC
from nextseek_api.permissions import IsSuperUser

#: The source file and the file the CC agent reads. Since NessieAI Phase C the
#: cc-agent image COPYs the source file itself from the chat_nextseek named
#: context, so "baked" resolves (through the Dockerfile's COPY lines) to the same
#: file; `test_shared_context_file_is_identical_to_source` in
#: NessieAI/tests/cc/test_cc_context_drift_guard.py would catch a second copy.
CATALOGS = (
    paths.CHAT_NEXTSEEK_DIR / "src" / "chat_nextseek" / "context" / "min_api_endpoints.json",
    image_context_source("min_api_endpoints.json"),
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
    rows = json.loads(catalog.read_text())
    entries = list(_assay_patch_entries(rows))
    if not entries:
        # Since the 2026-09-24 ruling (3bfb2f10) the catalog lists read pairs only, so it offers
        # no assay write for an agent to misread. Pin that, so the test is never vacuous.
        writes = sorted(_assay_writes(rows))
        assert not writes, (
            f"{catalog.name} has no assays PATCH row but offers {writes}; has the schema moved?"
        )
        return
    for entry in entries:
        blob = json.dumps(entry).lower()
        for phrase in ADDITIVE_PHRASES:
            assert phrase not in blob, (
                f"the assays PATCH row in {catalog} still contains {phrase!r}"
            )


REGISTRATION_ROW = ("POST", "/nextseek_api/assay-registrations/")

_MUTATING = ("POST", "PUT", "PATCH", "DELETE")


def _advertised(rows):
    return {(r.get("method", "").upper(), r.get("path", "")) for r in rows if isinstance(r, dict)}


def _assay_writes(rows):
    """Every mutating (method, path) pair on an assay or assay-registration path."""
    return {(m, p) for m, p in _advertised(rows) if m in _MUTATING and "/assay" in p}


def _registration_row_or_skip(catalog, rows):
    """The registration row, or a skip when the catalog does not offer it.

    The row checks below are about a row an agent can read. Since the 2026-09-24 ruling
    (3bfb2f10) the catalog offers no assay write, so there is no row to check;
    test_agent_catalog_advertises_the_additive_registration_endpoint still requires it
    whenever the assays PATCH row comes back.
    """
    matches = [r for r in rows
               if (r.get("method", "").upper(), r.get("path", "")) == REGISTRATION_ROW]
    if not matches:
        pytest.skip(f"{catalog.name} does not offer {REGISTRATION_ROW} (read pairs only)")
    [row] = matches
    return row


@pytest.mark.parametrize("catalog", CATALOGS, ids=["source", "baked"])
def test_agent_catalog_advertises_the_additive_registration_endpoint(catalog):
    """The PATCH warning routes agents here, so the row has to exist.

    Both parameters: the baked one is the file the CC agent reads (the source
    file itself, since the image COPYs it from the chat_nextseek named context).
    """
    if not catalog.exists():
        pytest.skip(f"{catalog} not present in this checkout")
    rows = json.loads(catalog.read_text())
    advertised = _advertised(rows)
    if not list(_assay_patch_entries(rows)):
        # No PATCH row to warn from (read pairs only since 2026-09-24): then no write at all.
        assert not _assay_writes(rows), sorted(_assay_writes(rows))
        return
    assert REGISTRATION_ROW in advertised, (
        f"{catalog.name} does not advertise {REGISTRATION_ROW}, but the assays "
        "PATCH description tells the agent to use it"
    )


@pytest.mark.parametrize("catalog", CATALOGS, ids=["source", "baked"])
def test_the_registration_row_says_it_cannot_delete(catalog):
    if not catalog.exists():
        pytest.skip(f"{catalog} not present in this checkout")
    rows = json.loads(catalog.read_text())
    row = _registration_row_or_skip(catalog, rows)
    description = row["description"].lower()
    assert "additive" in description
    assert "cannot remove" in description


#: Every model whose field names the description is allowed to teach. Note
#: RegistrationAcceptedResponse: the 202 path's job_id and status_url are real
#: fields an agent must know, and omitting the model here would make the guard
#: below reject its own correct description.
ADVERTISED_MODELS = (
    RegistrationRow, RegistrationRequest, RowResult, RegistrationAcceptedResponse,
)

#: snake_case tokens, which is the shape every field name in these models takes.
_SNAKE = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")


@pytest.mark.parametrize("catalog", CATALOGS, ids=["source", "baked"])
def test_the_registration_row_teaches_no_key_the_models_reject(catalog):
    """The "only" half, which an in-the-description check cannot cover.

    Asserting that every real field APPEARS catches a name being dropped. It
    does not catch a name being INVENTED, and an invented one is worse: the
    request models are extra="forbid", so a description mentioning `sample_id`
    teaches an agent a key that is rejected outright. This walks the other
    direction -- every snake_case token in the description must be a field of
    one of the models above.
    """
    if not catalog.exists():
        pytest.skip(f"{catalog} not present in this checkout")
    rows = json.loads(catalog.read_text())
    row = _registration_row_or_skip(catalog, rows)

    known = {name for model in ADVERTISED_MODELS for name in model.model_fields}
    taught = set(_SNAKE.findall(row["description"]))
    invented = taught - known
    assert not invented, (
        f"the registration row teaches {sorted(invented)}, which no request or "
        f"response model declares. The request models forbid extra keys, so an "
        f"invented name is a 422 an agent cannot debug from the catalog."
    )


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
#:
#: NOT exhaustive, and one absence is deliberate: `registrations` is a real
#: RegistrationRequest field that the description does name, but requiring it
#: here would pin the envelope key's prose rather than the agent's ability to
#: use it, which test_the_registration_row_teaches_no_key_the_models_reject
#: already covers from the other side. Read this as "names the description must
#: teach", not "every field that exists".
ADVERTISED_FIELDS = {
    "sample_uid": RegistrationRow,
    "assay": RegistrationRow,
    "assay_id": RegistrationRow,
    "dry_run": RegistrationRequest,
    # The receipt promise. `written` is licensed by reading this back from the
    # database, so a description that misnames it promises a field no response
    # carries.
    "assay_assets_id": RowResult,
    # The 202 path. service.register (service.py:96-114) returns
    # RegistrationAcceptedResponse above ASSAY_REGISTRATION_SYNC_ROW_THRESHOLD,
    # and that model has NO `rows` field, so a description that never mentions
    # the mode leaves an agent expecting rows it will not get and no way to
    # reach the per-row report. Requiring these two names is what makes "the
    # catalog must say the async mode exists" a test rather than a promise: if
    # the mode is ever removed, RegistrationAcceptedResponse goes with it and
    # this import fails loudly instead of the catalog going quietly stale.
    "job_id": RegistrationAcceptedResponse,
    "status_url": RegistrationAcceptedResponse,
}


@pytest.mark.parametrize("catalog", CATALOGS, ids=["source", "baked"])
def test_the_registration_row_teaches_only_real_field_names(catalog):
    if not catalog.exists():
        pytest.skip(f"{catalog} not present in this checkout")
    # The model side first: it holds whether or not this catalog offers the row.
    for name, model in ADVERTISED_FIELDS.items():
        assert name in model.model_fields, (
            f"{model.__name__} no longer has a {name!r} field, so the "
            f"{catalog.name} description teaches a key the endpoint rejects."
        )
    rows = json.loads(catalog.read_text())
    row = _registration_row_or_skip(catalog, rows)
    description = row["description"]
    for name, model in ADVERTISED_FIELDS.items():
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
    row = _registration_row_or_skip(catalog, rows)
    assert "superuser" in row["description"].lower(), (
        f"the registration row in {catalog.name} does not say the endpoint is "
        "superuser-only, but AssayRegistrationViewSet enforces IsSuperUser."
    )
