"""helpers/suggestions: the suggestion ("chip") contract and its guardrails (SPEC 3.2, #128).

A chip click sends its ``query`` as the user's next message, so every query must be one the router keeps on NS: the
router's own follow-up cue check (``NessieAI.router.followup.followup_cue``) must not fire on it, or the click becomes
a Container-CC turn. That check is the default ``refers_back``; when it cannot be imported, every suggestion is
rejected. The rule tests pass a ``refers_back`` that never fires, so each one proves its own rule and nothing else.
"""
from __future__ import annotations

import builtins
import json
import sys

import pytest

from chat_nextseek.helpers import suggestions as sg
from NessieAI.router import followup

REVIEW = {"verdict": "suggest", "suggestion": {"kind": "narrow", "label": "Only Converter",
          "query": "Show samples for human subjects classified as Converter.", "reason": "57 of 98 were Non-converter."}}
GOOD = REVIEW["suggestion"]
QUERY = GOOD["query"]


def NO_CUE(_q):
    return None


# ---------------------------------------------------------------- the brief's tests --------------------------------
def test_a_reviewer_suggestion_becomes_one_chip():
    out = sg.suggestions_from_review(REVIEW, bundle_id=7)
    assert len(out) == 1 and out[0]["source"] == "reviewer" and out[0]["id"] == "b7-r0"


def test_a_query_that_refers_back_is_rejected():
    s = dict(REVIEW["suggestion"], query="Just the Converter ones.")
    assert followup.followup_cue(s["query"]) == "the-ones"       # the example does exercise the router's cue
    reason = sg.check_suggestion(s)
    assert reason is not None and "the-ones" in reason
    assert sg.check_suggestion(s, refers_back=NO_CUE) is None    # and nothing else rejects it


def test_write_verbs_and_cypher_are_rejected():
    for q in ("Delete the Non-converter samples.", "MATCH (s) RETURN s"):
        assert sg.check_suggestion(dict(REVIEW["suggestion"], query=q)) is not None
        assert sg.check_suggestion(dict(REVIEW["suggestion"], query=q), refers_back=NO_CUE) is not None


def test_ok_reviews_make_no_chips():
    assert sg.suggestions_from_review({"verdict": "ok"}, bundle_id=1) == []


def test_accept_matches_exact_text_once_and_expires():
    s = {}
    sg.pending_for(s, sg.suggestions_from_review(REVIEW, bundle_id=7), turn_id=3)
    assert sg.accept(s, "Show samples for human subjects classified as Converter.", last_turn_id=3)["id"] == "b7-r0"
    assert sg.accept(s, "Show samples for human subjects classified as Converter.", last_turn_id=3) is None


def test_a_turn_in_between_cancels_pending():
    s = {}
    sg.pending_for(s, sg.suggestions_from_review(REVIEW, bundle_id=7), turn_id=3)
    assert sg.accept(s, "Show samples for human subjects classified as Converter.", last_turn_id=4) is None


# ---------------------------------------------------------------- the default refers_back --------------------------
def test_the_default_refers_back_is_the_routers_followup_cue(monkeypatch):
    seen = []

    def spy(q):
        seen.append(q)
        return "spy-cue"

    monkeypatch.setattr(followup, "followup_cue", spy)
    reason = sg.check_suggestion(GOOD)
    assert seen == [QUERY]
    assert reason is not None and "spy-cue" in reason
    assert sg.suggestions_from_review(REVIEW, bundle_id=7) == []


def test_the_constants_are_the_spec_values():
    assert (sg.MAX_SUGGESTIONS, sg.MAX_LABEL, sg.MAX_QUERY) == (2, 60, 300)


def _block_router_import(monkeypatch):
    # None in sys.modules makes both `from NessieAI.router import followup` and
    # `from NessieAI.router.followup import followup_cue` raise ModuleNotFoundError (an ImportError)
    monkeypatch.setitem(sys.modules, "NessieAI.router", None)
    monkeypatch.setitem(sys.modules, "NessieAI.router.followup", None)


def test_without_the_router_every_suggestion_is_rejected(monkeypatch):
    _block_router_import(monkeypatch)
    with pytest.raises(ImportError):
        from NessieAI.router import followup as _f  # noqa: F401  (the block is real)
    assert sg.check_suggestion(GOOD) is not None
    assert sg.suggestions_from_review(REVIEW, bundle_id=7) == []
    # an explicit refers_back does not need the router
    assert sg.check_suggestion(GOOD, refers_back=NO_CUE) is None
    assert len(sg.suggestions_from_review(REVIEW, bundle_id=7, refers_back=NO_CUE)) == 1


def test_a_router_import_that_raises_anything_rejects_every_suggestion(monkeypatch):
    real_import = builtins.__import__

    def broken(name, *args, **kwargs):
        if name.startswith("NessieAI.router"):
            raise RuntimeError("router failed to load")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken)
    assert sg.check_suggestion(GOOD) is not None
    assert sg.suggestions_from_review(REVIEW, bundle_id=7) == []


def test_a_refers_back_check_that_raises_rejects_the_suggestion():
    def boom(_q):
        raise ValueError("bad pattern")

    assert sg.check_suggestion(GOOD, refers_back=boom) is not None
    assert sg.suggestions_from_review(REVIEW, bundle_id=7, refers_back=boom) == []


@pytest.mark.parametrize("q", [
    "Of those, how many are Converter?",
    "Show me those samples classified as Converter.",
    "Just the Converter ones.",
])
def test_a_follow_up_question_copied_into_a_chip_is_dropped(q):
    """A chip built from a follow-up question can carry its back-reference; the router's cue drops it."""
    assert followup.followup_cue(q) is not None
    assert sg.suggestions_from_review({"verdict": "suggest", "suggestion": dict(GOOD, query=q)}, bundle_id=2) == []


# ---------------------------------------------------------------- the rules, one at a time -------------------------
@pytest.mark.parametrize("label", ["", "   ", None, 7, "x" * 61])
def test_a_bad_label_is_rejected(label):
    assert sg.check_suggestion(dict(GOOD, label=label), refers_back=NO_CUE) is not None


@pytest.mark.parametrize("query", ["", "   ", None, 7, "Show samples " + "x" * 288])
def test_a_bad_query_is_rejected(query):
    assert sg.check_suggestion(dict(GOOD, query=query), refers_back=NO_CUE) is not None


def test_the_length_limits_are_inclusive():
    q = ("Show samples " + "x" * 300)[:300]
    assert len(q) == 300
    assert sg.check_suggestion(dict(GOOD, label="L" * 60, query=q), refers_back=NO_CUE) is None


def test_a_non_dict_is_rejected():
    for s in (None, "Only Converter", ["Only Converter"]):
        assert sg.check_suggestion(s, refers_back=NO_CUE) is not None


@pytest.mark.parametrize("verb", ["create", "add", "update", "edit", "delete", "remove", "rename", "upload",
                                  "register", "write"])
def test_every_write_verb_is_rejected_in_the_query_and_the_label(verb):
    q = f"{verb.capitalize()} the samples classified as Converter."
    reason = sg.check_suggestion(dict(GOOD, query=q), refers_back=NO_CUE)
    assert reason is not None and verb in reason.lower()
    assert sg.check_suggestion(dict(GOOD, query=q.upper()), refers_back=NO_CUE) is not None
    assert sg.check_suggestion(dict(GOOD, label=f"{verb} Converter"), refers_back=NO_CUE) is not None


@pytest.mark.parametrize("q", [
    "Show samples added in 2023.",
    "How many files were uploaded last year?",
    "Which subjects are registered as Converter?",
    "Show samples created by the lab, updated since May.",
    "List samples with an address field.",
])
def test_other_forms_of_a_write_word_pass(q):
    assert sg.check_suggestion(dict(GOOD, query=q), refers_back=NO_CUE) is None


@pytest.mark.parametrize("q", [
    "MATCH (s) RETURN s",
    "Show samples WHERE the species is Macaca.",
    "Show samples whose title CONTAINS lung.",
    "Show samples with uid $uid.",
    "Show samples -> assays.",
    "Show assays <- samples.",
])
def test_cypher_tokens_are_rejected(q):
    reason = sg.check_suggestion(dict(GOOD, query=q), refers_back=NO_CUE)
    assert reason is not None and "cypher" in reason.lower()


def test_cypher_tokens_are_rejected_in_the_label():
    assert sg.check_suggestion(dict(GOOD, label="Only s.Classification CONTAINS"), refers_back=NO_CUE) is not None


@pytest.mark.parametrize("q", [
    "Show samples where the donor is female.",
    "Which samples match lung tissue?",
    "Which samples contain liver tissue?",
    "What does the search return for Converter subjects?",
])
def test_plain_english_uses_of_the_cypher_words_pass(q):
    assert sg.check_suggestion(dict(GOOD, query=q), refers_back=NO_CUE) is None


# ---------------------------------------------------------------- suggestions_from_review ---------------------------
def test_a_chip_has_exactly_the_contract_fields():
    out = sg.suggestions_from_review(REVIEW, bundle_id=7, refers_back=NO_CUE)
    assert out == [{"id": "b7-r0", "source": "reviewer", "kind": "narrow", "label": "Only Converter",
                    "query": QUERY, "reason": "57 of 98 were Non-converter."}]


def test_expected_count_and_alt_are_carried_when_present():
    alt = {"cypher": "MATCH (s:T_HUMAN) WHERE s.Classification = $v RETURN count(s)", "parameters": {"v": "Converter"}}
    review = {"verdict": "suggest", "suggestion": dict(GOOD, expected_count=32, alt=alt)}
    (chip,) = sg.suggestions_from_review(review, bundle_id=7, refers_back=NO_CUE)
    assert chip["expected_count"] == 32 and chip["alt"] == alt


@pytest.mark.parametrize("bad", ["32", True, None, 3.5])
def test_an_expected_count_that_is_not_a_whole_number_is_dropped(bad):
    review = {"verdict": "suggest", "suggestion": dict(GOOD, expected_count=bad)}
    (chip,) = sg.suggestions_from_review(review, bundle_id=7, refers_back=NO_CUE)
    assert "expected_count" not in chip


@pytest.mark.parametrize("review", [
    None,
    "suggest",
    {},
    {"verdict": "ok", "suggestion": GOOD},
    {"verdict": "note", "suggestion": GOOD},            # breakage: the reply states it, no chip
    {"verdict": "suggest"},
    {"verdict": "suggest", "suggestion": None},          # zero, premise, unapplied value: a disclosure, no chip
    {"verdict": "suggest", "suggestion": {}},
])
def test_only_a_suggest_verdict_with_a_suggestion_makes_a_chip(review):
    assert sg.suggestions_from_review(review, bundle_id=7, refers_back=NO_CUE) == []


def test_a_rejected_suggestion_makes_no_chip():
    review = {"verdict": "suggest", "suggestion": dict(GOOD, query="Delete the Non-converter samples.")}
    assert sg.suggestions_from_review(review, bundle_id=7, refers_back=NO_CUE) == []


def test_at_most_two_chips_numbered_in_order_skipping_rejected_and_repeated_ones():
    review = {"verdict": "suggest", "suggestion": [
        dict(GOOD, query="Remove the Converter samples."),        # write verb: dropped
        GOOD,
        dict(GOOD),                                               # the same query again: dropped
        dict(GOOD, label="Only Reverter", query="Show samples for human subjects classified as Reverter."),
        dict(GOOD, label="Only Non-converter", query="Show samples for human subjects classified as Non-converter."),
    ]}
    out = sg.suggestions_from_review(review, bundle_id=12, refers_back=NO_CUE)
    assert [(c["id"], c["label"]) for c in out] == [("b12-r0", "Only Converter"), ("b12-r1", "Only Reverter")]
    assert len(out) == sg.MAX_SUGGESTIONS


def test_whitespace_is_normalised_so_the_clicked_text_matches():
    review = {"verdict": "suggest",
              "suggestion": dict(GOOD, label="  Only   Converter ", query="Show samples  for human\nsubjects "
                                 "classified as Converter.  ")}
    (chip,) = sg.suggestions_from_review(review, bundle_id=7, refers_back=NO_CUE)
    assert chip["label"] == "Only Converter" and chip["query"] == QUERY


def test_the_review_is_not_mutated():
    review = json.loads(json.dumps(REVIEW))
    (chip,) = sg.suggestions_from_review(review, bundle_id=7, refers_back=NO_CUE)
    chip["query"] = "changed"
    assert review == REVIEW


def test_a_graph_review_as_debug_dict_makes_a_chip():
    from chat_nextseek.graph_review import GraphReview, as_debug

    review = as_debug(GraphReview(verdict="suggest", checks=[], disclosure="57 of 98 were Non-converter.",
                                  suggestion={"kind": "value_split", "label": "Only Converter", "query": QUERY,
                                              "reason": "57 of 98 were Non-converter.", "expected_count": 32}))
    (chip,) = sg.suggestions_from_review(review, bundle_id=4)
    assert chip["id"] == "b4-r0" and chip["kind"] == "value_split" and chip["expected_count"] == 32


# ---------------------------------------------------------------- pending_for and accept ---------------------------
def _pending(turn_id=3):
    s = {}
    sg.pending_for(s, sg.suggestions_from_review(REVIEW, bundle_id=7, refers_back=NO_CUE), turn_id=turn_id)
    return s


def test_pending_is_stored_under_the_spec_key():
    s = _pending()
    assert s["pending_suggestions"] == {"for_turn": 3, "items": sg.suggestions_from_review(REVIEW, bundle_id=7,
                                                                                            refers_back=NO_CUE)}


def test_accept_strips_the_users_text():
    assert sg.accept(_pending(), f"  {QUERY}\n", last_turn_id=3)["id"] == "b7-r0"


def test_any_other_text_is_not_accepted_and_clears_pending():
    s = _pending()
    assert sg.accept(s, "Show samples for human subjects classified as Reverter.", last_turn_id=3) is None
    assert "pending_suggestions" not in s
    assert sg.accept(s, QUERY, last_turn_id=3) is None


def test_a_turn_in_between_also_clears_pending():
    s = _pending()
    assert sg.accept(s, QUERY, last_turn_id=4) is None
    assert "pending_suggestions" not in s


def test_accept_with_nothing_pending_is_none():
    assert sg.accept({}, QUERY, last_turn_id=3) is None
    assert sg.accept({"pending_suggestions": None}, QUERY, last_turn_id=3) is None
    assert sg.accept({"pending_suggestions": {"for_turn": 3}}, QUERY, last_turn_id=3) is None


@pytest.mark.parametrize("text", [None, 3, ""])
def test_accept_with_no_text_is_none(text):
    s = _pending()
    assert sg.accept(s, text, last_turn_id=3) is None
    assert "pending_suggestions" not in s


def test_a_missing_turn_id_never_matches():
    s = _pending(turn_id=None)
    assert sg.accept(s, QUERY, last_turn_id=None) is None


def test_a_legacy_string_turn_id_does_not_match_an_int():
    assert sg.accept(_pending(turn_id=3), QUERY, last_turn_id="3") is None


def test_no_items_clears_an_older_pending_entry():
    s = _pending()
    sg.pending_for(s, [], turn_id=5)
    assert "pending_suggestions" not in s
    assert sg.accept(s, QUERY, last_turn_id=5) is None


def test_new_pending_replaces_the_old():
    s = _pending(turn_id=3)
    other = dict(GOOD, label="Only Reverter", query="Show samples for human subjects classified as Reverter.")
    sg.pending_for(s, sg.suggestions_from_review({"verdict": "suggest", "suggestion": other}, bundle_id=8,
                                                 refers_back=NO_CUE), turn_id=5)
    assert sg.accept(s, other["query"], last_turn_id=5)["id"] == "b8-r0"


def test_pending_survives_a_json_session_round_trip():
    """Django's session serializer is JSON: what pending_for stores must come back acceptable."""
    s = json.loads(json.dumps(_pending()))
    assert sg.accept(s, QUERY, last_turn_id=3)["id"] == "b7-r0"


def test_pending_does_not_alias_the_callers_items():
    items = sg.suggestions_from_review(REVIEW, bundle_id=7, refers_back=NO_CUE)
    s = {}
    sg.pending_for(s, items, turn_id=3)
    items[0]["query"] = "changed"
    items.append({"query": QUERY})
    assert sg.accept(s, QUERY, last_turn_id=3)["id"] == "b7-r0"


def test_pending_works_on_a_session_like_mapping():
    """A Django session is a mapping with get/pop/__setitem__, not a dict subclass."""

    class Session:
        def __init__(self):
            self._d = {}
            self.modified = False

        def __setitem__(self, k, v):
            self._d[k] = v
            self.modified = True

        def __getitem__(self, k):
            return self._d[k]

        def __contains__(self, k):
            return k in self._d

        def get(self, k, default=None):
            return self._d.get(k, default)

        def pop(self, k, *default):
            self.modified = self.modified or k in self._d
            return self._d.pop(k, *default)

    s = Session()
    sg.pending_for(s, sg.suggestions_from_review(REVIEW, bundle_id=7, refers_back=NO_CUE), turn_id=3)
    assert s.modified
    assert sg.accept(s, QUERY, last_turn_id=3)["id"] == "b7-r0"
    assert "pending_suggestions" not in s
