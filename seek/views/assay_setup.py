"""The assay setup page: pick an assay, download its sample sheet templates.

Login is required; project membership is not. An assay recipe is a schema
definition shared by every project, so there is nothing project-scoped to
expose -- the same rule and the same reasoning as `templatesList` in assets.py
and the catalog pages in catalog.py.

Its own module rather than an addition to catalog.py: that module's docstring
commits it to read-only pages over the context tables, and this one writes. It
borrows catalog.py's clade grouping and its filter text, so the picker and
/seek/assays/ group, order and search assays identically; the row shape is this
page's own, because the two pages link different things.

HTTP only. Everything these views know comes from
`nextseek_api.services.assay_templates` and `.assay_authoring`, so no query
lives here.
"""

import json
import logging

from django.http import (Http404, HttpResponse, HttpResponseForbidden,
                         HttpResponseRedirect)
from django.shortcuts import render
from django.views.decorators.http import require_POST

from nextseek_api.services.assay_authoring import ValidationError, create_assay
from nextseek_api.services.assay_templates import resolve_components
from nextseek_api.services.context_catalog import load_assays, slugify_name
from nextseek_api.services.sample_workbook import render_template_workbook
from nextseek_api.services.template_catalog import (downloadable_codes,
                                                    select_entries)

from ..decorators import requires_seek_login_redirect, verifySuperUser
from .catalog import assay_clade_buckets, assay_filter_text, distinct_codes

logger = logging.getLogger(__name__)

PAGE = 'assaySetup.html'
LOGIN_NEXT = '/seek/assay-setup/'


def _picker_groups(entries):
    """Curated assays as the picker list's groups, by consumed clade.

    Row = {href, name, consumes, produces, filter_text}. The NAME is the link,
    which is the whole reason this is not `/seek/assays/`'s table: that table
    links its leading `code` cell, which carries the PRODUCED sample-type code,
    so on a page for picking the assay you are running the assay name was the
    one thing on the row you could not click -- and the 72 curated assays of
    193 that produce nothing rendered that cell as an em dash and had no
    clickable element at all.

    `consumes` and `produces` are read across EVERY row of the entry, not just
    the first. 24 slugs carry two unreconciled rows, and a first row that names
    no children would otherwise report an assay as having no outputs while the
    second row names them.

    Consumed means REQUIRED parents, matching the catalog table's Consumes
    column. Optional parents are left to `assayDetail`, which has the room to
    label them: listing them here would read as requirements.
    """
    groups = []
    for g in assay_clade_buckets(entries):
        rows = []
        for e in g["entries"]:
            rows.append({
                "href": f"/seek/assay-setup/?assay={e.slug}",
                "name": e.name,
                "consumes": distinct_codes(*[r.required_parents for r in e.rows]),
                "produces": distinct_codes(*[r.children for r in e.rows]),
                "filter_text": assay_filter_text(e),
            })
        groups.append({"clade": g["clade"], "clade_slug": g["clade_slug"],
                       "rows": rows, "collapsed": g["collapsed"]})
    return groups


def _picker():
    """The assay list, and a notice if it could not be built.

    Same grouping, order and filter hooks as /seek/assays/, with the assay name
    linking back here. Empty plus a notice on failure, never a 500: the house
    rule is that a missing table costs the offer, not the page.
    """
    try:
        entries = load_assays()
    except Exception:
        logger.exception("assay catalog unavailable; the picker will be empty")
        return [], 0, ("The assay catalog is unavailable. "
                       "Tell an administrator if this persists.")
    return _picker_groups(entries), len(entries), ""


def _blank_form() -> dict:
    """An empty create form: what a GET renders, and the shape `form` always has."""
    return {"name": "", "description": "",
            "groups": {role: [] for role in ROLES}}


def _page_context(request, message="", form=None):
    # GET is the primary source, so the plain GET page (no POST body at all)
    # is unaffected; POST is the fallback so an empty download submission --
    # nothing ticked -- can still say which assay it was for. The download
    # form's hidden `assay` field is what carries it; `_posted_codes` never
    # sees that key, since it only collects `codes` and `codes-<prefix>-<n>`.
    slug = (request.GET.get('assay') or request.POST.get('assay') or "").strip()

    components = None
    if slug:
        # An assay is selected: the picker would only be built to be thrown
        # away, and building it runs `load_assays()` a second time (the first
        # is inside `resolve_components` -> `load_assay`), so skip it.
        groups, total, notice = [], 0, ""
        components = resolve_components(slug)
        if components is None:
            raise Http404(f"no assay {slug!r}")
    else:
        groups, total, notice = _picker()

    # The same question `assaySetupCreate` enforces with, not a near-miss:
    # `verifySuperUser` also requires an authenticated user, so an anonymous
    # request whose `user.is_superuser` is somehow truthy cannot be shown a
    # form the POST would refuse.
    can_create = verifySuperUser(request) == 1

    return {
        "groups": groups,
        "total": total,
        "notice": notice,
        "message": message,
        "selected_slug": slug,
        "components": components,
        "can_create": can_create,
        # What the superuser typed, re-rendered on an error path. Blank on a
        # GET. Without it a rejected proposal costs the name, the definition
        # and the whole hand-built group structure, with no way back.
        "form": form or _blank_form(),
        "roles": [
            {"field": "required_parents", "label": "Required parent sample types"},
            {"field": "optional_parents", "label": "Optional parent sample types"},
            {"field": "children", "label": "Produces"},
        ],
        # Only a superuser ever sees the create form, so only a superuser's
        # render pays for `downloadable_codes()` -- `Sample_types.objects.all()`
        # plus `load_sample_type_context()`, two extra queries otherwise paid
        # by every logged-in render, including the 404 path and the download
        # view's empty re-render.
        "code_options": sorted(downloadable_codes()) if can_create else [],
    }


@requires_seek_login_redirect(LOGIN_NEXT)
def assaySetup(request):
    """Pick an assay and see what a template download for it would contain."""
    return render(request, PAGE, _page_context(request))


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _posted_codes(request) -> list:
    """Every code the form carried, checkboxes and alternation radios alike.

    A group of one posts as a `codes` checkbox; an alternation group posts as a
    radio under `codes-<prefix>-<n>`, which is what makes the browser enforce
    pick-one without any JavaScript. Both have to be collected here, or a
    download would silently omit every alternation the user resolved.
    """
    codes = list(request.POST.getlist('codes'))
    for key in request.POST:
        if key.startswith('codes-'):
            codes.extend(request.POST.getlist(key))
    return codes


@require_POST
@requires_seek_login_redirect(LOGIN_NEXT)
def assaySetupDownload(request):
    """Stream one workbook for the components the user ticked.

    Resolution and rendering are both `nextseek_api.services` calls, the same
    two `templatesDownload` makes, so this page and /seek/templates/ cannot hand
    out different workbooks.

    Its own view rather than a POST to `templatesDownload`: that one re-renders
    templatesList.html when the selection is empty, which would drop the user on
    a different page than the one they were using. Unknown codes are dropped for
    the reason it states -- a stale bookmark still produces the types it names.
    """
    chosen, _unknown = select_entries(_posted_codes(request))

    if not chosen:
        context = _page_context(request, message="Select at least one sample type.")
        return render(request, PAGE, context)

    buffer, filename = render_template_workbook(chosen)
    response = HttpResponse(buffer.getvalue(), content_type=XLSX)
    response['Content-Disposition'] = f'attachment; filename={filename}'
    return response


# The three role fields the create form posts. Read at call time by
# `_blank_form` above, which is why it may be defined further down.
ROLES = ("required_parents", "optional_parents", "children")
UNREADABLE = ("The sample types could not be read from the form. "
              "Reload the page and try again.")


def _posted_groups(request) -> dict:
    """Each role as `list[list[str]]`, or raise ValueError.

    The form posts JSON so that the grouping survives -- "TIS or CEX, AB" is two
    pick-one groups and would arrive as three independent codes through ordinary
    form fields, which no later edit could recover.

    Every code is checked against `downloadable_codes()`: a code with no sheet
    would make the new assay offer a download that resolves to nothing, which is
    the exact defect `template_catalog` warns about.
    """
    known = downloadable_codes()
    groups = {}
    for role in ROLES:
        parsed = json.loads(request.POST.get(role) or "[]")
        if not isinstance(parsed, list):
            raise ValueError(f"{role} is not a list")
        cleaned = []
        for group in parsed:
            if not isinstance(group, list):
                raise ValueError(f"{role} group is not a list")
            codes = [str(c).strip() for c in group if str(c).strip() in known]
            if codes:
                cleaned.append(codes)
        groups[role] = cleaned
    return groups


def _echoed_form(request, groups=None) -> dict:
    """What the superuser typed, so a refusal re-renders it rather than losing it.

    `groups` is the already-validated structure from `_posted_groups`, or None
    when the payload could not be read at all -- in which case there is nothing
    trustworthy to echo into the builder and only the two text fields come back.

    The codes here have been through `downloadable_codes()`, and they reach the
    page through `json_script` rather than through a template string, so neither
    half of this can put attacker-controlled text into a script element.
    """
    echoed = _blank_form()
    echoed["name"] = request.POST.get("name", "")
    echoed["description"] = request.POST.get("description", "")
    if groups:
        echoed["groups"] = {role: groups.get(role, []) for role in ROLES}
    return echoed


@require_POST
@requires_seek_login_redirect(LOGIN_NEXT)
def assaySetupCreate(request):
    """Create an assay: one assay_context row and one internal_assays term.

    Superuser only, matching every other vocabulary write in this app
    (`internalAssaySave`, `cladeSave`). The gate is enforced here as well as by
    hiding the form, because a hidden form is not an access control.
    """
    if verifySuperUser(request) != 1:
        logger.error("non-superuser attempted to create an assay: %s",
                     request.user.get_username())
        return HttpResponseForbidden("Superuser required.")

    try:
        groups = _posted_groups(request)
    except (ValueError, TypeError):
        logger.warning("malformed assay creation payload")
        return render(request, PAGE, _page_context(
            request, message=UNREADABLE, form=_echoed_form(request)))

    name = request.POST.get('name', '')
    try:
        create_assay(
            name=name,
            description=request.POST.get('description', ''),
            required_parents=groups["required_parents"],
            optional_parents=groups["optional_parents"],
            children=groups["children"],
        )
    except ValidationError as exc:
        return render(request, PAGE, _page_context(
            request, message=exc.message, form=_echoed_form(request, groups)))

    return HttpResponseRedirect(f"/seek/assay-setup/?assay={slugify_name(name)}")
