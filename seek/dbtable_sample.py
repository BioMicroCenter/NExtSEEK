#!/usr/bin/env python
import os
import datetime
import simplejson
import json
import html
import logging
import xlwt
import operator
logger = logging.getLogger(__name__)

import MySQLdb
import zipfile
import pandas as pd
from django.conf import settings
from django.db.models import Q
from itertools import chain

from .models import Samples, Projects_samples, People, Assets_creators, Projects
from dmac.dbtable import DBtable
from dmac.csv_excel import load_excelfile_asdic, saveExcelDiclist, modifyExcelCell, reviseExcelDiclist, removeRedundancy, AddExcelDiclist
from dmac.conversion import toString, cleanString, getDefaultDateTime, toInt, verifyValueType
from dmac.iocsv import saveDiclistIntoExcel, filterDiclist, saveTwoDiclistsIntoExcel, getConstantRows, removeDiclistDuplicates

from .dbtable_sampleattribute import DBtable_sampleattribute
from .dbtable_sampletype import DBtable_sampletype
from .dbtable_assay_assets import DBtable_assay_assets

from .dbtable_data_files import DBtable_data_files
from .dbtable_sops import DBtable_sops
from .dbtable_policies import DBtable_policies

from .dbtable_ontology import DBtable_ontology
from nextseek_api.services.sample_workbook import write_samples_workbook
from neo4j import GraphDatabase

NEO4J_DATABASE = settings.NEO4J_DATABASE
SEEK_DATABASE = settings.SEEK_DATABASE
NEXTSEEK_DATABASE = settings.NEXTSEEK_DATABASE
DOWNLOAD_DIRECTORY  = settings.MEDIA_ROOT + "/download/"
DOWNLOAD_DIRECTORY_LINK = settings.MEDIA_URL + 'download/'

SAMPLE_FILTER_MAPPING = {
    "id":"A.id",
    "title":"A.title",
    "sample_type_id":"A.sample_type_id",
    "sample_type":"B.title",
    "uid":"A.uuid",
    "contributor_id":"A.contributor_id",
    "first_name":"C.first_name",
    "created_at":"A.created_at",
    "json_metadata":"A.json_metadata",
    "assay_id":"D.assay_id",
    "assayname":"E.assayname",
    "work_group_id":"F.work_group_id",
    "project_id":"G.project_id",
    "institution_id":"G.institution_id",
    "projectname":"H.title",
    "institution":"I.title"
}
SAMPLE_HEADERS = [
    "id",
    "title",
    "sample_type_id",
    "sample_type",
    "uid",
    "contributor_id",
    "first_name",
    "created_at",
    "json_metadata",
    "assays"
    #"assay_id",
    #"assayname",
    #"work_group_id",
    #"project_id",
    #"institution_id",
    #"projectname",
    #"institution"
]

SAMPLE_DEFAULT = {
    #'id':'',
    'title':'',
    'sampleType_id':0,
    'json_metadata':'',
    'uuid':'',
    'contributor_id':0,
    'policy_id':'',
    'created_at':'',
    'updated_at':'',
    'first_letter':'',
    'other_creators':'',
    'originating_data_file_id':None,  
    'deleted_contributor':None         
}

SAMPLE_SHEET_NAMES = ["INSTRUCTIONS", "SAMPLES", "ASSAY", "ONTOLOGY"]

ATTRIBUTETYPE_ID_WEBLINK = 5
ATTRIBUTETYPE_ID_URI = 19

SAMPLE_PARENT_ATTRIBUTOR = "CreatedFromSample"
SAMPLE_PARENT_ACCESSOR_NAME = "Parent"
SAMPLE_PROTOCOL_ACCESSOR_NAME = "Protocol"
SAMPLE_FILE_ACCESSOR_NAME = "File_"         
SAMPLE_LINK_ACCESSOR_NAME = "Link_"        
SAMPLE_CONTRIBUTOR_ACCESSOR_NAME = "Scientist"
SAMPLE_PUBLISH_ACCESSOR_NAME = "Publish"

SAMPLE_ERRORCODE = {
    '101': 'Error: Excel file in incorrect format.',
    '102': 'Error: Assay sheet does not contain required sheet - ',
    '103': 'Error: Assay sheet does not contain valid "Instruction" sheet.',
    '104': 'Error: Assay sheet does not contain valid "Samples" sheet.',
    '105': 'Error: Sample type not identified in assay sheet - ',
    '106': 'Error: Excel file failed to load - ',
    '201': 'Error: Sample type not uniquely defined in database - ',
    '202': 'Error: Unknown attribute in assay sheet - ',
    '301': 'Error: Sample has an invalid Parent UID. ',
    '302': 'Error: Sample is missing data for either the "Name" or "File_PrimaryData" attribute - ',
    '303': 'Error: Sample has invalid entry for either the "Name" or "File_PrimaryData" attribute. ',
    '304': 'Error: Sample has no entry for the required "Scientist" attribute',
    '305': 'Error: "Scientist" name for the sample not registered in Seek: ',
    '401': 'Error: User has already uploaded a sample with this name to the database; please include the UID in order to update the sample metadata - ',
    '402': 'Error: Sample UID does not match sample name in the SEEK database - ',
    '403': 'Error: Sample name corresponds to more than one record in the database; please ask an admin for help.',
    '501': 'Error: No information provided for sample.',
    '502': 'Error: Required data is missing - ',
    '503': 'Error: Assay sheet is missing the "UID" attribute. ',
    '504': 'Error: Sample not saved into DB - ',
    '601': 'Warning: Sample not saved to the SEEK database - ',
    '602': 'Warning: Data file not associated with a sample in the SEEK database - ',
    
    '701': 'Warning: Assay sheet does not contain valid "Update_assay" sheet.',
    
}

DELIMITER_DBFIELD = "::"
SAMPLE_TEMPLATE_FILE = settings.MEDIA_ROOT + "/reserved/SAMPLE_TEMPLATE.xlsx"
IMMPORT_TEMPLATE_FILE_PREFIX = settings.MEDIA_ROOT + "/reserved/IMMPORT_TEMPLATE-"
IMMPORT_TEMPLATE_FILE = settings.MEDIA_ROOT + "/reserved/IMMPORT_TEMPLATE-MAPPING.xlsx"
IMMPORT_TEMPLATES = {'protocols':'protocols',
    'subjectanimals':'subjectanimals',
    'biosamples':'biosamples',
    'experiments':'experiments',
    'experimentsamples':'mass_spec_proteomics'   
}
IMMPORT_TEMPLATES_VERSION = 'Schema Version 3.32'
PUBLISH_SERVER = settings.PUBLISH_URL
RESERVED_REMOVE_VALUE_FOR_UPDATE = "-null"
RESERVED_DEFAULT_VALUE_FOR_UPDATE = "-none"

    
from joblib import Parallel, delayed
import multiprocessing

def unwrap_self_createMultiParentTreeParallel_i(arg, **kwarg):    
    return DBtable_sample.createMultiParentTreeParallel_i(*arg, **kwarg)

def unwrap_self_createSampleChildrenTreeParallel_i(arg, **kwarg):
    return DBtable_sample.createSampleChildrenTreeParallel_i(*arg, **kwarg)

class DBtable_sample(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'SEEK', 'seek_development')
        self.tablename = 'samples'
        self.tablemodel = Samples
        self.fulltablename = self.tablemodel
        # Use the Django model for viewtablename so the Django backend can call .objects
        self.viewtablename = self.tablemodel
        self.fields = [
            'id',
            'title',
            'sample_type_id',
            'json_metadata',
            'uuid',
            'contributor_id',
            'policy_id',
            'created_at',
            'updated_at',
            'first_letter',
            'other_creators',
            'originating_data_file_id',
            'deleted_contributor'
        ]
        self.uniqueFields = ['uuid']
        self.primaryField = "id"
        self.fieldMapping = SAMPLE_FILTER_MAPPING
        self.excludeFields = []

    def __runQuery(self, query, withColumns=False):
        db = settings.DATABASES[SEEK_DATABASE]
        conn = MySQLdb.connect(host=db['HOST'],
                               user=db['USER'],
                               passwd=db['PASSWORD'],
                               db=db['NAME'])
        cursor = conn.cursor()
        
        try:
            cursor.execute(query)
            results = cursor.fetchall()
            if withColumns:
                columns = [col[0] for col in cursor.description]
                return results, columns
            else:
                return results
        except Exception:
            return None
        
    def __notEmptyLine(self, csvdic):
        notEmpty = True
        if len(csvdic)==0:
            notEmpty = False
            return notEmpty
            
        allNone = True
        for key, value in csvdic.items():
            if value is None:
                okay = 1
            else:
                allNone = False
            
        if allNone:    
            notEmpty = False
            
        return notEmpty
        
    def getSampleUIDIndex(self, sampleUIDPrefix):
        records = self.db.retrieveRecords(self.tablemodel, 'uuid', sampleUIDPrefix)
        prefix = sampleUIDPrefix + '-'          
        indexes = []
        maxindex = 0
        for record in records:
            uid = record['uuid']               
            if prefix in uid:
                index = uid.replace(prefix, '') 
                index = toInt(index)
                if index>maxindex:
                    maxindex = index
            
        nextIndex = maxindex + 1    
        return nextIndex
    
    def __defineUID(self, user_seek, record, attributeInfo):
        sampletype = attributeInfo['sampletype']
        typetitle = sampletype['title']
        uid_prefix = typetitle
        if '_' in typetitle:
            terms = typetitle.split('_')
            uid_prefix = terms[0]
            
        uid_date = str(datetime.datetime.now().strftime("%Y%m%d"))
        lab = user_seek['lababbv']
        prefix = uid_prefix + '-' + uid_date[2:] + lab
        nextIndex = str(self.getSampleUIDIndex(prefix))
        uid = prefix + '-' + nextIndex
        return uid
    
    def __getRecordToJson(self, record, attributeInfo):
        headers = attributeInfo['headers']
        record_new = {}
        for header in headers:
            field = header
            if header in record:
                record_new[field] = toString(record[header])
            else:
                record_new[field] = ''
        
        record_json = simplejson.dumps(record_new, default=str)
        return record_json
        
    def __getRecordFromJson(self, record_json):
        record = json.loads(record_json)
        return record
        
    def __updateSampleMetadata(self, metadata_db, metadata_in, attributes=None):
        #logger.debug('updateSampleMetadata')

        logger.debug(f"Updating sample with UID: {metadata_db['UID']}")
        if attributes is not None:
            metadata_db2 = {}
            for key, value in metadata_db.items():
                if key in attributes:
                    metadata_db2[key] = value
            metadata_db = metadata_db2
        
        metadata_out = {}
        for key, value in metadata_db.items():
            if key in metadata_in:
                value_in = metadata_in[key]
                if value_in is None:
                    metadata_out[key] = value
                    continue
                
                elif value_in==RESERVED_REMOVE_VALUE_FOR_UPDATE:
                    continue
                
                elif value_in==RESERVED_DEFAULT_VALUE_FOR_UPDATE:
                    metadata_out[key] = ''
                    continue
                
                try:
                    value_str = str(value_in)
                    if len(value_str)>0:
                        metadata_out[key] = value_in
                except:
                    metadata_out[key] = value_in
            else:
                metadata_out[key] = value
                
        for key, value in metadata_in.items():
            if key not in metadata_out:
                if value==RESERVED_DEFAULT_VALUE_FOR_UPDATE:
                    metadata_out[key] = ''
                else:
                    metadata_out[key] = value

        #logger.debug('updateSampleMetadata: Finish')
        return metadata_out
        
    def __getRecord(self, user_seek, record, attributeInfo, contributor_id):
        username = user_seek['username']
        user_id = user_seek['user_id']
        project_id = user_seek['projectid']
        
        record_new = {}
        for field in self.fields:
            value = ''
            if field in SAMPLE_DEFAULT:
                value = SAMPLE_DEFAULT[field]
            record_new[field] = value
            
        uid = record['UID']
        newSample = False
        if uid is None or len(uid.strip())==0:
            uid = self.__defineUID(user_seek, record, attributeInfo)
            record['UID'] = uid
            newSample = True
            
        if 'Name' in record:
            samplename = str(record['Name'])
        elif 'File_PrimaryData' in record:
            samplename = str(record['File_PrimaryData'])
        elif 'File_PrimaryData_Forward' in record:
            samplename = str(record['File_PrimaryData_Forward'])
        elif 'File_PrimaryData_Reverse' in record:
            samplename = str(record['File_PrimaryData_Reverse'])
        else:
            samplename = 'Undefined'
        
        record_new['title'] = samplename
        record_new['sample_type_id'] = attributeInfo['sampleType_id']
        record_new['json_metadata'] = self.__getRecordToJson(record, attributeInfo)
        record_new['uuid'] = uid
        record_new['contributor_id'] = contributor_id
        
        policy = DBtable_policies("DEFAULT")
        record_db = self.__retrieveSampleByUID(uid)
        if record_db is None:
            msg, status, policy_id = policy.createDefaultPolicy(username, contributor_id, project_id)
        else:
            policy_id = record_db['policy_id']
            contributor_id = record_db['contributor_id']
            record_new['contributor_id'] = contributor_id
            
            metadata_db = self.__getRecordFromJson(record_db['json_metadata'])
            metadata_in = self.__getRecordFromJson(record_new['json_metadata'])
            metadata_out = self.__updateSampleMetadata(metadata_db, metadata_in)
            record_new['json_metadata'] = simplejson.dumps(metadata_out, default=str)
            
            other_creators = record_db['other_creators']
            if other_creators is None:
                record_new['other_creators'] = username
            else:
                record_new['other_creators'] = other_creators + ';' + username
            
        record_new['policy_id'] = policy_id
        record_new['created_at'] = getDefaultDateTime()
        record_new['updated_at'] = getDefaultDateTime()
        record_new['first_letter'] = samplename[0]
        return record_new, newSample
        
    def __updateSampleProject(self, user_seek, sample_id):
        username = user_seek['username']
        user_id = user_seek['user_id']
        project_id = user_seek['projectid']

        db = settings.DATABASES[SEEK_DATABASE]
        conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])
        conn.autocommit(False)
        cursor = conn.cursor()
        
        try:
            cursor.execute(f"INSERT INTO projects_samples (project_id, sample_id) VALUES ({project_id}, {sample_id})")
            conn.commit()
        except:
            conn.rollback()
        
        #record = {}
        #record['sample_id'] = sample_id
        #record['project_id'] = project_id
        #record = Projects_samples(project_id=project_id, sample_id=sample_id)
        #record.save()
        return
        
        
    def __updateSampleAssetsCreators(self, sample_id, creator_id):
        records = Assets_creators.objects.filter(asset_id=sample_id, creator_id=creator_id, asset_type='Sample')
        if len(records)==1:
            return
        
        if len(records)>1:
            Assets_creators.objects.filter(asset_id=sample_id, creator_id=creator_id, asset_type='Sample').delete()
        
        timenow = getDefaultDateTime()
        record = Assets_creators(asset_id=sample_id, creator_id=creator_id, asset_type='Sample', created_at=timenow, updated_at=timenow)
        record.save()
        return
        
    def __verifyRequiredFields(self, record, fields_required):
        if 'UID' not in record.keys():
            msg = SAMPLE_ERRORCODE['503']
            meetRequired = False
            return msg, meetRequired
        
        uid = record['UID']
        if uid is None or len(uid.strip())==0:
            newSample = True
        else:
            newSample = False
            meetRequired = True
            msg = 'Other required fields are not necessary when the UID is available for updating the sample Info.'
            return msg, meetRequired
        
        if 'UID' in fields_required:
            fields_required.remove('UID')
        
        meetRequired = True
        msg_required = 'Following fields are required: '
        for field in fields_required:
            if field in record:
                value = record[field]
                if value is None:
                    meetRequired = False
                    msg_required += field + ";"
                else:
                    valuestr = str(value)
                    if len(valuestr.strip())==0:
                        meetRequired = False
                        msg_required += field + ";"
            else:
                meetRequired = False
                msg_required += field + ";"
                
        if meetRequired:
            msg_required = 'All fields required are available'
        return msg_required, meetRequired
        
    
    def __loadSampleTypes(self, diclist_instruction):
        sampleTypes = {}      
        sampleTypes_order = []
        for dici in diclist_instruction:
            if "Field" not in dici or "Database Field" not in dici:
                msg = "Error: Instruction sheet should contain 'Field' or 'Database Field' columns."
                return {}, []
                
            tbheader = dici["Field"]
            if tbheader is not None:
                header = str(tbheader)
                if len(header.strip())==0:
                    continue
            else:
                continue
            
            dbfield = dici["Database Field"]
            if DELIMITER_DBFIELD not in dbfield:
                msg = "Error: 'Database Field' column should follow the format 'tableName::attributeName."
                return {}, []
                
            terms = dbfield.split(DELIMITER_DBFIELD)     
            sampleType = terms[0]         
            attribute = terms[1]            
            if sampleType in sampleTypes:
                attributeMapping = sampleTypes[sampleType]
            else:
                attributeMapping = {}
                                
            attributeMapping[tbheader] = attribute
            sampleTypes[sampleType] = attributeMapping
            
            if sampleType not in sampleTypes_order:
                sampleTypes_order.append(sampleType)
            
        return sampleTypes, sampleTypes_order
        
    def __splitSampleTypes(self, sampleTypes, diclist_samples):
        sample_sheets = {}
        for sampleType in sampleTypes:
            sample_sheets[sampleType] = []
            
        unique_samples = {}
        for dici_meta in diclist_samples:
            for sampleType, attributeMapping in sampleTypes.items():
                diclist = sample_sheets[sampleType]
                
                dici_sample = {}
                samplename = None
                for header, value in dici_meta.items():
                    if header in attributeMapping:
                        attribute = attributeMapping[header]
                        dici_sample[attribute] =  value
                        
                        if attribute.lower()=='name':
                            samplename = value
                        elif attribute.lower()=='file_primarydata':
                            samplename = value
                        elif attribute.lower()=='file_primarydata_forward':
                            samplename = value
                        elif attribute.lower()=='file_primarydata_reverse':
                            samplename = value
                            
                if samplename is None:
                    msg = 'Error: one sample does not have an unique identifier, which will be captured later on in __getRecord()'
                elif samplename in unique_samples:
                    dici_sample = unique_samples[samplename]
                else:
                    unique_samples[samplename] = dici_sample
                        
                diclist.append(dici_sample)
                sample_sheets[sampleType] = diclist
        return sample_sheets
    
    
    def __setSampleDatafileAssociation(self, user_seek, sampleType, record, attributeInfo, diclist_assay):   
        username = user_seek['username']
        user_id = user_seek['user_id']
        project_id = user_seek['projectid']
        msg = ''
        status = 1
        
        attributeTypes = attributeInfo['attributeTypes']
        dbdf = DBtable_data_files("DEFAULT")
        for attribute, dfurl in record.items():
            if attribute not in attributeTypes:
                continue
            
            attributeType_id = attributeTypes[attribute]
            if attributeType_id!=ATTRIBUTETYPE_ID_WEBLINK and attributeType_id!=ATTRIBUTETYPE_ID_URI:
                continue
            
            msgi, statusi = dbdf.processSampleDatafile(user_seek, sampleType, dfurl, diclist_assay)
            if not statusi:
                status = 0
                msg += msgi + ';'
                
        return msg, status
    
    
    def __verifyUID(self, uidIn, attributeInfo):
        isValid = True
        msg = ''
        sampletype = attributeInfo['sampletype']
        typetitle = sampletype['title']
        uid_prefix = typetitle
        if '_' in typetitle:
            terms = typetitle.split('_')
            uid_prefix = terms[0]
            
        uidIn_prefix = uidIn
        if '-' in uidIn:
            terms = uidIn.split('-')
            uidIn_prefix = terms[0]
        
        if uid_prefix.strip()!=uidIn_prefix.strip():
            msg = "Error: Sample UID " + uidIn + " does not match sample type: " + typetitle
            isValid = False
            return isValid, msg
        
        record = self.__retrieveSampleByUID(uidIn)
        if record is None:
            msg = "Error: Sample UID " + uidIn + " does not exist in DB for update "
            isValid = False
            return isValid, msg
        
        return isValid, msg

            
    def __storeSample(self, user_seek, sampleType, record, attributeInfo, diclist_assay, creator):
        username = user_seek['username']
        contributor_id = user_seek['user_id']
        
        creator_id = creator['user_id']
        project_id = creator['projectid']
        
        if not self.__notEmptyLine(record):
            msg = SAMPLE_ERRORCODE['501']
            return msg, 0, None
        
        headers_required = attributeInfo['headers_required']
        
        msg_required, meetRequired = self.__verifyRequiredFields(record, headers_required)
        if not meetRequired:
            msg = SAMPLE_ERRORCODE['502'] + msg_required
            return msg, 0, None
                
        if 'UID' not in record.keys():
            msg = SAMPLE_ERRORCODE['503']
            return msg, 0, None
        
        record_new, newSample = self.__getRecord(creator, record, attributeInfo, contributor_id)
        uid = record_new['uuid']
        msg, status, sample_id = self.storeOneRecord(username, record_new)
        logger.debug(f"Username {username} storing record {record_new}")
        if status:
            if newSample:
                self.__updateSampleProject(creator, sample_id)
                self.__updateSampleAssetsCreators(sample_id, creator_id)
                if len(diclist_assay)>0:
                    msgj, statusj = self.__storeSample_assay_asset(creator, sampleType, sample_id, diclist_assay)
                    if statusj==0:
                        msgj = SAMPLE_ERRORCODE['601'] + msgj
                        msg += ';' + msgj
                else:
                    msg = 'Info: Assay info not available for updating array-sample relationship for sample id: ' + str(sample_id)

                try:
                    self.storeSampleNeo4j(sampleType, record_new)
                except:
                    None
            else:
                msg = 'Info: No update on array-sample relationship for old sample id: ' + str(sample_id)
                    
            msgdf, statusdf = self.__setSampleDatafileAssociation(creator, sampleType, record, attributeInfo, diclist_assay)
            if not statusdf:
                msgdf = SAMPLE_ERRORCODE['602'] + msgdf
                msg += ';' + msgdf
        else:
            msg = SAMPLE_ERRORCODE['504'] + msg
        
        return msg, status, uid
    
    def __storeSample_assay_asset(self, user_seek, sampleType, sample_id, diclist_assay):
        username = user_seek['username']
        assay_assets = DBtable_assay_assets("DEFAULT")
        return assay_assets.storeSample_assay_asset(user_seek, sampleType, sample_id, diclist_assay)
        
    def __searchUniqueSample(self, samplename, scientist, sample_type_id):
        query = {}
        query['sample_type_id__exact'] = sample_type_id
        qset = Q(**query)
        
        query = {}
        query['json_metadata__icontains'] = scientist
        qset = qset & Q(**query)
        
        query = {}
        query['title__iexact'] = samplename
        qset = qset & Q(**query)
            
        records = self.queryRecordsCustom(qset)
        return records
        
    def __verifySampleUID(self, samplename, creator_id, uidIn, sample_type_id, scientist):
        samplename = str(samplename)
        records = self.__searchUniqueSample(samplename, scientist, sample_type_id)
        
        nr = len(records)
        status = 1
        msg = ''
        if nr==0:
            if uidIn is None or len(uidIn.strip())==0:
                status = 1
                msg = 'Okay: ready for store a new sample without predefined UID'
            else:
                status = 1
                msg = 'Okay: ready for store a new sample with predefined UID: ' + uidIn
        elif nr==1:
            record = records[0]
            uid_verified = record['uuid'] # we always use 'uuid' to store UID for a sample.
            if uidIn is None or len(uidIn.strip())==0:
                status = 0
                msg = SAMPLE_ERRORCODE['401'] + uid_verified
            elif uid_verified==uidIn:
                status = 1
                msg = 'Okay: Unique record exists based on ' + samplename
                msg += ', which is consistent with the UID ' + uidIn
            else:
                status = 0
                msg = SAMPLE_ERRORCODE['402'] + uidIn + ' ' + uid_verified
        else:
            status = 0
            msg = SAMPLE_ERRORCODE['403']
        return msg, status
    
    def __updateSampleErrorMsg(self, sampledic_feeback, primaryField, msg, sampleType):
        header = sampleType + "::UID"
        sampledic_feeback[header] = msg
        
        if primaryField in sampledic_feeback:
            value = sampledic_feeback[primaryField]
            if value is None or len(str(value))==0:
                sampledic_feeback[primaryField] = msg
            else:
                sampledic_feeback[primaryField] = value + ":" + msg
        else:
            sampledic_feeback[primaryField] = msg
            
        return sampledic_feeback
        
    def getConnectingRelationships(self, child_id, parent_id):
        db = settings.DATABASES[SEEK_DATABASE]
        nextseekdb = settings.DATABASES[NEXTSEEK_DATABASE]
        relationships = {
            "child_id": child_id,
            "parent_id": parent_id,
        }
        connecting_assay_query = f"""
            SELECT aa.assay_id, a.title
            FROM {db["NAME"]}.assay_assets aa
            JOIN {db["NAME"]}.assays a ON a.id = aa.assay_id
            WHERE
                aa.asset_type = 'Sample' AND
                (aa.asset_id = {child_id} OR aa.asset_id = {parent_id})
            GROUP BY aa.assay_id
            HAVING COUNT(aa.assay_id) = 2
        """
        connecting_assay_results = self.__runQuery(connecting_assay_query)

        if len(connecting_assay_results) != 0:
            connecting_assay_id, connecting_assay_title = connecting_assay_results[0]

            relationships["assay_id"] = connecting_assay_id
            relationships["assay_title"] = connecting_assay_title

            internal_assay_query = f"""
                SELECT ia.internal_assay_title
                FROM {nextseekdb["NAME"]}.internal_assays ia
                JOIN {nextseekdb["NAME"]}.assays_internal_assays aia ON aia.internal_assay_id = ia.id
                WHERE aia.assay_id = {connecting_assay_id}
            """

            internal_assay_results = self.__runQuery(internal_assay_query)

            if len(internal_assay_results) != 0:
                internal_assay_title = internal_assay_results[0][0]

                db = settings.DATABASES[SEEK_DATABASE]
                relationships["internal_assay_title"] = internal_assay_title

        protocol_id_substring = """
            SUBSTRING_INDEX(
                REPLACE(
                    JSON_EXTRACT(s.json_metadata, '$.Protocol'),
                    '"',
                    ''
                ),
                '/',
                -1
            )
        """
        
        connecting_sop_query = f"""
            SELECT
                sop.id AS sop_id,
                sop.title AS sop_title
            FROM {db["NAME"]}.samples s
            JOIN {db["NAME"]}.sops sop ON sop.id = {protocol_id_substring}
            WHERE s.id = {child_id}
        """

        connecting_sop_results = self.__runQuery(connecting_sop_query)
        
        if len(connecting_sop_results) != 0:
            sop_id, sop_title = connecting_sop_results[0]

            relationships["protocol_id"] = sop_id
            relationships["protocol_title"] = sop_title
            
        return relationships

    def extractParents(self, json_metadata):
        parents = []
        for k, v in json_metadata.items():
            if "Parent" in k:
                parents.append(v)
        parents = list(map(lambda p: p.split(";"), parents))
        parents = list(chain(*parents))
        parents = list(map(lambda p: p.strip(), parents))
        return parents

    def storeSampleNeo4j(self, sampleType, record):
        logger.debug(f"Storing sample into neo4j with info: {record}")
        sample_id = self.getSampleID(record['uuid'])
        json_metadata = json.loads(record['json_metadata'])
        parents = self.extractParents(json_metadata)
        
        with GraphDatabase.driver(NEO4J_DATABASE['URI'], auth=NEO4J_DATABASE['AUTH']) as driver:
            
            # Create the sample node
            driver.execute_query(
                    "MERGE (s:Sample {id: $sample_id, uuid: $sample_uuid, type: $sample_type})",
                    sample_id=sample_id,
                    sample_type=sampleType,
                    sample_uuid=record['uuid'],
                    database_=NEO4J_DATABASE['NAME'])

            # Assign it a sample type
            driver.execute_query(
                """
                    MATCH (s:Sample {id: $sample_id})
                    MATCH (st:SampleType {title: $sample_type})
                    MERGE (s)-[:OF_TYPE]->(st)
                """,
                sample_id=sample_id,
                sample_type=sampleType,
                database_=NEO4J_DATABASE['NAME'])

            # Create relationships between sample nodes
            if len(parents) > 0:
                for parent in parents:
                    parent_id = self.getSampleID(parent)
                    relationships = self.getConnectingRelationships(sample_id, parent_id)
                    driver.execute_query("""
                                MATCH (child:Sample {id: $child_id})
                                MATCH (parent:Sample {id: $parent_id})
                                MERGE (child)-[r:DERIVED_FROM]->(parent)
                                SET r+= $rels""",
                                child_id=sample_id,
                                parent_id=parent_id,
                                rels=relationships,
                                database_=NEO4J_DATABASE['NAME'])

    def getChildrenUIDs(self, sample_uids, user_project_ids, admin):
        db = settings.DATABASES[SEEK_DATABASE]
        NEO4J_DATABASE = settings.NEO4J_DATABASE
        with GraphDatabase.driver(NEO4J_DATABASE['URI'], auth=NEO4J_DATABASE['AUTH']) as driver:
            r,s,k = driver.execute_query("""
    		UNWIND $sample_uids AS sample_uid
            MATCH (s:Sample {uuid: sample_uid})
            MATCH parents=(s)-[:DERIVED_FROM*0..]->(parent)
            MATCH children=(s)<-[:DERIVED_FROM*0..]-(child)
            RETURN collect(DISTINCT s.uuid) + collect(DISTINCT parent.uuid) + collect(DISTINCT child.uuid) AS uuids
            """,
            sample_uids=sample_uids,
            database_=NEO4J_DATABASE['NAME'])
            uids = r[0]['uuids']

        # No Sample nodes matched the requested UIDs (e.g. data-file UIDs absent
        # from the graph, or UIDs not present in this database). Return an empty
        # frame rather than building invalid ``WHERE uuid IN ()`` SQL — which
        # __runQuery swallows into None, crashing the caller with
        # "cannot unpack non-iterable NoneType object" (a 500). The view treats
        # an empty frame as a clean 404 "No samples found".
        if not uids:
            return pd.DataFrame(columns=["id", "sample_type_id", "uuid", "json_metadata"])

        uids_str = ', '.join(f"'{uid}'" for uid in uids)
        project_ids_str = ', '.join(f"'{pid}'" for pid in user_project_ids)

        if admin:
            query = f"""
            SELECT id,sample_type_id,uuid,json_metadata
            FROM {db["NAME"]}.samples
            WHERE uuid IN ({uids_str})
            """
        else:
            query = f"""
            SELECT s.id, s.sample_type_id, s.uuid, s.json_metadata
            FROM {db["NAME"]}.samples s
            JOIN {db["NAME"]}.projects_samples ps
            ON s.id = ps.sample_id
            WHERE s.uuid IN ({uids_str}) AND ps.sample_id = s.id AND ps.project_id IN ({project_ids_str})
            """

        result = self.__runQuery(query, withColumns=True)
        if not result:
            # Query failed (e.g. transient DB error). Degrade to an empty frame
            # so the endpoint returns a clean 404 instead of a 500 unpack crash.
            return pd.DataFrame(columns=["id", "sample_type_id", "uuid", "json_metadata"])
        rows, columns = result
        samples_retrieved_df = pd.DataFrame(rows, columns=columns)

        return samples_retrieved_df

    def __parse_json_metadata(self, metadata_series):
        return metadata_series.apply(lambda x: json.loads(x) if isinstance(x, str) else {})

    def __parse_children_uids(self, children_uids):
        children_uids['json_metadata'] = self.__parse_json_metadata(children_uids['json_metadata'])

        metadata_df = pd.json_normalize(children_uids['json_metadata'])
        metadata_df = metadata_df.loc[:, ~metadata_df.columns.duplicated()]

        final_df = pd.concat([children_uids[['uuid']], metadata_df], axis=1)
        final_df.replace("", pd.NA, inplace=True)
        final_df.dropna(axis=1, how='all', inplace=True)

        return final_df

    def sampleRetrievalData(self, children_uids, output):
        # Sheet layout, README included, is owned by
        # nextseek_api.services.sample_workbook so it cannot drift per call path.
        write_samples_workbook(self.__parse_children_uids(children_uids), output)

    def __batchUploadTest(self, seekdb, sampleType, diclist, diclist_feedback, attributeInfo, attributeMapping, diclist_assay, uploadEnforced=False):
        user_seek = seekdb.user_seek
        username = user_seek['username']
        user_id = user_seek['user_id']
        contributor_id = user_id
        creator = seekdb.creator
        creator_id = creator['user_id']
        msg0 = '<br/>'
        nright = 0
        nrow = 0
        statusTest = True
        diclist_new = []
        ndici = len(diclist)
        uids_predefined = {}
        
        for index in range(ndici):
            dici = diclist[index]
            if len(diclist_feedback)>0:
                dici_feedback = diclist_feedback[index]
                
                findParentUID = True
                for field, attribute in attributeMapping.items():
                    if DELIMITER_DBFIELD in field and field in dici_feedback:
                        uid_from = dici_feedback[field]
                        if "Error" in uid_from:
                            findParentUID = False
                        else:
                            dici[attribute] = uid_from
                if not findParentUID:
                    msgi = SAMPLE_ERRORCODE['301']
                    statusTest = False
                    msg0 += msgi +  '<br/>'
                
                    header = sampleType + "::UID"
                    dici_feedback[header] = msgi
                    diclist_new.append(dici_feedback)
                    continue
            else:
                dici_feedback = {}
                
            primaryField = sampleType + "::UID" 
            for header, attribute in attributeMapping.items():
                if attribute in dici:
                    dici_feedback[header] = dici[attribute]
                else:
                    dici_feedback[header] = ''
                    
                if attribute=='UID':
                    primaryField = header
                    
            if 'Name' in dici:
                samplename = str(dici['Name'])
            elif 'File_PrimaryData' in dici:
                samplename = str(dici['File_PrimaryData'])
            elif 'File_PrimaryData_Forward' in dici:
                samplename = str(dici['File_PrimaryData_Forward'])
            elif 'File_PrimaryData_Reverse' in dici:
                samplename = str(dici['File_PrimaryData_Reverse'])
            else:
                msgi = SAMPLE_ERRORCODE['302'] + sampleType + " " + str(index)
                statusTest = False
                msg0 += msgi +  '<br/>'
                
                dici_feedback = self.__updateSampleErrorMsg(dici_feedback, primaryField, msgi, sampleType)
                diclist_new.append(dici_feedback)
                continue
            
            if samplename is None:
                cleanname = ''
            else:
                cleanname = str(samplename)
            cleanname = cleanname.strip()
            if len(cleanname)==0:
                msgi = SAMPLE_ERRORCODE['303']
                statusTest = False
                msg0 += msgi +  '<br/>'
                
                dici_feedback = self.__updateSampleErrorMsg(dici_feedback, primaryField, msgi, sampleType)
                diclist_new.append(dici_feedback)
                continue
            
            if SAMPLE_CONTRIBUTOR_ACCESSOR_NAME in dici:
                scientist = str(dici[SAMPLE_CONTRIBUTOR_ACCESSOR_NAME])
            elif SAMPLE_CONTRIBUTOR_ACCESSOR_NAME in dici:
                scientist = str(dici[SAMPLE_CONTRIBUTOR_ACCESSOR_NAME])
            else:
                msgi = SAMPLE_ERRORCODE['304']
                statusTest = False
                msg0 += msgi +  '<br/>'
                dici_feedback = self.__updateSampleErrorMsg(dici_feedback, primaryField, msgi, sampleType)
                diclist_new.append(dici_feedback)
                continue
            
            if contributor_id<=0:
                msgi = SAMPLE_ERRORCODE['305'] + contributor
                statusTest = False
                msg0 += msgi +  '<br/>'
                dici_feedback = self.__updateSampleErrorMsg(dici_feedback, primaryField, msgi, sampleType)
                diclist_new.append(dici_feedback)
                continue
            

            if samplename in uids_predefined:
                dici['UID'] = uids_predefined[samplename]
            else:
                sample_type_id = attributeInfo['sampleType_id']
                uidIn = None
                if 'UID' in dici:
                    uidIn = dici['UID']
                msgi, statusi = self.__verifySampleUID(samplename, creator_id, uidIn, sample_type_id, scientist)
                if statusi==0:
                    statusTest = False
                    msg0 += msgi +  '<br/>'
                    dici_feedback = self.__updateSampleErrorMsg(dici_feedback, primaryField, msgi, sampleType)
                    diclist_new.append(dici_feedback)
                    continue
                
                if uidIn is not None and len(uidIn.strip())>0:
                    isValid, msgi = self.__verifyUID(uidIn, attributeInfo)
                    if not isValid:
                        statusTest = False
                        msg0 += msgi +  '<br/>'
                        dici_feedback = self.__updateSampleErrorMsg(dici_feedback, primaryField, msgi, sampleType)
                        diclist_new.append(dici_feedback)
                        continue
            
            msgi, statusi, uid = self.__storeSample(user_seek, sampleType, dici, attributeInfo, diclist_assay, creator)
                
            nrow += 1
            if statusi:
                msg0 += str(samplename) + ": " + msgi +  '<br/>'
                nright += 1
                if samplename not in uids_predefined:
                    uids_predefined[samplename] = uid
                #with ThreadPoolExecutor() as executor:
                #    sample_id = self.getSampleID(uid)
                #    executor.submit(updateTrees, sample_id)
            else:
                statusTest = False
                msg0 += msgi +  '<br/>'
                dici_feedback = self.__updateSampleErrorMsg(dici_feedback, primaryField, msgi, sampleType)
                
            header = sampleType + "::UID"
            if statusi:
                dici_feedback[header] = uid
                dici_feedback[primaryField] = uid
            else:
                dici_feedback[header] = msgi
                
            try:
                self.storeSampleNeo4j(sampleType, dici_feedback)
            except:
                None

            diclist_new.append(dici_feedback)
                
        msg = 'The number of samples uploaded for ' + sampleType + ': ' + str(nright) + ' out of in total ' + str(ndici) + ' samples.'
        if not statusTest:
            msg = msg + '<br/>' + msg0
        else:
            msg = msg + '<br/>' + msg0
        
        return msg, statusTest, diclist_new
    
    def __batchUploadSampleTest(self, seekdb, sampleType, diclist_sample, diclist_feedback, attributeMapping, diclist_assay, uploadEnforced=False):
        user_seek = seekdb.user_seek
        username = user_seek['username']
        stype = DBtable_sampletype()
        sampletype_id = stype.getSampleTypeID(sampleType)
        if sampletype_id<=0:
            msg = SAMPLE_ERRORCODE['201'] + sampleType + " id: " + str(sampletype_id)
            return msg, 0, diclist_feedback
    
        sattr = DBtable_sampleattribute()
        attributeInfo = sattr.getAttributeInfo(sampletype_id)
        if len(attributeInfo['headers'])==0:
            msg = SAMPLE_ERRORCODE['202'] + sampleType
            status = 0
            return msg, status,diclist_feedback
        
        msg, status, diclist_feedback = self.__batchUploadTest(seekdb, sampleType, diclist_sample, diclist_feedback, attributeInfo, attributeMapping, diclist_assay, uploadEnforced)
        return msg, status, diclist_feedback


    def __outputUploadFeedback_V2(self, diclist, diclist_feedback, headers, feedbackfile):
        book = xlwt.Workbook(encoding="utf-8")
        sheet1 = book.add_sheet("Samples")
        row = 0
        for index, header in enumerate(headers):
            try:
                newitem = toString(header)
            except:
                newitem = cleanString(header)
            sheet1.write(row, index, newitem)
        
        style = xlwt.easyxf('pattern: pattern solid, fore_colour red;')
        style0 = xlwt.Style.easyxf('pattern: pattern solid, fore_colour white;')
        i = 0
        for dici in diclist:
            dici_feedback = diclist_feedback[i]
            row += 1
            for index, header in enumerate(headers):
                if header in dici:
                    newitem = dici[header]
                else:
                    newitem = ""
                
                try:
                    newitem = str(newitem)
                except:
                    newitem = toString(newitem)
                
                if header in dici_feedback:
                    feedback = dici_feedback[header]
                    if feedback is None:
                        sheet1.write(row, index, newitem)
                    elif 'Error' in toString(feedback):
                        feedback = feedback + ":" + newitem
                        sheet1.write(row, index, feedback, style)
                    elif 'Warning' in toString(feedback):
                        feedback = feedback + ":" + newitem
                        sheet1.write(row, index, feedback, style)
                    elif 'UID' in header:
                        sheet1.write(row, index, feedback)
                    else:
                        sheet1.write(row, index, newitem)
                else:
                    sheet1.write(row, index, newitem)
                
            i += 1 
        book.save(feedbackfile)        
        
    def __verifyUpdateSample(self, sheetData, feedbackfile):
        #logger.debug('verifyUpdateSample')
        status = 1
        msg = "Okay"
        headers = sheetData['headers']
        diclist = sheetData['diclist']
        if len(diclist)<1:
            msg = SAMPLE_ERRORCODE['104']
            status = 0
            logger.debug(msg)
            return msg, status, None
        
        if 'UID' not in headers and 'uid' not in headers:
            msg = 'UID column not available in the sheet for update'
            status = 0
            logger.debug(msg)
            return msg, status, None
        
        sampleTypes_order = []
        for dici in diclist:
            if 'UID' in dici:
                uid = dici['UID']
            else:
                uid = dici['uid']
            terms = uid.split('-')
            sampleType = terms[0]
            if sampleType not in sampleTypes_order:
                sampleTypes_order.append(sampleType)
            
        if len(sampleTypes_order)==0:
            msg = 'Sample type not available in the sheet for update'
            status = 0
            logger.debug(msg)
            return msg, status, None
        
        if len(sampleTypes_order)>1:
            msg = 'Only one sample type should be included in the sheet for update'
            status = 0
            logger.debug(msg)
            return msg, status, None
        
        sampleType = sampleTypes_order[0]
        stype = DBtable_sampletype()
        sampletype_id = stype.getSampleTypeID(sampleType)
        if sampletype_id<=0:
            msg = SAMPLE_ERRORCODE['201'] + sampleType + " id: " + str(sampletype_id)
            logger.debug(msg)
            return msg, 0, None
    
        sattr = DBtable_sampleattribute()
        attributeInfo = sattr.getAttributeInfo(sampletype_id)
        if len(attributeInfo['headers'])==0:
            msg = SAMPLE_ERRORCODE['202'] + sampleType
            status = 0
            logger.debug(msg)
            return msg, status, None
        
        attributes = attributeInfo['headers']
        msg = 'The following column(s) are not in sample attributes thus must be removed from the sheet before further processing:<br/><br/>'
        headers_error = []
        for header in headers:
            if header not in attributes:
                msg += header +  '<br/>'
                headers_error.append(header)
                status = 0
        
        if status==0:
            diclist_sanity = []
            for dici in diclist:
                for header in headers_error:
                    dici[header] = 'Error:' + dici[header]
                diclist_sanity.append(dici)
            self.__outputUploadFeedback_V2(diclist, diclist_sanity, headers, feedbackfile)
        
        #logger.debug('verifyUpdateSample: Finish')
        return msg, status, attributes
        
    def __batchUpdateSample(self, sheetData, feedbackfile, user_seek):
        #logger.debug('batchUpdateSample')
        username = user_seek['username']
        msg = "batchUpdate"
        status = 0
        
        headers = sheetData['headers']
        diclist = sheetData['diclist']
        if len(diclist)<1:
            msg = SAMPLE_ERRORCODE['104']
            status = 0
            logger.debug(msg)
            return msg, status
        
        msg, status, attributes = self.__verifyUpdateSample(sheetData, feedbackfile)
        if status==0:
            logger.debug(msg)
            return msg, status
        
        headers.append('feedback')
        username = user_seek['username']
        user_id = user_seek['user_id']

        msg0 = '<br/>'
        nright = 0
        nrow = 0
        statusTest = True
        ndici = len(diclist)
        diclist_feedback = []
        for index in range(ndici):
            dici = diclist[index]
            dici_feedback = {}
            
            for key, elem in dici.items():
                dici_feedback[key] = elem
                
            msgi, statusi = self.updateSingleSample(dici, username, attributes)
            nrow += 1
            if statusi:
                msg0 += str(nrow) + ": " + msgi +  '<br/>'
                nright += 1
                dici_feedback['feedback'] = 'successful'
            else:
                logger.debug(msgi)
                statusTest = False
                msg0 += msgi +  '<br/>'
                dici_feedback['feedback'] = msgi

            diclist_feedback.append(dici_feedback)
                
        msg = 'The number of samples updated: ' + str(nright) + ' out of in total ' + str(ndici) + ' samples.'
        if not statusTest:
            msg = msg + '<br/>' + msg0
            status = 0
        else:
            msg = msg + '<br/>' + msg0
            status = 1
                
        self.__outputUploadFeedback_V2(diclist, diclist_feedback, headers, feedbackfile)
        #logger.debug(feedbackfile)
        #logger.debug('batchUpdateSample: Finish')
        return msg, status
    
    
    def __batchUpdateSampleAssociation(self, sheetData, feedbackfile, user_seek):
        username = user_seek['username']
        msg = "batchUpdate sample-assay association"
        status = 0
        
        headers = sheetData['headers']
        headers_required = ["Sample UID","Current Assay ID","Current Assay Direction","New Assay ID","New Assay Direction"]
        missing = False
        missed = ''
        for header in headers_required:
            if header not in headers:
                missing = True
                missed += header + ' '
        if missing:
            msg = SAMPLE_ERRORCODE['701'] + ' with missing columns ' + missed
            status = 0
            return msg, status
        
        diclist = sheetData['diclist']
        if len(diclist)<1:
            msg = SAMPLE_ERRORCODE['701'] + ' with no content'
            status = 0
            return msg, status
        
        headers.append('Feedback')
        username = user_seek['username']
        user_id = user_seek['user_id']
        assay_assets = DBtable_assay_assets("DEFAULT")

        msg0 = '<br/>'
        nright = 0
        nrow = 0
        statusTest = True
        ndici = len(diclist)
        diclist_feedback = []
        for index in range(ndici):
            dici = diclist[index]
            dici_feedback = {}
            
            for key, elem in dici.items():
                dici_feedback[key] = elem
                
            uid = dici['Sample UID']
            sample_id = self.getSampleID(uid)
            if sample_id>0:
                msgi, statusi = assay_assets.updateSample_assay_asset(user_seek, sample_id, dici)
            else:
                msgi ='Warning: Sample UID not found: ' + uid
                statusi = 0
            nrow += 1
            if statusi:
                msg0 += uid + ": " + msgi +  '<br/>'
                nright += 1
                dici_feedback['Feedback'] = 'successful: ' + msgi
            else:
                statusTest = False
                msg0 += msgi +  '<br/>'
                dici_feedback['Feedback'] = msgi

            diclist_feedback.append(dici_feedback)
                
        msg = 'The number of samples updated: ' + str(nright) + ' out of in total ' + str(ndici) + ' samples.'
        if not statusTest:
            msg = msg + '<br/>' + msg0
            status = 0
        else:
            msg = msg + '<br/>' + msg0
            status = 1
                
        self.__outputUploadFeedback_V2(diclist, diclist_feedback, headers, feedbackfile)
        return msg, status
    
    
    def __runSanityCheck(self, diclist, diclist_ins, diclist_ont):
        status = 1
        msg = ''
        diclist_sanity = []
        headermapping = {}
        for dici in diclist_ins:
            if 'Field' not in dici:
                msg += 'Error: Instructions sheet must have a "Field" column \n'
                status = 0
            
            if 'Field Type' not in dici:
                msg += 'Error: Instruction sheet must have a "Field Type" column \n'
                status = 0
                
            if status==0:
                return msg, status, diclist_sanity
            
            header = dici['Field']
            fieldtype = dici['Field Type']
            headermapping[header] = fieldtype
        
        for dici in diclist:
            sanity_error = {}
            for header, value in dici.items():
                if header not in headermapping:
                    msgi = "Warning: header not defined in the Instructions sheet: " + header
                    sanity_error[header] = msgi
                    msg += msgi + '\n'
                else:
                    valuetype = headermapping[header]
                    isRightType = verifyValueType(valuetype, value)
                    if isRightType:
                        sanity_error[header] = value
                    else:
                        valueStr = toString(value)
                        valueStr = valueStr.strip().upper()
                        if len(valueStr)==0:
                            # allow empty value 
                            sanity_error[header] = value    
                        else:
                            status = 0
                            msgi = "Error: value not in the expected " + valuetype + " type: " + toString(value)
                            msg += msgi + '\n'
                            sanity_error[header] = msgi
                                
            diclist_sanity.append(sanity_error)
        return msg, status, diclist_sanity
        
        
    def batchUpload(self, infile, feedbackfile, seekdb):
        user_seek = seekdb.user_seek
        username = user_seek['username']
        msg = "batchUpload"
        #logger.debug(msg)
        status = 0
        try:
            filedata = load_excelfile_asdic(infile)
        except:
            msg = SAMPLE_ERRORCODE['101']
            status = 0
            logger.debug(msg)
            return msg, status
        
        status = filedata['status']
        msg = filedata['msg']
        if status==0:
            msg = SAMPLE_ERRORCODE['106'] + msg
            logger.debug(msg)
            return msg, status
        
        if 'UPDATE' in filedata['sheetnames'] and 'UPDATE' in filedata:
            sheetData = filedata['UPDATE']
            if "ONTOLOGY" not in filedata or "INSTRUCTIONS" not in filedata:
                return self.__batchUpdateSample(sheetData, feedbackfile, user_seek)
            
            sheetData_ont = filedata["ONTOLOGY"]
            diclist_ont = sheetData_ont['diclist']
            
            sheetData_ins = filedata["INSTRUCTIONS"]
            diclist_ins = sheetData_ins['diclist']
            
            diclist_up = sheetData['diclist']
            headers = sheetData['headers']
            ontology = DBtable_ontology()
            msg, status, ontology_feedback = ontology.evaluateOntology(diclist_up, diclist_ins, diclist_ont)
            if status==0:
                if len(ontology_feedback)==0:
                    return msg, status
                else:
                    msg = 'Error: Refer to the feedback excel file for vialation in controlled Ontology terms.'
                    ontology.outputOntologyFeedback(diclist_up, headers, feedbackfile, ontology_feedback)     
                    return msg, status
            
            return self.__batchUpdateSample(sheetData, feedbackfile, user_seek)
        
        elif 'UPDATE_ASSAY' in filedata['sheetnames'] and 'UPDATE_ASSAY' in filedata:
            sheetData = filedata['UPDATE_ASSAY']
            return self.__batchUpdateSampleAssociation(sheetData, feedbackfile, user_seek)
        
        status = 1
        for sheetname in SAMPLE_SHEET_NAMES:
            msg = SAMPLE_ERRORCODE['102']
            if sheetname not in filedata['sheetnames'] or sheetname not in filedata:
                status = 0
                msg += sheetname + ';'
            
            if status==0:
                return msg, status
        
        sheetData_ins = filedata["INSTRUCTIONS"]
        diclist_ins = sheetData_ins['diclist']
        sampleTypes, sampleTypes_order = self.__loadSampleTypes(diclist_ins)
        if len(sampleTypes.keys())==0:
            msg = SAMPLE_ERRORCODE['103']
            status = 0
            return msg, status
        
        sheetData_assay = filedata["ASSAY"]
        diclist_assay = sheetData_assay['diclist']
        sheetData = filedata["SAMPLES"]
        headers = sheetData['headers']
        diclist = sheetData['diclist']
        if len(diclist)<1:
            msg = SAMPLE_ERRORCODE['104']
            status = 0
            return msg, status
        
        sheetData_ont = filedata["ONTOLOGY"]
        diclist_ont = sheetData_ont['diclist']
        
        ontology = DBtable_ontology()
        msg, status, ontology_feedback = ontology.evaluateOntology(diclist, diclist_ins, diclist_ont)
        if status==0:
            if len(ontology_feedback)==0:
                return msg, status
            else:
                msg = 'Error: Refer to the feedback excel file for violation in controlled Ontology terms.'
                ontology.outputOntologyFeedback(diclist, headers, feedbackfile, ontology_feedback)     
                return msg, status
        
        
        msg, status, diclist_sanity = self.__runSanityCheck(diclist, diclist_ins, diclist_ont)
        if status==0:
            msg = 'Error: Refer to the feedback excel file for any error.'
            self.__outputUploadFeedback_V2(diclist, diclist_sanity, headers, feedbackfile)
            return msg, status
        
        sample_sheets = self.__splitSampleTypes(sampleTypes, diclist)
        
        msg = ""
        diclist_feedback = []
        for sampleType in sampleTypes_order:
            if sampleType in sample_sheets:
                diclist_sample = sample_sheets[sampleType]
                attributeMapping = sampleTypes[sampleType]

                msgi, statusi, diclist_feedback = self.__batchUploadSampleTest(seekdb, sampleType, diclist_sample, diclist_feedback, attributeMapping, diclist_assay)                
                msg += msgi + "<br/>"
                if not statusi:
                    status = 0
            else:
                msgi = SAMPLE_ERRORCODE['105'] + sampleType
                status = 0
                msg += msgi + "<br/>"
                
        self.__outputUploadFeedback_V2(diclist, diclist_feedback, headers, feedbackfile)
        return msg, status
    
    def __retrieveSampleByUID(self, uid):
        record = None
        if uid is None or len(uid.strip())==0:
            msg = 'No record is found based on the input UID: ' + uid
            return None
        
        constraint = {'uuid':uid}
        records = self.queryRecordsByConstraint(constraint)
        if len(records)==1:
            record = records[0]
        
        return record
    
    def getSampleID(self, uid):
        sample_id = None
        record = self.__retrieveSampleByUID(uid)
        if record is not None:
            sample_id = record['id']
            
        return sample_id
        
 
    def __retrieveSampleByID(self, idIn):
        record = None
        try:
            id = int(idIn)
        except:
            msg = 'No record is found based on the input ID: ' + idIn
            return None
        
        if id<=0:
            msg = 'No record is found based on the input ID: ' + idIn
            return None
        
        constraint = {'id':id}
        records = self.queryRecordsByConstraint(constraint)
        if len(records)==1:
            record = records[0]
        
        return record           
        
    def __getSeeklink(self, seek_type, id):
        seek_url = settings.SEEK_PUBLIC_URL + "/" + seek_type + "/" + str(id) + "/"
        seeklink = '<a href="' + seek_url + '" target="_blank">' + str(id) + '</a>'
        return seeklink
        
    def __getSamplelink(self, sample_uid, sample_id):
        sample_url = "/seek/sample/id=" + str(sample_id) + "/"
        # The uid column is rendered as raw HTML by the client; escape the uid
        # text so a sample uid containing markup cannot inject script.
        samplelink = '<a href="' + sample_url + '" target="_blank">' + html.escape(str(sample_uid)) + '</a>'
        return samplelink
    
        
    def reformatDataForClient(self, jdata):
        jdata_new = []
        for data in jdata:
            datadic = {}
            datadic['idlink'] = self.__getSeeklink('samples', data['id'])
            datadic['idurl'] = self.__getSamplelink(data['id'], data['id'])
            datadic['id'] = data['id']
            datadic['title'] = data['title']
            datadic['uuid'] = data['uid']
            datadic['uid'] = self.__getSamplelink(data['uid'], data['id'])
            datadic['sample_type_id'] = data['sample_type_id']
            datadic['contributor_id'] = data['contributor_id']
            datadic['created_at'] = str(data['created_at'])
            datadic['json_metadata'] = toString(data['json_metadata'])
            datadic['sample_type'] = data['sample_type']
            datadic['first_name'] = data['first_name']
            datadic['assays'] = data['assays']
            
            jdata_new.append(datadic)
        
        jdata_new = sorted(jdata_new, key=operator.itemgetter('id'))
        
        return jdata_new
    
    def __sqlQuery_select_records_select(self):
        sqlquery_select =  " SELECT "
        sqlquery_select +=  "A.id as id,"
        sqlquery_select +=  "A.title as title,"
        sqlquery_select +=  "A.sample_type_id as sample_type_id,"
        sqlquery_select +=  "B.title as sample_type,"
        sqlquery_select +=  "A.uuid as uid,"
        sqlquery_select +=  "A.contributor_id as contributor_id,"
        sqlquery_select +=  "C.first_name as first_name,"
        sqlquery_select +=  "A.created_at as created_at,"
        sqlquery_select +=  "A.json_metadata as json_metadata,"
        
        sqlquery_select +=  "("
        sqlquery_select +=  "SELECT GROUP_CONCAT(E.title) as assays "
        sqlquery_select +=  "FROM assay_assets D "
        sqlquery_select +=  "left join assays E on E.id=D.assay_id "
        sqlquery_select +=  "WHERE A.id=D.asset_id AND D.asset_type='Sample' "
        sqlquery_select +=  ") "
        return sqlquery_select
    
    def __sqlQuery_select_records_from(self, projectID=None):
        sqlquery_from =  " FROM "   
        sqlquery_from +=  "samples A "
        sqlquery_from +=  "left join sample_types B on A.sample_type_id=B.id "
        sqlquery_from +=  "left join people C on A.contributor_id=C.id "
        if projectID is not None and projectID!=0:
            sqlquery_from +=  "left join projects_samples D on D.sample_id=A.id "
        
        return sqlquery_from     
    
    def __sqlQuery_select_records(self, filtersdic, withLimit=True):
        # The query that this generates does not return all items that it should
        # in the simple search page
        sqlquery_select = self.__sqlQuery_select_records_select()
        project_id = filtersdic['project_id']
        sqlquery_from = self.__sqlQuery_select_records_from(project_id)
        orderby = filtersdic['orderby'] 
        startNo = filtersdic['startNo'] 
        endNo = filtersdic['endNo']
        sqlquery_where = self.__sqlQuery_select_records_filters_advanced(filtersdic)
        sqlqueryMega = sqlquery_select + sqlquery_from + sqlquery_where
        if len(orderby)==0:
            orderby = " ORDER BY A.id desc"
        if withLimit:
            sqlqueryMega = sqlquery_select + sqlquery_from + sqlquery_where + orderby
        else:
            sqlqueryMega = " SELECT count(A.id) " + sqlquery_from
        logger.debug(sqlqueryMega)
        return sqlqueryMega

    def __getParentUIDs(self, sampleDic):
        uids = []
        for key, value in sampleDic.items():
            if SAMPLE_PARENT_ACCESSOR_NAME in key:
                if value is None:
                    continue
                else:
                    if ";" in value:
                        vis = value.split(";")
                        for vi in vis:
                            vi = vi.strip()
                            if len(vi)>0:
                                uids.append(vi)
                    else:
                        value = value.strip()
                        if len(value)>0:
                            uids.append(value)
                
        return uids
    
    def __getParents(self, childuid):
        record_db = self.__retrieveSampleByUID(childuid)
        if record_db is None:
            return []
            
        metadata = record_db['json_metadata']
        sampleDic = self.__getRecordFromJson(metadata)
        uids = self.__getParentUIDs(sampleDic)
        return uids

    def __filterSamples(self, jdata, sampletype_id, attribute, filter_rule, filter_valueFrom, filter_valueTo):
        logger.debug('filterSamples')
        
        if filter_rule=='No Filter':
            return jdata
        
        accessor_name = attribute.strip()
        
        values = []     
        parentUIDs = []    
        n = 0
        for data in jdata:
            json_metadata = data['json_metadata']
            dici = self.__getRecordFromJson(json_metadata)
            
            if accessor_name not in dici:
                value = None
            else:
                value = dici[accessor_name]
            values.append(value)
            
            #uids = self.__getParentUIDs(dici)
            #parentUIDs.append(uids)
            
            n += 1
            
        sattr = DBtable_sampleattribute()
        passvalues = sattr.filterValues(values, sampletype_id, attribute, filter_rule, filter_valueFrom, filter_valueTo)
        
        jdata_new = []
        parentUIDs_new = [] 
        index = 0
        ni = 0
        nf = 0
        for data in jdata:
            passit = passvalues[index]
            if passit:
                json_metadata = data['json_metadata']
                dici = self.__getRecordFromJson(json_metadata)
                attributeValue = self.__highlightKeyValues(dici, None, None, accessor_name)
                if len(attributeValue)==0:
                    nf += 1
                    continue
                
                data['attributeValue'] = attributeValue
                
                #childuid = data['uid']
                #uids = parentUIDs[index]
                #parentinfo, treeData = self.__trackParent(childuid, uids)
                #data['parent_uids'] = ';'.join(uids)
                
                jdata_new.append(data)
                ni += 1
            index += 1
        
        print("Total number of samples retrieved: %d"%index)
        print("Total number of samples passing filter: %d"%ni)
        print("Total number of samples passing filter but not highlighted: %d"%nf)

        logger.debug("Total number of samples retrieved: %d"%index)
        logger.debug("Total number of samples passing filter: %d"%ni)
        logger.debug("Total number of samples passing filter but not highlighted: %d"%nf)
        return jdata_new
    
    def __initSearchFilters(self, searchType, sampletype_id, project_id=0):
        filtersdic = {}
        filtersdic['orderby'] = " ";
        filtersdic['limit'] = " ";
        filtersdic['suffix'] = " ";
        filtersdic['startNo'] = " "
        filtersdic['endNo'] = " "
        filtersdic['sqlquery_filter'] = " "
        filtersdic['project_id'] = project_id
        if sampletype_id is None:
            filterRules = []
        else:
            filterRules = [{
                "field":"sample_type_id",
                "op":"equal",
                "value":sampletype_id
            }]
        
        filtersdic['tableField'] = 'json_metadata'
        filtersdic['categoryField'] = 'sample_type_id'
        filtersdic['filterRules'] = filterRules
        filtersdic['searchType'] = searchType
        return filtersdic
    
    def __retrieveSamplesInType(self, user_seek, sampletype_id, project_id=0):
        searchType = 'FILTERING'
        filtersdic = self.__initSearchFilters(searchType, sampletype_id, project_id)
        data = self.__retrieveRecords_advanced(user_seek, filtersdic)
        return data
    
    def __getChildrenUIDs(self, currentuid):
        records = self.db.retrieveRecords(self.tablemodel, 'json_metadata', currentuid)
        uids = []
        for record in records:
            uid = record['uuid']
            metadata = record['json_metadata']
            sampleDic = self.__getRecordFromJson(metadata)
            parent_uids = self.__getParentUIDs(sampleDic)
            if currentuid in parent_uids:
                uids.append(uid)
            
        return uids
    
    def __getChildLoop(self, parentuid):
        child = {}
        child["name"] = str(parentuid)
        child["id"] = str(parentuid)
        child_uids = self.__getChildrenUIDs(parentuid)
        if len(child_uids)==0:
            return child
        
        next_children = []
        for uid in child_uids:
            next_child = self.__getChildLoop(uid)
            next_children.append(next_child)
        
        if len(next_children)>0:
            child["children"] = next_children
        return child
    
    def createSampleChildrenTree(self, sample_id):
        return self.createSampleChildrenTreeParallel(sample_id)
        
    def createSampleChildrenTreeParallel_i(self, uid):
        child = self.__getChildLoop(uid)
        return child
    
    def createSampleChildrenTreeParallel(self, sample_id):
        record = self.__retrieveSampleByID(sample_id)
        if record is None:
            return None
        
        currentuid = record['uuid']
        children_uids =  self.__getChildrenUIDs(currentuid)
        
        treeData = {}
        treeData["name"] = str(currentuid)
        treeData["id"] = str(currentuid)
        
        children = []
        num_cores = multiprocessing.cpu_count()
        n = len(children_uids)
        childs = Parallel(n_jobs=-2, backend="threading")\
            (delayed(unwrap_self_createSampleChildrenTreeParallel_i)(i) for i in zip([self]*n, children_uids))

        for child in childs:
            children.append(child)

        if len(children)>0:
            treeData["children"] = children
        return treeData
    
    
    def __getParentTreeListLoop(self, childNode):
        upTreeList = []
        childuid = childNode['id']
        parent_uids = self.__getParents(childuid)
        if parent_uids is None or len(parent_uids)==0:
            upTreeList.append(childNode)
            return upTreeList
        
        for uid in parent_uids:
            uid = str(uid)
            node = {'id':uid, 'name':uid, 'children':[childNode]}
            parentTreeList = self.__getParentTreeListLoop(node)
            upTreeList += parentTreeList
            
        return upTreeList
    
    def __createMultiParentTree(self, sample_id, includeChilren, childrenTreeIn=None):
        return self.__createMultiParentTreeParallel(sample_id, includeChilren, childrenTreeIn)
        
    def createMultiParentTreeParallel_i(self, uid, child):
        uid = str(uid)
        childNode = {'id':uid, 'name':uid, 'children':[child]}
        parentTreeList = self.__getParentTreeListLoop(childNode)
        return parentTreeList
    
    def __createMultiParentTreeParallel(self, sample_id, includeChilren, childrenTreeIn=None):
        record = self.__retrieveSampleByID(sample_id)
        if record is None:
            return None, None
        
        childuid = record['uuid']
        json_metadata = record['json_metadata']
        dici = self.__getRecordFromJson(json_metadata)
        parent_uids =  self.__getParentUIDs(dici)
        # Parent uids aren't being found
        
        childuid = str(childuid)
        child = {'id':childuid, 'name':childuid}
        if includeChilren:
            if childrenTreeIn is None:
                child = self.createSampleChildrenTree(sample_id)
            else:
                child = childrenTreeIn
        
        upTreeList = []
        if len(parent_uids)==0:
            upTreeList.append(child)
        else:
            num_cores = multiprocessing.cpu_count()
            n = len(parent_uids)
            parentTreeLists = Parallel(n_jobs=-2, backend="threading")\
                (delayed(unwrap_self_createMultiParentTreeParallel_i)(i) for i in zip([self]*n, parent_uids, [child]*n))
            
            for parentTreeList in parentTreeLists:
                upTreeList += parentTreeList
        
        return upTreeList, parent_uids
    
    def __getChildrenListLoop(self, parentTreeData):
        listlists = []
        for node in parentTreeData:
            if 'children' in node:
                children = node['children']
                sublists = self.__getChildrenListLoop(children)
            else:
                sublists = [[]]
            
            uid = node['id']
            for listi in sublists:
                newlist = [uid] + listi
                listlists.append(newlist)
        
        return listlists
    
    def __getSampleTypeAttributes(self, parentList):
        sampleTypes = []       
        sampleTypeCount = {}
        for listi in parentList:
            sampleTypeCount_i = {}
            for uid in listi:
                if "-" in uid:
                    terms = uid.split('-')
                    sampleType = terms[0]
                else:
                    sampleType = uid
                    
                if sampleType not in sampleTypes:
                    sampleTypes.append(sampleType)
                    
                if sampleType not in sampleTypeCount_i:    
                    sampleTypeCount_i[sampleType] = 1
                else:
                    sampleTypeCount_i[sampleType] = sampleTypeCount_i[sampleType] + 1
                    
            for sampleType_i, count_i in sampleTypeCount_i.items():
                if sampleType_i not in sampleTypeCount:
                    sampleTypeCount[sampleType_i] = count_i
                else:
                    if count_i>sampleTypeCount[sampleType_i]:
                        sampleTypeCount[sampleType_i] = count_i
                
        stype = DBtable_sampletype("DEFAULT")
        attributes = stype.retrieveAttributes(sampleTypes)
        headers = []
        headersMapping = {} 
        for attr in attributes:
            for sampleType, attrInfo in attr.items():
                count = sampleTypeCount[sampleType]
                for i in range(count):
                    if count>1:
                        suffix = "_" + str(i+1)
                        prefix = sampleType + suffix + ':'      
                    else:
                        prefix = sampleType + ':'
                
                    if attrInfo is not None and 'headers' in attrInfo:
                        headers_i = attrInfo['headers']
                        for header in headers_i:
                            title = prefix + header
                            newheader = prefix + header
                            headers.append(newheader)
                            headersMapping[newheader] = title

        return sampleTypes, sampleTypeCount, headers, headersMapping
    
    def __retrieveSampleJsonData(self, uid):
        record_db = self.__retrieveSampleByUID(uid)
        if record_db is None:
            return None
            
        metadata = record_db['json_metadata']
        sampleDic = self.__getRecordFromJson(metadata)
        return sampleDic
        
    def __convertSampleTreeToList(self, parentList, sampleTypes, sampleTypeCount, headers):
        uids = {}
        for listi in parentList:
            for uid in listi:
                if uid not in uids:
                    sampleDic = self.__retrieveSampleJsonData(uid)
                    uids[uid] = sampleDic
                    
        diclist_new = []
        for listi in parentList:
            dici_new = {}
            sampleTypeCount_now = {}        
            for uid in listi:
                if "-" in uid:
                    terms = uid.split('-')
                    sampleType = terms[0]
                else:
                    sampleType = uid
                
                if sampleType not in sampleTypeCount_now:
                    sampleTypeCount_now[sampleType] = 1
                else:
                    sampleTypeCount_now[sampleType] = sampleTypeCount_now[sampleType] + 1

                count = sampleTypeCount[sampleType]
                if count==1:
                    prefix = sampleType + ':'     
                else:
                    suffix = "_" + str(sampleTypeCount_now[sampleType])
                    prefix = sampleType + suffix + ':'       # such as "DNA_2:"
                
                sampleDic = uids[uid]
                if sampleDic is not None and sampleDic is not []:
                    for key, value in sampleDic.items():
                        newkey = prefix + key
                        dici_new[newkey] = value        
            diclist_new.append(dici_new)
        
        headers_new = filterDiclist(headers, diclist_new)
        return headers_new, diclist_new
    
    def __saveSampleList(self, headers_new, diclist_new, excelfile, attributeFilter=None):
        headers_noneConstant, diclist_constant, headers_constant = getConstantRows(headers_new, diclist_new)
        if attributeFilter is not None and len(attributeFilter)>0:
            headersFiltered = []
            if ',' in attributeFilter:
                headersFiltered = attributeFilter.split(',')
            
            headers_noneConstant_new = []
            for header in headers_noneConstant:
                if header in headersFiltered:
                    headers_noneConstant_new.append(header)
            headers_noneConstant = headers_noneConstant_new
            
            diclist_constant_new = []
            for dici in diclist_constant:
                header = dici[headers_constant[0]]
                if header in headersFiltered:
                    diclist_constant_new.append(dici)
            diclist_constant = diclist_constant_new
        
        n1 = len(diclist_new)
        msg = "Number of rows before filtering: " + str(n1) + " at " + str(datetime.datetime.now())
        logger.debug(msg)
        diclist_new = removeRedundancy(headers_noneConstant, diclist_new)
        n2 = len(diclist_new)
        msg = "Number of rows after filtering: " + str(n2) + " at " + str(datetime.datetime.now())
        logger.debug(msg)
        saveTwoDiclistsIntoExcel(excelfile, diclist_new, headers_noneConstant, 'samples', diclist_constant, headers_constant, 'constants')
        nsamples = len(diclist_new)
        return nsamples


    def __createSampleTree(self, sample_ids):
        includeChilren = False
        parentList = []
        for sample_id in sample_ids:
            upTreeList, parent_uids = self.__createMultiParentTree(sample_id, includeChilren)
            parentList_i = self.__getChildrenListLoop(upTreeList)
            parentList += parentList_i
        
        sampleTypes, sampleTypeCount, headers, headersMapping = self.__getSampleTypeAttributes(parentList)       
        headers_new, diclist_new = self.__convertSampleTreeToList(parentList, sampleTypes, sampleTypeCount, headers)       
        return headers_new, diclist_new, headersMapping

    def __createSampleTreeToList_new(self, sample_ids, xlsfile='test.xls', attributeFilter=None):
        headers_new, diclist_new, headersMapping = self.__createSampleTreeFromDB(sample_ids)
        nsamplesOutput = self.__saveSampleList(headers_new, diclist_new, xlsfile, attributeFilter)
        return nsamplesOutput


    def downloadSamples_new(self, user_seek, xlsfile, link, sample_ids, includeSampleTree=1, attributeFilter=None):
        if includeSampleTree==1:
            nsamplesOutput = self.__createSampleTreeToList_new(sample_ids, xlsfile, attributeFilter)
        else:
            isNewSheet = True
            msg, status, nsamplesOutput = self.__downloadSampleList(sample_ids, xlsfile, isNewSheet)
        
        data = {}
        data['link'] = link
        if nsamplesOutput>=len(sample_ids):
            data['msg'] = 'okay'
            data['status'] = 1
        else:
            data['msg'] = 'Warning: Number of samples output: ' + str(nsamplesOutput) + ' is less than the number selected: ' + str(len(sample_ids))
            data['status'] = 0
            
        reportData = simplejson.dumps(data, default=str)
        return reportData
    
    def __exportImmportSheetInfo(self, headersMapping, diclist_new, templatedata, sheetName, excelfile):
        nameup = sheetName.upper()
        sheetData = templatedata[nameup]
        diclist = sheetData['diclist']
        
        headers = []
        mapping = {}
        for dici in diclist:
            header = dici['ImmPort']
            attribute = dici['FairData']
            if attribute is not None:
                headers.append(header)
                mapping[header] = attribute
                
        diclistOut = []
        for dici in diclist_new:
            diciOut = {}
            for header in headers:
                attribute = mapping[header]
                if ":" in attribute:
                    terms = attribute.split(":")
                    attribute = terms[0] + ":" + terms[1]
                
                if attribute in dici:
                    diciOut[header] = dici[attribute]
                else:
                    if ":" in attribute:
                        diciOut[header] = ''
                    else:
                        diciOut[header] = attribute
            
            diclistOut.append(diciOut)
            diclistOut = removeDiclistDuplicates(diclistOut)
        
        reviseExcelDiclist(excelfile, headers, diclistOut, sheetName)
        return
    
    def __exportImportProtocls(self, user_seek, diclist, zf):
        dbsop = DBtable_sops("DEFAULT")
        diclist_new = []
        for dici in diclist:
            if "File Name" in dici:
                sop_link = dici["File Name"] 
                terms = sop_link.split('/')
                if sop_link[-1]=='/':
                    uidterm = terms[-2]
                else:
                    uidterm = terms[-1]
                    
                if "=" in uidterm:  
                    terms = uidterm.split("=")
                    sop_uid = terms[-1]
                    fullfilename, status, link = dbsop.downloadSOP_fromStorage(user_seek, sop_uid)
                    if status==1:
                        dici['User Defined ID'] = sop_uid
                        terms = fullfilename.split('/')
                        originalName = terms[-1]
                        dici['Name'] = originalName
                        
                        if os.path.isfile(fullfilename):
                            zf.write(fullfilename, originalName)
            
            diclist_new.append(dici)
        return diclist_new
        
    
    def __exportImmportSheetInfoZip(self, user_seek, headersMapping, diclist_new, templatedata, sheetName, txtfile, fileLabel, zf):
        nameup = sheetName.upper()
        sheetData = templatedata[nameup]
        diclist = sheetData['diclist']
        
        headers = []
        mapping = {}
        for dici in diclist:
            header = dici['ImmPort']
            attribute = dici['FairData']
            if attribute is not None:
                headers.append(header)
                mapping[header] = attribute
                
        diclistOut = []
        for dici in diclist_new:
            diciOut = {}
            for header in headers:
                attribute = mapping[header]
                if ":" in attribute:
                    terms = attribute.split(":")
                    attribute = terms[0] + ":" + terms[1]
                
                if attribute in dici:
                    diciOut[header] = dici[attribute]
                else:
                    if ":" in attribute:
                        diciOut[header] = ''
                    else:
                        diciOut[header] = attribute
            
            diclistOut.append(diciOut)
            diclistOut = removeDiclistDuplicates(diclistOut)
        
        if sheetName=='protocols':
            diclistOut = self.__exportImportProtocls(user_seek, diclistOut, zf)
        
        fo = open(txtfile,"w")
        
        delimit = '\t'
        line = fileLabel + delimit + IMMPORT_TEMPLATES_VERSION + '\n'
        fo.write(line)
        line = "Please do not delete or edit this column"  + '\n'
        fo.write(line)
        
        line = delimit.join(headers) + '\n'
        fo.write(line)
        for dici in diclistOut:
            line = ""
            for index, header in enumerate(headers):
                if header in dici:
                    item = dici[header]
                    newitem = toString(item)
                else:
                    newitem = ""
                
                line += newitem + delimit
        
            line = line[:-1] + '\n'
            fo.write(line)
        fo.close()    
        return           
            
    def __exportImmportSampleListZip(self, user_seek, headers_new, diclist_new, downloadfile, sampletypeName, headersMapping):
        if 'xlsx' in downloadfile:
            return self.__exportImmportSampleList(user_seek, headers_new, diclist_new, downloadfile, sampletypeName, headersMapping)
            
        excelfile = downloadfile.replace('zip', 'xlsx')
        templatefile = IMMPORT_TEMPLATE_FILE
        cmd = 'cp ' + templatefile + ' ' + excelfile
        os.system(cmd)
        
        msg = "Load Immport template file"
        status = 0
        
        try:
            filedata = load_excelfile_asdic(excelfile)
        except:
            msg = "Error: Immport template file not loaded: " + excelfile
            status = 0
            return msg, status

        status = 1
        for sheetname in IMMPORT_TEMPLATES:
            msg = "Error: the following sheet is missing: "
            nameup = sheetname.upper()
            if nameup not in filedata['sheetnames'] or nameup not in filedata:
                status = 0
                msg += sheetname + ';'
            
            if status==0:
                return msg, status
        
        zf = zipfile.ZipFile(downloadfile, mode='w')
        status = 0
        msg = "Start generating zip file"
        for sheetname in IMMPORT_TEMPLATES:
            filename = sheetname + '.txt'
            sheetfile = DOWNLOAD_DIRECTORY + filename
            fileLabel = IMMPORT_TEMPLATES[sheetname]
            self.__exportImmportSheetInfoZip(user_seek, headersMapping, diclist_new, filedata, sheetname, sheetfile, fileLabel, zf)
            zf.write(sheetfile, filename)
        
        zf.close()
        return "Okay", 1
    
    def __exportImmportSampleList(self, user_seek, headers_new, diclist_new, downloadfile, sampletypeName, headersMapping):
        if 'zip' in downloadfile:
            return self.__exportImmportSampleListZip(user_seek, headers_new, diclist_new, downloadfile, sampletypeName, headersMapping)
            
        excelfile = downloadfile
        templatefile = IMMPORT_TEMPLATE_FILE
        cmd = 'cp ' + templatefile + ' ' + excelfile
        os.system(cmd)
        
        msg = "Load Immport template file"
        status = 0
        
        try:
            filedata = load_excelfile_asdic(excelfile)
        except:
            msg = "Error: Immport template file not loaded: " + excelfile
            status = 0
            return msg, status

        status = 1
        for sheetname in IMMPORT_TEMPLATES:
            msg = "Error: the following sheet is missing: "
            nameup = sheetname.upper()
            if nameup not in filedata['sheetnames'] or nameup not in filedata:
                status = 0
                msg += sheetname + ';'
            
            if status==0:
                return msg, status
        
        for sheetname in IMMPORT_TEMPLATES:
            self.__exportImmportSheetInfo(headersMapping, diclist_new, filedata, sheetname, downloadfile)
            
        return "Okay", 1
    
    
    def __exportImmportCreateSampleTreeToList(self, user_seek, sample_ids, downloadfile, sampletypeName):
        headers_new, diclist_new, headersMapping = self.__createSampleTree(sample_ids)
        msg, status = self.__exportImmportSampleList(user_seek, headers_new, diclist_new, downloadfile, sampletypeName, headersMapping)
        return msg
    
    
    def exportSamples(self, user_seek, xlsfile, link, sample_ids, sampletype_id):
        stype = DBtable_sampletype()
        sampletypeName = stype.retrieveFieldValue(sampletype_id, 'title')
        return self.__exportSamples0(user_seek, xlsfile, link, sample_ids, sampletypeName)

    def __exportSamples0(self, user_seek, downloadfile, link, sample_ids, sampletypeName):
        sampletypeName = 'D.MSP'
        nsamplesOutput = self.__exportImmportCreateSampleTreeToList(user_seek, sample_ids, downloadfile, sampletypeName)
        msg = 'Okay'
        data = {}
        data['link'] = link
        if nsamplesOutput>=len(sample_ids):
            data['msg'] = 'okay'
            data['status'] = 1
        else:
            data['msg'] = msg
            data['status'] = 0
            
        reportData = simplejson.dumps(data, default=str)
        return reportData
    
    def __verifyFileInRecord(self, sampleRecord, originalfilename, filetype):
        if 'json_metadata' not in sampleRecord:
            return False
        
        fileInRecord = 0
        json_metadata = sampleRecord['json_metadata']
        sampledic = self.__getRecordFromJson(json_metadata)
        for key, value in sampledic.items():
            if filetype=="SOP":
                if SAMPLE_PROTOCOL_ACCESSOR_NAME in key:
                    if value==originalfilename:
                        fileInRecord = 1
            
            if filetype=="DATAFILE":
                if SAMPLE_FILE_ACCESSOR_NAME in key:
                    if value==originalfilename:
                        fileInRecord = 1
                elif SAMPLE_LINK_ACCESSOR_NAME in key:
                    if value==originalfilename:
                        fileInRecord = 2
                
        return fileInRecord, sampledic
    
    def searchFileInSample(self, creator, originalfilename, filetype):
        #print("searchFileInSample now...", creator)
        creator_id = creator['user_id']
        #print("creator_id: ", creator_id)
        if filetype!="SOP" and filetype!="DATAFILE":
            msg = 'Error: file type not supported for uploading file.'
            return None, msg
        
        #print("retrieveRecords...")
        records = self.db.retrieveRecords(self.tablemodel, 'json_metadata', originalfilename)
        if records is None:
            msg = 'Warning: File not associated with any sample that has been uploaded first'
            return None, msg
        
        nrecords = len(records)
        if nrecords<=0:
            msg = 'Warning: File not associated with any sample that has been uploaded first'
            return None, msg
        
        msg = "Number of samples for the file: " + str(nrecords) + ";"
        #print(msg)
        creator_lab = creator['lababbv']
        records_now = []
        for record in records:
            contributor_id = record['contributor_id']
            sample_uid = record['uuid']
            msg += "Lab: " + creator_lab + "; sample UID: " + sample_uid
            if sample_uid is not None and creator_lab in sample_uid:
                fileInRecord, sampledic = self.__verifyFileInRecord(record, originalfilename, filetype)
                if fileInRecord>0:
                    record_now = record.update(sampledic)
                    records_now.append(record)
        
        nnow = len(records_now)
        if nnow==1:
            sampleRecord = records_now[0]
            msg = 'okay'
        elif nnow>1:
            if filetype=="SOP":
                msg = 'okay'
                sampleRecord = records_now[0]
            else:
                msg += 'Error: File associated with more than one sample that has been uploaded'
                sampleRecord = None
        else:
            sampleRecord = None
            msg += 'Error: No sample is defined for the file from same user'
        
        return sampleRecord, msg
    
    
    def updateSampleDFurl(self, username, sample_uid, originalfilename, df_link):
        msg = ''
        status = 0
        record_db = self.__retrieveSampleByUID(sample_uid)
        if record_db is None:
            msg = 'Error: sample not found: ' + sample_uid
            return msg, status
        
        metadata = record_db['json_metadata']
        sampleDic = self.__getRecordFromJson(metadata)
        
        suffix = None
        for key, value in sampleDic.items():
            if SAMPLE_FILE_ACCESSOR_NAME in key:
                if value==originalfilename:
                    suffix = key.replace(SAMPLE_FILE_ACCESSOR_NAME, '')     # such as "qc"
        
        for key, value in sampleDic.items():
            if SAMPLE_LINK_ACCESSOR_NAME in key:
                suffixi = key.replace(SAMPLE_LINK_ACCESSOR_NAME, '')        # such as 'qc'
                if suffix is not None and suffixi==suffix:
                    sampleDic[key] = df_link
        
        record_db['json_metadata'] = simplejson.dumps(sampleDic, default=str)
        msg, status, sample_id = self.storeOneRecord(username, record_db)
        return msg, status
    
    def deleteSampleNeo4j(self, sample_id):
        with GraphDatabase.driver(NEO4J_DATABASE['URI'], auth=NEO4J_DATABASE['AUTH']) as driver:
            records, summary, keys = driver.execute_query("MATCH (s:Sample {id: $id}) DETACH DELETE s",
                    id=sample_id,
                    database_=NEO4J_DATABASE['NAME'])
            logger.debug(f"NEO4J summary: {summary}")

    
    def __deleteOneSample(self, sample_id, policy_id):
        sqlqueries = []
        sqlquery = "DELETE FROM projects_samples where sample_id=" + str(sample_id) + ";"
        sqlqueries.append(sqlquery)
        sqlquery = "DELETE FROM sample_resource_links where sample_id=" + str(sample_id) + ";"
        sqlqueries.append(sqlquery)
        sqlquery = "DELETE FROM sample_auth_lookup where asset_id=" + str(sample_id) + ";"
        sqlqueries.append(sqlquery)
        sqlquery = "DELETE FROM assay_assets where asset_id=" + str(sample_id) + " AND asset_type='Sample';"
        sqlqueries.append(sqlquery)
        sqlquery = "DELETE FROM assets_creators where asset_id=" + str(sample_id) + " AND asset_type='Sample';"
        sqlqueries.append(sqlquery)
        sqlquery = "delete FROM samples where id=" + str(sample_id) + ";"
        sqlqueries.append(sqlquery)
        sqlquery = "DELETE FROM permissions where policy_id=" + str(policy_id) + ";"
        sqlqueries.append(sqlquery)
        sqlquery = "delete FROM policies where id=" + str(policy_id) + ";"
        sqlqueries.append(sqlquery)
        db_alias = SEEK_DATABASE
        status = self.db.run_custom_transaction(sqlqueries, db_alias)
        if status:
            msg = "Transaction successful"
            try:
                self.deleteSampleNeo4j(sample_id)
            except:
                None
        else:
            msg = "Error: The trandsaction of deletion failed. Delete this sample manually"
        
        return msg, status
    
    def __getSampleChildren(self, currentuid):
        records = self.db.retrieveRecords(self.tablemodel, 'json_metadata', currentuid)
        childrenList = []
        for record in records:
            uid = record['uuid']
            metadata = record['json_metadata']
            sampleDic = self.__getRecordFromJson(metadata)
            parent_uids = self.__getParentUIDs(sampleDic)
            if currentuid in parent_uids:
                sid = record['id']
                dici = {'id':sid, 'uid':uid}
                childrenList.append(dici)
            
        return childrenList
    
    def __deleteSampleList(self, user_seek, sample_ids, xlsfile):
        user_id = user_seek['user_id']
        roles_mask = self.db.retrieveFieldValue(People, user_id, 'roles_mask')
    
        status = 1
        msg = ''
        diclist = []
        for sample_id in sample_ids:
            dici = {}
            dici['id'] = sample_id
            record = self.__retrieveSampleByID(sample_id)
            if record is None:
                msgi = 'Error: Sample ' + str(sample_id) +  ' not found in DB '
                status = 0
                dici['json_metadata'] = msgi
                msg += msgi + '<br/>'
                diclist.append(dici)
                continue
            
            contributor_id = record['contributor_id']
            policy_id = record['policy_id']
            currentuid = record['uuid']
            
            dici['uid'] = currentuid
            childrenList =  self.__getSampleChildren(currentuid)
            if user_id==contributor_id or int(roles_mask)>0:
                if len(childrenList)==0:
                    msgi, statusi = self.__deleteOneSample(sample_id, policy_id)
                    if statusi:
                        dici['statusi'] = 'DELETED'
                    else:
                        dici['statusi'] = msgi
                else:
                    msgi = 'Warning: Sample has child sample thus has to be deleted manually.'
                    msg += msgi + '<br/>'
                    status = 0
                    dici['statusi'] = msgi
            else:
                msgi = 'Error: Only admin or owner is allowed to delete sample.'
                msg += msgi + '<br/>'
                status = 0
                dici['statusi'] = msgi
            dici['json_metadata'] = msgi
            diclist.append(dici)
        
        headers = ['id', 'uid', 'sample_type', 'first_name', 'created_at', 'json_metadata', 'statusi']
        saveDiclistIntoExcel(diclist, xlsfile, headers, 'samples')
        return diclist, msg, status 
    
    def deleteSamples(self, user_seek, xlsfile, link, sample_ids):
        diclist, msg, status = self.__deleteSampleList(user_seek, sample_ids, xlsfile)
        data = {}
        data['msg'] = msg
        data['status'] = status
        data['link'] = link
        data['diclist'] = diclist
        
        reportData = simplejson.dumps(data, default=str)
        return reportData
    
    
    def __formatSampleUIDLink(self, sample_uid):
        url = "/seek/sampletree/uid=" + str(sample_uid) + "/";
        # Rendered as raw HTML by the client; escape the untrusted uid in both
        # the href attribute and the link text to prevent HTML injection.
        weblink = '<a href="' + html.escape(url) + '" target="_blank">' + html.escape(str(sample_uid)) + '</a>'
        return weblink

    def __formatSopUIDLink(self, sop_uid):
        url = "/seek/sop/uid=" + str(sop_uid) + "/";
        # Rendered as raw HTML by the client; escape the untrusted uid in both
        # the href attribute and the link text to prevent HTML injection.
        weblink = '<a href="' + html.escape(url) + '" target="_blank">' + html.escape(str(sop_uid)) + '</a>'
        return weblink
    
    def __formatExternalLink(self, urlValue):
        weblink = urlValue
        if ";" in urlValue:
            weblink = ''
            vis = urlValue.split(";")
            i = 0
            for vi in vis:
                vi = vi.strip()
                if len(vi)>0:
                    if vi[0:4].lower()=='http':
                        if i>0:
                            weblink += ","
                        
                        # 'http' prefix check above blocks javascript:/data:
                        # schemes; escape to prevent href/text HTML injection.
                        weblink += '<a href="' + html.escape(vi) + '" target="_blank">' + html.escape(vi) + '</a>'
                        i += 1
        else:
            vi = urlValue.strip()
            if len(vi)>0:
                if vi[0:4].lower()=='http':
                    # 'http' prefix check blocks javascript:/data: schemes;
                    # escape to prevent href/text HTML injection.
                    weblink = '<a href="' + html.escape(vi) + '" target="_blank">' + html.escape(vi) + '</a>'
        
        return weblink
    
    def __formatLinkUrl(self, attrname, attrvalue):
        weblink = attrvalue
        value = attrvalue
        
        if SAMPLE_PARENT_ACCESSOR_NAME in attrname:
            if attrvalue is None:
                return weblink
            
            if ";" in value:
                weblink = ''
                vis = value.split(";")
                i = 0
                for vi in vis:
                    vi = vi.strip()
                    if len(vi)>0:
                        if i>0:
                            weblink += ","
                            
                        weblink += self.__formatSampleUIDLink(vi)
                        i += 1
            else:
                value = value.strip()
                if len(value)>0:
                    weblink = self.__formatSampleUIDLink(value)
        
        elif SAMPLE_PROTOCOL_ACCESSOR_NAME in attrname:
            if attrvalue is None:
                return weblink
            
            if ";" in value:
                weblink = ''
                vis = value.split(";")
                i = 0
                for vi in vis:
                    vi = vi.strip()
                    if len(vi)>0:
                        if i>0:
                            weblink += ","
                            
                        weblink += self.__formatSopUIDLink(vi)
                        i += 1
            else:
                value = value.strip()
                if len(value)>0:
                    weblink = self.__formatSopUIDLink(value)
        
        elif SAMPLE_LINK_ACCESSOR_NAME in attrname:
            if attrvalue is None:
                return weblink
            weblink = self.__formatExternalLink(attrvalue)
            
        elif SAMPLE_FILE_ACCESSOR_NAME in attrname:
            if attrvalue is None:
                return weblink
            weblink = self.__formatExternalLink(attrvalue)
            
        elif SAMPLE_PUBLISH_ACCESSOR_NAME in attrname:
            if attrvalue is None:
                return weblink
            weblink = self.__formatExternalLink(attrvalue)
        
        return weblink
    
    
    def getSampleInfo(self, sample_id):
        record = self.__retrieveSampleByID(sample_id)
        if record is None:
            return None, None
        
        sampletype_id = record['sample_type_id']
        sattr = DBtable_sampleattribute()
        attributeInfo = sattr.getAttributeInfo(sampletype_id)
        headers = attributeInfo['headers']
        childuid = record['uuid']
        json_metadata = record['json_metadata']
        dici = self.__getRecordFromJson(json_metadata)
        
        diclist = []
        for header in headers:
            headerStripped = header.strip()
            attrdici = {}
            attrdici['attrname'] = header
            if headerStripped in dici:
                value = dici[headerStripped]
                if value is not None:
                    try:
                        valuestr = str(value)
                        if len(valuestr.strip())>0:
                            attrdici['attrvalue'] = self.__formatLinkUrl(headerStripped, valuestr)
                            diclist.append(attrdici)
                    except:
                        attrdici['attrvalue'] = value
                        diclist.append(attrdici)
            
        return dici, diclist
    
    def __publishSampleList(self, user_seek, sample_ids, xlsfile, assay_id=None, project_id=None):
        isNewSheet = False
        excelfile = xlsfile
        msg, status, nsamplesOutput = self.__downloadSampleList(sample_ids, xlsfile, isNewSheet)
        
        filename = excelfile
        if "/" in filename:
            terms = filename.split('/')
            filename = terms[-1]
        modifyExcelCell(excelfile, 4, 3, filename, "Metadata")
        modifyExcelCell(excelfile, 5, 3, 'Batch sample publishing', "Metadata")
        if assay_id is not None:
            assay_url = PUBLISH_SERVER + '/assays/' + str(assay_id)
            modifyExcelCell(excelfile, 10, 3, assay_url, "Metadata")
            
        if project_id is not None:
            project_url = PUBLISH_SERVER + '/projects/' + str(project_id)
            modifyExcelCell(excelfile, 6, 3, project_url, "Metadata")
            
        return msg, status
        
    def publishSamples(self, user_seek, xlsfile, link, sample_ids, assay_id=None, project_id=None):
        msg, status = self.__publishSampleList(user_seek, sample_ids, xlsfile, assay_id, project_id)
        
        data = {}
        data['msg'] = msg
        data['status'] = status
        data['link'] = link
        data['ptype'] = 'Sample'
        reportData = simplejson.dumps(data, default=str)
        return reportData
    
    
    def __loadPublishedSampleSheet(self, excelfile):
        msg = "loadPublishedSampleSheet"
        status = 0
        sampletype = ''
        uids = []
        
        try:
            filedata = load_excelfile_asdic(excelfile)
        except:
            msg = "Error: sample excel file can't be loaded."
            status = 0
            return msg, status, sampletype, uids
        
        status = filedata['status']
        msg = filedata['msg']
        if status==0:
            return msg, status, sampletype, uids
        
        if 'SAMPLES' not in filedata['sheetnames'] or 'SAMPLES' not in filedata:
            msg = "Error: Samples sheet not in the excel."
            status = 0
            return msg, status, sampletype, uids
        
        sheetData = filedata["SAMPLES"]
        diclist = sheetData['diclist']
        sampletypes = {}
        for dici in diclist:
            uid = dici['UID']               # such as TIS-200901ENG-8
            terms = uid.split('-')
            sampletype = uid[0]             # such as TIS
            
            if sampletype in sampletypes:
                uids = sampletypes[sampletype]
            else:
                uids = []
            if uid not in uids:
                uids.append(uid)
                
            sampletypes[sampletype] = uids
        
        n = 0
        for sampletype in sampletypes:
            uids = sampletypes[sampletype]
            if n==0:
                msg = 'okay'
                status = 1
                return msg, status, sampletype, uids
            else:
                msg = "Error: More than one sample type in the excel."
                status = 0
                return msg, status, sampletype, uids
            n += 1
        
        return msg, status, sampletype, uids
        
    
    def findSamplesForExport(self, user_seek, downloadfile, link, excelfile):
        username = user_seek['username']
        
        msg, status, sampletype, uids = self.__loadPublishedSampleSheet(excelfile)
        if status==0:
            data = {}
            data['link'] = link
            data['msg'] = msg
            data['status'] = 0
            reportData = simplejson.dumps(data, default=str)
            return reportData
        
        sample_ids = []
        for uid in uids:
            sample_id = self.getSampleID(uid)
            sample_ids.append(sample_id)
            
        sdata = self.__exportSamples0(user_seek, downloadfile, link, sample_ids, sampletype)
        return sdata 
        
    def getSampleType(self, user_seek, sampletype_id, attribute, project_id=0):
        if attribute=='none':
            msg = 'ignore validation'
        else:
            sattr = DBtable_sampleattribute()
            msg, status = sattr.validateFilters(sampletype_id, attribute, filter_rule, filter_valueFrom, filter_valueTo)
            if status==0:
                data = {'msg':msg, 'status': status}
                reportData = simplejson.dumps(data, default=str)
                return reportData
        
        data = self.__retrieveSamplesInType(user_seek, sampletype_id, project_id)
        if attribute=='none':
            msg = 'ignore filtering'
        else:
            rows = self.__filterSamples(data['rows'], sampletype_id, attribute, filter_rule, filter_valueFrom, filter_valueTo) 
            data['rows'] = rows
            data['total'] = len(rows)
        data['msg'] = 'okay'
        data['status'] = 1
        
        reportData = simplejson.dumps(data, default=str)
        return reportData
    
    def __updateSampleMeta(self, metadata_db, diclist_attributes, attri_renamed):
        metadata_out = {}
        
        for dici in diclist_attributes:
            accessor_name = dici['title']
            
            if accessor_name in metadata_db:
                metadata_out[accessor_name] = metadata_db[accessor_name]
            elif accessor_name in attri_renamed:
                accessor_name_old = attri_renamed[accessor_name]
                if accessor_name_old in metadata_db:
                    metadata_out[accessor_name] = metadata_db[accessor_name_old]
                else:
                    metadata_out[accessor_name] = ''
            else:
                metadata_out[accessor_name] = ''
        
        return metadata_out
        
    
    def __updateSamplesMeta(self, user_seek, samples, sampletype_id, attri_renamed):
        sattr = DBtable_sampleattribute()
        attributeInfo = sattr.getAttributeInfo(sampletype_id)
        diclist_attributes = attributeInfo['diclist']
        username = user_seek['username']
        
        n = 0
        nright = 0
        msg = ''
        status = 1
        for record in samples:
            json_metadata = record['json_metadata']
            metadata_db = self.__getRecordFromJson(json_metadata)
            
            metadata_out = self.__updateSampleMeta(metadata_db, diclist_attributes, attri_renamed)
            
            record['json_metadata'] = simplejson.dumps(metadata_out, default=str)
            record['updated_at'] = getDefaultDateTime()

            msgi, statusi, sample_id = self.storeOneRecord(username, record)
            if statusi:
                nright += 1
            else:
                status = 0
                msg += msgi +  '<br/>'
            
            n += 1
            
        return msg, status
    
    
    def updateSampleType(self, user_seek, sampletype_id, attri_renamed, project_id=0):
        data = self.__retrieveSamplesInType(user_seek, sampletype_id, project_id)
        msg, status = self.__updateSamplesMeta(user_seek, data['rows'], sampletype_id, attri_renamed)
        data['msg'] = msg
        data['status'] = status
        data['link'] = ''
        
        reportData = simplejson.dumps(data, default=str)
        return reportData
    
    def updateSingleSample(self, dici, username=None, attributes=None):
        msg = "updateSingleSample"
        status = 0
        #logger.debug(msg)        
        if 'UID' not in dici and 'uid' not in dici:
            msg = 'UID not available for update'
            status = False
            logger.debug(msg)
            return msg, status
            
        if 'UID' in dici:
            uidIn = dici['UID']
        else:
            uidIn = dici['uid']
            
        if uidIn is None or len(uidIn.strip())==0:
            msg = 'UID not available for update'
            status = False
            logger.debug(msg)
            return msg, status
                
        record = self.__retrieveSampleByUID(uidIn)
        if record is None:
            msg = "Error: Sample UID " + uidIn + " does not exist in DB for update "
            status = False
            logger.debug(msg)
            return msg, status
            
        json_metadata = record['json_metadata']
        dici_json = self.__getRecordFromJson(json_metadata)
        
        metadata_db = dici_json
        metadata_in = dici
        metadata_out = self.__updateSampleMetadata(metadata_db, metadata_in, attributes)
        dici_json = metadata_out
                
        json_metadata_updated = simplejson.dumps(dici_json, default=str)
        record['json_metadata'] = json_metadata_updated
        other_creators = record['other_creators']
        if username is not None:
            if other_creators is None:
                record['other_creators'] = username
            elif username not in other_creators:
                record['other_creators'] = other_creators + ';' + username

        record['updated_at'] = getDefaultDateTime()
        #logger.debug('storeOneRecord: Start')
        msg, status, sample_id = self.storeOneRecord(username, record)
        return msg, status
    
    
    def __getSampleTypeInfo(self, sampleType):
        stype = DBtable_sampletype()
        sampletype_id = stype.getSampleTypeID(sampleType)
        if sampletype_id<=0:
            msg = SAMPLE_ERRORCODE['201'] + sampleType + " id: " + str(sampletype_id)
            return msg, 0, {}
        
        sattr = DBtable_sampleattribute()
        attributeInfo = sattr.getAttributeInfo(sampletype_id)
        attributeInfo['sampleType'] = sampleType
        attributeInfo['sampletype_id'] = sampletype_id
        if len(attributeInfo['headers'])==0:
            msg = SAMPLE_ERRORCODE['202'] + sampleType
            return msg, 0, attributeInfo
        
        msg = 'Sample type info retrieved'
        status = 1
        return msg, status, attributeInfo
        
    
    def __verifySampleAttributes(self, record, headers_required):
        msg_required, meetRequired = self.__verifyRequiredFields(record, headers_required)
        if not meetRequired:
            msg = SAMPLE_ERRORCODE['502'] + msg_required
            return msg, 0, '', ''
    
        if 'Name' in record:
            samplename = str(record['Name'])
        elif 'File_PrimaryData' in record:
            samplename = str(record['File_PrimaryData'])
        elif 'File_PrimaryData_Forward' in record:
            samplename = str(record['File_PrimaryData_Forward'])
        elif 'File_PrimaryData_Reverse' in record:
            samplename = str(record['File_PrimaryData_Reverse'])
        else:
            msg = SAMPLE_ERRORCODE['302']
            return msg, 0, '', ''
            
        if samplename is None:
            cleanname = ''
        else:
            cleanname = str(samplename)
        cleanname = cleanname.strip()
        if len(cleanname)==0:
            msg = SAMPLE_ERRORCODE['303']
            return msg, 0, '', ''
            
        if SAMPLE_CONTRIBUTOR_ACCESSOR_NAME in record:
            scientist = str(record[SAMPLE_CONTRIBUTOR_ACCESSOR_NAME])
        elif SAMPLE_CONTRIBUTOR_ACCESSOR_NAME in record:
            scientist = str(record[SAMPLE_CONTRIBUTOR_ACCESSOR_NAME])
        else:
            msg = SAMPLE_ERRORCODE['304']
            return msg, 0, '', ''
        
        msg = "Pass verification on all required and mandetroy attributes"
        return msg, 1, cleanname, scientist
    
    def apiInsertSample(self, dici, sampleType, user_seek, diclist_assay):
        msg = "insertSingleSample"
        status = 0
        username = user_seek['username']
        user_id = user_seek['user_id']
        project_id = user_seek['projectid']
        
        if not self.__notEmptyLine(dici):
            msg = SAMPLE_ERRORCODE['501']
            return msg, 0, None
        
        msg, status, attributeInfo = self.__getSampleTypeInfo(sampleType)
        if status==0:
            return msg, status, None
        sampletype_id = attributeInfo['sampletype_id']
        headers_required = attributeInfo['headers_required']
        headers = attributeInfo['headers']
        
        record = {}
        for header in headers:
            if header in dici:
                record[header] = dici[header]
        
        msg, status, samplename, scientist = self.__verifySampleAttributes(record, headers_required)
        if status==0:
            return msg, status, None
            
        contributor_id = user_id
        if contributor_id<=0:
            msg = SAMPLE_ERRORCODE['305'] + contributor
            return msg, status, None
               
        uidIn = None
        if 'uid' in dici:
            uidIn = dici['uid']
        elif 'UID' in dici:
            uidIn = dici['UID']
        
        msg, status = self.__verifySampleUID(samplename, contributor_id, uidIn, sampletype_id, scientist)
        if status==0:
            status = 0
            return msg, status, None
                
        if uidIn is not None and len(uidIn.strip())>0:
            isValid, msg = self.__verifyUID(uidIn, attributeInfo)
            if not isValid:
                return msg, 0, None
        
        record_new, newSample = self.__getRecord(user_seek, record, attributeInfo, contributor_id)
        uid = record_new['uuid']
        
        msg, status, sample_id = self.storeOneRecord(username, record_new)
        if status:
            if newSample:
                self.__updateSampleProject(user_seek, sample_id)
                self.__updateSampleAssetsCreators(sample_id, contributor_id)
                if len(diclist_assay)>0:
                    msgj, statusj = self.__storeSample_assay_asset(user_seek, sampleType, sample_id, diclist_assay)
                    if statusj==0:
                        msgj = SAMPLE_ERRORCODE['601'] + msgj
                        msg += ';' + msgj
                else:
                    msg = 'Info: Assay info not available for updating array-sample relationship for sample id: ' + str(sample_id)
                    
            else:
                msg = 'Info: No update on array-sample relationship for old sample id: ' + str(sample_id)
                
            msgdf, statusdf = self.__setSampleDatafileAssociation(user_seek, sampleType, dici, attributeInfo, diclist_assay)
            if not statusdf:
                msgdf = SAMPLE_ERRORCODE['602'] + msgdf
                msg += ';' + msgdf
        else:
            msg = SAMPLE_ERRORCODE['504'] + msg
        
        return msg, status, uid
    
    def apiUploadSamples(self, data, user_seek):
        if 'UID' in data or 'uid' in data:
            msg, status = self.updateSingleSample(data)
            if 'UID' in data:
                uid = data['UID']
            else:
                uid = data['uid']
            
            return msg, status, uid
        
        if 'User' not in data:
            msg = "Error: 'User' info not provided in the input json dictionary"
            return msg, 0, None
        submitter = data['User']
        user_seek['lababbv'] = submitter['lababbv']
        user_seek['projectid'] = submitter['projectid']
        if 'Sample type' not in data:
            msg = "Error: 'Sample type' info not provided in the input json dictionary"
            return msg, 0, None
        sampleType = data['Sample type']
        
        if 'Samples' not in data:
            msg = "Error: 'Samples' info not provided in the input json dictionary"
            return msg, 0, None
        samples = data['Samples']
        
        if 'Assay' in data:
            diclist_assay = data['Assay']
        else:
            diclist_assay = []
        
        msg, status, uid = self.apiInsertSample(samples, sampleType, user_seek, diclist_assay)
        if uid is None:
            uid = ''
        return msg, status, uid


    def getSampleUIDInfo(self, sample_uid):
        sinfo = {}
        sinfo['sample_uid'] = sample_uid
        if '-' not in sample_uid:
            return sinfo
        
        record = self.__retrieveSampleByUID(sample_uid)
        if record is None:
            return sinfo
        
        sinfo['record'] = record
        terms = sample_uid.split("-")
        sampletype = terms[0]   
        sinfo['sample type'] = sampletype
        
        dateabbr = terms[1]     
        if len(dateabbr)!=9:
            return sinfo
        
        lababbv = dateabbr[-3:] 
        sinfo['lababbv'] = lababbv
        
        sid = record['id']
        sqlquery = 'SELECT * FROM projects A '
        sqlquery += 'LEFT JOIN projects_samples B '
        sqlquery += 'ON A.id=B.project_id '
        sqlquery += 'where B.sample_id=' + str(sid)
        sqlquery = 'SELECT * FROM projects_samples where sample_id=' + str(sid) + ';'
        project_id = None
        for p in Projects_samples.objects.raw(sqlquery):
            project_id = p.project_id
        
        sinfo['project_id'] = project_id
        sinfo['projectname'] = None
        if project_id is not None:
            project = Projects.objects.get(pk=project_id)
            sinfo['projectname'] = project.title
        
        return sinfo
        
    def __downloadSampleList(self, sample_ids, xlsfile, isNewSheet=True):
        status = 1
        msg = ''
        sample_type_id = None
        sattr = DBtable_sampleattribute()
        sattrInfo = {}
        headers = None
        diclist = []
        nsamplesOutput = 0
        for sample_id in sample_ids:
            record = self.__retrieveSampleByID(sample_id)
            if record is None:
                msgi = 'Error: Sample id ' + str(sample_id) +  ' not found in DB '
                status = 0
                msg += msgi + '<br/>'
                continue
            
            if sample_type_id is None:
                sample_type_id = record['sample_type_id']
                if sample_type_id in sattrInfo:
                    attributeInfo = sattrInfo[sample_type_id]
                else:
                    attributeInfo = sattr.getAttributeInfo(sample_type_id)
                    sattrInfo[sample_type_id] = attributeInfo
                    
                headers = attributeInfo['headers']
            else:
                if sample_type_id!=record['sample_type_id']:
                    msgi = 'Error: Sample id ' + str(sample_id) +  ' is not in the sample type with other sample'
                    status = 0
                    msg += msgi + '<br/>'
                    continue
            
            json_metadata = record['json_metadata']
            dici = self.__getRecordFromJson(json_metadata)
            dici_rev = {}
            for header in headers:
                hi = header.strip()
                if hi in dici:
                    dici_rev[header] = dici[hi]
                else:
                    dici_rev[header] = ''
            
            diclist.append(dici_rev)
            nsamplesOutput += 1
        
        #n1 = len(diclist)
        #diclist = removeRedundancy(headers, diclist)
        #n2 = len(diclist)
        #msg = "Number of rows before and after filtering: " + str(n1) + ' ' + str(n2)
        #logger.debug(msg)
        saveExcelDiclist(xlsfile, headers, diclist, 'Samples', isNewSheet, True)
        return msg, status, nsamplesOutput
    
    
    def __parseSearchFilters(self, filters, searchType, project_id=0):
        msg = ''
        status = 1
        sampletype_id = None
        attribute = None
        filter_rule = None
        filter_valueFrom = None
        filter_valueTo = None
        filtersdic = self.__initSearchFilters(searchType, sampletype_id, project_id)
        if searchType == "UIDs":
            filtersdic['searchText'] = filters['filter_searchUIDs']
            field = 'uid'
            filtersdic['tableField'] = SAMPLE_FILTER_MAPPING[field]
        elif searchType=="Advanced":
            filtersdic['searchText'] = filters['filter_searchText']
            sampletype_id = filters['sampletype_id']
            filtersdic['sampletype_id'] = sampletype_id
            filtersdic['filterRules'] = [{
                "field":"sample_type_id",
                "op":"equal",
                "value":sampletype_id
            }]
            # the following not in use
            attribute = filters['attribute']
            filtersdic['attribute'] = attribute
            #filter_logic = filters['filter_logic']
            #filter_searchValue = filters['filter_searchValue']
            filtersdic['matchType'] = filters['filter_matchType']
        elif searchType=="FILTERING":
            filtersdic['searchText'] = None
            sampletype_id = filters['sampletype_id']
            attribute = filters['attribute']
            filter_rule = filters['filter_rule']
            filter_valueFrom = filters['filter_valueFrom']
            filter_valueTo = filters['filter_valueTo']
            rules = [{
                "field":"sample_type_id",
                "op":"equal",
                "value":sampletype_id
            }]
            if attribute!='none':
                sattr = DBtable_sampleattribute()
                msg, status = sattr.validateFilters(sampletype_id, attribute, filter_rule, filter_valueFrom, filter_valueTo)
                if status==0:
                    #data = {'msg':msg, 'status': status}
                    #reportData = simplejson.dumps(data, default=str)
                    #return reportData
                    return msg, status, filtersdic
            else:    
                #attribute=='none':  
                # no attribute is selected
                keyword = toString(filter_valueFrom)
                keyword = keyword.strip()
                if len(keyword)>0:
                    # Add additional rule for keyword search
                    field = "json_metadata"
                    #tableField = SAMPLE_FILTER_MAPPING[field]
                    rule = {
                        "field":field,
                        "op":"contains",
                        "value":keyword
                    }
                    rules.append(rule)
                    filtersdic['searchText'] = keyword
            
            filtersdic['filterRules'] = rules
            filtersdic['sampletype_id'] = sampletype_id
        else:
            filtersdic['searchText'] = None
            field = "json_metadata"
            filtersdic['tableField'] = SAMPLE_FILTER_MAPPING[field]
            filtersdic['sampletype_id'] = sampletype_id
        
        filtersdic['attribute'] = attribute
        filtersdic['filter_rule'] = filter_rule
        filtersdic['filter_valueFrom'] = filter_valueFrom
        filtersdic['filter_valueTo'] = filter_valueTo
        return msg, status, filtersdic
    
    def searchAdvanced(self, user_seek, filters, searchType, project_id=0):
        logger.debug('searchAdvanced')
        msg, status, filtersdic = self.__parseSearchFilters(filters, searchType, project_id)
        if status==0:
            data = {'msg':msg, 'status': status}
            reportData = simplejson.dumps(data, default=str)
            return reportData
        
        data = self.__retrieveRecords_advanced(user_seek, filtersdic)
        
        if searchType=="UIDs":
            msg = 'ignore filtering'
            data['tree'] = self.__getAttributeTree(data['rows'])
        elif searchType=="FILTERING":
            attribute = filtersdic['attribute']
            if attribute!='none':
                sampletype_id = filtersdic['sampletype_id']
                filter_rule = filtersdic['filter_rule']
                filter_valueFrom = filtersdic['filter_valueFrom']
                filter_valueTo = filtersdic['filter_valueTo']
                rows = self.__filterSamples(data['rows'], sampletype_id, attribute, filter_rule, filter_valueFrom, filter_valueTo) 
                data['rows'] = rows
                data['total'] = len(rows)
            elif filtersdic['searchText'] is not None:
                filtersdic['matchType'] = 'CONTAIN'
                rows = self.__filterSamples_advanced(data['rows'], filtersdic)    
                data['rows'] = rows
                data['total'] = len(rows)
        else:
            rows = self.__filterSamples_advanced(data['rows'], filtersdic)
            data['rows'] = rows
            data['total'] = len(rows)
            
            sampleTypes = []
            for row in rows:
                sampleType = row['sample_type']
                if sampleType not in sampleTypes:
                    sampleTypes.append(sampleType)
            data['sampleTypes'] = sampleTypes
            data['noSampleTypes'] = len(sampleTypes)
            
        data['msg'] = 'okay'
        data['status'] = 1
        reportData = simplejson.dumps(data, default=str)
        return reportData
    
    def __retrieveRecords_advanced(self, user_seek, filtersdic):
        sqlquery = self.__sqlQuery_select_records(filtersdic)
        headers = SAMPLE_HEADERS
        db_alias = settings.SEEK_DATABASE
        jdata = self.db.queryToListDics(sqlquery, headers, db_alias)
        total = len(jdata)
        #sqlquery = self.__sqlQuery_select_records(filtersdic, False)
        #total = self.db.getQueryValue(sqlquery, db_alias)
        #if total is None:
        #    total = 0
        #else:
        #    total = int(total)
    
        jdata_new = self.reformatDataForClient(jdata)
        footer = []
        data = {'total':total,'rows':jdata_new,'footer':footer}
        return data
        
    def __sqlQuery_select_records_filters_advanced(self, filtersdic):
        from .search import Search
        spi = Search('')
        sqlquery_filter = spi.designSearchAdvanced(filtersdic, SAMPLE_FILTER_MAPPING)            
        if 'project_id' in filtersdic:
            project_id = filtersdic['project_id']
            if int(project_id)>0:
                sqlquery_filter = sqlquery_filter.replace('WHERE ', 'WHERE (')
                sqlquery_filter = sqlquery_filter + ") AND D.project_id=" + str(project_id)    
            
        logger.debug(sqlquery_filter)
        return sqlquery_filter
            
    
    def __highlightKeyword(self, keyword, value, style=None):
        defaultStyle = "color:red;"
        if style is None:
            style = defaultStyle

        # The result is rendered as raw HTML by the client, so HTML-escape the
        # untrusted keyword/value before splicing them into the highlight span.
        # html.escape maps each character independently, so substring matching
        # on the escaped strings is equivalent to matching on the originals.
        keyword = html.escape(str(keyword))
        value = html.escape(str(value))

        if keyword in value:
            newKeyword = '<span style="' + style + '">' + keyword + '</span>'
            value = value.replace(keyword, newKeyword)
        else:
            kl = keyword.lower()
            vl = value.lower()
            if kl in vl:
                pos = vl.find(kl)
                keyw = value[pos:(pos+len(keyword))]
                newKeyword = '<span style="' + style + '">' + keyw + '</span>'
                value = value.replace(keyw, newKeyword)
        return value
    
    def __highlightKeyValues(self, dici, terms, matchType, attribute=None):
        separator = ',   '
        attributeValue = ''
        ki = 0
        for key, value in sorted(dici.items()):
            if value is None:
                continue
            try:
                value = str(value)
            except:
                continue
            
            if attribute is not None:
                if key==attribute:
                    key = self.__highlightKeyword(key, key, "color:blue;font-weight:bold;")
                    attributeValue += key + ':' + self.__highlightKeyword(value, value)
                continue
                
            key = self.__highlightKeyword(key, key, "color:blue;font-weight:bold;")
            valuel = value.lower()
            for term in terms:
                if matchType=='EXACT':
                    if term==value or term.upper()==value.upper():
                        if ki==0:
                            attributeValue += key + ':' + self.__highlightKeyword(term, value)
                        else:
                            attributeValue += separator + key + ':' + self.__highlightKeyword(term, value)
                        ki += 1
                    continue   
                        
                if term in value or term.lower() in valuel:     
                    if ki==0:
                        attributeValue += key + ':' + self.__highlightKeyword(term, value)
                    else:
                        attributeValue += separator + key + ':' + self.__highlightKeyword(term, value)
                    ki += 1
                elif "&" in term:
                    termi = term.split("&")
                    for ti in termi:
                        if ':' in ti:
                            ti = self.__getCleanKeyword(ti)
                                
                        if ti in value or ti.lower() in valuel:
                            if ki==0:
                                attributeValue += key + ':' + self.__highlightKeyword(ti, value)
                            else:
                                attributeValue += separator + key + ':' + self.__highlightKeyword(ti, value)
                            ki += 1
                elif "^" in term:
                    termi = term.split("^")
                    for ti in termi:
                        if ':' in ti:
                            ti = self.__getCleanKeyword(ti)

                        if ti in value or ti.lower() in valuel:
                            if ki==0:
                                attributeValue += key + ':' + self.__highlightKeyword(ti, value)
                            else:
                                attributeValue += separator + key + ':' + self.__highlightKeyword(ti, value)
                            ki += 1            
                elif ':' in term:
                    term = self.__getCleanKeyword(term)
                    if term in value or term.lower() in valuel:
                        if ki==0:
                            attributeValue += key + ':' + html.escape(str(value))
                        else:
                            attributeValue += separator + key + ':' + self.__highlightKeyword(term, value)
                        ki += 1
                    
        return attributeValue
    
    def __filterSamples_advanced(self, jdata, filtersdic):
        filterRules = filtersdic['filterRules']
        sampletype_id = filtersdic['sampletype_id']
        attribute = filtersdic['attribute']
        matchType = filtersdic['matchType']
        
        filter_rule = None
        filter_valueFrom = None
        filter_valueTo = None
        
        searchText = filtersdic['searchText']
        from .search import Search
        spi = Search('')
        tableField = 'json_metadata'
        categoryField = 'sample_type_id'
        query, terms = spi.designSearchPubmed(searchText, tableField, categoryField)
        
        sampletype_id = 0
        
        n = 0
        separator = ',   '
        jdata_new = []
        for data in jdata:
            json_metadata = data['json_metadata']
            sample_type_id = data['sample_type_id']
            dici = self.__getRecordFromJson(json_metadata)
            
            attributeValue = self.__highlightKeyValues(dici, terms, matchType)

            data['json_metadata'] = json.loads(data['json_metadata'])
            
            if len(attributeValue)==0:
                continue
    
            data['attributeValue'] = attributeValue
            if sampletype_id>0:
                if sampletype_id==sample_type_id:
                    jdata_new.append(data)
                    n += 1
            else:
                jdata_new.append(data)
                n += 1

        return jdata_new
    
    
    def __getCleanKeyword(self, keywordIn):
        keywordOut = keywordIn
        if ':' in keywordIn:
            tii = keywordIn.split(':')
            if len(tii)==3:
                keywordOut = tii[2]
            else:
                keywordOut = tii[-1]
        
        return keywordOut
        
 
    def __createSampleTreeFromDB(self, sample_ids):
        from .models import Sample_tree
        
        includeChilren = True
        parentList = []
        ntotal = len(sample_ids)
        n = 0
        for sample_id in sample_ids:
            n += 1
            msg = "Retrieve sample tree " + str(sample_id) + " " + str(n) + "/" + str(ntotal)
            logger.debug(msg)
            id = int(sample_id)
            objs = Sample_tree.objects.filter(sample_id=id)
            total = objs.count()
            if total==1:
                obj = objs[0]
                fullTree = obj.full
                atree = json.loads(fullTree)
                fullTreeList = atree
                upTreeList = fullTreeList
            else:
                upTreeList, parent_uids = self.__createMultiParentTree(sample_id, includeChilren)

            parentList_i = self.__getChildrenListLoop(upTreeList)
            parentList += parentList_i
        
        sampleTypes, sampleTypeCount, headers, headersMapping = self.__getSampleTypeAttributes(parentList)       
        headers_new, diclist_new = self.__convertSampleTreeToList(parentList, sampleTypes, sampleTypeCount, headers)       
        return headers_new, diclist_new, headersMapping
    
    def __getAttributeTree(self, jdata):
        sample_ids = []
        for data in jdata:
            id = data['id']
            sample_ids.append(id)

        includeSampleTree = 1
        if includeSampleTree==1:
            headers_new, diclist_new, headersMapping = self.__createSampleTreeFromDB(sample_ids)
            headers_noneConstant, diclist_constant, headers_constant = getConstantRows(headers_new, diclist_new)

            logger.debug(f"RETRIEVE: {diclist_new}")
            
            headers_inConstant = []
            for dici in diclist_constant:
                header = dici[headers_constant[0]]
                headers_inConstant.append(header)
                
            headers_filter = []
            for header in headers_new:
                if header in headers_noneConstant or header in headers_inConstant:
                    headers_filter.append(header)
                
            stypes = []
            for header in headers_filter:
                if ':' not in header:
                    continue
                
                terms = header.split(':')
                stype = terms[0]
                if '_' in stype:
                    # such as 'DNA_1'
                    terms = stype.split('_')
                    stype = terms[0]
                    
                if stype not in stypes:
                    stypes.append(stype)
            
            treeChildren = {}
            uniqueIDs = []
            for header in headers_filter:
                if ':' not in header:
                    continue
                
                terms = header.split(':')
                stype = terms[0]
                if '_' in stype:
                    # such as 'DNA_1'
                    terms2 = stype.split('_')
                    stype = terms2[0]
                
                if stype in treeChildren:
                    children = treeChildren[stype]
                else:
                    children = []
                
                attribute = terms[1]
                uniqueID = stype + ':' + attribute
                if uniqueID in uniqueIDs:
                    # such as header='DNA_1:uid' and 'DNA_2:uid', uniqueID='DNA:uid'
                    continue
                else:
                    uniqueIDs.append(uniqueID)
                
                child = {}
                child['id'] = uniqueID
                if header in headers_noneConstant:
                    #child["text"] = attribute
                    child["text"] = '<span style="color:red;">' + attribute + '</span>'
                else:
                    #child["text"] = '<span style="color:red;">' + attribute + '</span>'
                    child["text"] = attribute
                child['checked'] = 'true'
                children.append(child)
                treeChildren[stype] = children
            
            tree = []    
            for stype in stypes:
                node = {}
                node['id'] = stype
                node['text'] = stype
                node['state'] = 'closed'
                node['checked'] = 'true'
                node['children'] = treeChildren[stype]
                tree.append(node)
            
            return tree
        else:
            return None

    def parseSampleIDs(self, sample_ids):
        sampleDiclist = self.retrieveRecordsByIDs(sample_ids)
        sampleTypes = {}
        for dici in sampleDiclist:
            id = dici['id']
            uid = dici['uuid']
            terms = uid.split('-')
            sampleType = terms[0]
            if sampleType in sampleTypes:
                slist = sampleTypes[sampleType]
            else:
                slist = []
            slist.append(id)
            sampleTypes[sampleType] = slist
            
        return sampleTypes
        
    def __createSampleTreeFromDB_noTree(self, sample_ids):
        logger.debug("createSampleTreeFromDB_noTree")
        from .models import Sample_tree
        
        includeChilren = True
        parentList = []
        ntotal = len(sample_ids)
        n = 0
        sampleTypes = {}
        for sample_id in sample_ids:
            n += 1
            msg = "Retrieve sample tree " + str(sample_id) + " " + str(n) + "/" + str(ntotal)
            logger.debug(msg)
            id = int(sample_id)
            objs = Sample_tree.objects.filter(sample_id=id)
            total = objs.count()
            if total==1:
                obj = objs[0]
                fullTree = obj.full
                atree = json.loads(fullTree)
                fullTreeList = atree
                upTreeList = fullTreeList
            else:
                upTreeList, parent_uids = self.__createMultiParentTree(sample_id, includeChilren)

            parentList_i = self.__getChildrenListLoop(upTreeList)
            parentList += parentList_i
            '''
            sampleTypes_i = self.__getChildrenListLoop_noTree(upTreeList)
            for sampleType in sampleTypes_i:
                uids_i = sampleTypes_i[sampleType]
                if sampleType in sampleTypes:
                    uids = sampleTypes[sampleType]
                    for uid in uids_i:
                        if uid not in uids:
                            uids.append(uid)
                    sampleTypes[sampleType] = uids
                else:
                    sampleTypes[sampleType] = uids_i
            '''
        sampleTypes = self.__getTreeSampleTypes(parentList)
        return sampleTypes
        
    def __getSampleTypeFromUID(self, sampleUID):
        if "-" in sampleUID:
            terms = sampleUID.split('-')
            sampleType = terms[0]
            if '_' in sampleType:
                # such as 'DNA_1'
                terms = sampleType.split('_')
                sampleType = terms[0]
        else:
            sampleType = uid
        return sampleType
        
    def __getTreeSampleTypes(self, parentList):
        logger.debug("getTreeSampleTypes")
        sampleTypes = {}
        for listi in parentList:
            for uid in listi:
                sampleType = self.__getSampleTypeFromUID(uid)
                if sampleType in sampleTypes:
                    uids = sampleTypes[sampleType]
                else:
                    uids = []
                
                if uid not in uids:
                    uids.append(uid)
                sampleTypes[sampleType] = uids
        return sampleTypes
    
    def __retrieveSamples(self, headers, sample_uids):
        return self.__retrieveSamples_v2(headers, sample_uids)
            
    def __retrieveSamples_v2(self, headers, sample_uids):
        logger.debug("retrieveSamples_v2")
        status = 1
        msg = ''
        nsamplesOutput = 0
        diclist = []
        
        query = {}
        query['uuid__in'] = sample_uids
        qset = Q(**query)
        records = self.queryRecordsCustom(qset)
        if len(records)==0:
            msg = 'retrieveSamples_v2: Custom retrieval not working'
            logger.debug(msg)
            return diclist, msg, status, nsamplesOutput
        
        #print(records)
        for record in records:  
            if record is None:
                msgi = 'Error: Sample not found in DB '
                status = 0
                msg += msgi + '<br/>'
                logger.debug(msgi)
                continue
            
            json_metadata = record['json_metadata']
            dici = self.__getRecordFromJson(json_metadata)
            dici_rev = {}
            for header in headers:
                hi = header.strip()
                if hi in dici:
                    dici_rev[header] = dici[hi]
                else:
                    dici_rev[header] = ''
            
            diclist.append(dici_rev)
            nsamplesOutput += 1
        
        #n1 = len(diclist)
        #diclist = removeRedundancy(headers, diclist)
        #n2 = len(diclist)
        #msg = "Number of rows before and after filtering: " + str(n1) + ' ' + str(n2)
        #logger.debug(msg)
        return diclist, msg, status, nsamplesOutput
        
    def __exportSamplesInZipfile(self, sampleTypes, dzipfile='test.zip', attributeFilter=None):
        logger.debug("exportSamplesInZipfile")
        
        headersFiltered = []
        if attributeFilter is not None and len(attributeFilter)>0:
            if ',' in attributeFilter:
                headersFiltered = attributeFilter.split(',')
        if len(headersFiltered)==0:
            logger.error(attributeFilter)
            return 0
                
        sattr = DBtable_sampleattribute()
        dtype = DBtable_sampletype()
        zf = zipfile.ZipFile(dzipfile, mode='w')
        
        n = 0
        for sampleType in sampleTypes:
            suffix = '-' + sampleType + '.xls'
            downfilei = dzipfile.replace('.zip', suffix)
            #logger.debug(downfilei)
            
            sample_uids = sampleTypes[sampleType]
            sample_type_id = dtype.getSampleTypeID(sampleType)
            attributeInfo = sattr.getAttributeInfo(sample_type_id)
            headers = attributeInfo['headers']
            headers_new = []
            for header in headers:
                header_new = sampleType + ':' + header.lower()
                if header_new in headersFiltered:
                    headers_new.append(header)
            
            if len(headers_new)>0:
                logger.debug(downfilei)
                diclist, msg, status, nsamplesOutput = self.__retrieveSamples(headers_new, sample_uids)
                isNewSheet = True
                saveExcelDiclist(downfilei, headers_new, diclist, 'Samples', isNewSheet)
                terms = downfilei.split('/')
                filenamei = terms[-1]
                if status:
                    zf.write(downfilei, filenamei)
                    n += nsamplesOutput
            else:
                msg = 'exportSamplesInZipfile: No metadata for sampletype: ' + sampleType
                logger.debug(msg)
                
        return n
    
    def __exportSamplesInExcel(self, sampleTypes, excelfile, attributeFilter=None):
        logger.debug("exportSamplesInExcel")
        
        headersFiltered = []
        if attributeFilter is not None and len(attributeFilter)>0:
            if ',' in attributeFilter:
                headersFiltered = attributeFilter.split(',')
        #if len(headersFiltered)==0:
        #    logger.error(attributeFilter)
        #    return 0
        excludeEmptyColumns = False
        if len(headersFiltered)==0:
            excludeEmptyColumns = True
                
        sattr = DBtable_sampleattribute()
        dtype = DBtable_sampletype()
        n = 0
        isNewFile = True
        for sampleType in sampleTypes:
            sample_uids = sampleTypes[sampleType]
            sample_type_id = dtype.getSampleTypeID(sampleType)
            attributeInfo = sattr.getAttributeInfo(sample_type_id)
            headers = attributeInfo['headers']
            headers_new = []
            for header in headers:
                header_new = sampleType + ':' + header
                if header_new in headersFiltered:
                    headers_new.append(header)
                elif excludeEmptyColumns:
                    headers_new.append(header)
            
            if len(headers_new)>0:
                msg = 'exportSamplesInExcel: Retrieve sampletype: ' + sampleType + ' ' + str(len(sample_uids))
                print(msg)
                diclist, msg, status, nsamplesOutput = self.__retrieveSamples(headers_new, sample_uids)
                if isNewFile:
                    saveExcelDiclist(excelfile, headers_new, diclist, sampleType, isNewFile, excludeEmptyColumns)
                else:
                    AddExcelDiclist(excelfile, headers_new, diclist, sampleType, excludeEmptyColumns)
               
                isNewFile = False
                if status:
                    n += nsamplesOutput
            else:
                msg = 'exportSamplesInExcel: No metadata for sampletype: ' + sampleType
                #print(msg)
                #print(headers)
                #print(headers_new)
                #print(headersFiltered)
                logger.debug(msg)
                
        return n
        
    def downloadSamples_noTree(self, user_seek, dzipfile, link, sample_ids, includeSampleTree=1, attributeFilter=None):
        logger.debug("downloadSamples_noTree")
        
        sampleTypes = self.__createSampleTreeFromDB_noTree(sample_ids)
        
        if ".zip" in dzipfile:
            # download a zip file, in which each sample type has its own excel file
            nsamplesOutput = self.__exportSamplesInZipfile(sampleTypes, dzipfile, attributeFilter)
        else:
            downloadfile = dzipfile
            nsamplesOutput = self.__exportSamplesInExcel(sampleTypes, downloadfile, attributeFilter)
        data = {}
        data['link'] = link
        #if nsamplesOutput>=len(sample_ids):
        if nsamplesOutput>0:
            data['msg'] = 'okay'
            data['status'] = 1
        else:
            data['msg'] = 'Warning: Number of samples output: ' + str(nsamplesOutput) + ' is less than the number selected: ' + str(len(sample_ids))
            data['status'] = 0
            
        reportData = simplejson.dumps(data, default=str)
        return reportData    
        
