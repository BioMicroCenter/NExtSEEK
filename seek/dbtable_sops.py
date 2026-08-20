#!/usr/bin/env python
import os
import datetime
import simplejson
import zipfile
import logging
logger = logging.getLogger(__name__)

from .models import Sops
from .models import Projects
from .seekdb import SeekDB

from dmac.dbtable import DBtable
from dmac.iocsv import saveCsvfile
from dmac.conversion import handle_uploaded_file, correctFileName, sizeof_fmt, getFileChecksum

from django.conf import settings
from django import forms
from .dbtable_assay_assets import DBtable_assay_assets
from .dbtable_content_blobs import DBtable_content_blobs

DOWNLOAD_DIRECTORY  = settings.MEDIA_ROOT + "/download/"
DOWNLOAD_DIRECTORY_LINK = settings.MEDIA_URL + '/download/'

SOPS_FILTER_MAPPING = {
    'id':'id',
    'title':'uid'
}

SOPS_DEFAULT = {
    #'id':'',
    'contributor_id':0,
    'title':'',
    'description':'',
    'created_at':'',
    'updated_at':'',
    'version':1,
    'first_letter':'',
    'other_creators':'',
    'uuid':'',
    'policy_id':'',
    'doi':None,
    'license':'CC-BY-4.0',
    'deleted_contributor':None         
}

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

FILETYPES_SOP_SUPPORTED = [
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/msword",
    "application/zip",
]

SOP_ERRORCODE = {
    '001': 'Error P001: User not logged in.',
    '101': 'Warning P101: File already uploaded in Seek thus no update.',
    '102': 'Warning P102: Not one of supported pdf, Word, Excel or txt files: ',
    '201': 'Error P201: File UID not in right format: ',
    '202': 'Error P202: Failed in searching for SOP in Seek: ',
    '203': 'Error P203: File uploading into content_blob table failed: ',
    '204': 'Warning P204: File and sample association not saved correctly into DB: ',
    '205': 'Warning P205: File already uploaded in Seek, forced update: ',
    '301': 'Error: SOP file UID not in right format: ',
    '302': 'Error: SOP file UID not found in DB: ',
    '303': 'Error: More than one SOP file found for the UID: ',
    '304': 'Error: User info not found in DB: ',
    '305': 'Error: File not found on server as a file: ',
    '306': 'Error: File not found on server: '
}  

SOP_FILE_UID_DELIMITER = "_"

class BatchSearchForm(forms.Form):
    keywords = forms.CharField(required=True, label='Keywords', widget=forms.Textarea )
    category = forms.ChoiceField(required=True, label="Category", initial='', choices=CATEGORY_CHOICES, widget=forms.Select())

class DBtable_sops(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'SEEK', 'seek_development')
        
        self.tablename = 'sops'
        self.tablemodel = Sops
        self.fulltablename = self.tablemodel
        self.viewtablename = self.dbname + '.' + self.tablename
        self.fields = [
            'id',
            'contributor_id',
            'title',
            'description',
            'created_at',
            'updated_at',
            'version',
            'first_letter',
            'other_creators',
            'uuid',
            'policy_id',
            'doi',
            'license',
            'deleted_contributor'
        ]
        
        self.uniqueFields = ['title', 'version']
        self.primaryField = "id"
        self.fieldMapping = SOPS_FILTER_MAPPING
        self.excludeFields = []
        
        report = {}
        self.form = BatchSearchForm(report)
        self.formDefault = BATCHSEARCHFORM_DEFAULT
        self.formMapping = BATCHSEARCHFORM_MAPPING
     
    def __getUID(self, lababbv, filename, nassets=0):
        uid_date = str(datetime.datetime.now().strftime("%Y%m%d"))  # such as '20200323'
        uid_date_2 = uid_date[2:]    # such as # such as '200323'
        next_version = nassets + 1
        
        uid = 'P.' + lababbv + '-' + uid_date_2 + '-V' + str(next_version) + SOP_FILE_UID_DELIMITER + filename
        return uid

    def __defineUploadFilename(self, username, infilename, uid):
        outfilename = uid
        return outfilename

    def __getWeblink(self, uid):
        url = "/seek/sop/uid=" + uid + "/"
        weburl = settings.SEEK_DATAFILE_SERVER + url
        weblink = '<a href="' + url + '" target="_blank">' + weburl + '</a>'
        return weblink
    
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
    
    def __defineUploadPath(self, creator, originalfilename, nassets=0):
        infilename_corrected = correctFileName(originalfilename)
        username = None
        lababbv, upload_full_path = self.__getUploadPath(creator)
        pid = 1
        uid = self.__getUID(lababbv, infilename_corrected, nassets)
        outfilename = self.__defineUploadFilename(username, infilename_corrected, uid)
            
        weblink = self.__getWeblink(uid)
        dfrecord = {}
        dfrecord['id'] = pid
        dfrecord['uid'] = uid
        dfrecord['originalname'] = originalfilename
        dfrecord['fileurl'] = weblink
        dfrecord['notes'] = ''
        dfrecord['outfilename'] = outfilename
        dfrecord['upload_full_path'] = upload_full_path
        
        fullfilename = os.path.join(upload_full_path, outfilename)
        dfrecord['fullfilename'] = fullfilename
        return dfrecord
        
    def __getSeeklink(self, originalfilename, sop_id):
        seek_url = settings.SEEK_PUBLIC_URL + "/sops/" + str(sop_id)
        seeklink = '<a href="' + seek_url + '" target="_blank">' + originalfilename + '</a>'
        return seeklink

    
    def __defineSOP(self, creator, originalfilename):
        dbcb = DBtable_content_blobs("DEFAULT")
        asset_id, asset_type, asset_version, nassets = dbcb.searchFile(originalfilename, 'Sop')
        
        dfrecord = self.__defineUploadPath(creator, originalfilename, nassets)
        return asset_id, asset_type, dfrecord
    
    
    def __searchSOP(self, creator, originalfilename):
        asset_id, asset_type, dfrecord = self.__defineSOP(creator, originalfilename)
        if asset_id is not None and asset_type=='Sop':
            record = self.getOneRecord(asset_id)
            if record is None or 'contributor_id' not in record:
                return 0, dfrecord
            
            contributor_id = record['contributor_id']
            if contributor_id is not None and int(contributor_id)==creator['user_id']:
                dfrecord['id'] = asset_id
                dfrecord['uid'] = record['title']
                dfrecord['originalname'] = originalfilename
                dfrecord['originalname_url'] = self.__getSeeklink(originalfilename, asset_id)
                dfrecord['notes'] = 'Warning: File already uploaded in Seek'
                dfrecord['fileurl'] = self.__getWeblink(record['title'])
                return asset_id, dfrecord
            
        return 0, dfrecord
                
    def uploadSOP_toStorage(self, creator, infile, md5In):
        originalfilename = infile.name
        report = {}
        if creator is None:
            report['msg'] = SOP_ERRORCODE['001']
            report['status'] = 0
            dfrecord = {}
            dfrecord['uid'] = ''
            dfrecord['originalname'] = originalfilename
            dfrecord['notes'] = report['msg']
            report['newrow'] = dfrecord
            return report
        
        content_type = infile.content_type
        if content_type not in FILETYPES_SOP_SUPPORTED:
            report['msg'] = SOP_ERRORCODE['102'] + content_type
            report['status'] = 0
            dfrecord = {}
            dfrecord['uid'] = ''
            dfrecord['originalname'] = originalfilename
            dfrecord['fileurl'] = 'Not available'
            dfrecord['notes'] = report['msg']
            dfrecord['content_type'] = content_type
            report['newrow'] = dfrecord
            return report
               
        df_id, dfrecord = self.__searchSOP(creator, originalfilename)
        dfrecord['content_type'] = content_type
        
        fullfilename = dfrecord['fullfilename']
        handle_uploaded_file(infile, fullfilename)
        
        if df_id>0:
            report['msg'] = SOP_ERRORCODE['101']
            report['status'] = -1
            dfrecord['notes'] = report['msg']
            report['newrow'] = dfrecord
            return report
        
        md5Now = getFileChecksum(fullfilename, 'MD5')
        logger.debug(f"MD5 of {fullfilename} is {md5Now}")
        if md5Now!=md5In:
            msg = 'Error: File MD5 checksum not match: ' + md5Now
            logger.debug(msg)
            status = 0
        else:
            msg = 'File uploaded to storage server'
            logger.debug(msg)
            status = 1
        
        report['md5'] = md5Now
        report['msg'] = msg
        report['status'] = status
        dfrecord['notes'] = report['msg']
        report['newrow'] = dfrecord
        return report

    def __getIDfromUID(self, sopUID):
        constraint = {}
        constraint['title'] = sopUID
        diclist_cb = self.queryRecordsByConstraint(constraint)
        nrecords = len(diclist_cb)
        if nrecords==1:
            dici = diclist_cb[0]
            id = dici['id']
        else:
            id = None
            
        return id

    def uploadSOP_storageToSeek(self, seekdb, originalfilename, content_type):
        creator = seekdb.creator
        submitter = seekdb.user_seek['username']
        
        report = {}
        df_id, asset_type, dfrecord = self.__defineSOP(creator, originalfilename)
        if df_id is not None:
            if int(df_id)>0:
                report['msg'] = SOP_ERRORCODE['205'] + originalfilename
                report['status'] = 0
                dfrecord['notes'] = report['msg']
                report['newrow'] = dfrecord
            
        userid = creator['user_id']
        project_id = creator['projectid']
        assay_id = None
        tags = None
        dfrecord['content_type'] = content_type
    
        datatitle = dfrecord['uid']
        fullfilename = dfrecord['fullfilename']
        description = 'File uploaded from DropZone'
        msg, status, df_info, datafile_url = seekdb.seekuploadSOP(
            datatitle,
            fullfilename,
            originalfilename,
            content_type,
            userid,
            project_id,
            assay_id,
            description,
            tags,
            submitter
        )
        if status==1:
            df_id = self.__getIDfromUID(datatitle)
            if df_id>0:
                dfrecord['originalname'] = originalfilename
                dfrecord['id'] = self.__getSeeklink(originalfilename, df_id)
                dfrecord['notes'] = 'Successful'
            else:
                msg = SOP_ERRORCODE['202'] + datatitle
                dfrecord['notes'] = msg
                
        else:
            dfrecord['id'] = ''
            dfrecord['uid'] = ''
            msg = SOP_ERRORCODE['203'] + msg
            dfrecord['notes'] = msg
    
        report['msg'] = msg
        report['status'] = status
        report['df_info'] = df_info
        report['newrow'] = dfrecord
        return report                    
                
    def uploadSOPs_storageToSeek(self, seekdb, diclist):
        report = {}
        report['msg'] = 'test uploadSOPs_storageToSeek()'
        report['status'] = 0
        report['df_info'] = ''
        report['newrows'] = []
        
        newrows = []
        status = 1
        msg = ''
        for dici in diclist:
            originalfilename = dici['originalname']
            content_type = dici['content_type']
            reporti = self.uploadSOP_storageToSeek(seekdb, originalfilename, content_type)
            newrows.append(reporti['newrow'])
            if reporti['status']==0:
                status = 0
                msg += reporti['msg'] + '\n'
        
        report['msg'] = msg
        report['status'] = status
        report['df_info'] = ''
        report['newrows'] = newrows
        return report 
        
    def uploadSOP_intoSeek_retired(self, seekdb, infile):
        originalfilename = infile.name
        content_type = infile.content_type
        report = {}
        if seekdb.user_seek is None:
            report['msg'] = 'Error: user not loged in.'
            report['status'] = 0
            dfrecord = {}
            dfrecord['originalname'] = originalfilename
            dfrecord['notes'] = report['msg']
            report['newrow'] = dfrecord
            return report
                
        df_id, dfrecord = self.__searchSOP(seekdb.creator, originalfilename)
        if df_id>0:
            report['msg'] = 'Warning: file already uploaded in Seek, no update: ' + infile.name
            report['status'] = 0
            dfrecord['notes'] = report['msg']
            report['newrow'] = dfrecord
            return report
        
        content_type = infile.content_type
        if content_type not in FILETYPES_SOP_SUPPORTED:
            report['msg'] = 'Warning: not one of supported pdf, Word, Excel or txt files: ' + content_type
            report['status'] = 0
            dfrecord['uid'] = ''
            dfrecord['fileurl'] = 'Not available'
            dfrecord['notes'] = report['msg']
            dfrecord['filetypeid'] = content_type
            report['newrow'] = dfrecord
            return report

        dfrecord['filetypeid'] = content_type
        fullfilename = dfrecord['fullfilename']
        handle_uploaded_file(infile, fullfilename)
                
        userid = seekdb.user_seek['user_id']
        project_id = seekdb.user_seek['projectid']
        assay_id = None
        tags = None
        
        dfrecord['content_type'] = content_type
    
        datatitle = dfrecord['outfilename']
        description = 'File uploaded from DropZone'
        msg, status, df_info, datafile_url = seekdb.seekuploadSOP(
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
            df_id, dfrecord = self.__searchSOP(seekdb.creator, originalfilename)
            if df_id>0:
                dfrecord['notes'] = 'Successful'
            else:
                dfrecord['notes'] = 'Error: failed in searching for SOP in Seek.'
        else:
            dfrecord['id'] = ''
            dfrecord['uid'] = ''
            dfrecord['notes'] = 'Error: failed in uploading into Seek, available on server.'
    
        report['msg'] = msg
        report['status'] = status
        report['df_info'] = df_info
        report['newrow'] = dfrecord
        return report    
        
    def downloadSOP_fromSeek(self, user_seek, uid):
        msg = 'Waening: File to be found on server: ' + uid
        status = 0
        weblink = None
        
        lababbv = user_seek['lababbv']
        
        labfolder = lababbv
            
        upload_full_path_labroot = os.path.join(settings.SEEK_DATAFILE_ROOT, labfolder)
        if not os.path.exists(upload_full_path_labroot):
            msg = 'Error: File not found on server: ' + uid
            return msg, status, weblink
        
        return msg, status, weblink
    
    def __getUploadPathByUID(self, uid):
        fileInfo = {
            'uid':'',               
            'prefix':'',            
            'originalfilename':'',  
            'lababbv':'',           
            
            'upload_full_path':'',  
            'fullfilename':'',      
        }
        fileInfo['uid'] = uid   
        if SOP_FILE_UID_DELIMITER not in uid:
            return fileInfo
        
        terms = uid.split(SOP_FILE_UID_DELIMITER)
        prefix = terms[0]    
        fileInfo['prefix'] = prefix
        
        terms = terms[1:]
        originalfilename = SOP_FILE_UID_DELIMITER.join(terms) 
        fileInfo['originalfilename'] = originalfilename
        
        if '-' not in prefix:
            return fileInfo
        
        terms = prefix.split("-")
        prefix0 = terms[0]   
        dateabbr = terms[1]     
        if len(dateabbr)!=6:
            return fileInfo
        
        lababbv = prefix0[-3:] 
        fileInfo['lababbv'] = lababbv
        
        labfolder = lababbv
        upload_full_path = ''
        fullfilename = ''
        
        p = Projects()
        projects = p.getProjects()
        
        for projectfolder in projects:
            fileroot = settings.SEEK_DATAFILE_ROOT
            upload_full_path_projectroot = os.path.join(fileroot, projectfolder)
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
        
    def downloadSOP_fromStorage(self, user_seek, uid):
        msg = 'Warning: File to be found on server: ' + uid
        status = 0
        weblink = None
        fileInfo = {}
        if SOP_FILE_UID_DELIMITER not in uid:
            msg = SOP_ERRORCODE['301'] + uid
            logger.debug(msg)
            return msg, status, fileInfo
                
        constraint = {"title":uid}
        diclist = self.queryRecordsByConstraint(constraint)
        if diclist is None or len(diclist)==0:
            msg = SOP_ERRORCODE['302'] + uid
            return msg, status, fileInfo
        elif len(diclist)>1:
            msg = SOP_ERRORCODE['303'] + uid
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
                logger.debug(msg)
                return msg, status, fileInfo
        
            username = user_seek['username']
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
            msg = SOP_ERRORCODE['305'] + fullfilename
            logger.debug(msg)
            fileInfo['weblink'] = ''
            return msg, status, fileInfo    
        
        weblink = fullfilename
        weblink = weblink.replace(settings.SEEK_DATAFILE_ROOT, settings.SEEK_DATAFILE_ROOT_WEBLINK)
        weblink = settings.SEEK_DATAFILE_SERVER + weblink
        fileInfo['weblink'] = weblink
        
        if not os.path.isfile(fullfilename):
            msg = SOP_ERRORCODE['305'] + fullfilename
            logger.debug(msg)
            return msg, status, fileInfo
 
        if not os.path.exists(fullfilename):
            msg = SOP_ERRORCODE['306'] + fullfilename
            logger.debug(msg)
            return msg, status, fileInfo
        
        status = 1
        msg = fullfilename
        return msg, status, fileInfo


###################  below are to be modified
    
    def __getDatafileUrl(self, filename):
        link = settings.SEEK_DATAFILE_ROOT_WEBLINK + filename
        url = link
        return url
        
    def filesGetUIDs(self, seekdb, allFiles):
        diclist = []
        index = 0
        for originalfilename in allFiles:
            index += 1
            dici = self.__defineUploadPath(seekdb.user_seek, originalfilename)
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
            logger.debug(msg)
            return msg, 0, None
                
        if 'UID' not in record.keys():
            msg = 'Error: Sample record does not have a UID field.'
            logger.debug(msg)
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
        filename = terms[-1]    
        ids = filename.split("_")   
        uid = ids[0]            
        pids = uid.split('-')
        pid = pids[-1] 
        try:
            id = int(pid)    
        except:
            id = -1
        return id
    
    def processSampleDatafile(self, user_seek, sampleType, dfurl, diclist_assay):
        msg = 'To be implemented!'
        status = 0
        dfurl_prefix = settings.SEEK_URL + "/sops/"
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
        fdata = dbcb.retrieveFileList('', "Sop")
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
            datadic['fileurl'] = self.__getWeblink(data['title'])
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
            logger.debug(msg)
            status = 0
            return fullfilename, filename, status, msg
 
        if not os.path.exists(fullfilename):
            msg = "Error: file not found:" + fullfilename
            logger.debug(msg)
            return fullfilename, filename, status, msg
        
        status = 2
        return fullfilename, filename, status, msg
    
    def download(self, user_seek, allids, downloadallterms):
        msg = "Error: Download not successful."
        link = " "
        status = 1
        
        datenow = str(datetime.datetime.now().strftime("%Y-%m-%d_%H"))
        filename = 'sops-' + datenow + '.zip'
        zipfilename = DOWNLOAD_DIRECTORY + filename
        link = DOWNLOAD_DIRECTORY_LINK + filename
        
        zf = zipfile.ZipFile(zipfilename, mode='w')
        msg = "Start generating zip file"
        try:
            msg = ''
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
    
    
    def publishSOPs(self, user_seek, sdb, user, sop_ids, assay_id, project_id):
        status = 1
        msg = ''
        diclist = []
        for sop_id in sop_ids:
            record_cb = {}
            record_cb['id'] = sop_id
            record_cb['assay_id'] = assay_id
            record_cb['project_id'] = project_id
            record_cb['title'] = 'undefined'
            
            dfrecord = self.getOneRecord(sop_id)
            if dfrecord is None:
                status = 0
                record_cb['status'] = 0
                record_cb['msg'] = 'Protocol file not found'
                msg += record_cb['msg'] + '<br/>'
                diclist.append(record_cb)
                continue
            
            record_cb['title'] = dfrecord['title']
            
            fullfilename, filename, statusi, msgi = self.getFile(user_seek, sop_id)
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
            diclist_blobs = dbcb.getRecord(sop_id, 'Sop')
            if diclist_blobs is not None:
                content_type = diclist_blobs[0]['content_type']
            record_cb['content_type'] = content_type
            userid = user['user_id']
            description = 'File published through API'
            tags = None
            
            msgi, statusi, sop_info, datafile_url = sdb.seekuploadSOP(
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
                record_blob = sop_info["attributes"]["content_blobs"][0]
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
        data['ptype'] = 'Sop'
        data['diclist'] = diclist
        reportData = simplejson.dumps(data, default=str)
        return reportData
        
                
        
