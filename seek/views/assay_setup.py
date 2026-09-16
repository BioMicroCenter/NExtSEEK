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

import logging

from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from nextseek_api.services.assay_templates import resolve_components
from nextseek_api.services.context_catalog import load_assays
from nextseek_api.services.sample_workbook import render_template_workbook
from nextseek_api.services.template_catalog import select_entries

from ..decorators import requires_seek_login_redirect
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
    slug = (request.GET.get('assay') or "").strip()

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

    return {
        "groups": groups,
        "columns": ["Assay", "Consumes"],
        "total": total,
        "notice": notice,
        "message": message,
        "selected_slug": slug,
        "components": components,
        "can_create": request.user.is_superuser,
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
