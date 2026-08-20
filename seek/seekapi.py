#!/usr/bin/env python
import subprocess
import json
from subprocess import call

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
    
    def callAPI(self, apiquery):
        try:
            call([apiquery], shell=True)
            status = True
        except:
            status = False
        return status
    
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
        
    def runGetPage(self, queryurl):
        apicmd = self.__apiGet()
        suffix = " "
        apiquery = (apicmd + queryurl + suffix)
        resultset = self.__queryRaw(apiquery)
        return resultset
        
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
    
    
    def __getPageClient(self, seekurl):
        from django.test.client import Client
        c = Client()
        loginpage = self.__server + '/login/'
        response = c.post(loginpage, {'username': self.__username, 'password': self.__password})
        urlIn = self.__server + seekurl
        response = c.get(urlIn)
        return HttpResponse(response.content)
    
    def getPageUrllib2(self, seekurl):
        import urllib2
        seekurl = 'https://api.github.com'
        req = urllib2.Request(seekurl)
        password_manager = urllib2.HTTPPasswordMgrWithDefaultRealm()
        password_manager.add_password(None, seekurl, self.__username, self.__password)

        auth_manager = urllib2.HTTPBasicAuthHandler(password_manager)
        opener = urllib2.build_opener(auth_manager)

        urllib2.install_opener(opener)

        urllib2.urlopen(req)

    def getInfoObject(self, object_url, object_id):
        objectdata = None
        if object_id<=0:
            return objectdata
        
        queryurl = object_url + str(object_id)
        jsonobject = self.runGetQuery(queryurl)
        if jsonobject is None:
            objectdata = None
        elif "data" in jsonobject:
            objectdata = jsonobject["data"]
        
        return objectdata

    def getTitleFromID(self, object_url, object_id):
        title = "undefined"
        try:
            id = int(object_id)
        except:
            return title
        
        objinfo = self.getInfoObject(object_url, id)
        if objinfo is None:
            return title
        
        if 'attributes' in objinfo:
            if 'title' in objinfo['attributes']:
                title =  objinfo['attributes']['title']
        return title

    def getCurrentUser(self):
        import requests
        req = requests.get(self.__server + "/people/current",
                           auth=(self.__username, self.__password),
                           headers = {"content-type": "application/json",
                                      "accept": "application/json"})
        return req.json()
    
    def getProjects(self):
        apiquery = "/projects.xml | grep -e \'project xlink\' | sed -n \'s/.*title=\"\\([^\"]*\\).*/\\1/p\'"
        project_titles = self.runSilentQuery(apiquery)
        project_titles = project_titles.split("\n")
        project_titles = list(filter(None, project_titles))
        projects = {}
        for it in project_titles:
            apiqueryi = "/projects.xml | grep -e \'" + it + "\' | sed -n \'s/.*href=\"\\([^\"]*\\).*/\\1/p\'"
            project_id = self.runSilentQuery(apiqueryi)
            projects[it] = project_id.strip("\n")
        return projects
    
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
    
    def getSamples(self, assayid):
        assay_id = assayid.split("/")[-1]
        apiquery = "/assays/" + assay_id + ".xml | grep -e \'sample xlink\' | sed -n \'s/.*title=\"\\([^\"]*\\).*/\\1/p\'"
        sample_titles = self.runSilentQuery(apiquery)
        sample_titles = sample_titles.split("\n")
        sample_titles = list(filter(None, sample_titles))
        samples = {}
        for st in sample_titles:
            apiqueryi = "/assays/" + assay_id + ".xml | grep -e \'" + st + "\' | sed -n \'s/.*href=\"\\([^\"]*\\).*/\\1/p\'"
            sample_id = self.runSilentQuery(apiqueryi)
            if sample_id is not None or sample_id != '':
                samples[st] = sample_id.strip("\n")
            else:
                samples["0"] = "None"
        return samples
    
    def getAssayDFs(self, assay):
        queryurl = "/assays"
        json_assays = self.runGetQuery(queryurl)
        assayid = None
        for ar in range(0, len(json_assays["data"])):
            if json_assays["data"][ar]["attributes"]["title"] in assay:
                assayid = json_assays["data"][ar]["id"]
                        
        return self.getAssayDFsFromID(assayid)
    
    def getAssayDFsFromID(self, assayid):
        results = {}
        if assayid is None or assayid==0:
            return results
        
        queryurl = "/assays/" + assayid
        json_assay = self.runGetQuery(queryurl)
        fileidlist = []
        for df in range(0, len(json_assay["data"]["relationships"]["data_files"]["data"])):
            fileidlist.append(
                json_assay["data"]["relationships"]["data_files"]["data"][df]["id"])
        for fileid in fileidlist:
            queryurl = "/data_files/" + fileid
            json_file = self.runGetQuery(queryurl)
            
            results[json_file["data"]["id"]
                ] = json_file["data"]["attributes"]["title"]
            
        return results

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
        
