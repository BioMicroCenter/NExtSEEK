"""v1 byte-equivalence baseline.

Rationale: task-01 establishes the contract that every v1 response body is
byte-identical across (a) no Accept header, (b) `application/json`, and
(c) `application/vnd.nextseek.v1+json`. Any future task that breaks this
invariant turns this test red — preventing silent v1 regressions.

The test hits samples/advanced_search (touched by task-02) with a deterministic
upstream mock and compares rendered response.content bytes across the three
variants. A separate structural-shape assertion guards against pathological
empty-but-equal responses (all three returning `b""` would otherwise pass).

AMD-05 (2026-05-04): switched from a pinned-literal expected-bytes assertion
to a property-based cross-variant equality + structural-shape check. Original
literal baked in `json.dumps` separator assumptions that didn't match the
view's default-spacing output. The cross-variant invariant is what actually
catches v1 regressions; the structural check guards against empty/error
bodies that happen to be identical across variants.
"""
import json
from unittest.mock import patch

import pytest


@pytest.fixture
def deterministic_upstream():
    with patch("nextseek_api.services.samples.DBtable_sample") as m:
        m.return_value.searchAdvanced.return_value = (
            '{"rows":[{"id":1,"title":"sample-1"}],"total":1,'
            '"sampleTypes":["TIS"],"noSampleTypes":1,"footer":[]}'
        )
        yield m


@pytest.mark.django_db
def test_v1_bytes_identical_across_accept_headers(auth_client, deterministic_upstream):
    """All three v1 Accept variants must render byte-identical bodies."""
    accept_variants = [
        None,                                     # no Accept header
        "application/json",                       # generic
        "application/vnd.nextseek.v1+json",       # explicit v1
    ]
    bodies = []
    for accept in accept_variants:
        kwargs = {"HTTP_ACCEPT": accept} if accept else {}
        resp = auth_client.post(
            "/nextseek_api/samples/advanced_search/", {}, format="json", **kwargs,
        )
        assert resp.status_code == 200, (
            f"Accept={accept!r} returned {resp.status_code}, body={resp.content!r}"
        )
        bodies.append((accept, resp.content))

    reference_accept, reference_bytes = bodies[0]
    for accept, body in bodies[1:]:
        assert body == reference_bytes, (
            f"v1 byte-drift: Accept={accept!r} returned {body!r}, "
            f"differs from Accept={reference_accept!r} which returned {reference_bytes!r}"
        )


@pytest.mark.django_db
def test_v1_response_has_expected_structural_shape(auth_client, deterministic_upstream):
    """Guard against the byte-equivalence test passing on an empty/error body
    that happens to be identical across all three variants."""
    resp = auth_client.post(
        "/nextseek_api/samples/advanced_search/", {}, format="json",
    )
    assert resp.status_code == 200
    payload = json.loads(resp.content)
    assert payload["total"] == 1
    assert payload["rows"] == [{"id": 1, "title": "sample-1"}]
    assert payload["sampleTypes"] == ["TIS"]
    assert payload["noSampleTypes"] == 1
