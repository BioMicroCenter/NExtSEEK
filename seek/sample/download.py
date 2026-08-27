"""Sample download, export, publish and retrieval-data flows."""

from dmac.csv_excel import AddExcelDiclist
from ..dbtable_sampleattribute import DBtable_sampleattribute
from ..dbtable_sampletype import DBtable_sampletype
from django.db.models import Q
import datetime
from dmac.iocsv import getConstantRows
import json
from dmac.csv_excel import load_excelfile_asdic
from dmac.csv_excel import modifyExcelCell
import pandas as pd
from dmac.csv_excel import removeRedundancy
from dmac.csv_excel import saveExcelDiclist
from dmac.iocsv import saveTwoDiclistsIntoExcel
import simplejson
from nextseek_api.services.sample_workbook import write_samples_workbook
import zipfile

from .constants import PUBLISH_SERVER, logger


class SampleDownloadMixin:
    """Mixin for :class:`~seek.sample.table.DBtable_sample`."""

    def _parse_json_metadata(self, metadata_series):
        return metadata_series.apply(lambda x: json.loads(x) if isinstance(x, str) else {})

    def _parse_children_uids(self, children_uids):
        children_uids['json_metadata'] = self._parse_json_metadata(children_uids['json_metadata'])

        metadata_df = pd.json_normalize(children_uids['json_metadata'])
        metadata_df = metadata_df.loc[:, ~metadata_df.columns.duplicated()]

        final_df = pd.concat([children_uids[['uuid']], metadata_df], axis=1)
        final_df.replace("", pd.NA, inplace=True)
        final_df.dropna(axis=1, how='all', inplace=True)

        return final_df

    def sampleRetrievalData(self, children_uids, output):
        # Sheet layout, README included, is owned by
        # nextseek_api.services.sample_workbook so it cannot drift per call path.
        write_samples_workbook(self._parse_children_uids(children_uids), output)

    def _saveSampleList(self, headers_new, diclist_new, excelfile, attributeFilter=None):
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

    def _createSampleTreeToList_new(self, sample_ids, xlsfile='test.xls', attributeFilter=None):
        headers_new, diclist_new, headersMapping = self._createSampleTreeFromDB(sample_ids)
        nsamplesOutput = self._saveSampleList(headers_new, diclist_new, xlsfile, attributeFilter)
        return nsamplesOutput

    def downloadSamples_new(self, user_seek, xlsfile, link, sample_ids, includeSampleTree=1, attributeFilter=None):
        if includeSampleTree==1:
            nsamplesOutput = self._createSampleTreeToList_new(sample_ids, xlsfile, attributeFilter)
        else:
            isNewSheet = True
            msg, status, nsamplesOutput = self._downloadSampleList(sample_ids, xlsfile, isNewSheet)
        
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

    def exportSamples(self, user_seek, xlsfile, link, sample_ids, sampletype_id):
        stype = DBtable_sampletype()
        sampletypeName = stype.retrieveFieldValue(sampletype_id, 'title')
        return self._exportSamples0(user_seek, xlsfile, link, sample_ids, sampletypeName)

    def _publishSampleList(self, user_seek, sample_ids, xlsfile, assay_id=None, project_id=None):
        isNewSheet = False
        excelfile = xlsfile
        msg, status, nsamplesOutput = self._downloadSampleList(sample_ids, xlsfile, isNewSheet)
        
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
        msg, status = self._publishSampleList(user_seek, sample_ids, xlsfile, assay_id, project_id)
        
        data = {}
        data['msg'] = msg
        data['status'] = status
        data['link'] = link
        data['ptype'] = 'Sample'
        reportData = simplejson.dumps(data, default=str)
        return reportData

    def _loadPublishedSampleSheet(self, excelfile):
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
        
        msg, status, sampletype, uids = self._loadPublishedSampleSheet(excelfile)
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
            
        sdata = self._exportSamples0(user_seek, downloadfile, link, sample_ids, sampletype)
        return sdata 

    def _createSampleTreeFromDB_noTree(self, sample_ids):
        logger.debug("createSampleTreeFromDB_noTree")
        from ..models import Sample_tree
        
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
                upTreeList, parent_uids = self._createMultiParentTree(sample_id, includeChilren)

            parentList_i = self._getChildrenListLoop(upTreeList)
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
        sampleTypes = self._getTreeSampleTypes(parentList)
        return sampleTypes

    def _getSampleTypeFromUID(self, sampleUID):
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

    def _getTreeSampleTypes(self, parentList):
        logger.debug("getTreeSampleTypes")
        sampleTypes = {}
        for listi in parentList:
            for uid in listi:
                sampleType = self._getSampleTypeFromUID(uid)
                if sampleType in sampleTypes:
                    uids = sampleTypes[sampleType]
                else:
                    uids = []
                
                if uid not in uids:
                    uids.append(uid)
                sampleTypes[sampleType] = uids
        return sampleTypes

    def _retrieveSamples(self, headers, sample_uids):
        return self._retrieveSamples_v2(headers, sample_uids)

    def _retrieveSamples_v2(self, headers, sample_uids):
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
        return diclist, msg, status, nsamplesOutput

    def _exportSamplesInZipfile(self, sampleTypes, dzipfile='test.zip', attributeFilter=None):
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
                diclist, msg, status, nsamplesOutput = self._retrieveSamples(headers_new, sample_uids)
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

    def _exportSamplesInExcel(self, sampleTypes, excelfile, attributeFilter=None):
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
                diclist, msg, status, nsamplesOutput = self._retrieveSamples(headers_new, sample_uids)
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
        
        sampleTypes = self._createSampleTreeFromDB_noTree(sample_ids)
        
        if ".zip" in dzipfile:
            # download a zip file, in which each sample type has its own excel file
            nsamplesOutput = self._exportSamplesInZipfile(sampleTypes, dzipfile, attributeFilter)
        else:
            downloadfile = dzipfile
            nsamplesOutput = self._exportSamplesInExcel(sampleTypes, downloadfile, attributeFilter)
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
