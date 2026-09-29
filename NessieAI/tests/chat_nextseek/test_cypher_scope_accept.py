"""
The prover accepts every Cypher shape the graph agent is taught, and scopes it.

Each taught shape (both graph agent prompts, the default and v2) is a literal string in
graph_scope/battery.py; for a non-admin it must come back Scoped, with exactly the predicates and joins the battery
names. The one taught shape that reads the catalog is on an explicit expected-refusal list (decision 4). The
report runners' two statements, captured through a patched tool, are proven the same way (spec section 10).

`agents.graph._scan` is used as an oracle only here: every variable it reports as a Sample must be covered by an
injected clause or a path clause in the output (spec section 11.1).

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md sections 5 and 11.1.
"""
from __future__ import annotations

import re

import pytest

from chat_nextseek import cypher_scope
from chat_nextseek.cypher_scope import (
    FUNCTION_ALLOWLIST,
    FUNCTION_NAMESPACE_ALLOWLIST,
    PURE_APOC_FAMILIES,
    Refused,
    Scoped,
    scope_cypher,
)
from chat_nextseek.graph_scope import SCOPE_PARAM, GraphScope

from NessieAI.tests.chat_nextseek.graph_scope.battery import (
    ACCEPTED,
    TAUGHT,
    TAUGHT_REFUSED,
    report_statements,
)

CALLER = GraphScope.for_projects([3, 1], source="test")
ADMIN = GraphScope.admin("test")
ALL_ACCEPTED = TAUGHT + ACCEPTED


@pytest.mark.parametrize("case", ALL_ACCEPTED, ids=[c.id for c in ALL_ACCEPTED])
def test_accepted_shape_is_scoped(case):
    out = scope_cypher(case.cypher, case.params, CALLER)
    assert isinstance(out, Scoped), getattr(out, "reasons", out)
    assert out.decision == "proven"
    assert out.injected == case.injected
    assert out.joined == case.joined
    assert out.parameters == {**case.params, SCOPE_PARAM: [1, 3]}


@pytest.mark.parametrize("case", ALL_ACCEPTED, ids=[c.id for c in ALL_ACCEPTED])
def test_admin_runs_the_submitted_text(case):
    out = scope_cypher(case.cypher, case.params, ADMIN)
    assert isinstance(out, Scoped)
    assert out.decision == "admin"
    assert out.cypher == case.cypher
    assert out.parameters == case.params
    assert out.parameters is not case.params
    assert out.injected == () and out.joined == ()


def test_the_taught_corpus_covers_both_prompts():
    prefixes = {case.id.split(".")[0] for case in TAUGHT}
    assert prefixes == {"default", "v2"}
    assert len(TAUGHT) >= 50


@pytest.mark.parametrize("case", TAUGHT_REFUSED, ids=[c.id for c in TAUGHT_REFUSED])
def test_the_catalog_shape_is_refused_for_a_non_admin(case):
    out = scope_cypher(case.cypher, case.params, CALLER)
    assert isinstance(out, Refused)
    assert out.codes == case.codes
    admin = scope_cypher(case.cypher, case.params, ADMIN)
    assert isinstance(admin, Scoped) and admin.cypher == case.cypher


# --------------------------------------------------------------------------- #
# agents.graph._scan as an oracle
# --------------------------------------------------------------------------- #

_LINE_RE = re.compile(r"^(?P<name>.+?): (?P<what>sample clause|project clause|every node)(?: \((?P<names>.*)\))?$")


def _covered(scoped: Scoped) -> set[str]:
    covered: set[str] = set()
    for line in scoped.injected:
        m = _LINE_RE.match(line)
        assert m, line
        if m.group("what") == "sample clause":
            covered.add(m.group("name").strip("`"))
        elif m.group("what") == "every node" and m.group("names"):
            covered.update(n.strip().strip("`") for n in m.group("names").split(","))
    return covered


@pytest.mark.parametrize("case", ALL_ACCEPTED, ids=[c.id for c in ALL_ACCEPTED])
def test_every_sample_the_scanner_sees_is_covered(case):
    from chat_nextseek.agents.graph import _scan

    scan = _scan(case.cypher)
    aliases = {alias for _, alias in scan.aliases}
    samples = {v for v in scan.samples if v not in aliases}
    out = scope_cypher(case.cypher, case.params, CALLER)
    assert isinstance(out, Scoped)
    missing = samples - _covered(out)
    assert not missing, (missing, out.injected)


@pytest.mark.parametrize("case", ALL_ACCEPTED, ids=[c.id for c in ALL_ACCEPTED])
def test_every_injected_line_is_in_the_text(case):
    out = scope_cypher(case.cypher, case.params, CALLER)
    for line in out.injected:
        m = _LINE_RE.match(line)
        name = m.group("name")
        if m.group("what") == "sample clause":
            assert re.search(rf"any\(__scope_p\d+ IN {re.escape(name)}\.project_ids WHERE __scope_p\d+ IN "
                             rf"\${SCOPE_PARAM}\)", out.cypher), line
        elif m.group("what") == "project clause":
            assert f"{name}.id IN ${SCOPE_PARAM}" in out.cypher, line
        else:
            assert re.search(rf"all\(__scope_m\d+ IN nodes\({re.escape(name)}\) WHERE any\(", out.cypher), line


# --------------------------------------------------------------------------- #
# The report runners (spec section 10)
# --------------------------------------------------------------------------- #

def test_report_statements_are_proven(tmp_path):
    captured = report_statements(tmp_path)
    assert len(captured) == 3
    for cypher, params in captured:
        assert cypher.startswith("MATCH (inv:Investigation)<-[:IN_INVESTIGATION]-(study:Study)<-[:IN_STUDY]-(s:Sample)")
        out = scope_cypher(cypher, params, CALLER)
        assert isinstance(out, Scoped), getattr(out, "reasons", out)
        assert out.injected == ("s: sample clause",)
        assert out.joined == ("inv (Investigation): joined to study", "study (Study): joined to s")
        assert out.parameters == {**params, SCOPE_PARAM: [1, 3]}


# --------------------------------------------------------------------------- #
# The pinned tables
# --------------------------------------------------------------------------- #

def test_function_allowlist_pin():
    assert FUNCTION_ALLOWLIST == frozenset({
        # aggregates
        "avg", "collect", "count", "max", "min", "percentilecont", "percentiledisc", "stdev", "stdevp", "sum",
        # scalar and list
        "coalesce", "elementid", "endnode", "head", "id", "isempty", "isnan", "keys", "labels", "last", "length",
        "nodes", "nullif", "range", "relationships", "reverse", "size", "startnode", "tail", "type", "valuetype",
        # conversions
        "toboolean", "tobooleanornull", "tobooleanlist", "tofloat", "tofloatornull", "tofloatlist", "tointeger",
        "tointegerornull", "tointegerlist", "tostring", "tostringornull", "tostringlist",
        # math
        "abs", "ceil", "floor", "rand", "round", "sign", "e", "exp", "log", "log10", "sqrt", "acos", "asin", "atan",
        "atan2", "cos", "cosh", "cot", "coth", "degrees", "haversin", "pi", "radians", "sin", "sinh", "tan", "tanh",
        # strings
        "left", "right", "ltrim", "rtrim", "trim", "btrim", "lower", "upper", "tolower", "toupper", "replace", "split",
        "substring", "normalize", "char_length", "character_length",
        # temporal constructors
        "date", "datetime", "localdatetime", "localtime", "time", "duration",
    })
    assert FUNCTION_NAMESPACE_ALLOWLIST == frozenset({
        "date.truncate", "date.realtime", "date.statement", "date.transaction",
        "datetime.truncate", "datetime.fromepoch", "datetime.fromepochmillis", "datetime.realtime",
        "datetime.statement", "datetime.transaction",
        "localdatetime.truncate", "localdatetime.realtime", "localdatetime.statement", "localdatetime.transaction",
        "localtime.truncate", "localtime.realtime", "localtime.statement", "localtime.transaction",
        "time.truncate", "time.realtime", "time.statement", "time.transaction",
        "duration.between", "duration.inmonths", "duration.indays", "duration.inseconds",
    })
    assert PURE_APOC_FAMILIES == ("apoc.text.", "apoc.coll.", "apoc.number.", "apoc.math.", "apoc.date.",
                                  "apoc.temporal.")
    # Inside the pure families, the functions that read a property by a name given as data are refused.
    assert cypher_scope.APOC_PROPERTY_READERS == frozenset({"apoc.coll.sortnodes", "apoc.coll.sortmaps",
                                                            "apoc.coll.sortmulti"})
    for refused in ("properties", "exists", "randomuuid", "timestamp"):
        assert refused not in FUNCTION_ALLOWLIST


def test_label_and_relationship_tables_pin():
    assert cypher_scope.SAMPLE_LABEL == "Sample"
    assert cypher_scope.SAMPLE_TYPE_LABEL_PREFIX == "T_"
    assert cypher_scope.PROJECT_LABEL == "Project"
    assert cypher_scope.JOINED_LABELS == frozenset({"Study", "Investigation", "Person"})
    assert cypher_scope.LINEAGE_RELATIONSHIP == "DERIVED_FROM"
    assert cypher_scope.FIXED_RELATIONSHIPS == frozenset({"IN_STUDY", "IN_INVESTIGATION", "IN_PROJECT", "MEMBER_OF"})
    assert cypher_scope.FULLTEXT_PROCEDURE == "db.index.fulltext.queryNodes"
    assert cypher_scope.FULLTEXT_INDEX == "sample_search_text"


@pytest.mark.parametrize("name", ["apoc.text.join", "apoc.coll.toSet", "apoc.date.format", "toLower", "TOUPPER",
                                  "date.truncate", "duration.between", "percentileCont"])
def test_allowed_functions_pass(name):
    arg = "[1, 2]" if name.startswith("apoc.coll") else "'x'"
    out = scope_cypher(f"MATCH (s:Sample) RETURN {name}({arg}) AS v", {}, CALLER)
    assert isinstance(out, Scoped), getattr(out, "reasons", out)


def test_prover_module_is_pure():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(cypher_scope))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
    allowed = {"__future__", "dataclasses", "typing", "collections.abc", ".graph_scope"}
    assert imported <= allowed, imported - allowed
