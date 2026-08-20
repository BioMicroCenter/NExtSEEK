#!/usr/bin/env python
import json

import logging
logger = logging.getLogger(__name__)

from .seekapi import SeekAPI
from .models import Sample_types
from dmac.dbtable import DBtable
from .dbtable_sampleattribute import DBtable_sampleattribute

SAMPLETYPE_FILTER_MAPPING = {
}

SAMPLETYPE_DEFAULT = {
}

class DBtable_sampletype(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'SEEK', 'seek_development')
        
        self.tablename = 'sample_types'
        self.tablemodel = Sample_types
        self.fulltablename = self.tablemodel
        self.viewtablename = self.dbname + '.' + self.tablename
        self.fields = [
            'id',
            'title',
            'uuid',
            'created_at',
            'updated_at',
            'first_letter',
            'description',
            'uploaded_template',
            'contributor_id',
            'deleted_contributor',
            'template_id',
            'other_creators',
        ]
        
        self.uniqueFields = []
        self.primaryField = "id"
        self.fieldMapping = SAMPLETYPE_FILTER_MAPPING
        
    def __getSampleTypes(self, id):
        queryset = self.tablemodel.objects.all()
        options = []
        if id==0:
            options.append({'id':0, 'title':'','selected':True})
            options += [ {'id':qi.id, 'title':qi.title} for qi in queryset]
        else:
            options.append({'id':0, 'title':''})
            for qi in queryset:
                dici = {'id':qi.id, 'title':qi.title}
                if int(qi.id)==int(id):
                    dici['selected'] = True
                options.append(dici)
    
        return simplejson.dumps(options, default=str)
        
    def getSamplePage(self, sampletype_id, server, username, password):
        seekapi = SeekAPI(server, username, password)
        if sampletype_id>0:
            seek_url = "/sample_types/" + str(sampletype_id) + "/samples/"
            bodyhtml = seekapi.getPageRequests(seek_url)
        else:
            bodyhtml = '<div>Select the sample type from the ComboBox to get the innformation of samples.</div>'
        return bodyhtml
        
    def getSampleTypeID(self, sampleType_title):
        if '_' in sampleType_title:
            terms = sampleType_title.split('_')
            sampleType_title = terms[0]
        
        return self.db.getPrimarykey(self.tablemodel, 'title', sampleType_title)
        
    def retrieveAttributes(self, sampleTypes):
        sattr = DBtable_sampleattribute()
        
        diclist = []
        for title in sampleTypes:
            sampletype_id = self.getSampleTypeID(title)
            dici = {}
            if sampletype_id>0:
                dici[title] = sattr.getAttributeInfo(sampletype_id)
            else:
                dici[title] = None
            diclist.append(dici)
        
        return diclist
    
    def getSampleTypes(self):
        sampletype_id = 0
        options = self.getComboboxOptions(sampletype_id, 'title')
        options = json.loads(options)
        options_new = []
        for option in options:
            title = option['title']
            if "A." in title:
                option['group'] = "Analysis type"
            elif "D." in title:
                option['group'] = "Data type"
            else:
                option['group'] = "Experimental type"
            options_new.append(option)
        
        options = sorted(options_new, key = lambda i: i['title'])
        
        options_new = []
        for group in ["Experimental type", "Analysis type", "Data type"]:
            for option in options:
                groupi = option['group']
                if group==groupi:
                    options_new.append(option)
        
        options = json.dumps(options_new, default=str)
        return options
        
        
        
