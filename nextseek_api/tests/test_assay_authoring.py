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
