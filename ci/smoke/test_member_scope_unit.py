"""Unit tests for the member-scope helpers. No stack, no network, no browser."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from ci.smoke.member_scope import parse_my_project_ids, pick_non_member


def test_parse_reads_the_list_the_page_embeds():
    assert parse_my_project_ids("x\n\t\t\tconst myProjectIds = [13, 2];\n y") == [13, 2]


def test_parse_reads_null_as_every_project():
    assert parse_my_project_ids("const myProjectIds = null;") is None


def test_parse_refuses_a_page_without_the_constant():
    with pytest.raises(ValueError):
        parse_my_project_ids("<html></html>")


def test_pick_non_member_skips_the_members_projects_and_mixed_types():
    assert pick_non_member(["13", "2", "7"], [13, 2]) == 7


def test_pick_non_member_is_none_when_the_member_is_in_every_project():
    assert pick_non_member([1, 2], [2, 1]) is None
