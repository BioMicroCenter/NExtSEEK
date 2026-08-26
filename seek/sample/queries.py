"""SQL building for sample retrieval, filtering and keyword highlighting."""

from ..dbtable_sampleattribute import DBtable_sampleattribute
import html
from django.conf import settings

from .constants import SAMPLE_FILTER_MAPPING, SAMPLE_HEADERS, logger


class SampleQueriesMixin:
    """Mixin for :class:`~seek.sample.table.DBtable_sample`."""

    def _sqlQuery_select_records_select(self):
        sqlquery_select =  " SELECT "
        sqlquery_select +=  "A.id as id,"
        sqlquery_select +=  "A.title as title,"
        sqlquery_select +=  "A.sample_type_id as sample_type_id,"
        sqlquery_select +=  "B.title as sample_type,"
        sqlquery_select +=  "A.uuid as uid,"
        sqlquery_select +=  "A.contributor_id as contributor_id,"
        sqlquery_select +=  "C.first_name as first_name,"
        sqlquery_select +=  "A.created_at as created_at,"
        sqlquery_select +=  "A.json_metadata as json_metadata,"
        
        sqlquery_select +=  "("
        sqlquery_select +=  "SELECT GROUP_CONCAT(E.title) as assays "
        sqlquery_select +=  "FROM assay_assets D "
        sqlquery_select +=  "left join assays E on E.id=D.assay_id "
        sqlquery_select +=  "WHERE A.id=D.asset_id AND D.asset_type='Sample' "
        sqlquery_select +=  ") "
        return sqlquery_select

    def _sqlQuery_select_records_from(self, projectID=None):
        sqlquery_from =  " FROM "   
        sqlquery_from +=  "samples A "
        sqlquery_from +=  "left join sample_types B on A.sample_type_id=B.id "
        sqlquery_from +=  "left join people C on A.contributor_id=C.id "
        if projectID is not None and projectID!=0:
            sqlquery_from +=  "left join projects_samples D on D.sample_id=A.id "
        
        return sqlquery_from     

    def _sqlQuery_select_records(self, filtersdic, withLimit=True):
        # The query that this generates does not return all items that it should
        # in the simple search page
        sqlquery_select = self._sqlQuery_select_records_select()
        project_id = filtersdic['project_id']
        sqlquery_from = self._sqlQuery_select_records_from(project_id)
        orderby = filtersdic['orderby'] 
        sqlquery_where = self._sqlQuery_select_records_filters_advanced(filtersdic)
        sqlqueryMega = sqlquery_select + sqlquery_from + sqlquery_where
        if len(orderby)==0:
            orderby = " ORDER BY A.id desc"
        if withLimit:
            sqlqueryMega = sqlquery_select + sqlquery_from + sqlquery_where + orderby
        else:
            sqlqueryMega = " SELECT count(A.id) " + sqlquery_from
        logger.debug(sqlqueryMega)
        return sqlqueryMega

    def _filterSamples(self, jdata, sampletype_id, attribute, filter_rule, filter_valueFrom, filter_valueTo):
        logger.debug('filterSamples')
        
        if filter_rule=='No Filter':
            return jdata
        
        accessor_name = attribute.strip()
        
        values = []     
        n = 0
        for data in jdata:
            json_metadata = data['json_metadata']
            dici = self._getRecordFromJson(json_metadata)
            
            if accessor_name not in dici:
                value = None
            else:
                value = dici[accessor_name]
            values.append(value)
            
            #uids = self._getParentUIDs(dici)
            #parentUIDs.append(uids)
            
            n += 1
            
        sattr = DBtable_sampleattribute()
        passvalues = sattr.filterValues(values, sampletype_id, attribute, filter_rule, filter_valueFrom, filter_valueTo)
        
        jdata_new = []
        index = 0
        ni = 0
        nf = 0
        for data in jdata:
            passit = passvalues[index]
            if passit:
                json_metadata = data['json_metadata']
                dici = self._getRecordFromJson(json_metadata)
                attributeValue = self._highlightKeyValues(dici, None, None, accessor_name)
                if len(attributeValue)==0:
                    nf += 1
                    continue
                
                data['attributeValue'] = attributeValue
                
                #childuid = data['uid']
                #uids = parentUIDs[index]
                #parentinfo, treeData = self.__trackParent(childuid, uids)
                #data['parent_uids'] = ';'.join(uids)
                
                jdata_new.append(data)
                ni += 1
            index += 1
        
        print("Total number of samples retrieved: %d"%index)
        print("Total number of samples passing filter: %d"%ni)
        print("Total number of samples passing filter but not highlighted: %d"%nf)

        logger.debug("Total number of samples retrieved: %d"%index)
        logger.debug("Total number of samples passing filter: %d"%ni)
        logger.debug("Total number of samples passing filter but not highlighted: %d"%nf)
        return jdata_new

    def _initSearchFilters(self, searchType, sampletype_id, project_id=0):
        filtersdic = {}
        filtersdic['orderby'] = " ";
        filtersdic['limit'] = " ";
        filtersdic['suffix'] = " ";
        filtersdic['startNo'] = " "
        filtersdic['endNo'] = " "
        filtersdic['sqlquery_filter'] = " "
        filtersdic['project_id'] = project_id
        if sampletype_id is None:
            filterRules = []
        else:
            filterRules = [{
                "field":"sample_type_id",
                "op":"equal",
                "value":sampletype_id
            }]
        
        filtersdic['tableField'] = 'json_metadata'
        filtersdic['categoryField'] = 'sample_type_id'
        filtersdic['filterRules'] = filterRules
        filtersdic['searchType'] = searchType
        return filtersdic

    def _retrieveSamplesInType(self, user_seek, sampletype_id, project_id=0):
        searchType = 'FILTERING'
        filtersdic = self._initSearchFilters(searchType, sampletype_id, project_id)
        data = self._retrieveRecords_advanced(user_seek, filtersdic)
        return data

    def _retrieveRecords_advanced(self, user_seek, filtersdic):
        sqlquery = self._sqlQuery_select_records(filtersdic)
        headers = SAMPLE_HEADERS
        db_alias = settings.SEEK_DATABASE
        jdata = self.db.queryToListDics(sqlquery, headers, db_alias)
        total = len(jdata)
        #sqlquery = self._sqlQuery_select_records(filtersdic, False)
        #total = self.db.getQueryValue(sqlquery, db_alias)
        #if total is None:
        #    total = 0
        #else:
        #    total = int(total)
    
        jdata_new = self.reformatDataForClient(jdata)
        footer = []
        data = {'total':total,'rows':jdata_new,'footer':footer}
        return data

    def _sqlQuery_select_records_filters_advanced(self, filtersdic):
        from .search import Search
        spi = Search('')
        sqlquery_filter = spi.designSearchAdvanced(filtersdic, SAMPLE_FILTER_MAPPING)            
        if 'project_id' in filtersdic:
            project_id = filtersdic['project_id']
            if int(project_id)>0:
                sqlquery_filter = sqlquery_filter.replace('WHERE ', 'WHERE (')
                sqlquery_filter = sqlquery_filter + ") AND D.project_id=" + str(project_id)    
            
        logger.debug(sqlquery_filter)
        return sqlquery_filter

    def _highlightKeyword(self, keyword, value, style=None):
        defaultStyle = "color:red;"
        if style is None:
            style = defaultStyle

        # The result is rendered as raw HTML by the client, so HTML-escape the
        # untrusted keyword/value before splicing them into the highlight span.
        # html.escape maps each character independently, so substring matching
        # on the escaped strings is equivalent to matching on the originals.
        keyword = html.escape(str(keyword))
        value = html.escape(str(value))

        if keyword in value:
            newKeyword = '<span style="' + style + '">' + keyword + '</span>'
            value = value.replace(keyword, newKeyword)
        else:
            kl = keyword.lower()
            vl = value.lower()
            if kl in vl:
                pos = vl.find(kl)
                keyw = value[pos:(pos+len(keyword))]
                newKeyword = '<span style="' + style + '">' + keyw + '</span>'
                value = value.replace(keyw, newKeyword)
        return value

    def _highlightKeyValues(self, dici, terms, matchType, attribute=None):
        separator = ',   '
        attributeValue = ''
        ki = 0
        for key, value in sorted(dici.items()):
            if value is None:
                continue
            try:
                value = str(value)
            except:
                continue
            
            if attribute is not None:
                if key==attribute:
                    key = self._highlightKeyword(key, key, "color:blue;font-weight:bold;")
                    attributeValue += key + ':' + self._highlightKeyword(value, value)
                continue
                
            key = self._highlightKeyword(key, key, "color:blue;font-weight:bold;")
            valuel = value.lower()
            for term in terms:
                if matchType=='EXACT':
                    if term==value or term.upper()==value.upper():
                        if ki==0:
                            attributeValue += key + ':' + self._highlightKeyword(term, value)
                        else:
                            attributeValue += separator + key + ':' + self._highlightKeyword(term, value)
                        ki += 1
                    continue   
                        
                if term in value or term.lower() in valuel:     
                    if ki==0:
                        attributeValue += key + ':' + self._highlightKeyword(term, value)
                    else:
                        attributeValue += separator + key + ':' + self._highlightKeyword(term, value)
                    ki += 1
                elif "&" in term:
                    termi = term.split("&")
                    for ti in termi:
                        if ':' in ti:
                            ti = self._getCleanKeyword(ti)
                                
                        if ti in value or ti.lower() in valuel:
                            if ki==0:
                                attributeValue += key + ':' + self._highlightKeyword(ti, value)
                            else:
                                attributeValue += separator + key + ':' + self._highlightKeyword(ti, value)
                            ki += 1
                elif "^" in term:
                    termi = term.split("^")
                    for ti in termi:
                        if ':' in ti:
                            ti = self._getCleanKeyword(ti)

                        if ti in value or ti.lower() in valuel:
                            if ki==0:
                                attributeValue += key + ':' + self._highlightKeyword(ti, value)
                            else:
                                attributeValue += separator + key + ':' + self._highlightKeyword(ti, value)
                            ki += 1            
                elif ':' in term:
                    term = self._getCleanKeyword(term)
                    if term in value or term.lower() in valuel:
                        if ki==0:
                            attributeValue += key + ':' + html.escape(str(value))
                        else:
                            attributeValue += separator + key + ':' + self._highlightKeyword(term, value)
                        ki += 1
                    
        return attributeValue

    def _getCleanKeyword(self, keywordIn):
        keywordOut = keywordIn
        if ':' in keywordIn:
            tii = keywordIn.split(':')
            if len(tii)==3:
                keywordOut = tii[2]
            else:
                keywordOut = tii[-1]
        
        return keywordOut
