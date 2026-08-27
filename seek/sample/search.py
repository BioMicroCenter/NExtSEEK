"""Advanced search over samples."""

from ..dbtable_sampleattribute import DBtable_sampleattribute
from dmac.iocsv import getConstantRows
import json
import simplejson
from dmac.conversion import toString

from .constants import SAMPLE_FILTER_MAPPING, logger


class SampleSearchMixin:
    """Mixin for :class:`~seek.sample.table.DBtable_sample`."""

    def _parseSearchFilters(self, filters, searchType, project_id=0):
        msg = ''
        status = 1
        sampletype_id = None
        attribute = None
        filter_rule = None
        filter_valueFrom = None
        filter_valueTo = None
        filtersdic = self._initSearchFilters(searchType, sampletype_id, project_id)

        if searchType == "UIDs":
            filtersdic['searchText'] = filters['filter_searchUIDs']
            field = 'uid'
            filtersdic['tableField'] = SAMPLE_FILTER_MAPPING[field]
        elif searchType=="Advanced":
            filtersdic['searchText'] = filters['filter_searchText']
            sampletype_id = filters['sampletype_id']
            filtersdic['sampletype_id'] = sampletype_id
            filtersdic['filterRules'] = [{
                "field":"sample_type_id",
                "op":"equal",
                "value":sampletype_id
            }]
            # the following not in use
            attribute = filters['attribute']
            filtersdic['attribute'] = attribute
            #filter_logic = filters['filter_logic']
            #filter_searchValue = filters['filter_searchValue']
            filtersdic['matchType'] = filters['filter_matchType']
        elif searchType=="FILTERING":
            filtersdic['searchText'] = None
            sampletype_id = filters['sampletype_id']
            attribute = filters['attribute']
            filter_rule = filters['filter_rule']
            filter_valueFrom = filters['filter_valueFrom']
            filter_valueTo = filters['filter_valueTo']
            rules = [{
                "field":"sample_type_id",
                "op":"equal",
                "value":sampletype_id
            }]
            if attribute!='none':
                sattr = DBtable_sampleattribute()
                msg, status = sattr.validateFilters(sampletype_id, attribute, filter_rule, filter_valueFrom, filter_valueTo)
                if status==0:
                    #data = {'msg':msg, 'status': status}
                    #reportData = simplejson.dumps(data, default=str)
                    #return reportData
                    return msg, status, filtersdic
            else:    
                #attribute=='none':  
                # no attribute is selected
                keyword = toString(filter_valueFrom)
                keyword = keyword.strip()
                if len(keyword)>0:
                    # Add additional rule for keyword search
                    field = "json_metadata"
                    #tableField = SAMPLE_FILTER_MAPPING[field]
                    rule = {
                        "field":field,
                        "op":"contains",
                        "value":keyword
                    }
                    rules.append(rule)
                    filtersdic['searchText'] = keyword
            
            filtersdic['filterRules'] = rules
            filtersdic['sampletype_id'] = sampletype_id
        else:
            filtersdic['searchText'] = None
            field = "json_metadata"
            filtersdic['tableField'] = SAMPLE_FILTER_MAPPING[field]
            filtersdic['sampletype_id'] = sampletype_id
        
        filtersdic['attribute'] = attribute
        filtersdic['filter_rule'] = filter_rule
        filtersdic['filter_valueFrom'] = filter_valueFrom
        filtersdic['filter_valueTo'] = filter_valueTo
        return msg, status, filtersdic

    def searchAdvanced(self, user_seek, filters, searchType, project_id=0, skip_tree=False,
                       scoped_project_ids=None):
        logger.debug('searchAdvanced')
        msg, status, filtersdic = self._parseSearchFilters(filters, searchType, project_id)
        if status==0:
            data = {'msg':msg, 'status': status}
            reportData = simplejson.dumps(data, default=str)
            return reportData

        # Data scope for API callers. Distinct from `project_id` above, which is the
        # legacy single-project path used by seek/views.py:runSampleSearch and is left
        # untouched. This one is a LIST, because a SEEK person can belong to several
        # projects, and it is applied as an EXISTS rather than a join (see
        # _sqlQuery_select_records_filters_advanced for why the join is wrong).
        #
        #   None -> unrestricted (superuser)
        #   []   -> no resolvable projects, matches nothing
        #
        # The empty list must NOT mean "no filter": that is the difference between
        # failing closed and handing an unscoped read to a caller whose projects we
        # could not resolve.
        if scoped_project_ids is not None:
            filtersdic['scoped_project_ids'] = [str(p) for p in scoped_project_ids]
        
        data = self._retrieveRecords_advanced(user_seek, filtersdic)
        
        if searchType=="UIDs":
            msg = 'ignore filtering'
            # Building the sample lineage tree does per-row DB lookups (~0.7s/row) and is
            # unused by the advanced_search API; callers that need the tree (e.g. the SEEK
            # sample-tree UI) leave skip_tree False.
            if not skip_tree:
                data['tree'] = self._getAttributeTree(data['rows'])
        elif searchType=="FILTERING":
            attribute = filtersdic['attribute']
            if attribute!='none':
                sampletype_id = filtersdic['sampletype_id']
                filter_rule = filtersdic['filter_rule']
                filter_valueFrom = filtersdic['filter_valueFrom']
                filter_valueTo = filtersdic['filter_valueTo']
                rows = self._filterSamples(data['rows'], sampletype_id, attribute, filter_rule, filter_valueFrom, filter_valueTo) 
                data['rows'] = rows
                data['total'] = len(rows)
            elif filtersdic['searchText'] is not None:
                filtersdic['matchType'] = 'CONTAIN'
                rows = self._filterSamples_advanced(data['rows'], filtersdic)    
                data['rows'] = rows
                data['total'] = len(rows)
        else:
            rows = self._filterSamples_advanced(data['rows'], filtersdic)
            data['rows'] = rows
            data['total'] = len(rows)
            
            sampleTypes = []
            for row in rows:
                sampleType = row['sample_type']
                if sampleType not in sampleTypes:
                    sampleTypes.append(sampleType)
            data['sampleTypes'] = sampleTypes
            data['noSampleTypes'] = len(sampleTypes)
            
        data['msg'] = 'okay'
        data['status'] = 1
        reportData = simplejson.dumps(data, default=str)
        return reportData

    def _filterSamples_advanced(self, jdata, filtersdic):
        sampletype_id = filtersdic['sampletype_id']
        matchType = filtersdic['matchType']
        
        searchText = filtersdic['searchText']
        from ..search import Search
        spi = Search('')
        tableField = 'json_metadata'
        categoryField = 'sample_type_id'
        # #93: designSearchPubmed returns (query, params, keywords) on every path.
        # This call site wants only the keywords -- `query` is built and then
        # discarded here, never executed -- so its params are discarded with it.
        query, _params, terms = spi.designSearchPubmed(searchText, tableField, categoryField)
        
        sampletype_id = 0
        
        n = 0
        jdata_new = []
        for data in jdata:
            json_metadata = data['json_metadata']
            sample_type_id = data['sample_type_id']
            dici = self._getRecordFromJson(json_metadata)
            
            attributeValue = self._highlightKeyValues(dici, terms, matchType)

            data['json_metadata'] = json.loads(data['json_metadata'])
            
            if len(attributeValue)==0:
                continue
    
            data['attributeValue'] = attributeValue
            if sampletype_id>0:
                if sampletype_id==sample_type_id:
                    jdata_new.append(data)
                    n += 1
            else:
                jdata_new.append(data)
                n += 1

        return jdata_new

    def _getAttributeTree(self, jdata):
        sample_ids = []
        for data in jdata:
            id = data['id']
            sample_ids.append(id)

        includeSampleTree = 1
        if includeSampleTree==1:
            headers_new, diclist_new, headersMapping = self._createSampleTreeFromDB(sample_ids)
            headers_noneConstant, diclist_constant, headers_constant = getConstantRows(headers_new, diclist_new)

            logger.debug(f"RETRIEVE: {diclist_new}")
            
            headers_inConstant = []
            for dici in diclist_constant:
                header = dici[headers_constant[0]]
                headers_inConstant.append(header)
                
            headers_filter = []
            for header in headers_new:
                if header in headers_noneConstant or header in headers_inConstant:
                    headers_filter.append(header)
                
            stypes = []
            for header in headers_filter:
                if ':' not in header:
                    continue
                
                terms = header.split(':')
                stype = terms[0]
                if '_' in stype:
                    # such as 'DNA_1'
                    terms = stype.split('_')
                    stype = terms[0]
                    
                if stype not in stypes:
                    stypes.append(stype)
            
            treeChildren = {}
            uniqueIDs = []
            for header in headers_filter:
                if ':' not in header:
                    continue
                
                terms = header.split(':')
                stype = terms[0]
                if '_' in stype:
                    # such as 'DNA_1'
                    terms2 = stype.split('_')
                    stype = terms2[0]
                
                if stype in treeChildren:
                    children = treeChildren[stype]
                else:
                    children = []
                
                attribute = terms[1]
                uniqueID = stype + ':' + attribute
                if uniqueID in uniqueIDs:
                    # such as header='DNA_1:uid' and 'DNA_2:uid', uniqueID='DNA:uid'
                    continue
                else:
                    uniqueIDs.append(uniqueID)
                
                child = {}
                child['id'] = uniqueID
                if header in headers_noneConstant:
                    #child["text"] = attribute
                    child["text"] = '<span style="color:red;">' + attribute + '</span>'
                else:
                    #child["text"] = '<span style="color:red;">' + attribute + '</span>'
                    child["text"] = attribute
                child['checked'] = 'true'
                children.append(child)
                treeChildren[stype] = children
            
            tree = []    
            for stype in stypes:
                node = {}
                node['id'] = stype
                node['text'] = stype
                node['state'] = 'closed'
                node['checked'] = 'true'
                node['children'] = treeChildren[stype]
                tree.append(node)
            
            return tree
        else:
            return None

    def parseSampleIDs(self, sample_ids):
        sampleDiclist = self.retrieveRecordsByIDs(sample_ids)
        sampleTypes = {}
        for dici in sampleDiclist:
            id = dici['id']
            uid = dici['uuid']
            terms = uid.split('-')
            sampleType = terms[0]
            if sampleType in sampleTypes:
                slist = sampleTypes[sampleType]
            else:
                slist = []
            slist.append(id)
            sampleTypes[sampleType] = slist
            
        return sampleTypes
