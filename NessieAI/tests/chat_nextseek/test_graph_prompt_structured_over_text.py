"""One defect with three symptoms: a text predicate preferred over a structured one,
then reported as though it had been structured. All three are from the 2026-09-21 run.

* **CC mice.** "do you have any datasets with collaborative cross mice?" resolved the
  Mouse type and carried "collaborative cross" as a KEYWORD, and a keyword's only
  mechanism here is ``toLower(s.search_text) CONTAINS``. The phrase appears nowhere in
  the records, so the query returned 0 and the reply said so with confidence. The
  zero-row retry DID fire (``[GRAPH] Query ran but matched nothing, retrying once``);
  its second query broadened from ``T_MUS`` to every ``:Sample`` and stayed in
  ``search_text``, so the answer was text twice. Measured in the graph, 737 T_MUS
  samples carry a ``Genotype`` matching ``^CC[0-9]``: CC042 427, CC024J 75, CC027J 12,
  CC045J 12, CC051 12, and a tail of CC0xx strains. The reply even named the strategy
  it did not take ("you might try ... such as CC001").

* **Shoulders.** ``report.shoulders_inventory`` answered 568, which is right, but got
  there by falling back to text: "A direct query for the 'Shoulders' investigation
  returned no matches. Instead, a graph query ... constrained by the keyword
  'Shoulders' and project 'Shoulders'." Shoulders is in the vocabulary the agent was
  handed, so the scope existed and the wrong node was matched; the text OR then widened
  the result to anything mentioning the word, which is a count neither the agent nor the
  reply can account for. The operator's note: "should probably hit samples in
  investigation not the sample search text."

* **HeLa.** ``search.hela_trap`` answered "We have 4 HeLa cell-line samples". The four
  are HeLa, HeLa-ActD, HeLa-GSK and HeLa-TSA, so the retrieval was right and the
  description was wrong. The chatter could not have done better: the Cypher projected
  ``count(s)`` and nothing else, so the names never reached it, which left
  ``chatter_agent.txt``'s own "COUNT THE THING YOU NAME" rule unenforceable.
"""
import re
from pathlib import Path

import chat_nextseek

PROMPT = (Path(chat_nextseek.__file__).resolve().parent / "prompts" / "graph_agent.txt").read_text(encoding="utf-8")


def _window(needle, after=1400, before=0):
    at = PROMPT.find(needle)
    assert at != -1, f"the prompt does not contain {needle!r}"
    return PROMPT[max(0, at - before):at + after]


# --- CC: a family name whose members are coded -----------------------------------------------------------------------


def test_a_family_term_that_found_nothing_has_a_rung_of_its_own():
    rung = _window("family, series or panel")

    assert "search_text" in rung, "the rung has to name the predicate it is replacing"
    assert ":Attribute" in rung or "Attribute)" in rung, "read the type's fields from the catalog"


def test_that_rung_names_the_field_and_the_pattern_rather_than_the_phrase():
    """The member codes are in the data; the family name is not."""
    rung = _window("family, series or panel")

    assert "Genotype" in rung
    assert re.search(r"=~\s*'\(\?i\)\^CC", rung), "the worked example is the measured one"


# --- Shoulders: a scope in the vocabulary is matched structurally -----------------------------------------------------


def test_a_scope_name_in_the_vocabulary_is_not_widened_with_a_text_match():
    rule = _window("A NAME YOU WERE GIVEN")
    lowered = rule.lower()

    assert "search_text" in lowered
    assert "do not" in lowered or "never" in lowered


def test_a_scope_that_matched_nothing_tries_the_other_node_before_leaving_the_relationship():
    rule = _window("A NAME YOU WERE GIVEN")

    for node in ("Study", "Investigation", "Project"):
        assert node in rule, node


def test_the_text_retry_is_only_for_a_name_the_vocabulary_does_not_hold():
    """The old rung sent every failed scope to search_text, which is how Shoulders got there."""
    ladder = _window("## When a query matched nothing", after=3000)
    at = ladder.find("Study or Investigation scope")
    assert at != -1, "the scope rung is still in the ladder"

    rung = ladder[at:at + 400]
    assert "vocabulary" in rung.lower(), "the text retry has to be conditioned on the name being absent from it"


# --- HeLa: the evidence behind a count reaches the writer -------------------------------------------------------------


def test_a_count_over_a_contained_name_returns_its_distinct_values():
    rule = _window("A COUNT OF A NAME THAT HAS VARIANTS")

    assert "count(*)" in rule
    assert "HeLa" in rule, "the measured failure is the example"
    assert "distinct" in rule.lower()


# --- the shared honesty rule ------------------------------------------------------------------------------------------


def test_a_zero_reached_only_through_text_is_not_reported_as_settled():
    rule = _window("A ZERO FROM A TEXT MATCH")
    lowered = rule.lower()

    assert "explanation" in lowered, "the reply can only disclose what the explanation carries"
    assert "free text" in lowered


# --- the guards the other prompt claims depend on ---------------------------------------------------------------------


def test_the_prompt_still_names_its_steps_and_output_format():
    for step in ("## STEP 1", "## STEP 5", "## STEP 6", "## Output format"):
        assert step in PROMPT


# The SRP run of 2026-09-22: "How many Aag-knockout mice are there?" answered "There are
# 1,183 Aag-knockout mice" from `toLower(toString(s.Genotype)) CONTAINS 'aag'`. Measured
# in the graph, that field holds SIX distinct spellings of the genotype for those 1,183
# samples, and the knockout itself ("aag -/-") is 848 of them. The rule for a count over
# a contained name was already in the prompt and its trigger list did not mention a
# genotype, which is the field where the spellings differ most.


def test_a_genotype_is_a_name_with_variants():
    rule = _window("A COUNT OF A NAME THAT HAS VARIANTS")
    lowered = rule.lower()

    assert "genotype" in lowered or "allele" in lowered
    assert "1,183" in rule, "the measured failure is the example"
