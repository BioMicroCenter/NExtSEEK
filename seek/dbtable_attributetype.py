#!/usr/bin/env python
import json
import logging
logger = logging.getLogger(__name__)

from .models import Sample_attribute_types
from dmac.dbtable import DBtable

SAMPLEATTRIBUTE_TYPE_MAPPING = {
}

ATTRIBUTETYPE_DEFAULT = {
}

class DBtable_attributetype(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'SEEK', 'seek_development')
        self.tablename = 'sample_attribute_types'
        self.tablemodel = Sample_attribute_types
        self.fulltablename = self.tablemodel
        self.viewtablename = self.dbname + '.' + self.tablename
        self.fields = [
            'id',
            'title',
            'base_type',
            'regexp',
            'created_at',
            'updated_at',
            'placeholder',
            'description',
            'resolution'
        ]
        
        self.uniqueFields = ['title']
        self.primaryField = "id"
        self.fieldMapping = SAMPLEATTRIBUTE_TYPE_MAPPING
    
    def getAttributeTypes(self):
        options = self.getComboboxOptions(0, 'title')
        options = json.loads(options)
        attributeTypes = {}
        for option in options:
            title = option['title']
            id = option['id']
            attributeTypes[id] = title
        
        return attributeTypes
    
    def getAttributeTypeOptions(self):
        options = self.getComboboxOptions(0, 'title')
        options = json.loads(options)
        options_new = []
        for option in options:
            option_new = {}
            option_new['sample_attribute_type_id'] = option['id']
            option_new['sample_attribute_type_title'] = option['title']
            options_new.append(option_new)
            
        options = json.dumps(options_new, default=str)
        return options
        
        
        

