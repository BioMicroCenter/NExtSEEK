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
from seek.models import Sample_types_clades, Sample_types, Clades
from dmac.dbtable import DBtable

SEEK_DATABASE = settings.DATABASES[settings.SEEK_DATABASE]
DJANGO_DATABASE = settings.DATABASES['default']

class DBtable_sample_types_clades(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'DJANGO', DJANGO_DATABASE['NAME'])
        
        self.tablename = 'sample_types_clades'
        self.tablemodel = Sample_types_clades
        self.fulltablename = self.tablemodel
        self.viewtablename = self.dbname + '.' + self.tablename
        self.fields = [
            'id',
            'clade_id',
            'sample_type_id'
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
                st.id AS sample_type_id,
                st.title AS sample_type_title,
                c.id AS clade_id,
                c.title AS clade_title
            FROM {self.dbname}.sample_types_clades stc
            JOIN {seekdb}.sample_types st ON st.id = stc.sample_type_id
            LEFT JOIN {self.dbname}.clades c ON c.id = stc.clade_id
        """)
        
        return data

    def getAllCounts(self):
        seekdb = SEEK_DATABASE['NAME']
        data = self.__sendQuery(f"""
            SELECT
            	c.title AS title,
            	c.color AS color,
            	st.id AS st_id,
            	st.description AS st_group,
            	COUNT(stc.sample_type_id) AS count
            FROM {seekdb}.samples s
            JOIN {self.dbname}.sample_types_clades stc ON stc.sample_type_id = s.sample_type_id
            JOIN {self.dbname}.clades c ON c.id = stc.clade_id
            JOIN {seekdb}.sample_types st ON st.id = stc.sample_type_id
            GROUP BY st.description
            ORDER BY c.order, st.description
        """)

        return data

    def getColorBySampleTypeID(self, sample_type_id):
        try:
            clade_id = self.tablemodel.objects.get(sample_type_id=sample_type_id).id
        except Exception:
            return "#000000"

        clade = Clades.objects.get(id=clade_id)
        color = clade.color if clade.color is not None else "#000000"

        return color
    
    def getColorBySampleTypeTitle(self, sample_type_title):
        try:
            sample_type_id = Sample_types.objects.get(title=sample_type_title).id
            clade_id = self.tablemodel.objects.get(sample_type_id=sample_type_id).clade_id
        except Exception:
            return "#000000"

        clade = Clades.objects.get(id=clade_id)
        color = clade.color if clade.color is not None else "#000000"

        return color
        
    def syncSampleTypes(self):
        sample_types = set(Sample_types.objects.values_list('id', flat=True))
        sample_types_clades = set(self.tablemodel.objects.values_list('sample_type_id', flat=True))

        missing_types = sample_types.difference(sample_types_clades)
        extra_types = sample_types_clades.difference(sample_types)

        for st_id in missing_types:
            obj = self.tablemodel.objects.create(sample_type_id=st_id)
            obj.save()
            
        if len(extra_types) > 0:
            for st_id in extra_types:
                obj = self.tablemodel.objects.get(sample_type_id=st_id)
                obj.delete()

    def update(self, sample_type_id, clade_id):
        obj = self.tablemodel.objects.get(sample_type_id=sample_type_id)
        obj.clade_id = clade_id
        obj.save()
