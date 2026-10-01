"""UI-063: the Data File Query toolbar calls functions its own embed defines."""

import re
from pathlib import Path

from django.conf import settings


def test_every_toolbar_handler_is_defined_in_the_embed():
    for name in ("datafile_table.embed.html", "sops_table.embed.html"):
        html = (Path(settings.BASE_DIR) / "seek" / "templates" / "pages" / name).read_text()
        defined = set(re.findall(r"function\s+(\w+)\s*\(", html))
        called = set(re.findall(r'onclick="(\w+)\(', html))
        assert called <= defined, (name, called - defined)
