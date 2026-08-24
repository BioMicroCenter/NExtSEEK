#!/usr/bin/env python
import subprocess
import json

import shlex
from subprocess import Popen, PIPE

import logging
logger = logging.getLogger(__name__)

class SeekAPI(object):
    def __init__(self, server, username, password):
        self.__server = server
        self.__username = username
        self.__password = password
            
    def __curlPrefix(self):
        if self.__username is None:
            curl_prefix = "curl -k "
        else:    
            curl_prefix = "curl -u '" + self.__username + ":" + self.__password + "' -k "
        
        return curl_prefix
            
    def apiPost(self):
        apicmd = self.__curlPrefix() + " -X POST \"" + self.__server + "\""
        return apicmd
    
    def __apiGet(self):
        apicmd = self.__curlPrefix() + " -X GET " + self.__server
        return apicmd        
            
    def __apiSilent(self):
        apicmd = self.__curlPrefix() + " -s " + self.__server
        return apicmd         
            
    def __queryRaw(self, apiquery):
        resultset = subprocess.Popen([apiquery],
            stdout=subprocess.PIPE,
            shell=True).communicate()[0].decode("utf-8")

        return resultset  
            
    def __query(self, apiquery):
        resultset = self.__queryRaw(apiquery)
        if 'Access denied' in resultset:
            resultDic = None
        else:
            resultDic = json.loads(resultset)
        
        return resultDic
    
    def callCmdline(self, cmd):
        args = shlex.split(cmd)
        proc = Popen(args, stdout=PIPE, stderr=PIPE)
        out, err = proc.communicate()
        exitcode = proc.returncode
        return exitcode, out, err
        
    def runGetQuery(self, queryurl):
        apicmd = self.__apiGet()
        suffix = " -H \"accept: application/json\""
        apiquery = (apicmd + queryurl + suffix)
        return self.__query(apiquery)
        
    def runSilentQuery(self, apiquery):
        apicmd = self.__apiSilent()
        queryi = apicmd + apiquery
        return self.__queryRaw(queryi)
    
    def getIDfromTitle(self, url, title):
        json_objects = self.runGetQuery(url)
        
        title = title.split("/")[-1]
        title = title.strip("\n")
        id = None
        for x in range(0, len(json_objects["data"])):
            if json_objects["data"][x]["attributes"]["title"] == title:
                id = json_objects["data"][x]["id"]
                
        return id
    
    
    def callFileAPI(self, apiurl, file):
        data_file_query = (
            "curl -u '" +
            self.__username + ":" + self.__password +
            "' -k -X PUT \"" + apiurl + "\" "
            "-H \"accept: */*\" -H \"Content-Type: application/octet-stream\" -T \"" +
            file + "\""
        )
        exitcode, out, err = self.callCmdline(data_file_query)
        return exitcode, out, err
        
        
    def __reviseURLs(self, htmlpage):
        htmlpage = htmlpage.replace('/assets/', (self.__server + '/assets/'))
        htmlpage = htmlpage.replace('href="/samples/', ('href="/seek/sample/id='))
        return htmlpage
        
    def __getHtmlpageDiv(self, htmlpage, div_id):
        from bs4 import BeautifulSoup
        parsed_html = BeautifulSoup(htmlpage)
        bodyhtml = parsed_html.body.find('div', attrs={'id':div_id})
        return bodyhtml.prettify()
        
    def getPageRequests(self, seekurl):
        import requests
        urlIn = self.__server + seekurl
        response = requests.get(urlIn, auth=(self.__username, self.__password), verify=False)
        htmlpage = response.text
        htmlpage = self.__reviseURLs(htmlpage)
        return self.__getHtmlpageDiv(htmlpage, 'content')
    
    
    def getCurrentUser(self):
        import requests
        req = requests.get(self.__server + "/people/current",
                           auth=(self.__username, self.__password),
                           headers = {"content-type": "application/json",
                                      "accept": "application/json"})
        return req.json()
    
    def getInvestigations(self, project_title):
        if project_title is None or project_title=="":
            apiquery = "/investigations.xml | grep -e \'investigation xlink\' | sed -n \'s/.*title=\"\\([^\"]*\\).*/\\1/p\'"
        else:
            projectid = self.getIDfromTitle("/projects/", project_title)
            apiquery = "/projects/" + projectid + ".xml | grep -e \'investigation xlink\' | sed -n \'s/.*title=\"\\([^\"]*\\).*/\\1/p\'"
        
        investigation_titles = self.runSilentQuery(apiquery)
        investigation_titles = investigation_titles.split("\n")
        investigation_titles = list(filter(None, investigation_titles))
        investigations = {}
        for it in investigation_titles:
            apiqueryi = "/investigations.xml | grep -e \'" + it + "\' | sed -n \'s/.*href=\"\\([^\"]*\\).*/\\1/p\'"
            investigation_id = self.runSilentQuery(apiqueryi)
            investigations[it] = investigation_id.strip("\n")
        return investigations
    
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
        apiquery = "/studies/" + study_id + ".xml | grep -e \'assay xlink\' | sed -n \'s/.*title=\"\\([^\"]*\\).*/\\1/p\'"
        assay_titles = self.runSilentQuery(apiquery)
        assay_titles = assay_titles.split("\n")
        assay_titles = list(filter(None, assay_titles))
        assays = {}
        for at in assay_titles:
            apiqueryi = "/studies/" + study_id + ".xml | grep -e \'" + at + "\' | sed -n \'s/.*href=\"\\([^\"]*\\).*/\\1/p\'"
            assay_id = self.runSilentQuery(apiqueryi)
            if assay_id is not None or study_id != '':
                assays[at] = assay_id.strip("\n")
            else:
                assays["0"] = "None"
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
        
