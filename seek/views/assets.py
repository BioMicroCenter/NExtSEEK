"""Documents, SOPs, data files and the sample-sheet templates."""

from ..dbtable_documents import DBtable_documents
from ..responses import json_response
from django.shortcuts import render
import requests
from ..decorators import requires_seek_login
from ..decorators import requires_seek_login_redirect
from django.conf import settings
import json
import logging
from django.http import HttpResponse

from nextseek_api.services.sample_workbook import render_template_workbook
from nextseek_api.services.template_catalog import build_catalog, select_entries

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
    """Adapt build_catalog() to the names templatesList.html reads.

    The payload is assembled in nextseek_api.services.template_catalog so that
    this page and /nextseek_api/templates/catalog/ cannot drift apart. All this
    function does is rename keys and add the flash message.
    """
    payload = build_catalog()
    entries = [entry for group in payload["groups"] for entry in group["entries"]]
    return {
        "groups": payload["groups"],
        "message": message,
        "children_json": payload["children"],
        "meta_json": {
            e.code: {"name": e.name, "group": e.group} for e in entries
        },
        "requirements_json": payload["requires"],
        "companions_json": payload["companions"],
        "max_suggestions": payload["max_suggestions"],
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

    Which entries and what they become are both decided in
    nextseek_api.services, so this page and /nextseek_api/templates/generate/
    cannot hand out different workbooks. The one thing this view still decides
    for itself is what an unknown code costs: it is dropped, so a stale bookmark
    still produces the types it names. The API 422s instead.
    """
    chosen, _unknown = select_entries(request.POST.getlist('codes'))

    if not chosen:
        context = _templates_context(message="Select at least one sample type.")
        return render(request, 'templatesList.html', context)

    buffer, filename = render_template_workbook(chosen)
    response = HttpResponse(
        buffer.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
    response['Content-Disposition'] = f'attachment; filename={filename}'
    return response
