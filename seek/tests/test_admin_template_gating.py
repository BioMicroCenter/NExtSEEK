"""Admin UI in the NextSeek theme must gate on is_superuser, never is_staff.

``dmac.views.userSynchronization`` sets ``is_staff = 1`` on every SEEK user, on
both the create branch (dmac/views.py:80) and the update branch that runs at
every single login (dmac/views.py:97). ``is_staff`` is therefore equivalent to
"is authenticated" in this project and carries no authorization meaning.

Observed on production 2026-08-21: charlie-test-3, a non-superuser, saw the
whole Admin sidebar section (Admin Panel, Sample Attributes, Clades) and a user
badge reading "Admin". Commit 864c1d38 had already corrected four Python call
sites to ``is_superuser`` but did not touch the templates, so the UI kept
claiming admin for everyone.

This is a source guard rather than a render test on purpose: it catches the
mistake anywhere in the theme, including in templates written later, which a
render test of two known files would not.
"""

from pathlib import Path

import pytest

# Only the NextSeek theme. templates/generic/ and templates/includes/ are
# upstream Mezzanine files whose is_staff usage (comment moderation, suppressing
# analytics for staff) is theirs to decide and is not an authorization gate on
# our admin surfaces.
THEME_ROOT = Path(__file__).resolve().parents[2] / "themes" / "NextSeek" / "templates"

FORBIDDEN = "request.user.is_staff"


def _theme_templates():
    if not THEME_ROOT.is_dir():
        pytest.skip(f"theme templates not present at {THEME_ROOT}")
    return sorted(THEME_ROOT.rglob("*.html"))


def test_theme_templates_exist():
    """Guard the guard: a bad path would make every assertion below vacuous."""
    assert _theme_templates(), f"no templates found under {THEME_ROOT}"


@pytest.mark.parametrize("template", _theme_templates(), ids=lambda p: p.name)
def test_no_admin_surface_gates_on_is_staff(template):
    text = template.read_text(encoding="utf-8")
    offending = [
        (n, line.strip())
        for n, line in enumerate(text.splitlines(), 1)
        if FORBIDDEN in line
    ]
    assert not offending, (
        f"{template.relative_to(THEME_ROOT)} gates on {FORBIDDEN!r}, which is true for "
        f"every logged-in SEEK user (dmac/views.py:80,97). Use request.user.is_superuser. "
        f"Offending lines: {offending}"
    )


def test_the_two_known_admin_gates_use_is_superuser():
    """Pin the specific sites from the production report, so a future edit that
    deletes the gate entirely (rather than weakening it) also fails."""
    nav = (THEME_ROOT / "nav.embed.html").read_text(encoding="utf-8")
    assert "{% if request.user.is_superuser %}" in nav, (
        "nav.embed.html no longer gates the Admin sidebar section on is_superuser"
    )

    panel = (THEME_ROOT / "accounts" / "includes" / "user_panel.html").read_text(encoding="utf-8")
    assert "{% if request.user.is_superuser %}{% trans \"Admin\" %}" in panel, (
        "user_panel.html no longer gates the Admin badge on is_superuser"
    )


# ---------------------------------------------------------------------------
# Django's {# #} comment is SINGLE-LINE ONLY. A multi-line one is not a comment
# at all -- it renders as visible page text. That shipped to production on
# 2026-08-21: the user badge read
#   "{# is_superuser, never is_staff -- ... #} Researcher"
# The source guard above could not catch it, because the gate itself was
# correct. Use {% comment %}/{% endcomment %} for anything multi-line.
# ---------------------------------------------------------------------------

import re

_OPEN = re.compile(r"\{#")
_SINGLE_LINE = re.compile(r"\{#.*?#\}")


@pytest.mark.parametrize("template", _theme_templates(), ids=lambda p: p.name)
def test_no_multiline_django_comments(template):
    bad = []
    for n, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1):
        # Every {# on the line must be closed on that same line.
        if len(_OPEN.findall(line)) != len(_SINGLE_LINE.findall(line)):
            bad.append((n, line.strip()))
    assert not bad, (
        f"{template.relative_to(THEME_ROOT)} opens a {{# #}} comment that does not close on "
        f"the same line. Django's {{# #}} is single-line only; a multi-line one RENDERS as "
        f"visible page text. Use {{% comment %}}/{{% endcomment %}}. Offending lines: {bad}"
    )


@pytest.mark.django_db
def test_admin_sidebar_renders_nothing_for_a_non_superuser():
    """Render the real template, not just scan it.

    Asserts two things at once: the admin links are absent for a non-superuser,
    and no template-comment text leaked into the output.
    """
    from django.template.loader import render_to_string
    from types import SimpleNamespace

    user = SimpleNamespace(
        is_authenticated=True, is_staff=True, is_superuser=False, username="researcher",
    )
    html = render_to_string("nav.embed.html", {"request": SimpleNamespace(user=user)})

    for forbidden in ("Admin Panel", "Clades", "/seek/admin/clades/",
                      "Internal Assays", "/seek/admin/internal_assays/"):
        assert forbidden not in html, f"non-superuser sees {forbidden!r} in the sidebar"
    for leaked in ("is_staff", "is_superuser", "dmac/views.py"):
        assert leaked not in html, f"template comment text leaked into the page: {leaked!r}"


@pytest.mark.django_db
def test_admin_sidebar_still_renders_for_a_superuser():
    """The gate must not be so tight that admins lose the section."""
    from django.template.loader import render_to_string
    from types import SimpleNamespace

    user = SimpleNamespace(
        is_authenticated=True, is_staff=True, is_superuser=True, username="admin",
    )
    html = render_to_string("nav.embed.html", {"request": SimpleNamespace(user=user)})

    assert "Admin Panel" in html
    assert "Clades" in html
    # The internal-assays admin page existed and was superuser-gated, but nothing
    # in the nav pointed at it, so admins could not reach it without the URL.
    assert "Internal Assays" in html
    assert "/seek/admin/internal_assays/" in html
