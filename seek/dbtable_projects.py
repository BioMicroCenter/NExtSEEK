#!/usr/bin/env python
import os
import sys
import time, json

import logging
logger = logging.getLogger(__name__)

from seek.seekapi import SeekAPI
from seek.models import Projects, Projects_sops, Data_files_projects, Projects_samples
from dmac.dbtable import DBtable

class DBtable_projects(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'SEEK', 'seek_development')
        
        self.tablename = 'projects'
        self.tablemodel = Projects
        self.fulltablename = self.tablemodel
        self.viewtablename = self.dbname + '.' + self.tablename
        self.fields = [
            'id',
            'title',
            'web_page',
            'wiki_page',
            'created_at',
            'updated_at',
            'description',
            'avatar_id',
            'default_policy_id',
            'first_letter',
            'site_credentials',
            'site_root_uri',
            'last_jerm_run',
            'uuid',
            'programme_id',
            'default_license',
            'use_default_policy',
            'start_date',
            'end_date'
        ]
        
        self.uniqueFields = []
        self.primaryField = "id"
        self.fieldMapping = {}

    def sample_count(self, project_id):
        sample_count = len(Projects_samples.objects.filter(project_id=project_id))
        return {'sample_count': sample_count}

    def files_count(self, project_id):
        sop_count = len(Projects_sops.objects.filter(project_id=project_id))
        df_count = len(Data_files_projects.objects.filter(project_id=project_id))
        return {'sop_count': sop_count, 'df_count': df_count}
