"""Hermetic tests for the Step-2 slug helper and ProjectIdentity."""
import pytest

from nextseek_api.cc_assistant.cc_provision import ProjectIdentity, slugify_project


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Liver Tox (NDMA) study", "liver-tox-ndma-study"),
        ("Already-slugged", "already-slugged"),
        ("   leading/trailing   ", "leading-trailing"),
        ("UPPER_case__Mix", "upper-case-mix"),
        ("a..b//c", "a-b-c"),
        ("cafe deja", "cafe-deja"),
        ("cafe\u0301 de\u0301ja\u0300", "cafe-deja"),
        ("!!!", ""),
        ("", ""),
    ],
)
def test_slugify_project_rule(title, expected):
    assert slugify_project(title) == expected


def test_slugify_project_is_deterministic():
    assert slugify_project("Liver Tox") == slugify_project("liver   tox")


def test_project_identity_dirname():
    project = ProjectIdentity(
        id="42", title="Liver Tox (NDMA) study", slug="liver-tox-ndma-study"
    )

    assert project.dirname == "42-liver-tox-ndma-study"


def test_project_identity_dirname_with_degenerate_slug():
    project = ProjectIdentity(id="7", title="!!!", slug="")

    assert project.dirname == "7-"
