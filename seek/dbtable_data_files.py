#!/usr/bin/env python
import os
import logging
logger = logging.getLogger(__name__)

from .seekdb import SeekDB
from .models import Data_files
from .models import Projects
from dmac.dbtable import DBtable

from django.conf import settings
from django import forms
from django.db.models import Q

from .dbtable_assay_assets import DBtable_assay_assets
from .dbtable_content_blobs import DBtable_content_blobs

DATA_FILES_FILTER_MAPPING = {
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
    
    def __defineUploadFilename(self, username, infilename, uid):
        outfilename = uid
        return outfilename
    
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
        