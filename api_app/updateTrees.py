#!/bin/python

import csv
import MySQLdb
import glob
import os, sys
import zipfile
from io import StringIO
import json
import simplejson
import multiprocessing
import time
import datetime

from multiprocessing import Pool, cpu_count

from datetime import date
from api_app.dbconn_mysql import DBconn_mysql

SAMPLE_PARENT_ACCESSOR_NAME = "Parent"

def isValidUID(sample_uid):
    try:
        terms = sample_uid.split("-")
        if len(terms)==3:
            return 1
        else:
            return 0
    except:
        return 0

def getParentUIDs(sampleDic):
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
                        if len(vi)>0 and vi not in uids and isValidUID(vi):
                            uids.append(vi)
                else:
                    value = value.strip()
                    if len(value)>0 and value not in uids and isValidUID(value):
                        uids.append(value)
                        
    #print('Parent uids: ', uids)
    return uids



def getChildrenUIDs(conn_seek, currentuid):
    #sqlquery = "select uuid from samples where json_metadata like '%" + currentuid + "';"
    #sqlquery = "select * from samples where json_metadata like '%" + currentuid + "';"
    sqlquery = "select uuid, json_metadata from samples where json_extract(json_metadata, '$.UID')='" + currentuid + "' OR find_in_set('" + currentuid + "', replace(json_unquote(json_extract(json_metadata, '$.Parent')), ';', ',')) > 0"

    records = conn_seek.retrieveRecordsDiclist(sqlquery)
    
    #records = self.db.retrieveRecords(self.tablemodel, 'json_metadata', currentuid)
    uids = []
    for record in records:
        uid = record['uuid']
        #metadata = record['json_metadata']
        #sampleDic = self.__getRecordFromJson(metadata)
        #parent_uids = self.__getParentUIDs(sampleDic)
        #if currentuid in parent_uids:
        #    uids.append(uid)
        uids.append(uid)
    
    #print('Children uids: ', uids)       
    return uids

def getChildLoop(parentuid, sampleUIDs):
    ''' modified from
        dbsample.__getChildLoop(self, parentuid):
    '''
    child = {}
    child["name"] = str(parentuid)
    child["id"] = str(parentuid)
    
    try:
        # should be initialized in getSamples()
        sampleTree = sampleUIDs[parentuid]
    except:
        return child, sampleUIDs
    
    if "childrenTree" in sampleTree:
        child = sampleTree["childrenTree"]
        return child, sampleUIDs
        
    #child_uids = self.__getChildrenUIDs(parentuid)
    try:
        children_uids = sampleTree['children']
    except:
        return child, sampleUIDs
    
    if children_uids is None or len(children_uids)==0:
        return child, sampleUIDs
        
    next_children = []
    for uid in children_uids:
        #next_child = self.__getChildLoop(uid)
        next_child, sampleUIDs = getChildLoop(uid, sampleUIDs)
        next_children.append(next_child)
        
    if len(next_children)>0:
        child["children"] = next_children
    
    sampleTree["childrenTree"] = child
    sampleUIDs[parentuid] = sampleTree
        
    return child, sampleUIDs

#def createSampleChildrenTree(sample_id, sampleDics):
def createSampleChildrenTree(currentuid, sampleUIDs):
    ''' Modified from
            childrenTree = dbsample.createSampleChildrenTree(sample_id)
    '''
    #return self.createSampleChildrenTreeParallel(sample_id)
        
    #record = self.__retrieveSampleByID(sample_id)
    #record = sampleDics[sample_id]
    #if record is None:
    #    return None
    #currentuid = record['uuid']
    
    try:
        # should be initialized in getSamples()
        sampleTree = sampleUIDs[currentuid]
    except:
        return None, sampleUIDs
    
    if "childrenTree" in sampleTree:
        treeData = sampleTree["childrenTree"]
        return treeData, sampleUIDs
    
    #children_uids =  self.__getChildrenUIDs(currentuid)
    try:
        children_uids = sampleTree['children']
    except:
        return None, sampleUIDs
       
    treeData = {}
    treeData["name"] = str(currentuid)
    treeData["id"] = str(currentuid)
    
    children = []
    for uid in children_uids:
        #child = self.__getChildLoop(uid)
        child, sampleUIDs = getChildLoop(uid, sampleUIDs)
        children.append(child)

    if len(children)>0:
        treeData["children"] = children
    return treeData, sampleUIDs

def createMultiParentTree0(sample_id, includeChilren, childrenTreeIn=None):
    #return self.__createMultiParentTreeParallel(sample_id, includeChilren, childrenTreeIn)
        
    record = self.__retrieveSampleByID(sample_id)
    if record is None:
        return None, None
        
    childuid = record['uuid']
    json_metadata = record['json_metadata']
    dici = self.__getRecordFromJson(json_metadata)
    parent_uids =  self.__getParentUIDs(dici)
        
    childuid = str(childuid)
    child = {'id':childuid, 'name':childuid}
        
    if includeChilren:
        child = self.createSampleChildrenTree(sample_id)
        
    upTreeList = []
    if len(parent_uids)==0:
        upTreeList.append(child)
    else:
        for uid in parent_uids:
            uid = str(uid)
            childNode = {'id':uid, 'name':uid, 'children':[child]}
            parentTreeList = self.__getParentTreeListLoop(childNode)
            upTreeList += parentTreeList
        
    return upTreeList, parent_uids

#def __getParentTreeListLoop(self, childNode):
def getParentTreeListLoop(childNode, sampleUIDs):
    upTreeList = []
    childuid = childNode['id']

    #parent_uids = self.__getParents(childuid)
    try:
        # should be initialized in getSamples()
        sampleTree = sampleUIDs[childuid]
    except:
        print("Error: Sample tree not available " + sample_uid)
        return upTreeList, sampleUIDs
    
    #if childuid=='NHP-220927FLY-8':
    #    print("Sample tree available " + childuid)
    
    #if "parentTree" in sampleTree:
    #    upTreeList = sampleTree["parentTree"]
    #    #print("upTreeList generated: ", upTreeList)
    #    return upTreeList, sampleUIDs
    
    #if childuid=='NHP-220927FLY-8':
    #    print("parentTree available " + childuid)
    
    try:
        parent_uids = sampleTree['parents']
    except:
        upTreeList.append(childNode)
        return upTreeList, sampleUIDs
    
    #if childuid=='NHP-220927FLY-8':
    #    print("parent_uids available ", parent_uids)
    
    if parent_uids is None or len(parent_uids)==0:
        upTreeList.append(childNode)
        #print(upTreeList)
        return upTreeList, sampleUIDs
        
    #if childuid=='NHP-220927FLY-8':
    #    print("parent_uids available not empty ", parent_uids)
        
    for uid in parent_uids:
        uid = str(uid)
        #print("parent uid: " + uid)
        node = {'id':uid, 'name':uid, 'children':[childNode]}
        #parentTreeList = self.__getParentTreeListLoop(node)
        parentTreeList, sampleUIDs = getParentTreeListLoop(node, sampleUIDs)
        #print(parentTreeList)
        upTreeList += parentTreeList
        
    #sampleTree["parentTree"] = upTreeList
    #sampleUIDs[childuid] = sampleTree
            
    #print(upTreeList)
    return upTreeList, sampleUIDs


def createMultiParentTree(currentuid, sampleUIDs, includeChilren):
    #return self.__createMultiParentTreeParallel(sample_id, includeChilren, childrenTreeIn)
        
    #record = self.__retrieveSampleByID(sample_id)
    #if record is None:
    #    return None, None
        
    #childuid = record['uuid']
    childuid = currentuid
    try:
        # should be initialized in getSamples()
        sampleTree = sampleUIDs[currentuid]
    except:
        return None, sampleUIDs
    
    if "parentTree" in sampleTree:
        treeData = sampleTree["parentTree"]
        return treeData, sampleUIDs
    
    #json_metadata = record['json_metadata']
    #dici = self.__getRecordFromJson(json_metadata)
    #parent_uids =  self.__getParentUIDs(dici)
    try:
        parent_uids = sampleTree['parents']
    except:
        return None, sampleUIDs
        
    childuid = str(childuid)
    child = {'id':childuid, 'name':childuid}
        
    if includeChilren:
        try:
            #child = self.createSampleChildrenTree(sample_id)
            child = sampleTree['childrenTree']
        except:
            child = {'id':childuid, 'name':childuid}
        
    
    #if currentuid=='PAV-230508FLY-88':
    #    print(currentuid)
    #    print("parent_uids: ", parent_uids)
    #    print("childrenTree: ", child)
        
    upTreeList = []
    if len(parent_uids)==0:
        upTreeList.append(child)
    else:
        for uid in parent_uids:
            uid = str(uid)
            #print("parent uid: " + uid)
            childNode = {'id':uid, 'name':uid, 'children':[child]}
            #if currentuid=='PAV-230508FLY-88':
            #    print("childNode: ", childNode)
            
            #parentTreeList = self.__getParentTreeListLoop(childNode)
            parentTreeList, sampleUIDs = getParentTreeListLoop(childNode, sampleUIDs)
            
            #if currentuid=='PAV-230508FLY-88':
            #    print("parentTreeList: ", parentTreeList)
            
            upTreeList += parentTreeList
        
    sampleTree["parentTree"] = upTreeList
    sampleUIDs[currentuid] = sampleTree
    #if currentuid=='PAV-230508FLY-88':
    #    print("upTreeList: ", upTreeList)
    return upTreeList, sampleUIDs

def getSampleTree(sample_id, sampleDics, conn_seek, childrenTreeIn=None):
    sampleTree = {}
    try:
        sampleDic = sampleDics[sample_id]
    except:
        return sampleTree
    
    uid = sampleDic['UID']
    #tree = Sample_tree()
    #tree.id = id
    #tree.sample_id = sample_id
    #tree.uuid = uid
        
    #sampleTree['id'] = id
    sampleTree['sample_id'] = sample_id
    sampleTree['uuid'] = uid
    
    sampleTree['parents'] = getParentUIDs(sampleDic) 
    sampleTree['children'] = getChildrenUIDs(conn_seek, uid)
    #return sampleTree
        
    if childrenTreeIn is None:
        #childrenTree = self.createSampleChildrenTree(sample_id)
        childrenTree = createSampleChildrenTree(uid, sampleDics)
    else:
        childrenTree = childrenTreeIn
    sampleTree['children'] = childrenTree
        
    includeChilren = True
    #fullTreeList, parent_uids = self.__createMultiParentTree(sample_id, includeChilren, childrenTree)
    fullTreeList, parent_uids = createMultiParentTree(uid, childrenTree, includeChilren)
    sampleTree['full'] = fullTreeList
        
    #if parent_uids is None:
    #    sampleTree['parents'] = ''
    #elif len(parent_uids)==0:
    #    sampleTree['parents'] = ''
    #else:
    #    sampleTree['parents'] = ';'.join(parent_uids)
    return sampleTree

def getSampleTree0(sample, conn_seek):
    sampleTree = {}
    sample_id = sample['id']
    metadata = sample['json_metadata']
    try:
        sampleDic = json.loads(metadata)
    except:
        print("Json loads error at %d"%sample_id)
        sampleDic = json.loads(metadata.decode("utf-8","ignore"))
    uid = sampleDic['uid']
    #tree = Sample_tree()
    #tree.id = id
    #tree.sample_id = sample_id
    #tree.uuid = uid
        
    #sampleTree['id'] = id
    sampleTree['sample_id'] = sample_id
    sampleTree['uuid'] = uid
    
    sampleTree['parents'] = getParentUIDs(sampleDic) 
    sampleTree['children'] = getChildrenUIDs(conn_seek, uid)
    return sampleTree
        
    if childrenTreeIn is None:
        #childrenTree = self.createSampleChildrenTree(sample_id)
        childrenTree = createSampleChildrenTree(sample_id, sampleDics)
    else:
        childrenTree = childrenTreeIn
    sampleTree['children'] = childrenTree
        
    includeChilren = True
    #fullTreeList, parent_uids = self.__createMultiParentTree(sample_id, includeChilren, childrenTree)
    fullTreeList, parent_uids = createMultiParentTree(sample_id, includeChilren, childrenTree)
    sampleTree['full'] = fullTreeList
        
    if parent_uids is None:
        sampleTree['parents'] = ''
    elif len(parent_uids)==0:
        sampleTree['parents'] = ''
    else:
        sampleTree['parents'] = ';'.join(parent_uids)
    return sampleTree

def getSampleTrees(samples):
    conn_seek = DBconn_mysql('SEEK')
    print(len(samples))
    trees = []
    for sample in samples:
        #sample_id = sample['id']
        tree = getSampleTree0(sample, conn_seek)
        trees.append(tree)
        
    conn_seek.close()
    return trees

def getSamples():
    conn_seek = DBconn_mysql('SEEK')
    ns = conn_seek.retrieveNumberOfRecords('samples')
    print("Number of samples: %d"%ns)
    
    max_sample_id = conn_seek.getLatestID('samples')
    
    samples = conn_seek.retrieveAllRecord('samples')
    print("Number of samples retrieved: %d"%len(samples))
    sampleDics = {}
    sampleUIDs = {}
    for sample in samples:
        sample_id = sample['id']
        sample_uid = sample['uuid']
        metadata = sample['json_metadata']
        try:
            sampleDic = json.loads(metadata)
        except:
            print("Json loads error at %d"%sample_id)
            sampleDic = json.loads(metadata.decode("utf-8","ignore"))
        sampleDics[sample_id] = sampleDic
        
        if sample_uid in sampleUIDs:
            sampleTree = sampleUIDs[sample_uid]
        else:
            sampleTree = {}
            sampleTree['children'] = []
        #sampleTree['id'] = id
        sampleTree['sample_id'] = sample_id
        sampleTree['uuid'] = sample_uid
        
        parents = getParentUIDs(sampleDic)
        sampleTree['parents'] = parents
        
        #sampleTree['children'] = getChildrenUIDs(conn_seek, uid)
        for uid in parents:
            if uid in sampleUIDs:
                tree = sampleUIDs[uid]
                try:
                    children = tree['children']
                except:
                    children = []
                if sample_uid not in children:
                    children.append(sample_uid)
            else:
                tree = {}
                children = []
                children.append(sample_uid)
            tree['children'] = children
            sampleUIDs[uid] = tree
        sampleUIDs[sample_uid] = sampleTree
        
    conn_seek.close()
    return max_sample_id, sampleDics, samples, sampleUIDs
    
# function you want to run in parallel:
def myfunction(a, b):
  return a + b

def saveSampleTree(conn_dmac, sample_id, uid, sampleUIDs, id=None):
    status = 0
    try:
        tree = sampleUIDs[uid]
    except:
        print("Error: Tree not defined for %s %s"%(str(sample_id), uid))
        #continue
        return status
        
    record = {}
    if id is not None:
        record['id'] = id
        
    record['sample_id'] = sample_id
    record['uuid'] = uid
    record['parents'] = simplejson.dumps(tree['parents'], default=str)
    if uid in record['parents']:
        print(f"Sample {uid} is its own parent. Not generating tree for it to avoid infinite recursion")
        return 0
    record['children'] = simplejson.dumps(tree['children'], default=str)
    #record['full'] = simplejson.dumps(tree['parentTree'], default=str)
    #if len(tree['parents']) == 0:
    fullTree = createSampleChildrenTree(uid, sampleUIDs)[0]
    #record['full'] = simplejson.dumps([fullTree], default=str)
    #else:
    #fullTree = createMultiParentTree(uid, sampleUIDs, True)[0]
    parents = tree['parents']
    while len(parents) > 0:
        for parent in parents:
            fullTree = {'name': parent, 'id': parent, 'children': [fullTree]}
        try:
            parents = sampleUIDs[parent]['parents']
        except:
            parents = []
    try:
        record['full'] = simplejson.dumps([fullTree], default=str)
    except:
        return 0
    #record['updated'] = str(datetime.datetime.now(tz=get_current_timezone()))
    record['updated'] = str(datetime.datetime.now())

    #print(f"Record: {record}")
    #print(tree)
    try:
        #tree.save()
        print(f"Adding record: {record}")
        idout, msg = conn_dmac.storeOneRecord('seek_sample_tree', record)
        if idout>0:
            status = 1
        else:
            print(msg)
            status = 0
    except:
        msg = "Error: Failed in adding record for " + uid
        print(msg)
        status = 0
    return status

def saveTreesToDB(conn_dmac, samples, sampleUIDs):
    status = 0
    id = 1
    n = 0
    #for sample_uid, tree in sampleUIDs.items():
    for sample in samples:
        n += 1
        sample_id = sample['id']
        uid = sample['uuid']
        try:
            tree = sampleUIDs[uid]
        except:
            continue
        
        record = {}
        #record['id'] = id
        record['sample_id'] = sample_id
        record['uuid'] = uid
        record['parents'] = simplejson.dumps(tree['parents'], default=str)
        record['children'] = simplejson.dumps(tree['children'], default=str)
        record['full'] = simplejson.dumps(tree['parentTree'], default=str)
        #tree['updated'] = str(datetime.datetime.now(tz=get_current_timezone()))
        record['updated'] = str(datetime.datetime.now())
        #print(tree)
        try:
            #tree.save()
            idout, msg = conn_dmac.storeOneRecord('seek_sample_tree', record)
            if idout>0:
                status = 1
                id += 1
            else:
                #print(msg)
                status = 0
        except:
            msg = "Error: Failed in adding record for " + uid
            #print(msg)
            status = 0
        
    print("Total number of samples: %d, number uploaded: %d"%(n, id))  
    return status

def transactTreesToDB(conn_dmac, samples, sampleUIDs):
    status = 0
    id = 1
    #for sample_uid, tree in sampleUIDs.items():
    records = []
    for sample in samples:
        sample_id = sample['id']
        uid = sample['uuid']
        try:
            tree = sampleUIDs[uid]
        except:
            continue
        
        record = {}
        record['id'] = id
        record['sample_id'] = sample_id
        record['uuid'] = uid
        record['parents'] = simplejson.dumps(tree['parents'], default=str)
        record['children'] = simplejson.dumps(tree['children'], default=str)
        record['full'] = simplejson.dumps(tree['parentTree'], default=str)
        #tree['updated'] = str(datetime.datetime.now(tz=get_current_timezone()))
        record['updated'] = str(datetime.datetime.now())
        records.append(record)
        
    headers = ['id', 'sample_id', 'uuid', 'parents', 'children', 'full', 'updated']
    table = 'seek_sample_tree'
    status = conn_dmac.insertBatchRecords(table, headers, records)
    print(status)
    return status

def getSampleTreesChildren(sample_uid, sampleUIDs):
    ''' Modified from
            sampleTree = dbsample.getSampleTrees(sample_id, childrenTreeIn)
    '''
    try:
        # should be initialized in getSamples()
        sampleTree = sampleUIDs[sample_uid]
    except:
        print("Error: Sample tree not available " + sample_uid)
        return sampleUIDs
    
    #if childrenTreeIn is None:
    #    childrenTree = self.createSampleChildrenTree(sample_id)
    #else:
    #    childrenTree = childrenTreeIn
    if 'childrenTree' in sampleTree:
        # already generated, no update
        #print("Info: Children tree already generated " + sample_uid)
        return sampleUIDs
    
    childrenTree, sampleUIDs = createSampleChildrenTree(sample_uid, sampleUIDs)
    if childrenTree is None:
        print("Error: Children tree has error " + sample_uid)
        return sampleUIDs
    
    sampleTree['childrenTree'] = childrenTree
    sampleUIDs[sample_uid] = sampleTree
    
    #print(sampleTree)
    # HERE
    #return sampleUIDs
        
    includeChilren = True
    #fullTreeList, parent_uids = self.__createMultiParentTree(sample_id, includeChilren, childrenTree)
    fullTreeList, sampleUIDs = createMultiParentTree(sample_uid, sampleUIDs, includeChilren)
    #sampleTree['full'] = fullTreeList
    sampleTree['parentTree'] = fullTreeList
    sampleUIDs[sample_uid] = sampleTree
    return sampleUIDs
        
    if parent_uids is None:
        sampleTree['parents'] = ''
    elif len(parent_uids)==0:
        sampleTree['parents'] = ''
    else:
        sampleTree['parents'] = ';'.join(parent_uids)
    return sampleTree

def getSampleTreesParents(sample_uid, sampleUIDs):
    ''' Modified from
            sampleTree = dbsample.getSampleTrees(sample_id, childrenTreeIn)
    '''
    try:
        # should be initialized in getSamples()
        sampleTree = sampleUIDs[sample_uid]
    except:
        print("Error: Sample tree not available " + sample_uid)
        return sampleUIDs
    
    #if childrenTreeIn is None:
    #    childrenTree = self.createSampleChildrenTree(sample_id)
    #else:
    #    childrenTree = childrenTreeIn
    if 'parentTree' in sampleTree:
        # already generated, no update
        #print("Info: Children tree already generated " + sample_uid)
        return sampleUIDs
        
    includeChilren = True
    #fullTreeList, parent_uids = self.__createMultiParentTree(sample_id, includeChilren, childrenTree)
    fullTreeList, sampleUIDs = createMultiParentTree(sample_uid, sampleUIDs, includeChilren)
    #sampleTree['full'] = fullTreeList
    sampleTree['parentTree'] = fullTreeList
    sampleUIDs[sample_uid] = sampleTree
    
    #print(sampleTree)
    return sampleUIDs


def saveTrees(sampleUIDs):
    outfile = 'trees.txt'
    de = '\t'
    
    fo = open(outfile,"w")
    headers = ['uid', 'parents', 'children', 'childrenTree', 'parentTree']
    line = de.join(headers) + '\n'
    fo.write(line)
    for sample_uid, tree in sampleUIDs.items():
        line = sample_uid + de
        try:
            parents = tree['parents']
            parents = simplejson.dumps(parents, default=str)
        except:
            parents = ' '
        line += parents + de    
        try:
            children = tree['children']
            children = simplejson.dumps(children, default=str)
        except:
            children = ' '
        line += children + de
        try:
            childrenTree = tree['childrenTree']
            childrenTree = simplejson.dumps(childrenTree, default=str)
        except:
            childrenTree = ' '
        line += childrenTree + de
        try:
            parentTree = tree['parentTree']
            parentTree = simplejson.dumps(parentTree, default=str)
        except:
            parentTree = ' '
        line += parentTree + '\n'
        fo.write(line)
    fo.close()

def generateTrees(sanityCheck_sampleID=None):
    ''' Renew all trees in seek_sample_tree table
    Input:
        sanityCheck_sampleID, if not None, run the sanity check to generate the sample tree only for the
            sample ID of interest, without saving it into DB.
    '''
    print("generateTrees")
    start = time.time()
    conn_dmac = DBconn_mysql('NEXTSEEK')
    trees = conn_dmac.retrieveAllRecord('seek_sample_tree')
    
    mid1 = time.time()
    mi = int((mid1 - start)//60)
    se = round((mid1 - start)%60,2)
    print('Processing time for retriving all trees: %d mins %d s'%(mi, se))
    
    # Get the last sample id from the sample table
    max_sample_id, sampleDics, samples, sampleUIDs = getSamples()
    mid2 = time.time()
    mi = int((mid2 - mid1)//60)
    se = round((mid2 - mid1)%60,2)
    print('Processing time for retrieving all samples: %d mins %d s'%(mi, se))
    
    np = 0
    nn = 0
    #for tree in trees:
    #    id = tree['id']
    #    sample_id = tree['sample_id']
    #    uid = tree['uuid']
    for sample in samples:
        sample_id = sample['id']
        uid = sample['uuid']
        
        if sanityCheck_sampleID is not None and sanityCheck_sampleID!=sample_id:
            continue
        
        # modified from
        # sampleTree = dbsample.getSampleTrees(sample_id, childrenTreeIn)
        sampleUIDs = getSampleTreesChildren(uid, sampleUIDs)
        
        if sample_id % 10000 == 0:
            print(sample_id)
            
        np += 1
        continue
        
        '''
        try:
            sampleDic = sampleDics[sample_id]
        except:
            print("Error: sample id not found in samples: %s"%str(sample_id))
            nn += 1
            continue
        #print(sampleDic)
        
        if sampleDic is None:
            print("Error: sample not defined in samples: %s"%str(sample_id))
            nn += 1
            continue
        
        uid = sampleDic['uid']
        '''
        status = saveSampleTree(conn_dmac, uid, sampleUIDs, id)
        if status==1:
            np += 1
        else:
            nn += 1
            break
    print('Number of samples with children tree generated: %d'%np)
    print('Number of samples without renewal: %d'%nn)
    end = time.time()
    mi = int((end - start)/60)
    se = round((end - start)%60,2)
    print('total childrenTree time: %d mins %d s'%(mi, se))
    
    np = 0
    nn = 0
    #for tree in trees:
    #    id = tree['id']
    #    sample_id = tree['sample_id']
    #    uid = tree['uuid']
    for sample in samples:
        sample_id = sample['id']
        uid = sample['uuid']
        
        if sanityCheck_sampleID is not None and sanityCheck_sampleID!=sample_id:
            continue
        
        # modified from
        # sampleTree = dbsample.getSampleTrees(sample_id, childrenTreeIn)
        sampleUIDs = getSampleTreesParents(uid, sampleUIDs)
        
        if sanityCheck_sampleID is not None and sanityCheck_sampleID==sample_id:
            sampleTree = sampleUIDs[uid]
            sampleTree['id'] = sample_id
            print(sampleTree)
            print(sampleTree['parents'])
            print(sampleTree['children'])
            print(sampleTree["parentTree"])
        
        # sanity check
        #if uid=='PAV-230508FLY-88':
        #    print("Exit at PAV-230508FLY-88")
        #    sys.exit(1)
            
        if sample_id % 10000 == 0:
            print(sample_id)
            
        np += 1
        continue
        
        '''
        try:
            sampleDic = sampleDics[sample_id]
        except:
            print("Error: sample id not found in samples: %s"%str(sample_id))
            nn += 1
            continue
        #print(sampleDic)
        
        if sampleDic is None:
            print("Error: sample not defined in samples: %s"%str(sample_id))
            nn += 1
            continue
        
        uid = sampleDic['uid']
        '''
        status = saveSampleTree(conn_dmac, uid, sampleUIDs, id)
        if status==1:
            np += 1
        else:
            nn += 1
            break
    print('Number of samples with parent tree generated: %d'%np)
    #print('Number of samples without renewal: %d'%nn)
    #now = datetime.now(tz=get_current_timezone())
    #print(str(now))
    
    end = time.time()
    mi = int((end - start)/60)
    se = round((end - start)%60,2)
    print('total parentTree time: %d mins %d s'%(mi, se))
    
    if sanityCheck_sampleID is not None:
        saveTreesToDB(conn_dmac, [sampleTree], sampleUIDs)
        return

    #saveTrees(sampleUIDs)
    #getSamples()
    end = time.time()
    mi = int((end - start)/60)
    se = round((end - start)%60,2)
    print('total saving time: %d mins %d s'%(mi, se))
    
    
    #status = transactTreesToDB(conn_dmac, samples, sampleUIDs)
    saveTreesToDB(conn_dmac, samples, sampleUIDs)
    end2 = time.time()
    mi = int((end2 - end)/60)
    se = round((end2 - end)%60,2)
    print('total time for saving trees into DB: %d mins %d s'%(mi, se))
    return
    

def updateTrees(sample_to_update=None):
    ''' Update those trees that are present in samples table but not in seek_sample_tree table.
    '''
    print("updateTrees")
    start = time.time()
    conn_dmac = DBconn_mysql('NEXTSEEK')
    nr = conn_dmac.retrieveNumberOfRecords('seek_sample_tree')
    print("Number of tree generated: %d"%nr)
    
    next_id = conn_dmac.getLatestID('seek_sample_tree')
    print("Next id of tree generated: %d"%next_id)
    record = conn_dmac.retrieveOneRecordDic('seek_sample_tree', next_id-1)
    #print(record)
    last_sample_id = record['sample_id']
    print("Last sample id with tree: %d"%last_sample_id)
    
    # Get the last sample id from the sample table
    max_sample_id, sampleDics, samples, sampleUIDs = getSamples()
    
    mid = time.time()
    mi = int((mid - start)//60)
    se = round((mid - start)%60,2)
    print('Processing time for samples: %d mins %d s'%(mi, se))
    

    # determine number of samples to be newly added into tree table
    nsamples = max_sample_id - last_sample_id
    print("Number of samples to be added with tree: %d"%nsamples)
    '''
    # number of cores you have allocated for your slurm task:
    try:
        number_of_cores = int(os.environ['SLURM_CPUS_PER_TASK'])
        print("number_of_cores from slurm: %d"%number_of_cores)
    except:
        number_of_cores = cpu_count() # if not on the cluster you should do this instead
        print("number_of_cores from cpu count: %d"%number_of_cores)
   
    job_number = number_of_cores
    total = len(samples)
    chunk_size = int(total / job_number)
    #slice = chunks(samples, chunk_size)
    n = chunk_size
    print("Number of jobs submitted: %d"%job_number)
    slice = [samples[i:i+n] for i in range(0, total, n)]
    jobs = []
    
    
    #results = []
    #with Pool(number_of_cores) as pool:
        ##AttributeError: __exit__
        # distribute computations and collect results:
        #results = pool.starmap(getSampleTrees, slice)
        # list of tuples to serve as arguments to function:
    #    args = [(1, 2), (9, 11), (6, 2)]
    #    results = pool.starmap(myfunction, args)
    
    
    #for i, s in enumerate(slice):
    #    j = multiprocessing.Process(target=getSampleTrees, args=(i, s, sampleDics))
    #    jobs.append(j)
    #for j in jobs:
    #    j.start()
    
    #for i, s in enumerate(slice):
    #    pool = multiprocessing.Pool()
    #    result = pool.map(getSampleTrees, s)
    #    results += result
    
    pool = multiprocessing.Pool(number_of_cores)
    results[:] = pool.map(getSampleTrees, (s for i, s in enumerate(slice)))
    #    results += result
    
        
    print("Number of results: %d"%len(results))
    end = time.time()
    mi = int((end - start)/60)
    se = round((end - start)%60,2)
    print('total processing time: %d mins %d s'%(mi, se))
    '''
    
    id = next_id
    nf = 0
    #childrenTrees = {}
    #trees = {}
    #trees = []

    if sample_to_update is not None:
        try:
            sampleDic = sampleDics[sample_to_update]
        except:
            pass
        uid = sampleDic['UID']
        status = saveSampleTree(conn_dmac, sample_to_update, uid, sampleUIDs)
        return

    for i in range(nsamples):
        sample_id = last_sample_id + i + 1
        #dici, diclist = dbsample.getSampleInfo(sample_id)
        #print(sample_id)
        try:
            sampleDic = sampleDics[sample_id]
        except:
            continue
        #print(sampleDic)
        
        if sampleDic is None:
            continue
        
        if id % 1000 == 0:
            print(id)
        
        uid = sampleDic['UID']
        status = saveSampleTree(conn_dmac, sample_id, uid, sampleUIDs)
        
        '''
        conn_dmac.storeOneRecord('seek_sample_tree', tree)
        
        #tree = Sample_tree()
        #tree.id = id
        #tree.sample_id = sample_id
        #tree.uuid = uid
        
        tree = getSampleTree(sample_id, sampleDic, sampleDics, childrenTreeIn)
        
        #p = multiprocessing.Process(target = getSampleTree)
        #jobs.append(p)
        #p.start()
        
        #result = pool.map(DNA_orf_split, list_DNA_seqs)
        result = pool.map(getSampleTree, (sample_id, sampleDic, sampleDics, childrenTreeIn))
        
        nf += 1
        #if nf>5:
        #    break
        
        continue
        
        childrenTreeIn = None
        msg = 'children tree absent'
        if uid in childrenTrees:
            childrenTreeIn = childrenTrees[uid]
            msg = 'children tree present'
        
        #sampleTree = dbsample.getSampleTrees(sample_id, childrenTreeIn)
        sampleTree = getSampleTree(sample_id, sampleDic, sampleDics, childrenTreeIn) 
        
        parents = sampleTree['parents']
        tree['parents'] = simplejson.dumps(parents, default=str)
        children = sampleTree['children']
        tree['children'] = simplejson.dumps(children, default=str)
        fullTree = sampleTree['full']
        tree['full'] = simplejson.dumps(fullTree, default=str)
        tree['updated'] = str(datetime.now(tz=get_current_timezone()))
        #childrenTrees = dbsample.updateChildrenTreeDic(childrenTrees, uid, children)
        '''
        
    print('Number of samples with tree newly added: %d'%(id-1-next_id))
    print('Number of samples without tree: %d'%nf)
    #now = datetime.now(tz=get_current_timezone())
    #print(str(now))
    
    end = time.time()
    mi = int((end - start)/60)
    se = round((end - start)%60,2)
    print('total processing time: %d mins %d s'%(mi, se))

    
    #getSamples()
    
    return
    
    
    
    conn = MySQLdb.connect(host=mysqldb['HOST'], user=mysqldb['USER'], passwd=mysqldb['PASSWORD'], db=mysqldb['NAME'])
    cursor = conn.cursor()

    array_id = process_csvfile(input_csv_file, conn, cursor)
    if array_id>0:
        retrieveCellInfo(array_id, output_csv_file, conn, cursor)
        
    cursor.close ()
    conn.close ()        

def renewTrees():
    ''' Renew all trees in seek_sample_tree table, not tested
    '''
    print("renewTrees")
    start = time.time()
    conn_dmac = DBconn_mysql('NEXTSEEK')
    trees = conn_dmac.retrieveAllRecord('seek_sample_tree')
    
    # Get the last sample id from the sample table
    max_sample_id, sampleDics, samples, sampleUIDs = getSamples()
    
    mid = time.time()
    mi = int((mid - start)//60)
    se = round((mid - start)%60,2)
    print('Processing time for samples: %d mins %d s'%(mi, se))
    
    np = 0
    nn = 0
    for tree in trees:
        id = tree['id']
        sample_id = tree['sample_id']
        uid = tree['uuid']
        
        '''
        try:
            sampleDic = sampleDics[sample_id]
        except:
            print("Error: sample id not found in samples: %s"%str(sample_id))
            nn += 1
            continue
        #print(sampleDic)
        
        if sampleDic is None:
            print("Error: sample not defined in samples: %s"%str(sample_id))
            nn += 1
            continue
        
        uid = sampleDic['uid']
        '''
        if id % 1000 == 0:
            print(id)
        status = saveSampleTree(conn_dmac, uid, sampleUIDs, id)
        if status==1:
            np += 1
        else:
            nn += 1
            break
    print('Number of samples with tree renewed: %d'%np)
    print('Number of samples without renewal: %d'%nn)
    #now = datetime.now(tz=get_current_timezone())
    #print(str(now))
    
    end = time.time()
    mi = int((end - start)/60)
    se = round((end - start)%60,2)
    print('total processing time: %d mins %d s'%(mi, se))

    
    #getSamples()
    
    return
    
def renewTreesCronjob():
    ''' Renew all trees in seek_sample_tree table
    Input:
    sanityCheck_sampleID, if not None, run the sanity check to generate the sample tree only for the
    sample ID of interest, without saving it into DB.
    '''

    print("renewTreesCronjob")
    start = time.time()
    conn_dmac = DBconn_mysql('NEXTSEEK')
    trees = conn_dmac.retrieveAllRecord('seek_sample_tree')

    mid1 = time.time()
    mi = int((mid1 - start)//60)
    se = round((mid1 - start)%60,2)
    print('Processing time for retriving all trees: %d mins %d s'%(mi, se))

    print('About to export records')
    status = conn_dmac.exportRecords('seek_sample_tree')
    if status==0:
        print("Exporting records failed.")
        return
    print("Exporting records was successful")

    sqlquery = 'DELETE FROM seek_sample_tree where id>0;'
    rows = conn_dmac.retrieve_custom_sql(sqlquery)
    return generateTrees(None)

def main():
    
    #if len(sys.argv) >= 3:
        #input_csv_file = sys.argv[1]
        #output_csv_file = sys.argv[2]
    if len(sys.argv) >= 2:    
        rtype = sys.argv[1]
        
        #wga_submit(input_csv_file, output_csv_file, dbchoices('default'))
        if rtype=='update':
            updateTrees()
        elif rtype=='renew':
            renewTrees()
        elif rtype=='generate':
            try:
                sanityCheck_sampleID = int(sys.argv[2])
            except:
                sanityCheck_sampleID = None
            generateTrees(sanityCheck_sampleID)
        elif rtype=='cronjob':
            renewTreesCronjob()
        exit(1)
        
        updateTrees(input_csv_file, output_csv_file, 'NEXTSEEK')
    else:
        print("Usage: python updateTrees.py keyword\n")
        print("where the keyword has the following options: \n")
        print("     update - Update those trees that are present in samples table but not in seek_sample_tree table.\n")
        print("     renew - Renew all trees in seek_sample_tree table, not tested yet.\n")
        print("     generate - Renew all trees in seek_sample_tree table\n")
        print("     generate 89903 - Only renew the tree for sample id=89903\n)")
        print("     cronjob - Delete and re-generate all trees in seek_sample_tree table in a cron job.")
        sys.exit()
    
if __name__ == '__main__':
    main()
