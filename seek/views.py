from django.shortcuts import render
from django.http import HttpResponseRedirect, HttpResponse, FileResponse

import json
import os
import re
import requests

import logging
logger = logging.getLogger(__name__)

import pandas as pd

from django.conf import settings
from itertools import groupby

import simplejson
import datetime
import zipfile
import MySQLdb

from dmac.conversion import convertDicToOptions, handle_uploaded_file
from dmac.dbtable_clades import DBtable_clades
from dmac.dbtable_sampletypesclades import DBtable_sample_types_clades as DBtable_stc
from dmac.dbtable_internalassays import DBtable_internalassays
from dmac.dbtable_assaysinternalassays import DBtable_assaysinternalassays
from nextseek_api.services.sample_workbook import write_samples_workbook

from .decorators import (requires_seek_login, requires_seek_login_redirect,
                         requires_supervisor, verifySuperUser)
from .responses import json_response, plain_text
from .seekdb import SeekDB
from .models import Projects

from .dbtable_sampletype import DBtable_sampletype
from .dbtable_sample import DBtable_sample
from .dbtable_documents import DBtable_documents
from .dbtable_sampleattribute import DBtable_sampleattribute
from .dbtable_attributetype import DBtable_attributetype
from .dbtable_projects import DBtable_projects

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status


from .timeline.services.timeline_service import run_All, get_event_data
from .timeline.services.nhp_service import save_nhp_info_to_json, get_timeline_data, save_nhp_data
from neo4j import GraphDatabase
import io
SEEK_DATABASE = settings.SEEK_DATABASE
NEXTSEEK_DATABASE = settings.NEXTSEEK_DATABASE
DOWNLOAD_DIRECTORY  = settings.MEDIA_ROOT + "/download/"
DOWNLOAD_DIRECTORY_LINK = settings.MEDIA_URL + 'download/'  
UPLOAD_DIRECTORY = settings.MEDIA_ROOT + "/uploads/"
SEEK_DATAFILE_ROOT = settings.SEEK_DATAFILE_ROOT
DROPBOX_DIRECTORY = settings.MEDIA_ROOT + "/dropbox/"

SAMPLE_TEMPLATE_FILE = settings.MEDIA_ROOT + "/reserved/SAMPLE_TEMPLATE.xlsx"
SAMPLE_TEMPLATES_FOLDER = settings.SAMPLE_TEMPLATES_FOLDER
SAMPLE_TEMPLATES_FOLDER_PROJECT = settings.SAMPLE_TEMPLATES_FOLDER_PROJECT

PUBLISH_STATS_FILE = settings.PUBLISH_STATS_FILE

SEEK_HOSTNAME = settings.SEEK_HOSTNAME

report = {}
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

@requires_seek_login_redirect('/seek/samples/batchupload/', whetherFullInfo=True)
def batchUpload(request):
    seekdb = request.seekdb
    user_seek = request.user_seek

    isSupervisor = verifySuperUser(request)

    lab_options = seekdb.getObjectsToOptions("/institutions")

    all_lab_users = {}
    for lab in lab_options:
        lab_id = int(lab['id'])
        if lab_id != 0:
            lab_info = seekdb.getInfoObject("/institutions/", lab_id)
            if isSupervisor:
                people = lab_info["relationships"]["people"]["data"]
                all_people = []
                for person in people:
                    all_people.append({'id': person['id'], 'title': seekdb.getUserFullname(person['id'])})
                all_lab_users[lab_id] = all_people
            else:
                all_lab_users = {}
                all_lab_users[lab_id] = [{'id': user_seek['person_id'], 'title': seekdb.getUserFullname(user_seek['person_id'])}]

    report['lab_options'] = json.dumps(lab_options, default=str)
    report['all_lab_users'] = json.dumps(all_lab_users, default=str)
    
    return render(request,"batchUpload.html", {'report': report})
    
def sampleUploadAjax(request):
    logger.debug('sampleUploadAjax')
    username = str(request.user)  # noqa: F841 (kept: resolves the lazy request.user)
    seekdb = SeekDB(None, None, None)
    seekdb.getSeekLogin(request)
    msg = "Error: File not valid"
    message = ''
    status = 0
    data = {'msg':msg, 'status': status, 'link':''}
    if request.method == "POST":
        if request.FILES and request.FILES.get('excelfile_upload'):
            excelfile = request.FILES['excelfile_upload']
            if excelfile:
                inputfile = excelfile.name
                #logger.debug(inputfile)
                instituion_id = request.POST.get('instituion_id')
                creator_id = request.POST.get('people_id')
                if verifySuperUser(request)==1:
                    #logger.debug(creator_id)
                    try:
                        creator_id = int(creator_id)
                    except:
                        msg = 'Error: You login as admin and must choose the creator.'
                        status = 0
                        logger.error(msg)
                        return json_response(msg, status, message='')
                        
                    if int(creator_id)>0:
                        status, msg = seekdb.updateCreator(instituion_id, creator_id)
                        logger.debug(msg)
                        if not status:
                            logger.error(msg)
                            return json_response(msg, status, message='')
                    else:
                        msg = 'Error: You login as admin and must choose the creator.'
                        status = 0
                        logger.error(msg)
                        return json_response(msg, status, message='')
                
                names = inputfile.split(".")
                n = len(names)
                
                datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
                filename = '.'.join(names[:(n-1)]) + '_feedback-' + datenow + '.xls'
                feedbackfile = DOWNLOAD_DIRECTORY + filename
                link = DOWNLOAD_DIRECTORY_LINK + filename
                logger.debug(feedbackfile)
                
                backupfile = '.'.join(names[:(n-1)]) + '_v' + datenow + '.' + names[-1]
                backupfile = UPLOAD_DIRECTORY + backupfile
                logger.debug(backupfile)
                handle_uploaded_file(excelfile, backupfile)
                
                sample = DBtable_sample()
                msgi, status = sample.batchUpload(excelfile, feedbackfile, seekdb)
                if status:
                    msg = 'Batch sample uploading successful. To find the UIDs for samples uploaded, refer to the feedback excel file: ' + filename
                    message = msg + '\n\n' + msgi
                else:
                    message = msgi
                    terms = msgi.split("<")
                    msg = terms[0] + "<br/><br/>"
                    msg += "Refer to the log and the excel file: " + filename + '.<br/>'
                data = {'msg':msg, 'status': status, 'link':link}
                #logger.debug(message)
            else:
                message = 'Error: Not a valid file from client side'
                data = {'msg':message, 'status': 0, 'link':''}
                logger.error(message)
        else:
            message = 'Error: Not a valid file from client side'
            data = {'msg':message, 'status': 0, 'link':''}
            logger.error(message)
    else:
        message = 'Error: Not a valid http POST request'
        data = {'msg':message, 'status': 0, 'link':''}
        logger.error(message)
                
    data['message'] = plain_text(message)
                
    return HttpResponse(simplejson.dumps(data, default=str))       

        
    
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

#@csrf_exempt
def remote(request):
    return samples(request)

@requires_seek_login_redirect('/seek/data/upload/', whetherFullInfo=True)
def datafileUpload(request):
    seekdb = request.seekdb
    user_seek = request.user_seek

    isSupervisor = verifySuperUser(request)

    lab_options = seekdb.getObjectsToOptions("/institutions")

    all_lab_users = {}
    for lab in lab_options:
        lab_id = int(lab['id'])
        if lab_id != 0:
            lab_info = seekdb.getInfoObject("/institutions/", lab_id)
            if isSupervisor:
                people = lab_info["relationships"]["people"]["data"]
                all_people = []
                for person in people:
                    all_people.append({'id': person['id'], 'title': seekdb.getUserFullname(person['id'])})
                all_lab_users[lab_id] = all_people
            else:
                all_lab_users = {}
                all_lab_users[lab_id] = [{'id': user_seek['person_id'], 'title': seekdb.getUserFullname(user_seek['person_id'])}]

    report['lab_options'] = json.dumps(lab_options, default=str)
    report['all_lab_users'] = json.dumps(all_lab_users, default=str)
    report['seek_url'] = settings.SEEK_PUBLIC_URL

    return render(request,"dataFileUpload.html", {'report':report})

#@api_view(http_method_names=['GET'])
#@authentication_classes((TokenAuthentication,))
#@permission_classes((IsAuthenticated,))
def retrieveSamples(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    dbsample = DBtable_sample()
    reportData = dbsample.processRecords(request, user_seek, "retrieve")
    return HttpResponse(reportData) 

def sampleDownload(request):
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
    
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    
    datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = 'download-samples-' + datenow + '.xlsx'
    downloadfile = DOWNLOAD_DIRECTORY + filename
    link = DOWNLOAD_DIRECTORY_LINK + filename
    
    dbsample = DBtable_sample()
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
    if request.method == "POST":
        ret = request.POST
    else:
        ret = request.GET
    
    allids = ret['allids']
    sampletype_id = ret['sampletype_id']
    sample_ids = json.loads(allids)
    
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = 'samples-export' + datenow + '.xlsx'
    downloadfile = DOWNLOAD_DIRECTORY + filename
    link = DOWNLOAD_DIRECTORY_LINK + filename
    
    dbsample = DBtable_sample()
    sdata = dbsample.exportSamples(user_seek, downloadfile, link, sample_ids, sampletype_id)
    return HttpResponse(sdata)   

def sampleFindAjax(request):
    username = str(request.user)  # noqa: F841 (kept: resolves the lazy request.user)
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
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
                downloadfile = DOWNLOAD_DIRECTORY + filename
                link = DOWNLOAD_DIRECTORY_LINK + filename
                
                dbsample = DBtable_sample()
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

    
def sampleDelete(request):
    ret = request.GET
        
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request)
    
    datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = 'samples-deletion' + datenow + '.xls'
    downloadfile = DOWNLOAD_DIRECTORY + filename
    link = DOWNLOAD_DIRECTORY_LINK + filename
    
    dbsample = DBtable_sample()

    if 'allids' in ret: 
        sample_ids = json.loads(ret['allids'])
    elif 'alluids' in ret:
        sample_uids = json.loads(ret['alluids'])
        sample_ids = list(map(dbsample.getSampleID, sample_uids))

    sdata = dbsample.deleteSamples(user_seek, downloadfile, link, sample_ids)
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
    ret = request.GET
    sampletype_id = ret['sampletype_id']
    attribute = ret['attribute']
    
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    
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
    
    return HttpResponse(reportData)
    
@requires_seek_login(log_failure=True)
@requires_supervisor('The login user does not have the permission to delete the sample attribute.')
def sampleAttributeDelete(request):
    user_seek = request.user_seek

    sampleattr = DBtable_sampleattribute()
    reportData = sampleattr.processRecords(request, user_seek, "delete")
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
    
def samplesValidate(request):
    logger.debug('samplesValidate')
    username = str(request.user)  # noqa: F841 (kept: resolves the lazy request.user)
    seekdb = SeekDB(None, None, None)
    seekdb.getSeekLogin(request)

    msg = "Error: File not valid"
    message = ''
    status = 0
    data = {'msg':msg, 'status': status, 'link':''}

    if request.method == "POST":
        if request.FILES and request.FILES.get('excelfile_upload'):
            excelfile = request.FILES['excelfile_upload']
            if excelfile:
                # validate

                db = settings.DATABASES[SEEK_DATABASE]
                conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])

                df = pd.read_sql(f'''
                    SELECT
                        sa.id AS attribute_id,
                        sa.title AS attribute_title,
                        sa.sample_type_id, st.title AS sample_type_title
                    FROM
                        {db["NAME"]}.sample_attributes sa
                    JOIN
                        {db["NAME"]}.sample_types st ON sa.sample_type_id = st.id
                ''', con=conn)

                df['Instructions'] = df.apply(lambda row: f"{row['sample_type_title']}::{row['attribute_title']}", axis=1)

                # Load the Excel workbook
                workbook = pd.ExcelFile(excelfile)

                logger.debug('Validating Structure of the Assay Sheet:')

                # Validate the number of sheets
                expected_sheets = ['Instructions', 'Samples', 'Ontology', 'Assay']
                actual_sheets = workbook.sheet_names

                if set(expected_sheets) != set(actual_sheets):
                    missing_sheets = set(expected_sheets) - set(actual_sheets)
                    extra_sheets = set(actual_sheets) - set(expected_sheets)  # noqa: F841 (LATENT_BUGS #40)
                    if set(['Instructions', 'Samples', 'Assay']) & missing_sheets:
                        message += f"Missing sheets: {missing_sheets}. Please fix this and reupload sheet."
                        status += 1
                        data = {'msg': message, 'status': status, 'link': ''}
                        data['message'] = plain_text(message)
                        return HttpResponse(simplejson.dumps(data, default=str))       

                    message += "Extra sheets: {extra_sheets}"
                    status += 1
                else:
                    message += "\n\nSheets match what is expected ✅"

                logger.debug('Validating Structure of the Instructions Page:')
                
                # Validate the structure of the Instructions sheet
                instructions_sheet = pd.read_excel(workbook, 'Instructions')
                expected_columns = ['Field', 'Database Field', 'Field Type', 'Ontology']
                actual_columns = instructions_sheet.columns
                
                if set(expected_columns) != set(actual_columns):
                    missing_columns = set(expected_columns) - set(actual_columns)
                    extra_columns = set(actual_columns) - set(expected_columns)
                    message += f"\n\nError in Instructions sheet: Missing columns: {list(missing_columns)}, Extra columns: {list(extra_columns)}"
                    status += 1
                else:
                    message += "\n\nInstructions sheet has correct structure ✅"

                logger.debug('Validating Instructions(Database Field) values to the Database:')

                # Validate that all entries in 'Database Field' exist in the 'Instructions' column of the modified CSV
                # Assuming the modified CSV is already loaded into a DataFrame called 'df'

                df_instructions = df['Instructions'].tolist()
                try:
                    database_field_column = instructions_sheet['Database Field'].tolist()
                except:
                    message = 'Error: No database field column in the Instructions sheet' 
                    data = {'msg':message, 'status': 0, 'link':''}
                    logger.error(message)
                    return HttpResponse(simplejson.dumps(data, default=str))       

                statusChanged = False
                for entry in database_field_column:
                    if entry not in df_instructions:
                        message += f"\n\nError: {entry} in 'Database Field' column does not exist in Database for that Sample Type"
                        if not statusChanged:
                            status += 1
                            statusChanged = True

                if not statusChanged:
                    message += "\n\nAll Database Fields in Instructions sheet match values in database ✅"

                logger.debug('Validating Headers(Samples) to Instructions(Field):')

                # Validate the structure of the Samples sheet
                if 'Samples' not in workbook.sheet_names:
                    message += "\n\nError: 'Samples' sheet does not exist"
                    status += 1

                samples_sheet = pd.read_excel(workbook, 'Samples')
                samples_headers = samples_sheet.columns.tolist()
                field_column = instructions_sheet['Field'].tolist()
                samples_headers.append('Field')

                # Check for mismatches between Samples headers and Instructions 'Field' column
                samples_set = set(samples_headers)
                field_set = set(field_column)
                mismatches = {
                    'missingSamples': samples_set - field_set,
                    'missingInstructions': field_set - samples_set
                }

                if mismatches['missingSamples']:
                    message += "\n\nHeaders in 'Samples' sheet not found in 'Field' column of 'Instructions' sheet: ❌"
                    status += 1
                    for header in mismatches['missingSamples']:
                        message += "\n- " + header
                else:
                    message += "\n\nAll headers in Samples sheet found in Instructions sheet ✅" 

                if mismatches['missingInstructions']:
                    message += f"\n\nValues in 'Field' column of 'Instructions' sheet not found in headers of 'Samples' sheet: ❌"
                    status += 1
                    for value in mismatches['missingInstructions']:
                        message += "\n- " + value
                else:
                    message += "\n\nAll headers in Instructions sheet found in Samples sheet ✅"

                logger.debug('Validating Assay Page Setup:')
                assay_sheet = pd.read_excel(workbook, 'Assay')
                expected_columns = ['SampleType', 'AssayType', 'Assay', 'Direction']
                actual_columns = assay_sheet.columns

                if set(expected_columns) != set(actual_columns):
                    missing_columns = set(expected_columns) - set(actual_columns)
                    extra_columns = set(actual_columns) - set(expected_columns)
                    message += f"\n\nError in Assay Sheet: Missing columns: {list(missing_columns)}, Extra columns: {list(extra_columns)}"
                else:
                    message += "\n\nAssay Sheet columns have correct structure ✅"

                data = {'msg': message, 'status': status, 'link':''}
            else:
                message = 'Error: Not a valid file from client side'
                data = {'msg':message, 'status': 0, 'link':''}
                logger.error(message)
        else:
            message = 'Error: Not a valid file from client side'
            data = {'msg':message, 'status': 0, 'link':''}
            logger.error(message)
    else:
        message = 'Error: Not a valid http POST request'
        data = {'msg':message, 'status': 0, 'link':''}
        logger.error(message)
                
    data['message'] = plain_text(message)
                
    return HttpResponse(simplejson.dumps(data, default=str))       

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

@requires_seek_login_redirect('/seek/templates')
def templatesList(request):
    user_seek = request.user_seek

    headers = {'Accept': 'application/json'}
    r = requests.get(user_seek['server'] + '/projects', auth=(user_seek['username'], user_seek['password']), headers=headers)
    projects = [p['id'] for p in r.json()['data']]
    if not SAMPLE_TEMPLATES_FOLDER_PROJECT in projects:
        msg = 'You are not in the correct project to access this page'
        status = 0
        return json_response(msg, status)

    folders = getTemplateFolders(SAMPLE_TEMPLATES_FOLDER)

    return render(request, 'templatesList.html', {'folders': folders})

@api_view(['GET'])
def nhp_info(request, nhp_name):
    try:
        nhp_info = save_nhp_info_to_json(nhp_name)
        if nhp_info:
            return Response(nhp_info, status=status.HTTP_200_OK)
        else:
            return Response({"detail": "NHP Info not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
def fetch_event_data(request, nhp_name: str, event_type: str, date: str):
    if not nhp_name:
        raise HTTPException(status_code=404, detail="NHP data not found")
    try:
        event_data =get_event_data(nhp_name, event_type, date)
        if event_data:
            return Response(event_data, status=status.HTTP_200_OK)
        else:
            return Response({"detail": "Event data not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
@api_view(['GET'])
def get_nhp_data(request, nhp_name: str):
    try:
        timeline_data = run_All(nhp_name)
        if timeline_data:
            return Response(timeline_data, status=status.HTTP_200_OK)
        else:
            return Response({"detail": "Event Data not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
def download_nhp_data(request, nhp_name: str):
    try:
        timeline_data = get_timeline_data(nhp_name)
        if not timeline_data:
            return Response({"detail": "NHP data not found"}, status=status.HTTP_404_NOT_FOUND)
        
        # Convert to Excel
        excel_data = save_nhp_data(timeline_data)
        
        # Create a streaming response
        response = FileResponse(
            io.BytesIO(excel_data),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            filename=f"{nhp_name}_data.xlsx"
        )
        return response
    except Exception as e:
        return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

def get_children_uids(sample_uids, user_project_ids, admin):
    db = settings.DATABASES[SEEK_DATABASE]
    NEO4J_DATABASE = settings.NEO4J_DATABASE
    with GraphDatabase.driver(NEO4J_DATABASE['URI'], auth=NEO4J_DATABASE['AUTH']) as driver:
        r,s,k = driver.execute_query("""
		UNWIND $sample_uids AS sample_uid
        MATCH (s:Sample {uuid: sample_uid})
        MATCH parents=(s)-[:DERIVED_FROM*0..]->(parent)
        MATCH children=(s)<-[:DERIVED_FROM*0..]-(child)
        RETURN collect(DISTINCT s.uuid) + collect(DISTINCT parent.uuid) + collect(DISTINCT child.uuid) AS uuids
        """,
        sample_uids=sample_uids,
        database_=NEO4J_DATABASE['NAME'])
        uids = r[0]['uuids']

    db = settings.DATABASES[SEEK_DATABASE]
    conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])
    cursor = conn.cursor()

    uids_str = ', '.join(f"'{uid}'" for uid in uids)
    project_ids_str = ', '.join(f"'{pid}'" for pid in user_project_ids)

    if admin:
        query = f"""
        SELECT id,sample_type_id,uuid,json_metadata
        FROM {db["NAME"]}.samples
        WHERE uuid IN ({uids_str})
        """
    else:
        query = f"""
        SELECT s.id, s.sample_type_id, s.uuid, s.json_metadata
        FROM {db["NAME"]}.samples s
        JOIN {db["NAME"]}.projects_samples ps
        ON s.id = ps.sample_id
        WHERE s.uuid IN ({uids_str}) AND ps.sample_id = s.id AND ps.project_id IN ({project_ids_str})
        """

    cursor.execute(query)
    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchall()
    samples_retrieved_df = pd.DataFrame(rows, columns=columns)

    cursor.close()
    conn.close()
    return samples_retrieved_df

def parse_json_metadata(metadata_series):
    return metadata_series.apply(lambda x: json.loads(x) if isinstance(x, str) else {})

def parse_children_uids(children_uids):
    children_uids['json_metadata'] = parse_json_metadata(children_uids['json_metadata'])

    metadata_df = pd.json_normalize(children_uids['json_metadata'])
    metadata_df = metadata_df.loc[:, ~metadata_df.columns.duplicated()]

    final_df = pd.concat([children_uids[['uuid']], metadata_df], axis=1)
    final_df.replace("", pd.NA, inplace=True)
    final_df.dropna(axis=1, how='all', inplace=True)

    return final_df

def sample_retrieval_data(children_uids, output):
    # Sheet layout, README included, is owned by
    # nextseek_api.services.sample_workbook so it cannot drift per call path.
    write_samples_workbook(parse_children_uids(children_uids), output)

def adminRetrieveSamples(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    user_projects = seekdb.getCurrentUser()['data']['relationships']['projects']['data']
    user_project_ids = map(lambda x: x['id'], user_projects)

    if verifySuperUser(request) == 1:
        admin = True
    else:
        admin = False

    if not user_seek['status']:
        err = user_seek['err']
        msg = err
        status = 0
        docurl = ''
        return json_response(msg, status, docurl)
    else:
        if request.method == "POST":
            logger.debug(f"REQUEST: {request.POST.keys()}")
            uids = request.POST.get('retrieval_uids').strip().split()
            children_uids = get_children_uids(uids, user_project_ids, admin)

            datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
            filename = 'download-samples-' + datenow + '.xlsx'
            downloadfile = DOWNLOAD_DIRECTORY + filename

            sample_retrieval_data(children_uids, downloadfile)

            with open(downloadfile, 'rb') as fh:
                response = HttpResponse(fh.read(), content_type="application/vnd.ms-excel")
                response['Content-Disposition'] = 'inline; filename=' + os.path.basename(downloadfile)
                return response
        else:
            return render(request, "admin_retrieval.html")

@requires_seek_login_redirect('/seek/projects/')
def projects(request):
    seekdb = request.seekdb

    projectsdb = DBtable_projects()
    current_user = seekdb.getCurrentUser() or {}
    user_projects = (
        current_user.get('data', {})
        .get('relationships', {})
        .get('projects', {})
        .get('data', [])
    )
    user_project_ids = [project.get('id') for project in user_projects if project.get('id') is not None]

    if verifySuperUser(request) == 1:
        projects = list(Projects.objects.all().values('id', 'title', 'avatar_id'))
    else:
        projects = list(Projects.objects.filter(id__in=user_project_ids).values('id', 'title', 'avatar_id'))

    for project in projects:
        try:
            stats = projectsdb.sample_count(project['id'])
            stats.update(projectsdb.files_count(project['id']))
        except Exception:
            logger.exception("Failed to build project stats for project_id=%s", project.get('id'))
            stats = {'sample_count': 0, 'sop_count': 0, 'df_count': 0}
        project['stats'] = stats

    try:
        stcdb = DBtable_stc()
        clade_rows = stcdb.getAllCounts() or []
        clade_rows = sorted(clade_rows, key=lambda row: (row.get('title') or '', row.get('st_group') or ''))
        clade_data = {k: list(v) for k, v in groupby(clade_rows, lambda x: x.get('title') or 'Uncategorized')}
    except Exception:
        logger.exception("Failed to build clade data for projects page")
        clade_data = {}

    for k, group in clade_data.items():
        total = sum((item.get('count') or 0) for item in group)
        for item in group:
            item['total'] = total
           
    return render(request, 'projectsList.html', {'projects': projects,
                                                 'clade_data': clade_data,
                                                 'seek_hostname': SEEK_HOSTNAME})

def project_page(request, project_id):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)

    if not user_seek['status']:
        url_redirect = f"/login/?next=/seek/projects/{project_id}"
        return HttpResponseRedirect(url_redirect) 
    else:
        if verifySuperUser(request) == 1:
            admin = True
        else:
            admin = False

        user_projects = seekdb.getCurrentUser()['data']['relationships']['projects']['data']
        user_project_ids = list(map(lambda x: int(x['id']), user_projects))
        user_in_project = int(project_id) in user_project_ids

        if admin is False and user_in_project is False:
                data = {'msg': 'You are not in this project', 'status': 0, 'link': ''}
                return render(request, 'error.html', {'data': data})

        project = Projects.objects.get(id=project_id)

        cladedb = DBtable_clades()
        
        clade_data = cladedb.getCladeProjectStats(project_id)
        values = set(map(lambda x: x['title'], clade_data))
        clade_data = [[y for y in clade_data if y['title']==x] for x in values]

        try:
            published_stats = pd.read_excel(PUBLISH_STATS_FILE, sheet_name=None)
            published_stats = published_stats[project.title]
            published_stats = published_stats.fillna(0)
            published_stats['Published'] = published_stats['Published'].astype(int)
            type_published_counts = dict(zip(published_stats['Data Types'],
                                             published_stats['Published']))
            for clade_list in clade_data:
                for clade in clade_list:
                    data_type = clade['st_group']
                    published_count = type_published_counts.get(data_type, 0)
                    clade['published'] = published_count
                    
        except Exception:
            for clade_list in clade_data:
                for clade in clade_list:
                    clade['published'] = 0

        for group in clade_data:
            for item in group:
                item['total'] = sum(i['count'] for i in group)
                item['published_total'] = sum(i['published'] for i in group)

        clade_data.sort(key=lambda x: x[0]['order'])

        return render(request, 'projectPage.html', {'id': project.id,
                                                    'title': re.sub("-|_", " ", project.title),
                                                    'description': project.description.replace("\r", "\n"),
                                                    'seek_hostname': SEEK_HOSTNAME,
                                                    'avatar_id': project.avatar_id,
                                                    'clade_data': clade_data,})

@requires_seek_login_redirect('/seek/samples/attributes/')
@requires_supervisor('Error: You login as admin to view this page.', with_message_key=True)
def adminClades(request):
    cladedb = DBtable_clades()
    stcdb = DBtable_stc()
    stc = simplejson.dumps(stcdb.getAllWithTitles(), default=str)
    
    return render(request,"clades.html", {'clades': list(cladedb.getAll()), 'stc': stc})

@requires_seek_login
@requires_supervisor('The login user does not have the permission to perform this action.')
def cladesSyncSampleTypes(request):
    stcdb = DBtable_stc()
    stcdb.syncSampleTypes()
    
    return HttpResponse({})

@requires_seek_login
@requires_supervisor('The login user does not have the permission to add the clade.')
def cladeSave(request):
    cladedb = DBtable_clades()
    
    ret = request.GET
    clades = json.loads(ret['records'])

    for clade in clades:
        if 'id' not in clade:
            # New clade. Should create it in DB
            cladedb.new(title=clade['title'],
                        color=clade['color'],
                        order=clade['order'])
        else:
            # Existing clade. Should update
            cladedb.update(clade_id=clade['id'],
                           title=clade['title'],
                           color=clade['order'],
                           order=clade['order'])
        
    return HttpResponse({}, headers={"Refresh": 1})
    
@requires_seek_login(log_failure=True)
@requires_supervisor('The login user does not have the permission to delete the sample attribute.')
def cladeDelete(request):
    cladedb = DBtable_clades()

    ret = request.GET
    clades = json.loads(ret['records'])

    for clade in clades:
        cladedb.delete(clade['id'])
    
    return HttpResponse({}, headers={"Refresh": 1})

@requires_seek_login
@requires_supervisor('The login user does not have the permission to add the clade.')
def cladeSampleTypesSave(request):
    stcdb = DBtable_stc()
    
    ret = request.GET
    data = json.loads(ret['records'])

    for record in data:
        sample_type_id = record['sample_type_id']
        clade_id = record['clade_title']
        
        stcdb.update(sample_type_id, clade_id)
        
    return HttpResponse({}, headers={"Refresh": 1})

def editSample(request, id):
    return HttpResponseRedirect(f"https://{SEEK_HOSTNAME}/samples/{id}/edit")

def manageSample(request, id):
    return HttpResponseRedirect(f"https://{SEEK_HOSTNAME}/samples/{id}/manage")

def smartSearch(request):
    if not request.user.is_authenticated:
        data = {'msg': 'You do not have access to this page', 'status': 0, 'link': ''}
        return render(request, 'error.html', {'data': data})
    return render(request, "smartSearch.html")

@requires_seek_login_redirect('/seek/samples/attributes/')
@requires_supervisor('Error: You login as admin to view this page.', with_message_key=True)
def internalAssays(request):
    db_ia = DBtable_internalassays()
    db_aia = DBtable_assaysinternalassays()
    internal_assays = simplejson.dumps(db_ia.getAll(), default=list)
    assay_associations = simplejson.dumps(db_aia.getAllWithTitles(), default=list)

    return render(request,"internal_assays.html", {"internal_assays": internal_assays, "assay_associations": assay_associations})

@requires_seek_login
@requires_supervisor('The login user does not have the permission to add the internal assay.')
def internalAssaySave(request):
    ia = DBtable_internalassays()
    
    ret = request.GET
    internal_assays = json.loads(ret['records'])

    for internal_assay in internal_assays:
        if 'id' not in internal_assay:
            ia.new(internal_assay_title=internal_assay['internal_assay_title'],)
        else:
            id = internal_assay["id"]
            internal_assay_title = internal_assay["internal_assay_title"]
            ia.update(internal_assay_id=id,
                      internal_assay_title=internal_assay_title)
        
    return HttpResponse({}, headers={"Refresh": 1})

@requires_seek_login(log_failure=True)
@requires_supervisor('You do not have the permission to delete the internal assay.')
def internalAssayDelete(request):
    ia = DBtable_internalassays()

    ret = request.GET
    internal_assays = json.loads(ret['records'])

    for internal_assay in internal_assays:
        ia.delete(internal_assay['id'])
    
    return HttpResponse({}, headers={"Refresh": 1})

@requires_seek_login
@requires_supervisor('You do not have the permission to add the assay association.')
def assayAssociationSave(request):
    aia = DBtable_assaysinternalassays()
    
    ret = request.GET
    data = json.loads(ret['records'])

    for record in data:
        assay_id = record['assay_id']
        internal_assay_id = record['internal_assay_id']
        
        aia.update(assay_id, internal_assay_id)
        
    return HttpResponse({}, headers={"Refresh": 1})

@requires_seek_login
@requires_supervisor('The login user does not have the permission to perform this action.')
def syncInternalAssays(request):
    aia = DBtable_assaysinternalassays()
    aia.syncAssays()
    
    return HttpResponse({})

@requires_seek_login_redirect()
def sopQuery(request):
    report["seek_url"] = settings.SEEK_PUBLIC_URL

    return render(request, "sopsPage.html", {"report" : report})

@requires_seek_login_redirect()
def datafileQuery(request):
    report["seek_url"] = settings.SEEK_PUBLIC_URL

    return render(request, "dataFilesPage.html", {"report" : report})

@requires_seek_login_redirect()
def newSearch(request):
    return render(request, "newSearch.html")


def getting_started(request):
    """Tutorials / Getting Started landing page. Static content."""
    return render(request, "help/getting_started.html")


