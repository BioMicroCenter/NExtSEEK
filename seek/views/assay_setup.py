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

from django.http import Http404
from django.shortcuts import render

from nextseek_api.services.assay_templates import resolve_components
from nextseek_api.services.context_catalog import load_assays

from ..decorators import requires_seek_login_redirect
from .catalog import assay_table_groups

logger = logging.getLogger(__name__)

PAGE = 'assaySetup.html'
LOGIN_NEXT = '/seek/assay-setup'


def _picker(request):
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
    groups, total, notice = _picker(request)
    slug = (request.GET.get('assay') or "").strip()

    components = None
    if slug:
        components = resolve_components(slug)
        if components is None:
            raise Http404(f"no assay {slug!r}")

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
