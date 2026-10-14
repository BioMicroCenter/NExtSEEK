#!/usr/bin/env python
import json

import shlex
from subprocess import Popen, PIPE

import logging
logger = logging.getLogger(__name__)

# (connect, read) seconds for a SEEK page fetch, matching nextseek_api/helpers.py. A page SEEK
# cannot render in time becomes an empty panel, never an unbounded wait.
PAGE_TIMEOUT_S = (10, 300)

class SeekAPI(object):
    def __init__(self, server, username, password):
        self.__server = server
        self.__username = username
        self.__password = password
            
    def __curlPrefix(self):
        if self.__username is None:
            curl_prefix = "curl -k "
        else:    
            # One shell word whatever the password holds (callCmdline splits it with shlex).
            curl_prefix = "curl -u " + shlex.quote(self.__username + ":" + self.__password) + " -k "
        
        return curl_prefix
            
    def apiPost(self):
        apicmd = self.__curlPrefix() + " -X POST \"" + self.__server + "\""
        return apicmd
    
    def callCmdline(self, cmd):
        args = shlex.split(cmd)
        proc = Popen(args, stdout=PIPE, stderr=PIPE)
        out, err = proc.communicate()
        exitcode = proc.returncode
        return exitcode, out, err
        
    def runGetQuery(self, queryurl):
        import requests
        # Imported inside the method: nextseek_api.helpers imports seek.seekdb, which imports this module.
        from nextseek_api.helpers import basic_auth_header
        headers = {"accept": "application/json",
                   **basic_auth_header((self.__username, self.__password))}
        # No raise_for_status: like the curl call this replaces, the body decides (Access denied -> None).
        text = requests.get(self.__server + queryurl, headers=headers,
                            verify=False, timeout=PAGE_TIMEOUT_S).content.decode("utf-8")
        if 'Access denied' in text:
            return None
        return json.loads(text)
    
    def getIDfromTitle(self, url, title):
        json_objects = self.runGetQuery(url)
        
        title = title.split("/")[-1]
        title = title.strip("\n")
        id = None
        for x in range(0, len(json_objects["data"])):
            if json_objects["data"][x]["attributes"]["title"] == title:
                id = json_objects["data"][x]["id"]
                
        return id
    
    
    def __reviseURLs(self, htmlpage):
        htmlpage = htmlpage.replace('/assets/', (self.__server + '/assets/'))
        htmlpage = htmlpage.replace('href="/samples/', ('href="/seek/sample/id='))
        return htmlpage
        
    def __getHtmlpageDiv(self, htmlpage, div_id):
        # Both lookups can come back None and neither used to be checked: a page with no <body>,
        # and a body with no div of this id. SEEK returns both for an error page, a login redirect
        # and a page it could not render in time, and .prettify() on None then reached the caller
        # as a 500 (measured 2026-09-16 on /seek/sample_types/id=142/). Empty means "no panel",
        # which every caller already renders around.
        from bs4 import BeautifulSoup
        parsed_html = BeautifulSoup(htmlpage)
        body = parsed_html.body
        if body is None:
            logger.warning("SEEK page has no body; returning no %s panel", div_id)
            return ""
        bodyhtml = body.find('div', attrs={'id':div_id})
        if bodyhtml is None:
            logger.warning("SEEK page carries no div#%s; returning no panel", div_id)
            return ""
        return bodyhtml.prettify()

    def getPageRequests(self, seekurl):
        import requests
        # Imported inside the method: nextseek_api.helpers imports seek.seekdb,
        # which imports this module, so a top-level import here would cycle.
        from nextseek_api.helpers import basic_auth_header
        urlIn = self.__server + seekurl
        # (connect, read), the pair nextseek_api/helpers.py already uses for SEEK. Without it this
        # call waits as long as SEEK takes and holds the worker with it.
        response = requests.get(urlIn,
                                headers=basic_auth_header((self.__username, self.__password)),
                                verify=False,
                                timeout=PAGE_TIMEOUT_S)
        status = getattr(response, "status_code", None)
        if status is not None and status >= 400:
            logger.warning("SEEK answered %s for %s; returning no panel", status, seekurl)
            return ""
        htmlpage = response.text
        htmlpage = self.__reviseURLs(htmlpage)
        return self.__getHtmlpageDiv(htmlpage, 'content')
    
    
    def getCurrentUser(self):
        import requests
        # Basic auth is encoded here rather than passed as requests' auth= so a
        # non-Latin-1 password doesn't raise UnicodeEncodeError (#52). Imported
        # inside the method to avoid the seekdb <-> seekapi import cycle.
        from nextseek_api.helpers import basic_auth_header
        headers = {"content-type": "application/json",
                   "accept": "application/json"}
        headers.update(basic_auth_header((self.__username, self.__password)))
        req = requests.get(self.__server + "/people/current",
                           headers=headers)
        return req.json()
    
    def __getStudyTitle(self, study_id):
        queryurl = "/studies/" + study_id
        json_study = self.runGetQuery(queryurl)
        study_title = json_study["data"]["attributes"]["title"]
        return study_title
    
    
    def getStudies(self, investigation_id):
        studies = {}
        queryurl = "/investigations/" + investigation_id
        json_study_link = self.runGetQuery(queryurl)
        study_count = len(json_study_link["data"]["relationships"]["studies"]["data"])
        for i in range(study_count):
            study_id = json_study_link["data"]["relationships"]["studies"]["data"][i]["id"]
            study_title = self.__getStudyTitle(study_id)
            studies[study_title] = study_id
        
        return studies
    
    def getAssays(self, studyid):
        study_id = studyid.split("/")[-1]
        study = self.runGetQuery("/studies/" + study_id)
        assays = {}
        if not study or "data" not in study:
            return assays  # a missing or invisible study, or a refused login: no assays, as before
        for ref in study["data"]["relationships"]["assays"]["data"]:
            assay = self.runGetQuery("/assays/" + ref["id"])
            if assay and "data" in assay:
                assays[assay["data"]["attributes"]["title"]] = ref["id"]
        return assays

    def getObjectsToOptions(self, objectName):
        queryurl = objectName
        jsons = self.runGetQuery(queryurl)
        objects = jsons["data"]
        options = []
        options.append({'id':0, 'title':'','selected':True})
        for dici in objects:
            id = dici['id']
            title = dici['attributes']['title']
            options += [ {'id':id, 'title':title}]
        
        return options
        
