#!/usr/bin/env python
from .seekapi import SeekAPI
from django.conf import settings
import json
import shlex
from dmac.conversion import convertDicToOptions

import logging
logger = logging.getLogger(__name__)

class SeekDB(object):
    def __init__(self, server, username, password, token_provider=None):
        """
        ``token_provider`` (#16, sub-project 2) builds a credentialed SeekDB for
        a caller who has no username/password -- an OAuth session. It matters
        because ``SeekDB(None, None, None)`` takes the branch below, which never
        calls ``getSeekLogin`` and so leaves ``SeekAPI.__server`` as None; any
        method that builds a URL from it then fails. Callers needing to *call*
        SEEK as the user (rather than resolve credentials from a request) have
        therefore always had to pass real credentials -- see
        ``nextseek_api/views.py:105`` -- and this is the OAuth equivalent.
        """
        if username is not None or token_provider is not None:
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
            self.user_seek['token_provider'] = token_provider
            self.__seekapi = SeekAPI(self.user_seek['server'], username, password,
                                     token_provider=token_provider)
            if username is not None:
                # Unchanged for the credentialed path. Not called for a
                # token-only caller: it resolves a person id from the username,
                # which there is not one of here.
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
        
        
    def __oauthTokenProvider(self, request):
        """A callable yielding a fresh SEEK access token for `request`, or None.

        Resolving once to decide whether the credential exists means two
        resolutions on first use, each a short transaction taking a row lock.
        Accepted knowingly: the cheaper alternative is an existence check on the
        token row, but a row can exist while its refresh token is dead, and that
        variant would report a successful login and then surface a 401 from SEEK
        partway through a page. Failing at the login is worth an indexed read.
        """
        # Imported here, not at module scope: seek.oauth.service imports the
        # models, which import back through this package.
        from seek.oauth.service import token_provider_for_request

        return token_provider_for_request(request)

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

        # The OAuth branch (#16, sub-project 2). A session established by "Log
        # in with SEEK" carries a username and deliberately no password, so
        # everything below has to be satisfied by a token instead.
        #
        # This resolves a *provider* rather than a token: the callable is handed
        # to SeekAPI and invoked per call, so a long-lived SeekDB cannot pin a
        # token that has since expired. Resolving one here and now also proves
        # the user actually has a usable token, which is what lets the password
        # check below be skipped rather than merely bypassed.
        user_seek['token_provider'] = None
        if request is not None and user_seek['password'] in (None, ""):
            user_seek['token_provider'] = self.__oauthTokenProvider(request)

        # `is None` matters as much as `== ""`, and only one of them was here.
        # An OAuth session carries a username but no password by construction
        # (seek/oauth/views.py writes the former and not the latter), and a None
        # password used to pass this check.
        #
        # It used to then reach SeekAPI(server, username, None) and raise
        # TypeError inside __curlPrefix, which guarded on the username alone and
        # concatenated str + None. Sub-project 2 rewrote that method, so the
        # TypeError is gone -- but the guard still earns its place: without it a
        # password-less session produces a credential-less request that travels
        # to SEEK and comes back 401, instead of failing here for free.
        if (user_seek['password'] is None or user_seek['password']=="") \
                and user_seek['token_provider'] is None:
            # De-duplicated because this shares its wording with the username
            # check above, and `err` is rendered as a list straight onto the
            # login page (dmac/views.py:163, login.html). Widening this check to
            # cover None would otherwise make an anonymous request show the same
            # sentence twice.
            message = "No valid username or password"
            if message not in err:
                err.append(message)
            logger.debug(message)
            status = False

        if status:
            if request is None:
                logger.debug("SeekAPI should already be initialized")
                person_id = 0
                err.append("Person id not defined")
                status = False
                logger.debug("Person id not defined")
            else:
                self.__seekapi = SeekAPI(user_seek['server'], user_seek['username'],
                                         user_seek['password'],
                                         token_provider=user_seek['token_provider'])

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
    
    def __getProjectName(self, projectid):
        if projectid is None or projectid=="":
            return ""

        pinfo = self.getInfoObject("/projects/", int(projectid))
        projectname =  pinfo['attributes']['title']
        return projectname

    def getProjectName(self, projectid):
        return self.__getProjectName(projectid)

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
    
    
    def __dataFilePayload(self, title, description, originalfilename,
                          content_type, userid, projectid, assayid):
        """The SEEK JSON:API body for a data-file upload.

        This replaced a pair of 51-line escaped-JSON-inside-a-shell-string
        literals that differed only by whether the ``assays`` relationship was
        present. Building a dict and letting ``json.dumps`` escape it also
        closes ``LATENT_BUGS.md`` #45: the hand-built version interpolated
        ``title`` and ``description`` raw, so a quote silently dropped out of
        the stored title, a backslash was re-interpreted as an escape, and a
        crafted title could add arguments to the curl command line.
        """
        relationships = {
            'creators': {'data': [{'id': str(userid), 'type': 'people'}]},
            'projects': {'data': [{'id': str(projectid), 'type': 'projects'}]},
        }
        if not (assayid is None or assayid <= 0):
            relationships['assays'] = {'data': [{'id': str(assayid),
                                                 'type': 'assays'}]}
        return {'data': {
            'type': 'data_files',
            'attributes': {
                'title': title,
                'description': description,
                'license': 'CC-BY-4.0',
                'content_blobs': [{'original_filename': originalfilename,
                                   'content_type': content_type}],
                'policy': {
                    'access': 'no_access',
                    'permissions': [{
                        'resource': {'id': str(projectid), 'type': 'projects'},
                        'access': 'manage',
                    }],
                },
            },
            'relationships': relationships,
        }}

    def __postCommand(self, endpoint, payload):
        """A curl POST command line carrying ``payload`` as its JSON body.

        ``SeekAPI.callCmdline`` runs the string through ``shlex.split`` and
        ``Popen`` -- there is no shell -- so ``shlex.quote`` is the exact
        inverse of how this argument will be read back.
        """
        apiPostCmd = self.__seekapi.apiPost()[:-1]
        return (apiPostCmd + endpoint + "\" "
                "-H \"accept: application/json\" "
                "-H \"Content-Type: application/json\" "
                "-d " + shlex.quote(json.dumps(payload)))

    def seekupload_dfurl(self, title, fullfilename, originalfilename,
               content_type, userid, projectid, assayid, description, tags, weburl):
        payload = self.__dataFilePayload(title, description, originalfilename,
                                         content_type, userid, projectid, assayid)
        data_instance_query = self.__postCommand("/data_files", payload)
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
        
