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
from nextseek_api.services.template_catalog import build_catalog, load_catalog

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
