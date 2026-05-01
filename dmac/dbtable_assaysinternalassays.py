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

class DBtable_assaysinternalassays(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'DJANGO', DJANGO_DATABASE['NAME'])
        
        self.tablename = 'assays_internal_assays'
        self.tablemodel = Assays_internal_assays
        self.fulltablename = self.tablemodel
        self.viewtablename = self.dbname + '.' + self.tablename
        self.fields = [
            'id',
            'internal_assay_id',
            'assay_id'
        ]
        
        self.uniqueFields = []
        self.primaryField = "id"
        self.fieldMapping = {}
    
    def getAll(self):
        return self.tablemodel.objects.all().values()
 
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

    def getAllWithTitles(self):
        seekdb = SEEK_DATABASE['NAME']
        data = self.__sendQuery(f"""
            SELECT
                a.id AS assay_id,
                a.title AS assay_title,
                ia.id AS internal_assay_id,
                ia.internal_assay_title AS internal_assay_title
            FROM {self.dbname}.assays_internal_assays aia
            JOIN {seekdb}.assays a ON a.id = aia.assay_id
            LEFT JOIN {self.dbname}.internal_assays ia ON ia.id = aia.internal_assay_id
        """)
        return data

    def syncAssays(self):
        assays = set(Assays.objects.values_list('id', flat=True))
        assays_internal_assays = set(self.tablemodel.objects.values_list('assay_id', flat=True))

        missing = assays.difference(assays_internal_assays)
        extra = assays_internal_assays.difference(assays)

        for assay_id in missing:
            obj = self.tablemodel.objects.create(assay_id=assay_id)
            obj.save()
            
        if len(extra) > 0:
            for assay_id in extra:
                obj = self.tablemodel.objects.get(assay_id=assay_id)
                obj.delete()

    def update(self, assay_id, internal_assay_id):
        obj = self.tablemodel.objects.get(assay_id=assay_id)
        obj.internal_assay_id = internal_assay_id
        obj.save()
