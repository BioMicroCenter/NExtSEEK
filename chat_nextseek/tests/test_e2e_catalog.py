"""Catalog loader tests."""
from pathlib import Path

import pytest

from e2e.catalog import Catalog, Family, Variant, Turn, PassCriterion, load_catalog


def test_pass_criterion_minimal():
    pc = PassCriterion(field="parser_plan.mode", op="eq", value="new_search")
    assert pc.field == "parser_plan.mode"
    assert pc.op == "eq"
    assert pc.value == "new_search"


def test_pass_criterion_no_value_for_true_op():
    pc = PassCriterion(field="api_ok", op="true")
    assert pc.value is None


def test_pass_criterion_rejects_unknown_op():
    with pytest.raises(ValueError):
        PassCriterion(field="x", op="bogus")


def test_load_catalog_round_trip(tmp_path: Path):
    payload = {
        "version": "1.0",
        "families": {
            "search_advanced": {
                "description": "test",
                "variants": [
                    {
                        "family": "search_advanced",
                        "id": "advanced.basic",
                        "name": "Basic",
                        "tags": ["smoke"],
                        "requires_env": [],
                        "turns": [
                            {"label": "main", "query": "test",
                             "pass_criteria": [{"field": "x", "op": "eq", "value": "y"}]}
                        ]
                    }
                ]
            }
        }
    }
    import json
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(payload))
    cat = load_catalog(p)
    assert isinstance(cat, Catalog)
    assert "search_advanced" in cat.families
    fam = cat.families["search_advanced"]
    assert isinstance(fam, Family)
    assert len(fam.variants) == 1
    v = fam.variants[0]
    assert isinstance(v, Variant)
    assert v.id == "advanced.basic"
    assert v.turns[0].pass_criteria[0].field == "x"


def test_load_real_catalog():
    cat = load_catalog(Path(__file__).parent.parent / "e2e" / "catalog.json")
    assert len(cat.families) == 9
    # Every family has >=6 variants
    for name, fam in cat.families.items():
        assert len(fam.variants) >= 6, f"{name}: {len(fam.variants)} variants (expected >=6)"
    # All variant IDs unique
    all_ids = [v.id for f in cat.families.values() for v in f.variants]
    assert len(all_ids) == len(set(all_ids)), "duplicate variant IDs"
