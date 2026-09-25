"""Simple and advanced sample search."""

import json

from nextseek_api.services.context_catalog import load_sample_types

from ..dbtable_sample import DBtable_sample
from ..dbtable_sampletype import DBtable_sampletype
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from ..seekdb import SeekDB
from django.shortcuts import render
from ..decorators import requires_seek_login_redirect
from ..decorators import verifySuperUser

@requires_seek_login_redirect('/seek/samples/search/')
def sampleSearch(request):
    report = {}
    stype = DBtable_sampletype()
    report['type_options'] = stype.getSampleTypes()
    report['showSamplePage'] = True
    report['showSearch'] = True
    #return render(request,"sampleSearch.html", {'report':report})
    return HttpResponseRedirect('/seek/search/')

def _callerProjectIds(user_seek):
    '''Every project id the caller belongs to, as strings, order preserved.

    user_seek['projectid'] is only the FIRST project SEEK lists, because
    SeekDB.__getFeatureInfo returns featureData[defaultIndex] with defaultIndex=0
    (seek/seekdb.py:56). Scoping the UI search by it meant a caller in two projects
    saw only one of them, and if that first project happened to be empty they saw
    nothing at all -- observed on production 2026-08-21, where SEEK returned
    ['13', '2'] for charlie-test-3 and project 13 holds zero samples.

    The full list was already being fetched into 'projectOptions' (seek/seekdb.py:64-77);
    it just was not used here.

    Returns [] when the caller has no resolvable project. That is the fail-closed
    signal searchAdvanced expects, and it deliberately replaces the previous
    fail-OPEN behaviour: projectid was 0 for such a caller (seek/seekdb.py:106) and
    the builder applies the legacy project predicate only `if int(project_id) > 0`,
    so the search ran completely unscoped and that caller read every project.
    '''
    ids = []
    for option in (user_seek.get('projectOptions') or []):
        pid = option.get('id') if isinstance(option, dict) else option
        if pid is None:
            continue
        pid = str(pid).strip()
        if pid and pid not in ids:
            ids.append(pid)
    if ids:
        return ids

    # No options resolved. Fall back to the single project when we have one: a
    # transient SEEK hiccup should not black out a caller we can still scope.
    single = user_seek.get('projectid')
    if single is not None:
        single = str(single).strip()
        if single and single != '0':
            return [single]
    return []


def runSampleSearch(request, searchType):
    '''
    Input:
        searchType = 'FILTERING', 'UIDs', or'Advanced'
    '''
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, True)
    isSupervisor = verifySuperUser(request)

    # None means unrestricted, [] means match nothing. Scope travels as a LIST
    # applied as EXISTS, never as the legacy single project_id, which is left at 0
    # here so the two predicates cannot stack. EXISTS is also what keeps the count
    # honest: projects_samples is many-to-many, so scoping by that join could report
    # MORE rows than not scoping at all (measured 2026-08-20 on prod data,
    # project_id=3 gave 5134 against 5122 unscoped).
    scoped_project_ids = None if isSupervisor != 0 else _callerProjectIds(user_seek)

    filters = request.GET
    dbsample = DBtable_sample()
    sdata = dbsample.searchAdvanced(user_seek, filters, searchType, 0,
                                    scoped_project_ids=scoped_project_ids)

    return HttpResponse(sdata)

def sampleSearching(request):
    searchType = 'FILTERING'
    return runSampleSearch(request, searchType)

def remote(request):
    return samples(request)

def _with_names(type_options):
    """The sample type options with each type's curated name (sample_types_context), "" when it has none, for the
    Associated with dropdown's name and code. load_sample_types never raises: without the catalog the names are
    empty and the page still renders."""
    options = json.loads(type_options)
    names = {entry.code: entry.name for entry in load_sample_types()}
    for option in options:
        option['name'] = names.get(option.get('title'), '')
    return json.dumps(options, default=str)


@requires_seek_login_redirect('/seek/search/')
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
        data = {'msg': 'You do not have access to this page', 'status': 0, 'link': ''}
        return render(request, 'error.html', {'data': data})
    return render(request, "smartSearch.html")

@requires_seek_login_redirect()
def newSearch(request):
    return render(request, "newSearch.html")
