#!/usr/bin/env python
import os
import logging
logger = logging.getLogger(__name__)

from .models import Sops
from .models import Projects
from .seekdb import SeekDB

from dmac.dbtable import DBtable

from django.conf import settings
from django import forms

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
    
