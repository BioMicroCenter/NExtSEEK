#!/usr/bin/env python
import requests
import getpass
from urllib2 import urlopen, Request

import logging
logger = logging.getLogger(__name__)

class SeekSession(object):
    def __init__(self, server, username, password, token=None):
        self.__server = server
        self.__username = username
        self.__password = password
        
        self.__headers_json = {
            "Accept": "application/vnd.api+json", 
            "Content-type": "application/vnd.api+json",
            "Accept-Charset": "ISO-8859-1" 
        } 
    
        self.__API_TOKEN = "aHVpbWluZzpKaWFuZ0BuYW4hMTI="

        if token is not None:
            self.__API_TOKEN = token

        self.__headers_token = {
            "Authorization": "Token %s" %self.__API_TOKEN,
            "Accept": "application/vnd.api+json", 
            "Content-type": "application/vnd.api+json",
            "Accept-Charset": "ISO-8859-1" 
        }

        self.__headers_stream = {
            "Authorization": "Token %s" %self.__API_TOKEN,    
            "Accept": "application/octet-stream",
            "Content-Type": "application/octet-stream"
        } 
        if username is not None and password is not None:
            session = self.__authenticate(self.__headers_json, username, password)
        elif token is not None:
            session = None
        else:
            session = None
        self.__session = session

    def __authenticate(self, headers, username, password):
        # session.auth = (...) is the same Latin-1 HTTPBasicAuth path as
        # requests' auth= kwarg, so encode the header ourselves (#52). Imported
        # inside the method to avoid the seekdb <-> nextseek_api import cycle.
        from nextseek_api.helpers import basic_auth_header
        session = requests.Session()
        session.headers.update(headers)
        session.headers.update(basic_auth_header((username, password)))
        return session

    def loginShell(self):
        from nextseek_api.helpers import basic_auth_header
        session = requests.Session()
        session.headers.update(self.__headers_json)
        session.headers.update(
            basic_auth_header((input('Username:'), getpass.getpass('Password')))
        )
        self.__session = session
        
    def getSeekURL(self, seekurl):
        if self.__session is None:
            return {}
            
        server = self.__server
        if server[-1]!="/":
            server += "/"
            
        seekurl_complete = server + seekurl
        r = self.__session.get(seekurl_complete, verify=False, headers=self.__headers_json)
        if (r.status_code != 200):
            logger.debug(r.json())
        r.raise_for_status()
        return r.json()
        
    def postSeekURL(self, seekurl, data_json):
        if self.__session is None:
            return None
        
        server = self.__server
        if server[-1]!="/":
            server += "/"
            
        seekurl_complete = server + seekurl
        r = self.__session.post(seekurl_complete, json=data_json)
        if (r.status_code != 201):
            logger.debug(r.json())
            return None
        
        r.raise_for_status()
        populated_object = r.json() 
        id = populated_object['data']['id']
        return id
    
    
    def apiTokenRead(self, seekurl):
        if self.__API_TOKEN is None:
            return None

        server = self.__server
        if server[-1]!="/":
            server += "/"
            
        seekurl_complete = server + seekurl
        req = Request(url=seekurl_complete, headers=self.__headers_token)
        import ssl 
        gcontext = ssl._create_unverified_context()
        response = urlopen(req, context=gcontext)
        data = response.read()
        return data
        
    def apiTokenUpload(self, uploadUrl, binary_data):
        if self.__API_TOKEN is None:
            return None

        server = self.__server
        if server[-1]!="/":
            server += "/"
            
        seekurl_complete = server + uploadUrl
        session = requests.Session()
        upload = session.put(seekurl_complete, data = binary_data, headers = self.__headers_stream)
        upload.raise_for_status()
        
        populated_object = upload.json()  
        id = populated_object['data']['id']
        return id
        