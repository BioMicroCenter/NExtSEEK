#!/usr/bin/env python
import logging
logger = logging.getLogger(__name__)

from .models import Content_blobs
from dmac.dbtable import DBtable
from dmac.conversion import verifyFileChecksum

CONTENT_BLOBS_FILTER_MAPPING = {
}

CONTENT_BLOBS_DEFAULT = {
    #'id':'',
    'md5sum':'',
    'url':None,
    'uuid':'',
    'original_filename':'',
    'content_type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'asset_id':0,
    'asset_type':'DataFile',
    'asset_version':1,
    'is_webpage':0,
    'external_link':0,
    'sha1sum':'',
    'file_size':0,
    'created_at':'',
    'updated_at':''
}

class DBtable_content_blobs(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'SEEK', 'seek_development')
        self.tablename = 'content_blobs'
        self.tablemodel = Content_blobs
        self.fulltablename = self.tablemodel
        self.viewtablename = self.tablemodel
        self.fields = [
            'id',
            'md5sum',
            'url',
            'uuid',
            'original_filename',
            'content_type',
            'asset_id',
            'asset_type',
            'asset_version',
            'is_webpage',
            'external_link',
            'sha1sum',
            'file_size',
            'created_at',
            'updated_at'
            'deleted',
        ]
        
        self.uniqueFields = ['original_filename']
        self.primaryField = "id"
        self.fieldMapping = CONTENT_BLOBS_FILTER_MAPPING
        self.excludeFields = []
        
    def storeDataFile(self, username, sampleType, record, attributeInfo, uploadEnforced=False):
        if not self.__notEmptyLine(record):
            msg = 'Error: record for uploading empty in ' + sampleType
            logger.debug(msg)
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
    
    def searchFile(self, infilename, asset_typeIn=None):
        constraint = {}
        constraint['original_filename'] = infilename
        if asset_typeIn is not None:
            constraint['asset_type'] = asset_typeIn
        diclist_cb = self.queryRecordsByConstraint(constraint)
        
        asset_id = None
        asset_type = None
        asset_version = None
        nassets = len(diclist_cb)
        if nassets==1:
            logger.debug("unqiue record found in content_blobs table")
            dici = diclist_cb[0]
            asset_id = dici['asset_id']
            asset_type = dici['asset_type']
            asset_version = dici['asset_version']
        elif nassets>1:
            logger.debug("multiple records found, choose the one with the highest version")
            version_max = -1
            for dici in diclist_cb:
                version_i = dici['asset_version']
                if version_i is None:
                    version_i = 0
                else:
                    version_i = int(version_i)
                    
                if version_i > version_max:
                    asset_id = dici['asset_id']
                    asset_type = dici['asset_type']
                    asset_version = version_i
        else:
            logger.debug("file not found in content blob")
            asset_id = None
            asset_type = None
            asset_version = None
        return asset_id, asset_type, asset_version, nassets
    
    def retrieveFileList(self, username, asset_type):
        filtersdic = {}
        filtersdic['orderby'] = ''
        filtersdic['limit'] = ''
        filtersdic['suffix'] = ''
        filtersdic['startNo'] = 0
        filtersdic['endNo'] = 0
    
        filterRules = [{"field":"asset_type","op":"contains","value":asset_type}]
        if asset_type in ["Document", "SampleType", "DataFile", "Sop"]:
            sqlquery_filter = " asset_type='" + asset_type + "';"
        else:
            sqlquery_filter = " "
        
        filtersdic['sqlquery_filter'] = sqlquery_filter
        filtersdic['filterRules'] = filterRules
        
        data = self.retrieveRecords(username, filtersdic)
        return data
    
    def getRecord(self, asset_id, asset_typeIn):
        constraint = {}
        constraint['asset_id'] = asset_id
        constraint['asset_type'] = asset_typeIn
        diclist_cb = self.queryRecordsByConstraint(constraint)
        return diclist_cb
    
    def updateFileChecksum(self, asset_id, asset_typeIn, fullfilename):
        diclist_cb = self.getRecord(asset_id, asset_typeIn)
        if len(diclist_cb)!=1:
            msg = "Warning: no unique record found in Content_blobs table for asset_id=%s, asset_type=%s"%(asset_id, asset_typeIn)
            status = 0
            pid = 0
            return msg, status, pid
        
        record_cb = ddiclist_cb[0]
        
        md5, sha1, filesize = verifyFileChecksum(fullfilename)
        record_cb['md5sum'] = md5
        record_cb['sha1sum'] = sha1
        record_cb['size'] = filesize
        
        msg, status, cb_id = self.storeOneRecord(None, record_cb)
        return msg, status, cb_id
    
