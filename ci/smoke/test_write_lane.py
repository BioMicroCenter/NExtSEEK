"""Opt-in write lane. Deselected by default; run with `-m write`.

Everything here authenticates as ci_write, which must be a Django superuser: the
attributes and assay-registration endpoints are gated on IsSuperUser, and those
are exactly the endpoints whose defects reached production.

The lane is deliberately layered:

  * By default it proves the DRY-RUN contracts. Those are the ones that matter
    most, because a dry run that quietly writes is the worst possible bug in a
    preview feature, and they are provably free of side effects.
  * A real INSERT is behind a SECOND opt-in, CI_WRITE_DESTRUCTIVE=1, because
    creating an attribute is NOT cleanly reversible. Adding one to a sample type
    with gappy positions renumbers every definition below the gap, and deleting
    the attribute afterwards does not undo that renumbering.

Do not remove that second gate to make the lane "more thorough".
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ci.smoke.client import GuardedSession
from ci.smoke.conftest import SMOKE_SEARCH_TERM

pytestmark = pytest.mark.write

DESTRUCTIVE = os.environ.get("CI_WRITE_DESTRUCTIVE") == "1"
needs_destructive = pytest.mark.skipif(
    not DESTRUCTIVE,
    reason="real INSERT is gated behind CI_WRITE_DESTRUCTIVE=1; see the module docstring",
)


@pytest.fixture(scope="module")
def wapi(profile, base_url, write_creds):
    """A fresh Basic-authenticated client for the write account.

    Fresh, and separate from the read fixtures, for the same reason as always:
    a sessionid cookie would outrank the Basic header and silently change which
    identity is performing the write.
    """
    s = GuardedSession(profile=profile, base_url=base_url)
    s.auth = write_creds
    s.headers["Accept"] = "application/json"
    return s


def test_write_account_is_a_superuser(wapi, base_url):
    """If this fails, every other test in the lane fails with a confusing 403."""
    r = wapi.get(f"{base_url}/nextseek_api/assistant/me/", timeout=60)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
    assert r.json().get("is_admin") is True, (
        "CI_WRITE_USER is not a Django superuser, so the write lane cannot reach "
        "the attributes or assay-registration endpoints."
    )


def test_read_account_cannot_reach_a_write_endpoint(api, base_url):
    """The other half of the gate: prove the non-superuser is actually refused.

    A write lane that only ever tests the privileged path never notices when a
    permission class stops being applied.
    """
    r = api.post(
        f"{base_url}/nextseek_api/assay-registrations/",
        json={"registrations": [], "dry_run": True},
        timeout=60,
    )
    assert r.status_code in (401, 403), (
        f"a non-superuser got {r.status_code} from a superuser-gated endpoint"
    )


def test_assay_registration_dry_run_predicts_without_writing(wapi, base_url, web):
    """dry_run must return a full plan and touch nothing.

    The service returns before job creation, execution and the neo4j recompute,
    so a dry run that reports work is predicting it, not doing it. The assertions
    below pin exactly that: a prediction is present AND the write-side identifiers
    are absent.
    """
    # Discover a real sample rather than hard-coding a UID.
    r = web.get(
        f"{base_url}/seek/searchAdvanced/",
        params={"sampletype_id": "", "attribute": "none", "filter_logic": "AND",
                "filter_searchValue": "", "filter_searchText": SMOKE_SEARCH_TERM,
                "filter_matchType": "PARTIAL"},
        timeout=180,
    )
    rows = r.json().get("rows") or []
    if not rows:
        pytest.skip("no samples available to plan against")
    import re
    uid = re.sub(r"<[^>]+>", "", str(rows[0]["uid"])).strip()

    resp = wapi.post(
        f"{base_url}/nextseek_api/assay-registrations/",
        json={"registrations": [{"sample_uid": uid, "assay_id": 1}], "dry_run": True},
        timeout=120,
    )
    assert resp.status_code in (200, 409), f"{resp.status_code}: {resp.text[:300]}"
    body = resp.json()
    assert body["mode"] == "dry_run", f"mode was {body.get('mode')!r}"
    assert "counts" in body and "rows" in body, "dry run returned no plan"
    # Nothing was actually persisted: no asset id, and the graph step was skipped.
    for row in body["rows"]:
        assert row.get("assay_assets_id") is None, (
            f"dry run reported a persisted assay_assets_id for {row.get('sample_uid')}"
        )
    assert body.get("graph", {}).get("status") == "skipped", (
        "dry run touched the graph"
    )


def test_template_generate_returns_a_real_workbook_and_writes_nothing(wapi, base_url):
    """/nextseek_api/templates/generate/ is superuser-gated but side-effect free.

    It lives in this lane only because of the permission class: the endpoint
    reads the catalog and streams bytes, so there is nothing to undo and no
    CI_WRITE_DESTRUCTIVE gate. What is worth pinning is that a superuser
    actually gets a workbook rather than a 403 or an HTML error page, and that
    the codes it is asked for are ones the catalog endpoint just offered --
    the two halves of the tool have to agree about what exists.
    """
    catalog = wapi.get(f"{base_url}/nextseek_api/templates/catalog/", timeout=60)
    assert catalog.status_code == 200, f"{catalog.status_code}: {catalog.text[:200]}"
    groups = catalog.json().get("groups") or []
    codes = [e["code"] for g in groups for e in (g.get("entries") or [])][:2]
    if not codes:
        pytest.skip("the catalog offers no sample types on this instance")

    resp = wapi.post(f"{base_url}/nextseek_api/templates/generate/",
                     json={"codes": codes}, timeout=120)
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text[:300]}"
    assert resp.headers.get("Content-Type") == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ), f"not an xlsx: {resp.headers.get('Content-Type')!r}"
    # PK\x03\x04 -- a real zip container, not an HTML error page served with the
    # right content type, which is what a 500 behind a proxy usually looks like.
    assert resp.content[:2] == b"PK", "response body is not a zip container"


def test_template_generate_refuses_an_unknown_code(wapi, base_url):
    """The one behaviour the API keeps that the picker page does not.

    /seek/templates/download/ drops an unknown code so a stale bookmark still
    produces the types it names; the API answers 422, because a workbook quietly
    missing a sheet is worse than a refusal. Both resolve through the same
    template_catalog.select_entries, so this is the only place they differ.
    """
    resp = wapi.post(f"{base_url}/nextseek_api/templates/generate/",
                     json={"codes": ["NO_SUCH_SAMPLE_TYPE"]}, timeout=60)
    assert resp.status_code == 422, f"{resp.status_code}: {resp.text[:300]}"


def test_the_read_account_cannot_generate_a_template(api, base_url):
    """generate is superuser-only; catalog is not. Prove the split is real."""
    resp = api.post(f"{base_url}/nextseek_api/templates/generate/",
                    json={"codes": []}, timeout=60)
    assert resp.status_code in (401, 403), (
        f"a non-superuser got {resp.status_code} from templates/generate/"
    )


def test_attribute_batch_create_dry_run_changes_no_sample_rows(wapi, base_url):
    """The attributes preview contract.

    The response validator itself rejects a preview that claims any sample rows
    changed, so this asserts the contract end to end rather than trusting it.
    """
    r = wapi.post(
        f"{base_url}/nextseek_api/attributes/search/",
        json={"targets": [{"sample_type": "TIS"}]},
        timeout=90,
    )
    if r.status_code != 200:
        pytest.skip(f"cannot read attributes to build a request: {r.status_code}")

    # Targets are NESTED: one entry per owning sample type, each carrying its own
    # attribute list. The request model is strict with extra="forbid", so a flat
    # payload or a stray key is a 422, not a coercion.
    probe = f"ci_smoke_{uuid.uuid4().hex[:8]}"
    resp = wapi.post(
        f"{base_url}/nextseek_api/attributes/batch-create/",
        json={
            "dry_run": True,
            "targets": [{
                "sample_type": "TIS",
                "attributes": [{
                    "title": probe,
                    "sample_attribute_type": "Text",
                    "required": False,
                }],
            }],
        },
        timeout=120,
    )
    assert resp.status_code in (200, 202), f"unexpected {resp.status_code}: {resp.text[:400]}"

    # And prove it: the attribute must not exist afterwards.
    check = wapi.post(
        f"{base_url}/nextseek_api/attributes/search/",
        json={"targets": [{"sample_type": "TIS"}]},
        timeout=90,
    )
    titles = {a["title"] for a in check.json().get("attributes", [])}
    assert probe not in titles, "the dry run actually created the attribute"


@needs_destructive
def test_attribute_create_then_delete(wapi, base_url):
    """A real INSERT and a real DELETE, behind the second gate.

    Teardown runs in a finally block so a mid-test failure still cleans up. Note
    that cleanup restores the attribute list but NOT the position renumbering the
    create may have caused.
    """
    probe = f"ci_smoke_{uuid.uuid4().hex[:8]}"
    created_id = None
    try:
        r = wapi.post(
            f"{base_url}/nextseek_api/attributes/batch-create/",
            json={"targets": [{
                "sample_type": "TIS",
                "attributes": [{"title": probe, "sample_attribute_type": "Text",
                                "required": False}],
            }]},
            timeout=180,
        )
        assert r.status_code in (200, 202), f"create failed {r.status_code}: {r.text[:300]}"

        found = wapi.post(
            f"{base_url}/nextseek_api/attributes/search/",
            json={"targets": [{"sample_type": "TIS"}]}, timeout=90,
        ).json().get("attributes", [])
        match = [a for a in found if a["title"] == probe]
        assert match, f"{probe} was not created"
        created_id = match[0]["id"]
    finally:
        if created_id is not None:
            wapi.post(
                f"{base_url}/nextseek_api/attributes/batch-delete/",
                json={"targets": [{"sample_type": "TIS", "attributes": [probe]}]},
                timeout=180,
            )
