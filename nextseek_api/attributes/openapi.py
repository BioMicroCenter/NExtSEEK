from __future__ import annotations

from . import schemas

ATTRIBUTE_COMPONENTS = {
    model.__name__: model
    for model in (
        schemas.SearchRequest, schemas.BatchCreateRequest, schemas.BatchPatchRequest,
        schemas.BatchDeleteRequest, schemas.AttributeRecord, schemas.AttributeListResponse,
        schemas.MutationPreviewResponse, schemas.MutationCompletedResponse,
        schemas.MutationAcceptedResponse, schemas.MutationJobStatusResponse,
        schemas.AttributeErrorResponse,
    )
}

ATTRIBUTE_EXAMPLES = (
    {
        "component": "SearchRequest",
        "description": "A nested target uses a numeric string as an ID while another uses an exact title.",
        "value": {"targets": [{"sample_type": "12", "attributes": [7, "Concentration"]}]},
    },
    {
        "component": "BatchPatchRequest",
        "description": "A nested target preserves owning sample type association and explicit null clears its unit relationship.",
        "value": {"targets": [{"sample_type": "Serum", "attributes": [{"attribute": "Concentration", "changes": {"unit": None}}]}], "dry_run": True},
    },
    {
        "component": "AttributeRecord",
        "description": "Separate fields identify the attribute, its owning sample type, and its value type without follow-up lookup.",
        "value": {
            "id": 7, "title": "Concentration", "sample_type_id": 3,
            "sample_type_title": "Serum", "sample_attribute_type_id": 2,
            "sample_attribute_type_title": "Float", "required": False, "pos": 4,
            "is_title": False, "description": "Measured concentration.", "unit_id": None,
            "unit_title": None, "unit_symbol": None, "sample_controlled_vocab_id": None,
            "sample_controlled_vocab_title": None, "linked_sample_type_id": None,
            "linked_sample_type_title": None, "created_at": "2026-01-02T00:00:00Z",
            "updated_at": "2026-01-03T00:00:00Z",
        },
    },
)
