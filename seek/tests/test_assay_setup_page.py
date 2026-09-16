"""The assay setup page.

Login required, project membership not: an assay recipe is a schema definition
shared by every project, the same rule templatesList and the catalog pages
already apply.
"""

import io
import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

import seek.views.assay_setup  # noqa: F401  -- so @patch can resolve the target
from nextseek_api.services.assay_templates import (
    AssayComponents,
    ComponentCode,
    ComponentGroup,
)
from nextseek_api.services.context_catalog import AssayEntry, AssayRow

FLOW = AssayComponents(
    slug="flow-cytometry", name="Flow Cytometry", is_split=False,
    required_parents=[ComponentGroup([
        ComponentCode("TIS", "Tissue", True, "", True),
        ComponentCode("CEX", "Cell Extract", True, "", False),
    ])],
    optional_parents=[ComponentGroup([
        ComponentCode("D.FCS", "", False, "No template sheet exists for this "
                                          "sample type.", False),
    ])],
    produces=[ComponentGroup([ComponentCode("D.FLOW", "Flow Data", True, "", True)])],
)

# Every entry carries a row: assay_table_groups reads e.rows[0] to build the
# table, so an AssayEntry with no rows is not a shape the picker ever sees.
ASSAYS = [
    AssayEntry(slug="flow-cytometry", name="Flow Cytometry", rows=[
        AssayRow(row_id=1, required_parents=[["TIS"]], children=[["D.FLOW"]],
                 parent_clade="Processed"),
    ]),
    AssayEntry(slug="rna-extraction", name="RNA Extraction", rows=[
        AssayRow(row_id=2, required_parents=[["TIS"]], children=[["RNA"]],
                 parent_clade="Processed"),
    ]),
]


def _logged_in():
    db = MagicMock()
    db.getSeekLogin.return_value = {
        "status": True, "server": "https://seek.example",
        "username": "demo", "password": "demopassword",
    }
    return db


def _anonymous():
    db = MagicMock()
    db.getSeekLogin.return_value = {"status": False, "err": "not logged in"}
    return db


def _get(path):
    req = RequestFactory().get(path)
    req.user = MagicMock()
    return req


class TestAssaySetupPage:
    @patch("seek.decorators.SeekDB")
    def test_anonymous_is_redirected_to_login(self, db):
        from seek.views.assay_setup import assaySetup

        db.return_value = _anonymous()
        resp = assaySetup(_get("/seek/assay-setup/"))
        assert resp.status_code == 302
        assert "/login/" in resp.url

    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.decorators.SeekDB")
    def test_it_lists_every_assay_to_pick_from(self, db, _assays):
        from seek.views.assay_setup import assaySetup

        db.return_value = _logged_in()
        resp = assaySetup(_get("/seek/assay-setup/"))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Flow Cytometry" in body and "RNA Extraction" in body
        # The picker's whole reason for existing (rather than reusing
        # /seek/assays/'s table verbatim) is that its rows point back here.
        assert 'href="/seek/assay-setup/?assay=flow-cytometry"' in body

    @patch("seek.views.assay_setup.resolve_components", return_value=FLOW)
    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.decorators.SeekDB")
    def test_an_assay_query_renders_its_components(self, db, _assays, _resolve):
        from seek.views.assay_setup import assaySetup

        db.return_value = _logged_in()
        resp = assaySetup(_get("/seek/assay-setup/?assay=flow-cytometry"))
        body = resp.content.decode()
        assert "TIS" in body and "D.FLOW" in body

    @patch("seek.views.assay_setup.resolve_components", return_value=FLOW)
    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.decorators.SeekDB")
    def test_an_undownloadable_component_says_why(self, db, _assays, _resolve):
        from seek.views.assay_setup import assaySetup

        db.return_value = _logged_in()
        resp = assaySetup(_get("/seek/assay-setup/?assay=flow-cytometry"))
        assert "No template sheet exists" in resp.content.decode()

    @patch("seek.views.assay_setup.resolve_components", return_value=None)
    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.decorators.SeekDB")
    def test_an_unknown_assay_is_a_404(self, db, _assays, _resolve):
        from django.http import Http404

        from seek.views.assay_setup import assaySetup

        db.return_value = _logged_in()
        with pytest.raises(Http404):
            assaySetup(_get("/seek/assay-setup/?assay=nope"))

    @patch("seek.views.assay_setup.load_assays", side_effect=RuntimeError("boom"))
    @patch("seek.decorators.SeekDB")
    def test_an_unreadable_table_renders_a_notice_not_a_500(self, db, _assays):
        """House rule: a missing table costs the offer, never the page."""
        from seek.views.assay_setup import assaySetup

        db.return_value = _logged_in()
        resp = assaySetup(_get("/seek/assay-setup/"))
        assert resp.status_code == 200
        assert "The assay catalog is unavailable" in resp.content.decode()


def _post(path, data):
    req = RequestFactory().post(path, data)
    req.user = MagicMock()
    return req


class TestAssaySetupDownload:
    @patch("seek.decorators.SeekDB")
    def test_anonymous_is_redirected_to_login(self, db):
        from seek.views.assay_setup import assaySetupDownload

        db.return_value = _anonymous()
        resp = assaySetupDownload(_post("/seek/assay-setup/download/", {}))
        assert resp.status_code == 302

    @patch("seek.views.assay_setup.render_template_workbook",
           return_value=(io.BytesIO(b"xlsx-bytes"), "templates.xlsx"))
    @patch("seek.views.assay_setup.select_entries", return_value=(["e"], []))
    @patch("seek.decorators.SeekDB")
    def test_it_streams_the_workbook(self, db, select, render):
        from seek.views.assay_setup import assaySetupDownload

        db.return_value = _logged_in()
        resp = assaySetupDownload(
            _post("/seek/assay-setup/download/", {"codes": ["TIS", "D.FLOW"]})
        )
        assert resp.status_code == 200
        assert resp.content == b"xlsx-bytes"
        assert "templates.xlsx" in resp["Content-Disposition"]

    @patch("seek.views.assay_setup.render_template_workbook",
           return_value=(io.BytesIO(b"x"), "t.xlsx"))
    @patch("seek.views.assay_setup.select_entries", return_value=(["e"], []))
    @patch("seek.decorators.SeekDB")
    def test_radio_groups_are_collected_alongside_the_checkboxes(
        self, db, select, render
    ):
        """An alternation group posts under codes-<prefix>-<n>, not codes."""
        from seek.views.assay_setup import assaySetupDownload

        db.return_value = _logged_in()
        assaySetupDownload(_post("/seek/assay-setup/download/", {
            "codes": ["D.FLOW"], "codes-req-1": ["TIS"], "codes-opt-1": ["AB"],
        }))
        posted = select.call_args.args[0]
        assert set(posted) == {"D.FLOW", "TIS", "AB"}

    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.views.assay_setup.select_entries", return_value=([], []))
    @patch("seek.decorators.SeekDB")
    def test_an_empty_selection_re_renders_this_page_with_a_message(
        self, db, select, _assays
    ):
        """templatesDownload re-renders templatesList.html on empty, which would
        drop the user on the wrong page. This is why it has its own view."""
        from seek.views.assay_setup import assaySetupDownload

        db.return_value = _logged_in()
        resp = assaySetupDownload(_post("/seek/assay-setup/download/", {}))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Select at least one" in body
        # templatesList.html also renders `message`, and Django tolerates the
        # context keys this view never sets (`components`, `groups`, ...), so
        # asserting on the message alone passes just as green if the view
        # regressed to rendering templatesList.html instead. Assert on
        # something only assaySetup.html's own header renders.
        assert '<h1 class="ns-page-title">Assay setup</h1>' in body

    @patch("seek.views.assay_setup.resolve_components", return_value=FLOW)
    @patch("seek.views.assay_setup.select_entries", return_value=([], []))
    @patch("seek.decorators.SeekDB")
    def test_an_empty_submit_re_renders_the_originating_assay(
        self, db, select, _resolve
    ):
        """An assay whose components are all non-downloadable renders every
        control disabled, so submitting with nothing ticked is reachable. The
        download form's hidden `assay` field must carry the slug through that
        POST, or the view has nothing to fall back to and drops the user back
        on the picker instead of the assay they were working on."""
        from seek.views.assay_setup import assaySetupDownload

        db.return_value = _logged_in()
        resp = assaySetupDownload(_post(
            "/seek/assay-setup/download/", {"assay": "flow-cytometry"}
        ))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Select at least one" in body
        # The SAME assay's components, not the picker.
        assert "TIS" in body and "D.FLOW" in body
        assert "Flow Cytometry" in body

    @patch("seek.views.assay_setup.resolve_components", return_value=None)
    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.views.assay_setup.select_entries", return_value=([], []))
    @patch("seek.decorators.SeekDB")
    def test_an_unknown_assay_posted_on_an_empty_submit_is_still_a_404(
        self, db, select, _assays, _resolve
    ):
        """The POST fallback must not relax the 404 an unknown slug already
        gets via GET."""
        from django.http import Http404

        from seek.views.assay_setup import assaySetupDownload

        db.return_value = _logged_in()
        with pytest.raises(Http404):
            assaySetupDownload(_post("/seek/assay-setup/download/", {"assay": "nope"}))

    @patch("seek.views.assay_setup.render_template_workbook",
           return_value=(io.BytesIO(b"x"), "t.xlsx"))
    @patch("seek.views.assay_setup.select_entries", return_value=(["e"], ["NOPE"]))
    @patch("seek.decorators.SeekDB")
    def test_an_unknown_code_is_dropped_rather_than_failing_the_download(
        self, db, select, render
    ):
        """Same rule templatesDownload applies: a stale bookmark still produces
        the types it names."""
        from seek.views.assay_setup import assaySetupDownload

        db.return_value = _logged_in()
        resp = assaySetupDownload(
            _post("/seek/assay-setup/download/", {"codes": ["TIS", "NOPE"]})
        )
        assert resp.status_code == 200
        assert resp.content == b"x"


def _superuser_post(data):
    req = RequestFactory().post("/seek/assay-setup/create/", data)
    req.user = MagicMock()
    req.user.is_superuser = True
    return req


def _plain_post(data):
    req = RequestFactory().post("/seek/assay-setup/create/", data)
    req.user = MagicMock()
    req.user.is_superuser = False
    return req


GOOD = {
    "name": "Spectral Flow Cytometry",
    "description": "Full-spectrum single-cell analysis.",
    "required_parents": json.dumps([["TIS", "CEX"], ["AB"]]),
    "optional_parents": json.dumps([]),
    "children": json.dumps([["D.FLOW"]]),
}


class TestAssaySetupCreate:
    @patch("seek.views.assay_setup.verifySuperUser", return_value=0)
    @patch("seek.decorators.SeekDB")
    def test_a_non_superuser_is_refused(self, db, _super):
        from seek.views.assay_setup import assaySetupCreate

        db.return_value = _logged_in()
        resp = assaySetupCreate(_plain_post(GOOD))
        assert resp.status_code == 403

    @patch("seek.views.assay_setup.create_assay", return_value=7)
    @patch("seek.views.assay_setup.verifySuperUser", return_value=1)
    @patch("seek.decorators.SeekDB")
    def test_a_superuser_creates_and_is_sent_to_the_new_assay(
        self, db, _super, create
    ):
        from seek.views.assay_setup import assaySetupCreate

        db.return_value = _logged_in()
        resp = assaySetupCreate(_superuser_post(GOOD))
        assert resp.status_code == 302
        assert "assay=spectral-flow-cytometry" in resp.url
        # A redirect alone would also pass a view that never called
        # `create_assay` at all -- assert the write actually happened, and
        # that the description reaches it (a name/description swap would
        # otherwise pass the entire suite).
        create.assert_called_once()
        assert create.call_args.kwargs["name"] == GOOD["name"]
        assert create.call_args.kwargs["description"] == GOOD["description"]

    # `downloadable_codes` has to be patched, not left to fail soft: the view
    # allowlists every posted code against it, and in this hermetic container
    # the real one logs "catalog unavailable" and returns an empty set, which
    # would drop all four codes and make the assertion below pass vacuously in
    # the opposite direction -- an empty list is not the grouping surviving.
    @patch("seek.views.assay_setup.downloadable_codes",
           return_value={"TIS", "CEX", "AB", "D.FLOW"})
    @patch("seek.views.assay_setup.create_assay", return_value=7)
    @patch("seek.views.assay_setup.verifySuperUser", return_value=1)
    @patch("seek.decorators.SeekDB")
    def test_the_group_structure_survives_the_form(self, db, _super, create, _codes):
        """"TIS or CEX, AB" must not arrive as three independent codes."""
        from seek.views.assay_setup import assaySetupCreate

        db.return_value = _logged_in()
        assaySetupCreate(_superuser_post(GOOD))
        assert create.call_args.kwargs["required_parents"] == [["TIS", "CEX"], ["AB"]]

    @patch("seek.views.assay_setup.downloadable_codes",
           return_value={"TIS", "CEX", "D.FLOW"})
    @patch("seek.views.assay_setup.create_assay", return_value=7)
    @patch("seek.views.assay_setup.verifySuperUser", return_value=1)
    @patch("seek.decorators.SeekDB")
    def test_a_code_with_no_template_sheet_is_dropped(
        self, db, _super, create, _codes
    ):
        """The pickers are built from downloadable_codes(), so a code outside
        that set can only arrive from a hand-made POST. Keeping it would make
        the new assay offer a download that resolves to nothing."""
        from seek.views.assay_setup import assaySetupCreate

        db.return_value = _logged_in()
        assaySetupCreate(_superuser_post(GOOD))
        # "AB" is not downloadable here, and it was the whole second group.
        assert create.call_args.kwargs["required_parents"] == [["TIS", "CEX"]]
        assert create.call_args.kwargs["children"] == [["D.FLOW"]]

    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.views.assay_setup.verifySuperUser", return_value=1)
    @patch("seek.decorators.SeekDB")
    def test_malformed_json_is_a_message_not_a_500(self, db, _super, _assays):
        from seek.views.assay_setup import assaySetupCreate

        db.return_value = _logged_in()
        resp = assaySetupCreate(_superuser_post({**GOOD, "children": "{{{"}))
        assert resp.status_code == 200
        assert "could not be read" in resp.content.decode()

    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.views.assay_setup.verifySuperUser", return_value=1)
    @patch("seek.decorators.SeekDB")
    def test_a_validation_error_is_shown_on_the_page(self, db, _super, _assays):
        from nextseek_api.services.assay_authoring import ValidationError
        from seek.views.assay_setup import assaySetupCreate

        db.return_value = _logged_in()
        with patch("seek.views.assay_setup.create_assay",
                   side_effect=ValidationError("Give the assay a name.")):
            resp = assaySetupCreate(_superuser_post({**GOOD, "name": ""}))
        assert resp.status_code == 200
        assert "Give the assay a name." in resp.content.decode()


def _seeded_groups(body):
    """The `assay-form-groups` payload the page re-echoes, as Python."""
    tag = body.split('id="assay-form-groups"')[1]
    return json.loads(tag.split(">", 1)[1].split("</script>")[0])


class TestARefusalKeepsWhatWasTyped:
    """A refused proposal must come back with the form still filled in.

    Both error paths re-render the page. Without an echo the superuser loses the
    name, the definition and every requirement they built by hand, with no back
    button that recovers a POST -- and the group builder starts from empty, so
    the structure is gone rather than merely unsaved.
    """

    HOSTILE_NAME = '</script><script>alert(1)//'

    def _refused(self, message="An assay with this name already exists (x)."):
        from nextseek_api.services.assay_authoring import ValidationError

        return patch("seek.views.assay_setup.create_assay",
                     side_effect=ValidationError(message))

    @patch("seek.views.assay_setup.downloadable_codes",
           return_value={"TIS", "CEX", "AB", "D.FLOW"})
    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.views.assay_setup.verifySuperUser", return_value=1)
    @patch("seek.decorators.SeekDB")
    def test_a_rejected_proposal_comes_back_with_its_name_and_definition(
        self, db, _super, _assays, _codes
    ):
        from seek.views.assay_setup import assaySetupCreate

        db.return_value = _logged_in()
        with self._refused():
            body = assaySetupCreate(_superuser_post(GOOD)).content.decode()
        assert 'value="Spectral Flow Cytometry"' in body
        assert "Full-spectrum single-cell analysis." in body

    @patch("seek.views.assay_setup.downloadable_codes",
           return_value={"TIS", "CEX", "AB", "D.FLOW"})
    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.views.assay_setup.verifySuperUser", return_value=1)
    @patch("seek.decorators.SeekDB")
    def test_the_group_structure_comes_back_for_the_builder(
        self, db, _super, _assays, _codes
    ):
        """The hand-built requirements, not just the two text fields."""
        from seek.views.assay_setup import assaySetupCreate

        db.return_value = _logged_in()
        with self._refused():
            body = assaySetupCreate(_superuser_post(GOOD)).content.decode()
        assert 'id="assay-form-groups"' in body
        seeded = _seeded_groups(body)
        assert seeded["required_parents"] == [["TIS", "CEX"], ["AB"]]
        assert seeded["children"] == [["D.FLOW"]]

    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.views.assay_setup.verifySuperUser", return_value=1)
    @patch("seek.decorators.SeekDB")
    def test_an_unreadable_payload_still_returns_the_text_fields(
        self, db, _super, _assays
    ):
        """Nothing trustworthy to rebuild the builder from, but the two things
        the user typed by hand are not thrown away with it."""
        from seek.views.assay_setup import assaySetupCreate

        db.return_value = _logged_in()
        resp = assaySetupCreate(_superuser_post({**GOOD, "children": "{{{"}))
        body = resp.content.decode()
        assert "could not be read" in body
        assert 'value="Spectral Flow Cytometry"' in body
        assert "Full-spectrum single-cell analysis." in body

    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.views.assay_setup.verifySuperUser", return_value=1)
    @patch("seek.decorators.SeekDB")
    def test_a_hostile_name_is_echoed_escaped(self, db, _super, _assays):
        """The echo is attacker-controlled by definition -- it is whatever was
        posted. It reaches an ordinary auto-escaped attribute, never a script,
        so the sequence that would close one must not appear in the page."""
        from seek.views.assay_setup import assaySetupCreate

        db.return_value = _logged_in()
        body = assaySetupCreate(_superuser_post({
            **GOOD, "name": self.HOSTILE_NAME, "children": "{{{"
        })).content.decode()
        assert self.HOSTILE_NAME not in body
        assert "&lt;/script&gt;" in body

    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.decorators.SeekDB")
    def test_a_plain_get_renders_an_empty_form(self, db, _assays):
        """The echo must not make the ordinary page arrive pre-filled."""
        from seek.views.assay_setup import assaySetup

        db.return_value = _logged_in()
        req = _get("/seek/assay-setup/")
        req.user.is_superuser = True
        body = assaySetup(req).content.decode()
        name_tag = body.split('id="new-name"')[1].split(">")[0]
        assert 'value=""' in name_tag
        seeded = _seeded_groups(body)
        assert seeded == {"required_parents": [], "optional_parents": [],
                          "children": []}


class TestCreateFormVisibility:
    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.decorators.SeekDB")
    def test_the_form_is_not_rendered_for_a_non_superuser(self, db, _assays):
        """Not rendered rather than rendered-and-refused, so nobody fills it in
        to be told no."""
        from seek.views.assay_setup import assaySetup

        db.return_value = _logged_in()
        req = _get("/seek/assay-setup/")
        req.user.is_superuser = False
        assert "assay-setup/create/" not in assaySetup(req).content.decode()

    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.decorators.SeekDB")
    def test_the_form_is_rendered_for_a_superuser(self, db, _assays):
        from seek.views.assay_setup import assaySetup

        db.return_value = _logged_in()
        req = _get("/seek/assay-setup/")
        req.user.is_superuser = True
        assert "assay-setup/create/" in assaySetup(req).content.decode()

    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.decorators.SeekDB")
    def test_an_unauthenticated_user_is_not_shown_the_form(self, db, _assays):
        """The render and the POST must ask the same question. `verifySuperUser`
        -- what `assaySetupCreate` enforces with -- also requires an
        authenticated user, so a request that would be refused a 403 must not
        be handed the form to fill in."""
        from seek.views.assay_setup import assaySetup

        db.return_value = _logged_in()
        req = _get("/seek/assay-setup/")
        req.user.is_superuser = True
        req.user.is_authenticated = False
        assert "assay-setup/create/" not in assaySetup(req).content.decode()


class TestCodeOptionsAreNotTrustedInput:
    # A sample-type TITLE is ordinary-project-member-writable, not trusted
    # input -- `template_catalog._is_legal_sheet_name` only rejects titles
    # over 31 characters or containing `[]:*?/\`; `<`, `>` and `"` are all
    # legal. This 27-character title is a `</script>` close followed by a new
    # `<script>` open, so it must never reach the page unescaped: if it does,
    # it runs as script on the NEXtSEEK origin in the browser of any
    # superuser who opens this page, with that session's CSRF token in the
    # DOM -- privilege escalation from ordinary member to superuser.
    HOSTILE = "</script><script>alert(1)//"

    @patch("seek.views.assay_setup.downloadable_codes",
           return_value={"TIS", HOSTILE})
    @patch("seek.views.assay_setup.load_assays", return_value=ASSAYS)
    @patch("seek.decorators.SeekDB")
    def test_a_hostile_sample_type_title_cannot_break_out_of_the_script(
        self, db, _assays, _codes
    ):
        from seek.views.assay_setup import assaySetup

        db.return_value = _logged_in()
        req = _get("/seek/assay-setup/")
        req.user.is_superuser = True
        body = assaySetup(req).content.decode()
        # Not merely "escaped somehow" -- the exact sequence that would end
        # the options payload's own `<script>` element and open an attacker
        # one must not appear anywhere in the response.
        assert self.HOSTILE not in body
        # And not vacuously: the options payload must actually have reached
        # the page. Without this, deleting the `json_script` line entirely
        # would still pass the assertion above while silently breaking the
        # create form.
        assert 'id="assay-code-options"' in body
