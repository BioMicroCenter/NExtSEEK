"""The live graph catalog for a caller who is not a superuser: names, types and structure, no counts or values.

Spec: ``docs/superpowers/specs/2026-09-18-graph-cypher-scope.md`` sections 3.2 (S8), 8 and 11.3. The catalog's
counts, top values and ranges are computed over every project, so ``get_snapshot`` and ``get_type_details`` hand
any config that is not an admin ``GraphScope`` a redacted copy, and never touch the cached objects. A missing scope,
``None``, a plain dict and a ``MagicMock`` config all redact (fail closed).

The vocabulary (investigation, project and study titles, published studies, assay and protocol titles, assay
connections) holds record values, so a caller who is not an admin reads it through the project scope instead: its own
statements, bound to the caller's project ids, cached per set of ids. A caller who sees no project reads none of it.

Every test stubs the reader (``_make_driver`` and ``_read``): nothing reaches a driver or a Neo4j.
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest

from chat_nextseek import graph_catalog as gc
from chat_nextseek import graph_context
from chat_nextseek.agents import graph as graph_mod
from chat_nextseek.cypher_scope import SCOPE_CLAUSE_TEMPLATE
from chat_nextseek.graph_scope import SCOPE_ATTR, SCOPE_PARAM, GraphScope, with_scope

URI = "bolt://catalog-redaction:7687"

# --- the stored catalog ---------------------------------------------------------------------------------------------
# Every count, top value and range below is a value only an admin may read. Each one is distinctive, so a search of
# the rendered text for it cannot match anything else.

META = {"schema_version": "1.2", "catalog_hash": "hash-redaction", "synced_at": "2026-01-02T03:04:05Z",
        "has_usage": False}
INDEX_ROWS = [
    {"title": "CEL", "label": "T_CEL", "name": "Cells", "clade": "Source", "sample_count": 1913,
     "deprecated": False, "attributes_with_values": 0},
    {"title": "D.SEQ", "label": "T_D_SEQ", "name": "Sequencing Data", "clade": "Data", "sample_count": 6047,
     "deprecated": False, "attributes_with_values": 1},
    {"title": "OLD", "label": "T_OLD", "name": None, "clade": None, "sample_count": 0,
     "deprecated": True, "attributes_with_values": 0},
    {"title": "TIS", "label": "T_TIS", "name": "Tissue Sample", "clade": "Source", "sample_count": 58731,
     "deprecated": False, "attributes_with_values": 3},
]
GUARD_ROWS = [
    {"label": "T_TIS", "titles": ["Weight", "Organ", "Collected"]},
    {"label": "T_D_SEQ", "titles": ["ReadLength"]},
]
# Stored most filled first, as TYPES_ADMIN orders them; the titles sort the other way round.
TYPE_ROWS = {
    "TIS": {
        "title": "TIS", "label": "T_TIS", "name": "Tissue Sample", "summary": "A piece of tissue. More text.",
        "clade": "Source", "sample_count": 58731, "curated_parents": "PAT, MUS", "curated_children": "D.SEQ",
        "attributes": [
            {"title": "Weight", "value_type": "number", "declared": True, "needs_backticks": False,
             "sample_count": 5902, "meaning": "Mass of the piece in milligrams.", "unit_key": "3:WeightUnits",
             "role": "measurement", "num_min": 0.25, "num_max": 911.5},
            {"title": "Organ", "value_type": "string", "declared": True, "needs_backticks": False,
             "sample_count": 4388, "meaning": "The organ the tissue came from.", "unit_key": None,
             "role": "descriptive"},
            {"title": "Collected", "value_type": "date", "declared": False, "needs_backticks": False,
             "sample_count": 3770, "meaning": None, "unit_key": None, "role": None,
             "date_min": "2011-03-04", "date_max": "2019-08-27"},
        ],
        "never_filled": 7,
    },
    "D.SEQ": {
        "title": "D.SEQ", "label": "T_D_SEQ", "name": "Sequencing Data", "summary": None, "clade": "Data",
        "sample_count": 6047, "curated_parents": ["TIS", "CEL"], "curated_children": None,
        "attributes": [
            {"title": "ReadLength", "value_type": "integer", "declared": True, "needs_backticks": False,
             "sample_count": 4919, "num_min": 47, "num_max": 263},
        ],
        "never_filled": 0,
    },
}
VOCAB_ROWS = {
    "VOCAB_INVESTIGATIONS": [{"title": "Investigation B"}, {"title": "Investigation A"}],
    "VOCAB_PROJECTS": [{"title": "Project B"}, {"title": "Project A"}],
    "VOCAB_STUDIES": [{"title": "Study 1"}],
    "VOCAB_PUBLISHED": [{"title": "Study 1", "doi": "10.1000/example", "pmid": ""}],
    "VOCAB_EDGES": [{"assay": "Short Read Sequencing", "protocol": "RNA prep", "parent_type": "TIS",
                     "child_type": "D.SEQ"}],
}

# How each admin-only value renders (graph_context formats counts with thousands separators).
# 3,101 / 1,287 / marker-organ-a / marker-organ-b were the Organ attribute's example values.
# No Attribute node in the graph ever carried any (every one NULL, no writer anywhere), so the
# reader, the renderer and the column they fed are gone and there is no longer a value here for
# the redaction to strip. The counts and ranges it strips are real.
ADMIN_ONLY_TEXT = ("58,731", "6,047", "1,913", "5,902", "4,388", "3,770", "4,919",
                   "0.25", "911.5", "47..263", "2011-03-04", "2019-08-27")
# The attribute columns a non-admin must not get.
COLUMN_TOKENS = ("n=", "range")
# The one line that names those columns for every caller: the resolved-types legend in graph_context._assemble.
LEGEND_PREFIX = "## Resolved sample types:"

VOCAB_NAMES = ("VOCAB_INVESTIGATIONS", "VOCAB_PROJECTS", "VOCAB_STUDIES", "VOCAB_PUBLISHED", "VOCAB_EDGES")
SCOPED_VOCAB_NAMES = tuple(name + "_SCOPED" for name in VOCAB_NAMES)
# What the scoped statements return: a strict subset of the rows above, so a test can tell which statement answered.
SCOPED_VOCAB_ROWS = {
    "VOCAB_INVESTIGATIONS_SCOPED": [{"title": "Investigation A"}],
    "VOCAB_PROJECTS_SCOPED": [{"title": "Project A"}],
    "VOCAB_STUDIES_SCOPED": [],
    "VOCAB_PUBLISHED_SCOPED": [],
    "VOCAB_EDGES_SCOPED": [{"assay": "Short Read Sequencing", "protocol": None, "parent_type": "TIS",
                            "child_type": "D.SEQ"}],
}
EMPTY_VOCABULARY = gc.Vocabulary((), (), (), (), (), (), ())

STATEMENTS = {getattr(gc, name): name
              for name in ("META", "INDEX", "GUARD", "TYPES_ADMIN") + VOCAB_NAMES + SCOPED_VOCAB_NAMES
              if isinstance(getattr(gc, name, None), str)}


class StubReader:
    """Stands in for ``graph_catalog._read``: rows by statement, and the name (and parameters) of every statement
    asked for."""

    def __init__(self):
        self.names: list[str] = []
        self.calls: list[tuple[str, dict]] = []

    def vocabulary_reads(self) -> list[tuple[str, dict]]:
        return [(name, params) for name, params in self.calls if name.startswith("VOCAB_")]

    def __call__(self, driver, database, statement, params=None, *, timeout_s=None):
        name = STATEMENTS[statement]
        self.names.append(name)
        self.calls.append((name, dict(params or {})))
        if name == "META":
            return [dict(META)]
        if name == "INDEX":
            return [dict(row) for row in INDEX_ROWS]
        if name == "GUARD":
            return [dict(row) for row in GUARD_ROWS]
        if name == "TYPES_ADMIN":
            return [TYPE_ROWS[t] for t in (params or {}).get("types", []) if t in TYPE_ROWS]
        if name in SCOPED_VOCAB_ROWS:
            return [dict(row) for row in SCOPED_VOCAB_ROWS[name]]
        return [dict(row) for row in VOCAB_ROWS[name]]


@pytest.fixture(autouse=True)
def reader(monkeypatch):
    gc.reset_cache()
    stub = StubReader()
    monkeypatch.setattr(gc, "_now", lambda: 1000.0)
    monkeypatch.setattr(gc, "_make_driver", lambda config: object())
    monkeypatch.setattr(gc, "_read", stub)
    yield stub
    gc.reset_cache()


def _base():
    return types.SimpleNamespace(NEO4J_URI=URI, NEO4J_DATABASE="neo4j", NEO4J_USER="neo4j",
                                 NEO4J_PASSWORD="not-a-secret")


def admin():
    return with_scope(_base(), GraphScope.admin("test"))


def _with_attr(value):
    config = _base()
    setattr(config, SCOPE_ATTR, value)
    return config


def _magicmock():
    config = MagicMock()  # every attribute exists, GRAPH_SCOPE included, and none is a GraphScope
    config.NEO4J_URI, config.NEO4J_DATABASE = URI, "neo4j"
    config.NEO4J_USER, config.NEO4J_PASSWORD = "neo4j", "not-a-secret"
    return config


REDACTED = {
    "non_admin": lambda: with_scope(_base(), GraphScope.for_projects([1, 3], source="test")),
    "no_projects": lambda: with_scope(_base(), GraphScope.for_projects([], source="test")),
    "no_scope_attribute": _base,
    "scope_none": lambda: with_scope(_base(), None),
    "plain_dict_admin": lambda: _with_attr({"is_admin": True, "project_ids": []}),
    "string_admin": lambda: _with_attr("admin"),
    "magicmock_config": _magicmock,
}


@pytest.fixture(params=sorted(REDACTED))
def redacted(request):
    return REDACTED[request.param]()


# --- admin: the stored values, the cached objects ---------------------------------------------------------------------


def test_admin_snapshot_carries_the_stored_counts():
    snap = gc.get_snapshot(admin())

    assert [(r.title, r.sample_count) for r in snap.index] == [
        ("CEL", 1913), ("D.SEQ", 6047), ("OLD", 0), ("TIS", 58731)]


def test_admin_type_details_carry_the_stored_counts_and_ranges():
    tis, dseq = gc.get_type_details(admin(), ["TIS", "D.SEQ"])

    assert tis.sample_count == 58731
    weight, organ, collected = tis.attributes  # stored order: most filled first
    assert (weight.title, weight.sample_count, weight.num_min, weight.num_max) == ("Weight", 5902, 0.25, 911.5)
    assert (organ.title, organ.sample_count) == ("Organ", 4388)
    assert (collected.date_min, collected.date_max) == ("2011-03-04", "2019-08-27")
    assert (dseq.sample_count, dseq.attributes[0].num_min, dseq.attributes[0].num_max) == (6047, 47.0, 263.0)


def test_admin_gets_the_cached_objects_themselves():
    config = admin()
    assert gc.get_snapshot(config) is gc.get_snapshot(config)
    assert gc.get_type_details(config, ["TIS"])[0] is gc.get_type_details(config, ["TIS"])[0]


# --- everyone else: redacted copies ---------------------------------------------------------------------------------


def test_snapshot_drops_every_sample_count(redacted):
    snap = gc.get_snapshot(redacted)

    assert [r.sample_count for r in snap.index] == [None, None, None, None]


def test_snapshot_keeps_names_structure_and_the_guard(redacted):
    snap = gc.get_snapshot(redacted)

    assert [(r.title, r.label, r.name, r.clade, r.deprecated, r.attributes_with_values) for r in snap.index] == [
        ("CEL", "T_CEL", "Cells", "Source", False, 0),
        ("D.SEQ", "T_D_SEQ", "Sequencing Data", "Data", False, 1),
        ("OLD", "T_OLD", None, None, True, 0),
        ("TIS", "T_TIS", "Tissue Sample", "Source", False, 3),
    ]
    assert dict(snap.guard) == {
        "T_CEL": frozenset(), "T_D_SEQ": frozenset({"ReadLength"}), "T_OLD": frozenset(),
        "T_TIS": frozenset({"Weight", "Organ", "Collected"}),
    }
    assert (snap.catalog_hash, snap.synced_at, snap.schema_version, snap.has_usage) == (
        "hash-redaction", "2026-01-02T03:04:05Z", "1.2", False)


def test_type_details_drop_counts_and_ranges(redacted):
    details = gc.get_type_details(redacted, ["TIS", "D.SEQ"])

    assert [d.title for d in details] == ["TIS", "D.SEQ"]
    for detail in details:
        assert detail.sample_count is None
        for attribute in detail.attributes:
            assert attribute.sample_count is None, attribute.title
            assert (attribute.num_min, attribute.num_max) == (None, None), attribute.title
            assert (attribute.date_min, attribute.date_max) == (None, None), attribute.title


def test_type_details_keep_names_types_meanings_and_structure(redacted):
    tis, dseq = gc.get_type_details(redacted, ["TIS", "D.SEQ"])

    assert (tis.title, tis.label, tis.name, tis.clade) == ("TIS", "T_TIS", "Tissue Sample", "Source")
    assert tis.summary == "A piece of tissue. More text."
    assert (tis.curated_parents, tis.curated_children, tis.never_filled) == ("PAT, MUS", "D.SEQ", 7)
    assert [(a.title, a.value_type, a.declared, a.needs_backticks, a.meaning, a.unit_key, a.role)
            for a in tis.attributes] == [
        ("Collected", "date", False, False, None, None, None),
        ("Organ", "string", True, False, "The organ the tissue came from.", None, "descriptive"),
        ("Weight", "number", True, False, "Mass of the piece in milligrams.", "3:WeightUnits", "measurement"),
    ]
    assert (dseq.curated_parents, dseq.summary, dseq.never_filled) == ("TIS, CEL", None, 0)
    assert [(a.title, a.value_type) for a in dseq.attributes] == [("ReadLength", "integer")]


def test_attributes_are_listed_by_title_because_fill_order_is_itself_a_count(redacted):
    (tis,) = gc.get_type_details(redacted, ["TIS"])

    assert [a.title for a in tis.attributes] == ["Collected", "Organ", "Weight"]


# --- the vocabulary: every project for an admin, the caller's own projects for anyone else ----------------------------


def test_an_admin_vocabulary_reads_every_project(reader):
    vocab = gc.get_vocabulary(admin())

    assert [name for name, _ in reader.vocabulary_reads()] == list(VOCAB_NAMES)
    assert all(params == {} for _, params in reader.vocabulary_reads())
    assert vocab.investigation_titles == ("Investigation A", "Investigation B")
    assert vocab.project_titles == ("Project A", "Project B")
    assert vocab.study_titles == ("Study 1",)
    assert vocab.published_studies == ({"title": "Study 1", "doi": "10.1000/example", "pmid": ""},)
    assert vocab.protocol_titles == ("RNA prep",)


def test_a_non_admin_vocabulary_is_read_through_the_project_scope(reader):
    vocab = gc.get_vocabulary(REDACTED["non_admin"]())  # projects 1 and 3

    assert [name for name, _ in reader.vocabulary_reads()] == list(SCOPED_VOCAB_NAMES)
    assert all(params == {SCOPE_PARAM: [1, 3]} for _, params in reader.vocabulary_reads())
    assert vocab == gc.Vocabulary(
        investigation_titles=("Investigation A",), project_titles=("Project A",), study_titles=(),
        published_studies=(), assay_titles=("Short Read Sequencing",), protocol_titles=(),
        assay_connections=({"assay": "Short Read Sequencing", "parent_type": "TIS", "child_type": "D.SEQ"},))


@pytest.mark.parametrize("name", ["no_projects", "no_scope_attribute", "scope_none", "plain_dict_admin",
                                  "string_admin", "magicmock_config"])
def test_a_caller_who_sees_no_project_gets_an_empty_vocabulary_and_reads_none(reader, name):
    assert gc.get_vocabulary(REDACTED[name]()) == EMPTY_VOCABULARY
    assert reader.vocabulary_reads() == []


def test_a_vocabulary_is_never_served_to_another_caller(reader):
    first = with_scope(_base(), GraphScope.for_projects([3, 1], source="test"))
    second = with_scope(_base(), GraphScope.for_projects([2], source="test"))

    mine = gc.get_vocabulary(first)
    everything = gc.get_vocabulary(admin())
    theirs = gc.get_vocabulary(second)
    again = gc.get_vocabulary(first)

    assert mine.project_titles == theirs.project_titles == ("Project A",)
    assert everything.project_titles == ("Project A", "Project B")
    assert again == mine
    # one scoped read per set of ids (the repeat is cached), one unscoped read for the admin
    assert [params for name, params in reader.vocabulary_reads() if name == "VOCAB_PROJECTS_SCOPED"] == [
        {SCOPE_PARAM: [1, 3]}, {SCOPE_PARAM: [2]}]
    assert [name for name, _ in reader.vocabulary_reads()].count("VOCAB_PROJECTS") == 1
    # and the admin's cached vocabulary is still whole after both
    assert gc.get_vocabulary(admin()).project_titles == ("Project A", "Project B")


def test_a_scoped_vocabulary_is_read_again_after_its_shorter_ttl(reader, monkeypatch):
    """A caller's vocabulary follows membership churn within minutes, not an hour (7.1 red team, N4).

    Nothing a sample-level sync writes invalidates the cache (only a full sync, a drift run and a relabel stamp
    GraphMeta), so after a sample leaves a caller's project its study's title, DOI and PMID stay visible to that
    caller for as long as the scoped entry lives. The admin form keeps the hour: it shows every project anyway.
    """
    clock = [1000.0]
    monkeypatch.setattr(gc, "_now", lambda: clock[0])
    mine = with_scope(_base(), GraphScope.for_projects([1, 3], source="test"))

    def scoped_reads():
        return sum(1 for name, _ in reader.vocabulary_reads() if name == "VOCAB_PROJECTS_SCOPED")

    def admin_reads():
        return sum(1 for name, _ in reader.vocabulary_reads() if name == "VOCAB_PROJECTS")

    gc.get_vocabulary(mine)
    gc.get_vocabulary(admin())
    assert (scoped_reads(), admin_reads()) == (1, 1)

    clock[0] += gc.SCOPED_VOCAB_TTL_S - 1
    gc.get_vocabulary(mine)
    assert scoped_reads() == 1

    clock[0] += 1
    gc.get_vocabulary(mine)
    gc.get_vocabulary(admin())
    assert (scoped_reads(), admin_reads()) == (2, 1)
    assert gc.SCOPED_VOCAB_TTL_S < gc.VOCAB_TTL_S


def test_the_scoped_statements_carry_graph_searchs_scope_clause():
    def visible(var):
        return SCOPE_CLAUSE_TEMPLATE.format(element="__scope_p", var=var, param=SCOPE_PARAM)

    for name in ("VOCAB_INVESTIGATIONS_SCOPED", "VOCAB_STUDIES_SCOPED", "VOCAB_PUBLISHED_SCOPED"):
        assert visible("s") in getattr(gc, name), name
    assert visible("c") in gc.VOCAB_EDGES_SCOPED and visible("p") in gc.VOCAB_EDGES_SCOPED
    assert f"p.id IN ${SCOPE_PARAM}" in gc.VOCAB_PROJECTS_SCOPED
    assert f"p.id IN ${SCOPE_PARAM}" in gc.VOCAB_INVESTIGATIONS_SCOPED
    for name in SCOPED_VOCAB_NAMES:
        assert "LIMIT" not in getattr(gc, name).upper(), name


# --- the cache ------------------------------------------------------------------------------------------------------


def test_an_admin_call_after_a_non_admin_call_still_sees_the_full_counts(reader):
    other = REDACTED["non_admin"]()
    assert gc.get_snapshot(other).index[3].sample_count is None
    assert gc.get_type_details(other, ["TIS"])[0].attributes[1].sample_count is None

    snap = gc.get_snapshot(admin())
    (tis,) = gc.get_type_details(admin(), ["TIS"])

    assert snap.index[3].sample_count == 58731
    assert tis.sample_count == 58731
    assert [a.sample_count for a in tis.attributes] == [5902, 4388, 3770]
    assert (tis.attributes[0].num_min, tis.attributes[2].date_max) == (0.25, "2019-08-27")
    # and a non-admin after that is redacted again
    assert gc.get_snapshot(other).index[3].sample_count is None


def test_redaction_is_a_copy_of_the_cache_not_a_second_read(reader):
    other = REDACTED["non_admin"]()
    gc.get_snapshot(other)
    gc.get_type_details(other, ["TIS"])
    gc.get_snapshot(admin())
    gc.get_type_details(admin(), ["TIS"])
    gc.get_type_details(other, ["TIS"])

    assert reader.names == ["META", "INDEX", "GUARD", "TYPES_ADMIN"]


def test_a_redacted_snapshot_is_never_the_cached_object():
    cached = gc.get_snapshot(admin())
    copy = gc.get_snapshot(REDACTED["non_admin"]())

    assert copy is not cached
    assert copy.index[0] is not cached.index[0]
    assert cached.index[3].sample_count == 58731


# --- what the graph agent reads -------------------------------------------------------------------------------------

PLAN = {"resolved": {"sampletypes": [{"code": "TIS"}, {"code": "D.SEQ"}]}}


def _context(config) -> str:
    catalog = graph_mod.live_catalog_context(config, "which tissue samples", None, PLAN)
    assert catalog is not None, "the stubbed catalog must be read live, not the committed fallback"
    return catalog.schema


def _data_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.startswith(LEGEND_PREFIX)]


def test_the_admin_context_shows_counts_and_ranges():
    # The control: the tokens the next test looks for are really there for an admin.
    text = _context(admin())

    for token in COLUMN_TOKENS:
        assert any(token in line for line in _data_lines(text)), token
    for value in ADMIN_ONLY_TEXT:
        assert value in text, value


def test_the_rendered_context_for_a_non_admin_has_no_counts_or_ranges(redacted):
    text = _context(redacted)

    for line in _data_lines(text):
        for token in COLUMN_TOKENS:
            assert token not in line, (token, line)
    for value in ADMIN_ONLY_TEXT:
        assert value not in text, value
    # names, types and structure stay
    assert "TIS :T_TIS \"Tissue Sample\" clade Source, sample count unknown, 3 attributes with values" in text
    assert "- Collected [date] (undeclared)" in text
    assert "- Weight [number] | unit of WeightUnits | Mass of the piece in milligrams" in text
    assert "- ReadLength [integer]" in text


def test_the_legend_is_the_only_line_that_names_the_columns(redacted):
    text = _context(redacted)

    naming = [line for line in text.splitlines() if any(token in line for token in COLUMN_TOKENS)]
    assert len(naming) == 1 and naming[0].startswith(LEGEND_PREFIX), naming


def test_rendering_the_getters_directly_gives_the_same_redaction(redacted):
    snap = gc.get_snapshot(redacted)
    text = graph_context.render_graph_context(snap, gc.get_type_details(redacted, ["TIS", "D.SEQ"]))

    for value in ADMIN_ONLY_TEXT:
        assert value not in text, value
    assert "sample count unknown" in graph_context.render_type_index(snap.index)
