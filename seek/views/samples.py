"""Sample pages, queries, downloads and attribute editing.

The legacy attribute editor changes what the graph holds: the Attribute nodes and their ``HAS_ATTRIBUTE`` edges, and
the stored metadata and value casts of every sample of the type. Its two views therefore enqueue an outbox row after
they write, and the graph sync loop applies it later (``nextseek_api/graph_sync/hooks.py``; the design's section 5,
elements E2 and E11). No request waits on Neo4j.
"""

from ..dbtable_attributetype import DBtable_attributetype
from ..dbtable_sample import DBtable_sample
from ..dbtable_sampleattribute import DBtable_sampleattribute
from ..dbtable_sampletype import DBtable_sampletype
from django.http import Http404
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.views.decorators.http import require_POST
from ..seekdb import SeekDB
from dmac.conversion import convertDicToOptions
import datetime
import json
from ..responses import json_response
from ..responses import plain_text
from django.shortcuts import render
from ..decorators import requires_seek_login
from ..decorators import requires_seek_login_redirect
from ..decorators import requires_supervisor
import simplejson
from ..decorators import verifySuperUser
import zipfile
from django.conf import settings
import logging
from nextseek_api.graph_search.scope import resolve_scope
from nextseek_api.graph_sync import hooks

from .exports import newExport

logger = logging.getLogger(__name__)

# The envelope message a view that requires a login answers with when there is none.
LOGIN_REQUIRED = 'Error: Please log in first.'

# What the sample page answers for a sample the caller may not see AND for one that does not exist: the same, so the
# page confirms no id to a caller outside its projects.
SAMPLE_NOT_FOUND = 'Sample not found'

# What an export answers when none of the requested samples is one the caller may see: the same for an unknown id and
# for one outside the caller's projects.
NO_SAMPLE_TO_EXPORT = 'Error: None of the selected samples was found'


def _callerProjectIds(request, surface):
    """The projects whose samples the caller may see: None for a superuser (every sample), else their project ids.

    Membership is ``graph_search.scope.resolve_scope`` (``is_superuser`` alone, never ``is_staff``). Fails closed: a
    caller whose scope cannot be resolved (anonymous, no SEEK person, a failed membership read) gets () and sees
    nothing, exactly as a member of no project does.
    """
    try:
        scope = resolve_scope(request.user)
    except Exception as exc:  # noqa: BLE001 (ScopeUnavailable or a membership read failure: fail closed)
        logger.warning("%s: the caller's project scope could not be resolved (%s); answering not found",
                       surface, type(exc).__name__)
        return ()
    if scope.is_admin:
        return None
    return tuple(scope.project_ids)


def _visibleSampleIds(sample_ids, project_ids):
    """The requested SEEK sample ids in one of ``project_ids`` (``projects_samples``, through
    ``nextseek_api.views._samples_visible_to_projects``), in request order. ``project_ids`` None (a superuser) keeps
    every id. An unknown id and an id outside the projects are both dropped, so the two answer alike."""
    if project_ids is None:
        return list(sample_ids)
    if not project_ids:
        return []
    from nextseek_api.views import _samples_visible_to_projects  # deferred: that module imports every ViewSet
    visible = _samples_visible_to_projects(sample_ids, project_ids)
    kept = []
    for sample_id in sample_ids:
        try:
            if str(int(sample_id)) in visible:
                kept.append(sample_id)
        except (TypeError, ValueError):
            continue
    return kept


def _sampleVisible(request, sample_id):
    """May the caller see this sample's page?

    A superuser sees every sample; anyone else only a sample in one of their projects. Membership is
    ``graph_search.scope.resolve_scope`` (``is_superuser`` alone, never ``is_staff``), and the sample's projects are
    ``projects_samples``, read by ``nextseek_api.views._samples_visible_to_projects``: the helpers graph_search and the
    sample tree endpoint use. Fails closed: a caller whose scope cannot be resolved, a member of no project, an unknown
    id and a sample outside the caller's projects all answer False.
    """
    return bool(_visibleSampleIds([sample_id], _callerProjectIds(request, "sample page")))


def _exportLogin(request):
    """The SEEK login of an export's caller, or None when there is no logged-in caller.

    ``request.user`` decides, not ``getSeekLogin``'s status alone: on a POST that status comes from ``username`` and
    ``password`` fields in the body, unchecked against SEEK, so it is True for any caller who sends both."""
    if not request.user.is_authenticated:
        return None
    user_seek = SeekDB(None, None, None).getSeekLogin(request, False)
    return user_seek if user_seek['status'] else None


def _sampleTypeIdsInRequest(request):
    """The sample type ids an attribute-editor request names: its ``sampletype_id`` parameter and the
    ``sample_type_id`` of each record it carries. Sorted, deduplicated, positive.

    The save path names the type outright; the delete path names it only in the records it deleted. A record that
    names no type still changed the Attribute catalog, which is why the caller enqueues ``catalog`` whatever this
    returns.
    """
    ret = request.GET
    values = [ret.get('sampletype_id')]
    try:
        records = json.loads(ret.get('records') or '[]')
    except ValueError:
        records = []

    if isinstance(records, list):
        values += [record.get('sample_type_id') for record in records if isinstance(record, dict)]

    sampletype_ids = set()
    for value in values:
        try:
            sampletype_id = int(value)
        except (TypeError, ValueError):
            continue

        if sampletype_id > 0:
            sampletype_ids.add(sampletype_id)

    return sorted(sampletype_ids)


def _enqueueAttributeGraphSync(request):
    """Ask the graph sync loop for the catalog and for the samples of every type this request named.

    Called at the end of the two editor views, after their own write: an attribute edit changes the Attribute nodes
    and their ``HAS_ATTRIBUTE`` edges (E11) and the stored metadata and value casts of every sample of the type
    (E2). ``hooks.enqueue`` never raises, so the SEEK rows stand whatever the outbox does, and the nightly targeted
    sync finds what a lost row would have carried.
    """
    hooks.enqueue('catalog', '*')
    for sampletype_id in _sampleTypeIdsInRequest(request):
        hooks.enqueue('samples_of_type', 'type:%d' % sampletype_id)

def seek(request, url):
    report = {}
    if request.method == 'POST':
        bodyhtml = "To be implemented"
        return render(request,"samples.html", {'bodyhtml' : bodyhtml})
    else:
        url = "/" + url.replace("-", "/") + "/"
        bodyhtml = getPageRequests(url)
        report = {}
        report['bodyhtml'] = bodyhtml
        return render(request,"samples.html", {'bodyhtml' : bodyhtml})

def sample(request, id):
    sample_id = id
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        if sample_id==0:
            url_redirect = '/login/?next=/seek/samples/query/'
        else:
            url_redirect = '/login/?next=/seek/sample/id=' + str(sample_id) + '/'
        return HttpResponseRedirect(url_redirect)

    # Project scope: the page prints every metadata value. A sample outside the caller's projects reads exactly as
    # one that does not exist, before anything about it is fetched.
    if not _sampleVisible(request, sample_id):
        raise Http404(SAMPLE_NOT_FOUND)
    
    seek_url = "/samples/" + str(id) + "/"
    bodyhtml = seekdb.getPageRequests(seek_url)
    
    report = {}
    report['bodyhtml'] = bodyhtml
    report['sample_id'] = sample_id
    
    dbsample = DBtable_sample()

    # db = settings.DATABASES['default']
    # conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])
    # cursor = conn.cursor()

    # cursor.execute(f"SELECT full FROM seek_sample_tree WHERE sample_id='{sample_id}'")

    # cursor_results = cursor.fetchone()
    # if cursor_results is not None:
        # report['treeData_multiparents'] = json.loads(cursor_results[0])[0]
    # else:
        # report['treeData_multiparents'] = dbsample.createSampleMultiParentTree(sample_id)
    # This treeData_multiparents does not have the complete tree information
    sampledic, samplelist = dbsample.getSampleInfo(sample_id)
    report['sampledic'] = sampledic
    report['sampleinfo'] = samplelist

    return render(request,"samples.html", {'bodyhtml' : bodyhtml, 'report':report})

def sampleTree(request, uid):
    sample_uid = uid
    dbsample = DBtable_sample()
    sample_id = dbsample.getSampleID(sample_uid)
    return sample(request, sample_id)

def sampleQuery(request):
    return sample_type(request, 0)

def sample_type(request, id):
    sampletype_id = int(id)
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        if sampletype_id==0:
            url_redirect = '/login/?next=/seek/samples/query/'
        else:
            url_redirect = '/login/?next=/seek/sample_types/id=' + str(sampletype_id) + '/'
        return HttpResponseRedirect(url_redirect)
    
    report = {}
    stype = DBtable_sampletype()
    report['type_options'] = stype.getComboboxOptions(sampletype_id, 'title')
    if sampletype_id==0:
        report['showSamplePage'] = True
        report['showSearch'] = True
        report['bodyhtml'] = '<div></div>'
    else:
        report['showSamplePage'] = True
        report['showSearch'] = True
        report['bodyhtml'] = stype.getSamplePage(sampletype_id, user_seek['server'], user_seek['username'], user_seek['password'])
        
    return render(request,"sampleQuery.html", {'bodyhtml' : report['bodyhtml'], 'report':report})

def getAttributes(request, id):
    try:
        sampletype_id = int(id)
    except:
        stype = DBtable_sampletype()
        sampletype_id = stype.getSampleTypeID(id)
    valueSelected = ''
    ret = request.GET
    if 'valueSelected' in ret:
        valueSelected = ret['valueSelected']
    
    sattr = DBtable_sampleattribute()
    data = sattr.getAttributes(sampletype_id, valueSelected)
    return HttpResponse(simplejson.dumps(data, default=str))

def getOperators(request):
    ret = request.GET
    sampletype_id = ret['sampletype_id']
    attribute = ret['attribute']
    
    sattr = DBtable_sampleattribute()
    data = sattr.getOperators(sampletype_id, attribute)
    return HttpResponse(simplejson.dumps(data, default=str))

def retrieveSamples(request):
    """The rows behind the sample-type grid; the view requires a login."""
    if not request.user.is_authenticated:
        return json_response(LOGIN_REQUIRED, 0)
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        return json_response(LOGIN_REQUIRED, 0)
    dbsample = DBtable_sample()
    reportData = dbsample.processRecords(request, user_seek, "retrieve")
    return HttpResponse(reportData) 

def sampleDownload(request):
    """Export the requested samples (``allids``, SEEK ids), with their lineage when ``includeSampleTree=1``.

    Requires a login. For anyone but a superuser only the caller's samples are exported: a requested id outside their
    projects is dropped exactly as an unknown one is, and the lineage keeps only their samples (``restrictToProjects``).
    """
    user_seek = _exportLogin(request)
    if user_seek is None:
        return json_response(LOGIN_REQUIRED, 0)

    if request.method == "POST":
        ret = request.POST
    else:
        ret = request.GET
        
    includeSampleTree = int(ret['includeSampleTree'])
    if 'attributeFilter' in ret:
        attributeFilter = ret['attributeFilter']
    else:
        attributeFilter = None
    
    allids = ret['allids']
    sampletype_id = ret['sampletype_id']  # noqa: F841 (kept: deleting it would relax a required request field)
    sample_ids = json.loads(allids)

    project_ids = _callerProjectIds(request, "sample download")
    if project_ids is not None:
        sample_ids = _visibleSampleIds(sample_ids, project_ids)
        if not sample_ids:
            return json_response(NO_SAMPLE_TO_EXPORT, 0)
    
    datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = 'download-samples-' + datenow + '.xlsx'
    downloadfile, link = newExport(request, filename)
    
    dbsample = DBtable_sample()
    dbsample.restrictToProjects(project_ids)
    if 'attributeFilter' in ret and includeSampleTree:
        #linkfile = link.replace('.xls', '.zip')
        #dzipfile = downloadfile.replace('.xls', '.zip')
        #sdata = dbsample.downloadSamples_noTree(user_seek, dzipfile, linkfile, sample_ids, includeSampleTree, attributeFilter)
        
        sdata = dbsample.downloadSamples_noTree(user_seek, downloadfile, link, sample_ids, includeSampleTree, attributeFilter)
        return HttpResponse(sdata) 
    
    sampleTypes = dbsample.parseSampleIDs(sample_ids)
    if len(sampleTypes)==1:
        sdata = dbsample.downloadSamples_new(user_seek, downloadfile, link, sample_ids, includeSampleTree, attributeFilter)
    else:
        linkfile = link.replace('.xls', '.zip')
        dzipfile = downloadfile.replace('.xls', '.zip')
        zf = zipfile.ZipFile(dzipfile, mode='w')
        for sampleType in sampleTypes:
            suffix = '-' + sampleType + '.xls'
            downfilei = downloadfile.replace('.xls', suffix)
            filenamei = filename.replace('.xls', suffix)
            ids = sampleTypes[sampleType]
            sdata = dbsample.downloadSamples_new(user_seek, downfilei, linkfile, ids, includeSampleTree, attributeFilter)
            zf.write(downfilei, filenamei)
    
    return HttpResponse(sdata)    

def sampleExport(request):
    """The ImmPort export of the requested samples (``allids``, SEEK ids) and their parents.

    Requires a login. For anyone but a superuser only the caller's samples are exported, as ``sampleDownload`` does.
    """
    user_seek = _exportLogin(request)
    if user_seek is None:
        return json_response(LOGIN_REQUIRED, 0)

    if request.method == "POST":
        ret = request.POST
    else:
        ret = request.GET
    
    allids = ret['allids']
    sampletype_id = ret['sampletype_id']
    sample_ids = json.loads(allids)

    project_ids = _callerProjectIds(request, "sample export")
    if project_ids is not None:
        sample_ids = _visibleSampleIds(sample_ids, project_ids)
        if not sample_ids:
            return json_response(NO_SAMPLE_TO_EXPORT, 0)

    datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = 'samples-export' + datenow + '.xlsx'
    downloadfile, link = newExport(request, filename)
    
    dbsample = DBtable_sample()
    dbsample.restrictToProjects(project_ids)
    sdata = dbsample.exportSamples(user_seek, downloadfile, link, sample_ids, sampletype_id)
    return HttpResponse(sdata)   

def sampleFindAjax(request):
    """The ImmPort export of the samples a published workbook names by UID.

    Requires a login. For anyone but a superuser the export keeps only the caller's samples (``restrictToProjects``):
    its rows are lineage paths, so a UID outside their projects leaves no row, as an unknown one does.
    """
    user_seek = _exportLogin(request)
    if user_seek is None:
        return json_response(LOGIN_REQUIRED, 0)
    msg = "Error: File not valid"
    message = ''
    status = 0
    data = {'msg':msg, 'status': status, 'link':''}
    if request.method == "POST":
        if request.FILES and request.FILES.get('excelfile_find'):
            excelfile = request.FILES['excelfile_find']
            if excelfile:
                datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
                filename = 'samples-export' + datenow + '.zip'
                downloadfile, link = newExport(request, filename)
                
                project_ids = _callerProjectIds(request, "sample lookup")
                if project_ids is not None and not project_ids:
                    return json_response(NO_SAMPLE_TO_EXPORT, 0)
                dbsample = DBtable_sample()
                dbsample.restrictToProjects(project_ids)
                sdata = dbsample.findSamplesForExport(user_seek, downloadfile, link, excelfile)
                return HttpResponse(sdata)
            else:
                message = 'Error: Not a valid file from client side'
                data = {'msg':message, 'status': 0, 'link':''}
        else:
            message = 'Error: Not a valid file from client side'
            data = {'msg':message, 'status': 0, 'link':''}
    else:
        message = 'Error: Not a valid http POST request'
        data = {'msg':message, 'status': 0, 'link':''}
                
    data['message'] = plain_text(message)
                
    return HttpResponse(simplejson.dumps(data, default=str))   

@require_POST
def sampleDelete(request):
    """Delete samples given by id (``allids``) or by UID (``alluids``).

    POST only, so the CSRF middleware checks the token. The view requires a login,
    and the SEEK identity the deletion runs under is the logged-in account's own. A
    sample is deleted for its contributor or for a superuser
    (``DBtable_sample._deleteSampleList``).
    """
    ret = request.POST
    if not request.user.is_authenticated:
        return json_response(LOGIN_REQUIRED, 0)

    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request)
    if not user_seek['status'] or user_seek.get('username') != request.user.username:
        return json_response(LOGIN_REQUIRED, 0)

    datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = 'samples-deletion' + datenow + '.xls'
    downloadfile, link = newExport(request, filename)
    
    dbsample = DBtable_sample()

    if 'allids' in ret: 
        sample_ids = json.loads(ret['allids'])
    elif 'alluids' in ret:
        sample_uids = json.loads(ret['alluids'])
        sample_ids = list(map(dbsample.getSampleID, sample_uids))

    sdata = dbsample.deleteSamples(user_seek, downloadfile, link, sample_ids,
                                   is_superuser=verifySuperUser(request) == 1)
    return HttpResponse(sdata)

def getStudiesOptions(request, id):
    seekdb = SeekDB(None, None, None)
    seekdb.getSeekLogin(request, False)
    
    investigation_id = id
    studies = seekdb.getStudiesFromID(investigation_id)
    study_options = convertDicToOptions(studies)
    data = {'msg':'okay', 'status': 1, 'study_options':study_options}
    return HttpResponse(simplejson.dumps(data, default=str))

def getAssaysOptions(request, id):
    seekdb = SeekDB(None, None, None)
    seekdb.getSeekLogin(request, False)
    
    study_id = id
    assays = seekdb.getAssaysFromID(study_id)
    assay_options = convertDicToOptions(assays)
    data = {'msg':'okay', 'status': 1, 'assay_options':assay_options}
    return HttpResponse(simplejson.dumps(data, default=str))

@requires_seek_login_redirect('/seek/samples/attributes/')
def sampleAttributes(request):
    report = {}
    stype = DBtable_sampletype()
    report['type_options'] = stype.getSampleTypes()
    report['showSamplePage'] = True
    report['showSearch'] = True
    attritype = DBtable_attributetype()
    report['attribute_types_options'] = attritype.getAttributeTypeOptions()         
    return render(request,"sampleAttributes.html", {'report':report})

def getSampleType(request):
    """The samples of one sample type; the view requires a login."""
    if not request.user.is_authenticated:
        return json_response(LOGIN_REQUIRED, 0)
    ret = request.GET
    sampletype_id = ret['sampletype_id']
    attribute = ret['attribute']
    
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        return json_response(LOGIN_REQUIRED, 0)
    
    dbsample = DBtable_sample()
    sdata = dbsample.getSampleType(user_seek, sampletype_id, attribute)
    return HttpResponse(sdata)

@requires_seek_login
@requires_supervisor('The login user does not have the permission to add the sample attribute.')
def sampleAttributeSave(request):
    user_seek = request.user_seek

    ret = request.GET
    sampletype_id = ret['sampletype_id']
    records = ret['records']
    diclist = json.loads(records)
    
    sampleattr = DBtable_sampleattribute()
    attri_renamed = sampleattr.getAttributesRenamed(sampletype_id, diclist)
    reportData = sampleattr.processRecords(request, user_seek, "save")
    data = json.loads(reportData)
    if data['status']==1:
        dbsample = DBtable_sample()
        reportData = dbsample.updateSampleType(user_seek, sampletype_id, attri_renamed)

    _enqueueAttributeGraphSync(request)

    return HttpResponse(reportData)

@requires_seek_login(log_failure=True)
@requires_supervisor('The login user does not have the permission to delete the sample attribute.')
def sampleAttributeDelete(request):
    user_seek = request.user_seek

    sampleattr = DBtable_sampleattribute()
    reportData = sampleattr.processRecords(request, user_seek, "delete")

    # ``processRecords`` deletes only the records the request carried; with none, nothing was written.
    if 'records' in request.GET:
        _enqueueAttributeGraphSync(request)

    return HttpResponse(reportData)

def getInstituionUsers(request, id):
    seekdb = SeekDB(None, None, None)
    seekdb.getSeekLogin(request, False)
    
    instituion_id = int(id)
        
    options = []
    status = 0
    msg = 'No user not available'
    isSupervisor = verifySuperUser(request)
    if isSupervisor==0: 
        options.append({'id':-1, 'title':'Default','selected':True})
    else:
        objects = seekdb.getInfoObject("/institutions/", instituion_id)
        try:
            people = objects["relationships"]["people"]["data"]
            for dici in people:
                id = dici['id']
                title = seekdb.getUserFullname(id)
                options += [{'id':id, 'title':title}]
            status = 1
            msg = 'okay'
        except:
            options.append({'id':0, 'title':'','selected':True})
            status = 0
            msg = 'No user is found for the lab'
    
    data = {'msg':msg, 'status': status, 'userOptions':options}
    return HttpResponse(simplejson.dumps(data, default=str))   

def editSample(request, id):
    return HttpResponseRedirect(f"{settings.SEEK_PUBLIC_URL}/samples/{id}/edit")

def manageSample(request, id):
    return HttpResponseRedirect(f"{settings.SEEK_PUBLIC_URL}/samples/{id}/manage")
