"""An inline `route` criterion is the route that resolves, so an override must agree.

`apply_route_policy` (`corpus.py:190-193`) appends a route criterion to a variant's
FIRST turn only `if "route" not in present`. A variant that writes its own inline
route criterion therefore never sees the `route_policy.overrides` entry keyed on its
id: the inline value wins and the override is dead config that READS as if it were
live. Two variants carried exactly that shape, and both claimed `container_cc` in an
override while asserting `nextseek_query` inline.

They needed opposite fixes, which is the whole reason this class of bug is worth a
general test rather than two spot fixes:

* `green.global_count` ("How many samples are in the database?") is a plain global
  count and its own reply guard is `50,88[0-9]`, a number only the NS REST path
  produces. Its inline `nextseek_query` is right and the override was wrong, so the
  OVERRIDE was deleted.
* `green.refine_recall` really does route `container_cc`, the operator ruled the
  router right, and the case was rewritten around the route it takes. Its override
  stays, now agreeing with the inline criterion instead of contradicting it.

Flipping the route alone would not have been progress on the second one: with
`container_cc` observed, the `api_ok` / `api_plan.*` / `api_result_meta.*` criteria
it also carried are unresolvable by construction, so the case would have gone from
failing on one criterion to failing on four. Hence the rewrite.
"""
import json
import pathlib
import re

import pytest

from nessie_tests import corpus, evaluate, route_observer

OVERLAY = pathlib.Path(__file__).resolve().parents[1] / "overlay.json"

REFINE_RECALL = "green.refine_recall"
GLOBAL_COUNT = "green.global_count"


def _merged():
    return {v.id: v for v in corpus.merged(OVERLAY)}


def _source(vid):
    """The variant's own TEXT, before any policy or floor is applied.

    Mirrors `corpus.merged`: an overlay variant whose id matches a base one
    REPLACES it wholesale, so the overlay entry is the case's own text wherever
    one exists, never the union of the two.
    """
    overlay = {v.id: v for v in corpus.load_overlay(OVERLAY)}
    base = {v.id: v for v in corpus.load_base()}
    return overlay.get(vid) or base.get(vid)


def _inline_route(variant):
    """The route criteria the case writes for itself on its FIRST turn.

    First turn only, because that is the only turn `apply_route_policy` inspects
    or appends to.
    """
    if not variant or not variant.turns:
        return []
    return [c for c in variant.turns[0].pass_criteria if c.field == "route"]


def _overrides():
    return (corpus.load_route_policy(OVERLAY) or {}).get("overrides") or {}


def _families():
    return (corpus.load_route_policy(OVERLAY) or {}).get("families") or {}


def _ids_with_inline_route():
    return sorted(vid for vid in _merged() if _inline_route(_source(vid)))


def _crits(variant, turn_index):
    return [(c.field, c.op, c.value) for c in variant.turns[turn_index].pass_criteria]


def _fields(variant, turn_index):
    return {c.field for c in variant.turns[turn_index].pass_criteria}


# --------------------------------------------------------------------------- #
# The general trap: an override that can never fire.
# --------------------------------------------------------------------------- #

def test_the_corpus_actually_contains_variants_with_an_inline_route():
    """Guards the two tests below against passing vacuously on an empty set."""
    assert len(_ids_with_inline_route()) >= 10


def test_an_inline_route_criterion_is_the_one_that_resolves():
    """The invariant the override trap follows from, pinned rather than assumed.

    If `apply_route_policy` ever stops honouring an inline criterion, or starts
    appending a SECOND route criterion next to it, every case that writes its own
    route silently begins asserting something else.
    """
    merged = _merged()
    for vid in _ids_with_inline_route():
        inline = _inline_route(_source(vid))
        assert len(inline) == 1, f"{vid} writes {len(inline)} inline route criteria"
        resolved = [c for c in merged[vid].turns[0].pass_criteria if c.field == "route"]
        assert len(resolved) == 1, (
            f"{vid} resolves to {len(resolved)} route criteria; policy double-wrote one")
        assert (resolved[0].op, resolved[0].value) == (inline[0].op, inline[0].value), (
            f"{vid} resolves to {(resolved[0].op, resolved[0].value)} but writes "
            f"{(inline[0].op, inline[0].value)} inline")


def test_no_route_policy_override_contradicts_an_inline_route_criterion():
    """An override on a variant that writes its own route is dead config.

    It never fires, so it cannot be wrong in a way a run would reveal, and anyone
    reading `route_policy.overrides` to find out what the corpus expects is misled.
    Either the override matches the inline criterion, or one of the two is wrong and
    has to be settled.
    """
    overrides = _overrides()
    conflicts = []
    for vid in _ids_with_inline_route():
        rule = overrides.get(vid)
        if not rule:
            continue
        inline = _inline_route(_source(vid))[0]
        if (rule["op"], rule["value"]) != (inline.op, inline.value):
            conflicts.append(
                f"{vid}: override says {(rule['op'], rule['value'])}, "
                f"inline says {(inline.op, inline.value)}")
    assert conflicts == [], (
        "inert route_policy.overrides that contradict the criterion that actually "
        "resolves: " + "; ".join(conflicts))


def test_no_family_route_rule_forbids_the_route_its_member_asserts_inline():
    """The same trap one level up, where the honest answer is usually "narrower".

    A family rule is also suppressed by an inline criterion, but unlike an override
    it is written for a whole family, so a member pinning ONE branch of the family's
    alternation is a legitimate narrowing rather than a contradiction
    (`pipeline.create_an_nf_core_samplesheet` pins `nextseek_query` under a
    `(nextseek_query|container_cc)` family rule). What is never legitimate is a
    family rule that would REJECT the value its own member asserts.
    """
    families = _families()
    conflicts = []
    for vid in _ids_with_inline_route():
        variant = _merged()[vid]
        rule = families.get(variant.family)
        if not rule or _overrides().get(vid):
            continue
        inline = _inline_route(_source(vid))[0]
        if inline.op != "eq":
            continue
        pattern = re.escape(rule["value"]) if rule["op"] == "eq" else rule["value"]
        if not re.search(pattern, str(inline.value)):
            conflicts.append(f"{vid} ({variant.family}): family rule "
                             f"{(rule['op'], rule['value'])} rejects inline {inline.value!r}")
    assert conflicts == [], "; ".join(conflicts)


def test_the_global_count_override_is_gone_rather_than_the_case_being_changed():
    """`50,88[0-9]` is the NS REST global count. The inline criterion was right."""
    assert GLOBAL_COUNT not in _overrides()
    assert ("route", "eq", "nextseek_query") in _crits(_merged()[GLOBAL_COUNT], 0)


def test_the_refine_recall_override_is_kept_and_now_agrees():
    """Kept deliberately: it is consistent rather than contradictory, and deleting it
    would remove the one place the CC ruling for this case is written next to the
    other per-case route decisions."""
    assert _overrides().get(REFINE_RECALL) == {"op": "eq", "value": "container_cc"}


# --------------------------------------------------------------------------- #
# `green.refine_recall` rewritten for the route it actually takes.
#
# Ground truth, from the stored 2026-08-03 seed-6 run (turns.json id 997) and from
# the database:
#
#   Cohort = '4 week'   ->   2 samples, NHP-220524FLY-1-PUB and NHP-220524FLY-2-PUB
#   Cohort = '4wk'      -> 237 samples
#
# The turn routed container_cc citing `ambiguous_study_resolution`, cost $0.378, and
# produced the best answer in the whole run: 15 keyword hits, 2 separated out as the
# genuine `Cohort` match and 13 disclosed as substring artifacts on "14 weeks".
#
# `4wk` is DELIBERATELY not asserted. The known-correct reply never mentions that
# spelling: its false positives are "14 week" substring hits, not `4wk` cohort
# members. A `4wk` guard would fail against the answer the operator called right,
# which is precisely the false red this whole exercise exists to remove.
# --------------------------------------------------------------------------- #

# The naive failure mode this case exists to catch: a flat count with no reading of
# what the keyword actually matched.
NAIVE_REPLY = 'The keyword search for "4 week" returned 15 samples.'

# Right about the artifacts, wrong about which samples are genuine.
NO_UIDS_REPLY = (
    "15 samples matched. Most are substring artifacts on \"14 weeks\", "
    "so the real total is smaller.")


def _seed_guards():
    v = _merged()[REFINE_RECALL]
    return [c.value for c in v.turns[0].pass_criteria
            if c.field == "last_reply" and c.op == "matches_re"]


def _passes_seed_guards(reply):
    # matches_re is `re.search(..., flags=re.IGNORECASE)` in e2e/criteria.py.
    return all(re.search(g, reply, re.IGNORECASE) for g in _seed_guards())


def test_the_seed_turn_asserts_the_route_it_actually_takes():
    assert ("route", "eq", "container_cc") in _crits(_merged()[REFINE_RECALL], 0)


def test_the_seed_turn_no_longer_asserts_ns_rest_internals():
    """`api_ok`, `api_plan.*` and `api_result_meta.*` are unresolvable on a CC turn.

    Task 5's CC skip covers only the four DERIVED NS outcome fields; an inline
    `api_ok` on a CC-routed case still fails by design, and correctly so. So these
    have to leave the case's own text, not be excused by the harness.
    """
    offenders = sorted(f for f in _fields(_merged()[REFINE_RECALL], 0)
                       if f == "api_ok" or f.startswith(("api_plan.", "api_result_meta.")))
    assert offenders == [], f"seed turn still asserts NS REST fields: {offenders}"


def test_the_seed_turn_pins_the_two_genuine_uids():
    assert any("NHP-220524FLY-" in g for g in _seed_guards())
    assert _passes_seed_guards(
        "Two samples have Cohort \"4 week\": NHP-220524FLY-1-PUB and "
        "NHP-220524FLY-2-PUB. The other 13 hits are substring artifacts on 14 weeks.")
    assert not _passes_seed_guards(NO_UIDS_REPLY), (
        "a reply that never names the genuine samples must not pass")


def test_the_seed_turn_requires_the_cohort_field_the_truth_rests_on():
    """`Cohort` is HOW the 2 are established. A keyword-count answer never says it."""
    assert any(re.search(g, "Cohort", re.IGNORECASE) for g in _seed_guards())
    assert not _passes_seed_guards(NAIVE_REPLY)


def test_the_seed_turn_requires_the_false_positives_to_be_disclosed():
    """The real failure mode is answering "15 samples" flat.

    13 of the 15 keyword hits are "14 weeks" substrings. A reply that reports the
    raw count without saying so is wrong even though its number is what the search
    returned, and no route or plumbing criterion can tell the two apart.
    """
    disclosure = [g for g in _seed_guards()
                  if re.search(g, "substring", re.IGNORECASE)
                  or re.search(g, "false positive", re.IGNORECASE)
                  or re.search(g, "artifact", re.IGNORECASE)]
    assert disclosure, "nothing in the seed guards demands the artifacts be disclosed"
    assert not _passes_seed_guards(
        "NHP-220524FLY-1-PUB and NHP-220524FLY-2-PUB have Cohort 4 week. "
        "15 samples matched in total."), (
        "a reply that names the UIDs but hides the 13 bad hits must not pass")


def test_the_seed_turn_does_not_assert_the_4wk_spelling():
    """237 samples carry `Cohort='4wk'`, and the known-CORRECT reply never mentions
    them. Asserting that spelling would fail the one answer we have evidence is
    right, which is the exact false-red class this rewrite removes."""
    v = _merged()[REFINE_RECALL]
    values = [str(c.value) for t in v.turns for c in t.pass_criteria if c.value is not None]
    assert not any("4wk" in val.lower() for val in values), (
        f"the case asserts the 4wk spelling: {values}")


def test_the_refine_turn_asserts_a_reply_rather_than_a_rest_call():
    v = _merged()[REFINE_RECALL]
    fields = _fields(v, 1)
    assert "api_ok" not in fields, "api_ok cannot resolve on a follow-up to a CC turn"
    assert fields == {"last_reply"}, f"refine turn asserts {sorted(fields)}"
    ops = {c.op for c in v.turns[1].pass_criteria}
    assert ops == {"nonempty", "matches_re"}, (
        "the refine turn needs both a liveness check and one substantive guard")


def test_the_refine_turn_pins_no_route_and_the_case_pins_no_route_source():
    """Plan 2 ships sticky routing, so a follow-up after a CC turn carries
    `route_source="sticky"`. Pinning turn 1's route, or any `route_source`, is
    exactly the assumption that would break, and the refine turn has no observed
    evidence to pin it with anyway."""
    v = _merged()[REFINE_RECALL]
    assert "route" not in _fields(v, 1)
    assert not any(c.field == "route_source" for t in v.turns for c in t.pass_criteria)


def test_the_refine_turn_guard_is_honest_about_having_no_observed_reply():
    """Turn 998 came back `All provider fallbacks exhausted` in the only run that
    reached it, so a correct refine reply has never been seen. The guard keeps the
    turn on topic and rejects an off-topic or dead one; it does not invent a shape.
    """
    v = _merged()[REFINE_RECALL]
    guard = next(c.value for c in v.turns[1].pass_criteria if c.op == "matches_re")
    for good in ("Just NHP-220524FLY-1-PUB and NHP-220524FLY-2-PUB.",
                 "Restricting to the 4 week cohort leaves 2 samples.",
                 "The 4-week ones are the two Flynn NHP samples."):
        assert re.search(guard, good, re.IGNORECASE), f"guard falsely rejected {good!r}"
    for bad in ("**The request could not be completed.** All provider fallbacks exhausted",
                "I do not have any prior results to filter."):
        assert not re.search(guard, bad, re.IGNORECASE), f"guard let {bad!r} through"


def test_the_case_still_asserts_something_evaluable_on_every_turn():
    """The rewrite REMOVES four criteria, so the guard that it did not weaken into
    vacuity is the deliverable. Measured through the harness's own skip rules with
    the route it takes, which is the worst case for this variant."""
    v = _merged()[REFINE_RECALL]
    for i, t in enumerate(v.turns):
        live = [c for c in t.pass_criteria
                if not evaluate.is_unobservable(c.field, c.op, route=evaluate.CC_ROUTE)]
        assert len(live) >= 2, f"turn {i} evaluates only {[c.field for c in live]}"


# --------------------------------------------------------------------------- #
# Replay: the new criteria against the reply that was ACTUALLY observed.
#
# Without this the rewrite would be criteria invented to match a description of an
# answer. With it, the seed turn's four criteria are evaluated through the real
# evaluator against the real stored reply, and the counterfactual below proves they
# are not merely satisfiable by anything.
# --------------------------------------------------------------------------- #

_EVIDENCE = pathlib.Path("/home/cdemu/nessie-run-seed6b")
_MANIFEST = _EVIDENCE / "manifest.json"
_TURNS = _EVIDENCE / "turns.json"

requires_seed6b = pytest.mark.skipif(
    not (_MANIFEST.exists() and _TURNS.exists()),
    reason="stored 2026-08-03 seed-6 run evidence is not on this host")


def _ascii_prefix(reply):
    """The leading ASCII run of a reply.

    The manifest trims a long reply with a literal `…[trimmed]` and turns.json
    stores the same replies with their em dash mangled to U+FFFD, so neither the
    full string nor a fixed-length slice compares equal across the two files.
    Stopping at the first non-ASCII character sidesteps both.
    """
    out = ""
    for ch in reply:
        if not ch.isascii():
            break
        out += ch
    return out.rstrip()


def _stored_turn(query):
    rows = [r for r in json.loads(_TURNS.read_text(encoding="utf-8")) if r.get("q") == query]
    assert len(rows) == 1, f"{len(rows)} stored turns match {query!r}"
    return rows[0]


def _replay(row, criteria):
    """Evaluate `criteria` against a stored turn through the real evaluator.

    The payload is shaped like a REAL container_cc turn: `cc_engine` emits
    {reply, mode, artifacts, cc_raw_files} on `query_complete` and no `debug` key at
    all, which is exactly why the four NS outcome fields are unresolvable there. The
    RouteObservation is derived by `route_observer.observe` from the stored route
    and source rather than asserted, so the `route` criterion is checked against
    evidence and not against a fixture.
    """
    payload = {"status": "completed", "progress": [
        {"event": "route_decided",
         "data": {"route": row["route"], "source": row["src"],
                  "reasoning": row.get("why") or ""}},
        {"event": "query_complete",
         "data": {"reply": row["reply"], "mode": row.get("mode"), "artifacts": []}},
    ]}
    obs = route_observer.observe(payload)
    return evaluate.evaluate_turn(payload, criteria, obs, last_reply=row["reply"])


@requires_seed6b
def test_the_stored_seed_turn_is_the_evidence_this_rewrite_rests_on():
    entry = next(e for e in json.loads(_MANIFEST.read_text(encoding="utf-8"))["entries"]
                 if e["id"] == REFINE_RECALL)
    seed_reds = {f.split(":", 1)[-1] for f in entry["failed_criteria"] if f.startswith("seed:")}
    assert seed_reds == {"route", "api_ok", "api_plan.requestBody.filter_searchText",
                         "api_result_meta.row_count"}, (
        "the seed turn failed on the route AND on three REST fields, which is why "
        "flipping the route alone was never going to make this case green")

    row = _stored_turn(_merged()[REFINE_RECALL].turns[0].query)
    assert row["route"] == "container_cc" and row["src"] == "baml"
    # The manifest and turns.json agree about which reply this was.
    recorded = next(o["observed"] for o in entry["observations"] if o["field"] == "last_reply")
    prefix = _ascii_prefix(recorded)
    assert len(prefix) >= 64 and row["reply"].startswith(prefix)


@requires_seed6b
def test_the_new_seed_criteria_all_pass_on_the_reply_that_was_observed():
    variant = _merged()[REFINE_RECALL]
    criteria = variant.turns[0].pass_criteria
    passed, results, _observed = _replay(_stored_turn(variant.turns[0].query), criteria)

    assert len(results) == len(criteria), "a criterion went unevaluated"
    assert not any(r.get("skipped") for r in results), (
        "a skipped criterion is not evidence either way: "
        f"{[r['field'] for r in results if r.get('skipped')]}")
    assert evaluate.any_criterion_evaluated(results)
    assert passed, {r["field"]: r["reason"] for r in results if not r["passed"]}


@requires_seed6b
def test_the_same_criteria_still_reject_the_naive_answer_on_that_evidence():
    """The counterfactual, run through the same path as the green above.

    Same route, same everything, only the reply replaced with the flat count. If
    this passed, the rewrite would have swapped four unresolvable criteria for four
    that assert nothing.
    """
    variant = _merged()[REFINE_RECALL]
    row = dict(_stored_turn(variant.turns[0].query))
    row["reply"] = NAIVE_REPLY
    passed, results, _observed = _replay(row, variant.turns[0].pass_criteria)
    assert not passed
    reds = {r["field"] for r in results if not r["passed"]}
    assert reds == {"last_reply"}, f"expected only the reply guards to fail, got {reds}"


@requires_seed6b
def test_the_refine_turn_had_no_correct_reply_to_learn_from():
    """Recorded, not worked around. The refine expectation is the one part of this
    case with no observed evidence behind it, and that has to be visible in the
    suite rather than only in a comment."""
    row = _stored_turn(_merged()[REFINE_RECALL].turns[1].query)
    assert evaluate.is_provider_outage(row["reply"]), (
        "if this turn ever produced a real reply, the refine guard should be "
        "tightened against it instead of staying an alternation")
