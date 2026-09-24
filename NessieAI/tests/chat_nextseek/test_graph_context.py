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
         unit_key=None, role="data", num_min=None, num_max=None, date_min=None, date_max=None):
    return SimpleNamespace(title=title, value_type=value_type, declared=declared, needs_backticks=needs_backticks,
                           sample_count=sample_count, meaning=meaning, unit_key=unit_key, role=role, num_min=num_min,
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
        attr("Organ", sample_count=16841, meaning="Organ of origin; the anatomical site."),
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
    assert (gc.MAX_TYPES, gc.MEANING_MAX) == (3, 120)
    assert gc.STRUCTURE_PATH.name == "graph_schema_structure.txt"
    assert gc.STRUCTURE_PATH.parent.name == "prompts"
    assert gc.STRUCTURE_PATH.is_file()


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
    assert lines == ["- Organ [string] n=16,841 | Organ of origin"]


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


def test_values_part_absent_without_catalog_values():
    """No Attribute node ever carried values, so the line never has a values part."""
    d = detail("TIS", [attr("Organ", sample_count=5)])
    assert attribute_lines(gc.render_type_section(d, 25)) == ["- Organ [string] n=5"]


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


def test_the_names_only_tail_can_leave_its_counts_off():
    attributes = [attr(f"A{i:02d}", sample_count=1000 - i) for i in range(30)]
    text = gc.render_type_section(detail("TIS", attributes), 25, tail_counts=False)
    assert "also filled: A25, A26, A27, A28, A29" in text.splitlines()


def test_a_tail_attribute_with_no_known_count_is_a_bare_name():
    attributes = [attr(f"A{i:02d}", sample_count=1000 - i) for i in range(25)]
    attributes += [attr("Sparse", sample_count=12), attr("Unknown", sample_count=None)]
    text = gc.render_type_section(detail("TIS", attributes), 25)
    assert "also filled: Sparse n=12, Unknown" in text.splitlines()


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


def test_rendering_without_values_meanings_or_usage():
    d = detail("TIS", [attr("Organ", sample_count=5, meaning=None)],
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
        attr(f"{code}_attribute_{i:03d}", sample_count=100_000 - i, meaning="m" * 120)
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


def test_vocabulary_omits_empty_blocks():
    text = gc.render_vocabulary(vocab(investigation_titles=(), project_titles=(), study_titles=(),
                                      published_studies=(), assay_titles=(), protocol_titles=(),
                                      assay_connections=()), "study assay protocol")
    assert text == ""


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


def test_structure_derived_from_properties_are_the_v12_labels():
    # An edge several assays share names one in internal_assay_title and all in internal_assay_titles, so the
    # plural has to be in the structure or the agent filters on the singular and misses the others.
    labels = _doc_section("v1.2")
    labels = labels[labels.index("### DERIVED_FROM labels"):labels.index("\nRules:")]
    doc = {name for row in labels.splitlines() if row.startswith("| `") for name in re.findall(r"`([^`]+)`", row)}
    text = " ".join(gc.STRUCTURE_PATH.read_text(encoding="utf-8").split())
    (body,) = re.findall(r"\[:DERIVED_FROM \{([^}]*)\}\]", text)
    props = {p.strip() for p in body.split(",")}
    assert {"internal_assay_title", "internal_assay_titles", "protocol_title"} <= props <= doc, (props, doc)


def test_structure_is_compact():
    # This block goes into every graph turn's context, so it stays budgeted. F1 promoted the
    # measured structure, which is larger than the previous default: the ceiling moved once,
    # deliberately, to the size that was measured, not to whatever the file happens to be. It
    # moved again, by the operator's ruling of 2026-09-24, for the DERIVED_FROM assay list and its
    # test: an edge several assays share names only one in internal_assay_title.
    assert len(gc.STRUCTURE_PATH.read_bytes()) <= 5400


# ---------------------------------------------------------------------------------------------------------------
# The names-only tail's counts, on a heavy synthetic triple
# ---------------------------------------------------------------------------------------------------------------

VENUE_RESOLVED_RE = re.compile(r"^## Resolved sample types: .*?\((?:the (\d+) most-filled|(attribute names only))",
                               re.M)  # scripts/graph_search/nessie_venue_check.py reads K back with this


def heavy_type(code, n_attributes):
    """A synthetic type shaped like the heaviest: a full head of 25 lines, then a long, sparse named tail."""
    attributes = [
        attr(f"{code}_Measured_Property_{i:03d}", sample_count=50_000 - i,
             meaning="Synthetic meaning of it. More.")
        for i in range(25)
    ]
    attributes += [attr(f"{code}_Sparse_{i:03d}", sample_count=900 - i) for i in range(n_attributes - 25)]
    return detail(code, attributes, name=f"Heavy {code}", clade="Source", sample_count=50_000,
                  summary="A synthetic heavy type.", curated_parents="NHP", curated_children="DNA", never_filled=9)


def heavy_triple():
    details = [heavy_type("HVA", 180), heavy_type("HVB", 140), heavy_type("HVC", 80)]
    snap = snapshot(full_index([index_row(d.title, sample_count=50_000, attributes_with_values=len(d.attributes))
                                for d in details]))
    return snap, details


def test_the_heading_says_the_tail_carries_counts_and_the_venue_check_still_reads_k():
    attributes = [attr(f"A{i:02d}", sample_count=100 - i) for i in range(30)]
    snap = snapshot([index_row("TIS", sample_count=100)])
    text = gc.render_graph_context(snap, [detail("TIS", attributes)])
    assert "(the 25 most-filled attributes in full, then the rest by name with n; per attribute:" in text
    assert VENUE_RESOLVED_RE.search(text).group(1) == "25"
    names_only = gc.render_graph_context(snap, [detail("TIS", attributes)], k=0)
    assert "(attribute names only, with n; per attribute:" in names_only
    assert VENUE_RESOLVED_RE.search(names_only).group(2) == "attribute names only"


def test_the_tail_counts_go_before_k_steps_down():
    snap, details = heavy_triple()
    whole = gc.render_graph_context(snap, details, budget=10**9)
    text = gc.render_graph_context(snap, details, budget=len(whole.encode("utf-8")) - 1)
    assert set(section_attribute_counts(text).values()) == {25}
    assert "HVA_Sparse_000, HVA_Sparse_001" in text
    assert "(the 25 most-filled attributes in full, then the rest by name; per attribute:" in text


def test_k_steps_down_when_the_counts_alone_do_not_fit_it():
    snap, details = heavy_triple()
    whole = gc.render_graph_context(snap, details, budget=10**9)
    no_counts = gc.render_graph_context(snap, details, budget=len(whole.encode("utf-8")) - 1)
    text = gc.render_graph_context(snap, details, budget=len(no_counts.encode("utf-8")) - 1)
    counts = section_attribute_counts(text)
    assert set(counts) == {"HVA", "HVB", "HVC"} and set(counts.values()) == {15}


# ---------------------------------------------------------------------------------------------------------------
# How the context fit: reported and printed, and pinned on the heavy triple
# ---------------------------------------------------------------------------------------------------------------

def test_fit_reports_how_the_context_fit():
    attributes = [attr(f"A{i:02d}", sample_count=100 - i) for i in range(30)]
    snap, details = snapshot([index_row("TIS", sample_count=100)]), [detail("TIS", attributes)]
    fit = gc.fit_graph_context(snap, details)
    assert fit.text == gc.render_graph_context(snap, details)
    assert (fit.requested_k, fit.k, fit.tail_counts, fit.omitted, fit.budget) == (25, 25, True, (), gc.BUDGET_BYTES)
    assert fit.size == len(fit.text.encode("utf-8")) and not fit.stepped_down


def test_a_heavy_triple_renders_at_k_25_with_its_tail_counts():
    """The pin the verifier asked for: a triple this heavy still gets K 25 and every count, nothing left out."""
    snap, details = heavy_triple()
    fit = gc.fit_graph_context(snap, details)
    assert fit.size > 0.85 * gc.BUDGET_BYTES  # genuinely near the budget, or the pin proves nothing
    assert (fit.k, fit.tail_counts, fit.omitted, fit.stepped_down) == (25, True, (), False)
    assert fit.size <= gc.BUDGET_BYTES
    assert "HVA_Sparse_000 n=900" in fit.text


def test_a_step_down_is_printed(capsys):
    snap, details = heavy_triple()
    capsys.readouterr()
    gc.fit_graph_context(snap, details, budget=20_000)
    out = capsys.readouterr().out
    assert "[DEBUG][GRAPH] Schema context stepped down to fit 20,000 bytes: K 25 -> " in out


def test_nothing_is_printed_when_the_context_fits(capsys):
    snap, details = heavy_triple()
    capsys.readouterr()
    gc.fit_graph_context(snap, details)
    assert "stepped down" not in capsys.readouterr().out


def test_a_left_out_section_is_reported():
    details = [big_type("TIS"), big_type("D.SEQ"), big_type("A.VCF")]
    snap = snapshot([])
    one_names_only = gc.render_graph_context(snap, details[:1], k=0, budget=10**9)
    fit = gc.fit_graph_context(snap, details, budget=len(one_names_only.encode("utf-8")) + 200)
    assert fit.omitted == ("D.SEQ", "A.VCF") and fit.k == 0 and fit.stepped_down
