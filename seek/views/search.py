"""Simple and advanced sample search."""

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

def runSampleSearch(request, searchType):
    '''
    Input:
        searchType = 'FILTERING', 'UIDs', or'Advanced'
    '''
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, True)
    isSupervisor = verifySuperUser(request)
    if isSupervisor==0:
        project_id = user_seek['projectid']
    else:
        project_id = 0
    
    filters = request.GET
    dbsample = DBtable_sample()
    sdata = dbsample.searchAdvanced(user_seek, filters, searchType, project_id)
    
    return HttpResponse(sdata)

def sampleSearching(request):
    searchType = 'FILTERING'
    return runSampleSearch(request, searchType)

def remote(request):
    return samples(request)

@requires_seek_login_redirect('/seek/search/')
def searchAdvanced(request):
    report = {}
    stype = DBtable_sampletype()
    report['type_options'] = stype.getSampleTypes()
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
