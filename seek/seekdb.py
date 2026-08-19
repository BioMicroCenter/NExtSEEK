#!/usr/bin/env python
from .seekapi import SeekAPI
from django.conf import settings
import hashlib
import os
import json
from pandas import json_normalize
from dmac.conversion import convertDicToOptions

import logging
logger = logging.getLogger(__name__)

class SeekDB(object):
    def __init__(self, server, username, password):
        if username is not None:
            self.user_seek = {}
            if server is None:
                self.user_seek['server'] = settings.SEEK_URL
                self.user_seek['storage'] = settings.SEEK_URL
            else:
                self.user_seek['server'] = server
                self.user_seek['storage'] = server
            self.user_seek['storagetype'] = 'SEEK'
            self.user_seek['username'] = username
            self.user_seek['password'] = password
            self.__seekapi = SeekAPI(self.user_seek['server'], username, password)
            self.getSeekLogin(None)
        else:
            self.user_seek = None
            self.__seekapi = SeekAPI(server, username, password)
            
        self.creator = None
        if self.user_seek is not None:
            self.creator = self.user_seek.copy()
        
    def __getFeatureInfo(self, userdata, featureName, defaultIndex=0):
        featureInfo = {}
        if "relationships" not in userdata:
            logger.debug('"relationships" not in userdata')
            return featureInfo
        
        relationships = userdata["relationships"]
        if featureName not in relationships:
            logger.debug('featureName not in relationships:' + featureName)
            return featureInfo
        
        featureData = relationships[featureName]["data"]
        nfeatures = len(featureData)
        if nfeatures==0 or defaultIndex>(nfeatures-1):
            logger.debug('No feature available' + featureName)
            return featureInfo
        
        if defaultIndex>=0:
            featureInfo = featureData[defaultIndex]
            fid = featureInfo['id']
            ftype = featureInfo['type']  
            furl = "/" + ftype + "/"     
            finfo = self.getInfoObject(furl, int(fid))
            featureInfo['title'] = finfo['attributes']['title']
            
            return featureInfo
        else:
            featureInfoList = []
            for featureInfo in featureData:
                fid = featureInfo['id']
                ftype = featureInfo['type'] 
                furl = "/" + ftype + "/"    
                finfo = self.getInfoObject(furl, int(fid))
                featureInfo['title'] = finfo['attributes']['title']
                
                ffinfo = {}
                ffinfo['id'] = fid
                ffinfo['title'] = featureInfo['title']
                featureInfoList.append(ffinfo)
            return featureInfoList
        
    def getUserInfo(self, user_id):
        userInfo = {}
        status = True
        msg = ''
        if int(user_id)<=0:
            msg = "User id not valid: " + str(user_id)
            status = False
            userInfo['userdata'] = None
            return userInfo, status, msg
        
        userInfo['user_id'] = user_id
        userInfo['person_id'] = user_id
        
        userdata = self.getInfoObject("/people/", user_id)
        userInfo['userdata'] = userdata
        if userdata is None:
            status = False
            userInfo['projectid'] = 0
            userInfo['institution'] = "NA"
            msg = "Password not valid"
            return userInfo, status, msg
        
        projectInfo = self.__getFeatureInfo(userdata, "projects")
        #print(projectInfo)
        if 'id' in projectInfo:
            userInfo['projectid'] = projectInfo['id']
            userInfo['projectname'] = projectInfo['title']
        else:
            userInfo['projectid'] = 0
            userInfo['projectname'] = 'NA'
            status = False
            msg = "No project is assigned, ask Admin for help."
                
        projectOptions = self.__getFeatureInfo(userdata, "projects", -1)
        userInfo['projectOptions'] = projectOptions
                
        institutionInfo = self.__getFeatureInfo(userdata, "institutions")
        if 'id' in institutionInfo:
            userInfo['institutionid'] = institutionInfo['id']
            userInfo['institutionname'] = institutionInfo['title']
            institutionname = userInfo['institutionname']
            if len(institutionname)>3:
                lababbv = institutionname[0:3]
                lababbv = lababbv.upper()
            else:
                lababbv = institutionname.upper()
            if " " in lababbv:
                lababbv = lababbv.replace(" ", "_")
            userInfo['lababbv'] = lababbv
        else:
            userInfo['institutionid'] = 0
            userInfo['institutionname'] = 'NA'
            userInfo['lababbv'] = 'NA'
            status = False
            mag = "No institution/lab is assigned, ask Admin for help."    
        
        return userInfo, status, msg
        
    def getUserFullname(self, person_id):
        fullname = self.__getNameFromID('people', person_id)
        return fullname
        
        
    def updateUserProfile(self, fullname):
        self.user_seek['status'] = True
        self.user_seek['msg'] = "Okay"
        
        person_id = self.getUserid(fullname)
        userInfo, status, msg = self.getUserInfo(person_id)
        self.user_seek.update(userInfo)
        return self.user_seek
        
        
    def getSeekLogin(self, request, whetherFullInfo=True):
        user_seek = {}
        status = True
        err = []
        if request is None:
            logger.debug("getSeekLogin from command line")
            if self.user_seek is None:
                user_seek['server'] = settings.SEEK_URL
                user_seek['storage'] = settings.SEEK_URL
                user_seek['storagetype'] = 'SEEK'
                user_seek['username'] = None
                user_seek['password'] = None
            else:
                user_seek['server'] = self.user_seek['server']
                user_seek['storage'] = self.user_seek['storage']
                user_seek['storagetype'] = self.user_seek['storagetype']
                user_seek['username'] = self.user_seek['username']
                user_seek['password'] = self.user_seek['password']
            
        elif request.method == 'POST':
            user_seek['server'] = settings.SEEK_URL
            user_seek['storage'] = settings.SEEK_URL
            user_seek['storagetype'] = 'SEEK'
            user_seek['username'] = request.POST.get('username')
            user_seek['password'] = request.POST.get('password')
            user_seek['noexpire'] = request.POST.get('no-expire')
            username = request.POST.get("user")
            if username is None and user_seek['username'] is None:
                logger.debug("getSeekLogin from Session")
                user_seek['server'] = request.session.get('server')
                user_seek['storage'] = settings.SEEK_URL
                user_seek['storagetype'] = 'SEEK'
                user_seek['username'] = request.session.get('username')
                user_seek['password'] = request.session.get('password')
        else:
            logger.debug("getSeekLogin from GET")
            user_seek['server'] = request.session.get('server')
            user_seek['storage'] = settings.SEEK_URL
            user_seek['storagetype'] = 'SEEK'
            user_seek['username'] = request.session.get('username')
            user_seek['password'] = request.session.get('password')
        
        if user_seek['username'] is None or user_seek['username']=="":
            err.append("No valid username or password")
            logger.debug("No valid username or password")
            status = False
        
        if user_seek['server']=="":
            err.append("No server selected")
            logger.debug("No server selected")
            status = False
            
        if user_seek['server']=="https://localhost":
            user_seek['server'] = settings.SEEK_URL
            
        if user_seek['password']=="":
            err.append("No valid username or password")
            logger.debug("No valid username or password")
            status = False
            
        if status:
            if request is None:
                logger.debug("SeekAPI should already be initialized")
                person_id = 0
                err.append("Person id not defined")
                status = False
                logger.debug("Person id not defined")
            else:
                self.__seekapi = SeekAPI(user_seek['server'], user_seek['username'], user_seek['password'])
                
                if whetherFullInfo:
                    person_id = self.__getSeekPersonID(user_seek['username'])
                    userInfo, status, msg = self.getUserInfo(person_id)
                    user_seek.update(userInfo)
                    if not status:
                        err.append(msg)
            
        user_seek['status'] = status
        user_seek['err'] = err
        self.user_seek = user_seek
        
        self.creator = self.user_seek.copy()
        return user_seek
    
    def getPageRequests(self, seek_url):
        bodyhtml = self.__seekapi.getPageRequests(seek_url)
        return bodyhtml

    def __getSeekUserID(self, username):
        from .models import Users
        from django.db.models import Q

        filter = Q(login__exact=username)
        userobjs = Users.objects.filter(filter).values()
        if len(userobjs)==1:
            userinfo = userobjs[0]
            seek_userid = userinfo['id']
        else:
            seek_userid = 0

        return seek_userid
        
    def __getSeekPersonID(self, username):
        from .models import Users
        from django.db.models import Q
        filter = Q(login__exact=username)
        userobjs = Users.objects.filter(filter).values()
        if len(userobjs)==1:
            userinfo = userobjs[0]
            seek_personid = userinfo['person_id']
        else:
            seek_personid = 0
        
        return seek_personid

    def getCurrentUser(self):
        return self.__seekapi.getCurrentUser()
        
    def getProjects(self):
        projects = self.__seekapi.getProjects()
        return self.__convertKeyValue(projects)

    # `useSeekAPI` is kept here on purpose: this `if` has no `else`, so a False
    # call returns None rather than falling through to a dead branch. Dropping
    # the parameter would change that. See OPTIMIZATION_PLAN.md Step 4.
    def getObjectsToOptions(self, objectName, useSeekAPI=True):
        if useSeekAPI:
            options = self.__seekapi.getObjectsToOptions(objectName)
            return options
        else:
            return None

    def getUserid(self, fullname):
        queryurl = "/people"
        jsonpeople = self.__seekapi.runGetQuery(queryurl)
        person_id = None
        for uid in range(0, len(jsonpeople["data"])):
            if jsonpeople["data"][uid]["attributes"]["title"] == fullname:
                person_id = str(jsonpeople["data"][uid]["id"])
        return person_id
    
    
    def getInfoObject(self, object_url, object_id):
        objectdata = None
        if int(object_id)<=0:
            return objectdata
        
        queryurl = object_url + str(object_id)
        jsonobject = self.__seekapi.runGetQuery(queryurl)
        if jsonobject is None:
            objectdata = None
        elif "data" in jsonobject:
            objectdata = jsonobject["data"]
        
        return objectdata
    
    def __getProjectName(self, projectid):
        if projectid is None or projectid=="":
            return ""
        
        pinfo = self.getInfoObject("/projects/", int(projectid))
        projectname =  pinfo['attributes']['title']
        return projectname
    
    def __getNameFromID(self, objectname, id):
        if id is None or id=="":
            return ""
        
        item = "/" + objectname + "/"
        pinfo = self.getInfoObject(item, int(id))
        
        name =  pinfo['attributes']['title']
        return name
    
    def getLoginUserInfo(self, user_id):
        return self.getInfoObject("/people/", user_id)
        
    def __checkPermissions(self, userid, studyid):
        sids = []
        queryurl = "/people/" + str(userid)
        jsonuser = self.__seekapi.runGetQuery(queryurl)
        studyids = jsonuser["data"]["relationships"]["studies"]["data"]
        for datanr in range(0, len(studyids)):
            sids.append(jsonuser["data"]["relationships"]["studies"]["data"][datanr]["id"])
            
        if str(studyid) in sids:
            return True
        else:
            return False
        
    def createStudy(self, userid, projectid,
                 investigationid, title, description, studyname):
        new_study_json = self.__createStudyDic(userid, projectid,investigationid, title, description)
        from seeksession import SeekSession
        session = SeekSession(self.user_seek['server'], self.user_seek['username'], self.user_seek['password'])
        study_id = session.postSeekURL("studies", new_study_json)
        return new_study_json, study_id
    
    def createAssay(self, userid, projectid, studyid,
            title, description, assay_type, technology_type, assayname):
        if self.__checkPermissions(userid, studyid):
            apiPostCmd = self.__seekapi.apiPost()       
            apiPostCmd = apiPostCmd[:-1]   
            assay_creation_query = (
                apiPostCmd + "/assays\" "
                "-H \"accept: application/json\" "
                "-H \"Content-Type: application/json\" "
                "-d \"{ \\\"data\\\": "
                "{ \\\"type\\\": \\\"assays\\\", "
                "\\\"attributes\\\": "
                "{ \\\"title\\\": \\\"" + title + "\\\", "
                "\\\"assay_class\\\": { \\\"key\\\": \\\"EXP\\\" }, "
                "\\\"assay_type\\\": { \\\"uri\\\": \\\"" + assay_type + "\\\" }, "
                "\\\"technology_type\\\": { \\\"uri\\\": \\\"" +
                technology_type + "\\\" }, "
                "\\\"description\\\": \\\"" + description + "\\\", "
                "\\\"policy\\\": "
                "{ \\\"access\\\": \\\"download\\\", "
                "\\\"permissions\\\": [ { "
                "\\\"resource\\\": "
                "{ \\\"id\\\": \\\"" + str(projectid) + "\\\", "
                "\\\"type\\\": \\\"projects\\\" }, "
                "\\\"access\\\": \\\"view\\\" } ] } }, "
                "\\\"relationships\\\": "
                "{ \\\"study\\\": "
                "{ \\\"data\\\": "
                "{ \\\"id\\\": \\\"" + str(studyid) + "\\\", "
                "\\\"type\\\": \\\"studies\\\" } }, "
                "\\\"creators\\\": "
                "{ \\\"data\\\": [ { "
                "\\\"id\\\": \\\"" + str(userid) + "\\\", "
                "\\\"type\\\": \\\"people\\\" } ] } } }}\""
            )
            logger.debug(assay_creation_query)
            return self.__seekapi.callAPI(assay_creation_query)
        else:
            logger.debug("no permission to create assay")
            return False
        
    def __convertKeyValue(self, dicIn):
        dicOut = {}
        for k,v in dicIn.items():
            id = v.split("/")[-1]
            dicOut[id] = k
        return dicOut    
                
    def getInvestigations(self, project_title):
        investigations = self.__seekapi.getInvestigations(project_title)
        return self.__convertKeyValue(investigations)

    def getStudies(self, investigation_title):
        if investigation_title is None or investigation_title=="":
            return {}

        investigationid = self.__seekapi.getIDfromTitle("/investigations/", investigation_title)
        studies = self.__seekapi.getStudies(investigationid)
        return self.__convertKeyValue(studies)

    def getStudiesFromID(self, investigation_id):
        studies = self.__seekapi.getStudies(investigation_id)
        return self.__convertKeyValue(studies)

    def getAssays(self, study_title):
        if study_title is None or study_title=="":
            return {}

        studyid = self.__seekapi.getIDfromTitle("/studies/", study_title)
        assays = self.__seekapi.getAssays(studyid)
        return self.__convertKeyValue(assays)

    def getAssaysFromID(self, study_id):
        study_id = str(study_id)
        assays = self.__seekapi.getAssays(study_id)
        return self.__convertKeyValue(assays)

    def getAPIsamples(self, assayid):
        return self.__seekapi.getSamples(assayid)

    def getSamples(self, assay_title):
        if assay_title is None or assay_title=="":
            return {}

        assayid = self.__seekapi.getIDfromTitle("/assays/", assay_title)
        samples = self.__seekapi.getSamples(assayid)
        return self.__convertKeyValue(samples)

    def __get_investigation_folders(self):
        investigations = self.getInvestigations("")
        oc_folders = ""
        inv_folders = []
        for dummyii, it in investigations.items():
            inv_folders.append(it)
        return inv_folders, oc_folders
    
    def __get_study_folders(self, investigation):
        seek_inv = self.getInvestigations("")
        inv_folders = []
        for dummyii, it in seek_inv.items():
            inv_folders.append(it)
            
        it = investigation    
        seek_study = self.getStudies(it)
        oc_folders = []
        for st, dummysi in seek_study.items():
            oc_folders.append(st)
        return oc_folders, inv_folders
    
    def get_investigations_folders(self, investigation):
        folders = []
        investigations = []
        if investigation is not None and investigation != "":
            oc_folders, inv_folders = self.__get_study_folders(investigation)
        else:
            inv_folders, oc_folders = self.__get_investigation_folders()
            
        for inv in inv_folders:
            investigation_name = inv.replace('/remote.php/webdav/', '').replace('/', '')
            if "." not in investigation_name:
                new = investigation_name
                investigations.append(new)

        for oc in oc_folders:
            study = oc.replace('/remote.php/webdav/', '')
            study = study.replace('/', '').replace(investigation, '')
            if "." not in study:
                new = study
                folders.append(new)
 
        folders = list(filter(None, folders))
        investigations = list(filter(None, investigations))
        return investigations,folders
    
    
    def investigation(self, investigationIn):
        inv_names = self.__seekapi.getInvestigations('')
        oc_folders = inv_names.keys()
        oc_list = list(filter(None, oc_folders))
        oc_studies = ""
        folders = []
        studies = []
        if oc_list:
            for oc in oc_folders:
                folders.append(oc)
                
            folders = list(filter(None, folders))
            if(investigationIn != "" and investigationIn is not None):
                oc_studies = []
                for it, dummyii in inv_names.items():
                    if it == investigationIn:
                        #studydict = self.__getStudiesAPI(it)
                        studydict = self.__seekapi.getStudies(it)
                for st, dummysi in studydict.items():
                    oc_studies.append(st)
            
            if oc_studies != "":
                oc_studies = list(filter(None, oc_studies))
                for s in oc_studies:
                    studies.append(s)
                    
                studies = list(filter(None, studies))
                return oc_list, oc_studies, folders, studies
            else:
                return oc_list, oc_studies, folders, studies

        return oc_list, oc_studies, folders, studies
    
    
    def store_results(self, column, datafiles, server, username, password, storage,
        workflowid, groups, resultid, investigations, date,
        historyid, storagetype):
        if not groups:
            groups.append('Phenomenal')
        assay_id_list = []
        o = 0
        for name in datafiles[column]:
            cont = subprocess.Popen([
                "curl -s -k " + server + datafiles[column-1][o]
            ], stdout=subprocess.PIPE, shell=True).communicate()[0].decode()
            old_name = name.replace('/', '_').replace(' ', '_')
            with open(username + "/" + old_name, "w") as outputfile:
                outputfile.write(cont)
            new_name = sha1sum(username + "/" + old_name) + "_" + old_name
            os.rename(username + "/" + old_name, username + "/" + new_name)
            o += 1
        
        studies = self.__seekapi.runGetQuery("/studies")
        for s in range(0, len(studies["data"])):
            study_name = studies["data"][s]["attributes"]["title"]
            if study_name in groups:
                studyid = studies["data"][s]["id"]
                study_title = study_name
                
                projects = self.__seekapi.runGetQuery("/projects")
                
                for p in range(1, len(projects["data"]) + 1):
                    
                    apiurl = "/projects/" + str(p)
                    project =self.__seekapi.runGetQuery(apiurl)
                    
                    for ps in range(0, len(project["data"]["relationships"]["studies"]["data"])):
                        if studyid == project["data"]["relationships"]["studies"]["data"][ps]["id"]:
                            projectid = str(p)
                if column == 1:
                    assay_title = (study_title + "__result__" + str(resultid))
                    
                    self.createAssay(
                        1, projectid, studyid, assay_title,
                        "Results for ID: " + str(resultid),
                        "http://jermontology.org/ontology/JERMOntology#Experimental_assay_type",
                        "http://jermontology.org/ontology/JERMOntology#Technology_type", assay_title
                    )
                    
                assays = self.__seekapi.runGetQuery("/assays")
                
                mime = magic.Magic(mime=True)
                content_type = mime.from_file(username + "/" + new_name)
                for ail in range(0, len(assays["data"])):
                    assay_id_list.append(int(assays["data"][ail]["id"]))
                for galaxyfile in os.listdir(username):
                    tags = []
                    self.seekupload(
                        galaxyfile,
                        username + "/" + galaxyfile,
                        str(galaxyfile), content_type, 1, projectid,
                        str(max(assay_id_list)), workflowid, tags
                    )
    
    def seekupload(self, title, file, filename,
               content_type, userid, projectid, assayid, description, tags):
        apiPostCmd = self.__seekapi.apiPost()       
        apiPostCmd = apiPostCmd[:-1]  
        if assayid is None or assayid<=0:
            data_instance_query = (
                apiPostCmd +
                "/data_files\" "
                "-H \"accept: application/json\" "
                "-H \"Content-Type: application/json\" "
                "-d \"{ \\\"data\\\": { \\\"type\\\": \\\"data_files\\\", "
                "\\\"attributes\\\": "
                "{ \\\"title\\\": \\\"" + title + "\\\", "
                "\\\"description\\\": \\\"" + description + "\\\", "
                #"\\\"tags\\\": ["
                #"\\\"" + tags[0] + "\\\", "
                #"\\\"" + tags[1] + "\\\""
                #"], "
                "\\\"license\\\": \\\"CC-BY-4.0\\\", "
                "\\\"content_blobs\\\": [ { "
                "\\\"original_filename\\\": \\\"" + filename + "\\\", "
                "\\\"content_type\\\": \\\"" + content_type + "\\\" } ], "
                "\\\"policy\\\": "
                "{ \\\"access\\\": \\\"no_access\\\", "
                "\\\"permissions\\\": [ "
                "{ \\\"resource\\\": "
                "{ \\\"id\\\": \\\"" + str(projectid) + "\\\", "
                "\\\"type\\\": \\\"projects\\\" }, "
                #"\\\"access\\\": \\\"download\\\" } ] } }, "
                "\\\"access\\\": \\\"manage\\\" } ] } }, "
                "\\\"relationships\\\": "
                "{ \\\"creators\\\": "
                "{ \\\"data\\\": [ "
                "{ \\\"id\\\": \\\"" + str(userid) + "\\\", "
                "\\\"type\\\": \\\"people\\\" } ] }, "
                "\\\"projects\\\": "
                "{ \\\"data\\\": [ "
                "{ \\\"id\\\": \\\"" + str(projectid) + "\\\", "
                "\\\"type\\\": \\\"projects\\\" } ] }"
                #", \\\"assays\\\": "
                #"{ \\\"data\\\": [ "
                #"{ \\\"id\\\": \\\"" + str(assayid) + "\\\", "
                #"\\\"type\\\": \\\"assays\\\" } ] } "
                "} }} \""
            )
        else:
            data_instance_query = (
                apiPostCmd +
                "/data_files\" "
                "-H \"accept: application/json\" "
                "-H \"Content-Type: application/json\" "
                "-d \"{ \\\"data\\\": { \\\"type\\\": \\\"data_files\\\", "
                "\\\"attributes\\\": "
                "{ \\\"title\\\": \\\"" + title + "\\\", "
                "\\\"description\\\": \\\"" + description + "\\\", "
                #"\\\"tags\\\": ["
                #"\\\"" + tags[0] + "\\\", "
                #"\\\"" + tags[1] + "\\\""
                #"], "
                "\\\"license\\\": \\\"CC-BY-4.0\\\", "
                "\\\"content_blobs\\\": [ { "
                "\\\"original_filename\\\": \\\"" + filename + "\\\", "
                "\\\"content_type\\\": \\\"" + content_type + "\\\" } ], "
                "\\\"policy\\\": "
                "{ \\\"access\\\": \\\"no_access\\\", "
                "\\\"permissions\\\": [ "
                "{ \\\"resource\\\": "
                "{ \\\"id\\\": \\\"" + str(projectid) + "\\\", "
                "\\\"type\\\": \\\"projects\\\" }, "
                #"\\\"access\\\": \\\"download\\\" } ] } }, "
                "\\\"access\\\": \\\"manage\\\" } ] } }, "
                "\\\"relationships\\\": "
                "{ \\\"creators\\\": "
                "{ \\\"data\\\": [ "
                "{ \\\"id\\\": \\\"" + str(userid) + "\\\", "
                "\\\"type\\\": \\\"people\\\" } ] }, "
                "\\\"projects\\\": "
                "{ \\\"data\\\": [ "
                "{ \\\"id\\\": \\\"" + str(projectid) + "\\\", "
                "\\\"type\\\": \\\"projects\\\" } ] }"
                ", \\\"assays\\\": "
                "{ \\\"data\\\": [ "
                "{ \\\"id\\\": \\\"" + str(assayid) + "\\\", "
                "\\\"type\\\": \\\"assays\\\" } ] } "
                "} }} \""
            )
            
        
        exitcode, out, err = self.__seekapi.callCmdline(data_instance_query)
        df_info = out
        datafile_url = None
        if exitcode==0:
            msg = 'data file uploaded successfully'
        else:
            msg = 'Error: data file not uploaded.' 
            status = 0
            return msg, status, df_info, datafile_url
        
        seek_data_ids = []  # List with data_file ids
        data_files = self.__seekapi.runGetQuery("/data_files")
        for df in range(0, len(data_files["data"])):
            seek_data_ids.append(int(data_files["data"][df]["id"]))
    
        apiurl = "/data_files/" + str(max(seek_data_ids))
        content_blob = self.__seekapi.runGetQuery(apiurl)
        content_blob_url = content_blob["data"]["attributes"]["content_blobs"][0]["link"]
        self.__seekapi.callFileAPI(content_blob_url, file)
        
        msg = 'okay'
        status = 1
        datafile_url = apiurl
        return msg, status, df_info, datafile_url
    
    def seekupload_url(self, title, file, filename,
               content_type, userid, projectid, assayid, description, tags):
        apiPostCmd = self.__seekapi.apiPost()       
        apiPostCmd = apiPostCmd[:-1] 
        data_instance_query = (
            apiPostCmd +
            "/data_files\" "
            "-H \"accept: application/json\" "
            "-H \"Content-Type: application/json\" "
            "-d \"{ \\\"data\\\": { \\\"type\\\": \\\"data_files\\\", "
            "\\\"attributes\\\": "
            "{ \\\"title\\\": \\\"" + title + "\\\", "
            "\\\"description\\\": \\\"" + description + "\\\", "
            #"\\\"tags\\\": ["
            #"\\\"" + tags[0] + "\\\", "
            #"\\\"" + tags[1] + "\\\""
            #"], "
            "\\\"license\\\": \\\"CC-BY-4.0\\\", "
            "\\\"content_blobs\\\": [ { "
            "\\\"original_filename\\\": \\\"" + filename + "\\\", "
            "\\\"content_type\\\": \\\"" + content_type + "\\\" } ], "
            "\\\"policy\\\": "
            #"{ \\\"access\\\": \\\"download\\\", "
            "{ \\\"access\\\": \\\"no_access\\\", "
            "\\\"permissions\\\": [ "
            "{ \\\"resource\\\": "
            "{ \\\"id\\\": \\\"" + str(projectid) + "\\\", "
            "\\\"type\\\": \\\"projects\\\" }, "
            #"\\\"access\\\": \\\"edit\\\" } ] } }, "
            "\\\"access\\\": \\\"manage\\\" } ] } }, "
            "\\\"relationships\\\": "
            "{ \\\"creators\\\": "
            "{ \\\"data\\\": [ "
            "{ \\\"id\\\": \\\"" + str(userid) + "\\\", "
            "\\\"type\\\": \\\"people\\\" } ] }, "
            "\\\"projects\\\": "
            "{ \\\"data\\\": [ "
            "{ \\\"id\\\": \\\"" + str(projectid) + "\\\", "
            "\\\"type\\\": \\\"projects\\\" } ] }, "
            "\\\"assays\\\": "
            "{ \\\"data\\\": [ "
            "{ \\\"id\\\": \\\"" + str(assayid) + "\\\", "
            "\\\"type\\\": \\\"assays\\\" } ] } } }} \""
        )
        
        exitcode, out, err = self.__seekapi.callCmdline(data_instance_query)
        df_info = out
        if exitcode==0:
            msg = 'data file uploaded successfully'
        else:
            msg = 'Error: data file not uploaded.' 
            status = 0
            return msg, status, df_info
        
        seek_data_ids = []  # List with data_file ids
        data_files = self.__seekapi.runGetQuery("/data_files")
        for df in range(0, len(data_files["data"])):
            seek_data_ids.append(int(data_files["data"][df]["id"]))
    
        apiurl = "/data_files/" + str(max(seek_data_ids))
        content_blob = self.__seekapi.runGetQuery(apiurl)
        content_blob_url = content_blob["data"]["attributes"]["content_blobs"][0]["link"]
        self.__seekapi.callFileAPI(content_blob_url, file)
        
        msg = 'okay'
        status = 1
        return msg, status, df_info


    def getPage(self, queryurl):
        webpage = self.__seekapi.runGetPage(queryurl)
        return webpage
    
    def handle_uploaded_file_seek(self, infile, outfilename):
        dest = open(outfilename, 'wb')
        for chunk in infile.chunks():
            dest.write(chunk)
        dest.close()

    def uploadFile_intoSeek(self, request, username, userid, project_id, assay_id, tags):
        infile = request.FILES['file']
        content_type = infile.content_type
        outfilename, upload_full_path = self.__defineUploadPath(username, infile.name)
        self.handle_uploaded_file_seek(infile, outfilename)
        datatitle = 'testdatafile'
        description = 'test data file uploading'
        
        return 
        self.seekupload(
            datatitle,
            outfilename,
            infile.name,
            content_type,
            userid,
            project_id,
            assay_id,
            description,
            tags
        )
        call(["rm", "-r", outfilename])
        call(["rm", "-r", upload_full_path])
        
    def __defineUploadPath(self, username, infilename):
        upload_dir = (
            "tmp" +
            hashlib.md5(username.encode('utf-8')).hexdigest()
        )
        
        upload_full_path = os.path.join(settings.MEDIA_ROOT, upload_dir)
        if not os.path.exists(upload_full_path):
            os.makedirs(upload_full_path)
    
        outfilename = os.path.join(upload_full_path, infilename)
        while os.path.exists(outfilename):
            infilename = '_' + infilename
            outfilename = os.path.join(upload_full_path, infilename)
            
        return outfilename, upload_full_path
        
        
    def testCreateStudy(self, user_seek):
        userid = user_seek['user_id']
        projectid = user_seek['projectid']
        investigationid = 1
        title = 'testStudy2'
        description = 'try testStudy2'
        studyname = 'testStudy 2'
        return self.createStudy(userid, projectid,investigationid, title, description, studyname)
    
    def testDeleteStudy(self, user_seek, studyid):
        logger.debug('deleting study to be implemented')
        
    def testCreateAssay(self, user_seek):
        userid = user_seek['user_id']
        projectid = user_seek['projectid']
        
        studyid = 7
        title = 'testAssay2'
        description = 'test testAssay2' 
        assayname = 'testAssay2'
        assay_type = 'http://jermontology.org/ontology/JERMOntology#DNA_sequencing'
        technology_type = 'http://jermontology.org/ontology/JERMOntology#RNA-Seq'
        
        return self.createAssay(userid, projectid, studyid,
            title, description, assay_type, technology_type, assayname)
    
    
    def testSeekupload(self, user_seek):
        userid = user_seek['user_id']
        projectid = user_seek['projectid']
        
        studyid = 7
        title = 'testAssay2'
        description = 'test testAssay2' 
        assayname = 'testAssay2'
        assay_type = 'http://jermontology.org/ontology/JERMOntology#DNA_sequencing'
        technology_type = 'http://jermontology.org/ontology/JERMOntology#RNA-Seq'
        return self.createAssay(userid, projectid, studyid,
            title, description, assay_type, technology_type, assayname)
        
    def seekuploadSOP(self, title, fullfilename, originalfilename,
               content_type, creator_id, projectid, assayid, description, tags, other_creators=''):
        logger.debug("Running seekuploadSOP")
        if not os.path.exists(fullfilename):
            msg = "Error: file not available: ", fullfilename 
            status = 0
            return msg, status, None, None

        test = {"title": title,
                "fullfilename": fullfilename,
                "originalfilename": originalfilename,
                "creator_id": creator_id,
                "content_type": content_type,
                "projectid": projectid,
                "assayid": assayid,
                "description": description,
                "tags": tags}
        logger.debug(f"Uploading: {test}")
        
        apiPostCmd = self.__seekapi.apiPost()       
        apiPostCmd = apiPostCmd[:-1] 
        if assayid is None or assayid<=0:
            data_instance_query = (
                apiPostCmd +
                "/sops\" "
                "-H \"accept: application/json\" "
                "-H \"Content-Type: application/json\" "
                "-d \"{ \\\"data\\\": { \\\"type\\\": \\\"sops\\\", "
                "\\\"attributes\\\": "
                "{ \\\"title\\\": \\\"" + title + "\\\", "
                "\\\"description\\\": \\\"" + description + "\\\", "
                #"\\\"tags\\\": ["
                #"\\\"" + tags[0] + "\\\", "
                #"\\\"" + tags[1] + "\\\""
                #"], "
                "\\\"license\\\": \\\"CC-BY-4.0\\\", "
                #"\\\"other_creators\\\": \\\"John Smith\\\", "
                "\\\"other_creators\\\": \\\"" + other_creators + "\\\", "
                "\\\"content_blobs\\\": [ { "
                "\\\"original_filename\\\": \\\"" + originalfilename + "\\\", "
                "\\\"content_type\\\": \\\"" + content_type + "\\\" } ], "
                "\\\"policy\\\": "
                "{ \\\"access\\\": \\\"no_access\\\", "
                "\\\"permissions\\\": [ "
                "{ \\\"resource\\\": "
                "{ \\\"id\\\": \\\"" + str(projectid) + "\\\", "
                "\\\"type\\\": \\\"projects\\\" }, "
                #"\\\"access\\\": \\\"download\\\" } ] } }, "
                "\\\"access\\\": \\\"manage\\\" } ] } }, "
                "\\\"relationships\\\": "
                "{ \\\"creators\\\": "
                "{ \\\"data\\\": [ "
                "{ \\\"id\\\": \\\"" + str(creator_id) + "\\\", "
                "\\\"type\\\": \\\"people\\\" } ] }, "
                "\\\"projects\\\": "
                "{ \\\"data\\\": [ "
                "{ \\\"id\\\": \\\"" + str(projectid) + "\\\", "
                "\\\"type\\\": \\\"projects\\\" } ] }"
                #", \\\"assays\\\": "
                #"{ \\\"data\\\": [ "
                #"{ \\\"id\\\": \\\"" + str(assayid) + "\\\", "
                #"\\\"type\\\": \\\"assays\\\" } ] } "
                "} }} \""
            )
        else:
            data_instance_query = (
                apiPostCmd +
                "/sops\" "
                "-H \"accept: application/json\" "
                "-H \"Content-Type: application/json\" "
                "-d \"{ \\\"data\\\": { \\\"type\\\": \\\"sops\\\", "
                "\\\"attributes\\\": "
                "{ \\\"title\\\": \\\"" + title + "\\\", "
                "\\\"description\\\": \\\"" + description + "\\\", "
                #"\\\"tags\\\": ["
                #"\\\"" + tags[0] + "\\\", "
                #"\\\"" + tags[1] + "\\\""
                #"], "
                "\\\"license\\\": \\\"CC-BY-4.0\\\", "
                #"\\\"other_creators\\\": \\\"John Smith\\\", "
                "\\\"other_creators\\\": \\\"" + other_creators + "\\\", "
                "\\\"content_blobs\\\": [ { "
                "\\\"original_filename\\\": \\\"" + originalfilename + "\\\", "
                "\\\"content_type\\\": \\\"" + content_type + "\\\" } ], "
                "\\\"policy\\\": "
                "{ \\\"access\\\": \\\"no_access\\\", "
                "\\\"permissions\\\": [ "
                "{ \\\"resource\\\": "
                "{ \\\"id\\\": \\\"" + str(projectid) + "\\\", "
                "\\\"type\\\": \\\"projects\\\" }, "
                #"\\\"access\\\": \\\"download\\\" } ] } }, "
                "\\\"access\\\": \\\"manage\\\" } ] } }, "
                "\\\"relationships\\\": "
                "{ \\\"creators\\\": "
                "{ \\\"data\\\": [ "
                "{ \\\"id\\\": \\\"" + str(creator_id) + "\\\", "
                "\\\"type\\\": \\\"people\\\" } ] }, "
                "\\\"projects\\\": "
                "{ \\\"data\\\": [ "
                "{ \\\"id\\\": \\\"" + str(projectid) + "\\\", "
                "\\\"type\\\": \\\"projects\\\" } ] }"
                ", \\\"assays\\\": "
                "{ \\\"data\\\": [ "
                "{ \\\"id\\\": \\\"" + str(assayid) + "\\\", "
                "\\\"type\\\": \\\"assays\\\" } ] } "
                "} }} \""
            )
        exitcode, out, err = self.__seekapi.callCmdline(data_instance_query)

        logger.debug(f"API call exitcode: {exitcode}")
        logger.debug(f"API call out: {out}")
        logger.debug(f"API call err: {err}")

        object_info = out
        object_url = None
        if exitcode==0:
            msg = 'SOP uploaded successfully'
        else:
            msg = 'Error: SOP not uploaded.' 
            status = 0
            return msg, status, object_info, object_url
        
        seek_data_ids = []  # List with data_file ids
        sops = self.__seekapi.runGetQuery("/sops")
        for df in range(0, len(sops["data"])):
            seek_data_ids.append(int(sops["data"][df]["id"]))
    
        apiurl = "/sops/" + str(max(seek_data_ids))
        content_blob = self.__seekapi.runGetQuery(apiurl)
        content_blob_url = content_blob["data"]["attributes"]["content_blobs"][0]["link"]
        self.__seekapi.callFileAPI(content_blob_url, fullfilename)
        
        msg = 'okay'
        status = 1
        object_url = apiurl
        return msg, status, object_info, object_url
    
    
    def seekupload_dfurl(self, title, fullfilename, originalfilename,
               content_type, userid, projectid, assayid, description, tags, weburl):
        apiPostCmd = self.__seekapi.apiPost()       
        apiPostCmd = apiPostCmd[:-1]   
        if assayid is None or assayid<=0:
            data_instance_query = (
                apiPostCmd +
                "/data_files\" "
                "-H \"accept: application/json\" "
                "-H \"Content-Type: application/json\" "
                "-d \"{ \\\"data\\\": { \\\"type\\\": \\\"data_files\\\", "
                "\\\"attributes\\\": "
                "{ \\\"title\\\": \\\"" + title + "\\\", "
                "\\\"description\\\": \\\"" + description + "\\\", "
                #"\\\"tags\\\": ["
                #"\\\"" + tags[0] + "\\\", "
                #"\\\"" + tags[1] + "\\\""
                #"], "
                "\\\"license\\\": \\\"CC-BY-4.0\\\", "
                "\\\"content_blobs\\\": [ { "
                "\\\"original_filename\\\": \\\"" + originalfilename + "\\\", "
            
                # If you add the following line into the call,
                # an error message will show:  {"error":"bad upload"}
                # refer to: http://www.visualseq.net/dokuwiki/doku.php?id=computer:websites:dmac:datafile#step_1_select_an_example
                #"\\\"url\\\": \\\"" + weburl + "\\\", "
                #"\\\"md5sum\\\": \\\"" + md5sum + "\\\", "
                #"\\\"sha1sum\\\": \\\"" + sha1sum + "\\\", "
                #"\\\"link\\\": \\\"" + link + "\\\", "
                #"\\\"size\\\": \\\"" + size + "\\\", "
            
                "\\\"content_type\\\": \\\"" + content_type + "\\\" } ], "
                "\\\"policy\\\": "
                "{ \\\"access\\\": \\\"no_access\\\", "
                "\\\"permissions\\\": [ "
                "{ \\\"resource\\\": "
                "{ \\\"id\\\": \\\"" + str(projectid) + "\\\", "
                "\\\"type\\\": \\\"projects\\\" }, "
                #"\\\"access\\\": \\\"download\\\" } ] } }, "
                "\\\"access\\\": \\\"manage\\\" } ] } }, "
                "\\\"relationships\\\": "
                "{ \\\"creators\\\": "
                "{ \\\"data\\\": [ "
                "{ \\\"id\\\": \\\"" + str(userid) + "\\\", "
                "\\\"type\\\": \\\"people\\\" } ] }, "
                "\\\"projects\\\": "
                "{ \\\"data\\\": [ "
                "{ \\\"id\\\": \\\"" + str(projectid) + "\\\", "
                "\\\"type\\\": \\\"projects\\\" } ] } "
                #"\\\"type\\\": \\\"projects\\\" } ] }, "
                #",\\\"assays\\\": "
                #"{ \\\"data\\\": [ "
                #"{ \\\"id\\\": \\\"" + str(assayid) + "\\\", "
                #"\\\"type\\\": \\\"assays\\\" } ] } } }} \""
                "} }} \""
            )
        else:
            data_instance_query = (
                apiPostCmd +
                "/data_files\" "
                "-H \"accept: application/json\" "
                "-H \"Content-Type: application/json\" "
                "-d \"{ \\\"data\\\": { \\\"type\\\": \\\"data_files\\\", "
                "\\\"attributes\\\": "
                "{ \\\"title\\\": \\\"" + title + "\\\", "
                "\\\"description\\\": \\\"" + description + "\\\", "
                #"\\\"tags\\\": ["
                #"\\\"" + tags[0] + "\\\", "
                #"\\\"" + tags[1] + "\\\""
                #"], "
                "\\\"license\\\": \\\"CC-BY-4.0\\\", "
                "\\\"content_blobs\\\": [ { "
                "\\\"original_filename\\\": \\\"" + originalfilename + "\\\", "
            
                # If you add the following line into the call,
                # an error message will show:  {"error":"bad upload"}
                # refer to: http://www.visualseq.net/dokuwiki/doku.php?id=computer:websites:dmac:datafile#step_1_select_an_example
                #"\\\"url\\\": \\\"" + weburl + "\\\", "
                #"\\\"md5sum\\\": \\\"" + md5sum + "\\\", "
                #"\\\"sha1sum\\\": \\\"" + sha1sum + "\\\", "
                #"\\\"link\\\": \\\"" + link + "\\\", "
                #"\\\"size\\\": \\\"" + size + "\\\", "
            
                "\\\"content_type\\\": \\\"" + content_type + "\\\" } ], "
                "\\\"policy\\\": "
                "{ \\\"access\\\": \\\"no_access\\\", "
                "\\\"permissions\\\": [ "
                "{ \\\"resource\\\": "
                "{ \\\"id\\\": \\\"" + str(projectid) + "\\\", "
                "\\\"type\\\": \\\"projects\\\" }, "
                #"\\\"access\\\": \\\"download\\\" } ] } }, "
                "\\\"access\\\": \\\"manage\\\" } ] } }, "
                "\\\"relationships\\\": "
                "{ \\\"creators\\\": "
                "{ \\\"data\\\": [ "
                "{ \\\"id\\\": \\\"" + str(userid) + "\\\", "
                "\\\"type\\\": \\\"people\\\" } ] }, "
                "\\\"projects\\\": "
                "{ \\\"data\\\": [ "
                "{ \\\"id\\\": \\\"" + str(projectid) + "\\\", "
                "\\\"type\\\": \\\"projects\\\" } ] } "
                #"\\\"type\\\": \\\"projects\\\" } ] }, "
                ",\\\"assays\\\": "
                "{ \\\"data\\\": [ "
                "{ \\\"id\\\": \\\"" + str(assayid) + "\\\", "
                "\\\"type\\\": \\\"assays\\\" } ] } "
                "} }} \""
            )
        exitcode, out, err = self.__seekapi.callCmdline(data_instance_query)
        
        df_info = out
        if exitcode==0:
            msg = 'data file uploaded successfully'
        else:
            msg = 'Error: data file not uploaded.'
            status = 0
            return msg, status, df_info
        
        data_files = self.__seekapi.runGetQuery("/data_files")
        df_id = 0
        for df in range(0, len(data_files["data"])):
            ti = data_files["data"][df]["attributes"]["title"]
            if ti==title:
                df_id = int(data_files["data"][df]["id"])
            
        if df_id==0:
            msg = "Error: data file not found in DB:" + title
            return msg, 0, None, None
    
        apiurl = "/data_files/" + str(df_id)
        df_dic = self.__seekapi.runGetQuery(apiurl)
        df_info = df_dic["data"]
        content_blob = df_info["attributes"]["content_blobs"][0]
        content_blob_url = content_blob["link"]
        
        msg = 'okay'
        status = 1
        object_url = apiurl
        return msg, status, df_info, object_url
    
    def getAssayDFs(self, assayid, investigation, study, assay):
        resultDic = self.__seekapi.getAssayDFsFromID(assayid)
        dicList = []
        for k,v in resultDic.items():
            dici = {}
            dici['id'] = k
            dici['title'] = v
            dici['investigation'] = investigation
            dici['study'] = study
            dici['assay'] = assay
            dici['selected'] = '1'
            dicList.append(dici)
        
        return dicList
    
    def getISAOptions(self):
        projects = [{ 'id': "0", 'title': " " }]
        if 'projectOptions' in self.user_seek:
            projects = self.user_seek['projectOptions']
        project_options = json.dumps(projects)
        allinvestigations = {}
        investigation_options_dic = {}
        for pinfo in projects:
            project_title = pinfo['title']
            project_id = pinfo['id']
            investigations = self.getInvestigations(project_title)
            allinvestigations.update(investigations)
            
            investigation_options = convertDicToOptions(investigations)
            investigation_options_dic[project_id] = investigation_options
            
        investigation_options_dic = json.dumps(investigation_options_dic) 
    
        study_options_dic = {}
        allstudies = {}
        for iid, investigation in allinvestigations.items():
            studies = self.getStudies(investigation)
            study_options = convertDicToOptions(studies)
            study_options_dic[iid] = study_options
        
            for sid, study in studies.items():
                if sid not in allstudies:
                    allstudies[sid] = study
            

        study_options_dic = json.dumps(study_options_dic)
        assay_options_dic = {}
        for sid, study in allstudies.items():
            assays = self.getAssays(study)
            assay_options = convertDicToOptions(assays)
            assay_options_dic[sid] = assay_options
            
        assay_options_dic = json.dumps(assay_options_dic)
        return project_options, investigation_options_dic, study_options_dic, assay_options_dic
        
    
    def __formatJsonDataRelationships(self, input_data):
        files = []
        source_relationships = input_data['relationships']
    
        for dtype in source_relationships:
            source_dtype_entry = source_relationships[str(dtype)]['data']
        
            source_data_type = 'none'
            source_data_id = 'none'       
        
            if(dtype=='investigation' or dtype=='study'):#formated differently
                item = source_dtype_entry
                source_data_type = item['type']
                source_data_id = item['id']
            
                files.append({
                    'type':source_data_type, #j['data']['type'],
                    'id':source_data_id, #j['data']['id'],
                })

            else:
                for item in source_dtype_entry:
                    source_data_id = item['id']
                    source_data_type = item['type']
                    if(item['type']=='people'): #instead of type 'people', passing submitter, creators etc.
                        source_data_type = str(dtype)
                    
                    files.append({
                        'type':source_data_type,
                        'id':source_data_id, #j['data']['id'],
                    })
        return files
        
        
    def __formatJsonDataRelationshipsTitle(self, input_data):
        files = []
        source_relationships = input_data['relationships']
    
        for dtype in source_relationships:
            source_dtype_entry = source_relationships[str(dtype)]['data']
            source_data_type = 'none'
            source_data_id = 'none'    
            if(dtype=='investigation' or dtype=='study'):#formated differently
                item = source_dtype_entry
                source_data_type = item['type']
                source_data_id = item['id']
            
                title = self.__getNameFromID(item['type'],item['id'])
            
                files.append({
                    'type':source_data_type, 
                    'id':source_data_id,     
                    'title': title
                })

            else:
                for item in source_dtype_entry:
                    source_data_id = item['id']
                    source_data_type = item['type']
                    if(item['type']=='people'): 
                        source_data_type = str(dtype)
                
                    title = self.__getNameFromID(item['type'],item['id'])
                    files.append({
                        'type':source_data_type,
                        'id':source_data_id, 
                        'title':title   
                    })
                
        return files  

    def __determineISAstructureFromRelationships(self, data_type, input_relationships):
        structure = ['projects', 'investigations', 'studies', 'assays', 'data_files'] # investigation, study
        structure_up = []
        structure_down = []
        structure_people = ['creators', 'submitter', 'people'] # creator?
    
        isa_structure_up = []
        isa_structure_down = []  
        isa_structure_people = []    
    
        if(data_type == 'projects'):
            structure_up = [] 
            structure_down = ['investigations', 'studies', 'assays', 'data_files'] 
        if(data_type == 'investigations'):
            structure_up = ['projects'] 
            structure_down = ['studies', 'assays', 'data_files']         
        if(data_type == 'studies'):
            structure_up = ['projects', 'investigations'] # investigation
            structure_down = ['assays', 'data_files'] 
        if(data_type == 'assays'):
            structure_up = ['projects', 'investigations', 'studies'] # investigation, study
            structure_down = ['data_files'] 
        if(data_type == 'data_files'):
            structure_up = ['projects', 'investigations', 'studies', 'assays']
            structure_down = []   
    
        for item in input_relationships:
            for y in range(0, len(structure_up)):
                if(item['type']==structure_up[y]):
                    isa_structure_up.append(item)  
            for y in range(0, len(structure_down)):
                if(item['type']==structure_down[y]):
                    isa_structure_down.append(item) 
            for y in range(0, len(structure_people)):
                if(item['type']==structure_people[y]):
                    isa_structure_people.append(item)              
             
        if(len(isa_structure_up)>0):
            logger.debug("ISA STRUCTURE UP:");logger.debug(json_normalize(isa_structure_up));logger.debug()
        if(len(isa_structure_down)>0):        
            logger.debug("ISA STRUCTURE DOWN:");logger.debug(json_normalize(isa_structure_down));logger.debug()
        if(len(isa_structure_people)>0): 
            logger.debug("ISA STRUCTURE PEOPLE:");logger.debug(json_normalize(isa_structure_people));logger.debug()
        
        return isa_structure_up, isa_structure_down, isa_structure_people    
      
    def readJsonData(self, object_id, object_type):
        object_url = "/" + object_type + "/"
        source_json= self.getInfoObject(object_url, object_id)
        return source_json
      
    def __getDISA(self, object_id, object_type): 
        source_json = self.readJsonData(object_id, object_type)
        source_relationships =  self.__formatJsonDataRelationshipsTitle(source_json)
        isas = self.__determineISAstructureFromRelationships(object_type, source_relationships)
        return isas[1]
        
    def __getFullDISA(self, source_json_id, source_data_type):
        out = self.__getDISA(source_json_id, source_data_type)
        isa_struct = []
        isa_struct.append([{'type': source_data_type, 'id': source_json_id}, out])
        for entry_num in range(0, len(out)):
            out_temp = self.__getDISA(out[entry_num]['id'], out[entry_num]['type'])
            if(len(out_temp)>0):#if there is a DISA
                isa_struct.append([out[entry_num], out_temp])
            else:
                out_temp = [];
                isa_struct.append([out[entry_num], out_temp])
            
        
    def downloadAssets(self, object_id, object_type):
        source_json = self.readJsonData(object_id, object_type)
        isa_structure = self.__getFullDISA(object_id, object_type)
        return source_json, isa_structure
    
    def __createStudyDic(self, userid, projectid, investigationid, study_title, study_description):
        new_study_json = {}
        new_study_json['data'] = {}
        new_study_json['data']['type'] = 'studies'
        new_study_json['data']['attributes'] = {}
        new_study_json['data']['attributes']['title'] = study_title
        new_study_json['data']['attributes']['description'] = study_description
        new_study_json['data']['attributes']['policy'] = {'access':'no_access'}
        new_study_json['data']['attributes']['policy']['permissions'] = [{'resource':{'id':projectid,'type':'projects'},'access':'download'}];
        new_study_json['data']['relationships'] = {}
        new_study_json['data']['relationships']['creators'] = {}
        new_study_json['data']['relationships']['creators']['data'] = [{'id' : userid, 'type' : 'people'}]
        new_study_json['data']['relationships']['investigation'] = {}
        new_study_json['data']['relationships']['investigation']['data'] = {'id' : investigationid, 'type' : 'investigations'}
        new_study_json['data']['relationships']['projects'] = {}
        new_study_json['data']['relationships']['projects']['data'] = {'id' : projectid, 'type' : 'projects'}
        return new_study_json
    
    def registerStudy(self, in_study_json, target_project_id, target_investigation_id, target_creator_id):
        study_title = in_study_json['attributes']['title']
        study_description = in_study_json['attributes']['description']
        new_study_json, study_id = self.createStudy(target_creator_id, target_project_id, target_investigation_id,
            study_title, study_description, "")
        return new_study_json, study_id
        
    def createAssayDic(self, userid, projectid, investigationid, studyid,
            title, description, assay_type, technology_type, assay_class):
        new_assay_json = {}
        new_assay_json['data'] = {}
        new_assay_json['data']['type'] = 'assays'

        new_assay_json['data']['attributes'] = {}
        new_assay_json['data']['attributes']['title'] = title
        new_assay_json['data']['attributes']['description'] = description

        new_assay_json['data']['attributes']['policy'] = {'access':'no_access'}
        new_assay_json['data']['attributes']['policy']['permissions'] = [{'resource':{'id':projectid,'type':'projects'},'access':'download'}];

        new_assay_json['data']['attributes']['assay_class'] = assay_class
        new_assay_json['data']['attributes']['assay_type'] = assay_type
        new_assay_json['data']['attributes']['technology_type'] = technology_type

        new_assay_json['data']['relationships'] = {}
        new_assay_json['data']['relationships']['creators'] = {}
        new_assay_json['data']['relationships']['creators']['data'] = [{'id' : userid, 'type' : 'people'}]
        new_assay_json['data']['relationships']['study'] = {}
        new_assay_json['data']['relationships']['study']['data'] = {'id' : studyid, 'type' : 'studies'}
        new_assay_json['data']['relationships']['investigation'] = {}
        new_assay_json['data']['relationships']['investigation']['data'] = {'id' : investigationid, 'type' : 'investigations'}
        new_assay_json['data']['relationships']['projects'] = {}
        new_assay_json['data']['relationships']['projects']['data'] = {'id' : projectid, 'type' : 'projects'}
        return new_assay_json


    def registerAssay(self, in_assay_json, target_project_id, target_investigation_id, target_study_id, target_creator_id):
        title = in_assay_json['attributes']['title']
        description = in_assay_json['attributes']['description']
        assay_type = in_assay_json['attributes']['assay_type']
        technology_type = in_assay_json['attributes']['technology_type']
        assay_class = in_assay_json['attributes']['assay_class']
        new_assay_json = self.createAssayDic(target_creator_id, target_project_id, target_investigation_id, target_study_id,
            title, description, assay_type, technology_type, assay_class)
        
        from seeksession import SeekSession
        session = SeekSession(self.user_seek['server'], self.user_seek['username'], self.user_seek['password'])
        id = session.postSeekURL("assays", new_assay_json)
        return new_assay_json, id


    def registerAndCopyStudyAndBelow(self, source_seekdb, source_json, isa_structure, #source_json, 
                target_project_id, target_investigation_id, target_creator_id):
        out_json = self.registerStudy(source_json, target_project_id, target_investigation_id, target_creator_id)#out_json[0] - json   ;  #out_json[1] - id
        target_study_id = out_json[1]
        for x in range(0, len(isa_structure)):
            if(len(isa_structure)>1):
                if(isa_structure[x][0]['type']=='assays'):
                    assay_json = source_seekdb.readJsonData(isa_structure[x][0]['id'], isa_structure[x][0]['type'])
                    target_assay_id = self.registerAssay(assay_json, target_project_id, target_investigation_id, target_study_id, target_creator_id)
                    if(len(isa_structure[x][1])>0):
                        for y in range(0, len(isa_structure[x][1])):
                            if(isa_structure[x][1][y]['type']=='data_files'):

                                dataRead = readBlobData(session1, headers1, headers2, source_base_url, isa_structure[x][1][y]['id'], 'data_files')
                                dataBinary = dataRead[7]
                                target_filetitle = dataRead[1]
                                target_filelicense = dataRead[4]
                                target_filename = dataRead[2]
                                target_filetype = dataRead[3]
                                target_blob = {'original_filename' : target_filename, 'content_type' : target_filetype}           
                   
                                target_data_file  = registerBlobData(session2, target_base_url, 'data_files', target_filetitle, target_filelicense, target_blob, 
                                                                 target_project_id, target_investigation_id, target_study_id, target_assay_id, target_creator_id)    
                                target_data_file_id = target_data_file[0]
                                target_data_file_link = target_data_file[1]
                   
                                uploadBlobData(session2, headers1, headers3, target_base_url, 'data_files', target_data_file_id, target_data_file_link, dataBinary)

        
    def uploadAssets(self, source_seekdb, source_json, isa_structure):
        target_project_id = 2 
        target_project_type = 'projects'

        target_investigation_id = 4 
        target_investigation_data_type = 'investigatons'

        target_creator_id = 1      
        self.registerAndCopyStudyAndBelow(source_seekdb, source_json, isa_structure, 
                target_project_id, target_investigation_id, target_creator_id)
        
    def reviseOwnership(self, username):
        person_id = self.__getSeekPersonID(username)
        userInfo, status, msg = self.getUserInfo(person_id)
        self.user_seek.update(userInfo)
       
    def updateCreator(self, instituion_id, creator_id):
        userInfo, status, msg = self.getUserInfo(creator_id)
        self.creator = userInfo
        if 'username' not in self.creator:
            self.creator['username'] = ''
        return status, msg
        
