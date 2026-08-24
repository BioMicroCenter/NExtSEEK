#!/usr/bin/env python
import logging
logger = logging.getLogger(__name__)

from .models import Content_blobs
from dmac.dbtable import DBtable

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
    
