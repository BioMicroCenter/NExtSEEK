#!/usr/bin/env python
import logging
logger = logging.getLogger(__name__)

from .models import Permissions
from dmac.dbtable import DBtable
from dmac.conversion import getDefaultDateTime


PERMISSIONS_FILTER_MAPPING = {
}

PERMISSIONS_DEFAULT = {
    #'id':'',
    'contributor_type':'Project',
    'contributor_id':None,
    'policy_id':None,
    'access_type':4,    
    'created_at':getDefaultDateTime(),
    'updated_at':getDefaultDateTime()
}

class DBtable_permissions(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'SEEK', 'seek_development')
        self.tablename = 'permissions'
        self.tablemodel = Permissions
        self.fulltablename = self.tablemodel
        self.viewtablename = self.dbname + '.' + self.tablename
        self.fields = [
            'id',
            'contributor_type',
            'contributor_id',
            'policy_id',
            'access_type',    
            'created_at',
            'updated_at'
        ]
        self.default = PERMISSIONS_DEFAULT
        self.uniqueFields = []
        self.primaryField = "id"
        self.fieldMapping = PERMISSIONS_FILTER_MAPPING
        
    def createDefaultPermission(self, username, policy_id, project_id):
        record = {}
        for key, value in self.default.items():
            record[key] = value
        
        record['contributor_id'] = project_id
        record['policy_id'] = policy_id
        msg, status, primarykey = self.storeOneRecord(username, record)
        return msg, status, primarykey
        
        