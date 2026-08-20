#!/usr/bin/env python

import json
import logging
logger = logging.getLogger(__name__)

from django.shortcuts import render
from django.shortcuts import redirect
from django.http import HttpResponseRedirect, HttpResponse
from django.contrib.auth.models import User
from django import forms

from django.conf import settings

import simplejson
import json
import datetime

#from csv_excel import load_file, load_excelfile
from dmac import csv_excel
#from pandas import read_excel

DOWNLOAD_DIRECTORY  = settings.MEDIA_ROOT + "/download/"
DOWNLOAD_DIRECTORY_LINK = settings.MEDIA_URL + '/download/' 

class DataGrid(object):
    def __init__(self, dbtable):
        self.dbtable = dbtable
        if dbtable is None:
            self.db = None
        else:
            self.db = dbtable.db
        
    def __querySuffix(self, ret):
        if 'rows' in ret:
            page = ret['page']
            rows = ret['rows']
            msg = "page: " + page + " rows: " + rows
            offset = (int(page)-1)*int(rows)     
            limit = " LIMIT " + str(offset) + "," + rows
        else:
            limit = ""
        
        msg = 'limit: ' + limit
        orderby = ""
        if 'sort' in ret:
            sort= ret['sort']
            order = 'desc'
            if 'order' in ret:
                order= ret['order']
        
            orderby = " ORDER BY " + sort + " " + order + " "
        
        suffix = orderby + limit
        return orderby, limit

    def __getStartRows(self, limit):
        strs1 = limit.strip().split(' ')       
        offset = 0
        rows = 0
        if len(strs1)>1:
            strs2 = strs1[1].split(',')        
            if len(strs2)>1:
                offset = int(strs2[0])
                rows = int(strs2[1])
          
        startNo = offset
        endNo = offset + rows
        return startNo, endNo

    def __getFilteringParameters(self, ret):
        """Parse the client's ``filterRules`` JSON. Returns the rules only.

        This used to also concatenate a SQL fragment from those rules, splicing
        both ``rule["field"]`` as a bare column identifier and ``rule["value"]``
        into ``LIKE '%...%'`` with no allowlist and no binding (#95). That
        fragment was dead: it was assigned to ``filtersdic['sqlquery_filter']``
        and ``self.sqlquery_filter``, and a repo-wide sweep over .py/.html/.js
        found writes at three sites and **no reads anywhere** -- no ``.get``,
        no dict spread, no template reference. ``retrieve_table_list``
        (``dmac/dbconn_django.py:731``) reads ``filterRules`` and builds a
        Django ORM ``Q()`` instead.

        It is deleted rather than allowlisted because there is nothing to keep
        working: an unused SQL builder one caller away from being first-order,
        whose identifier half could not be fixed by binding anyway. The live
        sibling that *does* emit SQL, ``__sqlQuery_select_filters`` below, maps
        the client's key through a server-owned ``fieldMapping`` and binds the
        value; that is the pattern any future caller should use.
        """
        if 'filterRules' not in ret:
            return []

        filterRules = ret['filterRules']
        if filterRules is None:
            return []

        return json.loads(filterRules)
    
    def getDatagridFilters(self, ret):
        filtersdic = {}
    
        orderby, limit = self.__querySuffix(ret)
        filtersdic['orderby'] = orderby
        filtersdic['limit'] = limit
    
        suffix = orderby + limit
        filtersdic['suffix'] = suffix
    
        startNo, endNo = self.__getStartRows(limit)
        filtersdic['startNo'] = startNo
        filtersdic['endNo'] = endNo
    
        filtersdic['filterRules'] = self.__getFilteringParameters(ret)
    
        return filtersdic
    
    def getDataGridData(self, request, username):
        ret = request.GET
        data = {}
        data["status"] = 0
        data["msg"] = "Processing records"
        data["link"] = ""
        if 'records' not in ret:
            data["msg"] = "Warning: No data provided for processing records."
            return data
        
        if username is None:
            data["msg"] = "Error: user login required."
            return data
      
        records = ret['records']
        records = json.loads(records)
        data['records'] = records
        data["status"] = 1
        return data
    
    def sqlQuery_select_filters(self, filtersdic, fieldMapping):
        """Build a bound WHERE fragment from datagrid filter rules.

        Returns ``(fragment, params)``. Every client-supplied rule value is
        emitted as a ``LIKE %s`` placeholder with the wildcards moved onto the
        *bound* value (``f"%{value}%"``), so the statement text is constant
        modulo the number of rules and no value can reach it (#93). This is
        the sibling of the concatenation defect fixed in ``seek/search.py``.

        The column identifier is never client-controlled: the client picks a
        *key* of ``fieldMapping`` and the emitted name is always the
        server-owned *value*, so the ``field_dg in fieldMapping`` guard below
        already closes the identifier surface.

        The signature change from a bare string to a 2-tuple broke nothing:
        as of this commit ``grep -rn sqlQuery_select_filters`` over the repo
        finds only this definition (plus the #93 design docs) — the method has
        no in-repo caller, so the binding here is prophylactic.
        """
        filterRules = filtersdic['filterRules']     # such as [{"field":"unit","op":"contains","value":"Amon"}]
        sqlquery_filter = ""
        params = []
        n = 0
        for rule in filterRules:
            field_dg = rule["field"]
            value = rule["value"]
            op = rule["op"]
            if field_dg in fieldMapping:
                field_db = fieldMapping[field_dg]

                if n==0:
                    sqlquery_filter += " WHERE " + field_db
                else:
                    sqlquery_filter += " AND " + field_db
                if op=="contains":
                    sqlquery_filter += " LIKE %s "
                else:
                    sqlquery_filter += " LIKE %s "
                params.append(f"%{value}%")
            n += 1

        return sqlquery_filter, params
    
    
    def __retrieve(self):
        filterRules = self.dbtable.formatRule(self.filterRules)
        
        qset = self.db.generateQuerySet(filterRules)
        jdata = self.db.retrieveJoint(self.dbtable.tablemodel, '', qset, self.orderby, self.limit)
        jdata_new = self.dbtable.reformatData(jdata)
        
        total = self.db.retrieveTotalRecords(self.dbtable.tablemodel, qset)
        return jdata_new, total
    
    def __uploadExcelFile(self, excelfile, feedbackfile):
        msg, status, jdata, total = self.dbtable.upload(excelfile, feedbackfile)
        if status==1:
            return msg, status, jdata, total
        
        headersMapping = self.dbtable.headersMapping()
        csvdata = csv_excel.load_file(excelfile, headersMapping)
        #csvdata = read_excel(excelfile).to_dict()
        status = csvdata['status']
        msg = csvdata['msg']
        
        jdata = []
        total = len(jdata)
        if status:
            msg = "excel file loaded okay"
        else:
            return msg, status, jdata, total
        
        csvdiclist = csvdata['diclist']
        n = 0
        msg0 = '\n'
        index = 0
        for csvdic in csvdiclist:
            msgn, statusn = self.dbtable.storeRecord(csvdic)
            index += 1
            if statusn:
                n += 1
            else:
                status = statusn
                msg0 += "Row " + str(index) + ". " + msgn + "\n"
            
        if status:  
            msg = 'Total number of records uploaded successfully: ' + str(n) 
        else:
            msg = msg0
        
        return msg, status, jdata, total
    
    def __upload(self, request):
        msg = 'Uploading records'
        status = 0
        link = ""
        if request.method == "POST":
            if request.FILES and request.FILES.get('excelfile_upload'):
                excelfile = request.FILES['excelfile_upload']
                if excelfile:
                    infilename = excelfile.name
                    names = infilename.split(".")
                    prefix = names[0]
                    filename = prefix + "_feedback.xls"
                    feedbackfile = DOWNLOAD_DIRECTORY + filename
                    link = DOWNLOAD_DIRECTORY_LINK + filename
                    msg, status, jdata, total = self.__uploadExcelFile(excelfile, feedbackfile)
                    
        return msg, status, link
        
    def __download(self, allids, downloadallterms):
        datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H")
        filename = self.dbtable.tablename + "-" + datenow + '.xls'
        excelfile = DOWNLOAD_DIRECTORY + filename
        link = DOWNLOAD_DIRECTORY_LINK + filename
        
        self.dbtable.downloadRecords(allids, excelfile, downloadallterms)
        msg = 'Retrieved in ' + filename
        status = 1
        
        return msg, status, link
    
    def __delete(self, ids):
        for id in ids:
            self.dbtable.delete(id)
        
        status = 1
        msg = "Deleting records successfully"
        return msg, status
    
    def __save(self, records):
        statusTest = 1
        msgTest = ""
        for record in records:
            msg, status = self.dbtable.storeOneRecord(record)
            if status==0:
                msgTest += msg
                statusTest = 0
            
        return msgTest, statusTest
        
    def process(self, request, operation):
        ret = request.GET
        self.orderby, self.limit = self.__querySuffix(ret)
        self.suffix = self.orderby + self.limit
        self.startNo, self.endNo = self.__getStartRows(self.limit)
        self.filterRules = self.__getFilteringParameters(ret)
        
        msg = operation
        status = 0
        link = " "
        total = 0
        jdata = None
        footer = {}
        if operation=="retrieve":
            jdata, total = self.__retrieve()
            status = 1
            msg = "Retrieved number of records: " + str(len(jdata))
        elif operation=="upload":
            msg, status, link = self.__upload(request)
        elif operation=="download":
            if 'allids' in ret:
                allids = json.loads(ret['allids'])
                downloadallterms = json.loads(ret['downloadallterms'])
                msg, status, link = self.__download(allids, downloadallterms)
        elif operation=="delete":
            ids = None
            if 'ids' in ret:
                ids = json.loads(ret['ids'])
            elif 'records' in ret:
                records = json.loads(ret['records'])
                ids = []
                for record in records:
                    record_id = record.get('id')
                    if record_id is not None:
                        ids.append(record_id)
            if ids is not None:
                msg, status = self.__delete(ids)
        elif operation=="save":
            if 'records' in ret:
                records = ret['records']
                records = json.loads(records)
                msg, status = self.__save(records)
        
        data = {'total':total,'rows':jdata,'footer':footer, 'msg':msg, 'status': status, 'link': link}
        reportData = simplejson.dumps(data)
        return HttpResponse(reportData)    

    def formatRule(self, filterRules, boolfield):
        filterRules_new = []
        for rule in filterRules:
            field = rule["field"]
            value = rule["value"]
            op = rule["op"]
            if field==boolfield:
                value_new = convertBoolstrToInt(value)
                rule["value"] = value_new
                rule["op"] = "equal"
                
            filterRules_new.append(rule)
                
        return filterRules_new
