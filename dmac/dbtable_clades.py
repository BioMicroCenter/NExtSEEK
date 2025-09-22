#!/usr/bin/env python
import os
import sys
import MySQLdb
import time, json

import logging
logger = logging.getLogger(__name__)

import dmac.settings as settings
import pandas as pd
import numpy as np

from seek.seekapi import SeekAPI
from seek.models import Clades
from dmac.dbtable import DBtable


SEEK_DATABASE = settings.DATABASES[settings.SEEK_DATABASE]
DJANGO_DATABASE = settings.DATABASES['default']

class DBtable_clades(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'DJANGO', DJANGO_DATABASE['NAME'])
        
        self.tablename = 'clades'
        self.tablemodel = Clades
        self.fulltablename = self.tablemodel
        self.viewtablename = self.dbname + '.' + self.tablename
        self.fields = [
            'id',
            'title',
            'color',
            'order'
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

    def get(self,clade_id):
        clades = self.tablemodel.objects.filter(id=clade_id).values()
        if len(clades) == 0:
            return {}
        else:
            return clades[0]
        
    def getAll(self):
        return self.tablemodel.objects.all().values()

    def getCladeProjectStats(self, project_id):
        seekdb = SEEK_DATABASE['NAME']
        query = f"""
            SELECT p.id, p.title, st.description AS st_group, c.title, c.color, c.order, COUNT(s.id) AS count
            FROM {seekdb}.projects_samples ps
            JOIN {seekdb}.samples s ON ps.sample_id = s.id
            JOIN {seekdb}.sample_types st ON s.sample_type_id = st.id
            JOIN {self.dbname}.sample_types_clades stc ON st.id = stc.sample_type_id
            JOIN {self.dbname}.clades c ON stc.clade_id = c.id
            JOIN {seekdb}.projects p ON ps.project_id = p.id
            WHERE p.id = {project_id}
            GROUP BY st_group, c.title
            ORDER BY c.order
        """
        data = self.__sendQuery(query)

        return data

    def new(self, title, color, order):
        clade = self.tablemodel(title=title,color=color,order=int(order))
        clade.save()

    def update(self, clade_id, title, color, order):
        clade = self.tablemodel.objects.get(id=clade_id)
        clade.title = title
        clade.color = color
        clade.order = int(order)
        clade.save()

    def delete(self, clade_id):
        clade = self.tablemodel.objects.get(id=clade_id)
        clade.delete()
