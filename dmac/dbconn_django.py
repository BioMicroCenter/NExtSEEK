#!/bin/python
import os, sys
import MySQLdb
from os.path import abspath, exists
from django.db import connection
from django.db import connections
from django.db import transaction
from django.db import IntegrityError
from django.forms.models import model_to_dict
from django.db.models.fields.related import OneToOneField
from django.db.models import Q

import datetime
import simplejson
import logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

from dmac.iocsv import saveCsvfile, getString, getFloat
from dmac.conversion import convertSQLString, is_numeric

class DBconn_django(object):
    def __init__(self):
        self.conn = connection
        self.cursor = self.conn.cursor()
            
    def getPrimarykey(self, table, field, keyword):
        if keyword is None:
            return -1
        
        uniqueKeyword = keyword.strip()
        if len(uniqueKeyword)==0 or uniqueKeyword=="NA":
            return -1
        
        query = {}
        query['%s__exact'%field] = keyword
        objs = table.objects.filter(**query)
        total = objs.count()
        if total==1:
            obj = objs[0]
            return obj.id
        else:
            return -1    

        return -1    
            
    def retrieveFieldValue(self, table, id, fieldname):
        objs = table.objects.filter(id=id)
        total = objs.count()
        if total==1:
            obj = objs[0]
            for f in obj._meta.fields:
                name = f.name
                if name==fieldname:
                    try:
                        value = getattr(obj,f.name)
                    except AttributeError:
                        value = None
                    return str(value)
            
            return None
        else:
            return None    

        return None
    
    def updateFieldValue(self, tablemodel, idIn, fieldname, fieldvlue):
        try:
            tablemodel.objects.filter(id=idIn).update(fieldname=fieldvlue)
            msg = ""
        except table.DoesNotExist:
            msg = 'Error: no update on ' + fieldname
    
    def __runTransaction(self, sqlqueries):
        msg = "Run SQL transaction "
        status = 1
        sid = transaction.savepoint()
        something = 0

        if something:
            transaction.savepoint_rollback(sid)
            msg += " failed, roll back."
            status = 0
        else:
            try:
                transaction.savepoint_commit(sid)
                msg += " successfully"
                status = 1
            except IntegrityError:
                transaction.savepoint_rollback(sid)
                msg += " failed, recover."
                status = 0
        return msg, status
    
    def __insertOneRecord(self, tablemodel, record):
        msg = "Run SQL transaction "
        return self.__insertOneRecord_revised(tablemodel, record)
        
        status = 1
        sid = transaction.savepoint()
        try:
            tb = tablemodel(**record)
            tb.save()
            transaction.savepoint_commit(sid)
            msg += " successfully"
            status = 1
        except IntegrityError:
            transaction.savepoint_rollback(sid)
            msg += " failed."
            status = 0
            
        if status==0:
            return self.__insertOneRecord_revised(tablemodel, record)
            
        return msg, status
    
    def __insertOneRecord_revised(self, tablemodel, record):
        if "id" not in record:
            msg = "__insertOneRecord_revised error: no primary key in record for insert."
            return msg, 0
        
        id = record['id']
        if id is None or id<=0:
            msg = "__insertOneRecord_revised error: primary key is not available."
            logger.debug(msg)
            return msg, 0
        
        obj = tablemodel()
        for f in obj._meta.fields:
            name = f.name
            foreignkey = name + "_id"
            if name in record:
                value = record[name]
                setattr(obj, name, value)
            elif foreignkey in record:
                value = record[foreignkey]
                setattr(obj, foreignkey, value)    
        
        try:
            obj.save()
            status = 1
            msg = "Successful in adding record"
        except:
            status = 0
            msg = "Failed in adding record"
        return msg, status
    
    
    def __updateOneRecord(self, tablemodel, record):
        msg = "Run SQL transaction "
        
        if "id" not in record:
            msg = "__updateOneRecord error: no primary key in record for update."
            return -1, msg
        
        status = 1
        sid = transaction.savepoint()
        id = record["id"]
        record_old = self.retrieveOneRecord(tablemodel, id)
        record_forUpdate = self.__updateRecord(record_old, record)
        try:            
            model = tablemodel()
            for k, v in record_forUpdate.items():
                setattr(model, k, v)
            model.save()
            
            transaction.savepoint_commit(sid)
            msg = "Successful in updating record"
            status = 1
        except IntegrityError:
            transaction.savepoint_rollback(sid)
            msg = "Failed in updating record"
            status = 0
            
        return msg, status  
    
    def deleteOneRecord(self, tablemodel, id):
        b = tablemodel.objects.get(pk=id)
        b.delete()
    
    def getLatestID(self, tablemodel):
        try:
            max_id = tablemodel.objects.latest('id').id
        except tablemodel.DoesNotExist:
            max_id = 0
            
        new_id = max_id + 1
        return new_id
    
    def storeOneRecord(self, tablemodel, record):
        id = 0
        if "id" in record:
            id = record["id"]
        if int(id) > 0:
            msg, status = self.__updateOneRecord(tablemodel, record)
        else:
            id = self.getLatestID(tablemodel)
            record["id"] = id
            msg, status = self.__insertOneRecord(tablemodel, record)
            
        if status:
            return id, msg
        else:
            return -1, msg
            
    def __objToDic(self, obj):
        objdic = {}
        if obj is None:
            return objdic  
          
        for f in obj._meta.fields:
            fieldValue = getattr(obj,f.name)
            objdic[f.name] = self.convertDatetimeToString(fieldValue)
        return objdic
    
    def __objToDic_2(self, obj):
        objdic = model_to_dict(obj)
        return  objdic
    
    def __objToDic_3(self, obj, prefix):
        objdic = {}
        if obj is None:
            return objdic  
           
        for f in obj._meta.fields:
            if f.is_relation:
                name = f.name
            else:
                fieldValue = getattr(obj,f.name)
                name = prefix + f.name
                objdic[name] = fieldValue
        return objdic
    
    def convertDatetimeToString(self, fieldValue):
        DATE_FORMAT = "%Y-%m-%d" 
        TIME_FORMAT = "%H:%M:%S"

        if isinstance(fieldValue, datetime.date):
            return fieldValue.strftime(DATE_FORMAT)
        elif isinstance(fieldValue, datetime.time):
            return fieldValue.strftime(TIME_FORMAT)
        elif isinstance(fieldValue, datetime.datetime):
            return fieldValue.strftime("%s %s" % (DATE_FORMAT, TIME_FORMAT))
        else:
            return fieldValue
    
    def __objToDic_joint(self, obj):
        objdic =  model_to_dict(obj)
        opts = obj._meta
        for f in opts.get_fields(include_parents=False, include_hidden=True):
            if f.one_to_one:
                obj_child = getattr(obj,f.name)
                objdic_child = model_to_dict(obj_child)
                objdic.update(objdic_child)
            elif f.many_to_one:
                obj_child = getattr(obj,f.name)
                prefix = f.name + '.'
                objdic_child = self.__objToDic_3(obj_child, prefix)
                objdic.update(objdic_child)
                
        return  objdic
    
    def __objsToDicList(self, objs):
        objsdiclist = []
        for obj in objs:
            objdic = self.__objToDic_3(obj, '')
            objsdiclist.append(objdic)
            
        return objsdiclist 
            
    def retrieveOneRecord(self, tablemodel, idIn):
        objs = tablemodel.objects.filter(id=idIn)
        total = objs.count()
        if total==1:
            obj = objs[0]
        else:
            obj = None    

        return self.__objToDic(obj)
            
    def dictfetchall(self):
        desc = self.cursor.description
        return [
            dict(zip([col[0] for col in desc], row))
            for row in self.cursor.fetchall()
        ]    
    
    def retrieve_custom_sql(self, sqlquery, headers=None, db_alias=None, params=None):
        """Run a SELECT and return a list of dicts, one per row.

        ``params`` is optional and back-compatible (#99): omitted or falsy, the
        statement is executed alone exactly as before, so every pre-existing
        caller is unaffected. Supplied, the values are handed to
        ``cursor.execute`` for binding -- which is what a statement built by the
        #93 WHERE builders needs, since those emit ``%s`` placeholders and would
        otherwise reach the database with literal ``%s`` in the SQL.

        This is the same back-compatible shape ``feaa816`` used for ``__runQuery``
        in #78.
        """
        if db_alias is None:
            cursor = self.cursor
        else:
            cursor = connections[db_alias].cursor()

        if params:
            cursor.execute(sqlquery, params)
        else:
            cursor.execute(sqlquery)
        objs = cursor.fetchall()
        rowslist = list(objs)
        rowi = 0
        diclist = []
        for listi in rowslist:
            if rowi==0:
                nlen = len(listi)
                if headers==None:
                    headers = []
                    for i in range(nlen):
                        headers.append(str(i))
                else:
                    if nlen<len(headers):
                        logger.error("Error: Dimension of headers not match!")
                        return diclist
            
            dici = {}
            for coli, header in enumerate(headers):
                dici[header] = listi[coli]
 
            diclist.append(dici)
        return diclist
        
    def retrieveRecords(self, tablemodel, field, keyword):
        query = {}
        if is_numeric(keyword):
            query['%s__exact'%field] = keyword
        else:
            query['%s__icontains'%field] = keyword
            
        objs = tablemodel.objects.filter(**query)
        return self.__objsToDicList(objs)
    
    def updateStringsAdditive(self, tablemodel, field_known, value_known, field_change, string_added):
        self.__updateStringValuesAdditive(tablemodel, field_known, value_known, field_change, string_added)

    def retrieveNumberOfRecords(self, tablename):
        sqlquery = "select count(*) as total from " + tablename + ";"
        rows = self.retrieve_custom_sql(sqlquery)
        total = rows[0]['total']
        return total

    def retrieveTotalRecords(self, tablemodel, qset=None):
        if qset is None:
            total = tablemodel.objects.all().count()
        else:
            total = tablemodel.objects.filter(qset).count()
        return total

    def retrieveValuesDictionary(self, tablemodel, filter):
        objsdic = tablemodel.objects.filter(filter).values()
        return objsdic

    def retrieveValuesList(self, tablemodel, filter):
        objslist = tablemodel.objects.filter(filter).values_list()
        return objslist
    
    def retrieveJointQuery(self, tablemodel_parent, tablemodel_child, filter):
        objs = tablemodel_parent.objects.filter(filter).select_related(tablemodel_child)
        return objs
        
    def retrieveJoint(self, tablemodel, joint_tablemodels_string, qset, orderby, limit):
        if qset is None:
            qset = Q()
        
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
        
        strs3 = orderby.strip().split(' ')      
        keyword = ' '
        if len(strs3)>2:
            keyword = strs3[2]
        
        order = 'ASC'                          
        if len(strs3)>3:
            order = strs3[3]                    
            
        if order.upper()=='ASC':
            orderby_keyword = keyword
        elif order.upper()=='DESC':
            orderby_keyword = "-" + keyword
        else:
            orderby_keyword = keyword
            
        if len(joint_tablemodels_string)==0:
            if rows==0:
                if len(orderby_keyword)<2:
                    objs = tablemodel.objects.filter(qset)
                else:
                    objs = tablemodel.objects.filter(qset).order_by(orderby_keyword)
            else:
                if len(orderby_keyword)<2:
                    objs = tablemodel.objects.filter(qset)[startNo:endNo]
                else:
                    objs = tablemodel.objects.filter(qset).order_by(orderby_keyword)[startNo:endNo]

            return self.__objsToDicList(objs)
        else:
            if rows==0:
                if len(orderby_keyword)<2:
                    objs = tablemodel.objects.select_related(joint_tablemodels_string).filter(qset)
                else:
                    objs = tablemodel.objects.select_related(joint_tablemodels_string).filter(qset).order_by(orderby_keyword)
            else:
                if len(orderby_keyword)<2:
                    objs = tablemodel.objects.select_related(joint_tablemodels_string).filter(qset)[startNo:endNo]
                else:
                    objs = tablemodel.objects.select_related(joint_tablemodels_string).filter(qset).order_by(orderby_keyword)[startNo:endNo]
        
            return self.__objsToDicList_joint(objs)
        
        return self.__objsToDicList(objs)
        
        
        
    def __queryFiltering(self, tablemodel, qset, distinct, orderby_keyword, offset, rows):
        if rows>0:
            if distinct:
                if len(orderby_keyword)>0:
                    mylist = tablemodel.objects.filter(qset).distinct().order_by(orderby_keyword)[offset:rows]
                else:
                    mylist = tablemodel.objects.filter(qset).distinct()[offset:rows]
            else:
                if len(orderby_keyword)>0:
                    mylist = tablemodel.objects.filter(qset).order_by(orderby_keyword)[offset:rows]
                else:
                    mylist = tablemodel.objects.filter(qset)[offset:rows]
        else:
            if distinct:
                if len(orderby_keyword)>0:
                    mylist = tablemodel.objects.filter(qset).distinct().order_by(orderby_keyword)
                else:
                    mylist = tablemodel.objects.filter(qset).distinct()
            else:
                if len(orderby_keyword)>0:
                    mylist = tablemodel.objects.filter(qset).order_by(orderby_keyword)
                else:
                    mylist = tablemodel.objects.filter(qset)
    
        return mylist

    def __queryRecords(self, tablemodel, field, keyword):
        query = {}
        query['%s__contains'%field] = keyword
        objs = tablemodel.objects.filter(**query)
        return objs
    

    def __updateStringValuesAdditive(self, tablemodel, field_known, value_known, field_change, string_added):
        if field_known is None:
            return
        
        field_known = field_known.strip()
        if len(field_known)==0 or field_known=="NA":
            return
        
        if value_known is None:
            return
        
        query = {}
        if is_numeric(value_known):
            query['%s__exact'%field_known] = value_known
        else:
            query['%s__contains'%field_known] = value_known
        
        objs = tablemodel.objects.filter(**query)
        for obj in objs:
            for f in obj._meta.fields:
                name = f.name
                if name==field_change:
                    try:
                        string_old = getattr(obj,f.name)
                    except AttributeError:
                        string_old = None
                        
                    string_new = self.__mergeStringsAdditive(string_old, string_added)
                    setattr(obj, f.name, string_new)
                    obj.save()
                
                
    def __mergeStringsAdditive(self, string1, string2):
        newstring = string1
    
        if string1 is None:
            newstring = string2
        elif len(string1)==0:
            newstring = string2
        else:
            if string2 is None:
                newstring = string1
            elif len(string2)==0:
                newstring = string1
            else:
                if string2 in string1:
                    newstring = string1
                else:
                    newstring = string1 + ";" + stringIn
        return newstring    
    
    def deleteRocords(self, tablemodel, primarykeys):
        number = 0
        try:
            objs = tablemodel.objects.filter(pk__in=primarykeys)
            number = len(objs)
            objs.delete()
        except IntegrityError:
            number = 0
        
        return number
        
    def retrieveIDsUniqueField(self, tablemodel, uniqueField):
        objs = tablemodel.objects.all()
        idsdic = {}
        if objs is None:
            return idsdic
        
        try:
            n = len(objs)
        except:
            n = -1
        
        if n<=0:
            return idsdic
        
        diclist = self.__objsToDicList(objs)
        for dici in diclist:
            if uniqueField in dici:
                fieldvalue = dici[uniqueField]
                id = dici["id"]
                idsdic[fieldvalue] = id
        return idsdic
        
    def getRecords(self, tablemodel, datadic):
        objs = tablemodel.objects.filter(**datadic)
        return self.__objsToDicList(objs)
        
    def getDistinctList(self, tablemodel, fieldname):
        dlist = []
        objs = tablemodel.objects.order_by(fieldname).values(fieldname).distinct()
        for obj in objs:
            term = obj[fieldname]
            dlist.append(term)
        return dlist
            
            
    def generateQuerySet(self, filterRules):
        qset = Q()
        n = 0
        for rule in filterRules:
            field = rule["field"]
            keyword = rule["value"]
            op = rule["op"]
            
            query = {}
            if op=="contains":
                query['%s__icontains'%field] = keyword
            else:
                if is_numeric(keyword):
                    query['%s__exact'%field] = keyword
                else:
                    query['%s__icontains'%field] = keyword
            
            if n==0:
                qset = Q(**query)
            else:
                qset = qset & Q(**query)
            n += 1
            
        return qset
        
    def insertOneRecord(self, tablemodel, record):
        return self.__insertOneRecord(tablemodel, record)
        
    def __updateValue(self, value_fromDB, value_input):
        value_forUpdate = value_input
        if value_input is None:
            value_forUpdate = value_fromDB
        
        return value_forUpdate
        
    def __updateRecord(self, record_fromDB, record_input):
        record_forUpdate = record_fromDB
        for k, v in record_input.items():
            if k in record_forUpdate:
                v_old = record_forUpdate[k]
                if v!=v_old:
                    logger.debug(k, v, v_old)
                record_forUpdate[k] = self.__updateValue(v_old, v)
                
        return record_forUpdate
        
    def __updateOneToOneRecord(self, tablemodel, record, primarykeyname):
        msg = "Run SQL transaction "
        
        if primarykeyname not in record:
            msg = "__updateOneRecord error: no primary key in record for update."
            return -1, msg
        
        status = 1
        sid = transaction.savepoint()
        try:
            pid = record[primarykeyname]
            obj = self.retrieveUniqueObj(tablemodel, primarykeyname, pid)
            
            obj = tablemodel.objects.get(person_id=7)
            for k, v in record.items():
                setattr(obj, k, v)
            obj.save()
            transaction.savepoint_commit(sid)
            msg += " successfully"
            status = 1
        except IntegrityError:
            transaction.savepoint_rollback(sid)
            msg += " failed."
            status = 0
            
        msg = ""
        return msg, status      
            
    def storeOneToOneRecord(self, tablemodel, record, primarykeyname):
        if primarykeyname not in record:
            msg = "Not right format for storing record"
            return -1, msg
        
        msg, status = self.__updateOneToOneRecord(tablemodel, record, primarykeyname)
        return status, msg
    
    def __objsToDicList_joint(self, objs):
        objsdiclist = []
        for obj in objs:
            objdic = self.__objToDic_joint(obj)
            objsdiclist.append(objdic)
            
        return objsdiclist
    
    def retrieveUniqueObj(self, tablemodel, field, keyword):
        query = {}
        if is_numeric(keyword):
            query['%s__exact'%field] = keyword
        else:
            query['%s__icontains'%field] = keyword
            
        objs = tablemodel.objects.filter(**query)
        total = objs.count()
        if total==1:
            obj = objs[0]
        else:
            obj = None
        return obj
            
    def retrieveUniqueRecord(self, tablemodel, field, keyword):
        obj = self.retrieveUniqueObj(tablemodel, field, keyword)
        return self.__objToDic(obj)
        
        
    def getOptions(self, tablemodel, valueField, textField, valueSelected):
        objs = tablemodel.objects.all()
        diclist = self.__objsToDicList(objs)
        options = []
        selected = False
        for dici in diclist:
            value = dici[valueField]
            option = {}
            option[valueField] = value
            option[textField] = dici[textField]
            if str(value)==str(valueSelected):
                option['selected'] = True
                selected = True
                
            options.append(option)
        
        if not selected:
            option = {}
            option[valueField] = valueSelected
            option[textField] = ''
            option['selected'] = True
            
            options = [option] + options
            
        return simplejson.dumps(options)
        
    def retrieveConstraintRecords(self, tablemodel, constraint):
        return self.getRecords(tablemodel, constraint)
        
        qset = Q()
        n = 0
        for key, value in constraint.items():
            query = {}
            if is_numeric(value):
                query['%s__exact'%key] = value
            else:
                query['%s__icontains'%key] = value
            
            if n==0:
                qset = Q(**query)
            else:
                qset = qset & Q(**query)
            n += 1
            
        objs = tablemodel.objects.filter(qset)
        return self.__objsToDicList(objs)
        
    def retrieveContainedRecords(self, tablemodel, constraint):
        qset = Q()
        n = 0
        for key, value in constraint.items():
            query = {}
            if is_numeric(value):
                query['%s__exact'%key] = value
            else:
                query['%s__icontains'%key] = value
            
            if n==0:
                qset = Q(**query)
            else:
                qset = qset & Q(**query)
            n += 1
            
        objs = tablemodel.objects.filter(qset)
        return self.__objsToDicList(objs)
        
    def retrieve_table_list(self, tablename, primarykey, filtersdic, fieldMapping):
        orderby = filtersdic['orderby'] 
        startNo = filtersdic['startNo'] 
        endNo = filtersdic['endNo']     
        limit = filtersdic['limit']
        
        filterRules = filtersdic['filterRules']
        qset = self.generateQuerySet(filterRules)
        
        joint_tablemodels_string = ''
        dicList = self.retrieveJoint(tablename, joint_tablemodels_string, qset, orderby, limit)
        
        qset = Q()
        limit = ' '
        dicList_total = self.retrieveJoint(tablename, joint_tablemodels_string, qset, orderby, limit)
        total = len(dicList_total)
        
        footer = []
        return dicList, footer, total
            
    def getQueryValue(self, sqlquery, db_alias=None):
        if db_alias is None:
            cursor = self.cursor.execute(sqlquery)
        else:
            cursor = connections[db_alias].cursor()
        
        cursor.execute(sqlquery)
        objs = cursor.fetchall()
        if objs is None:
            value = None
        elif len(objs)==1:
            values = objs[0]
            value = values[0]
        else:
            value = None
        return value    
        
        
    def run_custom_transaction(self, sqlqueries, db_alias=None):
        if db_alias is None:
            con = self.conn
        else:
            con = connections[db_alias]
        cur = con.cursor()
        
        sid = transaction.savepoint()
        sqlquery = "START TRANSACTION;"
        cur.execute(sqlquery)
        
        try:
            for sqlquery in sqlqueries:
                cur.execute(sqlquery)
    
            sqlquery = "COMMIT;"
            cur.execute(sqlquery)
            transaction.savepoint_commit(sid)
            con.commit()
        except MySQLdb.Error:
            sqlquery = "ROLLBACK;"
            cur.execute(sqlquery)
            transaction.savepoint_rollback(sid)
            con.rollback()
            return 0
        except:
            sqlquery = "ROLLBACK;"
            cur.execute(sqlquery)
            transaction.savepoint_rollback(sid)
            con.rollback()
            return 0
    
        return 1
        
    def queryRecordsCustom(self, tablemodel, qset):
        objs = tablemodel.objects.filter(qset)
        return self.__objsToDicList(objs)
        
    def retrieveRecordsByIDs(self, tablemodel, ids):
        objs = tablemodel.objects.filter(pk__in=ids)
        return self.__objsToDicList(objs)
