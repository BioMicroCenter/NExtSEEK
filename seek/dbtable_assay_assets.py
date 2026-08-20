#!/usr/bin/env python
import logging
logger = logging.getLogger(__name__)

from .models import Assay_assets
from dmac.dbtable import DBtable
from dmac.conversion import getDefaultDateTime

ASSAY_ASSETS_FILTER_MAPPING = {
}

ASSAY_ASSETS_DEFAULT = {
    #'id':'',
    'version': 1,
    'created_at':'',
    'updated_at':'',
    'relationship_type_id':None,
    'direction':0
}

class DBtable_assay_assets(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'SEEK', 'seek_development')
        self.tablename = 'assay_assets'
        self.tablemodel = Assay_assets
        self.fulltablename = self.tablemodel
        self.viewtablename = self.dbname + '.' + self.tablename
        self.fields = [
            'id',
            'assay_id',
            'asset_id',
            'version',
            'created_at',
            'updated_at',
            'relationship_type_id',
            'asset_type',
            'direction'
        ]
        self.uniqueFields = ['assay_id', 'asset_id', 'asset_type']
        self.primaryField = "id"
        self.fieldMapping = ASSAY_ASSETS_FILTER_MAPPING
        self.excludeFields = []
        
    def storeSample_assay_asset(self, user_seek, sampleType, sample_id, diclist_assay):
        username = user_seek['username']
        record_new = {}
        for field in self.fields:
            value = ''
            if field in ASSAY_ASSETS_DEFAULT:
                value = ASSAY_ASSETS_DEFAULT[field]
            record_new[field] = value
        
        record_new['asset_id'] = sample_id
        record_new['created_at'] = getDefaultDateTime()
        record_new['updated_at'] = getDefaultDateTime()
        record_new['asset_type'] = 'Sample'
        
        msg = ''
        status = 1
        for dici in diclist_assay:
            smapletypei = dici['SampleType']
            try:
                assay_id = int(dici['Assay'])
            except:
                msg += "Warning: Assay not defined in the Assay sheet\n"
                logger.debug("Warning: Assay id not defined in the Assay sheet: "+dici['Assay'])
                status = 0
                continue
                
            direction = int(dici['Direction'])
            if smapletypei==sampleType:
                record_new['assay_id'] = assay_id
                record_new['direction'] = direction
                msgi, statusi, id = self.storeOneRecord(username, record_new)
                if not statusi:
                    msg += msgi + '\n'
                    status = 0
        return msg, status
        
    def storeDatafile_assay_asset(self, user_seek, datafile_id, sampleType, diclist_assay):
        username = user_seek['username']
        record_new = {}
        for field in self.fields:
            value = ''
            if field in ASSAY_ASSETS_DEFAULT:
                value = ASSAY_ASSETS_DEFAULT[field]
            record_new[field] = value
        
        record_new['asset_id'] = datafile_id
        record_new['created_at'] = getDefaultDateTime()
        record_new['updated_at'] = getDefaultDateTime()
        record_new['asset_type'] = 'DataFile'
        
        msg = ''
        status = 1
        for dici in diclist_assay:
            assay_id = int(dici['Assay'])
            sampleTypeNow = dici['SampleType']
            record_new['assay_id'] = assay_id
            record_new['direction'] = 0
            if sampleTypeNow==sampleType:
                msgi, statusi, id = self.storeOneRecord(username, record_new)
                if not statusi:
                    msg += msgi + '\n'
                    status = 0
        return msg, status
    
    
    def updateSample_assay_asset(self, user_seek, sample_id, dici):
        username = user_seek['username']
        msg = ''
        status = 1
        
        id = 0
        try:
            assay_id_old = int(dici['Current Assay ID'])
        except:
            id = -1
        
        try:
            direction_old = int(dici['Current Assay Direction'])
        except:
            id = -1
            
        if id==0:
            record_old = {}
            record_old['asset_id'] = sample_id
            record_old['assay_id'] = assay_id_old
            record_old['direction'] = direction_old
            record_old['asset_type'] = 'Sample'
            id = self.queryPrimaryKeyByConstraint(record_old)
            
        record_new = {}
        for field in self.fields:
            value = ''
            if field in ASSAY_ASSETS_DEFAULT:
                value = ASSAY_ASSETS_DEFAULT[field]
            record_new[field] = value
        
        record_new['asset_id'] = sample_id
        record_new['created_at'] = getDefaultDateTime()
        record_new['updated_at'] = getDefaultDateTime()
        record_new['asset_type'] = 'Sample'
        
        addnew = True
        try:
            assay_id = int(dici['New Assay ID'])
        except:
            addnew = False
            
        try:    
            direction = int(dici['New Assay Direction'])
        except:
            addnew = False
       
        if addnew:
            record_new['assay_id'] = assay_id
            record_new['direction'] = direction
            if id>0:
                record_new['id'] = id
            msg, status, id = self.storeOneRecord(username, record_new)
        else:
            if id>0:
                record = {}
                record['id'] = id
                msg, status = self.deleteOneRecord(record)
            else:
                msg = 'Warning: Nothing changed'
                status = 1
            
        return msg, status