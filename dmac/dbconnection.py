#!/bin/python
import os, sys
import datetime
from os.path import abspath, exists
from dmac.iocsv import saveCsvfile

class DBconnection(object):
    def __init__(self, whichDB, dbname=''):
        self.__dbconn = None
        self.dbtype = whichDB
        if whichDB=="DJANGO":
            from dmac.dbconn_django import DBconn_django
            self.__dbconn = DBconn_django()
        elif whichDB=="SEEK":
            from dmac.dbconn_django import DBconn_django
            self.__dbconn = DBconn_django()
        else:
            from dmac.dbconn_django import DBconn_django
            self.__dbconn = DBconn_django()
        
    def getPrimarykey(self, table, field, keyword):
        if self.__dbconn is not None:
            return self.__dbconn.getPrimarykey(table, field, keyword)
        else:
            return -1
    
    def getLatestID(self, tablemodel):
        return self.__dbconn.getLatestID(tablemodel)
        
    def retrieveFieldValue(self, tablemodel, primarykey, field):
        if self.__dbconn is not None:
            return self.__dbconn.retrieveFieldValue(tablemodel, primarykey, field)
        else:
            return None
        
    def retrieveForeignKeyId(self, tablemodel, primarykey, field):
        record = self.retrieveOneRecord(tablemodel, primarykey)
        id = 0
        if record is not None:
            if field in record:
                foreign_record = record[field]
                id = foreign_record.id
        
        return id    
        
    def storeOneRecord(self, tablemodel, record, primarykey=None, primaryvalue=None, excludeKeys=[]):
        id, msg = self.__dbconn.storeOneRecord(tablemodel, record)
        if int(id)>0:
            primarykey = id
        else:
            primarykey = 0
            
        return msg, primarykey
        
    def retrieveOneRecord(self, tablemodel, id):
        return self.__dbconn.retrieveOneRecord(tablemodel, id)
        
    def __updateRecordViaKeyword(self, tablemodel, uniqueField, uniqueKeyword, record):
        id = self.getPrimarykey(tablemodel, uniqueField, uniqueKeyword)
        if int(id)<=0:
            msg = "Warning: The primary key for the table is not valid."
            logger.debug(msg)
            return -1, msg
            
        record_update = self.__dbconn.retrieveOneRecord(tablemodel, id)
        for key, value in record.items():
            if key == "id":
                if value!=id:
                    msg = "Error: The primary key not consistent."
                    logger.debug(msg)
                    return -1, msg
        
            if key in record_update:
                record_update[key] = value      
           
        return self.storeOneRecord(tablemodel, record_update)

    def run_custom_sql(self, sqlquery):
        if self.__dbconn is not None:
            return self.__dbconn.retrieve_custom_sql(sqlquery)
        
        return None
    
    def saveRecordsIntoCSV(self, sqlquery, columns, outcsvfile):
        rows = self.run_custom_sql(sqlquery)
        saveCsvfile(outcsvfile, columns, rows)
        
    def retrieveRecords(self, tablemodel, field, keyword):
        if self.__dbconn is not None:
            return self.__dbconn.retrieveRecords(tablemodel, field, keyword)
            
        return None
    
    def retrieveNumberOfRecords(self, tablename, constraint={}):
        if self.__dbconn is not None:
            if not constraint:
                return self.__dbconn.retrieveNumberOfRecords(tablename)
            else:
                return -1
        return -1
    
    def retrieveTotalRecords(self, tablemodel, qset=None):
        if self.__dbconn is not None:
            return self.__dbconn.retrieveTotalRecords(tablemodel, qset)
            
        return -1
    
    def retrieveJoint(self, tablemodel, joint_tablemodels_string, qset, orderby, limit):
        if self.__dbconn is not None:
            return self.__dbconn.retrieveJoint(tablemodel, joint_tablemodels_string, qset, orderby, limit)
            
        return []
    

    def deleteRocords(self, tablemodel, primarykeys):
        if self.__dbconn is not None:
            return self.__dbconn.deleteRocords(tablemodel, primarykeys)
            
        return 0
        
    def printTest(self):
        print(self.dbtype, self.__dbconn.msg)
        
    def retrieve_table_list(self, tablename, primarykey, filtersdic, fieldMapping):
        if self.dbtype=="DJANGO" or self.dbtype=="SEEK":
            return self.__dbconn.retrieve_table_list(tablename, primarykey, filtersdic, fieldMapping)
        else:
            jdata = []
            footer = []
            total = 0
            return jdata, footer, total
        
    def getLatestPrimarykey(self, tablename):
        return self.__dbconn.getLatestID(tablename)
            
    def retrieveConstraintRecords(self, tablename, constraint):
        if self.dbtype=="DJANGO" or self.dbtype=="SEEK":
            return self.__dbconn.retrieveConstraintRecords(tablename, constraint)
        else:
            return []
            
    def retrieveContainedRecords(self, tablename, constraint):
        if self.dbtype=="DJANGO" or self.dbtype=="SEEK":
            return self.__dbconn.retrieveContainedRecords(tablename, constraint)
        else:
            return []    
            
    def deleteRecordsConstraint(self, tablename, constraint):
        return []
            
    def deleteOneRecord(self, fulltablename, primarykey, primaryvalue):
        if self.dbtype=="DJANGO" or self.dbtype=="SEEK":
            self.__dbconn.deleteOneRecord(fulltablename, primaryvalue)
            status = 1
            msg = "Okay"
            return msg, status
        else:
            status = 0
            msg = "To be implemented"
            return msg, status
     
    def getOneRecord(self, fulltablename, primarykey, primaryvalue):
        if self.dbtype=="DJANGO" or self.dbtype=="SEEK":
            return self.__dbconn.retrieveOneRecord(fulltablename, primaryvalue)
        else:
            status = 0
            msg = "To be impplemented."
            recordarray = self.__dbconn.retrieveOneRecord(fulltablename, primaryvalue)
            record = {}
            return record
        
    def queryToListDics(self, sqlquery, headers=None, db_alias=None, params=None):
        # params is optional and defaults to the pre-#99 unbound behaviour.
        return self.__dbconn.retrieve_custom_sql(sqlquery, headers, db_alias, params)
            
    def commitSQLQuery(self, sqlquery):
        return 'to be implemented', 0
        
    def compareOneRecord(self, obj, record):
        return self.__dbconn.compareOneRecord(obj, record)
        
    def retrieveValuesDictionary(self, tablemodel, filter):
        return self.__dbconn.retrieveValuesDictionary(tablemodel, filter)
        
        
    def saveOneRecord(self, datadic, tablemodel, unqiuefield, expectedfields):
        id = 0
        msg = "Unique identifier no found in record: " + unqiuefield
        if unqiuefield not in datadic:
            return id, msg
        
        record = {}
        keyword = datadic[unqiuefield]
        id = self.getPrimarykey(tablemodel, unqiuefield, keyword.strip())
        if int(id)>0:
            record['id'] = id
       
        for field in expectedfields:
            if field in datadic:
                value = datadic[field]
                if value is not None:
                    record[field] = value
                else:
                    record[field] = ""
            else:
                record[field] = ""
        id, msg = self.storeOneRecord(tablemodel, record)
        return id, msg
    
    def retrieveIDsUniqueField(self, tablemodel, uniqueField):
        return self.__dbconn.retrieveIDsUniqueField(tablemodel, uniqueField)
        
        
    def getRecords(self, tablemodel, datadic):
        return self.__dbconn.getRecords(tablemodel, datadic)
        
    def getDistinctList(self, tablemodel, fieldname):
        return self.__dbconn.getDistinctList(tablemodel, fieldname)

    def generateQuerySet(self, filterRules):
        return self.__dbconn.generateQuerySet(filterRules)
        
        
    def getUniueID(self, datadic, tablemodel, unqiuefield):
        unique_id = 0
        if unqiuefield in datadic:
            fieldvalue = datadic[unqiuefield]
            unique_id = self.getPrimarykey(tablemodel, unqiuefield, fieldvalue)
        
        return unique_id

    def storeOneToOneRecord(self, tablemodel, record, primarykeyname):
        return self.__dbconn.storeOneToOneRecord(tablemodel, record, primarykeyname)
        
    def insertOneRecord(self, tablemodel, record):
        return self.__dbconn.insertOneRecord(tablemodel, record)
        
    def retrieveUniqueRecord(self, tablemodel, field, keyword):
        return self.__dbconn.retrieveUniqueRecord(tablemodel, field, keyword)
        
        
    def getOptions(self, tablemodel, valueField, textField, valueSelected):
        return self.__dbconn.getOptions(tablemodel, valueField, textField, valueSelected)

    def getQueryValue(self, sqlquery, db_alias=None):
        return self.__dbconn.getQueryValue(sqlquery, db_alias)

    def run_custom_transaction(self, sqlqueries, db_alias=None):
        if self.__dbconn is not None:
            return self.__dbconn.run_custom_transaction(sqlqueries, db_alias)
        
        return None
        
    def queryRecordsCustom(self, tablemodel, qset):
        if self.__dbconn is not None:
            return self.__dbconn.queryRecordsCustom(tablemodel, qset)
        
        return []
        
    def retrieveRecordsByIDs(self, tablemodel, ids):
        if self.__dbconn is not None:
            return self.__dbconn.retrieveRecordsByIDs(tablemodel, ids)
            
        return []   
        
