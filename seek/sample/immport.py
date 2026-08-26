"""ImmPort export: sheet info, sample lists and the zip bundles."""

from ..dbtable_sops import DBtable_sops
from dmac.csv_excel import load_excelfile_asdic
import os
from dmac.iocsv import removeDiclistDuplicates
from dmac.csv_excel import reviseExcelDiclist
import simplejson
from dmac.conversion import toString
import zipfile

from .constants import DOWNLOAD_DIRECTORY, IMMPORT_TEMPLATES, IMMPORT_TEMPLATES_VERSION, IMMPORT_TEMPLATE_FILE


class SampleImmportMixin:
    """Mixin for :class:`~seek.sample.table.DBtable_sample`."""

    def _exportImmportSheetInfo(self, headersMapping, diclist_new, templatedata, sheetName, excelfile):
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

    def _exportImportProtocls(self, user_seek, diclist, zf):
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

    def _exportImmportSheetInfoZip(self, user_seek, headersMapping, diclist_new, templatedata, sheetName, txtfile, fileLabel, zf):
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
            diclistOut = self._exportImportProtocls(user_seek, diclistOut, zf)
        
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

    def _exportImmportSampleListZip(self, user_seek, headers_new, diclist_new, downloadfile, sampletypeName, headersMapping):
        if 'xlsx' in downloadfile:
            return self._exportImmportSampleList(user_seek, headers_new, diclist_new, downloadfile, sampletypeName, headersMapping)
            
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
            self._exportImmportSheetInfoZip(user_seek, headersMapping, diclist_new, filedata, sheetname, sheetfile, fileLabel, zf)
            zf.write(sheetfile, filename)
        
        zf.close()
        return "Okay", 1

    def _exportImmportSampleList(self, user_seek, headers_new, diclist_new, downloadfile, sampletypeName, headersMapping):
        if 'zip' in downloadfile:
            return self._exportImmportSampleListZip(user_seek, headers_new, diclist_new, downloadfile, sampletypeName, headersMapping)
            
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
            self._exportImmportSheetInfo(headersMapping, diclist_new, filedata, sheetname, downloadfile)
            
        return "Okay", 1

    def _exportImmportCreateSampleTreeToList(self, user_seek, sample_ids, downloadfile, sampletypeName):
        headers_new, diclist_new, headersMapping = self._createSampleTree(sample_ids)
        msg, status = self._exportImmportSampleList(user_seek, headers_new, diclist_new, downloadfile, sampletypeName, headersMapping)
        return msg

    def _exportSamples0(self, user_seek, downloadfile, link, sample_ids, sampletypeName):
        sampletypeName = 'D.MSP'
        nsamplesOutput = self._exportImmportCreateSampleTreeToList(user_seek, sample_ids, downloadfile, sampletypeName)
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
