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
