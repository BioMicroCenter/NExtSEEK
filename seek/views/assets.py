"""Documents, SOPs, data files and the sample-sheet templates."""

from ..dbtable_documents import DBtable_documents
from ..responses import json_response
import os
from django.shortcuts import render
import requests
from ..decorators import requires_seek_login
from ..decorators import requires_seek_login_redirect
from django.conf import settings
import datetime
import json
import logging
import tempfile
from django.http import HttpResponse

from nextseek_api.services.sample_workbook import write_template_workbook
from nextseek_api.services.template_catalog import (
    GROUPS,
    MAX_SUGGESTIONS,
    load_catalog,
    load_relationships,
    load_type_links,
)

logger = logging.getLogger(__name__)

from .shared import report

@requires_seek_login
def document(request, id):
    document_id = id
    user_seek = request.user_seek

    seekdoc = DBtable_documents("DEFAULT")
    docurl, filename = seekdoc.getDownloadURL(document_id,
                user_seek['server'],
                user_seek['username'],
                user_seek['password'])
    
    if docurl is None:
        msg = 'Sample template is not available. Choose a template from the list.'
        status = 0
        docurl = ''
    else:
        msg = 'Sample template is downloaded in ' + filename
        status = 1
    return json_response(msg, status, docurl)

@requires_seek_login_redirect()
def sopQuery(request):
    report["seek_url"] = settings.SEEK_PUBLIC_URL

    return render(request, "sopsPage.html", {"report" : report})

@requires_seek_login_redirect()
def datafileQuery(request):
    report["seek_url"] = settings.SEEK_PUBLIC_URL

    return render(request, "dataFilesPage.html", {"report" : report})

def _templates_context(message=""):
    """Everything the picker template needs.

    Grouping and relationships come from template_catalog, so the view stays
    HTTP-only and the same data can be reused by the download path when it has
    to re-render.
    """
    entries = load_catalog()
    by_code = {e.code: e for e in entries}
    relationships = load_relationships(list(by_code), set(by_code))
    _links = load_type_links(set(by_code))

    groups = [
        {"key": key, "label": label,
         "entries": [e for e in entries if e.group == key]}
        for key, label in GROUPS
    ]
    return {
        "groups": [g for g in groups if g["entries"]],
        "message": message,
        # The strip is re-derived in the browser as boxes are ticked, so no
        # selection is ever rendered server-side -- see render() in
        # templatesList.html. Same one-hop children-only rule as
        # template_catalog.suggest(), which this mirrors; keep both in sync.
        "children_json": {
            code: rel.get("children", []) for code, rel in relationships.items()
        },
        "meta_json": {
            e.code: {"name": e.name, "group": e.group} for e in entries
        },
        # Requirements are keyed on the whole catalog, not the selection: the
        # page needs the rule for every type a user might tick, and the strip
        # is derived in the browser with no round trip.
        # Both directions in one read. `requires` runs child -> parent (what
        # an upload cannot omit); `companions` runs parent -> child (what it
        # will almost certainly also record). Keyed on the whole catalog, not
        # the selection: the page needs the rule for every type a user might
        # tick, and the strip is derived in the browser with no round trip.
        "requirements_json": _links["requires"],
        "companions_json": _links["companions"],
        "max_suggestions": MAX_SUGGESTIONS,
    }


@requires_seek_login_redirect('/seek/templates')
def templatesList(request):
    """The Download Templates picker.

    Login is required; project membership is not. Templates are schema
    definitions, not sample data, so there is nothing project-scoped to expose.
    """
    return render(request, 'templatesList.html', _templates_context())


@requires_seek_login_redirect('/seek/templates')
def templatesDownload(request):
    """Generate and stream one workbook for the selected sample types.

    The bytes are returned directly rather than written under outputs/ and
    linked: a template is cheap to regenerate, so a persistent link buys little
    and would leave a file per download behind with nothing to clean it up.
    """
    requested = request.POST.getlist('codes')
    entries = load_catalog()
    by_code = {e.code: e for e in entries}
    # Selection order, not catalog order: the sheets should come out in the
    # order the user built them. Unknown codes are dropped rather than 400ing --
    # a stale bookmark should still produce the types it names.
    chosen = []
    seen = set()
    for code in requested:
        if code in by_code and code not in seen:
            seen.add(code)
            chosen.append(by_code[code])

    if not chosen:
        context = _templates_context(message="Select at least one sample type.")
        return render(request, 'templatesList.html', context)

    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as handle:
        temp_path = handle.name
    try:
        write_template_workbook(chosen, temp_path)
        with open(temp_path, 'rb') as fh:
            payload = fh.read()
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            logger.warning("could not remove temp workbook %s", temp_path)

    stamp = datetime.datetime.now().strftime('%Y%m%d')
    filename = f"NExtSEEK_templates_{len(chosen)}types_{stamp}.xlsx"
    response = HttpResponse(
        payload,
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
    response['Content-Disposition'] = f'attachment; filename={filename}'
    return response
