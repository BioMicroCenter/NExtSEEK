"""Record access, identifier verification and metadata updates.

The helpers every other mixin leans on. Nothing here calls into a
feature mixin -- the dependency runs one way, which is what makes the
split a layering rather than a set of mutually recursive fragments."""

from ..models import Assets_creators
from ..dbtable_assay_assets import DBtable_assay_assets
from ..dbtable_data_files import DBtable_data_files
from ..dbtable_policies import DBtable_policies
from ..dbtable_sampleattribute import DBtable_sampleattribute
from ..dbtable_sampletype import DBtable_sampletype
import MySQLdb
from django.db.models import Q
import datetime
from dmac.conversion import getDefaultDateTime
import html
import json
import operator
from dmac.csv_excel import saveExcelDiclist
from django.conf import settings
import simplejson
from dmac.conversion import toInt
from dmac.conversion import toString

from .constants import ATTRIBUTETYPE_ID_URI, ATTRIBUTETYPE_ID_WEBLINK, RESERVED_DEFAULT_VALUE_FOR_UPDATE, RESERVED_REMOVE_VALUE_FOR_UPDATE, SAMPLE_DEFAULT, SAMPLE_ERRORCODE, SEEK_DATABASE, logger


class SampleCore:
    """Mixin for :class:`~seek.sample.table.DBtable_sample`."""

    def _runQuery(self, query, withColumns=False):
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

    def _notEmptyLine(self, csvdic):
        notEmpty = True
        if len(csvdic)==0:
            notEmpty = False
            return notEmpty
            
        allNone = True
        for key, value in csvdic.items():
            if value is not None:
                allNone = False
            
        if allNone:    
            notEmpty = False
            
        return notEmpty

    def getSampleUIDIndex(self, sampleUIDPrefix):
        records = self.db.retrieveRecords(self.tablemodel, 'uuid', sampleUIDPrefix)
        prefix = sampleUIDPrefix + '-'          
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

    def _defineUID(self, user_seek, record, attributeInfo):
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

    def _getRecordToJson(self, record, attributeInfo):
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

    def _getRecordFromJson(self, record_json):
        record = json.loads(record_json)
        return record

    def _updateSampleMetadata(self, metadata_db, metadata_in, attributes=None):
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

    def _getRecord(self, user_seek, record, attributeInfo, contributor_id):
        username = user_seek['username']
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
            uid = self._defineUID(user_seek, record, attributeInfo)
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
        record_new['json_metadata'] = self._getRecordToJson(record, attributeInfo)
        record_new['uuid'] = uid
        record_new['contributor_id'] = contributor_id
        
        policy = DBtable_policies("DEFAULT")
        record_db = self._retrieveSampleByUID(uid)
        if record_db is None:
            msg, status, policy_id = policy.createDefaultPolicy(username, contributor_id, project_id)
        else:
            policy_id = record_db['policy_id']
            contributor_id = record_db['contributor_id']
            record_new['contributor_id'] = contributor_id
            
            metadata_db = self._getRecordFromJson(record_db['json_metadata'])
            metadata_in = self._getRecordFromJson(record_new['json_metadata'])
            metadata_out = self._updateSampleMetadata(metadata_db, metadata_in)
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

    def _updateSampleProject(self, user_seek, sample_id):
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

    def _updateSampleAssetsCreators(self, sample_id, creator_id):
        records = Assets_creators.objects.filter(asset_id=sample_id, creator_id=creator_id, asset_type='Sample')
        if len(records)==1:
            return
        
        if len(records)>1:
            Assets_creators.objects.filter(asset_id=sample_id, creator_id=creator_id, asset_type='Sample').delete()
        
        timenow = getDefaultDateTime()
        record = Assets_creators(asset_id=sample_id, creator_id=creator_id, asset_type='Sample', created_at=timenow, updated_at=timenow)
        record.save()
        return

    def _verifyRequiredFields(self, record, fields_required):
        if 'UID' not in record.keys():
            msg = SAMPLE_ERRORCODE['503']
            meetRequired = False
            return msg, meetRequired
        
        uid = record['UID']
        if uid is not None and len(uid.strip())>0:
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

    def _setSampleDatafileAssociation(self, user_seek, sampleType, record, attributeInfo, diclist_assay):   
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

    def _verifyUID(self, uidIn, attributeInfo):
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
        
        record = self._retrieveSampleByUID(uidIn)
        if record is None:
            msg = "Error: Sample UID " + uidIn + " does not exist in DB for update "
            isValid = False
            return isValid, msg
        
        return isValid, msg

    def _storeSample_assay_asset(self, user_seek, sampleType, sample_id, diclist_assay):
        assay_assets = DBtable_assay_assets("DEFAULT")
        return assay_assets.storeSample_assay_asset(user_seek, sampleType, sample_id, diclist_assay)

    def _searchUniqueSample(self, samplename, scientist, sample_type_id):
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

    def _verifySampleUID(self, samplename, creator_id, uidIn, sample_type_id, scientist):
        samplename = str(samplename)
        records = self._searchUniqueSample(samplename, scientist, sample_type_id)
        
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

    def _retrieveSampleByUID(self, uid):
        record = None
        if uid is None or len(uid.strip())==0:
            msg = 'No record is found based on the input UID: ' + uid  # noqa: F841 (LATENT_BUGS #38)
            return None
        
        constraint = {'uuid':uid}
        records = self.queryRecordsByConstraint(constraint)
        if len(records)==1:
            record = records[0]
        
        return record

    def getSampleID(self, uid):
        sample_id = None
        record = self._retrieveSampleByUID(uid)
        if record is not None:
            sample_id = record['id']
            
        return sample_id

    def _retrieveSampleByID(self, idIn):
        record = None
        try:
            id = int(idIn)
        except:
            msg = 'No record is found based on the input ID: ' + idIn  # noqa: F841 (LATENT_BUGS #38)
            return None
        
        if id<=0:
            msg = 'No record is found based on the input ID: ' + idIn  # noqa: F841 (LATENT_BUGS #38)
            return None
        
        constraint = {'id':id}
        records = self.queryRecordsByConstraint(constraint)
        if len(records)==1:
            record = records[0]
        
        return record           

    def _getSeeklink(self, seek_type, id):
        seek_url = settings.SEEK_PUBLIC_URL + "/" + seek_type + "/" + str(id) + "/"
        seeklink = '<a href="' + seek_url + '" target="_blank">' + str(id) + '</a>'
        return seeklink

    def _getSamplelink(self, sample_uid, sample_id):
        sample_url = "/seek/sample/id=" + str(sample_id) + "/"
        # The uid column is rendered as raw HTML by the client; escape the uid
        # text so a sample uid containing markup cannot inject script.
        samplelink = '<a href="' + sample_url + '" target="_blank">' + html.escape(str(sample_uid)) + '</a>'
        return samplelink

    def reformatDataForClient(self, jdata):
        jdata_new = []
        for data in jdata:
            datadic = {}
            datadic['idlink'] = self._getSeeklink('samples', data['id'])
            datadic['idurl'] = self._getSamplelink(data['id'], data['id'])
            datadic['id'] = data['id']
            datadic['title'] = data['title']
            datadic['uuid'] = data['uid']
            datadic['uid'] = self._getSamplelink(data['uid'], data['id'])
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

    def _getSampleTypeAttributes(self, parentList):
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

    def _retrieveSampleJsonData(self, uid):
        record_db = self._retrieveSampleByUID(uid)
        if record_db is None:
            return None
            
        metadata = record_db['json_metadata']
        sampleDic = self._getRecordFromJson(metadata)
        return sampleDic

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
                
        record = self._retrieveSampleByUID(uidIn)
        if record is None:
            msg = "Error: Sample UID " + uidIn + " does not exist in DB for update "
            status = False
            logger.debug(msg)
            return msg, status
            
        json_metadata = record['json_metadata']
        dici_json = self._getRecordFromJson(json_metadata)
        
        metadata_db = dici_json
        metadata_in = dici
        metadata_out = self._updateSampleMetadata(metadata_db, metadata_in, attributes)
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

    def _downloadSampleList(self, sample_ids, xlsfile, isNewSheet=True):
        status = 1
        msg = ''
        sample_type_id = None
        sattr = DBtable_sampleattribute()
        sattrInfo = {}
        headers = None
        diclist = []
        nsamplesOutput = 0
        for sample_id in sample_ids:
            record = self._retrieveSampleByID(sample_id)
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
            dici = self._getRecordFromJson(json_metadata)
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
