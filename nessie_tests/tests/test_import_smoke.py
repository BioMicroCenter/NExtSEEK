from pathlib import Path
from nessie_tests.pathsetup import ensure_e2e_importable


def test_e2e_dsl_and_catalog_load():
    ensure_e2e_importable()
    from e2e.catalog import load_catalog, Catalog, Variant, Turn, PassCriterion  # noqa
    cat_path = Path(__file__).resolve().parents[2] / "chat_nextseek" / "e2e" / "catalog.json"
    cat = load_catalog(cat_path)
    assert isinstance(cat, Catalog)
    assert len(cat.families) == 11
    total = sum(len(f.variants) for f in cat.families.values())
    assert total >= 300
