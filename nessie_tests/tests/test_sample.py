"""Tests for corpus.sample (per-family fractional sampling)."""
from nessie_tests import corpus
from nessie_tests.pathsetup import ensure_e2e_importable

ensure_e2e_importable()
from e2e.catalog import Turn, Variant  # noqa: E402


def _v(family, id):
    return Variant(family=family, id=id, name=id, tags=[], turns=[Turn(label="m", query="q")])


def test_sample_ratio_1_returns_all():
    vs = [_v("a", f"a{i}") for i in range(10)]
    assert len(corpus.sample(vs, 1.0)) == 10


def test_sample_per_family_keeps_at_least_one():
    vs = [_v("a", f"a{i}") for i in range(10)] + [_v("b", "b0")]
    out = corpus.sample(vs, 0.1, seed=0)
    assert {v.family for v in out} == {"a", "b"}          # no family dropped
    assert sum(1 for v in out if v.family == "a") == 1     # round(10*0.1) = 1
    assert sum(1 for v in out if v.family == "b") == 1     # max(1, round(1*0.1)) = 1


def test_sample_is_deterministic_for_seed():
    vs = [_v("a", f"a{i}") for i in range(20)]
    first = [v.id for v in corpus.sample(vs, 0.25, seed=7)]
    assert first == [v.id for v in corpus.sample(vs, 0.25, seed=7)]
    assert len(first) == 5                                 # round(20*0.25)
