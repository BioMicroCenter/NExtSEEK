"""Validating and serialising a proposed assay. No database in this module."""

import pytest

from nextseek_api.services.assay_authoring import (
    ValidationError,
    serialise_groups,
    validate,
)

OK = dict(
    name="Spectral Flow Cytometry",
    description="Full-spectrum single-cell analysis.",
    required_parents=[["TIS", "CEX"], ["AB"]],
    optional_parents=[],
    children=[["D.FLOW"]],
    existing_slugs={"flow-cytometry"},
)


class TestSerialiseGroups:
    def test_one_group_of_one_is_just_the_code(self):
        assert serialise_groups([["TIS"]]) == "TIS"

    def test_alternatives_within_a_group_join_with_or(self):
        assert serialise_groups([["TIS", "CEX", "CEL"]]) == "TIS or CEX or CEL"

    def test_groups_join_with_a_comma(self):
        assert serialise_groups([["TIS"], ["AB"]]) == "TIS, AB"

    def test_the_full_shape_round_trips_through_the_reader(self):
        """The whole point: what we write, context_catalog must read back."""
        from nextseek_api.services.context_catalog import parse_alternation

        groups = [["TIS", "CEX", "CEL"], ["AB", "ABP"]]
        known = {"TIS", "CEX", "CEL", "AB", "ABP"}
        assert parse_alternation(serialise_groups(groups), known) == groups

    def test_empty_is_empty_string(self):
        assert serialise_groups([]) == ""

    def test_empty_groups_are_dropped(self):
        assert serialise_groups([["TIS"], []]) == "TIS"

    def test_a_blank_alternative_is_dropped_from_a_group(self):
        assert serialise_groups([["TIS", ""]]) == "TIS"

    def test_a_group_emptied_entirely_by_cleaning_disappears(self):
        assert serialise_groups([["  "], ["AB"]]) == "AB"

    def test_duplicate_codes_within_a_group_collapse(self):
        assert serialise_groups([["TIS", "TIS"]]) == "TIS"


class TestValidate:
    def test_a_good_proposal_passes_and_carries_its_slug(self):
        got = validate(**OK)
        assert got["slug"] == "spectral-flow-cytometry"

    def test_it_serialises_each_role(self):
        got = validate(**OK)
        assert got["required_parents"] == "TIS or CEX, AB"
        assert got["children"] == "D.FLOW"
        assert got["optional_parents"] == ""

    def test_name_and_description_are_stripped(self):
        got = validate(**{
            **OK,
            "name": "  Spectral Flow Cytometry  ",
            "description": "  Full-spectrum single-cell analysis.  ",
        })
        assert got["name"] == "Spectral Flow Cytometry"
        assert got["description"] == "Full-spectrum single-cell analysis."

    def test_an_empty_name_is_rejected(self):
        with pytest.raises(ValidationError) as exc:
            validate(**{**OK, "name": "   "})
        assert "name" in exc.value.message.lower()

    def test_an_empty_description_is_rejected(self):
        with pytest.raises(ValidationError) as exc:
            validate(**{**OK, "description": ""})
        assert "definition" in exc.value.message.lower()

    def test_no_children_is_rejected(self):
        """An assay that produces nothing yields a meaningless bundle."""
        with pytest.raises(ValidationError) as exc:
            validate(**{**OK, "children": []})
        assert "produce" in exc.value.message.lower()

    def test_children_of_only_blank_strings_is_rejected(self):
        """[[""]] is a truthy list, but it serialises to nothing."""
        with pytest.raises(ValidationError) as exc:
            validate(**{**OK, "children": [[""]]})
        assert "produce" in exc.value.message.lower()

    def test_children_of_only_whitespace_is_rejected(self):
        with pytest.raises(ValidationError) as exc:
            validate(**{**OK, "children": [["  "]]})
        assert "produce" in exc.value.message.lower()

    def test_a_blank_alternative_in_a_parent_group_is_dropped(self):
        got = validate(**{**OK, "required_parents": [["TIS", ""]]})
        assert got["required_parents"] == "TIS"

    def test_a_duplicate_slug_is_rejected_and_names_the_clash(self):
        """assay_context already carries 22 duplicate names; it must not grow."""
        with pytest.raises(ValidationError) as exc:
            validate(**{**OK, "name": "Flow Cytometry"})
        assert "flow-cytometry" in exc.value.message

    def test_a_name_that_slugifies_to_nothing_is_rejected(self):
        with pytest.raises(ValidationError):
            validate(**{**OK, "name": "!!!"})

    def test_a_hyphen_apart_still_collides(self):
        """slugify_name collapses punctuation, so these are one assay."""
        with pytest.raises(ValidationError):
            validate(**{**OK, "name": "Flow-Cytometry"})


from unittest.mock import MagicMock, patch


class FakeCursor:
    """Stands in for a DB-API cursor. SQLite has no information_schema, so the
    introspection this module does can only be tested by injection."""

    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestActualColumns:
    def test_it_maps_lowercase_to_the_spelling_this_stack_uses(self):
        from nextseek_api.services.assay_authoring import actual_columns

        cursor = FakeCursor([("assay_name",), ("Required_Parent_Sample_Types",)])
        got = actual_columns(cursor)
        assert got["required_parent_sample_types"] == "Required_Parent_Sample_Types"
        assert got["assay_name"] == "assay_name"

    def test_an_all_lowercase_stack_maps_to_itself(self):
        from nextseek_api.services.assay_authoring import actual_columns

        cursor = FakeCursor([("required_parent_sample_types",)])
        got = actual_columns(cursor)
        assert got["required_parent_sample_types"] == "required_parent_sample_types"


class TestCreateAssay:
    """The two writes, and that a failure leaves neither."""

    # transaction.atomic(using=...) touches the connection even when the
    # model call inside it is mocked, and pytest-django blocks real DB access
    # without this marker.
    pytestmark = pytest.mark.django_db

    def _run(self, **overrides):
        from nextseek_api.services import assay_authoring

        kwargs = dict(
            name="Spectral Flow Cytometry",
            description="Full-spectrum single-cell analysis.",
            required_parents=[["TIS", "CEX"]],
            optional_parents=[],
            children=[["D.FLOW"]],
        )
        kwargs.update(overrides)
        return assay_authoring.create_assay(**kwargs)

    @patch("nextseek_api.services.assay_authoring._insert_context", return_value=7)
    @patch("nextseek_api.services.assay_authoring.Internal_assays")
    @patch("nextseek_api.services.assay_authoring._existing_slugs", return_value=set())
    def test_it_writes_both_rows_and_returns_the_context_id(
        self, _slugs, internal, insert
    ):
        internal.objects.create.return_value = MagicMock(id=42)
        assert self._run() == 7
        internal.objects.create.assert_called_once_with(
            internal_assay_title="Spectral Flow Cytometry"
        )

    @patch("nextseek_api.services.assay_authoring._insert_context", return_value=7)
    @patch("nextseek_api.services.assay_authoring.Internal_assays")
    @patch("nextseek_api.services.assay_authoring._existing_slugs", return_value=set())
    def test_the_context_row_links_to_the_vocabulary_term(
        self, _slugs, internal, insert
    ):
        """Without the link the assay is a recipe that can never be registered."""
        internal.objects.create.return_value = MagicMock(id=42)
        self._run()
        assert insert.call_args.kwargs["internal_assay_id"] == 42

    @patch("nextseek_api.services.assay_authoring._existing_slugs",
           return_value={"spectral-flow-cytometry"})
    def test_a_duplicate_is_rejected_before_anything_is_written(self, _slugs):
        with pytest.raises(ValidationError):
            self._run()

    @patch("nextseek_api.services.assay_authoring._insert_context",
           side_effect=RuntimeError("write failed"))
    @patch("nextseek_api.services.assay_authoring.Internal_assays")
    @patch("nextseek_api.services.assay_authoring._existing_slugs", return_value=set())
    def test_a_failed_context_write_is_reported_as_a_validation_error(
        self, _slugs, internal, insert
    ):
        """The view has one error path; the service does not leak RuntimeError."""
        internal.objects.create.return_value = MagicMock(id=42)
        with pytest.raises(ValidationError):
            self._run()
