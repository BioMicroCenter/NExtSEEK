#!/usr/bin/env python
from .seekapi import SeekAPI
from django.conf import settings
import os
import json
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
            mag = "No institution/lab is assigned, ask Admin for help."  # noqa: F841 (LATENT_BUGS #15)
        
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
    
    def __getNameFromID(self, objectname, id):
        if id is None or id=="":
            return ""
        
        item = "/" + objectname + "/"
        pinfo = self.getInfoObject(item, int(id))
        
        name =  pinfo['attributes']['title']
        return name
    
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
        content_blob_url = content_blob["link"]  # noqa: F841 (kept: asserts the upload produced a content blob)
        
        msg = 'okay'
        status = 1
        object_url = apiurl
        return msg, status, df_info, object_url
    
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
        
    
    def updateCreator(self, instituion_id, creator_id):
        userInfo, status, msg = self.getUserInfo(creator_id)
        self.creator = userInfo
        if 'username' not in self.creator:
            self.creator['username'] = ''
        return status, msg
        
