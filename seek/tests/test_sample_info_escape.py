"""The sample page shows attribute values as text, never as markup (SEC-005).

``/seek/sample/id=N/`` renders each value ``getSampleInfo`` hands it with ``|safe``
(``pages/samples.embed.html``), because the Parent, Protocol, Link_, File_ and Publish values become links. Every other
value used to go through ``_formatLinkUrl`` untouched, so markup stored in a sample ran in the browser of whoever opened
it. Now a value is escaped unless a branch builds a link from it, and the links still render as links.

Hermetic: no database; the table object is built without its constructor.
"""

from unittest.mock import MagicMock, patch

import pytest

from seek.dbtable_sample import DBtable_sample

PAYLOAD = '<img src=x onerror=alert(1)>'


@pytest.fixture
def table():
    return DBtable_sample.__new__(DBtable_sample)


@pytest.mark.parametrize("name", ["Organ", "Name", "Notes", "Link_Paper", "File_Raw", "Publish_GEO"])
def test_markup_in_a_value_is_shown_as_text(table, name):
    out = table._formatLinkUrl(name, PAYLOAD)

    assert "<" not in out and "&lt;img" in out


def test_a_non_link_entry_of_a_link_list_is_dropped_not_rendered(table):
    out = table._formatLinkUrl("Link_Paper", "https://x.org/a; <script>alert(1)</script>")

    assert "<script>" not in out and '<a href="https://x.org/a"' in out


@pytest.mark.parametrize("name", ["Link_Paper", "File_Raw", "Publish_GEO"])
def test_a_link_value_still_renders_as_a_link(table, name):
    assert table._formatLinkUrl(name, "https://x.org/a") == \
        '<a href="https://x.org/a" target="_blank">https://x.org/a</a>'


def test_a_parent_value_still_renders_as_a_link(table):
    assert table._formatLinkUrl("Parent", "TIS-1") == \
        '<a href="/seek/sampletree/uid=TIS-1/" target="_blank">TIS-1</a>'


def test_a_plain_value_is_unchanged(table):
    assert table._formatLinkUrl("Organ", "Lung") == "Lung"


def test_the_sample_page_rows_carry_no_markup(table):
    table._retrieveSampleByID = MagicMock(return_value={"sample_type_id": 1, "json_metadata": "{}"})
    table._getRecordFromJson = MagicMock(return_value={"UID": "TIS-1", "Notes": PAYLOAD})
    attributes = MagicMock()
    attributes.getAttributeInfo.return_value = {"headers": ["UID", "Notes"]}
    with patch("seek.sample.table.DBtable_sampleattribute", return_value=attributes):
        _, rows = table.getSampleInfo(1)

    assert {row["attrname"]: row["attrvalue"] for row in rows} == {
        "UID": "TIS-1", "Notes": "&lt;img src=x onerror=alert(1)&gt;"}
