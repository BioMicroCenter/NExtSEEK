"""
Regression lock for T1.8: the advanced_search retry ladder must not degrade to a
meaningless token.

Task 797 asked for the sequencing data derived from two NHP UIDs. `_split_retry_keyword`
splits on `[\\s_\\-/]+`, so `NHP-220524FLY-1-PUB` yielded ['NHP','220524FLY','1','PUB'],
and the ladder walked down to the term `"1"`:

    filter_searchText='NHP-220524FLY-1-PUB NHP-220524FLY-2-PUB'  -> total 0
    filter_searchText='NHP'                                       -> total 0
    filter_searchText='220524FLY'                                 -> total 0
    filter_searchText='1'                                         -> total 2057
    [DEBUG][API][RETRY] Success with SINGLE: filter_searchText='1' total=2057 rows=1000

2,057 unrelated records were then reported as the answer. The correct answer is the
six D.SEQ-220823SHA-1..6-PUB records; a ladder landing on 2,057 rows for a two-UID
question is not an answer.
"""
from __future__ import annotations

import pytest

import chat_nextseek.helpers.tools.nextseek_api as api
from chat_nextseek.helpers.tools.nextseek_api import (
    RETRY_SINGLE_TOTAL_CEILING,
    _advanced_search_retry_attempts,
    _retry_advanced_search_if_empty,
    _retry_terms,
    _split_retry_keyword,
)

ADV = "/nextseek_api/samples/advanced_search/"
TWO_UIDS = "NHP-220524FLY-1-PUB NHP-220524FLY-2-PUB"


def _plan(keywords=None, lab_codes=None):
    return {"filters": {"keywords": keywords or [], "lab_codes": lab_codes or []}}


def _api_plan(searchtext):
    return {"endpoint": ADV, "method": "POST",
            "requestBody": {"filter_searchText": searchtext}}


def _empty():
    return {"ok": True, "data": {"total": 0, "rows": []}}


def _hit(total, rows=None):
    return {"ok": True, "data": {"total": total, "rows": rows if rows is not None else [{}] * min(total, 1000)}}


# ------------------------------------------------------------------ tokenising


def test_the_task_797_ladder_no_longer_produces_a_bare_number():
    """The direct RED repro."""
    terms = _retry_terms(_plan(), _api_plan(TWO_UIDS))

    assert terms == ["NHP", "220524FLY"]
    for junk in ("1", "2", "PUB"):
        assert junk not in terms


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("NHP-220524FLY-1-PUB", ["NHP", "220524FLY"]),
        ("D.SEQ-220823SHA-6-PUB1", ["D.SEQ", "220823SHA"]),
        ("TIS-240612KAM-1", ["TIS", "240612KAM"]),
        ("KAM MetNet", ["KAM", "MetNet"]),
        ("a b c", []),
        ("2057", []),
    ],
)
def test_split_drops_short_numeric_and_pub_tokens(raw, expected):
    assert _split_retry_keyword(raw) == expected


def test_the_existing_lab_code_behaviour_is_unchanged():
    """The KAM/MetNet case this ladder was built for must still work."""
    assert _retry_terms(_plan(lab_codes=["KAM"]), _api_plan("KAM MetNet")) == ["KAM", "MetNet"]
    texts = {t for _, t in _advanced_search_retry_attempts(["KAM", "MetNet"])}
    assert {"KAM", "MetNet", "KAM OR MetNet"} <= texts


# --------------------------------------------------------------------- ceiling


def test_a_single_attempt_over_the_ceiling_is_rejected(monkeypatch):
    """
    The ladder must not accept 2,057 rows as the answer to a two-UID question. It
    exhausts and returns the ORIGINAL plan and result, so the reply is an honest zero.
    """
    original_plan = _api_plan(TWO_UIDS)
    original_result = _empty()
    sent: list[str] = []

    def fake_request(config, *, endpoint, method, requestBody, queryParameters):
        text = requestBody.get("filter_searchText")
        sent.append(text)
        return _hit(2057) if text == "220524FLY" else _empty()

    monkeypatch.setattr(api, "tool_nextseek_api_request", fake_request)

    plan, result = _retry_advanced_search_if_empty(None, _plan(), original_plan, original_result)

    assert plan is original_plan, "an over-ceiling single term was accepted as the answer"
    assert result is original_result
    assert "1" not in sent and "PUB" not in sent


def test_a_single_attempt_under_the_ceiling_is_still_accepted(monkeypatch):
    """The ladder must keep working for the case it exists to serve."""
    def fake_request(config, *, endpoint, method, requestBody, queryParameters):
        return _hit(12) if requestBody.get("filter_searchText") == "KAM" else _empty()

    monkeypatch.setattr(api, "tool_nextseek_api_request", fake_request)

    plan, result = _retry_advanced_search_if_empty(
        None, _plan(lab_codes=["KAM"]), _api_plan("KAM MetNet"), _empty()
    )

    assert plan["requestBody"]["filter_searchText"] == "KAM"
    assert result["data"]["total"] == 12


def test_an_or_rung_is_subject_to_the_ceiling_too(monkeypatch):
    """Changed knowingly by F4. The rule here used to read "the ceiling guards
    degradation to one token, not a legitimate broad OR", and that left the rung that
    actually substituted a different question unguarded: every rung of this ladder is
    already a substitution of the user's terms, and one matching this much of the
    database is not an answer to a filtered question.

    Two keywords of the same kind, so a mixed-kind set is not what withholds the rung
    here -- the ceiling is.
    """
    def fake_request(config, *, endpoint, method, requestBody, queryParameters):
        text = requestBody.get("filter_searchText")
        return _hit(900) if " OR " in (text or "") else _empty()

    monkeypatch.setattr(api, "tool_nextseek_api_request", fake_request)

    plan, result = _retry_advanced_search_if_empty(
        None, _plan(keywords=["fibrin", "collagen"]), _api_plan("fibrin collagen"), _empty()
    )

    assert " OR " not in (plan["requestBody"]["filter_searchText"] or "")
    assert (result["data"] or {}).get("total") in (None, 0), "no rung survived, so nothing is reported"


def test_a_lab_code_is_never_ored_with_a_term_of_another_kind(monkeypatch):
    """A lab code is a conjunctive constraint: ORing it can only add other labs' samples.

    The production case asked how many RNA samples one lab has. The entity step made the
    lab a project as well, the api agent fused both into one phrase, that matched nothing,
    and the OR rung answered from a search the user never asked for. It was right only by
    luck: the other term matched no sample of the requested type.
    """
    seen: list[str] = []

    def fake_request(config, *, endpoint, method, requestBody, queryParameters):
        text = requestBody.get("filter_searchText") or ""
        seen.append(text)
        return _hit(12) if text == "KAM" else _empty()

    monkeypatch.setattr(api, "tool_nextseek_api_request", fake_request)

    plan, result = _retry_advanced_search_if_empty(
        None, _plan(lab_codes=["KAM"]), _api_plan("KAM MetNet"), _empty()
    )

    assert not any(" OR " in text for text in seen), seen
    assert seen[0] == "KAM", "the lab code is the most specific term, so it goes first"
    assert plan["requestBody"]["filter_searchText"] == "KAM"
    assert result["data"]["total"] == 12


def test_a_labelled_identifier_is_searched_by_its_value_alone(monkeypatch):
    """The production PMID case, and the largest single family in the review.

    The question was sent as the label and the number together; the stored value is the
    number alone, so it matched nothing. The ladder then dropped the number as UID
    structure and kept the word "PMID", which matched unrelated rows and served them as
    the answer. Measured: a search for the bare value returns the study's samples.
    """
    seen: list[str] = []

    def fake_request(config, *, endpoint, method, requestBody, queryParameters):
        text = requestBody.get("filter_searchText") or ""
        seen.append(text)
        return _hit(9) if text == "40449485" else _empty()

    monkeypatch.setattr(api, "tool_nextseek_api_request", fake_request)

    plan, result = _retry_advanced_search_if_empty(
        None, _plan(keywords=["PMID 40449485"]), _api_plan("PMID 40449485"), _empty()
    )

    assert seen[0] == "40449485", seen
    assert not any(" OR " in text for text in seen), seen
    assert all(text.lower() != "pmid" for text in seen), "the label is not a search term"
    assert plan["requestBody"]["filter_searchText"] == "40449485"
    assert result["data"]["total"] == 9


def test_a_doi_is_extracted_from_its_label_too():
    from chat_nextseek.helpers.tools.nextseek_api import _identifier_terms

    assert _identifier_terms("DOI: 10.1016/j.immuni.2025.05.004") == ["10.1016/j.immuni.2025.05.004"]
    assert _identifier_terms("PMID 40449485") == ["40449485"]
    assert _identifier_terms("pmid: 40449485,") == ["40449485"]
    assert _identifier_terms("find tissue samples") == []
    assert _identifier_terms("") == []


def test_the_ceiling_does_not_apply_when_the_original_search_was_unfiltered(monkeypatch):
    """"How big is the database" legitimately returns everything."""
    def fake_request(config, *, endpoint, method, requestBody, queryParameters):
        return _hit(50886)

    monkeypatch.setattr(api, "tool_nextseek_api_request", fake_request)

    plan, result = _retry_advanced_search_if_empty(
        None, _plan(keywords=["samples", "total"]),
        {"endpoint": ADV, "method": "POST", "requestBody": {}}, _empty(),
    )

    assert result["data"]["total"] == 50886


# ------------------------------------------------------- substitution recorded


def test_an_accepted_substitution_is_recorded_for_disclosure(monkeypatch):
    """T1.10 surfaces this to the user; the ladder has to record it first."""
    def fake_request(config, *, endpoint, method, requestBody, queryParameters):
        return _hit(12) if requestBody.get("filter_searchText") == "KAM" else _empty()

    monkeypatch.setattr(api, "tool_nextseek_api_request", fake_request)

    plan, _result = _retry_advanced_search_if_empty(
        None, _plan(lab_codes=["KAM"]), _api_plan("KAM MetNet"), _empty()
    )

    sub = plan["retry_substituted_search"]
    assert sub["original"] == "KAM MetNet"
    assert sub["used"] == "KAM"
    assert sub["label"] == "SINGLE"


def test_no_substitution_is_recorded_when_the_ladder_exhausts(monkeypatch):
    monkeypatch.setattr(api, "tool_nextseek_api_request",
                        lambda *a, **k: _empty())

    plan, _result = _retry_advanced_search_if_empty(
        None, _plan(lab_codes=["KAM"]), _api_plan("KAM MetNet"), _empty()
    )

    assert "retry_substituted_search" not in plan


def test_the_ceiling_constant_is_sane():
    assert 50 <= RETRY_SINGLE_TOTAL_CEILING <= 1000
