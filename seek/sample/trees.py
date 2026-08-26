"""Parent/child sample-tree construction, including the parallel walks.

``unwrap_self_*`` are module-level trampolines named inside joblib
``delayed(...)`` calls, so they must stay in the same module as the
``*Parallel_i`` methods they wrap."""

from neo4j import GraphDatabase
from joblib import Parallel
from joblib import delayed
from dmac.iocsv import filterDiclist
import json
import pandas as pd
from django.conf import settings

from .constants import SAMPLE_PARENT_ACCESSOR_NAME, SEEK_DATABASE, logger

def unwrap_self_createMultiParentTreeParallel_i(arg, **kwarg):    
    from .table import DBtable_sample  # deferred: circular import
    return DBtable_sample.createMultiParentTreeParallel_i(*arg, **kwarg)
def unwrap_self_createSampleChildrenTreeParallel_i(arg, **kwarg):
    from .table import DBtable_sample  # deferred: circular import
    return DBtable_sample.createSampleChildrenTreeParallel_i(*arg, **kwarg)


class SampleTreesMixin:
    """Mixin for :class:`~seek.sample.table.DBtable_sample`."""

    def getChildrenUIDs(self, sample_uids, user_project_ids, admin):
        db = settings.DATABASES[SEEK_DATABASE]
        NEO4J_DATABASE = settings.NEO4J_DATABASE
        with GraphDatabase.driver(NEO4J_DATABASE['URI'], auth=NEO4J_DATABASE['AUTH']) as driver:
            r,s,k = driver.execute_query("""
    		UNWIND $sample_uids AS sample_uid
            MATCH (s:Sample {uuid: sample_uid})
            MATCH parents=(s)-[:DERIVED_FROM*0..]->(parent)
            MATCH children=(s)<-[:DERIVED_FROM*0..]-(child)
            RETURN collect(DISTINCT s.uuid) + collect(DISTINCT parent.uuid) + collect(DISTINCT child.uuid) AS uuids
            """,
            sample_uids=sample_uids,
            database_=NEO4J_DATABASE['NAME'])
            uids = r[0]['uuids']

        # No Sample nodes matched the requested UIDs (e.g. data-file UIDs absent
        # from the graph, or UIDs not present in this database). Return an empty
        # frame rather than building invalid ``WHERE uuid IN ()`` SQL — which
        # _runQuery swallows into None, crashing the caller with
        # "cannot unpack non-iterable NoneType object" (a 500). The view treats
        # an empty frame as a clean 404 "No samples found".
        if not uids:
            return pd.DataFrame(columns=["id", "sample_type_id", "uuid", "json_metadata"])

        uids_str = ', '.join(f"'{uid}'" for uid in uids)
        project_ids_str = ', '.join(f"'{pid}'" for pid in user_project_ids)

        if admin:
            query = f"""
            SELECT id,sample_type_id,uuid,json_metadata
            FROM {db["NAME"]}.samples
            WHERE uuid IN ({uids_str})
            """
        else:
            query = f"""
            SELECT s.id, s.sample_type_id, s.uuid, s.json_metadata
            FROM {db["NAME"]}.samples s
            JOIN {db["NAME"]}.projects_samples ps
            ON s.id = ps.sample_id
            WHERE s.uuid IN ({uids_str}) AND ps.sample_id = s.id AND ps.project_id IN ({project_ids_str})
            """

        result = self._runQuery(query, withColumns=True)
        if not result:
            # Query failed (e.g. transient DB error). Degrade to an empty frame
            # so the endpoint returns a clean 404 instead of a 500 unpack crash.
            return pd.DataFrame(columns=["id", "sample_type_id", "uuid", "json_metadata"])
        rows, columns = result
        samples_retrieved_df = pd.DataFrame(rows, columns=columns)

        return samples_retrieved_df

    def _getParentUIDs(self, sampleDic):
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
                            if len(vi)>0:
                                uids.append(vi)
                    else:
                        value = value.strip()
                        if len(value)>0:
                            uids.append(value)
                
        return uids

    def _getParents(self, childuid):
        record_db = self._retrieveSampleByUID(childuid)
        if record_db is None:
            return []
            
        metadata = record_db['json_metadata']
        sampleDic = self._getRecordFromJson(metadata)
        uids = self._getParentUIDs(sampleDic)
        return uids

    def _getChildrenUIDs(self, currentuid):
        records = self.db.retrieveRecords(self.tablemodel, 'json_metadata', currentuid)
        uids = []
        for record in records:
            uid = record['uuid']
            metadata = record['json_metadata']
            sampleDic = self._getRecordFromJson(metadata)
            parent_uids = self._getParentUIDs(sampleDic)
            if currentuid in parent_uids:
                uids.append(uid)
            
        return uids

    def _getChildLoop(self, parentuid):
        child = {}
        child["name"] = str(parentuid)
        child["id"] = str(parentuid)
        child_uids = self._getChildrenUIDs(parentuid)
        if len(child_uids)==0:
            return child
        
        next_children = []
        for uid in child_uids:
            next_child = self._getChildLoop(uid)
            next_children.append(next_child)
        
        if len(next_children)>0:
            child["children"] = next_children
        return child

    def createSampleChildrenTree(self, sample_id):
        return self.createSampleChildrenTreeParallel(sample_id)

    def createSampleChildrenTreeParallel_i(self, uid):
        child = self._getChildLoop(uid)
        return child

    def createSampleChildrenTreeParallel(self, sample_id):
        record = self._retrieveSampleByID(sample_id)
        if record is None:
            return None
        
        currentuid = record['uuid']
        children_uids =  self._getChildrenUIDs(currentuid)
        
        treeData = {}
        treeData["name"] = str(currentuid)
        treeData["id"] = str(currentuid)
        
        children = []
        n = len(children_uids)
        childs = Parallel(n_jobs=-2, backend="threading")\
            (delayed(unwrap_self_createSampleChildrenTreeParallel_i)(i) for i in zip([self]*n, children_uids))

        for child in childs:
            children.append(child)

        if len(children)>0:
            treeData["children"] = children
        return treeData

    def _getParentTreeListLoop(self, childNode):
        upTreeList = []
        childuid = childNode['id']
        parent_uids = self._getParents(childuid)
        if parent_uids is None or len(parent_uids)==0:
            upTreeList.append(childNode)
            return upTreeList
        
        for uid in parent_uids:
            uid = str(uid)
            node = {'id':uid, 'name':uid, 'children':[childNode]}
            parentTreeList = self._getParentTreeListLoop(node)
            upTreeList += parentTreeList
            
        return upTreeList

    def _createMultiParentTree(self, sample_id, includeChilren, childrenTreeIn=None):
        return self._createMultiParentTreeParallel(sample_id, includeChilren, childrenTreeIn)

    def createMultiParentTreeParallel_i(self, uid, child):
        uid = str(uid)
        childNode = {'id':uid, 'name':uid, 'children':[child]}
        parentTreeList = self._getParentTreeListLoop(childNode)
        return parentTreeList

    def _createMultiParentTreeParallel(self, sample_id, includeChilren, childrenTreeIn=None):
        record = self._retrieveSampleByID(sample_id)
        if record is None:
            return None, None
        
        childuid = record['uuid']
        json_metadata = record['json_metadata']
        dici = self._getRecordFromJson(json_metadata)
        parent_uids =  self._getParentUIDs(dici)
        # Parent uids aren't being found
        
        childuid = str(childuid)
        child = {'id':childuid, 'name':childuid}
        if includeChilren:
            if childrenTreeIn is None:
                child = self.createSampleChildrenTree(sample_id)
            else:
                child = childrenTreeIn
        
        upTreeList = []
        if len(parent_uids)==0:
            upTreeList.append(child)
        else:
            n = len(parent_uids)
            parentTreeLists = Parallel(n_jobs=-2, backend="threading")\
                (delayed(unwrap_self_createMultiParentTreeParallel_i)(i) for i in zip([self]*n, parent_uids, [child]*n))
            
            for parentTreeList in parentTreeLists:
                upTreeList += parentTreeList
        
        return upTreeList, parent_uids

    def _getChildrenListLoop(self, parentTreeData):
        listlists = []
        for node in parentTreeData:
            if 'children' in node:
                children = node['children']
                sublists = self._getChildrenListLoop(children)
            else:
                sublists = [[]]
            
            uid = node['id']
            for listi in sublists:
                newlist = [uid] + listi
                listlists.append(newlist)
        
        return listlists

    def _convertSampleTreeToList(self, parentList, sampleTypes, sampleTypeCount, headers):
        uids = {}
        for listi in parentList:
            for uid in listi:
                if uid not in uids:
                    sampleDic = self._retrieveSampleJsonData(uid)
                    uids[uid] = sampleDic
                    
        diclist_new = []
        for listi in parentList:
            dici_new = {}
            sampleTypeCount_now = {}        
            for uid in listi:
                if "-" in uid:
                    terms = uid.split('-')
                    sampleType = terms[0]
                else:
                    sampleType = uid
                
                if sampleType not in sampleTypeCount_now:
                    sampleTypeCount_now[sampleType] = 1
                else:
                    sampleTypeCount_now[sampleType] = sampleTypeCount_now[sampleType] + 1

                count = sampleTypeCount[sampleType]
                if count==1:
                    prefix = sampleType + ':'     
                else:
                    suffix = "_" + str(sampleTypeCount_now[sampleType])
                    prefix = sampleType + suffix + ':'       # such as "DNA_2:"
                
                sampleDic = uids[uid]
                if sampleDic is not None and sampleDic is not []:
                    for key, value in sampleDic.items():
                        newkey = prefix + key
                        dici_new[newkey] = value        
            diclist_new.append(dici_new)
        
        headers_new = filterDiclist(headers, diclist_new)
        return headers_new, diclist_new

    def _createSampleTree(self, sample_ids):
        includeChilren = False
        parentList = []
        for sample_id in sample_ids:
            upTreeList, parent_uids = self._createMultiParentTree(sample_id, includeChilren)
            parentList_i = self._getChildrenListLoop(upTreeList)
            parentList += parentList_i
        
        sampleTypes, sampleTypeCount, headers, headersMapping = self._getSampleTypeAttributes(parentList)       
        headers_new, diclist_new = self._convertSampleTreeToList(parentList, sampleTypes, sampleTypeCount, headers)       
        return headers_new, diclist_new, headersMapping

    def _createSampleTreeFromDB(self, sample_ids):
        from .models import Sample_tree
        
        includeChilren = True
        parentList = []
        ntotal = len(sample_ids)
        n = 0
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
        
        sampleTypes, sampleTypeCount, headers, headersMapping = self._getSampleTypeAttributes(parentList)       
        headers_new, diclist_new = self._convertSampleTreeToList(parentList, sampleTypes, sampleTypeCount, headers)       
        return headers_new, diclist_new, headersMapping
