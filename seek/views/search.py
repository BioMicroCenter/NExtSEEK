"""Simple and advanced sample search."""

import json

from nextseek_api.services.context_catalog import load_sample_types

from ..dbtable_sample import DBtable_sample
from ..dbtable_sampletype import DBtable_sampletype
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from ..seekdb import SeekDB
from ..responses import missing_param
from django.shortcuts import render
from ..decorators import login_redirect, requires_seek_login_redirect
from ..responses import json_response
from .samples import LOGIN_REQUIRED, _callerProjectIds

@requires_seek_login_redirect()
def sampleSearch(request):
    report = {}
    stype = DBtable_sampletype()
    report['type_options'] = stype.getSampleTypes()
    report['showSamplePage'] = True
    report['showSearch'] = True
    #return render(request,"sampleSearch.html", {'report':report})
    return HttpResponseRedirect('/seek/search/')

# The query-string parameters each search type reads without a default (seek/sample/search.py).
_SEARCH_PARAMS = {
    'FILTERING': ('sampletype_id', 'attribute', 'filter_rule', 'filter_valueFrom', 'filter_valueTo'),
    'Advanced': ('filter_searchText', 'sampletype_id', 'attribute', 'filter_matchType'),
    'UIDs': ('filter_searchUIDs',),
}


def runSampleSearch(request, searchType):
    '''
    Input:
        searchType = 'FILTERING', 'UIDs', or'Advanced'
    '''
    bad = missing_param(request.GET, *_SEARCH_PARAMS[searchType])
    if bad:
        return bad
    if not request.user.is_authenticated:
        return json_response(LOGIN_REQUIRED, 0)
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, True)

    # None means unrestricted, [] means match nothing. Scope travels as a LIST
    # applied as EXISTS, never as the legacy single project_id, which is left at 0
    # here so the two predicates cannot stack. EXISTS is also what keeps the count
    # honest: projects_samples is many-to-many, so scoping by that join could report
    # MORE rows than not scoping at all (measured 2026-08-20 on prod data,
    # project_id=3 gave 5134 against 5122 unscoped).
    # The caller's CURRENT projects (resolve_scope), not SEEK's option list, which keeps
    # projects they have left; a scope that cannot be resolved matches nothing (SEC-H05).
    project_ids = _callerProjectIds(request, "sample search")
    scoped_project_ids = None if project_ids is None else [str(pid) for pid in project_ids]

    filters = request.GET
    dbsample = DBtable_sample()
    sdata = dbsample.searchAdvanced(user_seek, filters, searchType, 0,
                                    scoped_project_ids=scoped_project_ids)

    return HttpResponse(sdata)

def sampleSearching(request):
    searchType = 'FILTERING'
    return runSampleSearch(request, searchType)

def _with_names(type_options):
    """The sample type options with each type's curated name (sample_types_context), "" when it has none, for the
    Associated with dropdown's name and code. load_sample_types never raises: without the catalog the names are
    empty and the page still renders."""
    options = json.loads(type_options)
    names = {entry.code: entry.name for entry in load_sample_types()}
    for option in options:
        option['name'] = names.get(option.get('title'), '')
    return json.dumps(options, default=str)


@requires_seek_login_redirect()
def searchAdvanced(request):
    report = {}
    stype = DBtable_sampletype()
    report['type_options'] = _with_names(stype.getSampleTypes())
    # type_options stays the JSON string the page's scripts read; the phone dropdown loops
    # over this list, since a {% for %} over the string runs once per character.
    report['type_option_list'] = json.loads(report['type_options'])
    report['showSamplePage'] = True
    report['showSearch'] = True        
    return render(request,"searchAdvanced.html", {'report':report})

def searchingAdvanced(request):
    searchType = "Advanced"
    return runSampleSearch(request, searchType)

def searchingUIDs(request):
    searchType = "UIDs"
    return runSampleSearch(request, searchType)

def smartSearch(request):
    if not request.user.is_authenticated:
        return login_redirect(request)
    return render(request, "smartSearch.html")
