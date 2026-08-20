#!/usr/bin/env python
import logging
logger = logging.getLogger(__name__)

from .models import Policies
from dmac.dbtable import DBtable
from dmac.conversion import getDefaultDateTime

from .dbtable_permissions import DBtable_permissions
POLICIES_FILTER_MAPPING = {
}

POLICIES_DEFAULT = {
    #'id':'',
    'name':'default policy',
    #'sharing_scope':None,
    'access_type':0,
    #'use_whitelist':None,
    #'use_blacklist':None,
    'created_at':getDefaultDateTime(),
    'updated_at':getDefaultDateTime()
}

class DBtable_policies(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'SEEK', 'seek_development')
        self.tablename = 'policies'
        self.tablemodel = Policies
        self.fulltablename = self.tablemodel
        self.viewtablename = self.dbname + '.' + self.tablename
        self.fields = [
            'id',
            'name',
            'sharing_scope',
            'access_type',
            'use_allowlist',
            'use_denylist',
            'created_at',
            'updated_at'
        ]
        self.default = POLICIES_DEFAULT
        self.uniqueFields = []
        self.primaryField = "id"
        self.fieldMapping = POLICIES_FILTER_MAPPING
        
    def createDefaultPolicy(self,username, user_id, project_id):
        record = {}
        for key, value in self.default.items():
            record[key] = value
        
        msg, status, policy_id = self.storeOneRecord(username, record)
        if policy_id>0:
            permission = DBtable_permissions("DEFAULT")
            msg, status, permission_id = permission.createDefaultPermission(username, policy_id, project_id)
        else:
            policy_id = -1
        
        return msg, status, policy_id
        
        
        
        
