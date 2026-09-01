"""The command that fills dmac.sample_type_requirements from the graph."""

from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

_MOD = "nextseek_api.management.commands.derive_sample_type_requirements"

# (child, parent, assay, n) as the Cypher returns them.
GRAPH_ROWS = [
    {"child": "D.SEQ", "parent": "DNA", "assay": "Short Read Sequencing", "n": 1763},
    {"child": "D.SEQ", "parent": "DNA", "assay": "Short Read Sequencing - Data Linked", "n": 292},
    {"child": "PAV", "parent": "NHP", "assay": "Patient Visit", "n": 3184},
    {"child": "PAV", "parent": "NHP", "assay": None, "n": 1607},
    {"child": "PAV", "parent": "PAT", "assay": "Patient Visit", "n": 1113},
    {"child": "PAV", "parent": "MUS", "assay": "Tissue Collection", "n": 123},
    {"child": "RARE", "parent": "TIS", "assay": None, "n": 4},
    # Single parent, every contributing row has assay=None: clears MIN_SUPPORT
    # on its own and should write assay_titles as SQL NULL, not "[]".
    {"child": "NUL", "parent": "SRC", "assay": None, "n": 25},
]


def _driver(rows):
    """Stand in for GraphDatabase.driver(...), context-managed at both the
    driver and the session level (the command does `with driver:` around
    `with driver.session():`, matching the rest of the codebase)."""
    session = MagicMock()
    session.run.return_value = rows
    driver = MagicMock()
    driver.__enter__.return_value = driver
    driver.session.return_value.__enter__.return_value = session
    return MagicMock(return_value=driver)


@pytest.fixture
def _graph():
    with patch(f"{_MOD}.GraphDatabase.driver", _driver(GRAPH_ROWS)) as d:
        yield d


def test_writes_one_row_per_constrained_child(_graph):
    with patch(f"{_MOD}.Sample_type_requirements") as model:
        call_command("derive_sample_type_requirements")
    written = {c.kwargs["child_code"] for c in model.objects.create.call_args_list}
    assert written == {"D.SEQ", "PAV", "NUL"}


def test_a_child_below_min_support_is_not_written(_graph):
    with patch(f"{_MOD}.Sample_type_requirements") as model:
        call_command("derive_sample_type_requirements")
    written = {c.kwargs["child_code"] for c in model.objects.create.call_args_list}
    assert "RARE" not in written


def test_parent_codes_are_stored_as_json_in_share_order(_graph):
    import json

    with patch(f"{_MOD}.Sample_type_requirements") as model:
        call_command("derive_sample_type_requirements")
    row = next(c.kwargs for c in model.objects.create.call_args_list
               if c.kwargs["child_code"] == "PAV")
    assert json.loads(row["parent_codes"]) == ["NHP", "PAT"]


def test_seek_title_suffixes_collapse_to_one_assay(_graph):
    import json

    with patch(f"{_MOD}.Sample_type_requirements") as model:
        call_command("derive_sample_type_requirements")
    row = next(c.kwargs for c in model.objects.create.call_args_list
               if c.kwargs["child_code"] == "D.SEQ")
    assert json.loads(row["assay_titles"]) == ["Short Read Sequencing"]


def test_counts_are_summed_across_assay_variants_not_last_write_wins(_graph):
    """(child, parent) rows are merged with `count +=`, not `count =`.

    D.SEQ<-DNA appears once per assay-title variant (1763 + 292 = 2055) and
    has a single parent, so a last-row-wins bug still clears MIN_SUPPORT and
    still yields coverage 1.0 -- only the written `support` gives it away.
    PAV<-NHP is split the same way (3184 + 1607), on a child with three
    parents, so its total support (4791 + 1113 + 123 = 6027) covers the same
    bug for the multi-parent path.
    """
    with patch(f"{_MOD}.Sample_type_requirements") as model:
        call_command("derive_sample_type_requirements")
    written = {c.kwargs["child_code"]: c.kwargs for c in model.objects.create.call_args_list}
    assert written["D.SEQ"]["support"] == 2055
    assert written["PAV"]["support"] == 6027


def test_a_child_with_only_null_assays_writes_null_not_empty_array(_graph):
    with patch(f"{_MOD}.Sample_type_requirements") as model:
        call_command("derive_sample_type_requirements")
    row = next(c.kwargs for c in model.objects.create.call_args_list
               if c.kwargs["child_code"] == "NUL")
    assert row["assay_titles"] is None


def test_the_table_is_replaced_not_appended(_graph):
    with patch(f"{_MOD}.Sample_type_requirements") as model:
        call_command("derive_sample_type_requirements")
    model.objects.all.return_value.delete.assert_called_once()


def test_dry_run_writes_nothing(_graph):
    with patch(f"{_MOD}.Sample_type_requirements") as model:
        call_command("derive_sample_type_requirements", "--dry-run")
    model.objects.create.assert_not_called()
    model.objects.all.return_value.delete.assert_not_called()


def test_a_graph_failure_leaves_the_existing_table_untouched():
    """A stale table beats an empty one: the page keeps working."""
    with patch(f"{_MOD}.GraphDatabase.driver", side_effect=RuntimeError("no graph")), \
         patch(f"{_MOD}.Sample_type_requirements") as model:
        with pytest.raises(SystemExit):
            call_command("derive_sample_type_requirements")
    model.objects.all.return_value.delete.assert_not_called()
    model.objects.create.assert_not_called()
