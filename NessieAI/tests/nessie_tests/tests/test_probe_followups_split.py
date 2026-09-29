"""The follow-up split probe and the follow-up A/B case list load and say what they claim.

* probes/probe-followups-split.json proves the split on a rebuilt box: an NExtSEEK-shaped
  follow-up stays on nextseek_query, a plot goes to container_cc, a self-contained
  question is routed as in a new chat, and a chat that used Container-CC stays there.
* probes/fu-compare.json is the paid A/B list (02-followup-loop FINDINGS section 7): the
  same cases run twice, NESSIE_FOLLOWUP_ROUTING=cc against =split, so it asserts answers
  and never a route.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from NessieAI.router.followup import followup_shape
from NessieAI.tests.nessie_tests import corpus
from NessieAI.tests.nessie_tests.scripts import pin_probe_truths as pin

PROBES = Path(__file__).resolve().parents[1] / "probes"
SPLIT = PROBES / "probe-followups-split.json"
AB = PROBES / "fu-compare.json"
BOTH = [SPLIT, AB]

NS, CC = "nextseek_query", "container_cc"
ROUTE_FIELDS = {"route", "route_source", "engine"}
GUARD = r"(?<![\w.,/-])"
NDMA_DEV = 207


def _spec(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _cases(path):
    return {v["id"]: v for v in pin.variants(_spec(path))}


def _crits(turn, field=None):
    return [c for c in turn["pass_criteria"] if field is None or c["field"] == field]


def _values(turn, field="last_reply"):
    """The patterns a turn asserts on one field (a `nonempty` criterion carries none)."""
    return [c["value"] for c in _crits(turn, field) if isinstance(c["value"], str)]


def _owned(measure):
    """Every number pattern a `_measure` block owns: one per number in mode 'each',
    one alternation otherwise (what pin_probe_truths rewrites)."""
    out = set()
    for m in measure.values():
        if m.get("mode") == "each":
            out |= {pin.number_pattern([n]) for n in m["locals"]}
        else:
            out.add(pin.number_pattern(m["locals"]))
    return out


# --------------------------------------------------------------------------- #
# Both files
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", BOTH, ids=lambda p: p.name)
def test_the_file_loads_as_a_case_file(path):
    include, variants = corpus.load_case_file(path)
    assert include == [] and variants
    assert all(v.turns for v in variants)
    assert all(t.query and t.pass_criteria for v in variants for t in v.turns)


@pytest.mark.parametrize("path", BOTH, ids=lambda p: p.name)
def test_ids_are_unique_and_new(path):
    ids = [v["id"] for v in pin.variants(_spec(path))]
    assert len(ids) == len(set(ids))
    assert not set(ids) & {v.id for v in corpus.load_all_definitions()}


@pytest.mark.parametrize("path", BOTH, ids=lambda p: p.name)
def test_no_id_is_username_like(path):
    """An id is `<prefix>.<words>`, and its first word is one the case's own name or
    questions use: a username tags a question it never appears in (the 2026-09-23
    USERS sets were keyed `users2.<person>_...`)."""
    prefix = "split" if path == SPLIT else "ab"
    for v in pin.variants(_spec(path)):
        vid = v["id"]
        assert re.fullmatch(rf"{prefix}\.[a-z0-9]+(?:_[a-z0-9]+)*", vid), vid
        first = vid.split(".", 1)[1].split("_", 1)[0]
        text = re.sub(r"[^a-z0-9]", "", " ".join([v["name"]] + [t["query"] for t in v["turns"]]).lower())
        assert first in text, (vid, first)
        assert not vid.split(".", 1)[1].startswith("users"), vid


@pytest.mark.parametrize("path", BOTH, ids=lambda p: p.name)
def test_every_guarded_number_is_measured_and_repins(path):
    spec = _spec(path)
    measure = spec["_measure"]
    assert set(measure) <= set(_cases(path))
    same = {cid: m["locals"] for cid, m in measure.items()}
    _, log = pin.pin(copy.deepcopy(spec), same)
    assert len(log) == sum(len(m["locals"]) if m.get("mode") == "each" else 1
                           for m in measure.values())
    owned = _owned(measure)
    for v in pin.variants(spec):
        for turn in v["turns"]:
            for c in turn["pass_criteria"]:
                if isinstance(c["value"], str) and c["value"].startswith(GUARD):
                    assert c["value"] in owned, (v["id"], c["value"])


@pytest.mark.parametrize("path", BOTH, ids=lambda p: p.name)
def test_repinning_with_its_own_numbers_is_byte_identical(path):
    """The file is written the way pin_probe_truths writes it, so a re-pin on the dev box
    changes numbers and nothing else."""
    spec = _spec(path)
    same = {cid: m["locals"] for cid, m in spec["_measure"].items()}
    repinned, _ = pin.pin(copy.deepcopy(spec), same)
    assert json.dumps(repinned, indent=2, ensure_ascii=False) + "\n" == path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", BOTH, ids=lambda p: p.name)
def test_the_file_says_where_its_numbers_come_from(path):
    spec = _spec(path)
    assert "pin_probe_truths.py" in spec["_instance_warning"]
    for cid, m in spec["_measure"].items():
        assert m.get("cypher") and m.get("measured"), cid


# --------------------------------------------------------------------------- #
# The split probe
# --------------------------------------------------------------------------- #

SPLIT_ROUTES = {
    "split.nextseek_shaped_followups_stay": [NS, NS, NS],
    "split.plot_goes_to_container_cc": [NS, CC],
    "split.fresh_question_after_an_ns_follow_up": [NS, NS, NS],
    "split.sticky_after_container_cc": [NS, CC, NS, CC],
}


def test_the_split_probe_asserts_one_route_on_every_turn():
    cases = _cases(SPLIT)
    assert set(cases) == set(SPLIT_ROUTES)
    for vid, v in cases.items():
        routes = []
        for turn in v["turns"]:
            rules = _crits(turn, "route")
            assert len(rules) == 1 and rules[0]["op"] == "eq", (vid, turn["label"])
            routes.append(rules[0]["value"])
            assert _crits(turn, "route_source"), (vid, turn["label"])
            assert _crits(turn, "last_reply"), (vid, turn["label"])
        assert routes == SPLIT_ROUTES[vid], vid


def test_the_split_probe_matches_the_routers_code_guard():
    """An NS follow-up is one followup_shape calls NExtSEEK-shaped, and after a
    Container-CC turn only a self-contained question (no cue at all) may assert
    nextseek_query; the plot is Container-CC-shaped; the sticky turn is
    NExtSEEK-shaped and still asserts container_cc, which only the chat's earlier
    Container-CC turn can explain."""
    cases = _cases(SPLIT)
    for vid, v in cases.items():
        for i, turn in enumerate(v["turns"]):
            route = _crits(turn, "route")[0]["value"]
            shape = followup_shape(turn["query"])
            earlier_cc = any(_crits(t, "route")[0]["value"] == CC for t in v["turns"][:i])
            if route == NS and i:
                assert shape != "cc", (vid, i)
                assert shape is None or not earlier_cc, (vid, i)
            if route == CC:
                assert shape == "cc" or earlier_cc, (vid, i)
    sticky = cases["split.sticky_after_container_cc"]["turns"][3]
    assert followup_shape(sticky["query"]) == "ns"
    assert _crits(sticky, "route_source")[0]["value"] == "^(baml|followup|sticky)$"


def test_a_self_contained_question_is_asserted_as_a_router_decision():
    for vid in ("split.fresh_question_after_an_ns_follow_up", "split.sticky_after_container_cc"):
        fresh = _cases(SPLIT)[vid]["turns"][2]
        assert fresh["query"] == "How many HeLa samples do we have?"
        assert followup_shape(fresh["query"]) is None
        assert _crits(fresh, "route_source") == [
            {"field": "route_source", "op": "eq", "value": "baml"}]


# --------------------------------------------------------------------------- #
# The A/B list
# --------------------------------------------------------------------------- #

def test_the_ab_list_has_the_twenty_five_cases():
    assert len(_cases(AB)) == 25


def test_the_ab_list_asserts_answers_never_routes():
    """The two arms differ only by NESSIE_FOLLOWUP_ROUTING, so a route criterion would
    score the switch. Nor does it assert anything only one engine can produce: no
    Container-CC trace and no NExtSEEK plumbing (no stale seed `api_ok`)."""
    for vid, v in _cases(AB).items():
        for turn in v["turns"]:
            fields = {c["field"] for c in turn["pass_criteria"]}
            assert fields == {"last_reply"}, (vid, turn["label"], fields)


def test_refers_back_turn_four_names_hela_and_rejects_73():
    t4 = _cases(AB)["ab.refers_back_to_the_newest_result"]["turns"][3]
    values = _values(t4)
    assert r"(?i)\bHeLa\b" in values
    assert any(v.startswith(r"(?s)\A(?!") and "73" in v for v in values)
    assert pin.number_pattern([3]) in values


def test_cc_mice_rejects_1442_and_accepts_a_subset_of_the_745():
    t2 = _cases(AB)["ab.cc_mice_transcriptomic"]["turns"][1]
    values = _values(t2)
    assert pin.number_pattern([91, 105]) in values
    assert any(v.startswith(r"(?s)\A(?!") and "1,?442" in v for v in values)
    # the question keeps the premise the reviewer's premise check is for
    assert "1,206" in t2["query"]


def test_luad_leads_with_122_and_no_longer_forbids_433():
    t2 = _cases(AB)["ab.luad_smokers"]["turns"][1]
    values = _values(t2)
    lead = [v for v in values if v.startswith(r"\A")]
    assert lead and "122" in lead[0]
    assert re.search(lead[0], "**122 of the 585 TCGA-LUAD patients are current smokers.**")
    assert not re.search(lead[0], "| status | n |\n| Current Smoker | 122 |")
    assert not any("433" in v for v in values)


def test_sha_expects_962_and_rejects_the_invented_first_20_rows():
    turns = _cases(AB)["ab.sha_cap_and_limit"]["turns"]
    assert [t["label"] for t in turns] == ["t111", "t115", "t116", "t117", "t118"]
    for t in turns[2:4]:
        assert pin.number_pattern([962]) in _values(t)
    limit = [v for v in _values(turns[4]) if "first 20" in v]
    assert limit and not re.search(limit[0], "I showed you the first 20 rows as a preview.")
    assert re.search(limit[0], "Nothing was capped at 20.")


def test_ndma_seeds_are_keyed_for_the_dev_box():
    cases, measure = _cases(AB), _spec(AB)["_measure"]
    ndma = [vid for vid, v in cases.items() if "ndma" in v["turns"][0]["query"].lower()]
    assert len(ndma) == 5, ndma
    for vid in ndma:
        assert pin.number_pattern([NDMA_DEV]) in _values(cases[vid]["turns"][0]), vid
        assert NDMA_DEV in measure[vid]["locals"], vid


def test_the_three_new_cases_are_there():
    cases = _cases(AB)
    capped = cases["ab.capped_seed_liver"]
    assert "MIT_SRP" in capped["turns"][0]["query"] and "liver" in capped["turns"][1]["query"]
    chain = cases["ab.followup_of_a_followup"]
    assert len(chain["turns"]) == 3
    nopause = cases["ab.ns_chain_no_pause"]
    assert len(nopause["turns"]) == 4
    assert all(followup_shape(t["query"]) == "ns" for t in nopause["turns"][1:])
    assert all(any("no stored results" in v for v in _values(t)) for t in nopause["turns"][1:])
    assert "--pace 0" in _spec(AB)["_run"]
