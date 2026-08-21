#!/usr/bin/env python
import logging
logger = logging.getLogger(__name__)


class Search():
    ''' Usage: Parse the search text in PubMed style and return a
        typpical SQL "WHERE" clause according to the search text.
    
        from search import Search
        spi = Search('')
        return spi.getSearchTermsPubmed(searchText)
    '''
    def __init__(self, keywordIn):
        self.keyword = keywordIn
        
    def __findMatchedParentheses(self, s, leftPT='(', rightPT=')'):
        ''' Refer to: https://stackoverflow.com/questions/29991917/indices-of-matching-parentheses-in-python
        
        Examples:
            S = '((((dfdfd) OR (feefe)) AND (eeewewe)) NOT (eefefe)) NOT (hhghjhh[Author])'
            find_parens(S) = {3: 9, 14: 20, 2: 21, 27: 35, 1: 36, 42: 49, 0: 50, 56: 72}
            
            s = 'ssasas'
            find_parens(s) = {}

            s = '(ssasas'
            ab = find_parens(s)
                Traceback (most recent call last):
                  File "<stdin>", line 1, in <module>
                  File "<stdin>", line 12, in find_parens
                IndexError: No matching opening parens at: 0
        '''
        toret = {}
        pstack = []

        for i, c in enumerate(s):
            if c==leftPT:
                pstack.append(i)
            elif c==rightPT:
                if len(pstack) == 0:
                    raise IndexError("No matching closing parens at: " + str(i))
                toret[pstack.pop()] = i

        if len(pstack) > 0:
            raise IndexError("No matching opening parens at: " + str(pstack.pop()))

        return toret
    
    def __parseKeyword(self, keyword):
        ''' Parse a keyword to find the clean keyword and the category.
        Input:
            keyword, such as 'CD8[MUS]', where 'CD8' is the clean keyword and
                'MUS' is the category.
        Output:
            keyword, the clean keyword without category, such as 'CD8'.
            category, tthe category, such as 'MUS', upper case.
        '''
        category = None
        if '[' not in keyword or ']' not in keyword:
            return keyword, category
            
        try:
            pts = self.__findMatchedParentheses(keyword, '[', ']')
        except:
            logger.debug("Warning: Search text has mismatched parentheses [ or ] in : " + keyword) 
            return keyword, category
            
        if pts is None or not pts:
            return keyword, category
        
        sids = []
        kws = []
        for i, j in pts.items():
            term = keyword[(i+1):j] 
            sids.append(term)
            kw = keyword[0:i]       
            kws.append(kw)
            
        if len(sids)!=1:
            logger.debug("Warning: Search text not in the right format for sample_type_id: " + keyword) 
            return keyword, category
        
        keyword = kws[0].strip()
        category = sids[0].strip()
        category = category.upper()
        return keyword, category
            
        
    def __getCategoryClause(self, category, categoryField="sample_type_id"):
        ''' Design a SQL clause, given
        Input:
            category, the name of a category, such as 'MUS', which is used in the keyword, such as 'CD8[MUS]'. 
            categoryField, the corresponding table field of the category, such as 'MUS', which is searched against
                the table field 'sample_type_id', for example.
            
        Output:
            The clause, such as
                "sample_type_id=15"
        '''
        from .dbtable_sampletype import DBtable_sampletype
        
        clause = None
        stype = DBtable_sampletype()
        sample_type_id = stype.getSampleTypeID(category)
        try:
            sample_type_id = int(sample_type_id)
        except:
            logger.debug("Warning: Search text not in the right format for sample_type_id: " + keyword) 
            return clause
        
        #clause = "sample_type_id=" + str(sample_type_id)
        clause = categoryField + '=' + str(sample_type_id)
        return clause
        
    def __designConstraint(self, field, keyword, isNOT=False, categoryField='sample_type_id'):
        keyword, category = self.__parseKeyword(keyword)
        if isNOT:
            constraint = field + " NOT LIKE '%" + str(keyword) + "%' "
        else:
            constraint = field + " LIKE '%" + str(keyword) + "%' "
        if category is None:
            return constraint
        
        clause = self.__getCategoryClause(category, categoryField)  
        if clause is None:
            logger.debug("Warning: Search text " + keyword + " not in the right format for " + categoryField)
            return constraint
        
        constraint = "(" + constraint + " AND " + clause + ")"
        return constraint
        
    def __designDualConstraint(self, field, expression, operator, termsdic):
        query = ''
        if operator not in expression:
            return None
        
        terms = expression.split(operator)
        if len(terms)!=2:
            msg = "More than two" + operator + "operations found in: " + expression
            logger.debug(msg)
            return None
                    
        keyword1 = terms[0].strip()
        keyword2 = terms[1].strip()
        
        isNOT = False
        operator2 = operator
        if operator==' NOT ':
            isNOT = True
            operator2 = ' AND '
            
        if keyword1 not in termsdic:
            constraint1 = self.__designConstraint(field, keyword1)
            query += constraint1
            if keyword2 not in termsdic:
                constraint2 = self.__designConstraint(field, keyword2, isNOT)
                query += operator2 + constraint2
            else:
                nextExpression = termsdic[keyword2]
                subQuery = self.__designIterativeQuery(nextExpression, field, termsdic)
                if subQuery is not None:
                    query += operator2 + " (" + subQuery + ") "
                
        else:
            nextExpression = termsdic[keyword1]
            subQuery1 = self.__designIterativeQuery(nextExpression, field, termsdic)
                
            if keyword2 not in termsdic:
                constraint2 = self.__designConstraint(field, keyword2, isNOT)
                if subQuery1 is not None:
                    query += " (" + subQuery1 + ") " + operator2
                query += constraint2
                
            else:
                nextExpression = termsdic[keyword2]
                subQuery2 = self.__designIterativeQuery(nextExpression, field, termsdic)
                if subQuery1 is not None:
                    if subQuery2 is not None:
                        query += " (" + subQuery1 + ") " + operator2 + " (" + subQuery2 + ") "
                    else:
                        query += " (" + subQuery1 + ") "
                else:
                    if subQuery2 is not None:
                        query += " (" + subQuery2 + ") "
                    
        return query
        
    def __validateExpression(self, expression):
        isValid = True
        operatorFound = None
        noOperators = 0
        
        expression = expression.upper().strip()
        for operator in [' NOT ', ' AND ', ' OR ']:
            if operator in expression:
                terms = expression.split(operator)
                if len(terms)!=2:
                    isValid = False
                else:
                    operatorFound = operator
                    noOperators += 1
                
        if noOperators>1:
            isValid = False
            
        return isValid, operatorFound   
        
    def __designIterativeQuery(self, expression, field, termsdic, categoryField='sample_type_id'):
        isValid, operatorFound = self.__validateExpression(expression)
        if not isValid:
            msg = 'Search expression not in the valid format: ' + expression
            logger.debug(msg)
            return None
            
        if operatorFound is None:
            constraint = self.__designConstraint(field, expression)
            query = constraint
        else:
            query = self.__designDualConstraint(field, expression, operatorFound, termsdic)     
            
        return query
    
    def __getDualKeywords(self, expression, operator, termsdic):
        keywords = []
        if operator not in expression:
            return keywords
        
        terms = expression.split(operator)
        if len(terms)!=2:
            msg = "More than two" + operator + "operations found in: " + expression
            logger.debug(msg)
            return keywords
                    
        keyword1 = terms[0].strip()
        keyword2 = terms[1].strip()
        
        if keyword1 not in termsdic:
            keywords.append(keyword1)
            if keyword2 not in termsdic:
                keywords.append(keyword2)
            else:
                nextExpression = termsdic[keyword2]
                keywords += self.__getKeywords(nextExpression, termsdic)
        else:
            nextExpression = termsdic[keyword1]
            keywords += self.__getKeywords(nextExpression, termsdic)
            if keyword2 not in termsdic:
                keywords.append(keyword2)
            else:
                nextExpression = termsdic[keyword2]
                keywords += self.__getKeywords(nextExpression, termsdic)
                    
        return keywords
    
    def __getKeywords(self, expression, termsdic):
        keywords = []
        isValid, operatorFound = self.__validateExpression(expression)
        if operatorFound is None:
            keywords.append(expression)
        else: 
            keywords += self.__getDualKeywords(expression, operatorFound, termsdic)  
        
        keywords_new = []
        for keyword in keywords:
            keyword, category = self.__parseKeyword(keyword)
            keywords_new.append(keyword)
            
        return keywords_new
    
    def designSearchPubmed(self, searchText, tableField='json_metadata', categoryField='sample_type_id'):
        ''' Design a SQL WHERE clause based on a searchText in the PubMed style
        in a database table field.
        
        Input:
            searchText, the keyword in the PubMed style, such as,
                "(CD8[MUS] and Depletion) OR CD4".
                
            tableField, the field name of a database table, such as
                tableField = 'json_metadata', which is in Samples table.
        
        Output:
            The WHERE clause in a SQL query, such as
                WHERE tableField like '%CD8%'
        
        '''
        print('designSearchPubmed')
        query = ''
        if searchText is None:
            return query
        try:
            pts = self.__findMatchedParentheses(searchText)
        except:
            logger.debug("Warning: Search text has mismatched parentheses in : " + searchText) 
            return query
        
        searching = True
        sss = searchText
        iterations = []
        termsdic = {}
        keywords = []
        while(searching):
            if pts is None or not pts:
                si = {}
                si['sss'] = sss
                si['pts'] = pts
                iterations.append(si)    
                searching = False
                
            for i, j in pts.items():
                term = sss[(i+1):j]
                termi = sss[i:(j+1)]
                if '(' not in term and ')' not in term:
                    termRep = 'term_' + str(i) + '_' + str(j+1)
                    if termRep not in termsdic:
                        termsdic[termRep] = term   
                    sss = sss.replace(termi, termRep)
            
            si = {}
            si['sss'] = sss
            si['pts'] = pts
            iterations.append(si)
            pts = self.__findMatchedParentheses(sss)
        
        query = ""
        si = iterations[-1]
        expression = si['sss']     
        query = self.__designIterativeQuery(expression, tableField, termsdic, categoryField)
        if query is None:
            query = tableField + " LIKE '%" + str(expression) + "%' "
        
        query = 'WHERE ' + query
        logger.debug("keyword query filter: " + query)
        
        keywords = self.__getKeywords(expression, termsdic)
        print(f"query: {query}\nkeywords: {keywords}")
        return query, keywords
            
    def __designSearchMatchKeywords(self, keywordList, tableField):
        '''Design a WHERE clause, given
        Input:
            keywordList, a list of keywords, such as a list of UIDs.
            tableField, the table field against, such as 'uid' or 'uuid'
         
        Output:
            The "WHERE" clause, such as,
                "WHERE uid in (uid1, uid2, ...)"
        '''
        print('designSearchMatchKeywords')
        if len(keywordList)>0:
            tarray = "('" + "','".join(keywordList) + "')"
            #tablefield = 'uid'
            #tableField = SAMPLE_FILTER_MAPPING[tablefield]
            sqlquery_filter = " WHERE " + tableField + " in " + tarray + ";"
        else:
            sqlquery_filter = " "
        
        return sqlquery_filter
            
    def __getCleanKeyword(self, keywordIn):
        keywordOut = keywordIn
        if ':' in keywordIn:
            tii = keywordIn.split(':')
            if len(tii)==3:
                keywordOut = tii[2]
            else:
                keywordOut = tii[-1]
        
        return keywordOut        
        
    def __designSearchContainKeywords(self, terms, tableField):
        '''
        Input:
            terms, keywords in several formats.
            tableField, a table field, such 'json_metadta' 
            
        Output:
            SQL WHERE clause, such as,
                'WHERE field like '%keyword%';
        '''
        query = {}
        multi_match = True
        for term in terms:
            if '&' in term:
                multi_match = False
                
        if multi_match:
            query["multi_match"] = terms  
        
        if len(terms)==1:
            term = terms[0]
            if '&' in term:
                termi = term.split('&')
                query["match_all"] = termi
        
        if "match_all" in query:
            keywords = query["match_all"]
            sqlquery_filter = ""
            n = 0
            for keyword in keywords:
                if ":" in keyword:
                    keyword = self.__getCleanKeyword(keyword)
                
                if n==0:
                    sqlquery_filter += " WHERE " + tableField
                else:
                    sqlquery_filter += " AND " + tableField
                sqlquery_filter += " LIKE '%" + str(keyword) + "%' "
                n += 1
        elif "multi_match" in query:
            keywords = query["multi_match"]
            sqlquery_filter = ""
            n = 0
            for keyword in keywords:
                if ":" in keyword:
                    keyword = self.__getCleanKeyword(keyword)
                
                if n==0:
                    sqlquery_filter += " WHERE " + tableField
                else:
                    sqlquery_filter += " OR " + tableField
                sqlquery_filter += " LIKE '%" + str(keyword) + "%' "
                n += 1
        else:
            sqlquery_filter = ""
            n = 0
            for term in terms:
                if n==0:
                    if '&' not in term:
                        keyword = term
                        sqlquery_filter += " WHERE " + tableField
                        if ":" in keyword:
                                keyword = self.__getCleanKeyword(keyword)
                                
                        sqlquery_filter += " LIKE '%" + str(keyword) + "%' "
                    else:
                        keywords = term.split('&')
                        i = 0
                        for keyword in keywords:
                            if ":" in keyword:
                                keyword = self.__getCleanKeyword(keyword)
                            
                            if i==0:   
                                sqlquery_filter += " WHERE (" + tableField
                            else:
                                sqlquery_filter += " AND " + tableField
                            sqlquery_filter += " LIKE '%" + str(keyword) + "%' "
                            i += 1
                        sqlquery_filter += " )" 
                else:
                    if '&' not in term:
                        keyword = term
                        if ":" in keyword:
                            keyword = self.__getCleanKeyword(keyword)
                            
                        sqlquery_filter += " OR " + tableField
                        sqlquery_filter += " LIKE '%" + str(term) + "%' "
                    else:
                        keywords = term.split('&')
                        i = 0
                        for keyword in keywords:
                            if ":" in keyword:
                                keyword = self.__getCleanKeyword(keyword)
                            
                            if i==0:   
                                sqlquery_filter += " OR (" + tableField
                            else:
                                sqlquery_filter += " AND " + tableField
                            sqlquery_filter += " LIKE '%" + str(keyword) + "%' "
                            i += 1
                        sqlquery_filter += " )" 
                n += 1
                
        return sqlquery_filter     
        
    def __designSearchFilters(self, filtersdic, fieldMapping):
        ''' Design a "WHERE" clause, given,
        Input:
            filtersdic, a dictionary with,
                filterRules = filtersdic['filterRules']
                            = [rule1, rule2, ...], where
                    rule = {'field':field, 'value':value, 'op':operator}
                
            fieldMapping, = {'field1':tableField1, 'field2':tableField2, ...}
        
        Output:
            SQL WHERE clause, such as,
                'WHERE tableField=value;
        '''
        filterRules = filtersdic['filterRules'] 
        sqlquery_filter = ""
        n = 0
        for rule in filterRules:
            tablefield = rule["field"]
            value = rule["value"]
            op = rule["op"]
            if tablefield in fieldMapping:
                field = fieldMapping[tablefield]
            
                if n==0:
                    sqlquery_filter += " WHERE " + field
                else:
                    sqlquery_filter += " AND " + field
                if op=="contains":
                    sqlquery_filter += " LIKE '%" + str(value) + "%' "
                elif op=="equal":
                    sqlquery_filter += "='" + str(value) + "' "
                else:
                    sqlquery_filter += " LIKE '%" + str(value) + "%' "
            n += 1
        
        return sqlquery_filter
        
    def __getSearchTerms(self, searchText):
        terms = []
        if searchText is None:
            return terms
        
        # split accoring to new line
        if '\n' in searchText:
            lines = searchText.split('\n')
        elif '\n\r' in searchText:
            lines = searchText.split('\n\r')
        else:
            lines = [searchText]
            
        for line in lines:
            line = line.strip()
            for delimiter in (',', ';', ' ', '\t'):
                if delimiter in line:
                    termi = line.split(delimiter)
                    for term in termi:
                        term = term.strip()
                        if len(term)>0 and term not in terms:
                            terms.append(term)
            
            if len(line)>0 and line not in terms:
                terms.append(line)
                
        if len(terms)==0:
            terms.append(searchText.strip())
        return terms
    
    def designSearchAdvanced(self, filtersdic, fieldMapping):
        ''' Design "WHERE" clause, given
        Input:
            filtersdic, = {
                'searchType': searchType,   # type of search
                'searchText': keywords,     # keywords of the search
                'orderby':...,              # sorting
                'limit':...,                # pagination
                'suffix':...,               # clause of the pagination
                'startNo':...,              # pagination 
                'endNo':...,                # pagination
                'sqlquery_filter':...,      # output of the 'WHERE" clause
                'project_id':...,           # scope of the search
                'filterRules': [{           # rules for the search
                    "field":"sample_type_id",
                    "op":"equal",
                    "value":sampletype_id
                }]
            }, where 'searchType' has the following options:
                'FILTERING', used for the search with the selection of
                    (sample type, attribute name, value, range);
                'UIDS', given a list of sample UIDs;
                'PUBMED', pubMed style advanced search;
                other, not in use really.
            
            
            fieldMapping,                   # mapping between keywords and DB fields
            tableField,                     # the table field in search 
            categoryField                   # the category of the search
        
        
        '''
        sqlquery_filter = ' '
        if 'searchType' not in filtersdic:
            return sqlquery_filter
        
        searchType = filtersdic['searchType']
        if searchType=='FILTERING':
            sqlquery_filter = self.__designSearchFilters(filtersdic, fieldMapping)
            return sqlquery_filter
            
        if 'searchText' not in filtersdic:
            return sqlquery_filter
            
        searchText = filtersdic['searchText']
        terms = self.__getSearchTerms(searchText)
        tableField = filtersdic['tableField']
        categoryField = filtersdic['categoryField']
        if searchType=="UIDs" or searchType=="UIDS":
            sqlquery_filter = self.__designSearchMatchKeywords(terms, tableField)
        elif searchType=="Advanced" or searchType=="PUBMED":
            sqlquery_filter, keywords = self.designSearchPubmed(searchText, tableField, categoryField)
        else:
            # will never reach here
            sqlquery_filter = self.__designSearchContainKeywords(terms, tableField)
        return sqlquery_filter
           
