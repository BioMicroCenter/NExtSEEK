"""Sampler tests."""
import random

import pytest

from e2e.catalog import Catalog, Family, Variant, Turn
from e2e.sampler import env_satisfied, sample


def _make_variant(family: str, id_: str, requires_env: list[str] | None = None) -> Variant:
    return Variant(
        family=family, id=id_, name=id_,
        requires_env=requires_env or [],
        turns=[Turn(label="main", query="x", pass_criteria=[])],
    )


def _make_catalog(layout: dict[str, list[Variant]]) -> Catalog:
    return Catalog(families={
        name: Family(variants=variants) for name, variants in layout.items()
    })


def test_env_satisfied_no_requirement():
    v = _make_variant("f", "f.a")
    assert env_satisfied(v)


def test_env_satisfied_set(monkeypatch):
    monkeypatch.setenv("TOWER_ACCESS_TOKEN", "abc")
    v = _make_variant("f", "f.b", requires_env=["TOWER_ACCESS_TOKEN"])
    assert env_satisfied(v)


def test_env_satisfied_unset(monkeypatch):
    monkeypatch.delenv("TOWER_ACCESS_TOKEN", raising=False)
    v = _make_variant("f", "f.c", requires_env=["TOWER_ACCESS_TOKEN"])
    assert not env_satisfied(v)


def test_sample_floor_keeps_one_per_family():
    cat = _make_catalog({
        "search_advanced": [_make_variant("search_advanced", f"adv.{i}") for i in range(8)],
        "search_tree":     [_make_variant("search_tree",     f"tree.{i}") for i in range(8)],
    })
    rng = random.Random(42)
    selected = sample(cat, ratio=0.01, rng=rng)
    families_in = {v.family for v in selected}
    assert families_in == {"search_advanced", "search_tree"}, "floor should pick ≥1 per family"
    assert len(selected) == 2


def test_sample_ratio_full():
    cat = _make_catalog({
        "search_advanced": [_make_variant("search_advanced", f"adv.{i}") for i in range(8)],
    })
    rng = random.Random(42)
    selected = sample(cat, ratio=1.0, rng=rng)
    assert len(selected) == 8


def test_sample_skips_unsatisfied_env(monkeypatch):
    monkeypatch.delenv("TOWER_ACCESS_TOKEN", raising=False)
    variants = [_make_variant("p", "p.normal"), _make_variant("p", "p.tower", requires_env=["TOWER_ACCESS_TOKEN"])]
    cat = _make_catalog({"pipeline_nfcore": variants})
    rng = random.Random(42)
    # Even at ratio 1.0 the tower variant is skipped
    selected = sample(cat, ratio=1.0, rng=rng)
    ids = {v.id for v in selected}
    assert "p.normal" in ids
    assert "p.tower" not in ids


def test_sample_empty_family_skipped(monkeypatch):
    monkeypatch.delenv("TOWER_ACCESS_TOKEN", raising=False)
    cat = _make_catalog({
        "pipeline_nfcore": [_make_variant("pipeline_nfcore", "p.tower", requires_env=["TOWER_ACCESS_TOKEN"])],
        "search_advanced": [_make_variant("search_advanced", "adv.basic")],
    })
    rng = random.Random(42)
    selected = sample(cat, ratio=1.0, rng=rng)
    families_in = {v.family for v in selected}
    assert families_in == {"search_advanced"}


def test_sample_seed_determinism():
    cat = _make_catalog({
        "search_advanced": [_make_variant("search_advanced", f"adv.{i}") for i in range(8)],
    })
    s1 = sample(cat, ratio=0.5, rng=random.Random(99))
    s2 = sample(cat, ratio=0.5, rng=random.Random(99))
    assert [v.id for v in s1] == [v.id for v in s2]
