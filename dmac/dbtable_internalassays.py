#!/usr/bin/env python
import os
import sys
import MySQLdb
import time, json

import logging
logger = logging.getLogger(__name__)

import pandas as pd
import numpy as np
import dmac.settings as settings

from seek.seekapi import SeekAPI
from seek.models import Assays, Internal_assays, Assays_internal_assays
from dmac.dbtable import DBtable

SEEK_DATABASE = settings.DATABASES[settings.SEEK_DATABASE]
DJANGO_DATABASE = settings.DATABASES['default']

class DBtable_internalassays(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'DJANGO', DJANGO_DATABASE['NAME'])
        
        self.tablename = 'internal_assays'
        self.tablemodel = Internal_assays
        self.fulltablename = self.tablemodel
        self.viewtablename = self.dbname + '.' + self.tablename
        self.fields = [
            'id',
            'internal_assay_title',
        ]
        
        self.uniqueFields = []
        self.primaryField = "id"
        self.fieldMapping = {}
 
    def __sendQuery(self, query):
        conn = MySQLdb.connect(host=SEEK_DATABASE['HOST'],
                               user=SEEK_DATABASE['USER'],
                               passwd=SEEK_DATABASE['PASSWORD'],
                               db=SEEK_DATABASE['NAME'])
        cursor = conn.cursor()
        cursor.execute(query)
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        data = pd.DataFrame(rows, columns=columns).replace({np.nan: None}).to_dict(orient="records")
        cursor.close()
        conn.close()

        return data

    def new(self, internal_assay_title):
        internal_assay = self.tablemodel(internal_assay_title=internal_assay_title)
        internal_assay.save()

    def update(self, internal_assay_id, internal_assay_title):
        internal_assay = self.tablemodel.objects.get(id=internal_assay_id)
        internal_assay.internal_assay_title = internal_assay_title

        internal_assay.save()

    def delete(self, internal_assay_id):
        internal_assay = self.tablemodel.objects.get(id=internal_assay_id)
        internal_assay.delete()
        
    def getAll(self):
        return self.tablemodel.objects.all().values()
