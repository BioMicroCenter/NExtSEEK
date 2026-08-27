"""Documents, SOPs, data files and the sample-sheet templates."""

from ..dbtable_documents import DBtable_documents
from ..responses import json_response
import os
from django.shortcuts import render
import requests
from ..decorators import requires_seek_login
from ..decorators import requires_seek_login_redirect
from django.conf import settings

from .shared import SAMPLE_TEMPLATES_FOLDER, SAMPLE_TEMPLATES_FOLDER_PROJECT, report

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

@requires_seek_login_redirect('/seek/templates')
def templatesList(request):
    user_seek = request.user_seek

    # Basic auth is encoded here rather than passed as requests' auth= so a
    # non-Latin-1 password doesn't raise UnicodeEncodeError before the
    # request is sent. See nextseek_api.helpers.basic_auth_header (#52).
    from nextseek_api.helpers import basic_auth_header
    headers = {'Accept': 'application/json'}
    headers.update(basic_auth_header((user_seek['username'], user_seek['password'])))
    r = requests.get(user_seek['server'] + '/projects', headers=headers)
    projects = [p['id'] for p in r.json()['data']]
    if not SAMPLE_TEMPLATES_FOLDER_PROJECT in projects:
        msg = 'You are not in the correct project to access this page'
        status = 0
        return json_response(msg, status)

    folders = getTemplateFolders(SAMPLE_TEMPLATES_FOLDER)

    return render(request, 'templatesList.html', {'folders': folders})

def getTemplateFolders(directory_path):
    folders = {}
    try:
        for item in os.listdir(directory_path):
            path = os.path.join(directory_path, item)
            if os.path.isdir(path):
                folders[item] = getTemplateFolders(path)
            else:
                folders[item] = None
    except OSError:
        return {}
    return folders
