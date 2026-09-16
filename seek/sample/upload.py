"""Batch sample upload: parsing, validation and the feedback sheet.

The rows land in MySQL here and nothing else. Every path that writes one enqueues an outbox row afterwards, and the
graph sync loop turns that into the sample's node, metadata, projects and lineage
(``nextseek_api/graph_sync/hooks.py``; the design's sections 5 and 7). The lineage used to be written inline, from
``_storeSample``, in a second step the uploader's request waited on and a bare ``except`` hid.
"""

from ..dbtable_assay_assets import DBtable_assay_assets
from ..dbtable_ontology import DBtable_ontology
from ..dbtable_sampleattribute import DBtable_sampleattribute
from ..dbtable_sampletype import DBtable_sampletype
from itertools import chain
from dmac.conversion import cleanString
from dmac.csv_excel import load_excelfile_asdic
from dmac.conversion import toString
from dmac.conversion import verifyValueType
from nextseek_api.graph_sync import hooks
import xlwt

from .constants import DELIMITER_DBFIELD, SAMPLE_CONTRIBUTOR_ACCESSOR_NAME, SAMPLE_ERRORCODE, SAMPLE_SHEET_NAMES, logger


def enqueueSampleSync(kind, sample_id):
    """Ask the graph sync loop for one sample, after the write above it has committed.

    ``kind`` is ``samples`` for a row that was written and ``retire`` for one that was deleted. An id that is not a
    positive integer names no sample -- ``getSampleID`` answers ``None`` for a UID it cannot find, and
    ``storeOneRecord`` answers ``-1`` when it stored nothing -- so nothing is enqueued for it.

    ``hooks.enqueue`` never raises: the MySQL write stands whatever the outbox does, and the nightly targeted sync
    finds a change whose row was lost. Returns whether the row was written, which only the tests read.
    """
    try:
        sample_id = int(sample_id)
    except (TypeError, ValueError):
        return False

    if sample_id <= 0:
        return False

    return hooks.enqueue(kind, 'sample:%d' % sample_id)


class SampleUploadMixin:
    """Mixin for :class:`~seek.sample.table.DBtable_sample`."""

    def _loadSampleTypes(self, diclist_instruction):
        sampleTypes = {}      
        sampleTypes_order = []
        for dici in diclist_instruction:
            if "Field" not in dici or "Database Field" not in dici:
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

    def _splitSampleTypes(self, sampleTypes, diclist_samples):
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
                    pass    # no unique identifier; _getRecord() reports it later
                elif samplename in unique_samples:
                    dici_sample = unique_samples[samplename]
                else:
                    unique_samples[samplename] = dici_sample
                        
                diclist.append(dici_sample)
                sample_sheets[sampleType] = diclist
        return sample_sheets

    def _storeSample(self, user_seek, sampleType, record, attributeInfo, diclist_assay, creator):
        """Store one sample row, then ask for its graph sync.

        Returns ``(msg, status, uid)``. The row lands in MySQL here; its node, metadata, projects and lineage are
        written later by the graph sync loop, from the outbox row enqueued after the commit (the design's section 5,
        elements E1, E2, E5 and E7).
        """
        username = user_seek['username']
        contributor_id = user_seek['user_id']

        creator_id = creator['user_id']
        project_id = creator['projectid']

        if not self._notEmptyLine(record):
            msg = SAMPLE_ERRORCODE['501']
            return msg, 0, None

        headers_required = attributeInfo['headers_required']

        msg_required, meetRequired = self._verifyRequiredFields(record, headers_required)
        if not meetRequired:
            msg = SAMPLE_ERRORCODE['502'] + msg_required
            return msg, 0, None

        if 'UID' not in record.keys():
            msg = SAMPLE_ERRORCODE['503']
            return msg, 0, None

        record_new, newSample = self._getRecord(creator, record, attributeInfo, contributor_id)
        uid = record_new['uuid']
        msg, status, sample_id = self.storeOneRecord(username, record_new)
        logger.debug(f"Username {username} storing record {record_new}")
        if status:
            if newSample:
                self._updateSampleProject(creator, sample_id)
                self._updateSampleAssetsCreators(sample_id, creator_id)
                if len(diclist_assay)>0:
                    msgj, statusj = self._storeSample_assay_asset(creator, sampleType, sample_id, diclist_assay)
                    if statusj==0:
                        msgj = SAMPLE_ERRORCODE['601'] + msgj
                        msg += ';' + msgj
                else:
                    msg = 'Info: Assay info not available for updating array-sample relationship for sample id: ' + str(sample_id)
            else:
                msg = 'Info: No update on array-sample relationship for old sample id: ' + str(sample_id)

            msgdf, statusdf = self._setSampleDatafileAssociation(creator, sampleType, record, attributeInfo, diclist_assay)
            if not statusdf:
                msgdf = SAMPLE_ERRORCODE['602'] + msgdf
                msg += ';' + msgdf

            # After the row is committed, never inside its write. Both branches enqueue: an existing sample was
            # rewritten by storeOneRecord, so its metadata and lineage in the graph are behind it too.
            enqueueSampleSync('samples', sample_id)
        else:
            msg = SAMPLE_ERRORCODE['504'] + msg

        return msg, status, uid

    def _updateSampleErrorMsg(self, sampledic_feeback, primaryField, msg, sampleType):
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

    def extractParents(self, json_metadata):
        """Collect parent tokens from every metadata key containing "Parent".

        Value handling matches the modern route's
        ``nextseek_api.batch_upload.helpers.collect_parent_tokens``: a value
        that is empty or not a ``str`` contributes no tokens, and empty tokens
        are dropped.

        Dropping empty tokens is the part that fixes a live failure, and the
        mechanism is not the obvious one. ``_getRecordToJson`` writes EVERY
        attribute of the sample type into ``json_metadata``, not just the ones
        the uploader filled in, so any sample type carrying an unused
        ``*Parent`` attribute ships a blank value on every row -- ``' '`` via
        ``toString(None)``, or ``''`` when the header is absent. The old code
        turned that into an ``''`` token; ``getSampleID('')`` matched no record
        and returned ``None``; the SQL built from that ``None`` was rejected by
        MySQL, the error was swallowed and ``None`` returned in its place; and
        ``len(None)`` raised ``TypeError``. Because the graph write this route
        used to make merged parents in a loop, the bare ``except`` upstream then
        abandoned every parent token ORDERED AFTER the blank one, leaving
        silent, partial lineage. That write is gone: the row is enqueued instead
        and the graph sync loop reads MySQL's parent tokens itself.

        The ``isinstance`` guard is defence-in-depth, not a fix for a path
        reachable from this route: values reaching here via ``_storeSample``
        have already been through ``toString``, so an integer from an Excel
        cell arrives as ``"12345"``. It matters because ``extractParents`` is
        public and reachable with a raw dict.

        Key matching is deliberately NOT aligned with the modern helper. This
        stays the case-sensitive substring test ``"Parent" in k``, which is
        narrower than ``"parent" in k.lower()``; and unlike the modern helper
        this does not deduplicate. Changing either is a behaviour change beyond
        the swallow-and-guard fix. Pinned by
        ``seek/tests/test_dbtable_sample_lineage.py`` ::
        ``TestExtractParentsUnchangedBehaviour``.
        """
        parents = []
        for k, v in json_metadata.items():
            if "Parent" in k:
                if not v or not isinstance(v, str):
                    continue
                parents.append(v)
        parents = list(map(lambda p: p.split(";"), parents))
        parents = list(chain(*parents))
        parents = [p.strip() for p in parents if p.strip()]
        return parents

    def _batchUploadTest(self, seekdb, sampleType, diclist, diclist_feedback, attributeInfo, attributeMapping, diclist_assay, uploadEnforced=False):
        user_seek = seekdb.user_seek
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
                
                dici_feedback = self._updateSampleErrorMsg(dici_feedback, primaryField, msgi, sampleType)
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
                
                dici_feedback = self._updateSampleErrorMsg(dici_feedback, primaryField, msgi, sampleType)
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
                dici_feedback = self._updateSampleErrorMsg(dici_feedback, primaryField, msgi, sampleType)
                diclist_new.append(dici_feedback)
                continue
            
            if contributor_id<=0:
                msgi = SAMPLE_ERRORCODE['305'] + contributor
                statusTest = False
                msg0 += msgi +  '<br/>'
                dici_feedback = self._updateSampleErrorMsg(dici_feedback, primaryField, msgi, sampleType)
                diclist_new.append(dici_feedback)
                continue
            

            if samplename in uids_predefined:
                dici['UID'] = uids_predefined[samplename]
            else:
                sample_type_id = attributeInfo['sampleType_id']
                uidIn = None
                if 'UID' in dici:
                    uidIn = dici['UID']
                msgi, statusi = self._verifySampleUID(samplename, creator_id, uidIn, sample_type_id, scientist)
                if statusi==0:
                    statusTest = False
                    msg0 += msgi +  '<br/>'
                    dici_feedback = self._updateSampleErrorMsg(dici_feedback, primaryField, msgi, sampleType)
                    diclist_new.append(dici_feedback)
                    continue
                
                if uidIn is not None and len(uidIn.strip())>0:
                    isValid, msgi = self._verifyUID(uidIn, attributeInfo)
                    if not isValid:
                        statusTest = False
                        msg0 += msgi +  '<br/>'
                        dici_feedback = self._updateSampleErrorMsg(dici_feedback, primaryField, msgi, sampleType)
                        diclist_new.append(dici_feedback)
                        continue
            
            msgi, statusi, uid = self._storeSample(user_seek, sampleType, dici, attributeInfo, diclist_assay, creator)

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
                dici_feedback = self._updateSampleErrorMsg(dici_feedback, primaryField, msgi, sampleType)
                
            header = sampleType + "::UID"
            if statusi:
                dici_feedback[header] = uid
                dici_feedback[primaryField] = uid
            else:
                dici_feedback[header] = msgi
                
            diclist_new.append(dici_feedback)

        msg = 'The number of samples uploaded for ' + sampleType + ': ' + str(nright) + ' out of in total ' + str(ndici) + ' samples.'
        if not statusTest:
            msg = msg + '<br/>' + msg0
        else:
            msg = msg + '<br/>' + msg0
        
        return msg, statusTest, diclist_new

    def _batchUploadSampleTest(self, seekdb, sampleType, diclist_sample, diclist_feedback, attributeMapping, diclist_assay, uploadEnforced=False):
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
        
        msg, status, diclist_feedback = self._batchUploadTest(seekdb, sampleType, diclist_sample, diclist_feedback, attributeInfo, attributeMapping, diclist_assay, uploadEnforced)
        return msg, status, diclist_feedback

    def _outputUploadFeedback_V2(self, diclist, diclist_feedback, headers, feedbackfile):
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

    def _verifyUpdateSample(self, sheetData, feedbackfile):
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
            self._outputUploadFeedback_V2(diclist, diclist_sanity, headers, feedbackfile)
        
        #logger.debug('verifyUpdateSample: Finish')
        return msg, status, attributes

    def _batchUpdateSample(self, sheetData, feedbackfile, user_seek):
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
        
        msg, status, attributes = self._verifyUpdateSample(sheetData, feedbackfile)
        if status==0:
            logger.debug(msg)
            return msg, status
        
        headers.append('feedback')
        username = user_seek['username']

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
                # The row's metadata changed, so its properties, search_text and lineage in the graph are behind
                # it. updateSingleSample keeps no id, so the UID it has already resolved is read again here.
                enqueueSampleSync('samples', self.getSampleID(dici.get('UID', dici.get('uid'))))
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
                
        self._outputUploadFeedback_V2(diclist, diclist_feedback, headers, feedbackfile)
        #logger.debug(feedbackfile)
        #logger.debug('batchUpdateSample: Finish')
        return msg, status

    def _batchUpdateSampleAssociation(self, sheetData, feedbackfile, user_seek):
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
                # The sample's assay links changed, so every DERIVED_FROM edge it touches is relabelled (E8).
                enqueueSampleSync('samples', sample_id)
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
                
        self._outputUploadFeedback_V2(diclist, diclist_feedback, headers, feedbackfile)
        return msg, status

    def _runSanityCheck(self, diclist, diclist_ins, diclist_ont):
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
                return self._batchUpdateSample(sheetData, feedbackfile, user_seek)
            
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
            
            return self._batchUpdateSample(sheetData, feedbackfile, user_seek)
        
        elif 'UPDATE_ASSAY' in filedata['sheetnames'] and 'UPDATE_ASSAY' in filedata:
            sheetData = filedata['UPDATE_ASSAY']
            return self._batchUpdateSampleAssociation(sheetData, feedbackfile, user_seek)
        
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
        sampleTypes, sampleTypes_order = self._loadSampleTypes(diclist_ins)
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
        
        
        msg, status, diclist_sanity = self._runSanityCheck(diclist, diclist_ins, diclist_ont)
        if status==0:
            msg = 'Error: Refer to the feedback excel file for any error.'
            self._outputUploadFeedback_V2(diclist, diclist_sanity, headers, feedbackfile)
            return msg, status
        
        sample_sheets = self._splitSampleTypes(sampleTypes, diclist)
        
        msg = ""
        diclist_feedback = []
        for sampleType in sampleTypes_order:
            if sampleType in sample_sheets:
                diclist_sample = sample_sheets[sampleType]
                attributeMapping = sampleTypes[sampleType]

                msgi, statusi, diclist_feedback = self._batchUploadSampleTest(seekdb, sampleType, diclist_sample, diclist_feedback, attributeMapping, diclist_assay)                
                msg += msgi + "<br/>"
                if not statusi:
                    status = 0
            else:
                msgi = SAMPLE_ERRORCODE['105'] + sampleType
                status = 0
                msg += msgi + "<br/>"
                
        self._outputUploadFeedback_V2(diclist, diclist_feedback, headers, feedbackfile)
        return msg, status
