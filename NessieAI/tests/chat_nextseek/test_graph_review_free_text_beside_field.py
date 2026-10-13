"""free_text_beside_field: a count compares a structured field for a term AND matches the same term as free text
(R3 A5 and A6). Entities are made up."""
from chat_nextseek.graph_review import (FOLLOWUP_SEEDED_SKIP, SHIP, DictCatalog, ReviewInput, review_tier1)

A5_CY = ("MATCH (m:T_RAT) WHERE (m.Strain =~ $p OR toLower(m.search_text) CONTAINS toLower($k)) "
         "AND EXISTS { MATCH (e:T_EXPR)-[:DERIVED_FROM]->(m) } RETURN count(m) AS n")
A6_CY = ("MATCH (m:T_MOU) WHERE any(v IN [m.Dose1, m.Dose2] WHERE v IS NOT NULL AND toLower(toString(v)) CONTAINS $t) "
         "OR EXISTS { MATCH (m)-[:DERIVED_FROM]->(c:T_REAGENT) WHERE toLower(c.search_text) CONTAINS $t } "
         "RETURN count(m) AS n")
FIELD_ONLY_CY = "MATCH (m:T_RAT) WHERE m.Strain =~ $p RETURN count(m) AS n"
TEXT_ONLY_CY = "MATCH (m:T_RAT) WHERE toLower(m.search_text) CONTAINS toLower($k) RETURN count(m) AS n"


def _review(question, cy, params, keyword_fields, skip=None):
    inp = ReviewInput(question=question, cypher=cy, parameters=params, keyword_fields=keyword_fields,
                      rows=[{"n": 7}], count=1, total=1, ok=True, error=None)
    return review_tier1(inp, DictCatalog({}), skip=skip)


def _fired(rv):
    return next(c for c in rv.checks if c.name == "free_text_beside_field").fired


def test_a_field_and_a_text_match_on_one_term_fires_and_names_the_field():
    rv = _review("How many Arcadia rats have expression data derived from them?", A5_CY,
                 {"p": "(?i)Arcadia", "k": "Arcadia"}, {"Arcadia": ["Strain"]})
    assert _fired(rv) and rv.verdict == "suggest"
    assert rv.disclosure == ("The count matches 'Arcadia' in the Strain field and also anywhere in a sample's text, "
                             "so it can include records whose Strain does not say Arcadia.")


def test_an_exists_arm_over_a_related_sample_says_related_sample():
    rv = _review("How many Zorbex mice are there?", A6_CY, {"t": "zorbex"}, {"Zorbex": ["Dose1", "Dose2"]})
    assert _fired(rv)
    assert rv.disclosure == ("The count matches 'Zorbex' in the Dose1 and Dose2 fields and also anywhere in a related "
                             "sample's text, so it can include records whose Dose1 and Dose2 fields do not say Zorbex.")


def test_a_field_only_count_stays_quiet():
    assert not _fired(_review("How many Arcadia rats?", FIELD_ONLY_CY, {"p": "(?i)Arcadia"}, {"Arcadia": ["Strain"]}))


def test_a_text_only_count_with_no_keyword_fields_stays_quiet():
    assert not _fired(_review("How many Arcadia rats?", TEXT_ONLY_CY, {"k": "Arcadia"}, {}))


def test_it_is_a_ship_check_and_a_seeded_follow_up_skips_it():
    assert "free_text_beside_field" in SHIP and "free_text_beside_field" in FOLLOWUP_SEEDED_SKIP
    rv = _review("How many Arcadia rats?", A5_CY, {"p": "(?i)Arcadia", "k": "Arcadia"}, {"Arcadia": ["Strain"]},
                 skip=FOLLOWUP_SEEDED_SKIP)
    assert not _fired(rv) and rv.verdict == "ok"


def test_a_field_used_only_in_return_to_group_stays_quiet():
    cy = ("MATCH (m:T_RAT) WHERE toLower(m.search_text) CONTAINS toLower($k) "
          "RETURN m.Strain AS strain, count(m) AS n")
    assert not _fired(_review("How many Arcadia rats, by strain?", cy, {"k": "Arcadia"}, {"Arcadia": ["Strain"]}))


def test_another_entity_with_the_field_only_in_return_stays_quiet():
    cy = "MATCH (m:T_MOU) WHERE toLower(m.search_text) CONTAINS $t RETURN m.Dose1 AS d, count(m) AS n"
    assert not _fired(_review("How many Zorbex mice, by dose?", cy, {"t": "zorbex"}, {"Zorbex": ["Dose1"]}))
