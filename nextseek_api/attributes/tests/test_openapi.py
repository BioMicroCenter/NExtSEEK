import json
from pathlib import Path

from nextseek_api.attributes import openapi, schemas

SCHEMA_CONTRACT = Path("/home/taishajo/work/state/attribute-viewset/verification/SCHEMA-CONTRACT.json")


def schema_allows_null(node):
    if node.get("type") == "null":
        return True
    return any(schema_allows_null(item) for item in node.get("anyOf", []) + node.get("oneOf", []))


def generated_type(node):
    if "$ref" in node:
        return node["$ref"].rsplit("/", 1)[-1]
    if "const" in node:
        return "const"
    if "enum" in node:
        values = node["enum"]
        return "enum_integer" if values and all(isinstance(value, int) and not isinstance(value, bool) for value in values) else "enum"
    variants = [item for item in node.get("anyOf", []) if item.get("type") != "null"]
    if variants:
        kinds = {generated_type(item) for item in variants}
        if kinds == {"integer", "string"}:
            return "Identifier"
        if len(kinds) == 1:
            return kinds.pop()
    if node.get("type") == "array":
        return f"array[{generated_type(node['items'])}]"
    if node.get("format"):
        return f"{node.get('type')}:{node['format']}"
    if node == {}:
        return "json_value"
    return node.get("type")


def test_machine_schema_contract_matches_generated_components():
    contract = json.loads(SCHEMA_CONTRACT.read_text())
    assert contract["status"] == "PENDING_PHASE_4_INDEPENDENT_REVIEW"
    for component_name, expected in contract["components"].items():
        model = getattr(schemas, component_name)
        generated = model.model_json_schema()
        assert generated.get("additionalProperties") is False, component_name
        assert set(generated["properties"]) == set(expected["properties"]), component_name
        required = set(generated.get("required", []))
        for property_name, property_contract in expected["properties"].items():
            node = generated["properties"][property_name]
            assert (property_name in required) is property_contract["required"], (component_name, property_name, "required")
            assert schema_allows_null(node) is property_contract["nullable"], (component_name, property_name, "nullable")
            assert generated_type(node) == property_contract["type"], (component_name, property_name, generated_type(node), property_contract["type"])
            description = node.get("description", "")
            assert len(description.split()) >= 4, (component_name, property_name, description)
            constraints = property_contract.get("constraints", {})
            key_map = {"minimum": "minimum", "maximum": "maximum", "min_length": "minItems" if node.get("type") == "array" else "minLength"}
            for contract_key, expected_value in constraints.items():
                if contract_key in key_map:
                    assert node.get(key_map[contract_key]) == expected_value, (component_name, property_name, contract_key)
    assert [item["value"] for item in openapi.ATTRIBUTE_EXAMPLES] == [item["value"] for item in contract["examples"]]


def test_component_registry_contains_every_public_envelope():
    expected = {
        "SearchRequest", "BatchCreateRequest", "BatchPatchRequest", "BatchDeleteRequest",
        "AttributeRecord", "AttributeListResponse", "MutationPreviewResponse",
        "MutationCompletedResponse", "MutationAcceptedResponse",
        "MutationJobStatusResponse", "AttributeErrorResponse",
    }
    assert set(openapi.ATTRIBUTE_COMPONENTS) == expected
    for name, model in openapi.ATTRIBUTE_COMPONENTS.items():
        assert model.__name__ == name


def test_every_named_example_validates_against_its_component():
    assert openapi.ATTRIBUTE_EXAMPLES
    for example in openapi.ATTRIBUTE_EXAMPLES:
        model = openapi.ATTRIBUTE_COMPONENTS[example["component"]]
        validated = model.model_validate(example["value"])
        assert validated is not None
        assert len(example["description"].split()) >= 6


def test_examples_make_identifier_and_identity_semantics_explicit():
    text = " ".join(example["description"] for example in openapi.ATTRIBUTE_EXAMPLES).lower()
    assert "numeric string" in text
    assert "exact title" in text
    assert "owning sample type" in text
    assert "value type" in text
    assert "explicit null" in text
    assert "nested target" in text


def test_identity_fields_have_precise_descriptions():
    properties = schemas.AttributeRecord.model_json_schema()["properties"]
    assert "attribute definition" in properties["id"]["description"].lower()
    assert "owning sample type" in properties["sample_type_id"]["description"].lower()
    assert "value type" in properties["sample_attribute_type_id"]["description"].lower()


def test_generic_or_swapped_identity_descriptions_fail_contract():
    properties = schemas.AttributeRecord.model_json_schema()["properties"]
    assert properties["id"]["description"] != properties["sample_type_id"]["description"]
    assert properties["sample_type_id"]["description"] != properties["sample_attribute_type_id"]["description"]


def test_response_discriminator_drift_fails_contract():
    assert schemas.MutationPreviewResponse.model_json_schema()["properties"]["mode"]["const"] == "dry_run"
    assert set(schemas.MutationCompletedResponse.model_json_schema()["properties"]["mode"]["enum"]) == {"synchronous", "asynchronous"}


def test_unapproved_response_property_fails_contract():
    assert "internal_state" not in schemas.AttributeRecord.model_fields
    assert schemas.AttributeRecord.model_config["extra"] == "forbid"


def test_json_schema_forbids_extras_and_preserves_nullable_relationships():
    schema = schemas.BatchPatchRequest.model_json_schema()
    assert schema["additionalProperties"] is False
    changes = schema["$defs"]["AttributePatchChanges"]["properties"]
    for field in ("description", "unit", "sample_controlled_vocab", "linked_sample_type"):
        assert {item.get("type") for item in changes[field]["anyOf"]} >= {"null"}
    record = schemas.AttributeRecord.model_json_schema()
    assert record["additionalProperties"] is False
    assert record["properties"]["id"]["description"].startswith("Database primary key")
    assert "owning sample type" in record["properties"]["sample_type_id"]["description"].lower()
    assert "value type" in record["properties"]["sample_attribute_type_id"]["description"].lower()
