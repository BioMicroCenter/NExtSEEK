"""``seek/urls.py`` must stay wired to real, reversible views.

This is the test that guards the view-package split (plan Step 9): every entry in
``urlpatterns`` names a callable, and every named pattern that takes no arguments
survives a ``reverse()``.

Note that importing ``seek.urls`` at all is itself a check -- ``urls.py``
references ``views.<name>`` by attribute, so a view deleted or renamed without
updating the URL conf raises ``AttributeError`` here rather than at first request.
"""

import pytest
from django.urls import reverse

from .. import urls

PATTERNS = list(urls.urlpatterns)

# Named patterns whose regex captures nothing, so reverse() needs no arguments.
REVERSIBLE = sorted(
    p.name for p in PATTERNS if p.name and p.pattern.regex.groups == 0
)


def test_urlpatterns_is_not_empty():
    assert len(PATTERNS) >= 50, len(PATTERNS)


@pytest.mark.parametrize("pattern", PATTERNS, ids=lambda p: str(p.pattern))
def test_pattern_points_at_a_callable(pattern):
    assert callable(pattern.callback), pattern


@pytest.mark.parametrize("name", REVERSIBLE)
def test_named_pattern_reverses(name):
    assert reverse(name).startswith("/")


def test_url_names_are_unique():
    """A duplicate name silently makes one pattern unreachable by reverse()."""
    names = [p.name for p in PATTERNS if p.name]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    assert not duplicates, duplicates
