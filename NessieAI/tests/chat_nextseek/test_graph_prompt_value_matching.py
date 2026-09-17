"""The graph agent's prompt carries the POC's value-matching procedure (P1), the value-SET rule (P3) and the
house UID grammar (P4).

Evidence: ``PilotAPOC/review/{FINDINGS,PROPOSALS}.md`` and the 60-row ``compare_export.json``. Every count
asserted here was replayed read-only against the frozen graph (1,084,754 samples) on 2026-09-17, so a number in
the prompt that no oracle produced fails ``test_every_thousands_number_in_the_new_sections_was_measured``.

What these tests pin, and why each is a claim and not a style preference:

* **P1 is a three-branch procedure, not a preference.** A flat "prefer property predicates" rule breaks the two
  questions the fulltext index won exactly (ImmPort 4,081, extravasation 338), and a flat "use the index" rule
  is what produced 127 of 241 fibrin images and 138,313 hits for a ChIP-seq question whose answer is none. So
  the tests require all three branches to be present, each with its trigger.
* **P3 kills the bare ``=`` and the "a value the user quotes exactly" escape hatch**, which fired exactly
  wrong: ``s.Organ = 'Lung'`` 16,841 against 22,734, ``toLower(s.Sex) = 'female'`` 160 against 3,073.
* **P4 copies the api_agent rule rather than inventing a second one**: a lab code lives INSIDE the UID, so a
  prefix test answers 0 where the truth is 9,821.

Routing is phase 12, not this unit, so ``test_the_prompt_stays_out_of_routing`` fails if routing vocabulary
appears in the graph prompt at all.
"""

import re
from pathlib import Path

import pytest

import chat_nextseek

PACKAGE = Path(chat_nextseek.__file__).resolve().parent
PROMPT = (PACKAGE / "prompts" / "graph_agent.txt").read_text(encoding="utf-8")
API_PROMPT = (PACKAGE / "prompts" / "api_agent.txt").read_text(encoding="utf-8")
STRUCTURE = (PACKAGE / "prompts" / "graph_schema_structure.txt").read_text(encoding="utf-8")

VALUE_HEADING = "### Matching a value"
UID_HEADING = "### UIDs and lab codes"
OTHER_HEADING = "### Other mappings"

# Counts of 1,000 or more that the new sections are allowed to cite, each replayed read-only against the frozen
# graph on 2026-09-17 (scratchpad cy.py; the queries are in the prompt itself).
MEASURED_THOUSANDS = {
    "1,084,754": "MATCH (s:Sample) RETURN count(s)",
    "1,084,753": "uuids matching ^[^-]+-\\d{6}[A-Za-z]{3}-\\d+",
    "16,841": "T_TIS s.Organ = 'Lung'",
    "22,734": "T_TIS toLower(toString(s.Organ)) = 'lung'",
    "3,073": "T_MUS toLower(toString(s.Sex)) IN ['f','female']",
    "2,913": "T_MUS s.Sex = 'F'",
    "5,564": "T_PAT Sex IN ['male','m']",
    "5,485": "T_PAT toLower(s.Sex) = 'male'",
    "14,998": "search_text CONTAINS 'cd8', every type",
    "2,111": "T_MUS search_text CONTAINS 'ndma'",
    "1,549": "fulltext 'ndma' scoped to T_MUS",
    "4,081": "fulltext 'ImmPort', and Repository = 'immport'",
    "1,103": "fulltext 'chip'",
    "137,210": "fulltext 'seq'",
    "138,313": "fulltext 'ChIP-seq', the OR of the two tokens",
    "9,821": "uuid =~ '(?i)^[^-]+-\\d{6}KAM-\\d+.*'",
}

THOUSANDS_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+\b")


def _between(start: str, end: str) -> str:
    at = PROMPT.find(start)
    assert at != -1, f"the prompt has no {start!r} section"
    to = PROMPT.find(end, at)
    assert to != -1, f"the prompt has no {end!r} after {start!r}"
    return PROMPT[at:to]


def value_section() -> str:
    return _between(VALUE_HEADING, UID_HEADING)


def uid_section() -> str:
    return _between(UID_HEADING, OTHER_HEADING)


def branch(n: int) -> str:
    """One branch of the procedure, from its heading to the next branch or the end of the section."""
    section = value_section()
    at = section.find(f"**Branch {n} ")
    assert at != -1, f"the procedure has no Branch {n}"
    to = section.find(f"**Branch {n + 1} ", at)
    return section[at:to if to != -1 else len(section)]


# --- the procedure exists, in STEP 2, and is a procedure -----------------------------------------------------


def test_the_value_matching_procedure_is_a_step_2_section():
    step2 = _between("## STEP 2", "## STEP 3")
    for heading in (VALUE_HEADING, UID_HEADING, OTHER_HEADING):
        assert heading in step2, f"{heading} belongs in STEP 2, where user language is mapped to graph values"
    assert step2.find(VALUE_HEADING) < step2.find(UID_HEADING) < step2.find(OTHER_HEADING)


def test_the_procedure_has_exactly_three_branches():
    section = value_section()
    labels = re.findall(r"\*\*Branch (\d+) ", section)
    assert labels == ["1", "2", "3"], f"three branches in order, got {labels}"


def test_it_says_it_is_a_procedure_and_not_a_preference():
    lowered = value_section().lower()
    assert "decision procedure" in lowered or "not a preference" in lowered
    # A flat preference is exactly what must not be written: it breaks the two questions the index won.
    assert "prefer property predicates" not in lowered
    assert "always use a property" not in lowered
    assert "never use the fulltext index" not in lowered


def test_each_branch_names_its_trigger_and_its_form():
    one, two, three = branch(1), branch(2), branch(3)
    assert "values:" in one and ("lists" in one or "listed" in one), "branch 1 triggers off the catalog listing"
    assert "toLower(toString(s." in one, "branch 1 shows a case-folded, type-safe property predicate"
    assert "toLower(s.search_text) CONTAINS" in two, "branch 2 shows the search_text scan"
    assert "db.index.fulltext.queryNodes('sample_search_text'" in three, "branch 3 shows the index call"


# --- P1 branch 2: inside a longer word, punctuation, adjacency -----------------------------------------------


def test_branch_2_fires_on_a_match_inside_a_longer_word():
    two = branch(2)
    assert "fibrin" in two and "241" in two and "127" in two
    assert "fibrinogen" in two, "the reason the index misses it: fibrinogen is one token"
    assert "token" in two.lower()


def test_branch_2_fires_on_punctuation_and_on_adjacency():
    two = branch(2).lower()
    assert "punctuation" in two or "hyphen" in two
    assert "adjacen" in two or "phrase" in two
    assert "cd8 depletion" in two and "151" in two


def test_branch_2_is_allowed_unscoped_because_that_was_measured():
    """PROPOSALS: "an unscoped toLower(s.search_text) CONTAINS over 1,084,754 nodes was never executed"."""
    two = branch(2)
    assert "1,084,754" in two
    assert re.search(r"\bunscoped\b", two, re.IGNORECASE), "say whether the unscoped scan is allowed"


# --- P1 branch 3: the index keeps the two questions it won, and only those ------------------------------------


def test_branch_3_keeps_the_two_questions_the_index_won():
    three = branch(3)
    assert "ImmPort" in three and "4,081" in three
    assert "extravasation" in three and "338" in three


def test_branch_3_restricts_the_index_to_a_single_clean_word():
    three = branch(3).lower()
    assert "single" in three and "word" in three
    for reason in ("hyphen", "digit"):
        assert reason in three, f"a clean word excludes a {reason}"


def test_branch_3_carries_the_chipseq_trap_with_its_arithmetic():
    three = branch(3)
    for number in ("1,103", "137,210", "138,313"):
        assert number in three, f"the trap's arithmetic needs {number}"
    assert "ChIP-seq" in three


def test_the_prompt_never_calls_the_index_a_substring_search():
    """The index tokenises. Calling it a substring search is what sent 8 questions to the wrong branch."""
    for match in re.finditer(r"substring search", PROMPT, re.IGNORECASE):
        before = PROMPT[max(0, match.start() - 40):match.start()].lower()
        assert "not a" in before or "never a" in before, (
            "the phrase may appear only as a correction, not as a description of the index")
    assert re.search(r"tokenis|tokeniz", PROMPT, re.IGNORECASE), "say what the index actually does"


def test_no_example_anywhere_filters_a_string_attribute_with_a_bare_equals():
    """Two examples taught the defect that P3 removes: the type-label bullet, and structure rule 1."""
    for text, where in ((PROMPT, "graph_agent.txt"), (STRUCTURE, "graph_schema_structure.txt")):
        assert "WHERE s.Organ = " not in text, f"{where} still shows a bare = on a string attribute"
        assert "toLower(toString(s.Organ))" in text, f"{where} should show the type-safe form instead"


def test_structure_rule_2_does_not_leave_the_bare_equals_standing():
    start, end = STRUCTURE.find("\n2. "), STRUCTURE.find("\n3. ")
    rule_2 = STRUCTURE[start:end].lower()
    assert "never write one on a string attribute" in rule_2
    assert "tostring" in rule_2 and "values:" in rule_2


def test_the_schema_structure_block_agrees_with_the_procedure():
    """The same context window holds both; a contradiction is resolved arbitrarily by the model."""
    assert "substring search" not in STRUCTURE.lower()
    start, end = STRUCTURE.find("\n5. "), STRUCTURE.find("\n6. ")
    assert start != -1 and end > start, "structure rule 5 is the fulltext rule and rule 6 follows it"
    rule_5 = STRUCTURE[start:end].lower()
    assert "substring" not in rule_5
    assert "tokenis" in rule_5 or "tokeniz" in rule_5, "say that the index tokenises"
    assert "tolower(s.search_text) contains" in rule_5, "and that a keyword scan is the CONTAINS form"


# --- P3: no bare = on string metadata, match the value SET ----------------------------------------------------


def test_the_quoted_value_escape_hatch_is_gone():
    """"and for a value the user quotes exactly" fired exactly wrong on Organ and Sex."""
    assert "quotes exactly" not in PROMPT
    assert re.search(r"quot\w+ tells you which concept", PROMPT), (
        "a quoted value must be named as the user's spelling, not as a licence for exact equality")


def test_a_bare_equals_on_string_metadata_is_refused_with_its_evidence():
    at = PROMPT.find("16,841")
    assert at != -1, "the Organ measurement belongs in the prompt"
    around = PROMPT[max(0, at - 400):at + 400]
    assert "22,734" in around, "both sides of the Organ measurement, together"
    lowered = PROMPT.lower()
    assert "bare `=`" in lowered, "name the thing being banned"


def test_exact_equality_is_kept_where_it_is_right():
    """P3 bans `=` on string metadata, not `=` on a code, a number, a date, an id or a whole UID."""
    lowered = PROMPT.lower()
    at = lowered.find("exact `=`")
    assert at != -1, "say where exact equality is still right"
    around = lowered[at:at + 320]
    for kind in ("number", "date", "code", "id", "uid"):
        assert kind in around, f"exact `=` is right for a {kind}"


def test_the_value_set_rule_reads_the_catalog_values_line():
    one = branch(1)
    assert re.search(r"IN \['f'\s*,\s*'female'\]", one), "the Sex value set, from the catalog's own values line"
    assert "160" in one and "3,073" in one, "both sides of the Sex measurement"
    assert "2,913" in one, "why: the data stores the code F"
    assert "5,564" in one and "5,485" in one, "the patient Sex measurement repeats the shape"


def test_the_value_set_rule_admits_the_values_line_is_only_the_top_ten():
    one = branch(1).lower()
    assert "ten" in one or "10" in one
    assert "search_text" in one, "widen with branch 2 when a spelling could be missing"


def test_a_zero_from_a_guessed_attribute_is_not_an_answer():
    one = branch(1)
    assert "HeLa" in one, "the measured case: CellLine returns 0 where the truth is 4"
    assert re.search(r"\b0\b", one) and re.search(r"\b4\b", one)


# --- P4: the UID grammar the api agent already has ------------------------------------------------------------


def test_the_uid_grammar_is_stated_with_a_real_example():
    uid = uid_section()
    assert "<TYPE>-<YYMMDD><LAB>-<n>" in uid
    assert re.search(r"TIS-220831FLY-26", uid), "a UID that exists in the graph"
    assert "1,084,753" in uid, "how much of the database the grammar covers"


def test_a_lab_code_prefix_test_is_refused_with_its_measurement():
    uid = uid_section()
    assert "STARTS WITH" in uid
    assert "9,821" in uid and re.search(r"\b0\b", uid), "0 against 9,821 is the whole argument"
    assert re.search(r"never (?:at the )?(?:start|beginning)|not at position 0", uid, re.IGNORECASE)


def test_the_anchored_lab_regex_is_given_verbatim():
    uid = uid_section()
    assert r"'(?i)^[^-]+-\d{6}" in uid, "the anchored form, as the answer key writes it"
    assert r"-\d+.*'" in uid
    for number in ("683", "140"):
        assert number in uid, f"the per-type replay {number}"


def test_the_loose_contains_form_is_offered_but_ranked_second():
    uid = uid_section()
    assert "toLower(s.uuid) CONTAINS" in uid
    at = uid.find("toLower(s.uuid) CONTAINS")
    assert re.search(r"anchor|prefer", uid[max(0, at - 300):at + 300], re.IGNORECASE)


def test_the_lab_is_not_looked_for_in_a_lab_or_scientist_field():
    uid = uid_section()
    assert "s.Lab" in uid and "26" in uid, "toLower(s.Lab) CONTAINS 'kam' answered 26 of 9,821"


def test_the_graph_uid_rule_agrees_with_the_api_agent_rule():
    """P4: copy the grammar api_agent.txt already carries, do not invent a second one."""
    assert "INSIDE the sample UID" in API_PROMPT, "the api agent's rule, unchanged"
    uid = uid_section()
    assert re.search(r"inside the (?:sample )?uid", uid, re.IGNORECASE)
    assert "api_agent" in uid, "say which prompt this is copied from"


def test_the_type_prefix_is_not_how_a_type_is_filtered():
    uid = uid_section()
    assert re.search(r"s\.type = \$?\w+|:T_<code>", uid)


# --- guards ---------------------------------------------------------------------------------------------------


def test_every_thousands_number_in_the_new_sections_was_measured():
    for section in (value_section(), uid_section()):
        for number in THOUSANDS_RE.findall(section):
            assert number in MEASURED_THOUSANDS, (
                f"{number} appears in the prompt but no replayed oracle produced it; "
                f"measured: {sorted(MEASURED_THOUSANDS)}")


def test_the_prompt_stays_out_of_routing():
    """Routing ships in phase 12, after the forced re-run, so the two deltas stay separable."""
    lowered = PROMPT.lower()
    for token in ("advanced_search", "new_search", "graph_query", "min_graph_schema", "disambiguation"):
        assert token not in lowered, f"{token} is routing vocabulary and does not belong in the graph prompt"


def test_the_prompt_keeps_its_three_steps_and_its_output_contract():
    for anchor in ("## STEP 1", "## STEP 2", "## STEP 3", "## Output format", '"cypher"', '"parameters"'):
        assert anchor in PROMPT


def test_the_new_sections_do_not_contradict_the_whole_node_ban():
    """Every worked example added here must obey the existing rule against returning a whole Sample node."""
    for section in (value_section(), uid_section()):
        assert not re.search(r"RETURN\s+s\s*(?:,|$|\n)", section, re.MULTILINE)
        assert "properties(s)" not in section and "collect(s)" not in section


def cypher_blocks() -> list[str]:
    """Every indented worked example in the two new sections, dedented, comments stripped."""
    blocks, current = [], []
    for line in (value_section() + "\n" + uid_section()).splitlines() + [""]:
        if line.strip() and line.startswith("    "):
            current.append(re.sub(r"\s*//.*$", "", line).strip())
        elif current:
            blocks.append(" ".join(current))
            current = []
    return [b for b in blocks if re.match(r"(?:MATCH|CALL|WITH|RETURN)\b", b)]


def test_no_worked_example_case_folds_with_coalesce():
    """``coalesce(s.Arm, '')`` aborts the whole query: 474 NHP Arm values are strings and 35 are integers, and
    coalesce refuses the mix. ``toString`` is type-safe and null-safe, and reproduces every oracle count."""
    for cypher in cypher_blocks():
        assert not re.search(r"coalesce\s*\(\s*\w+\.", cypher), cypher


def test_every_worked_example_passes_the_agents_own_guards():
    """A prompt that teaches Cypher its own guards refuse costs a repair round trip on every turn."""
    graph = pytest.importorskip("chat_nextseek.agents.graph")
    examples = cypher_blocks()
    assert len(examples) >= 5, f"the procedure shows worked queries, found {len(examples)}: {examples}"
    for cypher in examples:
        assert graph.whole_node_returns(cypher) == [], cypher
        assert graph.optional_match_filter_leaks(cypher) == [], cypher
        assert graph.canonicalize_sample_uid_property(cypher) == (cypher, []), (
            f"the canonical UID property is uuid, so no example may read .UID: {cypher}")
