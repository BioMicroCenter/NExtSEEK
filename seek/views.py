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
from django.conf import settings

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
from rest_framework.authentication import TokenAuthentication,SessionAuthentication
from rest_framework.decorators import api_view
from rest_framework.decorators import permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from subprocess import call

import shlex
from subprocess import Popen, PIPE

from django.conf import settings
from seek.timeline.services.timeline_service import run_All, get_event_data
from seek.timeline.services.nhp_service import save_nhp_info_to_json, get_timeline_data, save_nhp_data
import neo4j
from neo4j import GraphDatabase
import io
SEEK_DATABASE = settings.SEEK_DATABASE
DOWNLOAD_DIRECTORY  = settings.MEDIA_ROOT + "/download/"
DOWNLOAD_DIRECTORY_LINK = settings.MEDIA_URL + 'download/'  
UPLOAD_DIRECTORY = settings.MEDIA_ROOT + "/uploads/"
SEEK_DATAFILE_ROOT = settings.SEEK_DATAFILE_ROOT
DROPBOX_DIRECTORY = settings.MEDIA_ROOT + "/dropbox/"

SAMPLE_TEMPLATE_FILE = settings.MEDIA_ROOT + "/reserved/SAMPLE_TEMPLATE.xlsx"
SAMPLE_TEMPLATES_FOLDER = settings.SAMPLE_TEMPLATES_FOLDER
SAMPLE_TEMPLATES_FOLDER_PROJECT = settings.SAMPLE_TEMPLATES_FOLDER_PROJECT

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

    db = settings.DATABASES['default']
    conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])
    cursor = conn.cursor()

    cursor.execute(f"SELECT full FROM seek_sample_tree WHERE sample_id='{sample_id}'")

    cursor_results = cursor.fetchone()
    if cursor_results is not None:
        report['treeData_multiparents'] = json.loads(cursor_results[0])[0]
    else:
        report['treeData_multiparents'] = dbsample.createSampleMultiParentTree(sample_id)
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
    sampletype_id = int(id)
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

def datafileUpload(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
        return HttpResponseRedirect("/login/?next=/seek/data/upload/")
            
    report = {}
    isSupervisor = verifySuperUser(request)
    if isSupervisor==0: 
        options = []
        options.append({'id':-1, 'title':'Default','selected':True})
    else:
        options = seekdb.getObjectsToOptions("/institutions")
    
    report['lab_options'] = json.dumps(options, default=str)
    return render(request,"datafileUpload.html", {'report':report})

def __updateLabUser(seekdb, instituion_id, people_id):
    return seekdb

def filesBatchUpload(request):
    filetype = request.POST.get('filetype')
    instituion_id = request.POST.get('instituion_id')
    people_id = request.POST.get('people_id')
    md5 = request.POST.get('md5')
    report = {}
    report['msg'] = "Warning: tStart batch file uploading."
    report['status'] = 0
    report['df_info'] = {}            
    report['newrow'] = {} 
    
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request)
    if not user_seek['status']:
        report['msg'] = user_seek['err']
        err = user_seek['err']
        return HttpResponseRedirect("/login/?next=/seek/data/upload/")
        
    if verifySuperUser(request)==1:
        try:
            creator_id = int(people_id)
        except:
            msg = 'Error: You login as admin and must choose the creator.'
            status = 0
            logger.error(msg)
            report['msg'] = msg
            report['status'] = 0
            return HttpResponse(simplejson.dumps(report, default=str))

        if creator_id>0:
            status, msg = seekdb.updateCreator(instituion_id, people_id)
            if not status:
                report['msg'] = msg
                return HttpResponse(simplejson.dumps(report, default=str))
        else:
            msg = 'Error: You login as admin and must choose the creator.'
            status = 0
            logger.error(msg)
            report['msg'] = msg
            report['status'] = 0
            return HttpResponse(simplejson.dumps(report, default=str))
            
    infile = request.FILES['file']
    dfrecord = {}
    dfrecord['uid'] = ''
    dfrecord['originalname'] = infile.name
    dfrecord['fileurl'] = 'Not available ' + settings.SEEK_DATAFILE_SERVER
    dfrecord['notes'] = report['msg']
    dfrecord['content_type'] = filetype
    
    sampleRecord = None
    if filetype=="DATAFILE":
        dbsample = DBtable_sample()
        creator = seekdb.creator
        sampleRecord, msg = dbsample.searchFileInSample(creator, infile.name, filetype)
        if sampleRecord is None or sampleRecord['id']<=0:
            report['msg'] = msg
            report['status'] = 0
            dfrecord['notes'] = report['msg']
            report['newrow'] = dfrecord
            return HttpResponse(simplejson.dumps(report, default=str))

    if filetype=="DATAFILE":
        dbdf = DBtable_data_files("DEFAULT")
        sample_uid = sampleRecord['uuid']
        report = dbdf.uploadDF_toStorage(seekdb.creator, infile, sample_uid, md5)
    elif filetype=="SOP":
        dbsop = DBtable_sops("DEFAULT")
        report = dbsop.uploadSOP_toStorage(seekdb.creator, infile, md5)
    else:
        report = {}
        report['msg'] = "Warning: the file type is not supported yet: " + filetype
        report['status'] = 0
        report['df_info'] = {}            
        report['newrow'] = {}              
    
    return HttpResponse(simplejson.dumps(report, default=str))
    
    
def uploadToSeek(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request)
    if not user_seek['status']:
        err = user_seek['err']
        return HttpResponseRedirect("/login/?next=/seek/data/upload/")
    
    ret = request.POST
    filetype = ret['filetype']
    instituion_id = ret['instituion_id']
    people_id = ret['people_id']
    records = ret['records']
    diclist = json.loads(records)
        
    report = {}
    report['msg'] = "Warning: to be implemented"
    report['status'] = 0
    report['link'] = ""
    report['newrow'] = {}               
    if verifySuperUser(request)==1 and int(people_id)>0:
        logger.debug(f"Logged in user is superuser and uploading on behalf of user {people_id}")
        status, msg = seekdb.updateCreator(instituion_id, people_id)
        if not status:
            report['msg'] = msg
            return HttpResponse(simplejson.dumps(report, default=str))
    
    if filetype=="DATAFILE":
        dbdf = DBtable_data_files("DEFAULT")
        report = dbdf.uploadDFs_storageToSeek(seekdb, diclist)
    elif filetype=="SOP":
        dbsop = DBtable_sops("DEFAULT")
        report = dbsop.uploadSOPs_storageToSeek(seekdb, diclist)
        
    else:
        report = {}
        report['msg'] = "Warning: the file type is not supported yet: " + filetype
        report['status'] = 0
        report['df_info'] = {}            
        report['newrow'] = {}              
    
    return HttpResponse(simplejson.dumps(report, default=str))

def filesGetUIDs(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request)
    
    ret = request.GET
    allFiles = json.loads(ret['allfiles'])
    dbdf = DBtable_data_files("DEFAULT")
    data = dbdf.filesGetUIDs(seekdb, allFiles)
    return HttpResponse(simplejson.dumps(data, default=str))

def sopDownload(request, uid):
    print("sopDownload: " + uid)
    return fileDownload(request, uid, "SOP")


#@api_view(http_method_names=['GET'])
#@authentication_classes((TokenAuthentication,))
#@permission_classes((IsAuthenticated,))
def datafileDownload(request, uid):
    return fileDownload(request, uid, "DATAFILE")
    
def sopIDDownload(request, id):
    return fileDownload(request, id, "SOP_ID")
    
def fileDownloadEncoded(request, url_redirect, fileInfo):
    if url_redirect is not None:
        option = 1
        terms = url_redirect.split("/")
        filename = terms[-1]
    elif fileInfo is not None:
        option = 4
    else:
        option = 1
        
    if option==1:
        HttpResponseRedirect(url_redirect)
    elif option==2:
        #https://stackoverflow.com/questions/1156246/having-django-serve-downloadable-files
        from django.utils.encoding import smart_str
        
        file_path = fileInfo['fullfilename']
        file_name = fileInfo['originalfilename']
        
        response = HttpResponse(content_type='application/force-download')
        response['Content-Disposition'] = 'attachment; filename=%s' % smart_str(file_name)
        response['X-Sendfile'] = smart_str(file_path)
        return response
    elif option==3:
        from django.views.static import serve
        file_path = fileInfo['fullfilename']
        file_name = fileInfo['originalfilename']
        return serve(request, os.path.basename(file_path), os.path.dirname(file_path))
    elif option==4:
        # https://stackoverflow.com/questions/61351609/how-to-allow-users-to-directly-download-a-file-stored-in-my-media-folder-in-djan
        from django.utils.encoding import smart_str
        import mimetypes
        from wsgiref.util import FileWrapper
        
        file_path = fileInfo['fullfilename']
        file_name = fileInfo['originalfilename']
        
        file_wrapper = FileWrapper(open(file_path,'rb'))
        file_mimetype = mimetypes.guess_type(file_path)
        response = HttpResponse(file_wrapper, content_type=file_mimetype )
        response['X-Sendfile'] = file_path
        response['Content-Length'] = os.stat(file_path).st_size
        response['Content-Disposition'] = 'attachment; filename=%s' % smart_str(file_name) 
        return response
    else:
        HttpResponseRedirect(url_redirect)
        
    HttpResponseRedirect(url_redirect)
    
    
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
    
    
def fileDownload(request, uid, filetype):
    print(uid, filetype)
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request)
    if not user_seek['status']:
        err = user_seek['err']
        logger.error(err)
        if filetype=="DATAFILE":
            url_redirect = '/login/?next=/seek/datafiles/uid=' + uid + '/'
        elif filetype=="SOP":
            url_redirect = '/login/?next=/seek/sop/uid=' + uid + '/'
        elif filetype=="SOP_ID":
            url_redirect = '/login/?next=/seek/sop/download/id=' + uid + '/'
        else:
            url_redirect = '/login/'
        
        isokay = request.user.is_authenticated
        isTokenAuthenticated = verifyToken(request)
        if not isTokenAuthenticated:
            msg = "Login error: not token authenticated."
            logger.error(msg)
            return HttpResponseRedirect(url_redirect)
    
    url_redirect = None
    fileInfo = None
    if filetype=="DATAFILE":
        dbdf = DBtable_data_files("DEFAULT")
        msg, status, fileInfo = dbdf.downloadDF_fromStorage(user_seek, uid)
    elif filetype=="SOP":
        dbsop = DBtable_sops("DEFAULT")
        msg, status, fileInfo = dbsop.downloadSOP_fromStorage(user_seek, uid)
    elif filetype=="SOP_ID":
        dbsop = DBtable_sops("DEFAULT")
        sop_id = uid
        msg, status, fileInfo = dbsop.downloadSOPID_fromStorage(user_seek, sop_id)
    else:
        msg = 'Error: file type not supported.'
        logger.error(msg)
        status = 0
        
    if status==1:
        if filetype=="DATAFILE":
            return fileDownloadEncoded(request, url_redirect, fileInfo)
        elif filetype=="SOP" or filetype=="SOP_ID" :
            return fileDownloadEncoded(request, url_redirect, fileInfo)
        else:
            return HttpResponseRedirect(url_redirect)
    else:
        print(msg)
        return render(request, 'pages/404.html')
    return HttpResponse("You're downloading file %s." % uid)
 
def fileQuery(request, filetype):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        err = user_seek['err']
        if filetype=="DATAFILE":
            url_redirect = '/login/?next=/seek/datafile/query/'
        elif filetype=="SOP":
            url_redirect = '/login/?next=/seek/sop/query/'
        else:
            url_redirect = '/login/'
        return HttpResponseRedirect(url_redirect)

    pk = 0
    if filetype=="DATAFILE":
        dbdf = DBtable_data_files("DEFAULT")
        form, report = dbdf.formInfo(request, pk)
        report['table_url'] = '/seek/retrieve/datafiles/'
        report['batchdownload_url'] = '/seek/batchdownload/datafiles/'
        report['batchpublish_url'] = '/seek/datafiles/publish/'
    elif filetype=="SOP":
        dbsop = DBtable_sops("DEFAULT")
        form, report = dbsop.formInfo(request, pk)
        report['table_url'] = '/seek/retrieve/sops/'
        report['batchdownload_url'] = '/seek/batchdownload/sops/'
        report['batchpublish_url'] = '/seek/sops/publish/'
    else:
        msg = 'Error: file type not supported.'
        status = 0
        report = {}
        form= None
    
    return render(request,"batchSearch.html", {"report" : report, "form": form})

def datafileQuery(request):
    return fileQuery(request, "DATAFILE") 
    
def sopQuery(request):
    return fileQuery(request, "SOP") 
    
def filelist(request, filetype):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    
    if filetype=="DATAFILE":
        dbdf = DBtable_data_files("DEFAULT")
        reportData = dbdf.processRecords(request, user_seek, "retrieve")
    elif filetype=="SOP":
        dbsop = DBtable_sops("DEFAULT")
        reportData = dbsop.processRecords(request, user_seek, "retrieve")
    elif filetype=="DOWNLOAD_DATAFILE":
        dbdf = DBtable_data_files("DEFAULT")
        reportData = dbdf.processRecords(request, user_seek, "download")
    elif filetype=="DOWNLOAD_SOP":
        dbsop = DBtable_sops("DEFAULT")
        reportData = dbsop.processRecords(request, user_seek, "download")
    elif filetype=="PUBLISH_DATAFILE":
        dbdf = DBtable_data_files("DEFAULT")
        reportData = dbdf.processRecords(request, user_seek, "publish")
    elif filetype=="PUBLISH_SOP":
        dbsop = DBtable_sops("DEFAULT")
        reportData = dbsop.processRecords(request, user_seek, "publish")
    else:
        reportData = simplejson.dumps({})

    return HttpResponse(reportData)  
    
def retrieveSops(request):
    return filelist(request, "SOP")
    
def retrieveDatafiles(request):
    return filelist(request, "DATAFILE")
    
def batchdownloadSops(request):
    return filelist(request, "DOWNLOAD_SOP")
    
def batchdownloadDatafiles(request):
    return filelist(request, "DOWNLOAD_DATAFILE")
    
def batchpublishSops(request):
    return filelist(request, "PUBLISH_SOP")
    
def batchpublishDatafiles(request):
    return filelist(request, "PUBLISH_DATAFILE")
    
    
def callCmdline(cmd):
    args = shlex.split(cmd)
    proc = Popen(args, stdout=PIPE, stderr=PIPE)
    out, err = proc.communicate()
    exitcode = proc.returncode
    return exitcode, out, err    
    
def __getDropBoxFolders(user_seek, ifMkdir=True):
    username = user_seek['username']
    projectname = user_seek['projectname']
    institutionname = user_seek['institutionname']
    lababbv = user_seek['lababbv']
    labfolder = lababbv
            
    upload_dropbox_path_labroot = os.path.join(DROPBOX_DIRECTORY, labfolder)
    if not os.path.exists(upload_dropbox_path_labroot) and ifMkdir:
        os.makedirs(upload_dropbox_path_labroot)
        
    projectfolder = projectname
    if " " in projectname:
        projectfolder = projectname.replace(" ", "_")
            
    upload_dropbox_path_projectroot = os.path.join(upload_dropbox_path_labroot, projectfolder)
    if not os.path.exists(upload_dropbox_path_projectroot) and ifMkdir:
        os.makedirs(upload_dropbox_path_projectroot)
        
    upload_dropbox_path = upload_dropbox_path_projectroot
    dropbox_datafile_folder = os.path.join(upload_dropbox_path, 'for_datafiles')
    if not os.path.exists(dropbox_datafile_folder) and ifMkdir:
        os.makedirs(dropbox_datafile_folder)
    
    dropbox_sop_folder = os.path.join(upload_dropbox_path, 'for_protocols')
    if not os.path.exists(dropbox_sop_folder) and ifMkdir:
        os.makedirs(dropbox_sop_folder)
        
    return upload_dropbox_path, dropbox_datafile_folder, dropbox_sop_folder
    
def getDropboxPath(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request)
    if not user_seek['status']:
        err = user_seek['err']
        url_redirect = '/login/?next=/seek/dropbox/path/'
        return HttpResponseRedirect(url_redirect)

    msg = 'getDropboxPath'
    ifMkdir = True
    upload_dropbox_path, dropbox_datafile_folder, dropbox_sop_folder = __getDropBoxFolders(user_seek, ifMkdir)
        
    readmefile_root = DROPBOX_DIRECTORY + 'ReadME.html'
    cmd = 'cp ' + readmefile_root + ' ' + upload_dropbox_path
    exitcode, out, err = callCmdline(cmd)
    readmefile = upload_dropbox_path + '/ReadME.html'
    cmd = 'seek/dropbox.py sharelink ' + readmefile
    exitcode, out, err = callCmdline(cmd)
    if exitcode==0:
        msg = out.strip()
        return HttpResponseRedirect(msg)
    else:
        msg = 'Error: Dropbox not ready ' + str(err)
        return HttpResponse(msg)
    
def getDropboxStatus(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request)
    if not user_seek['status']:
        err = user_seek['err']
        url_redirect = '/login/?next=/seek/dropbox/status/'
        return HttpResponseRedirect(url_redirect)

    msg = 'getDropboxStatus'
    ifMkdir = False
    upload_dropbox_path, dropbox_datafile_folder, dropbox_sop_folder = __getDropBoxFolders(user_seek, ifMkdir)

    cmd = 'seek/dropbox.py ls ' + dropbox_datafile_folder
    exitcode, out, err = callCmdline(cmd)
    if exitcode==0:
        msg = out
    elif out is None:
        msg = 'Error: Dropbox not ready ' + str(err)
        return HttpResponse(msg)
    else:
        msg = 'Error: Dropbox not ready ' + str(err)
        return HttpResponse(msg)
    
    files = out.strip().split(' ')
    msg = ''
    for file in files:
        if file=='<empty>':
            msg = 'No file is found in the Dropbox folder.'
            continue
        else:
            msg += file + '\n'
    
    return HttpResponse(msg)
    
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
    
def samplePublish(request):
    ret = request.POST
    if 'allids' in ret:
        allids = ret['allids']
        sample_ids = json.loads(allids)
    else:
        sample_ids = []
        
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request)
    
    datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = 'samples-publish' + datenow + '.xlsx'
    downloadfile = DOWNLOAD_DIRECTORY + filename
    link = DOWNLOAD_DIRECTORY_LINK + filename
    
    templatefile = SAMPLE_TEMPLATE_FILE
    cmd = 'cp ' + templatefile + ' ' + downloadfile
    os.system(cmd)
    
    project_options, investigation_options_dic, study_options_dic, assay_options_dic, server = __getISA(seekdb, user_seek, "DESTINATION")
    
    report = {}
    report['project_options'] = project_options
    report['investigation_options_dic'] = investigation_options_dic
    report['study_options_dic'] = study_options_dic
    report['assay_options_dic'] = assay_options_dic
    report['showSamplePage'] = True
    report['showSearch'] = True
    return render(request,"publish.html", {'report':report})
    
def publish_assets(request, idsstring, assetType):
    ids = [int(i) for i in idsstring.split(',')]
    diclist = []
    for id in ids:
        dici = {}
        dici['id'] = id
        dici['asset_type'] = assetType
        if int(id)>0:
            diclist.append(dici)
    
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request)
    if not user_seek['status']:
        err = user_seek['err']
        url_redirect = '/login/?next=/seek/publish/'
        return HttpResponseRedirect(url_redirect)
    
    project_options, investigation_options_dic, study_options_dic, assay_options_dic, server = __getISA(seekdb, user_seek, "DESTINATION")
    print(investigation_options_dic)
    
    report = {}
    report['project_options'] = project_options
    report['investigation_options_dic'] = investigation_options_dic
    report['study_options_dic'] = study_options_dic
    report['assay_options_dic'] = assay_options_dic
    report['showSamplePage'] = True
    report['showSearch'] = True
    report['assetData'] = diclist
    report['publish_server'] = server
    return render(request,"publishAssets.html", {'report':report})
    
def publish_samples(request, sampleids=None):
    assetType = 'Sample'
    return publish_assets(request, sampleids, assetType)

def publish_datafiles(request, dfids=None):
    assetType = 'Datafile'
    return publish_assets(request, dfids, assetType)

def publish_sops(request, sopids=None):
    assetType = 'Sop'
    return publish_assets(request, sopids, assetType)
    
def publish(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request)
    if not user_seek['status']:
        err = user_seek['err']
        url_redirect = '/login/?next=/seek/publish/'
        return HttpResponseRedirect(url_redirect)
    
    project_title = user_seek['projectname']
    investigations = seekdb.getInvestigations(project_title)
    investigation_options = json.dumps(convertDicToOptions(investigations), default=str)
    
    report = {}
    report['investigation_options'] = investigation_options
    report['showSamplePage'] = True
    report['showSearch'] = True
    return render(request,"publish.html", {'report':report})
    
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
    
def publishSearching(request):
    ret = request.GET
    assay_id = ret['assay_id']
    investigation = ret['investigation']
    study = ret['study']
    assay = ret['assay']
    
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    
    diclist = seekdb.getAssayDFs(assay_id, investigation, study, assay)
    data = {}
    data['rows'] = diclist
    data['total'] = len(diclist)
    data['msg'] = 'okay'
    data['status'] = 1 
    sdata = simplejson.dumps(data, default=str)
    return HttpResponse(sdata)    
    
def publishAssets(request):
    ret = request.GET
    project = ret['project']
    investigation = ret['investigation']
    study = ret['study']
    assay = ret['assay']
    project_id = ret['project_id']
    investigation_id = ret['investigation_id']
    study_id = ret['study_id']
    assay_id = ret['assay_id']
    allids = ret['allids']
    df_ids = json.loads(allids)
    ids = df_ids
    
    assettypes = ret['assettypes']
    types = json.loads(assettypes)
    
    seekdb = SeekDB(None, None, None)
    
    iddic = {}
    for i, type in enumerate(types):
        if type in iddic:
            idlist = iddic[type]
        else:
            idlist = []
            
        id = ids[i]
        idlist.append(id)
        iddic[type] = idlist
        
    for type in iddic:
        if type=='Sample':
            sample_ids = iddic[type]
            user_seek = seekdb.getSeekLogin(request, False)
            return publishSamples(user_seek, sample_ids, assay_id, project_id)
        elif type.upper()=='SOP':
            user_seek = seekdb.getSeekLogin(request)
            sdb, user = __definePublishServer(seekdb, user_seek)
            sop_ids = iddic[type]
            dbsop = DBtable_sops("DEFAULT")
            sdata = dbsop.publishSOPs(user_seek, sdb, user, sop_ids, assay_id, project_id)
            return HttpResponse(sdata)
        elif type=='Datafile':
            user_seek = seekdb.getSeekLogin(request)
            sdb, user = __definePublishServer(seekdb, user_seek)
            df_ids = iddic[type]
            dbdf = DBtable_data_files("DEFAULT")
            sdata = dbdf.publishDFs(user_seek, sdb, user, df_ids, assay_id, project_id)
            return HttpResponse(sdata)
    
    return None
    
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

                df = pd.read_sql('''
                    SELECT
                        sa.id AS attribute_id,
                        sa.title AS attribute_title,
                        sa.sample_type_id, st.title AS sample_type_title
                    FROM
                        seek_production.sample_attributes sa
                    JOIN
                        seek_production.sample_types st ON sa.sample_type_id = st.id
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

def templatesList(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        url_redirect = '/login/?next=/seek/templates'
        return HttpResponseRedirect(url_redirect)
    else:
        headers = {'Accept': 'application/json'}
        r = requests.get(user_seek['server'] + '/projects', auth=(user_seek['username'], user_seek['password']), headers=headers)
        projects = [p['id'] for p in r.json()['data']]
        if not SAMPLE_TEMPLATES_FOLDER_PROJECT in projects:
            msg = 'You are not in the correct project to access this page'
            status = 0
            data = {'msg':msg, 'status': status, 'link': ""}
            return HttpResponse(simplejson.dumps(data, default=str)) 

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

def get_full_sample_trees_and_data(uids):
    db = settings.DATABASES[SEEK_DATABASE]
    conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])
    cursor = conn.cursor()

    uids_str = ', '.join(f"'{uid}'" for uid in uids)
    query_one = f"""
    SELECT s.id, s.sample_type_id, st.title AS sample_type, s.uuid, s.json_metadata
    FROM seek_production.samples s
    JOIN seek_production.sample_types st
    ON s.sample_type_id = st.id
    WHERE s.uuid IN ({uids_str})
    """

    cursor.execute(query_one)
    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchall()
    sample_data = pd.DataFrame(rows, columns=columns)

    query_two = f"""
    SELECT uuid, full, updated 
    FROM dmac.seek_sample_tree
    WHERE uuid IN ({uids_str})
    """

    cursor.execute(query_two)

    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchall()
    results_df = pd.DataFrame(rows, columns=columns)

    cursor.close()
    conn.close()

    results_df['updated'] = pd.to_datetime(results_df['updated'])
    results_df = results_df.sort_values(by='updated', ascending=False)
    sample_full_trees = results_df.groupby('uuid').first().reset_index()
    return sample_data, sample_full_trees

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
    conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])
    cursor = conn.cursor()
    query = f"""
    SELECT c.color FROM dmac.clades c
    JOIN dmac.sample_types st
    ON st.clade_id = c.id
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
    NEO4J_DATABASE = settings.NEO4J_DATABASE
    with GraphDatabase.driver(NEO4J_DATABASE['URI'], auth=NEO4J_DATABASE['AUTH']) as driver:
        r,s,k = driver.execute_query("""
		UNWIND $sample_uids AS sample_uid
        MATCH (s:Sample {uuid: sample_uid})
        MATCH parents=(s)-[:CHILD_OF*0..]->(parent)
        MATCH children=(s)<-[:CHILD_OF*0..]-(child)
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
        FROM seek_production.samples
        WHERE uuid IN ({uids_str})
        """
    else:
        query = f"""
        SELECT s.id, s.sample_type_id, s.uuid, s.json_metadata
        FROM seek_production.samples s
        JOIN seek_production.projects_samples ps
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
    parsed_df = parse_children_uids(children_uids)
    parsed_df['sample_type'] = parsed_df['uuid'].str.extract(r'([A-Z]+\.[A-Z]+|[A-Z]+)', expand=False)

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        for sample_type, sample_type_df in parsed_df.groupby('sample_type'):
            sample_type_df = sample_type_df.drop(columns=['uuid', 'sample_type'])
            sample_type_df.replace("", pd.NA, inplace=True)
            sample_type_df.dropna(axis=1, how='all', inplace=True)
            sample_type_df.to_excel(writer, sheet_name=sample_type, index=False)

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
            #sample_data, sample_full_trees = get_full_sample_trees_and_data(uids)
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

@api_view(['GET'])
@authentication_classes([TokenAuthentication, SessionAuthentication])
def sampleTreeNew(request, sample_id):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        url_redirect = '/login/?next=/seek/templates'
        return HttpResponseRedirect(url_redirect)

    NEO4J_DATABASE = settings.NEO4J_DATABASE
    with GraphDatabase.driver(NEO4J_DATABASE['URI'], auth=NEO4J_DATABASE['AUTH']) as driver:
        r = driver.execute_query("""
        MATCH (s: Sample {id: """ + str(sample_id)  + """})
        MATCH parents=(s)-[r1:CHILD_OF*0..]->(parent)
        MATCH children=(s)<-[r2:CHILD_OF*0..]-(child)
        RETURN collect(DISTINCT s) + collect(DISTINCT parent) + collect(DISTINCT child) AS nodes, r1 + r2 AS relationships
        """,
        result_transformer_=neo4j.Result.graph,
        database_=NEO4J_DATABASE['NAME'])

        nodeDict = {}
        for node in r.nodes:
            nodeUid = node._properties['uuid']
            nodeId = node._properties['id']
            nodeType = node._properties['type']
            nodeDict[nodeUid] = {'id': str(nodeId), 'type': nodeType, 'parents': []}

        for rel in r.relationships:
            nodeId = str(rel.start_node._properties['id'])
            nodeUid = rel.start_node._properties['uuid']
            nodeType = rel.start_node._properties['type']
            parentId = str(rel.end_node._properties['id'])

            if nodeDict.get(nodeUid) is not None:
                nodeDict[nodeUid]['parents'].append(parentId)
            else:
                nodeDict[nodeUid]['parents'] = [parentId]

        data = []
        for k, v in nodeDict.items():
            color = get_clade_color(v['type'])
            data.append({"id": v['id'], "uuid": k, "type": v['type'], "color": color, "parentIds": v['parents']})

        return Response(data, status=status.HTTP_200_OK)

@api_view(['GET'])
@authentication_classes([TokenAuthentication, SessionAuthentication])
def sampleTreeNewUID(request, uid):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        url_redirect = '/login/?next=/seek/templates'
        return HttpResponseRedirect(url_redirect)

    dbsample = DBtable_sample()
    sample_id = dbsample.getSampleID(uid)
    NEO4J_DATABASE = settings.NEO4J_DATABASE
    with GraphDatabase.driver(NEO4J_DATABASE['URI'], auth=NEO4J_DATABASE['AUTH']) as driver:
        r = driver.execute_query("""
        MATCH (s: Sample {id: """ + str(sample_id)  + """})
        MATCH parents=(s)-[r1:CHILD_OF*0..]->(parent)
        MATCH children=(s)<-[r2:CHILD_OF*0..]-(child)
        RETURN collect(DISTINCT s) + collect(DISTINCT parent) + collect(DISTINCT child) AS nodes, r1 + r2 AS relationships
        """,
        result_transformer_=neo4j.Result.graph,
        database_=NEO4J_DATABASE['NAME'])

        nodeDict = {}
        for node in r.nodes:
            nodeUid = node._properties['uuid']
            nodeId = node._properties['id']
            nodeType = node._properties['type']
            nodeDict[nodeUid] = {'id': str(nodeId), 'type': nodeType, 'parents': []}

        for rel in r.relationships:
            nodeId = str(rel.start_node._properties['id'])
            nodeUid = rel.start_node._properties['uuid']
            nodeType = rel.start_node._properties['type']
            parentId = str(rel.end_node._properties['id'])

            if nodeDict.get(nodeUid) is not None:
                nodeDict[nodeUid]['parents'].append(parentId)
            else:
                nodeDict[nodeUid]['parents'] = [parentId]

        data = []

        for k, v in nodeDict.items():
            color = get_clade_color(v['type'])
            data.append({"id": v['id'], "uuid": k, "type": v['type'], "color": color, "parentIds": v['parents']})

        return Response(data, status=status.HTTP_200_OK)

def projects(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)

    if not user_seek['status']:
        url_redirect = "/login/?next=/seek/projects/"
        return HttpResponseRedirect(url_redirect) 
    else:
        projectsdb = DBtable_projects()
        user_projects = seekdb.getCurrentUser()['data']['relationships']['projects']['data']
        user_project_ids = list(map(lambda x: x['id'], user_projects))

        if verifySuperUser(request) == 1:
            projects = Projects.objects.all().values('id', 'title', 'avatar_id')
        else:
            projects = Projects.objects.filter(id__in=user_project_ids).values('id', 'title', 'avatar_id')

        for project in projects:
            project['stats'] = projectsdb.sample_count(project['id']) | projectsdb.files_count(project['id'])

        stcdb = DBtable_stc()
        clade_data = {k: list(v) for k, v in groupby(stcdb.getAllCounts(), lambda x: x['title'])}

        for k, group in clade_data.items():
            for item in group:
                item['total'] = sum(i['count'] for i in group)
               
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
        user_project_ids = map(lambda x: x['id'], user_projects)

        # Can't view projects unless you're in it or an admin
        if not admin and int(project_id) not in user_project_ids:
            data = {'msg': 'You are not in this project', 'status': 0, 'link': ''}
            return render(request, 'error.html', {'data': data})

        # Project page
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
    return HttpResponseRedirect(f"https://{SEEK_HOSTNAME}/samples/{id}/edit")

def manageSample(request, id):
    return HttpResponseRedirect(f"https://{SEEK_HOSTNAME}/samples/{id}/manage")
