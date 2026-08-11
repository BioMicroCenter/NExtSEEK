"""`drop_field` must not strip an assertion the route guarantees is observable.

route_policy was written for `unsupported` / `writes_unsupported`, where the router
never reaches NS, so `parser_plan.mode` resolves to None and asserting it fails
unconditionally. Dropping it there is correct.

Extending the map to the NS families (the 2026-07-31 route mapping adds nine) made
that blanket drop actively harmful: measured over the merged corpus it would strip
`parser_plan.mode` from 256 variants where NS definitely runs and the field is real.
The corpus would gain a route assertion and lose a stronger one — a net weakening
that reads as a strengthening.

So the drop is now conditional on what the rule asserts:

  eq nextseek_query          NS is guaranteed  -> KEEP parser_plan.mode
  eq container_cc / unrelated  NS never runs   -> DROP
  matches_re with an alternation that includes a non-NS route
                             NS is not guaranteed -> DROP
"""
from pathlib import Path

import pytest

from nessie_tests import corpus

HERE = Path(__file__).resolve().parents[1]
CORPUS = HERE / "corpus.json"


def _variant(family, criteria):
    from e2e.catalog import Turn, Variant
    return Variant(family=family, id=f"{family}.x", name="n", tags=[], requires_env=[],
                   turns=[Turn(label="m", query="q", pass_criteria=criteria)])


MODE = {"field": "parser_plan.mode", "op": "eq", "value": "new_search"}


def _apply(rule, family="fam"):
    spec = {"families": {family: rule}, "drop_field": "parser_plan.mode",
            "also_assert": [{"field": "last_reply", "op": "nonempty", "value": None}]}
    out = corpus.apply_route_policy([_variant(family, [dict(MODE)])], spec)
    return {(c.field, c.op, c.value) for t in out[0].turns for c in t.pass_criteria}


def test_ns_pin_keeps_the_parser_mode():
    """NS is guaranteed to run, so parser_plan.mode is observable and must survive."""
    crits = _apply({"op": "eq", "value": "nextseek_query"})
    assert ("parser_plan.mode", "eq", "new_search") in crits
    assert ("route", "eq", "nextseek_query") in crits


def test_cc_pin_drops_the_parser_mode():
    """NS never runs, so the field resolves to None and fails unconditionally."""
    crits = _apply({"op": "eq", "value": "container_cc"})
    assert not any(f == "parser_plan.mode" for f, _, _ in crits)
    assert ("route", "eq", "container_cc") in crits


def test_unrelated_pin_drops_the_parser_mode():
    crits = _apply({"op": "eq", "value": "unrelated"})
    assert not any(f == "parser_plan.mode" for f, _, _ in crits)


def test_alternation_including_cc_drops_the_parser_mode():
    """`either` means NS is not guaranteed, so the field is conditionally unsatisfiable."""
    crits = _apply({"op": "matches_re", "value": "(nextseek_query|container_cc)"})
    assert not any(f == "parser_plan.mode" for f, _, _ in crits)
    assert ("route", "matches_re", "(nextseek_query|container_cc)") in crits


def test_alternation_of_only_ns_spellings_keeps_it():
    """An alternation that can only ever match NS still guarantees NS."""
    crits = _apply({"op": "matches_re", "value": "(nextseek_query)"})
    assert ("parser_plan.mode", "eq", "new_search") in crits


def test_the_real_corpus_keeps_parser_mode_on_ns_families():
    """The regression this guards: 256 variants must not silently lose the field."""
    merged = corpus.merged(CORPUS)
    policy = corpus.load_route_policy(CORPUS)
    ns_families = {f for f, r in (policy.get("families") or {}).items()
                   if r.get("op") == "eq" and r.get("value") == "nextseek_query"}
    if not ns_families:
        pytest.skip("no NS family pinned yet")
    kept = [v.id for v in merged if v.family in ns_families
            for t in v.turns for c in t.pass_criteria if c.field == "parser_plan.mode"]
    assert kept, (
        "every NS-pinned family lost parser_plan.mode — the blanket drop is back")
