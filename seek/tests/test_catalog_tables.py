"""Shared catalog presentation: clade tokens, the list-table partial, the read-only attribute table."""

from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string


def _css():
    return (Path(settings.BASE_DIR) / "themes" / "NextSeek" / "static" / "css" / "nextseek.css").read_text()


class TestCladeTokens:
    def test_four_clade_colors_are_defined(self):
        css = _css()
        for name in ("source", "processed", "raw", "analyzed"):
            assert f"--ns-clade-{name}:" in css

    def test_clade_helper_classes_exist(self):
        css = _css()
        assert ".clade-dot" in css
        assert ".clade-accent--source" in css


def _table_ctx():
    return {
        "columns": ["Name", "# attrs", "Samples"],
        "groups": [{
            "clade": "Source", "clade_slug": "source",
            "rows": [{"href": "/seek/sampletypes/NHP/", "code": "NHP",
                      "cells": ["Non-Human Primate", "18", "706"],
                      "filter_text": "nhp non-human primate"}],
        }],
    }


class TestCatalogTablePartial:
    def test_renders_a_table_with_the_code_anchor_and_no_chips(self):
        html = render_to_string("includes/catalog_table.html", _table_ctx())
        assert "cat-table" in html
        assert ">NHP<" in html
        assert 'href="/seek/sampletypes/NHP/"' in html
        assert "cat-chip" not in html          # chips retired

    def test_group_header_carries_a_clade_accent(self):
        html = render_to_string("includes/catalog_table.html", _table_ctx())
        assert "clade-accent--source" in html or "clade-dot--source" in html


class TestAttributeTablePartial:
    def test_renders_a_readonly_shell_scoped_to_the_type(self):
        html = render_to_string("includes/attribute_definitions_table.html", {"sample_type_id": 13})
        assert 'data-sample-type-id="13"' in html
        assert "attrs-ro-table" in html
        for banned in ("Add attribute", "attrs-tray", "batch-create", 'type="checkbox"'):
            assert banned not in html
