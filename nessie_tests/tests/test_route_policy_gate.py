"""`also_assert` must not be added to a route_gate variant.

route_policy appends `last_reply nonempty` next to the route criterion, because a
route assertion alone cannot tell a working turn from a dead one — task 957 in the
2026-07-29 run errored with a null reply and scored GREEN on `route eq container_cc`
alone.

That reasoning does not hold for a route_gate case. runner.py drives every
route_gate variant at `case_tier = "route"` regardless of the run's tier, and
http_driver breaks the poll loop the moment `route_decided` arrives. The turn is
abandoned before any reply is observed BY DESIGN, so `last_reply` is always empty
and the criterion can never pass.

Observed on the dev box 2026-07-31: extending route_policy to nessie_route gave
route.ns_plain_study_membership a `last_reply nonempty` it could not satisfy, and
the case failed on `main:last_reply` while the route it asserted was correct. Three
gates were affected. That is the same unsatisfiable-criterion class the policy
block was written to remove, reintroduced by the policy block itself.
"""
from pathlib import Path

from nessie_tests import corpus

HERE = Path(__file__).resolve().parents[1]
CORPUS = HERE / "corpus.json"


def _variant(vid, family, tags):
    from e2e.catalog import Turn, Variant
    return Variant(family=family, id=vid, name="n", tags=tags, requires_env=[],
                   turns=[Turn(label="m", query="q", pass_criteria=[
                       {"field": "parser_plan.mode", "op": "eq", "value": "new_search"}])])


SPEC = {
    "families": {"fam": {"op": "eq", "value": "nextseek_query"}},
    "drop_field": "parser_plan.mode",
    "also_assert": [{"field": "last_reply", "op": "nonempty", "value": None}],
}


def _fields(v):
    return {c.field for t in v.turns for c in t.pass_criteria}


def test_a_route_gate_variant_gets_the_route_but_not_the_reply_guard():
    out = corpus.apply_route_policy(
        [_variant("gate.x", "fam", ["nessie", "route_gate"])], SPEC)
    fields = _fields(out[0])
    assert "route" in fields, "the gate still needs its route assertion"
    assert "last_reply" not in fields, (
        "route_gate cases are driven route-only, so the turn is abandoned before a "
        "reply exists and `last_reply nonempty` can never pass")


def test_a_normal_variant_still_gets_the_reply_guard():
    """The 2026-07-29 guard must survive for everything that actually runs a turn."""
    out = corpus.apply_route_policy([_variant("plain.x", "fam", ["nessie"])], SPEC)
    fields = _fields(out[0])
    assert "route" in fields
    assert "last_reply" in fields


def test_no_route_gate_case_in_the_real_corpus_asserts_last_reply():
    """The regression this guards, against the shipped corpus."""
    offenders = [v.id for v in corpus.merged(CORPUS)
                 if "route_gate" in v.tags
                 for t in v.turns for c in t.pass_criteria if c.field == "last_reply"]
    assert offenders == [], (
        f"route_gate cases carrying an unsatisfiable last_reply: {sorted(set(offenders))}")


def test_every_route_gate_case_still_asserts_a_route():
    for v in corpus.merged(CORPUS):
        if "route_gate" in v.tags:
            assert "route" in _fields(v), f"{v.id} lost its route assertion"
