from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import orjson
import pytest
from pydantic import ValidationError

from nextseek_api.attributes import schemas


def valid_record() -> dict:
    return {
        "id": 7,
        "title": "Concentration",
        "sample_type_id": 3,
        "sample_type_title": "Serum",
        "sample_attribute_type_id": 2,
        "sample_attribute_type_title": "Float",
        "required": False,
        "pos": 4,
        "is_title": False,
        "description": "Measured concentration.",
        "unit_id": 8,
        "unit_title": "nanogram per millilitre",
        "unit_symbol": "ng/mL",
        "sample_controlled_vocab_id": None,
        "sample_controlled_vocab_title": None,
        "linked_sample_type_id": None,
        "linked_sample_type_title": None,
        "created_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 3, tzinfo=timezone.utc),
    }


def test_explicit_sample_type_identifiers_validate():
    for identifier in (4, "4", "Serum"):
        result = schemas.SEARCH_REQUEST_ADAPTER.validate_python({"targets": [{"sample_type": identifier}]})
        assert result.targets[0].sample_type == identifier


def test_unicode_decimal_identifier_is_an_exact_title_not_an_id():
    # Arabic-Indic digits satisfy str.isdecimal(), but T04's SQL resolver accepts
    # only ASCII [0-9]+ as an ID spelling.
    value = "١٢"
    assert schemas._identifier_key(value) == ("title-string", value)


def test_pattern_selectors_are_forbidden():
    for value in ({"prefix": "Ser"}, ["Serum"], ""):
        with pytest.raises(ValidationError):
            schemas.SEARCH_REQUEST_ADAPTER.validate_python({"targets": [{"sample_type": value}]})


def test_nested_target_preserves_association():
    result = schemas.SEARCH_REQUEST_ADAPTER.validate_python({"targets": [
        {"sample_type": "Serum", "attributes": ["Mass"]},
        {"sample_type": "Blood", "attributes": ["Volume"]},
    ]})
    assert [(target.sample_type, target.attributes) for target in result.targets] == [
        ("Serum", ["Mass"]), ("Blood", ["Volume"]),
    ]


def test_flat_and_cross_product_shapes_rejected():
    for payload in (
        {"sample_types": ["Serum", "Blood"], "attributes": ["Mass", "Volume"]},
        {"targets": [{"sample_type": ["Serum", "Blood"], "attributes": ["Mass"]}]},
    ):
        with pytest.raises(ValidationError):
            schemas.SEARCH_REQUEST_ADAPTER.validate_python(payload)


@pytest.mark.parametrize("identifier", [4, "4", "Serum", "001"])
def test_search_accepts_mixed_identifiers_without_coercion(identifier):
    model = schemas.SEARCH_REQUEST_ADAPTER.validate_python(
        {"targets": [{"sample_type": identifier}]}
    )
    assert model.targets[0].sample_type == identifier
    assert type(model.targets[0].sample_type) is type(identifier)
    assert model.targets[0].attributes is None


@pytest.mark.parametrize("identifier", [True, False, 1.5, "", "   ", None, [], {}])
def test_identifier_invalid_neighbors_are_rejected(identifier):
    with pytest.raises(ValidationError):
        schemas.SEARCH_REQUEST_ADAPTER.validate_python(
            {"targets": [{"sample_type": identifier}]}
        )


def test_search_rejects_explicitly_empty_attributes_and_empty_targets():
    with pytest.raises(ValidationError):
        schemas.SEARCH_REQUEST_ADAPTER.validate_python(
            {"targets": [{"sample_type": "Serum", "attributes": []}]}
        )
    with pytest.raises(ValidationError):
        schemas.SEARCH_REQUEST_ADAPTER.validate_python({"targets": []})


def test_empty_search_attributes_rejected():
    with pytest.raises(ValidationError):
        schemas.SEARCH_REQUEST_ADAPTER.validate_python({"targets": [{"sample_type": 1, "attributes": []}]})


def test_create_is_nested_nonempty_and_forbids_extras():
    payload = {
        "targets": [{
            "sample_type": "Serum",
            "attributes": [{
                "title": "Concentration",
                "sample_attribute_type": "Float",
                "required": False,
                "pos": 2,
                "is_title": False,
                "description": None,
                "unit": "nanogram per millilitre",
                "sample_controlled_vocab": None,
                "linked_sample_type": None,
            }],
        }],
        "dry_run": True,
    }
    model = schemas.CREATE_REQUEST_ADAPTER.validate_python(payload)
    assert model.dry_run is True
    assert model.targets[0].attributes[0].unit == "nanogram per millilitre"
    for bad in (
        {"targets": [], "dry_run": False},
        {"targets": [{"sample_type": 1, "attributes": []}]},
        {**payload, "unexpected": 1},
        {"targets": [{"sample_type": 1, "attributes": [{"title": "X", "sample_attribute_type": 2, "bogus": 3}]}]},
    ):
        with pytest.raises(ValidationError):
            schemas.CREATE_REQUEST_ADAPTER.validate_python(bad)


def test_literal_duplicate_create_deduplicates_identical_and_rejects_drift():
    item = {"title": "Mass", "sample_attribute_type": 2, "required": False}
    model = schemas.CREATE_REQUEST_ADAPTER.validate_python(
        {"targets": [{"sample_type": 1, "attributes": [item, dict(item)]}]}
    )
    assert len(model.targets[0].attributes) == 1
    drift = dict(item, required=True)
    with pytest.raises(ValidationError, match="conflicting duplicate create"):
        schemas.CREATE_REQUEST_ADAPTER.validate_python(
            {"targets": [{"sample_type": 1, "attributes": [item, drift]}]}
        )
    case_variant = dict(item, title="mass")
    unresolved = schemas.CREATE_REQUEST_ADAPTER.validate_python(
        {"targets": [{"sample_type": 1, "attributes": [item, case_variant]}]}
    )
    assert [entry.title for entry in unresolved.targets[0].attributes] == ["Mass", "mass"]


def test_patch_omission_differs_from_explicit_null_and_noop_is_rejected():
    payload = {
        "targets": [{
            "sample_type": "Serum",
            "attributes": [{"attribute": "Concentration", "changes": {"description": None}}],
        }],
        "dry_run": False,
    }
    model = schemas.PATCH_REQUEST_ADAPTER.validate_python(payload)
    changes = model.targets[0].attributes[0].changes
    assert changes.description is None
    assert changes.model_fields_set == {"description"}
    assert "unit" not in changes.model_fields_set
    with pytest.raises(ValidationError, match="at least one patch change"):
        schemas.PATCH_REQUEST_ADAPTER.validate_python(
            {"targets": [{"sample_type": 1, "attributes": [{"attribute": 2, "changes": {}}]}]}
        )
    for field in ("title", "sample_attribute_type", "required", "pos", "is_title"):
        with pytest.raises(ValidationError):
            schemas.PATCH_REQUEST_ADAPTER.validate_python(
                {"targets": [{"sample_type": 1, "attributes": [{"attribute": 2, "changes": {field: None}}]}]}
            )


def test_patch_delete_sample_type_omission_only_allows_id_grammar():
    schemas.PATCH_REQUEST_ADAPTER.validate_python(
        {"targets": [{"attributes": [{"attribute": "12", "changes": {"required": True}}]}]}
    )
    schemas.DELETE_REQUEST_ADAPTER.validate_python(
        {"targets": [{"attributes": [12, "13"]}]}
    )
    with pytest.raises(ValidationError, match="sample_type is required"):
        schemas.PATCH_REQUEST_ADAPTER.validate_python(
            {"targets": [{"attributes": [{"attribute": "UID", "changes": {"required": True}}]}]}
        )
    with pytest.raises(ValidationError, match="sample_type is required"):
        schemas.DELETE_REQUEST_ADAPTER.validate_python(
            {"targets": [{"attributes": [12, "UID"]}]}
        )


def test_all_id_patch_delete_may_omit_type():
    patch = schemas.PATCH_REQUEST_ADAPTER.validate_python({"targets": [{"attributes": [{"attribute": "12", "changes": {"required": True}}]}]})
    delete = schemas.DELETE_REQUEST_ADAPTER.validate_python({"targets": [{"attributes": [12, "13"]}]})
    assert patch.targets[0].sample_type is None
    assert delete.targets[0].sample_type is None


def test_title_patch_delete_requires_type():
    with pytest.raises(ValidationError):
        schemas.PATCH_REQUEST_ADAPTER.validate_python({"targets": [{"attributes": [{"attribute": "UID", "changes": {"required": True}}]}]})
    with pytest.raises(ValidationError):
        schemas.DELETE_REQUEST_ADAPTER.validate_python({"targets": [{"attributes": ["UID"]}]})


def test_relationship_fields_accept_mixed_identifiers_and_null():
    for value in (7, "7", "nanogram per millilitre", None):
        model = schemas.CREATE_REQUEST_ADAPTER.validate_python({"targets": [{"sample_type": 1, "attributes": [{"title": "Mass", "sample_attribute_type": 2, "unit": value}]}]})
        assert model.targets[0].attributes[0].unit == value


def test_literal_duplicate_patch_and_delete_rules():
    same = {"attribute": 7, "changes": {"description": "x"}}
    model = schemas.PATCH_REQUEST_ADAPTER.validate_python(
        {"targets": [{"attributes": [same, dict(same)]}]}
    )
    assert len(model.targets[0].attributes) == 1
    unresolved = schemas.PATCH_REQUEST_ADAPTER.validate_python(
        {"targets": [{"attributes": [same, {"attribute": "7", "changes": {"description": "y"}}]}]}
    )
    assert [entry.attribute for entry in unresolved.targets[0].attributes] == [7, "7"]
    deleted = schemas.DELETE_REQUEST_ADAPTER.validate_python(
        {"targets": [{"attributes": [7, "7", 8, 7]}]}
    )
    assert deleted.targets[0].attributes == [7, "7", 8]


def test_attribute_record_has_exact_approved_properties_and_json_round_trip():
    expected = {
        "id", "title", "sample_type_id", "sample_type_title",
        "sample_attribute_type_id", "sample_attribute_type_title", "required", "pos",
        "is_title", "description", "unit_id", "unit_title", "unit_symbol",
        "sample_controlled_vocab_id", "sample_controlled_vocab_title",
        "linked_sample_type_id", "linked_sample_type_title", "created_at", "updated_at",
    }
    assert set(schemas.AttributeRecord.model_fields) == expected
    model = schemas.AttributeRecord.model_validate(valid_record())
    encoded = orjson.dumps(model.model_dump(mode="json"))
    assert schemas.AttributeRecord.model_validate(orjson.loads(encoded)) == model


def test_attribute_record_exact_properties():
    assert set(schemas.AttributeRecord.model_fields) == set(valid_record())


def test_attribute_record_rejects_internal_columns():
    with pytest.raises(ValidationError):
        schemas.AttributeRecord.model_validate({**valid_record(), "uuid": "internal", "sample_attribute_type_id": 2})


def test_response_models_validate_discriminators_counts_and_error_indexes():
    counts = schemas.MutationCounts(requested=1, resolved=1, created=1)
    outcome = schemas.SampleTypeMutationOutcome(
        sample_type_id=3,
        sample_type_title="Serum",
        status="succeeded",
        counts=counts,
        attributes=[schemas.AttributeRecord.model_validate(valid_record())],
        automatic_changes=[],
        errors=[],
    )
    preview = schemas.MutationPreviewResponse(
        mode="dry_run", predicted_mode="synchronous", overall_status="succeeded",
        threshold=1000, counts=counts, outcomes=[outcome],
    )
    assert preview.mode == "dry_run"
    completed = schemas.MutationCompletedResponse(
        mode="synchronous", overall_status="succeeded", http_status=200,
        counts=counts, outcomes=[outcome],
    )
    assert completed.http_status == 200
    error = schemas.MutationError(
        code="missing", message="Not found.", target_index=0, attribute_index=0,
        field="attribute", submitted_identifier="unknown",
    )
    assert schemas.AttributeErrorResponse(errors=[error]).errors[0].target_index == 0
    with pytest.raises(ValidationError):
        schemas.MutationCounts(requested=-1)
    with pytest.raises(ValidationError):
        schemas.MutationError(code="x", message="x", target_index=-1)


@pytest.mark.parametrize("counts", [
    {"requested": 0, "resolved": 1},
    {"requested": 4, "resolved": 3, "created": 1, "patched": 1, "deleted": 1, "unchanged": 1},
    {"requested": 1, "resolved": 1, "affected_samples": 2, "updated_samples": 3},
])
def test_mutation_count_cross_field_invariants_reject(counts):
    with pytest.raises(ValidationError):
        schemas.MutationCounts.model_validate(counts)


def test_dry_run_and_async_acceptance_require_zero_updated_samples():
    invalid = schemas.MutationCounts(requested=1, resolved=1, patched=1, affected_samples=1, updated_samples=1)
    outcome = schemas.SampleTypeMutationOutcome(
        sample_type_id=3, sample_type_title="Serum", status="succeeded", counts=invalid,
    )
    with pytest.raises(ValidationError):
        schemas.MutationPreviewResponse(
            mode="dry_run", predicted_mode="synchronous", overall_status="succeeded",
            threshold=1, counts=invalid, outcomes=[outcome],
        )
    with pytest.raises(ValidationError):
        schemas.MutationAcceptedResponse(
            mode="asynchronous", job_id="12345678-1234-5678-1234-567812345678",
            status_url="/jobs/1", counts=invalid,
        )


@pytest.mark.parametrize("response_model,extra", [
    (schemas.MutationPreviewResponse, {"mode": "dry_run", "predicted_mode": "synchronous", "threshold": 1}),
    (schemas.MutationCompletedResponse, {"mode": "synchronous", "http_status": 200}),
])
def test_top_level_counts_must_equal_sum_of_per_type_counts(response_model, extra):
    per_type = schemas.MutationCounts(requested=1, resolved=1, created=1)
    top_level = schemas.MutationCounts(requested=2, resolved=2, created=2)
    outcome = schemas.SampleTypeMutationOutcome(
        sample_type_id=3, sample_type_title="Serum", status="succeeded", counts=per_type,
    )
    with pytest.raises(ValidationError):
        response_model(overall_status="succeeded", counts=top_level, outcomes=[outcome], **extra)


def test_mutation_envelopes_discriminate_modes():
    schema = schemas.MutationPreviewResponse.model_json_schema()
    assert schema["properties"]["mode"]["const"] == "dry_run"
    completed = schemas.MutationCompletedResponse.model_json_schema()
    assert set(completed["properties"]["mode"]["enum"]) == {"synchronous", "asynchronous"}


def test_async_terminal_shape_cannot_diverge():
    result_annotation = schemas.MutationJobStatusResponse.model_fields["result"].annotation
    assert "MutationCompletedResponse" in str(result_annotation)


def test_approved_request_family_exact_schema():
    assert set(schemas.SearchRequest.model_fields) == {"targets"}
    assert set(schemas.BatchCreateRequest.model_fields) == {"targets", "dry_run"}
    assert set(schemas.BatchPatchRequest.model_fields) == {"targets", "dry_run"}
    assert set(schemas.BatchDeleteRequest.model_fields) == {"targets", "dry_run"}


def test_extra_fields_and_noop_patch_rejected():
    with pytest.raises(ValidationError):
        schemas.SEARCH_REQUEST_ADAPTER.validate_python({"targets": [{"sample_type": 1}], "extra": True})
    with pytest.raises(ValidationError):
        schemas.PATCH_REQUEST_ADAPTER.validate_python({"targets": [{"attributes": [{"attribute": 1, "changes": {}}]}]})


def test_approved_response_family_exact_schema():
    assert set(schemas.AttributeListResponse.model_fields) == {"attributes", "pagination"}
    assert set(schemas.MutationPreviewResponse.model_fields) == {"mode", "predicted_mode", "overall_status", "threshold", "counts", "outcomes"}
    assert set(schemas.MutationAcceptedResponse.model_fields) == {"mode", "job_id", "status_url", "counts"}


def test_final_response_property_sets_exact():
    test_attribute_record_exact_properties()
    assert set(schemas.MutationCounts.model_fields) == {"requested", "resolved", "created", "patched", "deleted", "unchanged", "reordered", "affected_samples", "updated_samples"}
    assert set(schemas.MutationError.model_fields) == {"code", "message", "target_index", "attribute_index", "field", "submitted_identifier"}


def test_every_property_has_a_specific_description_and_models_forbid_extras():
    models = [
        schemas.SearchTarget, schemas.SearchRequest, schemas.AttributeCreate,
        schemas.CreateTarget, schemas.BatchCreateRequest, schemas.AttributePatchChanges,
        schemas.AttributePatch, schemas.PatchTarget, schemas.BatchPatchRequest,
        schemas.DeleteTarget, schemas.BatchDeleteRequest, schemas.AttributeRecord,
        schemas.Pagination, schemas.MutationCounts, schemas.MutationError,
        schemas.AutomaticChange, schemas.SampleTypeMutationOutcome,
        schemas.AttributeListResponse, schemas.MutationPreviewResponse,
        schemas.MutationCompletedResponse, schemas.MutationAcceptedResponse,
        schemas.MutationJobStatusResponse, schemas.AttributeErrorResponse,
    ]
    for model in models:
        assert model.model_config["extra"] == "forbid"
        for name, field in model.model_fields.items():
            description = field.description or ""
            assert len(description.split()) >= 4, (model.__name__, name, description)
            assert description.lower() not in {name.lower(), f"the {name.lower()}"}


def test_module_level_adapters_are_bound_to_envelopes():
    assert schemas.SEARCH_REQUEST_ADAPTER.validate_python({"targets": [{"sample_type": 1}]}).__class__ is schemas.SearchRequest
    assert schemas.CREATE_REQUEST_ADAPTER.validate_python({"targets": [{"sample_type": 1, "attributes": [{"title": "X", "sample_attribute_type": 1}]}]}).__class__ is schemas.BatchCreateRequest
    assert schemas.PATCH_REQUEST_ADAPTER.validate_python({"targets": [{"attributes": [{"attribute": 1, "changes": {"description": None}}]}]}).__class__ is schemas.BatchPatchRequest
    assert schemas.DELETE_REQUEST_ADAPTER.validate_python({"targets": [{"attributes": [1]}]}).__class__ is schemas.BatchDeleteRequest


# --- Section 9A / 9B: exact-node acceptance authority additions ---

JOB_ID = "12345678-1234-5678-1234-567812345678"
JOB_UUID = UUID(JOB_ID)


def _error(code, **overrides):
    kwargs = {"code": code, "message": "failure detail", "target_index": 0}
    kwargs.update(overrides)
    return schemas.MutationError(**kwargs)


def _outcome(status, counts=None, errors=()):
    return schemas.SampleTypeMutationOutcome(
        sample_type_id=1, sample_type_title="Serum", status=status,
        counts=counts or schemas.MutationCounts(), errors=list(errors),
    )


def _aggregate(outcomes):
    totals = {field: sum(getattr(item.counts, field) for item in outcomes) for field in schemas.MutationCounts.model_fields}
    return schemas.MutationCounts(**totals)


def _completed_for_state(state):
    if state == "succeeded":
        outcomes = [_outcome("succeeded", counts=schemas.MutationCounts(requested=1, resolved=1, created=1))]
        overall_status, http_status = "succeeded", 200
    elif state == "partial":
        outcomes = [
            _outcome("succeeded", counts=schemas.MutationCounts(requested=1, resolved=1, created=1)),
            _outcome("failed", errors=[_error("attribute_not_found")]),
        ]
        overall_status, http_status = "partial", 207
    elif state == "failed":
        outcomes = [_outcome("failed", errors=[_error("attribute_not_found")])]
        overall_status, http_status = "failed", 422
    elif state == "cancelled":
        outcomes = [_outcome("cancelled")]
        overall_status, http_status = "cancelled", 409
    else:
        raise ValueError(f"unsupported terminal state: {state}")
    return schemas.MutationCompletedResponse(
        mode="asynchronous", overall_status=overall_status, http_status=http_status,
        counts=_aggregate(outcomes), outcomes=outcomes,
    )


@pytest.mark.parametrize("case", [
    pytest.param("identical-same-type", id="identical-same-type"),
    pytest.param("integer-vs-numeric-string", id="integer-vs-numeric-string"),
    pytest.param("title-case-distinct", id="title-case-distinct"),
    pytest.param("resolved-identity-deferred", id="resolved-identity-deferred"),
])
def test_duplicate_boundary_preserves_literal_identity(case):
    if case == "identical-same-type":
        # exact repeated operation deduplicates with provenance
        item = {"attribute": 7, "changes": {"description": "same"}}
        result = schemas.PATCH_REQUEST_ADAPTER.validate_python(
            {"targets": [{"attributes": [item, dict(item)]}]}
        )
        assert len(result.targets[0].attributes) == 1
    elif case == "integer-vs-numeric-string":
        # JSON integer 7 and JSON string "7" remain distinct
        result = schemas.DELETE_REQUEST_ADAPTER.validate_python({"targets": [{"attributes": [7, "7"]}]})
        assert result.targets[0].attributes == [7, "7"]
    elif case == "title-case-distinct":
        # "Mass" and "mass" remain distinct
        item = {"title": "Mass", "sample_attribute_type": 2, "required": False}
        variant = dict(item, title="mass")
        result = schemas.CREATE_REQUEST_ADAPTER.validate_python(
            {"targets": [{"sample_type": 1, "attributes": [item, variant]}]}
        )
        assert [entry.title for entry in result.targets[0].attributes] == ["Mass", "mass"]
    else:
        # physical/resolved identity is handed unchanged to T04/T05: identical changes
        # for int 7 vs string "7" are still not merged, because resolution is deferred.
        same_changes = {"attribute": 7, "changes": {"description": "same"}}
        variant_changes = {"attribute": "7", "changes": {"description": "same"}}
        result = schemas.PATCH_REQUEST_ADAPTER.validate_python(
            {"targets": [{"attributes": [same_changes, variant_changes]}]}
        )
        assert [entry.attribute for entry in result.targets[0].attributes] == [7, "7"]


@pytest.mark.parametrize("case", [
    pytest.param("reject-nan", id="reject-nan"),
    pytest.param("reject-positive-infinity", id="reject-positive-infinity"),
    pytest.param("reject-negative-infinity", id="reject-negative-infinity"),
    pytest.param("round-trip-nested-json", id="round-trip-nested-json"),
])
def test_json_value_contract(case):
    if case == "round-trip-nested-json":
        nested = {"a": [1, "two", None, {"b": 3.5}], "c": True}
        change = schemas.AutomaticChange(
            kind="reorder", attribute_id=1, attribute_title="Concentration",
            field="pos", previous_value=nested, new_value=nested,
        )
        encoded = orjson.dumps(change.model_dump(mode="json"))
        restored = schemas.AutomaticChange.model_validate(orjson.loads(encoded))
        assert restored == change
        return
    bad_value = {
        "reject-nan": float("nan"),
        "reject-positive-infinity": float("inf"),
        "reject-negative-infinity": float("-inf"),
    }[case]
    with pytest.raises(ValidationError):
        schemas.AutomaticChange(
            kind="reorder", attribute_id=1, attribute_title="Concentration",
            field="pos", previous_value=bad_value, new_value=1,
        )


@pytest.mark.parametrize("case", [
    pytest.param("accepted-relative", id="accepted-relative"),
    pytest.param("reject-absolute-http", id="reject-absolute-http"),
    pytest.param("reject-absolute-https", id="reject-absolute-https"),
    pytest.param("reject-scheme-relative", id="reject-scheme-relative"),
])
def test_relative_status_url_grammar(case):
    counts = schemas.MutationCounts(requested=1, resolved=1, created=1)
    urls = {
        "accepted-relative": "/nextseek_api/attributes/jobs/123/",
        "reject-absolute-http": "http://example.com/nextseek_api/attributes/jobs/123/",
        "reject-absolute-https": "https://example.com/nextseek_api/attributes/jobs/123/",
        "reject-scheme-relative": "//example.com/nextseek_api/attributes/jobs/123/",
    }
    url = urls[case]
    if case == "accepted-relative":
        model = schemas.MutationAcceptedResponse(mode="asynchronous", job_id=JOB_UUID, status_url=url, counts=counts)
        assert model.status_url == url
    else:
        with pytest.raises(ValidationError):
            schemas.MutationAcceptedResponse(mode="asynchronous", job_id=JOB_UUID, status_url=url, counts=counts)


PREVIEW_STATUSES = ("succeeded", "partial", "failed")


def _assert_only_valid_preview_status(outcomes, expected):
    counts = _aggregate(outcomes)
    for candidate in PREVIEW_STATUSES:
        kwargs = dict(mode="dry_run", predicted_mode="synchronous", threshold=1000, counts=counts, outcomes=outcomes)
        if candidate == expected:
            model = schemas.MutationPreviewResponse(overall_status=candidate, **kwargs)
            assert model.overall_status == expected
        else:
            with pytest.raises(ValidationError):
                schemas.MutationPreviewResponse(overall_status=candidate, **kwargs)


@pytest.mark.parametrize("case", [
    pytest.param("succeeded-only", id="succeeded-only"),
    pytest.param("unchanged-only", id="unchanged-only"),
    pytest.param("succeeded-unchanged", id="succeeded-unchanged"),
    pytest.param("succeeded-failed", id="succeeded-failed"),
    pytest.param("unchanged-failed", id="unchanged-failed"),
    pytest.param("failed-only", id="failed-only"),
    pytest.param("cancelled-skipped-only", id="cancelled-skipped-only"),
    pytest.param("empty-outcomes", id="empty-outcomes"),
])
def test_preview_status_matrix(case):
    if case == "succeeded-only":
        outcomes = [_outcome("succeeded", counts=schemas.MutationCounts(requested=1, resolved=1, created=1))]
        _assert_only_valid_preview_status(outcomes, "succeeded")
    elif case == "unchanged-only":
        outcomes = [_outcome("unchanged", counts=schemas.MutationCounts(requested=1, resolved=1, unchanged=1))]
        _assert_only_valid_preview_status(outcomes, "succeeded")
    elif case == "succeeded-unchanged":
        outcomes = [
            _outcome("succeeded", counts=schemas.MutationCounts(requested=1, resolved=1, created=1)),
            _outcome("unchanged", counts=schemas.MutationCounts(requested=1, resolved=1, unchanged=1)),
        ]
        _assert_only_valid_preview_status(outcomes, "succeeded")
    elif case == "succeeded-failed":
        outcomes = [
            _outcome("succeeded", counts=schemas.MutationCounts(requested=1, resolved=1, created=1)),
            _outcome("failed", errors=[_error("attribute_not_found")]),
        ]
        _assert_only_valid_preview_status(outcomes, "partial")
    elif case == "unchanged-failed":
        outcomes = [
            _outcome("unchanged", counts=schemas.MutationCounts(requested=1, resolved=1, unchanged=1)),
            _outcome("failed", errors=[_error("attribute_not_found")]),
        ]
        _assert_only_valid_preview_status(outcomes, "partial")
    elif case == "failed-only":
        outcomes = [_outcome("failed", errors=[_error("attribute_not_found")])]
        _assert_only_valid_preview_status(outcomes, "failed")
    elif case == "cancelled-skipped-only":
        outcomes = [_outcome("cancelled"), _outcome("skipped")]
        _assert_only_valid_preview_status(outcomes, "failed")
    else:  # empty-outcomes
        with pytest.raises(ValidationError):
            schemas.MutationPreviewResponse(
                mode="dry_run", predicted_mode="synchronous", overall_status="succeeded",
                threshold=1000, counts=schemas.MutationCounts(), outcomes=[],
            )


COMPLETED_OVERALL_STATUSES = ("succeeded", "partial", "failed", "cancelled")
COMPLETED_HTTP_STATUSES = (200, 207, 409, 422)


def _assert_only_valid_completed_pair(outcomes, expected_pair):
    counts = _aggregate(outcomes)
    for overall_status in COMPLETED_OVERALL_STATUSES:
        for http_status in COMPLETED_HTTP_STATUSES:
            kwargs = dict(mode="synchronous", overall_status=overall_status, http_status=http_status, counts=counts, outcomes=outcomes)
            if (overall_status, http_status) == expected_pair:
                model = schemas.MutationCompletedResponse(**kwargs)
                assert (model.overall_status, model.http_status) == expected_pair
            else:
                with pytest.raises(ValidationError):
                    schemas.MutationCompletedResponse(**kwargs)


@pytest.mark.parametrize("case", [
    pytest.param("succeeded-200", id="succeeded-200"),
    pytest.param("unchanged-only-200", id="unchanged-only-200"),
    pytest.param("succeeded-unchanged-200", id="succeeded-unchanged-200"),
    pytest.param("partial-207", id="partial-207"),
    pytest.param("unchanged-failed-207", id="unchanged-failed-207"),
    pytest.param("cancelled-409", id="cancelled-409"),
    pytest.param("conflict-409", id="conflict-409"),
    pytest.param("semantic-invalid-422", id="semantic-invalid-422"),
    pytest.param("skipped-only-409", id="skipped-only-409"),
    pytest.param("cancelled-skipped-conflict-409", id="cancelled-skipped-conflict-409"),
    pytest.param("semantic-precedes-conflict-cancel-skipped-422", id="semantic-precedes-conflict-cancel-skipped-422"),
    pytest.param("malformed-never-completed", id="malformed-never-completed"),
    pytest.param("empty-outcomes-rejected", id="empty-outcomes-rejected"),
])
def test_completed_status_http_matrix(case):
    if case == "succeeded-200":
        outcomes = [_outcome("succeeded", counts=schemas.MutationCounts(requested=1, resolved=1, created=1))]
        _assert_only_valid_completed_pair(outcomes, ("succeeded", 200))
    elif case == "unchanged-only-200":
        outcomes = [_outcome("unchanged", counts=schemas.MutationCounts(requested=1, resolved=1, unchanged=1))]
        _assert_only_valid_completed_pair(outcomes, ("succeeded", 200))
    elif case == "succeeded-unchanged-200":
        outcomes = [
            _outcome("succeeded", counts=schemas.MutationCounts(requested=1, resolved=1, created=1)),
            _outcome("unchanged", counts=schemas.MutationCounts(requested=1, resolved=1, unchanged=1)),
        ]
        _assert_only_valid_completed_pair(outcomes, ("succeeded", 200))
    elif case == "partial-207":
        outcomes = [
            _outcome("succeeded", counts=schemas.MutationCounts(requested=1, resolved=1, created=1)),
            _outcome("failed", errors=[_error("attribute_not_found")]),
        ]
        _assert_only_valid_completed_pair(outcomes, ("partial", 207))
    elif case == "unchanged-failed-207":
        outcomes = [
            _outcome("unchanged", counts=schemas.MutationCounts(requested=1, resolved=1, unchanged=1)),
            _outcome("failed", errors=[_error("attribute_not_found")]),
        ]
        _assert_only_valid_completed_pair(outcomes, ("partial", 207))
    elif case == "cancelled-409":
        for outcomes in (
            [_outcome("cancelled")],
            [_outcome("cancelled"), _outcome("skipped")],
        ):
            _assert_only_valid_completed_pair(outcomes, ("cancelled", 409))
    elif case == "conflict-409":
        outcomes = [_outcome("failed", errors=[_error("conflicting_duplicate_operation")])]
        _assert_only_valid_completed_pair(outcomes, ("failed", 409))
    elif case == "semantic-invalid-422":
        outcomes = [_outcome("failed", errors=[_error("attribute_not_found")])]
        _assert_only_valid_completed_pair(outcomes, ("failed", 422))
    elif case == "skipped-only-409":
        outcomes = [_outcome("skipped")]
        _assert_only_valid_completed_pair(outcomes, ("failed", 409))
    elif case == "cancelled-skipped-conflict-409":
        outcomes = [
            _outcome("failed", errors=[_error("conflicting_duplicate_operation")]),
            _outcome("cancelled"), _outcome("skipped"),
        ]
        _assert_only_valid_completed_pair(outcomes, ("failed", 409))
    elif case == "semantic-precedes-conflict-cancel-skipped-422":
        outcomes = [
            _outcome("failed", errors=[_error("attribute_not_found"), _error("conflicting_duplicate_operation")]),
            _outcome("cancelled"), _outcome("skipped"),
        ]
        _assert_only_valid_completed_pair(outcomes, ("failed", 422))
    elif case == "malformed-never-completed":
        outcomes = [_outcome("succeeded", counts=schemas.MutationCounts(requested=1, resolved=1, created=1))]
        with pytest.raises(ValidationError):
            schemas.MutationCompletedResponse(
                mode="synchronous", overall_status="failed", http_status=400,
                counts=_aggregate(outcomes), outcomes=outcomes,
            )
        error_response = schemas.AttributeErrorResponse(errors=[_error("invalid_json_metadata")])
        assert not hasattr(error_response, "http_status")
    else:  # empty-outcomes-rejected
        with pytest.raises(ValidationError):
            schemas.MutationCompletedResponse(
                mode="synchronous", overall_status="succeeded", http_status=200,
                counts=schemas.MutationCounts(), outcomes=[],
            )


@pytest.mark.parametrize("case", [
    pytest.param("queued-null-result-zero-progress", id="queued-null-result-zero-progress"),
    pytest.param("running-null-result-bounded-progress", id="running-null-result-bounded-progress"),
    pytest.param("succeeded-terminal-exact-progress", id="succeeded-terminal-exact-progress"),
    pytest.param("partial-terminal-exact-progress", id="partial-terminal-exact-progress"),
    pytest.param("failed-terminal-exact-progress", id="failed-terminal-exact-progress"),
    pytest.param("cancelled-terminal-exact-progress", id="cancelled-terminal-exact-progress"),
])
def test_job_state_progress_matrix(case):
    if case == "queued-null-result-zero-progress":
        model = schemas.MutationJobStatusResponse(
            job_id=JOB_UUID, state="queued", completed_sample_types=0, total_sample_types=3,
            processed_samples=0, total_samples=100, result=None,
        )
        assert model.result is None
        assert (model.completed_sample_types, model.processed_samples) == (0, 0)
    elif case == "running-null-result-bounded-progress":
        model = schemas.MutationJobStatusResponse(
            job_id=JOB_UUID, state="running", completed_sample_types=1, total_sample_types=3,
            processed_samples=40, total_samples=100, result=None,
        )
        assert model.result is None
        assert model.completed_sample_types <= model.total_sample_types
        assert model.processed_samples <= model.total_samples
    else:
        state = case.split("-", 1)[0]
        result = _completed_for_state(state)
        model = schemas.MutationJobStatusResponse(
            job_id=JOB_UUID, state=state, completed_sample_types=3, total_sample_types=3,
            processed_samples=100, total_samples=100, result=result,
        )
        assert model.result.overall_status == state
        assert (model.completed_sample_types, model.processed_samples) == (model.total_sample_types, model.total_samples)


@pytest.mark.parametrize("case", [
    pytest.param("result-on-queued", id="result-on-queued"),
    pytest.param("missing-terminal-result", id="missing-terminal-result"),
    pytest.param("completed-types-over-total", id="completed-types-over-total"),
    pytest.param("processed-samples-over-total", id="processed-samples-over-total"),
    pytest.param("terminal-inexact-types", id="terminal-inexact-types"),
    pytest.param("terminal-inexact-samples", id="terminal-inexact-samples"),
    pytest.param("result-state-disagrees", id="result-state-disagrees"),
])
def test_job_state_progress_invalid_neighbor(case):
    base = dict(job_id=JOB_UUID, total_sample_types=3, total_samples=100)
    with pytest.raises(ValidationError):
        if case == "result-on-queued":
            schemas.MutationJobStatusResponse(
                **base, state="queued", completed_sample_types=0, processed_samples=0,
                result=_completed_for_state("succeeded"),
            )
        elif case == "missing-terminal-result":
            schemas.MutationJobStatusResponse(
                **base, state="succeeded", completed_sample_types=3, processed_samples=100, result=None,
            )
        elif case == "completed-types-over-total":
            schemas.MutationJobStatusResponse(
                **base, state="running", completed_sample_types=4, processed_samples=0, result=None,
            )
        elif case == "processed-samples-over-total":
            schemas.MutationJobStatusResponse(
                **base, state="running", completed_sample_types=0, processed_samples=150, result=None,
            )
        elif case == "terminal-inexact-types":
            schemas.MutationJobStatusResponse(
                **base, state="succeeded", completed_sample_types=2, processed_samples=100,
                result=_completed_for_state("succeeded"),
            )
        elif case == "terminal-inexact-samples":
            schemas.MutationJobStatusResponse(
                **base, state="succeeded", completed_sample_types=3, processed_samples=50,
                result=_completed_for_state("succeeded"),
            )
        else:  # result-state-disagrees
            schemas.MutationJobStatusResponse(
                **base, state="succeeded", completed_sample_types=3, processed_samples=100,
                result=_completed_for_state("failed"),
            )


def test_all_non_identifier_scalars_reject_coercive_invalid_neighbors():
    with pytest.raises(ValidationError):
        schemas.AttributeCreate(title="X", sample_attribute_type=1, required="true")
    with pytest.raises(ValidationError):
        schemas.AttributeCreate(title="X", sample_attribute_type=1, pos="2")
    with pytest.raises(ValidationError):
        schemas.MutationCounts(requested="1")
    with pytest.raises(ValidationError):
        schemas.MutationAcceptedResponse(
            mode="asynchronous", job_id="not-a-uuid",
            status_url="/nextseek_api/attributes/jobs/1/", counts=schemas.MutationCounts(),
        )
    with pytest.raises(ValidationError):
        schemas.AttributeRecord.model_validate({**valid_record(), "id": "7"})
    with pytest.raises(ValidationError):
        schemas.AttributeRecord.model_validate({**valid_record(), "required": 1})


def test_every_contract_model_forbids_unknown_extra_properties():
    with pytest.raises(ValidationError):
        schemas.SearchRequest.model_validate({"targets": [{"sample_type": 1}], "bogus": True})
    with pytest.raises(ValidationError):
        schemas.AttributeRecord.model_validate({**valid_record(), "bogus": True})
    with pytest.raises(ValidationError):
        schemas.MutationCounts.model_validate({"requested": 1, "bogus": True})
    with pytest.raises(ValidationError):
        schemas.AttributePatchChanges.model_validate({"description": "x", "bogus": True})


def test_patch_omission_and_explicit_null_remain_distinct():
    omitted = schemas.AttributePatchChanges(description="kept")
    assert "unit" not in omitted.model_fields_set
    explicit_null = schemas.AttributePatchChanges(description="kept", unit=None)
    assert "unit" in explicit_null.model_fields_set
    assert explicit_null.unit is None


def test_t01_duplicate_boundary_is_byte_and_json_type_identical_only():
    result = schemas.DELETE_REQUEST_ADAPTER.validate_python({"targets": [{"attributes": [7, "7", 7]}]})
    assert result.targets[0].attributes == [7, "7"]


def test_completed_response_rejects_every_invalid_status_http_neighbor():
    outcomes = [_outcome("succeeded", counts=schemas.MutationCounts(requested=1, resolved=1, created=1))]
    counts = _aggregate(outcomes)
    for overall_status in COMPLETED_OVERALL_STATUSES:
        for http_status in COMPLETED_HTTP_STATUSES:
            if (overall_status, http_status) == ("succeeded", 200):
                continue
            with pytest.raises(ValidationError):
                schemas.MutationCompletedResponse(
                    mode="synchronous", overall_status=overall_status, http_status=http_status,
                    counts=counts, outcomes=outcomes,
                )


def test_job_state_result_and_progress_matrix_is_exhaustive():
    for state in ("queued", "running", "succeeded", "partial", "failed", "cancelled"):
        if state in {"queued", "running"}:
            model = schemas.MutationJobStatusResponse(
                job_id=JOB_UUID, state=state, completed_sample_types=0, total_sample_types=1,
                processed_samples=0, total_samples=1, result=None,
            )
            assert model.result is None
            with pytest.raises(ValidationError):
                schemas.MutationJobStatusResponse(
                    job_id=JOB_UUID, state=state, completed_sample_types=0, total_sample_types=1,
                    processed_samples=0, total_samples=1, result=_completed_for_state("succeeded"),
                )
        else:
            result = _completed_for_state(state)
            model = schemas.MutationJobStatusResponse(
                job_id=JOB_UUID, state=state, completed_sample_types=1, total_sample_types=1,
                processed_samples=1, total_samples=1, result=result,
            )
            assert model.state == state
            with pytest.raises(ValidationError):
                schemas.MutationJobStatusResponse(
                    job_id=JOB_UUID, state=state, completed_sample_types=1, total_sample_types=1,
                    processed_samples=1, total_samples=1, result=None,
                )


def test_response_discriminators_reject_ambiguous_and_drifted_payloads():
    valid_outcomes = [_outcome("succeeded", counts=schemas.MutationCounts(requested=1, resolved=1, created=1))]
    with pytest.raises(ValidationError):
        schemas.MutationPreviewResponse.model_validate({
            "mode": "synchronous", "predicted_mode": "synchronous", "overall_status": "succeeded",
            "threshold": 1, "counts": _aggregate(valid_outcomes).model_dump(),
            "outcomes": [outcome.model_dump() for outcome in valid_outcomes],
        })
    with pytest.raises(ValidationError):
        schemas.MutationAcceptedResponse.model_validate({
            "mode": "synchronous", "job_id": JOB_ID,
            "status_url": "/nextseek_api/attributes/jobs/1/", "counts": schemas.MutationCounts().model_dump(),
        })
    with pytest.raises(ValidationError):
        schemas.MutationCompletedResponse.model_validate({
            "mode": "dry_run", "overall_status": "succeeded", "http_status": 200,
            "counts": _aggregate(valid_outcomes).model_dump(),
            "outcomes": [outcome.model_dump() for outcome in valid_outcomes],
        })


# --- Coverage completion: exercise defensive/edge branches directly ---


def test_strict_json_value_rejects_non_json_python_values():
    with pytest.raises(ValueError):
        schemas._strict_json_value(object())
    with pytest.raises(ValueError):
        schemas._strict_json_value({1: "a"})
    with pytest.raises(ValueError):
        schemas._strict_json_value((1, 2))


def test_attribute_create_rejects_whitespace_only_title():
    with pytest.raises(ValidationError):
        schemas.AttributeCreate(title="   ", sample_attribute_type=1)


def test_attribute_patch_changes_rejects_whitespace_only_title():
    with pytest.raises(ValidationError):
        schemas.AttributePatchChanges(title="   ")


def test_attribute_patch_changes_validates_non_null_reference_identifiers():
    changes = schemas.AttributePatchChanges(sample_attribute_type=2, unit="grams")
    assert changes.sample_attribute_type == 2
    assert changes.unit == "grams"


def test_patch_target_rejects_conflicting_duplicate_operations():
    with pytest.raises(ValidationError, match="conflicting duplicate patch"):
        schemas.PATCH_REQUEST_ADAPTER.validate_python({
            "targets": [{"attributes": [
                {"attribute": 7, "changes": {"description": "a"}},
                {"attribute": 7, "changes": {"description": "b"}},
            ]}],
        })


def test_delete_target_validates_explicit_sample_type_identifier():
    result = schemas.DELETE_REQUEST_ADAPTER.validate_python(
        {"targets": [{"sample_type": "Serum", "attributes": [7]}]}
    )
    assert result.targets[0].sample_type == "Serum"


def test_completed_error_class_rejects_unclassified_code_directly():
    with pytest.raises(ValueError, match="unclassified completed-response error code"):
        schemas._completed_error_class("totally_unknown_code")


def test_sample_type_outcome_rejects_errors_on_successful_or_unchanged_status():
    with pytest.raises(ValidationError):
        schemas.SampleTypeMutationOutcome(
            sample_type_id=1, sample_type_title="Serum", status="succeeded",
            counts=schemas.MutationCounts(), errors=[_error("attribute_not_found")],
        )
    with pytest.raises(ValidationError):
        schemas.SampleTypeMutationOutcome(
            sample_type_id=1, sample_type_title="Serum", status="unchanged",
            counts=schemas.MutationCounts(), errors=[_error("attribute_not_found")],
        )


def test_sample_type_outcome_rejects_failed_status_without_errors():
    with pytest.raises(ValidationError):
        schemas.SampleTypeMutationOutcome(
            sample_type_id=1, sample_type_title="Serum", status="failed",
            counts=schemas.MutationCounts(), errors=[],
        )


def test_sample_type_outcome_rejects_cancelled_or_skipped_with_committed_work():
    worked_counts = schemas.MutationCounts(requested=1, resolved=1, created=1)
    with pytest.raises(ValidationError):
        schemas.SampleTypeMutationOutcome(
            sample_type_id=1, sample_type_title="Serum", status="cancelled", counts=worked_counts,
        )
    with pytest.raises(ValidationError):
        schemas.SampleTypeMutationOutcome(
            sample_type_id=1, sample_type_title="Serum", status="skipped", counts=worked_counts,
        )


def test_valid_completed_status_http_defensive_branches(monkeypatch):
    # These branches are unreachable through real pydantic-validated models
    # (SampleTypeMutationOutcome.status is a closed Literal enum and
    # NO_COMMIT_ERROR_CLASS only ever yields "conflict"/"semantic"), but they
    # guard `valid_completed_status_http` against a future third error class
    # or status value. Exercise the pure function directly with duck-typed
    # fakes to prove the defensive `return False` fires correctly.
    monkeypatch.setitem(schemas.NO_COMMIT_ERROR_CLASS, "__test_unclassified_class__", "unexpected")
    fake_outcome_unexpected_class = SimpleNamespace(
        status="failed", errors=[SimpleNamespace(code="__test_unclassified_class__")],
    )
    assert schemas.valid_completed_status_http("failed", 409, [fake_outcome_unexpected_class]) is False

    fake_outcome_unknown_status = SimpleNamespace(status="__totally_unknown_status__", errors=[])
    assert schemas.valid_completed_status_http("failed", 409, [fake_outcome_unknown_status]) is False


def test_accepted_response_rejects_nonzero_updated_samples_with_valid_job_id():
    invalid_counts = schemas.MutationCounts(requested=1, resolved=1, patched=1, affected_samples=1, updated_samples=1)
    with pytest.raises(ValidationError):
        schemas.MutationAcceptedResponse(
            mode="asynchronous", job_id=JOB_UUID,
            status_url="/nextseek_api/attributes/jobs/1/", counts=invalid_counts,
        )


def test_job_status_rejects_queued_with_nonzero_progress():
    with pytest.raises(ValidationError):
        schemas.MutationJobStatusResponse(
            job_id=JOB_UUID, state="queued", completed_sample_types=1, total_sample_types=3,
            processed_samples=0, total_samples=100, result=None,
        )
    with pytest.raises(ValidationError):
        schemas.MutationJobStatusResponse(
            job_id=JOB_UUID, state="queued", completed_sample_types=0, total_sample_types=3,
            processed_samples=5, total_samples=100, result=None,
        )
