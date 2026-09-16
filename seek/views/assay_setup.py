"""The assay setup page: pick an assay, download its sample sheet templates.

Login is required; project membership is not. An assay recipe is a schema
definition shared by every project, so there is nothing project-scoped to
expose -- the same rule and the same reasoning as `templatesList` in assets.py
and the catalog pages in catalog.py.

Its own module rather than an addition to catalog.py: that module's docstring
commits it to read-only pages over the context tables, and this one writes. It
does borrow catalog.py's table builder, so the picker and /seek/assays/ are one
table with two link targets rather than two tables that drift.

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
from .catalog import assay_table_groups

logger = logging.getLogger(__name__)

PAGE = 'assaySetup.html'
LOGIN_NEXT = '/seek/assay-setup/'


def _picker():
    """The assay table, and a notice if it could not be built.

    Same groups, columns and filter hooks as /seek/assays/, with rows pointing
    back here. Empty plus a notice on failure, never a 500: the house rule is
    that a missing table costs the offer, not the page.
    """
    try:
        entries = load_assays()
    except Exception:
        logger.exception("assay catalog unavailable; the picker will be empty")
        return [], 0, ("The assay catalog is unavailable. "
                       "Tell an administrator if this persists.")
    groups = assay_table_groups(
        entries, lambda e: f"/seek/assay-setup/?assay={e.slug}"
    )
    return groups, len(entries), ""


def _page_context(request, message=""):
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

    can_create = request.user.is_superuser

    return {
        "groups": groups,
        "columns": ["Assay", "Consumes"],
        "total": total,
        "notice": notice,
        "message": message,
        "selected_slug": slug,
        "components": components,
        "can_create": can_create,
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
        return render(request, PAGE, _page_context(request, message=UNREADABLE))

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
        return render(request, PAGE, _page_context(request, message=exc.message))

    return HttpResponseRedirect(f"/seek/assay-setup/?assay={slugify_name(name)}")
