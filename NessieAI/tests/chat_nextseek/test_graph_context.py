"""The variant (b) renderer: the graph agent's schema text, built from the v1.1 catalog (spec section 4.2).

The renderer is pure. These tests build the catalog reader's rows with SimpleNamespace, by field name, so they
do not depend on graph_catalog.py.
"""

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from chat_nextseek import graph_context as gc

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DOC = REPO_ROOT / "docs" / "neo4j-schema.md"


# ---------------------------------------------------------------------------------------------------------------
# Builders (the field names of graph_catalog's dataclasses)
# ---------------------------------------------------------------------------------------------------------------

def index_row(title, *, label=None, name=None, clade=None, sample_count=0, deprecated=False,
              attributes_with_values=0):
    return SimpleNamespace(title=title, label=label or "T_" + re.sub(r"[^A-Za-z0-9_]", "_", title), name=name,
                           clade=clade, sample_count=sample_count, deprecated=deprecated,
                           attributes_with_values=attributes_with_values)


def attr(title, *, value_type="string", declared=True, needs_backticks=False, sample_count=1, meaning=None,
         unit_key=None, role="data", top_values=(), top_counts=(), num_min=None, num_max=None, date_min=None,
         date_max=None):
    return SimpleNamespace(title=title, value_type=value_type, declared=declared, needs_backticks=needs_backticks,
                           sample_count=sample_count, meaning=meaning, unit_key=unit_key, role=role,
                           top_values=None if top_values is None else tuple(top_values),
                           top_counts=None if top_counts is None else tuple(top_counts), num_min=num_min,
                           num_max=num_max, date_min=date_min, date_max=date_max)


def detail(title, attributes=(), *, label=None, name=None, summary=None, clade=None, sample_count=0,
           curated_parents=None, curated_children=None, never_filled=0):
    return SimpleNamespace(title=title, label=label or "T_" + re.sub(r"[^A-Za-z0-9_]", "_", title), name=name,
                           summary=summary, clade=clade, sample_count=sample_count, curated_parents=curated_parents,
                           curated_children=curated_children, attributes=tuple(attributes),
                           never_filled=never_filled)


def snapshot(index=(), *, has_usage=True):
    return SimpleNamespace(catalog_hash="h1", synced_at=None, has_usage=has_usage, index=tuple(index), guard={})


def vocab(**overrides):
    fields = dict(investigation_titles=("Impact", "MetNet"), project_titles=("BioMicroCenter",),
                  study_titles=("A paper about lungs",),
                  published_studies=({"title": "A paper about lungs", "DOI": "10.1/x", "PMID": "123"},),
                  assay_titles=("Bulk RNA Sequencing",), protocol_titles=("P.ABC-protocol.pdf",),
                  assay_connections=({"assay": "Bulk RNA Sequencing", "parent_type": "RNA",
                                      "child_type": "D.SEQ"},))
    fields.update(overrides)
    return SimpleNamespace(**fields)


def tis_detail(**overrides):
    fields = dict(
        name="Tissue Sample", clade="Source", sample_count=107412,
        summary="A piece of tissue taken from an organism. It is later processed into DNA or RNA.",
        curated_parents="PAV | NHP", curated_children="DNA | RNA", never_filled=7,
    )
    fields.update(overrides)
    attributes = fields.pop("attributes", (
        attr("Organ", sample_count=16841, meaning="Organ of origin; the anatomical site.",
             top_values=("Lung", "lung"), top_counts=(16841, 5893)),
    ))
    return detail("TIS", attributes, **fields)


def attribute_lines(text):
    return [line for line in text.splitlines() if line.startswith("- ")]


# ---------------------------------------------------------------------------------------------------------------
# Constants and purity
# ---------------------------------------------------------------------------------------------------------------

def test_constants():
    assert gc.BUDGET_BYTES == 32_768
    assert gc.K_STEPS == (25, 15, 10, 0)
    assert (gc.MAX_TYPES, gc.MEANING_MAX, gc.VALUE_MAX) == (3, 120, 60)
    assert gc.STRUCTURE_PATH.name == "graph_schema_structure.txt"
    assert gc.STRUCTURE_PATH.parent.name == "prompts"
    assert gc.STRUCTURE_PATH.is_file()
    # P7c: the vocabulary blocks have a budget of their own, half the schema's. Measured over the 181 questions
    # of PilotAPOC/stage1/vocabulary_sizes.json it binds 4, the only ones that fire the study and assay blocks
    # together (26,833 bytes); the median block is 12,203 and the 90th percentile 15,139.
    assert gc.VOCAB_BUDGET_BYTES == 16_384
    assert gc.VOCAB_BUDGET_BYTES * 2 == gc.BUDGET_BYTES


def test_module_is_pure():
    """No Neo4j, no config, no Django: only the standard library."""
    tree = ast.parse(Path(gc.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import in a pure module"
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "json", "re", "pathlib", "typing", "collections"}, imported


# ---------------------------------------------------------------------------------------------------------------
# The type index
# ---------------------------------------------------------------------------------------------------------------

def test_index_line_format():
    text = gc.render_type_index([index_row("TIS", name="Tissue Sample", clade="Source", sample_count=107412,
                                           attributes_with_values=41)])
    assert 'TIS :T_TIS "Tissue Sample" clade Source, 107,412 samples, 41 attributes with values' in text.splitlines()


def test_index_omits_deprecated_types():
    text = gc.render_type_index([
        index_row("TIS", sample_count=3, attributes_with_values=2),
        index_row("OLD", sample_count=9, attributes_with_values=1, deprecated=True),
    ])
    assert "TIS :T_TIS" in text
    assert "OLD" not in text


def test_index_flags_zero_sample_types():
    text = gc.render_type_index([index_row("A.EXPR", clade="Analyzed", sample_count=0)])
    assert "A.EXPR :T_A_EXPR clade Analyzed, no samples, 0 attributes with values" in text.splitlines()


def test_index_line_without_name_or_clade_and_singulars():
    text = gc.render_type_index([index_row("D.SEQ", sample_count=1, attributes_with_values=1)])
    assert "D.SEQ :T_D_SEQ, 1 sample, 1 attribute with values" in text.splitlines()


def test_index_keeps_input_order_and_has_a_heading():
    text = gc.render_type_index([index_row("TIS", sample_count=2), index_row("A.ALN", sample_count=5)])
    lines = text.splitlines()
    assert lines[0].startswith("## ")
    assert [line.split()[0] for line in lines[1:]] == ["TIS", "A.ALN"]


# ---------------------------------------------------------------------------------------------------------------
# A resolved type's section
# ---------------------------------------------------------------------------------------------------------------

def test_section_header_summary_and_curated_lines():
    lines = gc.render_type_section(tis_detail(), 25).splitlines()
    assert lines[0] == '### TIS :T_TIS "Tissue Sample", clade Source, 107,412 samples'
    assert lines[1] == "A piece of tissue taken from an organism."
    assert "Curated parents: PAV | NHP" in lines
    assert "Curated children: DNA | RNA" in lines


def test_section_header_zero_samples_and_missing_optional_fields():
    text = gc.render_type_section(detail("A.EXPR", sample_count=0), 25)
    assert text.splitlines()[0] == "### A.EXPR :T_A_EXPR, no samples"
    assert "Curated parents" not in text
    assert "Curated children" not in text


def test_attribute_line_format():
    lines = attribute_lines(gc.render_type_section(tis_detail(), 25))
    assert lines == ['- Organ [string] n=16,841 | values: "Lung" 16,841, "lung" 5,893 | Organ of origin']


def test_attribute_backticks_and_undeclared():
    d = detail("TIS", [attr("Catalog#", needs_backticks=True, sample_count=3),
                       attr("Freezer", declared=False, sample_count=2)])
    lines = attribute_lines(gc.render_type_section(d, 25))
    assert lines == ["- `Catalog#` [string] n=3", "- Freezer [string] n=2 (undeclared)"]


def test_attribute_unit_names_the_attribute_it_qualifies():
    d = detail("TIS", [attr("CellCountUnits", role="unit", unit_key="12:CellCount", sample_count=4)])
    assert attribute_lines(gc.render_type_section(d, 25)) == ["- CellCountUnits [string] n=4 | unit of CellCount"]


def test_attribute_ranges_for_numbers_and_dates():
    d = detail("D.SEQ", [
        attr("F_bp", value_type="float", sample_count=4288, num_min=20.0, num_max=150.0),
        attr("Conc", value_type="float", sample_count=12, num_min=0.5, num_max=2.25),
        attr("Collected", value_type="date", sample_count=9, date_min="2020-01-01", date_max="2024-12-31"),
    ])
    assert attribute_lines(gc.render_type_section(d, 25)) == [
        "- F_bp [float] n=4,288 | range 20..150",
        "- Conc [float] n=12 | range 0.5..2.25",
        "- Collected [date] n=9 | range 2020-01-01..2024-12-31",
    ]


def test_values_over_sixty_characters_are_skipped():
    long_value = "x" * 61
    d = detail("TIS", [
        attr("Notes", sample_count=5, top_values=(long_value, "short"), top_counts=(4, 1)),
        attr("Path", sample_count=3, top_values=(long_value,), top_counts=(3,)),
        attr("Edge", sample_count=2, top_values=("y" * 60,), top_counts=(2,)),
    ])
    lines = attribute_lines(gc.render_type_section(d, 25))
    assert lines[0] == '- Notes [string] n=5 | values: "short" 1'
    assert lines[1] == "- Path [string] n=3"
    assert lines[2] == '- Edge [string] n=2 | values: "' + "y" * 60 + '" 2'


def test_values_part_absent_without_catalog_values():
    d = detail("TIS", [attr("Organ", sample_count=5)])
    assert attribute_lines(gc.render_type_section(d, 25)) == ["- Organ [string] n=5"]


def test_at_most_ten_values_per_attribute():
    values = tuple(f"v{i}" for i in range(15))
    d = detail("TIS", [attr("Organ", sample_count=99, top_values=values, top_counts=tuple(range(15, 0, -1)))])
    line = attribute_lines(gc.render_type_section(d, 25))[0]
    assert '"v9"' in line
    assert '"v10"' not in line


def test_meaning_is_the_first_clause_within_120_characters():
    assert gc.first_clause("Organ of origin; the anatomical site.") == "Organ of origin"
    assert gc.first_clause("The format of the file, e.g. FASTQ. Other text.") == "The format of the file, e.g. FASTQ"
    assert gc.first_clause("  Read length   in base pairs. ") == "Read length in base pairs"
    assert gc.first_clause("Concentration of 2.5 mg per ml.") == "Concentration of 2.5 mg per ml"
    long = gc.first_clause("word " * 60)
    assert len(long) <= 120 and long.endswith("...")
    assert gc.first_clause(None) is None
    assert gc.first_clause("   ") is None


def test_rendered_meaning_never_exceeds_the_limit():
    d = detail("TIS", [attr("Organ", sample_count=5, meaning="a" * 400)])
    meaning = attribute_lines(gc.render_type_section(d, 25))[0].split(" | ")[-1]
    assert len(meaning) <= gc.MEANING_MAX


def test_k_most_filled_in_full_then_one_also_filled_line():
    attributes = [attr(f"A{i:02d}", sample_count=1000 - i) for i in range(30)]
    attributes.reverse()  # the renderer orders by fill, not by input order
    text = gc.render_type_section(detail("TIS", attributes), 25)
    assert [line.split()[1] for line in attribute_lines(text)] == [f"A{i:02d}" for i in range(25)]
    assert "also filled: A25 n=975, A26 n=974, A27 n=973, A28 n=972, A29 n=971" in text.splitlines()


def test_no_also_filled_line_when_everything_fits():
    text = gc.render_type_section(detail("TIS", [attr("Organ", sample_count=3)]), 25)
    assert "also filled" not in text


def test_names_only_at_k_zero():
    attributes = [attr("Organ", sample_count=9, meaning="Organ"), attr("Catalog#", needs_backticks=True,
                                                                        sample_count=3)]
    text = gc.render_type_section(detail("TIS", attributes), 0)
    assert attribute_lines(text) == []
    assert "filled: Organ n=9, `Catalog#` n=3" in text.splitlines()


def test_never_filled_count_line():
    assert "7 declared attributes hold no value" in gc.render_type_section(tis_detail(), 25).splitlines()
    assert "1 declared attribute holds no value" in gc.render_type_section(tis_detail(never_filled=1), 25)
    assert "hold no value" not in gc.render_type_section(tis_detail(never_filled=0), 25)


def test_empty_attributes_and_uid_are_not_rendered():
    d = detail("TIS", [attr("Organ", sample_count=5), attr("Empty", sample_count=0), attr("UID", sample_count=5,
                                                                                        role="identifier")])
    text = gc.render_type_section(d, 25)
    assert attribute_lines(text) == ["- Organ [string] n=5"]
    assert "Empty" not in text
    assert "UID" not in text


def test_only_catalog_attributes_appear():
    names = {f"Attr{i}" for i in range(40)}
    d = detail("TIS", [attr(n, sample_count=i + 1) for i, n in enumerate(sorted(names))])
    text = gc.render_type_section(d, 25)
    rendered = {line.split()[1] for line in attribute_lines(text)}
    also = next(line for line in text.splitlines() if line.startswith("also filled: "))
    rendered |= {entry.split(" n=")[0] for entry in also[len("also filled: "):].split(", ")}
    assert rendered == names


def test_tail_carries_the_sample_count():
    """P7a: ``_filled`` sorts by count, so the names-only tail is exactly the sparse attributes.

    Replayed against the live catalog: CEL has 71 attributes holding a value and ``CellLine`` is the 29th, so at
    K=25 the agent met it as a bare name in the tail. It wrote ``toLower(s.CellLine) CONTAINS 'hela'``, got
    nothing and reported the 0 as fact against a true 4. ``CellLine n=287`` of 3,288 CEL samples is the signal
    it did not have.

    Not every wrong-field failure is this one. BAC holds 24 filled attributes, so ``Strain [string] n=19`` was
    already in the K=25 head when the agent guessed it for mTB and returned 0 against a true 2,999: that failure
    is P1's and P2's, and the proposal's mTB evidence for P7a does not survive the replay.
    """
    attributes = [attr(f"Common{i:02d}", sample_count=3288 - i) for i in range(28)]
    attributes.append(attr("CellLine", sample_count=287))
    text = gc.render_type_section(detail("CEL", attributes, sample_count=3288), 25)
    tail = next(line for line in text.splitlines() if line.startswith("also filled: "))
    assert tail.endswith("CellLine n=287")
    assert "CellLine" not in "\n".join(attribute_lines(text))


def test_tail_entry_without_a_known_count_is_the_bare_name():
    d = detail("BAC", [attr("Organ", sample_count=5), attr("Strain", sample_count=None)])
    assert "also filled: Strain" in gc.render_type_section(d, 1).splitlines()


def test_tail_counts_are_grouped_by_thousands():
    d = detail("BAC", [attr("Organ", sample_count=22734), attr("Strain", sample_count=1234)])
    assert "also filled: Strain n=1,234" in gc.render_type_section(d, 1).splitlines()


def test_rendering_without_values_meanings_or_usage():
    d = detail("TIS", [attr("Organ", sample_count=5, top_values=None, top_counts=None, meaning=None)],
               summary=None, name=None, clade=None, sample_count=None)
    text = gc.render_graph_context(snapshot([index_row("TIS", sample_count=None)], has_usage=False), [d])
    assert "- Organ [string] n=5" in text.splitlines()
    assert "### TIS :T_TIS" in text


# ---------------------------------------------------------------------------------------------------------------
# resolved_type_codes
# ---------------------------------------------------------------------------------------------------------------

KNOWN = {"TIS", "D.SEQ", "A.VCF", "MUS", "NHP"}


def test_codes_plan_first_then_entity():
    plan = {"resolved": {"sampletypes": [{"code": "D.SEQ"}, {"code": "TIS"}]},
            "filters": {"sampletype_code": "MUS"}}
    entity = {"sampletypes": [{"code": "NHP"}]}
    assert gc.resolved_type_codes(plan, entity, KNOWN) == ["D.SEQ", "TIS", "MUS"]


def test_codes_filter_code_counts_as_plan():
    plan = {"resolved": {"sampletypes": []}, "filters": {"sampletype_code": "TIS"}}
    assert gc.resolved_type_codes(plan, {"sampletypes": [{"code": "NHP"}]}, KNOWN) == ["TIS", "NHP"]


def test_codes_drop_unknown_dedupe_and_cap_at_three():
    plan = {"resolved": {"sampletypes": [{"code": "XYZ"}, {"code": "TIS"}, {"code": "TIS"}]},
            "filters": {"sampletype_code": "TIS"}}
    entity = {"sampletypes": [{"code": "NHP"}, {"code": "MUS"}, {"code": "A.VCF"}]}
    assert gc.resolved_type_codes(plan, entity, KNOWN) == ["TIS", "NHP", "MUS"]


def test_codes_from_entity_alone_and_from_nothing():
    assert gc.resolved_type_codes(None, {"sampletypes": [{"code": "MUS"}]}, KNOWN) == ["MUS"]
    assert gc.resolved_type_codes({}, None, KNOWN) == []
    assert gc.resolved_type_codes(None, None, KNOWN) == []


def test_codes_accept_models_and_plain_strings():
    class Model:
        def __init__(self, data):
            self._data = data

        def model_dump(self):
            return self._data

    plan = Model({"resolved": {"sampletypes": ["A.VCF"]}, "filters": {"sampletype_code": None}})
    assert gc.resolved_type_codes(plan, Model({"sampletypes": [{"code": "TIS", "name": "x"}]}), KNOWN) == [
        "A.VCF", "TIS"]


# ---------------------------------------------------------------------------------------------------------------
# render_graph_context and the budget
# ---------------------------------------------------------------------------------------------------------------

def big_type(code, n_attributes=190):
    attributes = [
        attr(f"{code}_attribute_{i:03d}", sample_count=100_000 - i, meaning="m" * 120,
             top_values=tuple(f"value {i:03d} {j:02d}" for j in range(10)),
             top_counts=tuple(range(1000, 990, -1)))
        for i in range(n_attributes)
    ]
    return detail(code, attributes, name=f"Type {code}", clade="Processed", sample_count=100_000,
                  summary="A synthetic type.", curated_parents="NHP", curated_children="DNA", never_filled=4)


def full_index(extra=()):
    rows = [index_row(f"T{i:03d}", name=f"Synthetic type {i}", clade="Analyzed", sample_count=1000 + i,
                      attributes_with_values=20) for i in range(115)]
    return rows + list(extra)


def section_attribute_counts(text):
    counts, current = {}, None
    for line in text.splitlines():
        if line.startswith("### "):
            current = line.split()[1]
            counts[current] = 0
        elif line.startswith("- ") and current is not None:
            counts[current] += 1
    return counts


def test_three_large_types_step_k_down_to_fit_the_budget():
    details = [big_type("TIS"), big_type("D.SEQ"), big_type("A.VCF")]
    snap = snapshot(full_index([index_row(d.title, sample_count=100_000, attributes_with_values=190)
                                for d in details]))
    unbounded = gc.render_graph_context(snap, details, budget=10**9)
    assert len(unbounded.encode("utf-8")) > gc.BUDGET_BYTES  # K 25 alone would not fit
    text = gc.render_graph_context(snap, details)
    assert len(text.encode("utf-8")) <= gc.BUDGET_BYTES
    counts = section_attribute_counts(text)
    assert set(counts) == {"TIS", "D.SEQ", "A.VCF"}  # K dropped before any section was
    assert len(set(counts.values())) == 1 and next(iter(counts.values())) < 25


def test_small_type_renders_at_k_25():
    d = detail("TIS", [attr(f"A{i:02d}", sample_count=100 - i) for i in range(30)], sample_count=100)
    text = gc.render_graph_context(snapshot(full_index([index_row("TIS", sample_count=100)])), [d])
    assert section_attribute_counts(text) == {"TIS": 25}


def resolved_heading(text):
    return next(line for line in text.splitlines() if line.startswith("## Resolved sample types:"))


def test_resolved_heading_says_the_tail_carries_counts():
    d = detail("TIS", [attr(f"A{i:02d}", sample_count=100 - i) for i in range(30)], sample_count=100)
    snap = snapshot([index_row("TIS", sample_count=100)])
    assert "then the rest by name with its sample count" in resolved_heading(gc.render_graph_context(snap, [d]))
    assert "attribute names only, each with its sample count" in resolved_heading(
        gc.render_graph_context(snap, [d], k=0))


def test_resolved_heading_still_parses_for_the_venue_check():
    """scripts/graph_search/nessie_venue_check.py reads K out of this heading; both prefixes must survive."""
    venue = re.compile(r"^## Resolved sample types: .*?\((?:the (\d+) most-filled|(attribute names only))", re.M)
    snap, d = snapshot([index_row("TIS", sample_count=5)]), detail("TIS", [attr("Organ", sample_count=5)])
    assert venue.search(gc.render_graph_context(snap, [d])).group(1) == "25"
    assert venue.search(gc.render_graph_context(snap, [d], k=0)).group(2) == "attribute names only"


def test_context_is_structure_then_index_then_sections():
    structure = gc.STRUCTURE_PATH.read_text(encoding="utf-8").strip()
    text = gc.render_graph_context(snapshot([index_row("TIS", sample_count=5)]), [tis_detail()])
    assert text.startswith(structure)
    assert text.index("TIS :T_TIS") < text.index("### TIS :T_TIS")


def test_no_resolved_types_sends_structure_and_index():
    text = gc.render_graph_context(snapshot([index_row("TIS", sample_count=5)]), [])
    assert "###" not in text
    assert "TIS :T_TIS, 5 samples" in text


def test_at_most_three_sections():
    details = [detail(code, [attr("Organ", sample_count=1)]) for code in ("TIS", "MUS", "NHP", "PAV")]
    text = gc.render_graph_context(snapshot([]), details)
    assert list(section_attribute_counts(text)) == ["TIS", "MUS", "NHP"]


def test_sections_are_dropped_from_the_end_only_after_names_only():
    details = [big_type("TIS"), big_type("D.SEQ"), big_type("A.VCF")]
    snap = snapshot([])
    structure_only = gc.render_graph_context(snap, [], budget=10**9)
    one_names_only = gc.render_graph_context(snap, details[:1], k=0, budget=10**9)
    budget = len(one_names_only.encode("utf-8")) + 200
    text = gc.render_graph_context(snap, details, budget=budget)
    assert len(text.encode("utf-8")) <= budget
    assert list(section_attribute_counts(text)) == ["TIS"]
    assert "D.SEQ, A.VCF" in text  # named as left out
    tiny = gc.render_graph_context(snap, details, budget=10)
    assert tiny.startswith(structure_only.rstrip())
    assert "###" not in tiny


# ---------------------------------------------------------------------------------------------------------------
# Vocabulary blocks
# ---------------------------------------------------------------------------------------------------------------

def test_vocabulary_always_has_investigations_and_projects():
    text = gc.render_vocabulary(vocab(), "How many tissue samples are there?")
    assert '"Impact"' in text and '"MetNet"' in text
    assert '"BioMicroCenter"' in text
    assert "A paper about lungs" not in text
    assert "Bulk RNA Sequencing" not in text
    assert "P.ABC-protocol.pdf" not in text


@pytest.mark.parametrize("question", [
    "Which study used TIS samples?", "Samples in these studies", "What paper used them?",
    "Is there a publication?", "Samples for DOI 10.1/x", "Samples in PMID 123",
])
def test_vocabulary_studies_on_study_words(question):
    text = gc.render_vocabulary(vocab(), question)
    assert '"A paper about lungs"' in text
    assert "10.1/x" in text and "123" in text


@pytest.mark.parametrize("question", ["Which samples underwent an assay?", "Show sequencing data",
                                      "Samples processed by flow"])
def test_vocabulary_assays_on_assay_words(question):
    text = gc.render_vocabulary(vocab(), question)
    assert '"Bulk RNA Sequencing"' in text
    assert "RNA -> D.SEQ" in text


@pytest.mark.parametrize("question", ["Which protocol was used?", "What method made these?"])
def test_vocabulary_protocols_on_protocol_words(question):
    assert '"P.ABC-protocol.pdf"' in gc.render_vocabulary(vocab(), question)


def test_vocabulary_word_lists_match_the_graph_agent():
    assert gc.PROTOCOL_WORDS == ("protocol", "method", "procedure", "technique")
    assert gc.ASSAY_WORDS == ("assay", "sequencing", "cytometry", "spectrometry", "imaging", "data", "processed",
                              "associated", "underwent", "via", "collection", "extraction")


def test_vocabulary_omits_empty_blocks():
    text = gc.render_vocabulary(vocab(investigation_titles=(), project_titles=(), study_titles=(),
                                      published_studies=(), assay_titles=(), protocol_titles=(),
                                      assay_connections=()), "study assay protocol")
    assert text == ""


# The 60 distinct questions of the 2026-09-17 graph-versus-API run (PilotAPOC/review/compare_export.json), one
# per line and verbatim: they are run data, so they are not wrapped and not edited.
# The assay gate's ``word in question`` test fired on 18 of them.
POC_QUESTIONS = (
    "Do we have any ChIP-seq datasets?",
    "Do we have any western blot data",
    "Find PBMCs that were sequenced using single cell methods.",
    "Find RNA samples with a RIN score greater than 7.",
    "Find all NHP samples in the database",
    "Find bacteria samples with strain mTB.",
    "Find every sample whose Scientist is Owen Leddy, and break it down by sample type.",
    "Find female mouse samples.",
    "Find me DNA samples",
    "Find me RNA samples",
    "Find me all CD8 antibodies in the database.",
    "Find me all fibrin images on omero",
    "Find me all monkeys in the database",
    "Find me all of the fibrin images that are on omero",
    "Find me all samples associated with CD8 Antibodies",
    "Find me cd8 antibodies",
    "Find me cd8 depleted monkeys",
    "Find me cell line samples",
    "Find me extravasation images",
    "Find me images associated with fibrin",
    "Find me mice associated with ndma",
    "Find me mice treated with NDMA.",
    "Find me mice treated with mTB",
    "Find me mice treated with tuberculosis",
    "Find me monkeys",
    "Find me ndma treated mice",
    "Find me samples associated with cd8 depletion",
    "Find me scRNA-seq clustering results",
    "Find mice treated with 50mg NDMA for 6 weeks from the water study",
    "Find mice treated with 50mg NDMA from the water study",
    "Find mice treated with NDMA",
    "Find tissue samples with organ type Liver",
    "How many CometChip imaging datasets are there?",
    "How many HeLa cell-line samples do we have?",
    "How many PBMC samples do we have?",
    "How many RNA samples does the Kamm lab have?",
    "How many male patient samples are there?",
    "How many organ on chips exist in the Kamm lab",
    "How many samples are from the Kamm lab?",
    "How many samples are in the database?",
    "How many samples list ImmPort as their repository, across every spelling?",
    "How many samples, of any sample type, have TIS-220831FLY-26 inside their UID, whether or not that is the whole UID?",
    "How many samples, of any sample type, have the UID exactly TIS-220831FLY-26?",
    "How many sequencing samples used an amplicon library strategy?",
    "How many tissue (TIS) samples have 21619 somewhere in their Name?",
    "How many tissue (TIS) samples have the UID exactly TIS-220831FLY-26?",
    "How many tissue (TIS) samples have their Organ recorded as just Lung, with nothing else in that field?",
    "How many tissue (TIS) samples mention lung in the Organ field, counting longer values such as Bronchus and lung as well?",
    "I don't trust the sequencing-sample number — work out how many D.SEQ samples there are from scratch and show me your method.",
    "Show me all FACS data for the monkeys",
    "Some of these mouse genotype terms look like the same thing written differently — which ones should be merged, and what should each become?",
    "The AB sample type declares an attribute called Catalog# — is every antibody record actually using that exact key?",
    "The Scientist field looks like it has the same people entered under different names. Which entries are duplicates of each other, and what should each one be?",
    "What GPT Data is in the database",
    "What GPT data exists in the database",
    "What fibrin images exist",
    "What fibrin images exist in the database",
    "What is the difference between a D.SEQ sample and an A.SCXP sample?",
    "What monkeys exist in the database?",
    "what about organ on chips in the kamm lab?",
)

# P7c: the seven the substring test fired on with no assay language at all. Every one is "data" inside
# "database" or "datasets"; none names an assay, a platform or a processing step.
POC_ASSAY_SUBSTRING_ONLY = (
    "Do we have any ChIP-seq datasets?",
    "Find all NHP samples in the database",
    "Find me all CD8 antibodies in the database.",
    "Find me all monkeys in the database",
    "How many samples are in the database?",
    "What fibrin images exist in the database",
    "What monkeys exist in the database?",
)


def assay_gate(question):
    return "ASSAY TITLES" in gc.render_vocabulary(vocab(), question)


def protocol_gate(question):
    return "PROTOCOL TITLES" in gc.render_vocabulary(vocab(), question)


def test_assay_gate_no_longer_fires_on_a_substring():
    for question in POC_ASSAY_SUBSTRING_ONLY:
        assert not assay_gate(question), question


def test_assay_gate_fires_on_eleven_of_the_sixty_poc_questions():
    assert len(POC_QUESTIONS) == 60
    substring = [q for q in POC_QUESTIONS if any(word in q.lower() for word in gc.ASSAY_WORDS)]
    firing = [q for q in POC_QUESTIONS if assay_gate(q)]
    assert len(substring) == 18  # what shipped
    assert len(firing) == 11
    assert set(substring) == set(firing) | set(POC_ASSAY_SUBSTRING_ONLY)  # nothing else changed


def test_the_protocol_gate_is_unchanged_on_the_poc_questions():
    assert len([q for q in POC_QUESTIONS if protocol_gate(q)]) == 2


def test_a_gate_word_still_matches_its_plural():
    assert assay_gate("which assays ran on these")
    assert protocol_gate("Find PBMCs that were sequenced using single cell methods.")
    assert protocol_gate("which procedures apply")


def test_a_gate_word_no_longer_matches_a_longer_word():
    assert not assay_gate("how many samples are in the database?")
    assert not protocol_gate("which methodology applies")


def test_mentions_is_the_shared_whole_word_gate():
    """Exported so agents/graph.py's fallback path can drop its own ``kw in query`` test onto it."""
    assert gc.mentions(gc.ASSAY_WORDS, "Show sequencing data")
    assert not gc.mentions(gc.ASSAY_WORDS, "all samples in the database")
    assert gc.mentions(("assay",), "ASSAYS")
    assert not gc.mentions((), "an empty gate matches nothing")
    assert not gc.mentions(gc.ASSAY_WORDS, None)


# --- the vocabulary byte budget (P7c) -----------------------------------------------------------------------------

BUDGET_QUESTION = "which assays and studies used which protocol"


def big_vocab(n=2000):
    return vocab(study_titles=tuple(f"Study {i:04d} of the lung cohort" for i in range(n)),
                 assay_titles=tuple(f"Assay {i:04d} sequencing panel" for i in range(n)),
                 protocol_titles=tuple(f"P.{i:04d}-protocol-document.pdf" for i in range(n)),
                 published_studies=tuple({"title": f"Study {i:04d}", "DOI": f"10.1/{i}", "PMID": str(i)}
                                         for i in range(n)),
                 assay_connections=tuple({"assay": f"Assay {i:04d}", "parent_type": "RNA", "child_type": "D.SEQ"}
                                         for i in range(n)))


def test_vocabulary_had_no_budget_and_now_has_one():
    unbounded = gc.render_vocabulary(big_vocab(), BUDGET_QUESTION, budget=10**9)
    assert len(unbounded.encode("utf-8")) > gc.VOCAB_BUDGET_BYTES
    text = gc.render_vocabulary(big_vocab(), BUDGET_QUESTION)
    assert len(text.encode("utf-8")) <= gc.VOCAB_BUDGET_BYTES


def test_the_budget_trims_the_lists_before_it_drops_a_block():
    text = gc.render_vocabulary(big_vocab(), BUDGET_QUESTION)
    for heading in ("INVESTIGATION TITLES", "PROJECT TITLES", "STUDY TITLES", "PUBLISHED STUDIES",
                    "ASSAY TITLES", "ASSAY-SAMPLE CONNECTIONS", "PROTOCOL TITLES"):
        assert heading in text


def test_the_budget_keeps_as_much_as_it_fits():
    """A fixed step ladder undershot: on the live catalog it gave up 33 of 133 assay titles to save 2 KiB."""
    text = gc.render_vocabulary(big_vocab(), BUDGET_QUESTION)
    assert gc.VOCAB_BUDGET_BYTES - 400 <= len(text.encode("utf-8")) <= gc.VOCAB_BUDGET_BYTES


def test_a_trimmed_block_says_how_many_it_left_out():
    text = gc.render_vocabulary(big_vocab(), BUDGET_QUESTION)
    left_out = [line for line in text.splitlines() if line.startswith("... and ")]
    assert left_out and all(line.endswith(" more") for line in left_out)
    assert any("," in line for line in left_out)  # thousands separated, as every other count is


def test_a_vocabulary_inside_the_budget_renders_exactly_as_before():
    small = vocab()
    assert gc.render_vocabulary(small, BUDGET_QUESTION) == gc.render_vocabulary(small, BUDGET_QUESTION,
                                                                               budget=10**9)


def test_blocks_are_dropped_from_the_end_when_one_item_each_is_still_too_big():
    text = gc.render_vocabulary(big_vocab(), BUDGET_QUESTION, budget=200)
    assert len(text.encode("utf-8")) <= 200
    assert "PROTOCOL TITLES" not in text
    assert text.startswith("INVESTIGATION TITLES")


def test_an_unreachable_budget_sends_nothing():
    assert gc.render_vocabulary(big_vocab(), BUDGET_QUESTION, budget=1) == ""


# ---------------------------------------------------------------------------------------------------------------
# The structure file against docs/neo4j-schema.md v1.1
# ---------------------------------------------------------------------------------------------------------------

def _doc_section(title_prefix):
    text = SCHEMA_DOC.read_text(encoding="utf-8")
    start = text.index(f"\n## {title_prefix}")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else len(text)]


def _doc_node_table(section):
    """label -> backticked property names, from a section's Nodes table."""
    table, in_nodes, props_col = {}, False, None
    for line in section.splitlines():
        if line.startswith("### "):
            in_nodes = line.strip() == "### Nodes"
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if in_nodes and line.startswith("| Label"):
            props_col = cells.index("Properties")
            continue
        if not in_nodes or props_col is None or not line.startswith("| `"):
            continue
        labels = re.findall(r"`([^`]+)`", cells[0])
        props = set(re.findall(r"`([^`]+)`", cells[props_col]))
        for label in labels:
            for single in label.split(","):
                table.setdefault(single.strip(), set()).update(props)
    return table


def _doc_labels_and_relationships():
    v11 = _doc_section("v1.1")
    labels = set(_doc_node_table(v11))
    relationships = set(re.findall(r"\[:([A-Z_]+)", v11))
    return labels, relationships


def _structure_labels(text):
    labels = set()
    for chain in re.findall(r"\(\s*[A-Za-z_]*\s*((?::[A-Za-z_][A-Za-z0-9_<>]*)+)", text):
        labels.update(part for part in chain.split(":") if part)
    labels.update(re.findall(r"(?<![A-Za-z0-9_`'\"\[(]):([A-Za-z_][A-Za-z0-9_<>]*)", text))
    return labels


def test_structure_names_only_v11_labels():
    doc_labels, _ = _doc_labels_and_relationships()
    assert {"Sample", "T_<code>", "SampleType", "Attribute", "Study", "Project"} <= doc_labels  # the parse works
    text = gc.STRUCTURE_PATH.read_text(encoding="utf-8")
    labels = _structure_labels(text)
    assert {"Sample", "SampleType", "Attribute", "Study", "Investigation", "Project", "Person"} <= labels
    unknown = {lab for lab in labels if lab not in doc_labels and not lab.startswith("T_")}
    assert not unknown, unknown


def test_structure_names_only_v11_relationships():
    _, doc_relationships = _doc_labels_and_relationships()
    assert "CHILD_OF" not in doc_relationships and "DERIVED_FROM" in doc_relationships
    text = gc.STRUCTURE_PATH.read_text(encoding="utf-8")
    used = set(re.findall(r"\[\s*\w*\s*:([A-Z_]+)", text))
    assert used == doc_relationships, used ^ doc_relationships
    shouted = {tok for tok in re.findall(r"\b[A-Z]+(?:_[A-Z]+)+\b", text) if not tok.startswith("T_")}
    assert shouted - {"CHILD_OF"} <= doc_relationships


def test_structure_mentions_child_of_only_as_absent():
    text = " ".join(gc.STRUCTURE_PATH.read_text(encoding="utf-8").split())
    sentences = re.split(r"(?<=[.!?])\s+", text)
    mentions = [s for s in sentences if "CHILD_OF" in s]
    assert mentions, "the structure should say CHILD_OF does not exist"
    assert all("does not exist" in s for s in mentions), mentions


def test_structure_node_properties_are_in_the_doc():
    doc = _doc_node_table(_doc_section("v1.0"))
    for label, props in _doc_node_table(_doc_section("v1.1")).items():
        doc.setdefault(label, set()).update(props)
    doc["Study"].add("seek_study_id")  # the v1.1 row names it in prose, next to "as v1.0"
    text = gc.STRUCTURE_PATH.read_text(encoding="utf-8")
    for label, body in re.findall(r"\(:([A-Za-z]+) \{([^}]*)\}\)", " ".join(text.split())):
        props = {p.split(":")[0].strip() for p in body.split(",") if p.strip()}
        assert props <= doc[label], (label, props - doc[label])


def test_structure_is_compact():
    assert len(gc.STRUCTURE_PATH.read_bytes()) <= 4096
