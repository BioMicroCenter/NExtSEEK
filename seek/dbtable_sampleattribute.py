#!/usr/bin/env python
import os
import sys
import time, json
import simplejson
import logging
logger = logging.getLogger(__name__)

from seek.models import Sample_attributes
from seek.dbtable_attributetype import DBtable_attributetype
from dmac.dbtable import DBtable
from dmac.csv_excel import load_file, load_excelfile
from dmac.conversion import toDateClass, is_numeric, toFloat, toBinaryTinyInt, toString, getDefaultDate

SAMPLEATTRIBUTE_FILTER_MAPPING = {
}

SAMPLEATTRIBUTE_DEFAULT = {
}

class DBtable_sampleattribute(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'SEEK', 'seek_development')
        
        self.tablename = 'sample_attributes'
        self.tablemodel = Sample_attributes
        self.fulltablename = self.tablemodel
        self.viewtablename = self.dbname + '.' + self.tablename
        self.fields = [
            'id',
            'title',
            'sample_attribute_type_id',
            'required',
            'created_at',
            'updated_at',
            'pos',
            'sample_type_id',
            'unit_id',
            'is_title',
            'template_column_index',
            'original_accessor_name',
            'sample_controlled_vocab_id',
            'linked_sample_type_id',
            'pid',
            'description',
            'isa_tag_id',
            'allow_cv_free_text',
            'template_attribute_id',
        ]
        
        self.uniqueFields = ['title', 'sample_type_id']
        self.primaryField = "id"
        self.fieldMapping = SAMPLEATTRIBUTE_FILTER_MAPPING
        
    
    def getAttributeInfo(self, sampleType_id):
        attritype = DBtable_attributetype()
        attributeTypeNames = attritype.getAttributeTypes()
        diclist = self.db.retrieveRecords(self.tablemodel, 'sample_type_id', sampleType_id)
        from operator import itemgetter
        newlist = sorted(diclist, key=itemgetter('pos'))
        attributeInfo = {}
        headers = []    
        headers_required = []   
        attributeTypes = {}     
        diclist_new = []
        for dici in newlist:
            field = dici['title']
            headers.append(field)
            
            isrequired = dici['required']
            if isrequired:
                headers_required.append(field)
                
            attributeTypes[field] = dici['sample_attribute_type_id']
            
            dici_new = {}
            dici_new['title'] = dici['title']
            dici_new['id'] = dici['id']
            dici_new['pos'] = dici['pos']
            atype_id = dici['sample_attribute_type_id']
            dici_new['sample_attribute_type_id'] = atype_id
            if atype_id in attributeTypeNames:
                dici_new['sample_attribute_type_title'] = attributeTypeNames[atype_id]
            elif int(atype_id) in attributeTypeNames:
                dici_new['sample_attribute_type_title'] = attributeTypeNames[int(atype_id)]
            else:
                dici_new['sample_attribute_type_title'] = ''
            dici_new['required'] = toBinaryTinyInt(dici['required'])
            dici_new['is_title'] = toBinaryTinyInt(dici['is_title'])
            dici_new['sample_controlled_vocab_id'] = dici['sample_controlled_vocab_id']
            diclist_new.append(dici_new)
            
        attributeInfo['diclist'] = diclist_new
        attributeInfo['headers'] = headers
        attributeInfo['headers_required'] = headers_required
        attributeInfo['attributeTypes'] = attributeTypes
        
        from seek.dbtable_sampletype import DBtable_sampletype
        sampletype = DBtable_sampletype("DEFAULT")
        attributeInfo['sampletype'] = sampletype.getOneRecord(sampleType_id)
        attributeInfo['sampleType_id'] = sampleType_id
        return attributeInfo
        
    def getAttributes(self, sampletype_id, valueSelected=''):
        attributeInfo = self.getAttributeInfo(sampletype_id)
        headers = attributeInfo['headers']
        if len(headers)==0:
            msg = "Error: the sample type has no attribute defined. "
            status = 0
            options = []
        else:
            msg = "Attributes retrieved "
            status = 1
            attributeTypes = attributeInfo['attributeTypes']
            
            options = []
            selected = False
            id = 0
            for header in headers:
                attributeType_id = attributeTypes[header]
                option = {}
                id += 1
                option['name'] = header
                option['attribute'] = header
                if str(attributeType_id)==str(valueSelected):
                    option['selected'] = True
                    selected = True
                
                options.append(option)
        
            if not selected:
                option = {}
                option['name'] = valueSelected
                option['attribute'] = 'none'
                option['selected'] = True
            
                options = [option] + options
            
        attrOptions = options
        data = {'msg':msg, 'status': status, 'attrOptions':attrOptions}
        data['valueSelected'] = valueSelected
        if valueSelected=='yes':
            data['rows'] = attributeInfo['diclist']
        return data
        
    def getOperators(self, sampletype_id, attribute):
        attributeInfo = self.getAttributeInfo(sampletype_id)
        headers = attributeInfo['headers']
        if len(headers)==0:
            msg = "Error: the sample type has no attribute defined. "
            status = 0
            options = []
            data = {
                'msg':msg,
                'status': status,
                'filter_rule':options,
                'placeholder_start':'',
                'placeholder_end':'',
                'filter_type':''
            }
            return data
            
        attributeTypes = attributeInfo['attributeTypes']
        if attribute not in attributeTypes:
            msg = "Error: the sample attribute not available. "
            status = 0
            options = []
            data = {
                'msg':msg,
                'status': status,
                'filter_rule':options,
                'placeholder_start':'',
                'placeholder_end':'',
                'filter_type':''
            }
            return data    
        
        msg = "Attributes retrieved "
        status = 1    
        attributeType_id = attributeTypes[attribute]
        attributeType_id = int(attributeType_id)
        if attributeType_id==3 or attributeType_id==3:
            options = [
                {'name':'No Filter','operator':'No Filter', 'selected':True},
                {'name':'Equal','operator':'Equal'},
                {'name':'Not Equal','operator':'Not Equal'},
                {'name':'Less','operator':'Less'},
                {'name':'Greater','operator':'Greater'},
                {'name':'Between','operator':'Between'}
            ]
            placeholder_start = 'numeric value'
            placeholder_end = 'numeric value'
            filter_type = 'numeric'
        elif attributeType_id in [5,6,7,8,9,10,11,12,13,14,19,20,21]:
            options = [
                {'name':'Contain','operator':'Contain', 'selected': True},
                {'name':'Not Contain','operator':'Not Contain'},
                {'name':'No Filter','operator':'No Filter'},
            ]
            placeholder_start = 'string value'
            placeholder_end = 'not in use'
            filter_type = 'string'
        elif attributeType_id in [1,2]:
            options = [
                {'name':'No Filter','operator':'No Filter', 'selected':True},
                {'name':'Equal','operator':'Equal'},
                {'name':'Not Equal','operator':'Not Equal'},
                {'name':'Before','operator':'Before'},
                {'name':'After','operator':'After'},
                {'name':'Between','operator':'Between'}
            ]
            placeholder_start = 'mm/dd/year'
            placeholder_end = 'mm/dd/year'
            filter_type = 'date'
        elif attributeType_id==18:
            options = [
                {'name':'No Filter','operator':'No Filter', 'selected':True},
                {'name':'Contain','operator':'Contain'},
                {'name':'Not Contain','operator':'Not Contain'}
            ]
            placeholder_start = 'string value'
            placeholder_end = 'not in use'
            filter_type = 'string'
        elif attributeType_id==15:
            options = [
                {'name':'No Filter','operator':'No Filter', 'selected':True},
                {'name':'True','operator':'True'},
                {'name':'False','operator':'False'}
            ]
            placeholder_start = 'not in use'
            placeholder_end = 'not in use'
            filter_type = 'bool'
        else:
            options = [
                {'name':'No Filter','operator':'No Filter', 'selected':True},
                {'name':'Contain','operator':'Contain'},
                {'name':'Not Contain','operator':'Not Contain'}
            ]
            placeholder_start = 'string value'
            placeholder_end = 'not in use'
            filter_type = 'string'
            
        filter_rule = options
        data = {
            'msg':msg,
            'status': status,
            'filter_rule':filter_rule,
            'placeholder_start':placeholder_start,
            'placeholder_end':placeholder_end,
            'filter_type':filter_type
        }
        return data
    
    def filterString(self, values, filter_rule, filter_valueFrom, filter_valueTo):
        passvalues = []
        if filter_rule=='Contain':
            for value in values:
                vi = toString(value)
                if filter_valueFrom in vi:
                    passvalues.append(True)
                else:
                    passvalues.append(False)
                    
        elif filter_rule=='Not Contain':
            for value in values:
                vi = toString(value)
                if filter_valueFrom in vi:
                    passvalues.append(False)
                else:
                    passvalues.append(True)
        else:
            for value in values:
                passvalues.append(True)
        return passvalues  

    def filterBool(self, values, filter_rule, filter_valueFrom, filter_valueTo):
        passvalues = []
        if filter_rule=='True':
            for value in values:
                vi = toBinaryTinyInt(value)
                if vi==1:
                    passvalues.append(True)
                else:
                    passvalues.append(False)
                    
        elif filter_rule=='False':
            for value in values:
                vi = toBinaryTinyInt(value)
                if vi==1:
                    passvalues.append(False)
                else:
                    passvalues.append(True)
        else:
            for value in values:
                passvalues.append(True)
        return passvalues  

    def filterNumeric(self, values, filter_rule, filter_valueFrom, filter_valueTo):
        valueFrom = toFloat(filter_valueFrom)
        passvalues = []
        if filter_rule=='Equal':
            for value in values:
                vi = toFloat(value)
                if vi==valueFrom:
                    passvalues.append(True)
                else:
                    passvalues.append(False)
                    
        elif filter_rule=='Not Equal':
            for value in values:
                vi = toFloat(value)
                if vi==valueFrom:
                    passvalues.append(False)
                else:
                    passvalues.append(True)
                
        elif filter_rule=='Less':
            for value in values:
                vi = toFloat(value)
                if vi<=valueFrom:
                    passvalues.append(True)
                else:
                    passvalues.append(False)
                    
        elif filter_rule=='Greater':
            for value in values:
                vi = toFloat(value)
                if vi>=valueFrom:
                    passvalues.append(True)
                else:
                    passvalues.append(False)
                    
        elif filter_rule=='Between':
            valueTo = toFloat(filter_valueTo)
            for value in values:
                vi = toFloat(value)
                if vi>=valueFrom and vi<=valueTo:
                    passvalues.append(True)
                else:
                    passvalues.append(False)
                    
        else:
            for value in values:
                passvalues.append(True)
                
        return passvalues          

    def filterDate(self, values, filter_rule, filter_valueFrom, filter_valueTo):
        valueFrom = toDateClass(filter_valueFrom)
        passvalues = []
        if filter_rule=='Equal':
            for value in values:
                vi = toDateClass(value)
                if vi is None:
                    passvalues.append(False)
                elif vi==valueFrom:
                    passvalues.append(True)
                else:
                    passvalues.append(False)
                    
        elif filter_rule=='Not Equal':
            for value in values:
                vi = toDateClass(value)
                if vi is None:
                    passvalues.append(True)
                elif vi==valueFrom:
                    passvalues.append(False)
                else:
                    passvalues.append(True)
                
        elif filter_rule=='Before':
            for value in values:
                vi = toDateClass(value)
                if vi is None:
                    passvalues.append(False)
                elif vi<=valueFrom:
                    passvalues.append(True)
                else:
                    passvalues.append(False)
                    
        elif filter_rule=='After':
            for value in values:
                vi = toDateClass(value)
                if vi is None:
                    passvalues.append(False)
                elif vi>=valueFrom:
                    passvalues.append(True)
                else:
                    passvalues.append(False)
                    
        elif filter_rule=='Between':
            valueTo = toDateClass(filter_valueTo)
            for value in values:
                vi = toDateClass(value)
                if vi is None:
                    passvalues.append(False)
                elif vi>=valueFrom and vi<=valueTo:
                    passvalues.append(True)
                else:
                    passvalues.append(False)
                    
        else:
            for value in values:
                passvalues.append(True)
                
        return passvalues    

    def filterValues(self, values, sampletype_id, attribute, filter_rule, filter_valueFrom, filter_valueTo):
        attrdata = self.getOperators(sampletype_id, attribute)
        filter_type = attrdata['filter_type']
          
        if filter_type=='string':
            passvalues = self.filterString(values, filter_rule, filter_valueFrom, filter_valueTo)
        elif filter_type=='numeric':
            passvalues = self.filterNumeric(values, filter_rule, filter_valueFrom, filter_valueTo)
        elif filter_type=='date':
            passvalues = self.filterDate(values, filter_rule, filter_valueFrom, filter_valueTo)
        elif filter_type=='bool':
            passvalues = self.filterBool(values, filter_rule, filter_valueFrom, filter_valueTo)
        else:
            passvalues = self.filterString(values, filter_rule, filter_valueFrom, filter_valueTo)
            
        return passvalues
        
    def validateFilters(self, sampletype_id, attribute, filter_rule, filter_valueFrom, filter_valueTo):
        if filter_rule=='No Filter':
            msg = 'okay'
            status = 1
            return msg, status
        
        attrdata = self.getOperators(sampletype_id, attribute)
        msg = attrdata['msg']
        status = attrdata['status']
        if status==0:
            return msg, status
        
        filter_type = attrdata['filter_type']
        if filter_type=='numeric':
            if not is_numeric(filter_valueFrom):
                msg = 'Warning: Not a valid numeric value: ' + str(filter_valueFrom)
                status = 0
                return msg, status
            
            if filter_rule=='Between':
                if not is_numeric(filter_valueTo):
                    msg = 'Warning: Not a valid numeric value: ' + str(filter_valueTo)
                    status = 0
                    return msg, status
            
        elif filter_type=='date':
            if toDateClass(filter_valueFrom) is None:
                msg = 'Warning: Start date not valid: ' + str(filter_valueFrom)
                status = 0
                return msg, status
            
            if filter_rule=='Between':
                if toDateClass(filter_valueTo) is None:
                    msg = 'Warning: End date not valid: ' + str(filter_valueTo)
                    status = 0
                    return msg, status
        
        msg = 'validateFilters: okay'
        status = 1
        return msg, status
        
    def reformatRecordForDB(self, login_user, record):
        record_new = {}
        for key in self.fields:
            key_new = str(key)
            if key in record:
                value = record[key]
            elif key=="sample_attribute_type_id":
                value = record['sample_attribute_type_title']
            elif key=="created_at":
                value = getDefaultDate()
            elif key=="updated_at":
                value = getDefaultDate()
            elif key=="template_column_index":
                value = record['pos']
            elif key=="original_accessor_name":
                title = record['title']
                value = title.lower()
            else:
                continue
            
            if key_new=='id':
                pid = int(value)
                if pid>0:
                    record_new[key_new] = value
            elif key_new=="sample_controlled_vocab_id":
                continue
            else:
                record_new[key_new] = value
        return record_new
        
    def getAttributesRenamed(self, sampleType_id, records_revised):
        attributeInfo = self.getAttributeInfo(sampleType_id)
        attributes_db = attributeInfo['diclist']
        attributes_old = {}
        for attribute in attributes_db:
            id = attribute['id']
            attributes_old[id] = attribute
        
        attri_renamed= {}
        for record in records_revised:
            title_new = record['title']
            if 'id' not in record:
                continue
                
            id = int(record['id'])
            if id in attributes_old:
                attribute = attributes_old[id]
                title_old = attribute['title']
                if title_new!=title_old:
                    attri_renamed[title_new] = title_old
        
        return attri_renamed
        
