from django.shortcuts import render
from django.shortcuts import redirect
from django.http import HttpResponseRedirect, HttpResponse, FileResponse, Http404

import csv
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
import tempfile
import random
import requests

import logging
logger = logging.getLogger(__name__)

from subprocess import call
from subprocess import check_call
from time import strftime, gmtime
import pandas as pd
import numpy as np

from django.conf import settings
from django.core.files.storage import default_storage
from django.shortcuts import render, HttpResponseRedirect, redirect, HttpResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from pathlib import Path
from itertools import groupby
from datetime import datetime, timedelta
from pytz import timezone

from django.contrib.auth.models import User
from django import forms

import simplejson
import datetime
import json
import zipfile
import MySQLdb

from dmac.conversion import dateconversion, dateToString, dateToStringUK, convertDicToOptions, handle_uploaded_file
from dmac.datagrid_custom import DataGrid
from dmac.csv_excel import load_file, load_excelfile
from dmac.iocsv import saveCsvfile
from dmac.dbtable_clades import DBtable_clades
from dmac.dbtable_sampletypesclades import DBtable_sample_types_clades as DBtable_stc
from dmac.dbtable_internalassays import DBtable_internalassays
from dmac.dbtable_assaysinternalassays import DBtable_assaysinternalassays
from nextseek_api.services.sample_workbook import (
    write_samples_workbook,
    write_template_workbook,
)
from nextseek_api.services.template_catalog import (
    GROUPS,
    load_catalog,
    load_relationships,
)

from .seekdb import SeekDB
from .nextcloudapi import NextCloudAPI
from .galaxyapi import GalaxyAPI
from .seekapi import SeekAPI
from .models import Projects
from .models import Clades

from .dbtable_sampletype import DBtable_sampletype
from .dbtable_sample import DBtable_sample
from .dbtable_documents import DBtable_documents
from .dbtable_data_files import DBtable_data_files
from .dbtable_sops import DBtable_sops
from .dbtable_sampleattribute import DBtable_sampleattribute
from .dbtable_attributetype import DBtable_attributetype
from .dbtable_projects import DBtable_projects

from rest_framework.decorators import authentication_classes
from rest_framework.authentication import BasicAuthentication, TokenAuthentication,SessionAuthentication
from rest_framework.decorators import api_view
from rest_framework.decorators import permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from subprocess import call

import shlex
from subprocess import Popen, PIPE

from seek.timeline.services.timeline_service import run_All, get_event_data
from seek.timeline.services.nhp_service import save_nhp_info_to_json, get_timeline_data, save_nhp_data
import neo4j
from neo4j import GraphDatabase
import io
SEEK_DATABASE = settings.SEEK_DATABASE
NEXTSEEK_DATABASE = settings.NEXTSEEK_DATABASE
DOWNLOAD_DIRECTORY  = settings.MEDIA_ROOT + "/download/"
DOWNLOAD_DIRECTORY_LINK = settings.MEDIA_URL + 'download/'  
UPLOAD_DIRECTORY = settings.MEDIA_ROOT + "/uploads/"
SEEK_DATAFILE_ROOT = settings.SEEK_DATAFILE_ROOT

SAMPLE_TEMPLATE_FILE = settings.MEDIA_ROOT + "/reserved/SAMPLE_TEMPLATE.xlsx"

PUBLISH_STATS_FILE = settings.PUBLISH_STATS_FILE

PUBLISH_SERVER = settings.PUBLISH_URL
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
    
def getSeekPage(request, seek_url):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
        url_redirect = '/login/'
        return HttpResponseRedirect(url_redirect)
        
    bodyhtml = seekdb.getPageRequests(seek_url)
    report = {}
    report['bodyhtml'] = bodyhtml
    return render(request,"samples.html", {'bodyhtml' : bodyhtml})
    
def sample(request, id):
    sample_id = id
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
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

def document(request, id):
    document_id = id
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str)) 
    
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
    data = {'msg':msg, 'status': status, 'link':docurl}
    return HttpResponse(simplejson.dumps(data, default=str))   

def sampleUpload(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
        return HttpResponseRedirect("/login/?next=/seek/samples/upload/")
        
    report = {}
    docs = DBtable_documents()
    report['template_options'] = docs.getOptionsDocuments(0, "Sample Sheet Template")
    
    isSupervisor = verifySuperUser(request)
    if isSupervisor==0: 
        options = []
        options.append({'id':-1, 'title':'Default','selected':True})
    else:
        options = seekdb.getObjectsToOptions("/institutions")
    report['lab_options'] = json.dumps(options, default=str)
    
    return render(request,"sampleUpload.html", {'report':report})

def batchUpload(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, True)
    if not user_seek['status']:
        err = user_seek['err']
        return HttpResponseRedirect("/login/?next=/seek/samples/batchupload/")

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
    username = str(request.user)
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request)
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
                        data = {'msg':msg, 'status': status, 'link':'', 'message':''}
                        return HttpResponse(simplejson.dumps(data, default=str))
                        
                    if int(creator_id)>0:
                        status, msg = seekdb.updateCreator(instituion_id, creator_id)
                        logger.debug(msg)
                        if not status:
                            logger.error(msg)
                            data = {'msg':msg, 'status': status, 'link':'', 'message':''}
                            return HttpResponse(simplejson.dumps(data, default=str))
                    else:
                        msg = 'Error: You login as admin and must choose the creator.'
                        status = 0
                        logger.error(msg)
                        data = {'msg':msg, 'status': status, 'link':'', 'message':''}
                        return HttpResponse(simplejson.dumps(data, default=str))
                
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
                
    if message is not None and '<br/>' in message:
        data['message'] = message.replace('<br/>', '\n')
    else:
        data['message'] = message
                
    return HttpResponse(simplejson.dumps(data, default=str))       

        
    
def sampleQuery(request):
    return sample_type(request, 0)
        
def sample_type(request, id):
    sampletype_id = int(id)
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
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


def sampleSearch(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
        url_redirect = '/login/?next=/seek/samples/search/'
        return HttpResponseRedirect(url_redirect)
    
    report = {}
    stype = DBtable_sampletype()
    sampletype_id = 0
    report['type_options'] = stype.getSampleTypes()
    report['showSamplePage'] = True
    report['showSearch'] = True
    #return render(request,"sampleSearch.html", {'report':report})
    return HttpResponseRedirect('/seek/search/')

def __searchFilterKeywords(keywords):
    kkk = keywords.strip()
    if len(kkk)==0:
        uids = []
        return uids
    
    keywords = keywords.replace(" ",",")    
    uids = keywords.split(",")
    return uids


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

#@csrf_exempt
def remote(request):
    return samples(request)

def datafileUpload(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, True)
    if not user_seek['status']:
        err = user_seek['err']
        return HttpResponseRedirect("/login/?next=/seek/data/upload/")

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

def __updateLabUser(seekdb, instituion_id, people_id):
    return seekdb
  
#@api_view(http_method_names=['GET'])
#@authentication_classes((TokenAuthentication,))
#@permission_classes((IsAuthenticated,))
def verifyToken(request):
    from rest_framework.authentication import TokenAuthentication
    user_auth_tuple = TokenAuthentication().authenticate(request)
    tokenValidated = False
    if user_auth_tuple is None:
        tokenValidated = False
    else:
        (user, token) = user_auth_tuple 
        tokenValidated = True
    
    return tokenValidated
   
def callCmdline(cmd):
    args = shlex.split(cmd)
    proc = Popen(args, stdout=PIPE, stderr=PIPE)
    out, err = proc.communicate()
    exitcode = proc.returncode
    return exitcode, out, err    
    
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
    sampletype_id = ret['sampletype_id']
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
    username = str(request.user)
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
                inputfile = excelfile.name
                names = inputfile.split(".")
                n = len(names)
                
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
                
    if message is not None and '<br/>' in message:
        data['message'] = message.replace('<br/>', '\n')
    else:
        data['message'] = message
                
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
    
def publishSamples(user_seek, sample_ids, assay_id=None, project_id=None):
    datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = 'samples-publish' + datenow + '.xlsx'
    downloadfile = DOWNLOAD_DIRECTORY + filename
    link = DOWNLOAD_DIRECTORY_LINK + filename
    
    templatefile = SAMPLE_TEMPLATE_FILE
    cmd = 'cp ' + templatefile + ' ' + downloadfile
    os.system(cmd)
    
    dbsample = DBtable_sample()
    sdata = dbsample.publishSamples(user_seek, downloadfile, link, sample_ids, assay_id, project_id)
    return HttpResponse(sdata)
    
def __definePublishServer(seekdb, user_seek):
    username = user_seek['username']
    password = user_seek['password']
    person_id = user_seek['person_id']
    fullname = seekdb.getUserFullname(person_id)
    server = PUBLISH_SERVER
    sdb = SeekDB(server, username, password)
    user = sdb.updateUserProfile(fullname)
    return sdb, user
    
    
def __getISA(seekdb, user_seek, whichServer):
    if whichServer=="SOURCE":
        sdb = seekdb
        user = user_seek
        project_title = user['projectname']
        server = 'local'
    elif whichServer=="DESTINATION":
        sdb, user = __definePublishServer(seekdb, user_seek)
        server = PUBLISH_SERVER
        if 'projectname' in user:
            project_title = user['projectname']
        else:
            project_title = None
    else:
        return None, None, None, None, None
    
    project_options, investigation_options_dic, study_options_dic, assay_options_dic = sdb.getISAOptions()
    return project_options, investigation_options_dic, study_options_dic, assay_options_dic, server
    
def getStudiesOptions(request, id):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    
    investigation_id = id
    studies = seekdb.getStudiesFromID(investigation_id)
    study_options = convertDicToOptions(studies)
    data = {'msg':'okay', 'status': 1, 'study_options':study_options}
    return HttpResponse(simplejson.dumps(data, default=str))
    
def getAssaysOptions(request, id):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    
    study_id = id
    assays = seekdb.getAssaysFromID(study_id)
    assay_options = convertDicToOptions(assays)
    data = {'msg':'okay', 'status': 1, 'assay_options':assay_options}
    return HttpResponse(simplejson.dumps(data, default=str))
    
def sampleAttributes(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
        url_redirect = '/login/?next=/seek/samples/attributes/'
        return HttpResponseRedirect(url_redirect)
    
    report = {}
    stype = DBtable_sampletype()
    sampletype_id = 0
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
    
def verifySuperUser(request):
    user = request.user
    if user.is_authenticated:
        try:
            if user.is_superuser:
                return 1
            return 0
        except User.DoesNotExist:
            return 0
    return 0    
    
def sampleAttributeSave(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))
        
    isSupervisor = verifySuperUser(request)
    if isSupervisor==0: 
        err = 'The login user does not have the permission to add the sample attribute.'
        logger.error(err)
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))
    
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
    
def sampleAttributeDelete(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
        logger.error(err)
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))
        
    isSupervisor = verifySuperUser(request)
    if isSupervisor==0: 
        err = 'The login user does not have the permission to delete the sample attribute.'
        logger.error(err)
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))
    
    sampleattr = DBtable_sampleattribute()
    reportData = sampleattr.processRecords(request, user_seek, "delete")
    return HttpResponse(reportData)
    
    
def getInstituionUsers(request, id):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    
    instituion_id = int(id)
    valueSelected = ''
    ret = request.GET
    if 'valueSelected' in ret:
        valueSelected = ret['valueSelected']
        
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
        
def searchAdvanced(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
        url_redirect = '/login/?next=/seek/search/'
        return HttpResponseRedirect(url_redirect)
    
    report = {}
    stype = DBtable_sampletype()
    sampletype_id = 0
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
    username = str(request.user)
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request)

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
                    extra_sheets = set(actual_sheets) - set(expected_sheets)
                    if set(['Instructions', 'Samples', 'Assay']) & missing_sheets:
                        message += f"Missing sheets: {missing_sheets}. Please fix this and reupload sheet."
                        status += 1
                        data = {'msg': message, 'status': status, 'link': ''}
                        if message is not None and '<br/>' in message:
                            data['message'] = message.replace('<br/>', '\n')
                        else:
                            data['message'] = message
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
                
    if message is not None and '<br/>' in message:
        data['message'] = message.replace('<br/>', '\n')
    else:
        data['message'] = message
                
    return HttpResponse(simplejson.dumps(data, default=str))       

def _templates_context(selected=None, message=""):
    """Everything the picker template needs.

    Grouping and relationships come from template_catalog, so the view stays
    HTTP-only and the same data can be reused by the download path when it has
    to re-render.
    """
    entries = load_catalog()
    by_code = {e.code: e for e in entries}
    relationships = load_relationships(list(by_code), set(by_code))

    chosen = [c for c in (selected or []) if c in by_code]
    groups = [
        {"key": key, "label": label,
         "entries": [e for e in entries if e.group == key]}
        for key, label in GROUPS
    ]
    return {
        "groups": [g for g in groups if g["entries"]],
        "selected": chosen,
        "message": message,
        # The strip is re-derived in the browser as boxes are ticked, so picking
        # a type costs no round trip. Same one-hop children-only rule as
        # template_catalog.suggest, which stays the server-side source of truth.
        "children_json": json.dumps({
            code: rel.get("children", []) for code, rel in relationships.items()
        }),
        "meta_json": json.dumps({
            e.code: {"name": e.name, "group": e.group} for e in entries
        }),
    }


def templatesList(request):
    """The Download Templates picker.

    Login is required; project membership is not. Templates are schema
    definitions, not sample data, so there is nothing project-scoped to expose.
    """
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        return HttpResponseRedirect('/login/?next=/seek/templates')

    return render(request, 'templatesList.html', _templates_context())


def templatesDownload(request):
    """Generate and stream one workbook for the selected sample types.

    The bytes are returned directly rather than written under outputs/ and
    linked: a template is cheap to regenerate, so a persistent link buys little
    and would leave a file per download behind with nothing to clean it up.
    """
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        return HttpResponseRedirect('/login/?next=/seek/templates')

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

def extract_ids(data):
    ids = []
    if isinstance(data, dict):
        if 'id' in data:
            ids.append(data['id'])
        for key, value in data.items():
            ids.extend(extract_ids(value))
    elif isinstance(data, list):
        for item in data:
            ids.extend(extract_ids(item))
    return ids

def get_clade_color(sample_type):
    db = settings.DATABASES[SEEK_DATABASE]
    nextseekdb = settings.DATABASES[NEXTSEEK_DATABASE]
    conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])
    cursor = conn.cursor()
    query = f"""
    SELECT c.color FROM {nextseekdb["NAME"]}.clades c
    JOIN {nextseekdb["NAME"]}.sample_types_clades stc ON stc.clade_id = c.id
    JOIN {db["NAME"]}.sample_types st ON stc.sample_type_id = st.id
    WHERE st.title = '{sample_type}'
    """

    cursor.execute(query)
    try:
        color = cursor.fetchone()[0]
    except Exception:
        color = "#000000"

    cursor.close()

    return color

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

    # Bound, never inlined: a quote in a uuid or a project id broke out of the
    # literal. Second copy of #78 -- the twin in dbtable_sample.py:906 was fixed
    # by feaa816, this one was outside that scope. Shape mirrors the view-layer
    # prior art at nextseek_api/views.py:829-852 (7698848). The uuids are not
    # request data but r[0]['uuids'] read back out of Neo4j above, so the taint
    # is second-order; user_project_ids comes from SEEK's getCurrentUser().
    # Only the schema name, from settings, is still interpolated.
    uid_placeholders = ', '.join(['%s'] * len(uids))

    if admin:
        query = f"""
        SELECT id,sample_type_id,uuid,json_metadata
        FROM {db["NAME"]}.samples
        WHERE uuid IN ({uid_placeholders})
        """
        params = list(uids)
    else:
        # user_project_ids is a single-pass map() (views.py:1247), so it is
        # consumed here and only on the branch that needs it. The sentinel keeps
        # the statement valid, and matching nothing, when the caller has no
        # mapped projects; it used to emit `IN ()`, a MySQL syntax error.
        scoped_project_ids = [str(pid) for pid in user_project_ids] or ['']
        project_placeholders = ', '.join(['%s'] * len(scoped_project_ids))
        query = f"""
        SELECT s.id, s.sample_type_id, s.uuid, s.json_metadata
        FROM {db["NAME"]}.samples s
        JOIN {db["NAME"]}.projects_samples ps
        ON s.id = ps.sample_id
        WHERE s.uuid IN ({uid_placeholders}) AND ps.sample_id = s.id AND ps.project_id IN ({project_placeholders})
        """
        params = list(uids) + scoped_project_ids

    cursor.execute(query, params)
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
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str)) 
    else:
        if request.method == "POST":
            logger.debug(f"REQUEST: {request.POST.keys()}")
            uids = request.POST.get('retrieval_uids').strip().split()
            children_uids = get_children_uids(uids, user_project_ids, admin)

            datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
            filename = 'download-samples-' + datenow + '.xlsx'
            downloadfile = DOWNLOAD_DIRECTORY + filename
            link = DOWNLOAD_DIRECTORY_LINK + filename

            sample_retrieval_data(children_uids, downloadfile)

            with open(downloadfile, 'rb') as fh:
                response = HttpResponse(fh.read(), content_type="application/vnd.ms-excel")
                response['Content-Disposition'] = 'inline; filename=' + os.path.basename(downloadfile)
                return response
        else:
            return render(request, "admin_retrieval.html")

def projects(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)

    if not user_seek['status']:
        url_redirect = "/login/?next=/seek/projects/"
        return HttpResponseRedirect(url_redirect) 
    else:
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
            except Exception as exc:
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
                                                     'seek_public_url': settings.SEEK_PUBLIC_URL})

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
                                                    'seek_public_url': settings.SEEK_PUBLIC_URL,
                                                    'avatar_id': project.avatar_id,
                                                    'clade_data': clade_data,})

def adminClades(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)

    if not user_seek['status']:
        err = user_seek['err']
        url_redirect = '/login/?next=/seek/samples/attributes/'
        return HttpResponseRedirect(url_redirect)

    if verifySuperUser(request) != 1:
        msg = 'Error: You login as admin to view this page.'
        status = 0
        logger.error(msg)
        data = {'msg':msg, 'status': status, 'link':'', 'message':''}
        return HttpResponse(simplejson.dumps(data, default=str))

    cladedb = DBtable_clades()
    stcdb = DBtable_stc()
    stc = simplejson.dumps(stcdb.getAllWithTitles(), default=str)
    
    return render(request,"clades.html", {'clades': list(cladedb.getAll()), 'stc': stc})

def cladesSyncSampleTypes(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    
    if not user_seek['status']:
        err = user_seek['err']
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))
        
    isSupervisor = verifySuperUser(request)
    if isSupervisor==0: 
        err = 'The login user does not have the permission to perform this action.'
        logger.error(err)
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))

    stcdb = DBtable_stc()
    stcdb.syncSampleTypes()
    
    return HttpResponse({})

def cladeSave(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))
        
    isSupervisor = verifySuperUser(request)
    if isSupervisor==0: 
        err = 'The login user does not have the permission to add the clade.'
        logger.error(err)
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))

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
    
def cladeDelete(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
        logger.error(err)
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))
        
    isSupervisor = verifySuperUser(request)
    if isSupervisor==0: 
        err = 'The login user does not have the permission to delete the sample attribute.'
        logger.error(err)
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))

    cladedb = DBtable_clades()

    ret = request.GET
    clades = json.loads(ret['records'])

    for clade in clades:
        cladedb.delete(clade['id'])
    
    return HttpResponse({}, headers={"Refresh": 1})

def cladeSampleTypesSave(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))
        
    isSupervisor = verifySuperUser(request)
    if isSupervisor==0: 
        err = 'The login user does not have the permission to add the clade.'
        logger.error(err)
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))

    stcdb = DBtable_stc()
    
    ret = request.GET
    data = json.loads(ret['records'])

    for record in data:
        sample_type_id = record['sample_type_id']
        clade_id = record['clade_title']
        
        stcdb.update(sample_type_id, clade_id)
        
    return HttpResponse({}, headers={"Refresh": 1})

def editSample(request, id):
    return HttpResponseRedirect(f"{settings.SEEK_PUBLIC_URL}/samples/{id}/edit")

def manageSample(request, id):
    return HttpResponseRedirect(f"{settings.SEEK_PUBLIC_URL}/samples/{id}/manage")

def smartSearch(request):
    if not request.user.is_authenticated:
        data = {'msg': 'You do not have access to this page', 'status': 0, 'link': ''}
        return render(request, 'error.html', {'data': data})
    return render(request, "smartSearch.html")

def internalAssays(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)

    if not user_seek['status']:
        err = user_seek['err']
        url_redirect = '/login/?next=/seek/samples/attributes/'
        return HttpResponseRedirect(url_redirect)

    if verifySuperUser(request) != 1:
        msg = 'Error: You login as admin to view this page.'
        status = 0
        logger.error(msg)
        data = {'msg':msg, 'status': status, 'link':'', 'message':''}
        return HttpResponse(simplejson.dumps(data, default=str))

    db_ia = DBtable_internalassays()
    db_aia = DBtable_assaysinternalassays()
    internal_assays = simplejson.dumps(db_ia.getAll(), default=list)
    assay_associations = simplejson.dumps(db_aia.getAllWithTitles(), default=list)

    return render(request,"internal_assays.html", {"internal_assays": internal_assays, "assay_associations": assay_associations})

def internalAssaySave(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))
        
    isSupervisor = verifySuperUser(request)
    if isSupervisor==0: 
        err = 'The login user does not have the permission to add the internal assay.'
        logger.error(err)
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))

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

def internalAssayDelete(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
        logger.error(err)
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))
        
    isSupervisor = verifySuperUser(request)
    if isSupervisor==0: 
        err = 'You do not have the permission to delete the internal assay.'
        logger.error(err)
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))

    ia = DBtable_internalassays()

    ret = request.GET
    internal_assays = json.loads(ret['records'])

    for internal_assay in internal_assays:
        ia.delete(internal_assay['id'])
    
    return HttpResponse({}, headers={"Refresh": 1})

def assayAssociationSave(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))
        
    isSupervisor = verifySuperUser(request)
    if isSupervisor==0: 
        err = 'You do not have the permission to add the assay association.'
        logger.error(err)
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))

    aia = DBtable_assaysinternalassays()
    
    ret = request.GET
    data = json.loads(ret['records'])

    for record in data:
        assay_id = record['assay_id']
        internal_assay_id = record['internal_assay_id']
        
        aia.update(assay_id, internal_assay_id)
        
    return HttpResponse({}, headers={"Refresh": 1})

def syncInternalAssays(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    
    if not user_seek['status']:
        err = user_seek['err']
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))
        
    isSupervisor = verifySuperUser(request)
    if isSupervisor==0: 
        err = 'The login user does not have the permission to perform this action.'
        logger.error(err)
        msg = err
        status = 0
        docurl = ''
        data = {'msg':msg, 'status': status, 'link':docurl}
        return HttpResponse(simplejson.dumps(data, default=str))

    aia = DBtable_assaysinternalassays()
    aia.syncAssays()
    
    return HttpResponse({})

def sopQuery(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        url_redirect = '/login/'
        return HttpResponseRedirect(url_redirect)
    
    report["seek_url"] = settings.SEEK_PUBLIC_URL

    return render(request, "sopsPage.html", {"report" : report})

def datafileQuery(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        url_redirect = '/login/'
        return HttpResponseRedirect(url_redirect)
    
    report["seek_url"] = settings.SEEK_PUBLIC_URL

    return render(request, "dataFilesPage.html", {"report" : report})

def newSearch(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        url_redirect = '/login/'
        return HttpResponseRedirect(url_redirect)

    return render(request, "newSearch.html")


def getting_started(request):
    """Tutorials / Getting Started landing page. Static content."""
    return render(request, "help/getting_started.html")


