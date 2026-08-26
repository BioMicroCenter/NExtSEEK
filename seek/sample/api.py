"""The endpoints the api_app and nextseek_api packages call."""

from ..dbtable_sampleattribute import DBtable_sampleattribute
from ..dbtable_sampletype import DBtable_sampletype
from ..models import Projects
from ..models import Projects_samples
import simplejson

from .constants import SAMPLE_CONTRIBUTOR_ACCESSOR_NAME, SAMPLE_ERRORCODE, SAMPLE_FILE_ACCESSOR_NAME, SAMPLE_LINK_ACCESSOR_NAME, SAMPLE_PROTOCOL_ACCESSOR_NAME


class SampleApiMixin:
    """Mixin for :class:`~seek.sample.table.DBtable_sample`."""

    def _verifyFileInRecord(self, sampleRecord, originalfilename, filetype):
        if 'json_metadata' not in sampleRecord:
            return False
        
        fileInRecord = 0
        json_metadata = sampleRecord['json_metadata']
        sampledic = self._getRecordFromJson(json_metadata)
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
            sample_uid = record['uuid']
            msg += "Lab: " + creator_lab + "; sample UID: " + sample_uid
            if sample_uid is not None and creator_lab in sample_uid:
                fileInRecord, sampledic = self._verifyFileInRecord(record, originalfilename, filetype)
                if fileInRecord>0:
                    record.update(sampledic)
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
        record_db = self._retrieveSampleByUID(sample_uid)
        if record_db is None:
            msg = 'Error: sample not found: ' + sample_uid
            return msg, status
        
        metadata = record_db['json_metadata']
        sampleDic = self._getRecordFromJson(metadata)
        
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

    def _getSampleTypeInfo(self, sampleType):
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

    def _verifySampleAttributes(self, record, headers_required):
        msg_required, meetRequired = self._verifyRequiredFields(record, headers_required)
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
        
        if not self._notEmptyLine(dici):
            msg = SAMPLE_ERRORCODE['501']
            return msg, 0, None
        
        msg, status, attributeInfo = self._getSampleTypeInfo(sampleType)
        if status==0:
            return msg, status, None
        sampletype_id = attributeInfo['sampletype_id']
        headers_required = attributeInfo['headers_required']
        headers = attributeInfo['headers']
        
        record = {}
        for header in headers:
            if header in dici:
                record[header] = dici[header]
        
        msg, status, samplename, scientist = self._verifySampleAttributes(record, headers_required)
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
        
        msg, status = self._verifySampleUID(samplename, contributor_id, uidIn, sampletype_id, scientist)
        if status==0:
            status = 0
            return msg, status, None
                
        if uidIn is not None and len(uidIn.strip())>0:
            isValid, msg = self._verifyUID(uidIn, attributeInfo)
            if not isValid:
                return msg, 0, None
        
        record_new, newSample = self._getRecord(user_seek, record, attributeInfo, contributor_id)
        uid = record_new['uuid']
        
        msg, status, sample_id = self.storeOneRecord(username, record_new)
        if status:
            if newSample:
                self._updateSampleProject(user_seek, sample_id)
                self._updateSampleAssetsCreators(sample_id, contributor_id)
                if len(diclist_assay)>0:
                    msgj, statusj = self._storeSample_assay_asset(user_seek, sampleType, sample_id, diclist_assay)
                    if statusj==0:
                        msgj = SAMPLE_ERRORCODE['601'] + msgj
                        msg += ';' + msgj
                else:
                    msg = 'Info: Assay info not available for updating array-sample relationship for sample id: ' + str(sample_id)
                    
            else:
                msg = 'Info: No update on array-sample relationship for old sample id: ' + str(sample_id)
                
            msgdf, statusdf = self._setSampleDatafileAssociation(user_seek, sampleType, dici, attributeInfo, diclist_assay)
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
        
        record = self._retrieveSampleByUID(sample_uid)
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
