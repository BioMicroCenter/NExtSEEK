#!/usr/bin/env python
import os
import datetime
import simplejson

import zipfile

from subprocess import call
import logging
logger = logging.getLogger(__name__)

from .seekdb import SeekDB
from .models import Data_files
from .models import Projects
from dmac.dbtable import DBtable
from dmac.iocsv import saveCsvfile
from dmac.conversion import handle_uploaded_file, correctFileName, sizeof_fmt, getFileChecksum, verifyFileChecksum

from django.conf import settings
from django import forms
from django.db.models import Q

from .dbtable_assay_assets import DBtable_assay_assets
from .dbtable_content_blobs import DBtable_content_blobs

DOWNLOAD_DIRECTORY  = settings.MEDIA_ROOT + "/download/"
DOWNLOAD_DIRECTORY_LINK = settings.MEDIA_URL + '/download/'
DATA_FILES_FILTER_MAPPING = {
}

DATA_FILES_DEFAULT = {
    #'id':'',
    'contributor_id':0,
    'title':'',
    'description':'',
    'template_id':None,
    'created_at':'',
    'updated_at':'',
    'version':1,
    'first_letter':'',
    'other_creators':'',
    'uuid':'',
    'policy_id':'',
    'doi':None,
    'license':'CC-BY-4.0',
    'simulation_data':0,
    'deleted_contributor':None        
}

DATA_FILE_UID_DELIMITER = "_"
BATCHSEARCHFORM_MAPPING = {
    'keywords':'PK',
    'status':'Status'
}
BATCHSEARCHFORM_DEFAULT = {
    #'pk':'',   
    'keywords':'',
    'category':'ALL'
}

CATEGORY_CHOICES = (
    ("ALL", "All"),
    ("ASSAYS", "Assays"),
    ("DATAFILES", "Data files"),
    ("SAMPLES", "Samples"),
    ("SAMPLETYPES", "Sample types")
)
  
DATAFILE_ERRORCODE = {
    '001': 'Error: User not logged in.',
    '101': 'Warning: File already uploaded in Seek thus no update.',
    '201': 'Error: Data file UID not in right format: ',
    '202': 'Error: Data file uploading into datafile table failed: ',
    '203': 'Error: Data file uploading into content_blob table failed: ',
    '204': 'Warning: Data file and sample association not saved correctly into DB: ',
    '301': 'Error: Data file UID not in right format: ',
    '302': 'Error: Data file UID not found in DB: ',
    '303': 'Error: More than one data file found for the UID: ',
    '304': 'Error: User info not found in DB: ',
    '305': 'Error: File not found on server as a file: ',
    '306': 'Error: File not found on server: '
}  
  
class BatchSearchForm(forms.Form):
    keywords = forms.CharField(required=True, label='Keywords', widget=forms.Textarea )
    category = forms.ChoiceField(required=True, label="Category", initial='', choices=CATEGORY_CHOICES, widget=forms.Select())

class DBtable_data_files(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'SEEK', 'seek_development')
        
        self.tablename = 'data_files'
        self.tablemodel = Data_files
        self.fulltablename = self.tablemodel
        self.viewtablename = self.dbname + '.' + self.tablename
        self.fields = [
            'id',
            'contributor_id',
            'title',
            'description',
            'template_id',
            'created_at',
            'updated_at',
            'version',
            'first_letter',
            'other_creators',
            'uuid',
            'policy_id',
            'doi',
            'license',
            'simulation_data',
            'deleted_contributor',
            'file_template_id',
        ]
        
        self.uniqueFields = ['title']
        self.primaryField = "id"
        self.fieldMapping = DATA_FILES_FILTER_MAPPING
        self.excludeFields = []
        
        report = {}
        self.form = BatchSearchForm(report)
        self.formDefault = BATCHSEARCHFORM_DEFAULT
        self.formMapping = BATCHSEARCHFORM_MAPPING
    
    def __getUID(self, lababbv, filename, nassets=0, sample_uid=None):
        if sample_uid in filename:
            uid = filename
        else:
            uid = sample_uid + DATA_FILE_UID_DELIMITER + filename    
        return uid

    def __defineUploadFilename(self, username, infilename, uid):
        outfilename = uid
        return outfilename
    
    def __getWeblink(self, uid):
        url = "/seek/datafile/uid=" + uid + "/"
        weburl = settings.SEEK_DATAFILE_SERVER + url
        weblink = '<a href="' + url + '" target="_blank">' + weburl + '</a>'
        return weblink, weburl
    
    def __getUploadPath(self, creator):
        projectname = creator['projectname']
        lababbv = creator['lababbv']
        labfolder = lababbv
        projectfolder = projectname
        if " " in projectname:
            projectfolder = projectname.replace(" ", "_")

        upload_full_path_projectroot = os.path.join(settings.SEEK_DATAFILE_ROOT, projectfolder)
        if not os.path.exists(upload_full_path_projectroot):
            os.makedirs(upload_full_path_projectroot)
        
        upload_full_path_labroot = os.path.join(upload_full_path_projectroot, labfolder)
        if not os.path.exists(upload_full_path_labroot):
            os.makedirs(upload_full_path_labroot)
        
        upload_full_path = upload_full_path_labroot        
        return lababbv, upload_full_path
 
    def __getUploadPathByUID(self, uid):
        fileInfo = {
            'uid':'',               
            'sampleuid':'',         
            'originalfilename':'',  
            'lababbv':'',           
            
            'upload_full_path':'',  
            'fullfilename':'',      
        }
        fileInfo['uid'] = uid  
        if DATA_FILE_UID_DELIMITER not in uid:
            return fileInfo
        
        terms = uid.split(DATA_FILE_UID_DELIMITER)
        sampleuid = terms[0] 
        fileInfo['sampleuid'] = sampleuid
        
        terms = terms[1:]
        originalfilename = DATA_FILE_UID_DELIMITER.join(terms)
        fileInfo['originalfilename'] = originalfilename
        
        if '-' not in sampleuid:
            return fileInfo
        
        terms = sampleuid.split("-")
        dateabbr = terms[1]     
        if len(dateabbr)!=9:
            return fileInfo
        
        lababbv = dateabbr[-3:] 
        fileInfo['lababbv'] = lababbv       
        labfolder = lababbv
        upload_full_path = ''
        fullfilename = ''
        p = Projects()
        projects = p.getProjects()
        for projectfolder in projects:
            upload_full_path_projectroot = os.path.join(settings.SEEK_DATAFILE_ROOT, projectfolder)
            upload_full_path_labroot = os.path.join(upload_full_path_projectroot, labfolder)
            full_path = upload_full_path_labroot
            if os.path.isdir(full_path):
                upload_full_path = full_path
            
            outfilename = uid
            filepathname = os.path.join(full_path, outfilename)
            if os.path.isfile(filepathname) and os.path.exists(filepathname):
                fullfilename = filepathname
        
        fileInfo['upload_full_path'] = upload_full_path
        fileInfo['fullfilename'] = fullfilename
        return fileInfo
    
    def __defineUploadPath(self, creator, originalfilename, nassets=0, sample_uid=None):
        infilename_corrected = correctFileName(originalfilename)
        username = None 
        lababbv, upload_full_path = self.__getUploadPath(creator)
        pid = 0
        uid = self.__getUID(lababbv, infilename_corrected, nassets, sample_uid)
        outfilename = self.__defineUploadFilename(username, infilename_corrected, uid)
            
        weblink, weburl = self.__getWeblink(uid)
        dfrecord = {}
        dfrecord['id'] = pid
        dfrecord['uid'] = uid
        dfrecord['originalname'] = originalfilename
        dfrecord['weburl'] = weblink               
        dfrecord['fileurl'] = weburl                 
        dfrecord['notes'] = ''
        dfrecord['outfilename'] = outfilename
        dfrecord['upload_full_path'] = upload_full_path
        fullfilename = os.path.join(upload_full_path, outfilename)
        dfrecord['fullfilename'] = fullfilename
        return dfrecord
    
    def __getSeeklink(self, originalfilename, datafile_id):
        seek_url = settings.SEEK_PUBLIC_URL + "/data_files/" + str(datafile_id)
        seeklink = '<a href="' + seek_url + '" target="_blank">' + originalfilename + '</a>'
        return seeklink
    
    def __defineDatafile(self, creator, originalfilename, sample_uid):
        dbcb = DBtable_content_blobs("DEFAULT")
        asset_id, asset_type, asset_version, nassets = dbcb.searchFile(originalfilename, 'DataFile')
        dfrecord = self.__defineUploadPath(creator, originalfilename, nassets, sample_uid)
        return asset_id, asset_type, dfrecord
    
    def __searchDatafile(self, creator, originalfilename, sample_uid):
        asset_id, asset_type, dfrecord = self.__defineDatafile(creator, originalfilename, sample_uid)
        if asset_id is not None and asset_type=='DataFile':
            record = self.getOneRecord(asset_id)
            contributor_id = None
            if 'contributor_id' in record:
                contributor_id = record['contributor_id']
                
            if contributor_id is not None and int(contributor_id)==creator['user_id']:
                dfrecord['id'] = asset_id
                dfrecord['uid'] = record['title']
                dfrecord['originalname'] = originalfilename
                dfrecord['originalname_url'] = self.__getSeeklink(originalfilename, asset_id)
                dfrecord['notes'] = 'Warning: already uploaded, no update enforced.'
                weblink, weburl = self.__getWeblink(record['title'])
                dfrecord['weburl'] = weblink
                dfrecord['fileurl'] = weburl 
                return asset_id, dfrecord
    
        return None, dfrecord
    
    
    def uploadFileLink_intoSeek(self, seekdb, infile, sample_uid):
        report = {}
        if seekdb.user_seek is None:
            report['msg'] = 'Error: user not loged in.'
            report['status'] = 0
            return report
        
        df_id, dfrecord = self.__searchDatafile(seekdb.creator, infile.name, sample_uid)
        if df_id>0:
            report['msg'] = 'Warning: file already uploaded in Seek, no update: ' + infile.name
            report['status'] = 0
            report['newrow'] = dfrecord
            return report
        
        fullfilename = dfrecord['fullfilename']
        self.handle_uploaded_file(infile, fullfilename)
        userid = seekdb.creator['user_id']
        project_id = seekdb.creator['projectid']
        assay_id = None
        tags = None
        
        content_type = infile.content_type
        dfrecord['content_type'] = content_type
    
        datatitle = dfrecord['outfilename']
        description = 'File uploaded from DropZone'
        msg, status, df_info, datafile_url = seekdb.seekupload(
            datatitle,
            fullfilename,
            infile.name,
            content_type,
            userid,
            project_id,
            assay_id,
            description,
            tags
        )
        if status==1:
            df_id, dfrecord = self.__searchDatafile(seekdb.creator, infile.name, sample_uid)
            if df_id>0:
                dfrecord['notes'] = 'Successful'
            else:
                dfrecord['notes'] = 'Error: failed in uploading file into Seek.'
        else:
            dfrecord['id'] = ''
            dfrecord['uid'] = ''
            dfrecord['notes'] = 'Error: failed in uploading file into Seek.'
    
        report['msg'] = msg
        report['status'] = status
        report['df_info'] = df_info
        report['newrow'] = dfrecord
        return report
        
    def uploadDF_toStorage(self, creator, infile, sample_uid, md5In):
        originalfilename = infile.name
        report = {}
        if creator is None:
            report['msg'] = DATAFILE_ERRORCODE['001']
            report['status'] = 0
            dfrecord = {}
            dfrecord['uid'] = ''
            dfrecord['originalname'] = originalfilename
            dfrecord['notes'] = report['msg']
            report['newrow'] = dfrecord
            return report
        
        content_type = infile.content_type       
        df_id, dfrecord = self.__searchDatafile(creator, originalfilename, sample_uid)
        dfrecord['content_type'] = content_type
        
        fullfilename = dfrecord['fullfilename']
        if df_id is not None:
            if df_id>0:
                report['msg'] = DATAFILE_ERRORCODE['101']
                report['status'] = -1
                dfrecord['notes'] = report['msg']
                report['newrow'] = dfrecord
                return report
        
        handle_uploaded_file(infile, fullfilename)
        md5Now = getFileChecksum(fullfilename, 'MD5')
        if md5In is None:
            msg = 'Warning: File MD5 checksum on server: ' + md5Now
            status = 1
        elif md5Now!=md5In:
            msg = 'Error: File MD5 checksum not match: ' + md5Now
            status = 0
        else:
            msg = 'File uploaded to storage server'
            status = 1
        
        report['md5'] = md5Now
        report['msg'] = msg
        report['status'] = status
        dfrecord['notes'] = report['msg']
        report['newrow'] = dfrecord
        return report
        
    def uploadDF_storageToSeek(self, seekdb, originalfilename, content_type, df_uid, weburl, istest=False):
        report = {}
        if DATA_FILE_UID_DELIMITER not in df_uid:
            msg = DATAFILE_ERRORCODE['201'] + df_uid
            report['msg'] = msg
            report['status'] = status
            report['df_info'] = df_info
            report['newrow'] = {'notes':msg}
            return report
        
        terms = df_uid.split(DATA_FILE_UID_DELIMITER)
        sample_uid = terms[0]
        creator = seekdb.creator
        submitter = seekdb.user_seek['username']
        dfrecord = self.__defineUploadPath(creator, originalfilename, 0, sample_uid)
        
        userid = creator['user_id']
        project_id = creator['projectid']
        assay_id = None
        tags = None
        
        dfrecord['content_type'] = content_type
        datatitle = dfrecord['uid']
        fullfilename = dfrecord['fullfilename']
        description = 'File uploaded from DropZone'
        
        if istest:
            print('sample_uid: %s'%sample_uid)
            print('userid: %s'%userid)
            print('project_id: %s'%project_id)
            print('content_type: %s'%content_type)
            print('datatitle: %s'%datatitle)
        
        msg, status, df_info, datafile_url = seekdb.seekupload_dfurl(
            datatitle,
            fullfilename,
            originalfilename,
            content_type,
            userid,
            project_id,
            assay_id,
            description,
            tags,
            weburl
        )
        if status==1:
            record_cb = df_info["attributes"]["content_blobs"][0]
            record_cb['url'] = weburl
            
            md5, sha1, filesize = verifyFileChecksum(fullfilename)
            record_cb['md5sum'] = md5
            record_cb['sha1sum'] = sha1
            record_cb['size'] = filesize
            
            record_cb['is_webpage'] = 1
            record_cb['external_link'] = 1
            dbcb = DBtable_content_blobs("DEFAULT")
            msg, status, cb_id = dbcb.storeOneRecord(submitter, record_cb)
            if status:
                df_link = df_info["attributes"]["versions"][0]['url']
                dfrecord = {}
                dfrecord['id'] = df_info['id']
                dfrecord['uid'] = df_uid
                dfrecord['originalname'] = originalfilename
                dfrecord['fileurl'] = weburl
                
                dfrecord['notes'] = df_link
                dfrecord['fullfilename'] = fullfilename
                
                if istest:
                    print('Skip sample update in a test run.')
                else:
                    from .dbtable_sample import DBtable_sample  # deferred: circular import, see dbtable_sample.py:32
                    dbsample = DBtable_sample()
                    msg, status = dbsample.updateSampleDFurl(submitter, sample_uid, originalfilename, df_link)
                    if not status:
                        msg = DATAFILE_ERRORCODE['204'] + msg
            else:
                msg = DATAFILE_ERRORCODE['203'] + msg
                dfrecord['notes'] = msg
            
        else:
            dfrecord['id'] = ''
            dfrecord['uid'] = ''
            msg = DATAFILE_ERRORCODE['202'] + msg
            dfrecord['notes'] = msg
            
        report['msg'] = msg
        report['status'] = status
        report['df_info'] = df_info
        report['newrow'] = dfrecord
        return report                   
        
    def uploadDFs_storageToSeek(self, seekdb, diclist):
        report = {}
        report['msg'] = 'test uploadDF_storageToSeek()'
        report['status'] = 0
        report['df_info'] = ''
        report['newrows'] = []
        
        newrows = []
        status = 1
        msg = ''
        for dici in diclist:
            originalfilename = dici['originalname']
            content_type = dici['content_type']
            df_uid = dici['uid']
            weburl = dici['fileurl']
            reporti = self.uploadDF_storageToSeek(seekdb, originalfilename, content_type, df_uid, weburl)
            newrows.append(reporti['newrow'])
            if reporti['status']==0:
                status = 0
                msg += reporti['msg'] + '\n'
        
        report['msg'] = msg
        report['status'] = status
        report['df_info'] = ''
        report['newrows'] = newrows
        return report     
        
    def downloadDF_fromStorage(self, user_seek, uid):
        msg = 'Waening: File to be found on server: ' + uid
        status = 0
        weblink = None
        fileInfo = {}
        if DATA_FILE_UID_DELIMITER not in uid:
            msg = DATAFILE_ERRORCODE['301'] + uid
            logger.debug(msg)
            return msg, status, fileInfo
        
        constraint = {"title":uid}
        diclist = self.queryRecordsByConstraint(constraint)
        if diclist is None or len(diclist)==0:
            msg = DATAFILE_ERRORCODE['302'] + uid
            return msg, status, fileInfo
        elif len(diclist)>1:
            msg = DATAFILE_ERRORCODE['303'] + uid
            return msg, status, fileInfo
        
        user_seek = None
        if user_seek is None:
            fileInfo = self.__getUploadPathByUID(uid)
        else: 
            record = diclist[0]
            contributor_id = record['contributor_id']
            seekdb = SeekDB(user_seek['server'], user_seek['username'], user_seek['password'])
            creator, status, msg = seekdb.getUserInfo(contributor_id)
            if not status:
                msg = DATAFILE_ERRORCODE['304'] + msg
                return msg, status, fileInfo
        
            lababbv, upload_full_path = self.__getUploadPath(creator)
            outfilename = self.__defineUploadFilename(username, None, uid)
            fullfilename = os.path.join(upload_full_path, outfilename)
            fileInfo = {
                'uid':uid,               
                'sampleuid':'',         
                'originalfilename':'',  
                'lababbv':lababbv,  
                'upload_full_path':upload_full_path,  
                'fullfilename':fullfilename,      
            }
        fileInfo['uid'] = uid
        fullfilename = fileInfo['fullfilename']
        if fullfilename=='':
            status = 0
            msg = DATAFILE_ERRORCODE['305'] + fullfilename
            logger.debug(msg)
            fileInfo['weblink'] = ''
            return msg, status, fileInfo
       
        weblink = fullfilename
        weblink = weblink.replace(settings.SEEK_DATAFILE_ROOT, settings.SEEK_DATAFILE_ROOT_WEBLINK)
        weblink = settings.SEEK_DATAFILE_SERVER + weblink
        fileInfo['weblink'] = weblink
        
        if not os.path.isfile(fullfilename):
            status = 0
            msg = DATAFILE_ERRORCODE['305'] + fullfilename
            logger.debug(msg)
            return msg, status, fileInfo
 
        if not os.path.exists(fullfilename):
            msg = DATAFILE_ERRORCODE['306'] + fullfilename
            logger.debug(msg)
            return msg, status, fileInfo
        
        status = 1
        msg = fullfilename
        return msg, status, fileInfo
        
    def __getDatafileUrl(self, filename):
        link = settings.SEEK_DATAFILE_ROOT_WEBLINK + filename
        url = link
        return url
    
    def filesGetUIDs(self, seekdb, allFiles):
        diclist = []
        index = 0
        for filename in allFiles:
            index += 1
            dici = self.__defineUploadPath(seekdb.user_seek, filename)
            diclist.append(dici)
    
        datenow = str(datetime.datetime.now().strftime("%Y-%m-%d_%H"))
        filename = 'datafile-upload-feedback-' + datenow + '.csv'
        feedbackfile = DOWNLOAD_DIRECTORY + filename
        link = DOWNLOAD_DIRECTORY_LINK + filename
    
        headers = ['id', 'uid', 'originalname', 'fileurl']
        saveCsvfile(feedbackfile, headers, diclist)
    
        status = 1
        msg = 'okay'
        data = {'message':msg, 'status':status, 'link':link}
        data["allrows"] = index
        data["total"] = index
        data['rows'] = diclist
        return data
        
    def storeDataFile(self, username, sampleType, record, attributeInfo, uploadEnforced=False):
        if not self.__notEmptyLine(record):
            msg = 'Error: record for uploading empty in ' + sampleType
            return msg, 0, None
        
        headers_required = attributeInfo['headers_required']
        msg_required, meetRequired = self.__verifyRequiredFields(record, headers_required)
        if not meetRequired:
            msg = 'Error: ' + msg_required
            return msg, 0, None
                
        if 'UID' not in record.keys():
            msg = 'Error: Sample record does not have a UID field.'
            return msg, 0, None
        
        record_new = self.__getRecord(username, record, attributeInfo)
        uid = record_new['title'] 
        if not uploadEnforced:
            msg = 'Warning: Upload not enforced, test okay.'
            return 'Upload not enforced', 1, uid

        msg, status, sample_id = self.storeOneRecord(username, record_new)
        if status:
            self.__updateProject(username, sample_id)
        
        return msg, status, uid
    
    def __getDatafilePID(self, dfurl):
        terms = dfurl.split("/")
        filename = terms[-1]        #such as DF-20200106-22_dbadmin_training-dataset.csv
        
        ids = filename.split("_")   
        uid = ids[0]                #such as DF-20200106-22
        
        pids = uid.split('-')
        pid = pids[-1]              #such as 22
        
        try:
            id = int(pid)    
        except:
            id = -1
        return id
    
    def processSampleDatafile(self, user_seek, sampleType, dfurl, diclist_assay):
        msg = 'To be implemented!'
        status = 0
    
        dfurl_prefix = settings.SEEK_URL + "/data_files/"
        rooturl = settings.SEEK_DATAFILE_ROOT_WEBLINK
        
        if dfurl is None or len(dfurl)==0:
            msg = 'Warning: Data file url not available in Seek system thus ignored: ' + dfurl
            status = 0
            return msg, status
        elif dfurl_prefix in dfurl:
            if dfurl[-1]=="/":
                dfurl = dfurl[:-1]
            terms = dfurl.split("/")
            id = terms[-1]
            try:
                dfid = int(id)
            except:
                dfid = None
        elif rooturl not in dfurl:
            msg = 'Warning: Data file url not available in Seek system thus ignored: ' + dfurl
            status = 0
            return msg, status
        else:
            dfid = None
        
        if dfid is None or dfid<=0:
            msg = 'Warning: Data file UID not valid thus ignored: ' + dfurl
            status = 0
            return msg, status
        
        assay_assets = DBtable_assay_assets("DEFAULT")
        msg, status = assay_assets.storeDatafile_assay_asset(user_seek, dfid, sampleType, diclist_assay)                 
        return msg, status
    
    def reformatDataForClient(self, jdata):
        dbcb = DBtable_content_blobs("DEFAULT")
        fdata = dbcb.retrieveFileList('', "DataFile")
        fdatadic = {}
        for fi in fdata['rows']:
            aid = fi['asset_id']
            fdatadic[aid] = fi
            
        jdata_new = []
        for data in jdata:
            datadic = {}
            datadic['id'] = data['id']
            datadic['uid'] = data['title']
            datadic['title'] = data['title']
            weblink, weburl = self.__getWeblink(data['title'])
            datadic['fileurl'] = weblink
            if data['id'] in fdatadic:
                fi = fdatadic[data['id']]
                datadic['originalname'] = self.__getSeeklink(fi['original_filename'], data['id'])
                datadic['checksum'] = fi['md5sum']
                datadic['filesize'] = sizeof_fmt(fi['file_size'])
            
            jdata_new.append(datadic)
        
        return jdata_new
    
    def retrieveRecords(self, user_seek, filtersdic):
        jdata, footer, total = self.db.retrieve_table_list(self.tablemodel, self.primaryField, filtersdic, self.fieldMapping)
        jdata_new = self.reformatDataForClient(jdata)
        data = {'total':total,'rows':jdata_new,'footer':footer}
        return data
    
    
    def moveDF_toStorage(self, seekdb, pathtofile, filename, sample_uid, istest=False):
        originalfilename = filename
        report = {}
        if seekdb.user_seek is None:
            report['msg'] = 'Error: user not loged in.'
            report['status'] = 0
            dfrecord = {}
            dfrecord['uid'] = ''
            dfrecord['originalname'] = originalfilename
            dfrecord['notes'] = report['msg']
            report['newrow'] = dfrecord
            return report
        
        content_type = 'To be defined'
        df_id, dfrecord = self.__searchDatafile(seekdb.creator, originalfilename, sample_uid)
        dfrecord['content_type'] = content_type
        fullfilename = dfrecord['fullfilename']
        if df_id>0:
            report['msg'] = 'Warning: Data file already uploaded in Seek by same user'
            report['status'] = -1
            dfrecord['notes'] = report['msg']
            report['newrow'] = dfrecord
            return report
        
        filename_inqueue = os.path.join(pathtofile, filename)
        md5pre = None
        if istest:
            md5pre = getFileChecksum(filename_inqueue, 'MD5')
        
        cmdline = "cp " + filename_inqueue + " " + fullfilename
        try:
            call([cmdline], shell=True)
            status = True
        except:
            msg = "Error: " + cmdline
            status = False
        
        md5Now = None
        if istest:
            md5Now = getFileChecksum(fullfilename, 'MD5')
            if md5Now!=md5pre:
                msg = 'Error: File MD5 checksum not match: ' + md5Now
                status = 0
            else:
                msg = 'File uploaded to storage server'
                status = 1
        
        if istest:
            cmdline = "rm " + filename_inqueue
            try:
                call([cmdline], shell=True)
                status = True
            except:
                status = False
                msg = "Error: " + cmdline
        
        if status:
            report['msg'] = 'File uploaded to storage server'
            report['status'] = 1
        else:
            report['msg'] = msg
            report['status'] = 0
        dfrecord['notes'] = report['msg']
        report['newrow'] = dfrecord
        return report
    
    def getFile(self, user_seek, id):
        fullfilename = ''
        filename = ''
        status = 0
        msg = 'Error: file not found.'
        if id is None or int(id)<=0:
            return fullfilename, filename, status, msg
        
        record = self.getOneRecord(id)
        if record is None:
            return fullfilename, filename, status, msg
            
        contributor_id = record['contributor_id']
        
        seekdb = SeekDB(user_seek['server'], user_seek['username'], user_seek['password'])
        creator, status, msg = seekdb.getUserInfo(contributor_id)
        if not status:
            msg = "Error: " + msg
            return fullfilename, filename, status, msg
        
        lababbv, upload_full_path = self.__getUploadPath(creator)
        outfilename = record['title']
        filename = outfilename
        
        fullfilename = os.path.join(upload_full_path, outfilename)
        weblink = fullfilename
        weblink = weblink.replace(settings.SEEK_DATAFILE_ROOT, settings.SEEK_DATAFILE_ROOT_WEBLINK)
        if not os.path.isfile(fullfilename):
            msg = "Error: file not found:" + fullfilename
            status = 0
            return fullfilename, filename, status, msg
 
        if not os.path.exists(fullfilename):
            msg = "Error: file not found:" + fullfilename
            return fullfilename, filename, status, msg
        
        status = 1
        return fullfilename, filename, status, msg
    
    def download(self, user_seek, allids, downloadallterms):
        msg = "Error: Download not successful."
        status = 1
        link = " "
        
        datenow = str(datetime.datetime.now().strftime("%Y-%m-%d_%H"))
        filename = 'datafiles-' + datenow + '.zip'
        zipfilename = DOWNLOAD_DIRECTORY + filename
        link = DOWNLOAD_DIRECTORY_LINK + filename
        
        zf = zipfile.ZipFile(zipfilename, mode='w')
        msg = "Start generating zip file"
        try:
            msg = 'Error: '
            for id in allids:
                fullfilenamei, filenamei, statusi, msgi = self.getFile(user_seek, id)
                if statusi==0:
                    msg += msgi + '\n'
                    status = 0
                else:
                    zf.write(fullfilenamei, filenamei)
        finally:
            zf.close()
        
        return msg, status, link
    
    def publishDFs(self, user_seek, sdb, user, df_ids, assay_id, project_id):
        status = 1
        msg = ''
        diclist = []
        for df_id in df_ids:
            record_cb = {}
            record_cb['id'] = df_id
            record_cb['assay_id'] = assay_id
            record_cb['project_id'] = project_id
            record_cb['title'] = 'undefined'
            
            dfrecord = self.getOneRecord(df_id)
            if dfrecord is None:
                status = 0
                record_cb['status'] = 0
                record_cb['msg'] = 'Data file not found'
                msg += record_cb['msg'] + '<br/>'
                diclist.append(record_cb)
                continue
            
            record_cb['title'] = dfrecord['title']
            
            fullfilename, filename, statusi, msgi = self.getFile(user_seek, df_id)
            record_cb['fullfilename'] = fullfilename
            record_cb['filename'] = filename
            record_cb['status'] = statusi
            record_cb['msg'] = msgi
            if not statusi:
                status = 0
                msg += msgi + '<br/>'
                diclist.append(record_cb)
                continue
        
            datatitle = dfrecord['title']
            content_type = ''
            dbcb = DBtable_content_blobs("DEFAULT")
            diclist_blobs = dbcb.getRecord(df_id, 'DataFile')
            if diclist_blobs is not None:
                content_type = diclist_blobs[0]['content_type']
            record_cb['content_type'] = content_type
            
            userid = user['user_id']
            description = 'File published through API'
            tags = None
            
            msgi, statusi, df_info, datafile_url = sdb.seekupload(
                datatitle,
                fullfilename,
                filename,
                content_type,
                userid,
                project_id,
                assay_id,
                description,
                tags
            )
            record_cb['status'] = statusi
            record_cb['msg'] = msgi
            if statusi==0:
                status = 0
                msg += msgi + '<br/>'
            else:
                record_blob = df_info["attributes"]["content_blobs"][0]
                record_blob['url'] = weburl
                md5sum_published = record_blob['md5sum']
                
                import hashlib
                md5sum = hashlib.md5(open(fullfilename,'rb').read()).hexdigest()
                if md5sum_published!=md5sum:
                    status = 0
                    msgi = 'Error: File checksum on remote server ' + str(md5sum_published) + ' does not match the local record ' + md5sum
                    msg += msgi + '<br/>'
                    record_cb['status'] = 0
                    record_cb['msg'] = msgi
            
            diclist.append(record_cb)
        
        data = {}
        data['msg'] = msg
        data['status'] = status
        data['link'] = ''
        data['ptype'] = 'Datafile'
        data['diclist'] = diclist
        reportData = simplejson.dumps(data, default=str)
        return reportData
        
    def retrieveFileLinks(self, sampleDic):
        dbcb = DBtable_content_blobs("DEFAULT")
        
        sampleDic_rev = {}
        for key, value in sampleDic.items():
            sampleDic_rev[key] = value
            if "file_" in key:
                filename = value
                sampleDic_rev[key] = value
                sampleDic_rev[value] = ""
                qset = Q(title__icontains=filename)
                diclist = self.queryRecordsCustom(qset)
                if len(diclist)==1:
                    dici = diclist[0]
                    datafile_id = dici['id']
                    datafile_uid = dici['title']
                    msg, status, fileInfo = self.downloadDF_fromStorage(None, datafile_uid)
                    
                    dici['msg'] = msg
                    dici['status'] = status
                    dici.update(fileInfo)
                                
                    diclist_blobs = dbcb.getRecord(datafile_id, 'DataFile')
                    if diclist_blobs is not None:
                        content_blob = diclist_blobs[0]
                        dici.update(content_blob)
                    
                    sampleDic_rev[key] = dici
                    
                    if status==1:
                        sampleDic_rev[datafile_uid] = fileInfo['weblink']
                    
        return sampleDic_rev
        
    def apiUploadFile(self, user_seek, infile, sample_info):
        sample_uid = sample_info['sample_uid']
        user_seek['lababbv'] = sample_info['lababbv']
        user_seek['projectname'] = sample_info['projectname']
        user_seek['projectid'] = sample_info['project_id']
        seekdb = SeekDB(None, user_seek['username'], user_seek['password'])
        seekdb.user_seek = user_seek
        
        report = {}
        report['msg'] = 'Data file associated with sample: ' + sample_uid
        report['status'] = 1
        
        md5 = None
        report = self.uploadDF_toStorage(seekdb.user_seek, infile, sample_uid, md5)
        status = report['status']
        if status==0:
            return report
        
        originalfilename = infile.name
        content_type = infile.content_type
        dfrecord = report['newrow']
        df_uid = dfrecord['uid']
        weburl = dfrecord['fileurl']
        report = self.uploadDF_storageToSeek(seekdb, originalfilename, content_type, df_uid, weburl)
        return report
        
