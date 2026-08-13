"""#93 — ``DataGrid.sqlQuery_select_filters`` must bind its rule values.

The method is the sibling of the ``seek/search.py`` defect: it built a
``WHERE`` clause by splicing each datagrid rule's ``value`` into a
``LIKE '%...%'`` literal, so a single quote in the value broke out of the
literal and became statement text.

It has **no in-repo caller** (``grep -rn sqlQuery_select_filters`` finds only
the definition plus the design docs), so these tests are the only consumer of
the new ``(fragment, params)`` signature. They exist so the parity fix stays
fixed if a caller ever appears.

Hermetic: no DB, no fixtures. ``DataGrid.__init__`` only reads ``dbtable.db``,
but the instance is still built with ``__new__`` — the way
``nextseek_api/tests/test_views.py::TestGetChildrenUIDsSQLInjection`` builds
``DBtable_sample`` — so the test never depends on the constructor at all.
"""

from dmac.datagrid_custom import DataGrid

# Server-owned mapping: the client picks a *key*, the column name is always
# ours. Shaped like the mappings the real datagrid callers would pass.
FIELD_MAPPING = {"unit": "S.unit", "title": "S.title"}

PAYLOAD = "Amon' OR '1'='1"


def _grid():
    """Build the instance without __init__ (it wants a dbtable we don't have)."""
    return DataGrid.__new__(DataGrid)


def _rule(field, value, op="contains"):
    return {"field": field, "op": op, "value": value}


def _run(*rules):
    return _grid().sqlQuery_select_filters({"filterRules": list(rules)}, FIELD_MAPPING)


class TestHostileValueIsBound:
    def test_no_quote_reaches_the_statement_text(self):
        fragment, _params = _run(_rule("unit", PAYLOAD))
        assert "'" not in fragment

    def test_fragment_uses_a_placeholder(self):
        fragment, _params = _run(_rule("unit", PAYLOAD))
        assert "LIKE %s" in fragment

    def test_payload_travels_in_params_with_the_wildcards(self):
        _fragment, params = _run(_rule("unit", PAYLOAD))
        assert params == [f"%{PAYLOAD}%"]

    def test_column_identifier_still_comes_from_the_mapping(self):
        fragment, _params = _run(_rule("unit", PAYLOAD))
        assert "S.unit" in fragment
        # the client-supplied key must not reach the statement text
        assert "OR" not in fragment
        assert "1=1" not in fragment


class TestStatementTextIsValueInvariant:
    def test_benign_and_hostile_values_emit_identical_sql(self):
        benign_fragment, benign_params = _run(_rule("unit", "Amon"))
        hostile_fragment, hostile_params = _run(_rule("unit", PAYLOAD))
        assert benign_fragment == hostile_fragment
        assert benign_params != hostile_params

    def test_op_other_than_contains_emits_the_same_sql(self):
        # Both branches always built the same LIKE; keep it that way.
        contains, _ = _run(_rule("unit", "Amon", op="contains"))
        equals, _ = _run(_rule("unit", "Amon", op="equal"))
        assert contains == equals


class TestMultipleRules:
    def test_two_rules_join_with_and_and_bind_in_order(self):
        fragment, params = _run(_rule("unit", "Amon"), _rule("title", PAYLOAD))
        assert "WHERE" in fragment
        assert "AND" in fragment
        assert fragment.index("WHERE") < fragment.index("AND")
        assert fragment.count("%s") == 2
        assert params == ["%Amon%", f"%{PAYLOAD}%"]


class TestUnmappedFieldContributesNothing:
    def test_lone_unmapped_rule_yields_empty_fragment_and_no_params(self):
        fragment, params = _run(_rule("evil; DROP TABLE samples", PAYLOAD))
        assert fragment == ""
        assert params == []

    def test_unmapped_rule_alongside_a_mapped_one_adds_neither(self):
        fragment, params = _run(
            _rule("unit", "Amon"), _rule("nosuchfield", PAYLOAD)
        )
        assert "nosuchfield" not in fragment
        assert fragment.count("%s") == 1
        assert params == ["%Amon%"]

    def test_leading_unmapped_rule_still_advances_the_counter(self):
        """Pre-existing quirk, deliberately preserved: ``n += 1`` sits outside
        the ``field_dg in fieldMapping`` guard, so a skipped first rule makes
        the next mapped rule emit ``AND`` with no preceding ``WHERE``. Not a
        #93 concern (no injection, and the method has no caller) — locked here
        only to prove the binding fix changed nothing else."""
        fragment, params = _run(
            _rule("nosuchfield", "x"), _rule("unit", "Amon")
        )
        assert "WHERE" not in fragment
        assert "AND" in fragment
        assert params == ["%Amon%"]
