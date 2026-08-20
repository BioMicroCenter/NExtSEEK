#!/usr/bin/env python
import xlwt
import logging
logger = logging.getLogger(__name__)

from dmac.dbtable import DBtable
from dmac.conversion import cleanString, toString


ONTOLOGY_FILTER_MAPPING = {
}

ONTOLOGY_DEFAULT = {
}

INSTRUCTION_FIELD = "Field"
INSTRUCTION_FIELDTYPE = "Field Type"
INSTRUCTION_ONTOLOGY = "Ontology"

class DBtable_ontology(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'SEEK', 'seek_development')
        self.tablename = 'sample_controlled_vocab_terms'
        self.fulltablename = self.tablemodel
        self.viewtablename = self.dbname + '.' + self.tablename
        self.fields = [
            'id',
            'label',
            'sample_controlled_vocab_id',
            'created_at',
            'updated_at',
            'iri',
            'parent_irl',
        ]
        
        self.uniqueFields = []
        self.primaryField = "id"
        self.fieldMapping = ONTOLOGY_FILTER_MAPPING
        
    def loadControlledVocabulary(self, diclist_instruction, diclist_ontology):
        attributesControlled = {}
        ontologies = {}
        
        status = 1
        msg = ''
        for dici in diclist_instruction:
            if INSTRUCTION_FIELD not in dici or INSTRUCTION_FIELDTYPE not in dici or INSTRUCTION_ONTOLOGY not in dici:
                msg = "Error: Instruction sheet misses one of 'Field', 'Field Type' or 'Ontology' column."
                status = 0
                return attributesControlled, ontologies, status, msg
                
            tbheader = dici[INSTRUCTION_FIELD]           
            fieldType = dici[INSTRUCTION_FIELDTYPE]
            
            ontologyname = dici[INSTRUCTION_ONTOLOGY]
            if fieldType is not None and fieldType.strip()=='Controlled Ontology':
                if tbheader not in ontologies:
                    ontologies[tbheader] = ontologyname
                if ontologyname not in attributesControlled:
                    attributesControlled[ontologyname] = []
        
        if len(attributesControlled)==0:
            return attributesControlled, ontologies, status, msg
                    
        for dici in diclist_ontology:
            for field, term in dici.items():
                if field in attributesControlled:
                    terms = attributesControlled[field]
                    if term is not None and len(term.strip())>0:
                        terms.append(term.strip())
        
        return attributesControlled, ontologies, status, msg
    
    
    def getAttributeInfo(self, sampleType_id):
        diclist = self.db.retrieveRecords(self.tablemodel, 'sample_type_id', sampleType_id)
        
        from operator import itemgetter
        newlist = sorted(diclist, key=itemgetter('pos'))
        ontologyInfo = {}
        headers = []    
        headers_required = []  
        ontologyTypes = {}     
        for dici in newlist:
            field = dici['title']
            headers.append(field)
            
            isrequired = dici['required']
            if isrequired:
                headers_required.append(field)
                
            ontologyTypes[field] = dici['sample_ontology_type_id']
            
        ontologyInfo['headers'] = headers
        ontologyInfo['headers_required'] = headers_required
        ontologyInfo['ontologyTypes'] = ontologyTypes
        
        from .dbtable_sampletype import DBtable_sampletype  # deferred, but not a cycle -- safe to hoist
        sampletype = DBtable_sampletype("DEFAULT")
        ontologyInfo['sampletype'] = sampletype.getOneRecord(sampleType_id)
        ontologyInfo['sampleType_id'] = sampleType_id
        return ontologyInfo
        
    def __verifyOntologyTerm(self, ontology, term):
        status = 1
        termRevised = term
        if term in ontology:
            return status, termRevised
        
        ontologyDic = {}
        for termi in ontology:
            termUp = termi.strip().upper()
            ontologyDic[termUp] = termi
            
        termUp = term.strip().upper()
        if termUp in ontologyDic:
            status = 1
            termRevised = ontologyDic[termUp]
        else:
            status = 0
        
        return status, termRevised
        
        
    def evaluateOntology(self, diclist_sample, diclist_ins, diclist_ont):
        ontology_feedback = []
        attributesControlled, ontologies, status, msg = self.loadControlledVocabulary(diclist_ins, diclist_ont)
        if status==0:
            return msg, status, ontology_feedback
        
        if len(attributesControlled)==0:
            status = 1
            return msg, status, ontology_feedback
        
        status = 1  
        for dici in diclist_sample:
            ontology_error = []
            for header, value in dici.items():
                if header in ontologies:
                    ontologyname = ontologies[header]
                    ontology = attributesControlled[ontologyname]
                    if value is not None:
                        valuestr = toString(value)
                        if len(valuestr)>0:
                            if value not in ontology:
                                
                                status, valueRevised = self.__verifyOntologyTerm(ontology, value)
                                if status==1:
                                    dici[header] = valueRevised
                                else:
                                    ontology_error.append(header)
                                    status = 0                               
                                
            ontology_feedback.append(ontology_error)
        return msg, status, ontology_feedback
    
    def outputOntologyFeedback(self, dicilist_feedback, headers, feedbackfile, ontology_feedback):
        book = xlwt.Workbook(encoding="utf-8")
        sheet1 = book.add_sheet("Samples")
        row = 0
        for index, header in enumerate(headers):
            try:
                newitem = toString(header)
            except:
                newitem = cleanString(header)
            sheet1.write(row, index, newitem)
        
        i = 0
        
        style = xlwt.easyxf('pattern: pattern solid, fore_colour red;')
        for dici in dicilist_feedback:
            row += 1
            ontology_error = ontology_feedback[i]
            for index, header in enumerate(headers):
                if header in dici:
                    newitem = dici[header]
                else:
                    newitem = "N/A"
                
                try:
                    newitem = str(newitem)
                except:
                    newitem = cleanString(newitem)
                    
                if header in ontology_error:
                    sheet1.write(row, index, newitem, style)
                else:    
                    sheet1.write(row, index, newitem)
            i += 1    
        book.save(feedbackfile)
        
        
