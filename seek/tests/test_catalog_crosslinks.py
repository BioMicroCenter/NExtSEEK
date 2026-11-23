"""Every page that mentions sample types or assays links into the catalog.

Read as text rather than rendered: these are static template edits, and a
rendering test would need the whole SEEK login stack to prove one href.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text()


def test_the_sidebar_offers_both_catalog_pages():
    nav = _read("themes/NextSeek/templates/nav.embed.html")
    assert '/seek/sampletypes/' in nav
    assert '/seek/assays/' in nav


def test_the_catalog_links_are_not_hidden_behind_the_superuser_gate():
    """They sit in the Data section, not Admin.

    The Admin block is wrapped in {% if request.user.is_superuser %}, and these
    pages are open to any logged-in user, so a link inside that block would hide
    the feature from almost everyone who can use it.
    """
    nav = _read("themes/NextSeek/templates/nav.embed.html")
    admin_gate = nav.index("{% if request.user.is_superuser %}")
    assert nav.index('/seek/sampletypes/') < admin_gate
    assert nav.index('/seek/assays/') < admin_gate


def test_the_templates_picker_links_each_code_to_its_catalog_page():
    picker = _read("seek/templates/templatesList.html")
    assert '/seek/sampletypes/{{ entry.code }}/' in picker


def test_the_picker_still_has_exactly_one_script_block():
    """seek/tests/js/harness.js lifts the picker's behaviour out with a regex and
    throws unless it finds exactly one block. An added <script> breaks that
    harness in a way no Python test would otherwise catch."""
    import re

    picker = _read("seek/templates/templatesList.html")
    assert len(re.findall(r"<script>[\s\S]*?</script>", picker)) == 1


def test_the_advanced_search_sample_type_label_links_to_the_catalog():
    search = _read("seek/templates/searchAdvanced.html")
    assert '/seek/sampletypes/' in search


def test_useful_info_links_the_api_docs_for_any_logged_in_user():
    """A relative link, so it works on every box; inside the logged-in block, since Swagger needs a login, and
    above the superuser gate, since any logged-in user may call the API."""
    nav = _read("themes/NextSeek/templates/nav.embed.html")
    link = nav.index('href="/nextseek_api/swagger/"')
    menu = nav.index('id="usefulInfoSubmenu"')
    assert menu < nav.index("{% if request.user.is_authenticated %}", menu) < link < nav.index("{% endif %}", menu + 1,
                                                                                            nav.index("</ul>", menu))
    assert link < nav.index("{% if request.user.is_superuser %}")
