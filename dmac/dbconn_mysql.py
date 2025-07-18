#!/bin/python
import os, sys
import logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)
import MySQLdb
from os.path import abspath, exists
from django.conf import settings

SEEK_PRO = settings.SEEK_DATABASE
SEEK_DEV = settings.SEEK_DATABASE

class DBconn_mysql(object):
    def __init__(self):
        self.conn = None
        self.cursor = None
        mysqldb = self.__getDefaultDB()
        if mysqldb is not None:
            self.conn = MySQLdb.connect(host=mysqldb['HOST'], user=mysqldb['USER'], passwd=mysqldb['PASSWORD'], db=mysqldb['NAME'])
            self.cursor = self.conn.cursor()
        
    def __getDefaultDB(self):
        f_path = abspath("dbconnect.txt")
        if exists(f_path):
            defaultDBFile = f_path
        else:
            defaultDBFile = abspath("dbconnect.txt")
    
        defaultDB = self.__dbchoices("DEFAULT")
        try:
            fi = open(defaultDBFile,"r")
        except (OSError, IOError) as e:
            return defaultDB
    
        for line in fi:
            terms = line.strip().split(' ')
            db = terms[0]
            if db=='production':
                defaultDB = self.__dbchoices("SEEK_PRO")
            elif db=='development':
                defaultDB = self.__dbchoices("SEEK_DEV")
            else:
                print("The development DB will be used as default.")
    
        fi.close()
        return defaultDB

    def __dbchoices(self, whichDB):
        if whichDB=='SEEK_PRO':
            mysqldb = SEEK_PRO
        elif whichDB=='SEEK_DEV':
            mysqldb = SEEK_DEV
        else:
            mysqldb = SEEK_DEV
        
        return mysqldb

    def getPrimarykey(self, table, field, keyword):
        select_sql = "SELECT id FROM %s WHERE %s='%s';" % (table, field, keyword)
        return self.__selectPrimarykeyAny(select_sql)
    
    def __selectPrimarykeyAny(self, sqlquery): 
        rows = self.__retrieveRecords(sqlquery)
        if rows is None:
            return -1
    
        id = 0
        n = 0
        for row in rows:
            id = row[0]
            n += 1
    
        if n>1:
            return -n
    
        return id
            
         
    def __retrieveRecords(self, sqlquery):
        try:
            self.cursor.execute(sqlquery)
            self.conn.commit()
            rows = self.cursor.fetchall()
        except:
            self.conn.rollback()
            rows = None
        return rows
        
    def __is_numeric(self, s):
        try:
            i = float(s)
        except ValueError:
            return False
        return True
            
    def retrieveFieldValue(self, table, primarykey, field):
        sqlquery = "SELECT %s FROM %s " % (field, table)
        sqlquery += " WHERE id=" + str(primarykey) + ";"
        data = []
        rows = self.__retrieveRecords(sqlquery)
        if rows is None:
            return None
    
        n = 0
        for row in rows:
            data = row
            n += 1
    
        if n>1 or n==0:
            return None

        return data[0] 

    def __insertRecords(self, table, columns, rows):
        index = 0
        sqlqueries = []
        for tabs in rows:
            insert_row = []
            for value in tabs:
                strValue = self.__convertSQLString(value)
                insert_row.append(strValue)
            insert_sql = "INSERT INTO %s (%s) VALUES (%s);" % (table, ','.join(columns), ','.join(insert_row)) 
            sqlqueries.append(insert_sql)
            index += 1
            
        return self.__runTransaction(sqlqueries)
    
    def __convertSQLString(self, value):
        if not self.__is_numeric(value):
            strValue = '"' + MySQLdb.escape_string(value) + '"'
        else:
            strValue = str(value)
            
        return strValue

    def __insertOneRecord(self, table, record):
        columns = []
        row = []
        for key, value in record.items():
            columns.append(key)
            strValue = self.__convertSQLString(value)
            row.append(strValue)
                
        insert_sql = "INSERT INTO %s (%s) VALUES (%s);" % (table, ','.join(columns), ','.join(row))
        sqlqueries = []
        sqlqueries.append(insert_sql)
        return self.__runTransaction(sqlqueries)
        
    
    def __updateRecords(self, table, columns, rows):
        index = 0
        sqlqueries = []
        for tabs in rows:
            update_sql = "UPDATE %s SET " % table 
            n = 0
            where = " WHERE id="
            for value in tabs:
                if n==0:
                    where += str(value) + ";"
                else:
                    strValue = self.__convertSQLString(value)
                
                    if n<(len(columns)-1):
                        update_sql += columns[n] + "=" + strValue + ", "
                    else:
                        update_sql += columns[n] + "=" + strValue + " " + where
                n += 1
            
            sqlqueries.append(update_sql)
            
        return self.__runTransaction(sqlqueries) 
       
    def updateOneRecord(self, table, record):
        update_sql = "UPDATE %s SET " % table 
        where = " WHERE id="
        for key, value in record.items():
            if key == "id":
                where += str(value) + ";"
            else:
                strValue = self.__convertSQLString(value)
                update_sql += key + "=" + strValue + ", "
            
        update_sql = update_sql[:-1]    # remove last ","
        update_sql += " " + where
        sqlqueries = []
        sqlqueries.append(update_sql)
        return self.__runTransaction(sqlqueries)
        
       
    def __runTransaction(self, sqlqueries):
        msg = "Run SQL transaction "
        status = 1
        
        self.conn.autocommit(False)
        try:
            for sqlquery in sqlqueries:
                self.cursor.execute(sqlquery)
                logger.debug(f"Attempted sqlquery: {sqlquery}")
                print(f"Attempted sqlquery: {sqlquery}")
            self.conn.commit()
            msg += " successfully"
            status = 1
            logger.debug("All sqlqueries ran successful")
            print("All sqlqueries ran successful")
        except:
            self.conn.rollback()
            msg += " failed"
            status = 0
            logger.debug("A sqlquery failed")
            print("A sqlquery failed")
 
        return msg, status
 
    def __deleteRecords(self, table, field, keyword):   
        delete_sql = "DELETE FROM %s WHERE %s='%s';" % (table, field, keyword) 
        sqlqueries = []
        sqlqueries.append(delete_sql)
        return self.__runTransaction(sqlqueries)
        
    def getLatestID(self, table):
        select_sql = "SELECT max(id) FROM %s;" % (table)
        rows = self.__retrieveRecords(select_sql)
        if rows is None:
            id = 0
        else:
            row = rows[0]
            id = row[0]
            if id==None:
                id = 0
        new_id = id + 1
        return new_id    
    
    def storeOneRecord(self, table, record):
        id = 0
        if "id" in record:
            id = record["id"]
            
        if id>0:
            msg, status = self.__updateOneRecord(table, record)
        else:
            id = self.__getLatestID(table)
            record["id"] = id
            msg, status = self.__insertOneRecord(table, record)
        
        if status:
            return id, msg
        else:
            return -1, msg
    
    def retrieveOneRecord(self, table, primarykey):
        sqlquery = "SELECT * FROM %s " % (table)
        sqlquery += " WHERE id=" + str(primarykey) + ";"
    
        data = []
        try:
            cursor.execute(sqlquery)
            conn.commit()
            rows = cursor.fetchall()
        except:
            conn.rollback()
            return data
    
        for row in rows:
            data = row
    
        return data  
    
        
    def retrieve_custom_sql(self, sqlquery):
        try:
            self.cursor.execute(sqlquery)
            self.conn.commit()
            rows = self.cursor.fetchall()
        except:
            self.conn.rollback()
            return None
    
        return rows
    
    def retrieveNumberOfRecords(self, tablename):
        sqlquery = "select count(*) as total from " + tablename + ";"
        rows = self.retrieve_custom_sql(sqlquery)
        row = rows[0]   # only one record
        total = row[0]  # only one value
        return total
