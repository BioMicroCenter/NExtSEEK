#!/usr/bin/env python
import requests
import json
import string
import pandas as pd
from pandas.io.json import json_normalize
import getpass
from urllib2 import urlopen, Request
from PIL import Image
import io
import argparse

import logging
logger = logging.getLogger(__name__)

def authenticate(headers):
    # session.auth = (...) is the same Latin-1 HTTPBasicAuth path as requests'
    # auth= kwarg, so encode the header ourselves (#52).
    from nextseek_api.helpers import basic_auth_header
    session = requests.Session()
    session.headers.update(headers)
    session.headers.update(basic_auth_header(('username', getpass.getpass('Password'))))
    return session

def json_for_resource(session, headers_json, url, type, id):
    r = session.get(url + "/" + type + "/" + str(id), headers=headers_json)
    if (r.status_code != 200):
        print(r.json())
    r.raise_for_status()
    return r.json()

def readJsonData(session, headers_json, url, data_id, data_type):
    result_json = json_for_resource(session, headers_json, url, data_type, data_id)
    filetitle = result_json['data']['attributes']['title']
    return result_json

def formatJsonDataRelationshipsTitle(session, headers_json, source_base_url, input_data):
    files = []
    source_relationships = input_data['data']['relationships']
    
    for dtype in source_relationships:
        source_dtype_entry = source_relationships[str(dtype)]['data']
        source_data_type = 'none'
        source_data_id = 'none'   
        if(dtype=='investigation' or dtype=='study'):
            item = source_dtype_entry
            source_data_type = item['type']
            source_data_id = item['id']
            
            j = json_for_resource(session,headers_json,source_base_url,item['type'],item['id'])  
            
            files.append({
                'type':source_data_type, #j['data']['type'],
                'id':source_data_id, #j['data']['id'],
                'title':j['data']['attributes']['title'],      
            })

        else:
            for item in source_dtype_entry:
                source_data_id = item['id']
                source_data_type = item['type']
                if(item['type']=='people'): 
                    source_data_type = str(dtype)
                
                j = json_for_resource(session,headers_json,source_base_url,item['type'],item['id'])  
                
                files.append({
                    'type':source_data_type,
                    'id':source_data_id, #j['data']['id'],
                    'title':j['data']['attributes']['title'],      
                })
 
    return files

def formatJsonDataRelationships(input_data):
    files = []
    source_relationships = input_data['data']['relationships']
    
    for dtype in source_relationships:
        source_dtype_entry = source_relationships[str(dtype)]['data']
        source_data_type = 'none'
        source_data_id = 'none'       
        
        if(dtype=='investigation' or dtype=='study'):
            item = source_dtype_entry
            source_data_type = item['type']
            source_data_id = item['id']
            files.append({
                'type':source_data_type, 
                'id':source_data_id, 
            })

        else:
            for item in source_dtype_entry:
                source_data_id = item['id']
                source_data_type = item['type']
                if(item['type']=='people'): 
                    source_data_type = str(dtype)
                
                files.append({
                    'type':source_data_type,
                    'id':source_data_id, #j['data']['id'],
                })
                
    return files

def readBlobData(session, headers_json, headers_token, url, data_id, data_type):
    result_json = json_for_resource(session, headers_json, url, data_type, data_id)
    
    filetitle = result_json['data']['attributes']['title']
    filelicense = result_json['data']['attributes']['license']
    
    blob = result_json['data']['attributes']['content_blobs'][0]
    filename = blob['original_filename']
    filetype = blob['content_type']

    
    link = blob['link']
    download_link = link + "/download"
    req = Request(url=download_link, headers=headers_token) 
    data = urlopen(req).read()
    return result_json, filetitle, filename, filetype, filelicense, link, download_link, data


def registerStudy(session, in_study_json, target_project_id, target_investigation_id, target_creator_id):
    new_study_json = {}
    new_study_json['data'] = {}
    new_study_json['data']['type'] = 'studies'

    new_study_json['data']['attributes'] = {}
    new_study_json['data']['attributes']['title'] = in_study_json['data']['attributes']['title']
    new_study_json['data']['attributes']['description'] = in_study_json['data']['attributes']['description']

    #new_assay_json['data']['attributes']['policy'] = in_assay_json['data']['attributes']['policy']
    new_study_json['data']['attributes']['policy'] = {'access':'no_access'}
    new_study_json['data']['attributes']['policy']['permissions'] = [{'resource':{'id':target_project_id,'type':'projects'},'access':'download'}];

    #new_assay_json['data']['attributes']['assay_class'] = in_assay_json['data']['attributes']['assay_class']
    #new_assay_json['data']['attributes']['assay_type'] = in_assay_json['data']['attributes']['assay_type']
    #new_assay_json['data']['attributes']['technology_type'] = in_assay_json['data']['attributes']['technology_type']

    new_study_json['data']['relationships'] = {}
    new_study_json['data']['relationships']['creators'] = {}
    new_study_json['data']['relationships']['creators']['data'] = [{'id' : target_creator_id, 'type' : 'people'}]
    #new_study_json['data']['relationships']['study'] = {}
    #new_study_json['data']['relationships']['study']['data'] = {'id' : target_study_id, 'type' : 'studies'}
    new_study_json['data']['relationships']['investigation'] = {}
    new_study_json['data']['relationships']['investigation']['data'] = {'id' : target_investigation_id, 'type' : 'investigations'}
    new_study_json['data']['relationships']['projects'] = {}
    new_study_json['data']['relationships']['projects']['data'] = {'id' : target_project_id, 'type' : 'projects'}

    r = session.post(target_base_url + '/studies', json=new_study_json)
    r.raise_for_status()
    populated_study = r.json() 
    study_id = populated_study['data']['id']
    
    return new_study_json, study_id


def registerAssay(session, in_assay_json, target_project_id, target_investigation_id, target_study_id, target_creator_id):
    new_assay_json = {}
    new_assay_json['data'] = {}
    new_assay_json['data']['type'] = 'assays'

    new_assay_json['data']['attributes'] = {}
    new_assay_json['data']['attributes']['title'] = in_assay_json['data']['attributes']['title']
    new_assay_json['data']['attributes']['description'] = in_assay_json['data']['attributes']['description']

    #new_assay_json['data']['attributes']['policy'] = in_assay_json['data']['attributes']['policy']
    new_assay_json['data']['attributes']['policy'] = {'access':'no_access'}
    new_assay_json['data']['attributes']['policy']['permissions'] = [{'resource':{'id':target_project_id,'type':'projects'},'access':'download'}];

    new_assay_json['data']['attributes']['assay_class'] = in_assay_json['data']['attributes']['assay_class']
    new_assay_json['data']['attributes']['assay_type'] = in_assay_json['data']['attributes']['assay_type']
    new_assay_json['data']['attributes']['technology_type'] = in_assay_json['data']['attributes']['technology_type']

    new_assay_json['data']['relationships'] = {}
    new_assay_json['data']['relationships']['creators'] = {}
    new_assay_json['data']['relationships']['creators']['data'] = [{'id' : target_creator_id, 'type' : 'people'}]
    new_assay_json['data']['relationships']['study'] = {}
    new_assay_json['data']['relationships']['study']['data'] = {'id' : target_study_id, 'type' : 'studies'}
    new_assay_json['data']['relationships']['investigation'] = {}
    new_assay_json['data']['relationships']['investigation']['data'] = {'id' : target_investigation_id, 'type' : 'investigations'}
    new_assay_json['data']['relationships']['projects'] = {}
    new_assay_json['data']['relationships']['projects']['data'] = {'id' : target_project_id, 'type' : 'projects'}

    r = session.post(target_base_url + '/assays', json=new_assay_json)
    r.raise_for_status()
    populated_assay = r.json()
    assay_id = populated_assay['data']['id']
    
    return assay_id

def registerBlobData(session, base_url, data_type, filetitle, filelicense, blob, target_project_id, target_investigation_id, target_study_id, target_assay_id, target_creator_id):
    data_array_name = {}
    data_array_name['data'] = {}
    data_array_name['data']['type'] = data_type
    
    data_array_name['data']['attributes'] = {}
    data_array_name['data']['attributes']['title'] = filetitle
    data_array_name['data']['attributes']['license'] = filelicense #'CC-BY-4.0'
    #data_array_name['data']['attributes']['policy'] = {'access':'download'}
    data_array_name['data']['attributes']['policy'] = {'access':'no_access'}
    data_array_name['data']['attributes']['policy']['permissions'] = [{'resource':{'id':target_project_id,'type':'projects'},'access':'download'}];
    data_array_name['data']['attributes']['content_blobs'] = [blob] #error if blob is not there
        
    data_array_name['data']['relationships'] = {}
    data_array_name['data']['relationships']['projects'] = {}
    data_array_name['data']['relationships']['projects']['data'] = [{'id' : target_project_id, 'type' : 'projects'}]
    data_array_name['data']['relationships']['investigations'] = {}
    data_array_name['data']['relationships']['investigations']['data'] = [{'id' : target_investigation_id, 'type' : 'investigations'}]
    data_array_name['data']['relationships']['studies'] = {}
    data_array_name['data']['relationships']['studies']['data'] = [{'id' : target_study_id, 'type' : 'studies'}]
    data_array_name['data']['relationships']['assays'] = {}
    data_array_name['data']['relationships']['assays']['data'] = [{'id' : target_assay_id, 'type' : 'assays'}]
    data_array_name['data']['relationships']['creators'] = {}
    data_array_name['data']['relationships']['creators']['data'] = [{'id' : target_creator_id, 'type' : 'people'}]
    
    r = session.post(base_url + '/' + data_type, json = data_array_name)
    r.raise_for_status()

    populated_data_file = r.json()
    data_file_id = populated_data_file["data"]['id']
    data_file_link = populated_data_file['data']['attributes']['content_blobs'][0]['link'] 
    return data_file_id, data_file_link

def uploadBlobData(session, headers_json, headers_stream, base_url, data_type, blob_id, blob_url, binary_data):
    upload = session.put(blob_url, data = binary_data, headers = headers_stream)
    upload.raise_for_status()
    created_json = json_for_resource(session, headers_json, base_url, data_type, blob_id)

def TransferData(session, headers_json, headers_stream, base_url, data_type, filetitle, filelicense, blob, dataBinary,
    target_project_id, target_investigation_id, target_study_id, target_assay_id, target_creator_id): # register, upload
    target_data_file  = registerBlobData(
        session, base_url, data_type, filetitle, filelicense, blob, 
        target_project_id, target_investigation_id, target_study_id, target_assay_id, target_creator_id)
    target_data_file_id = target_data_file[0]
    target_data_file_link = target_data_file[1]
    uploadBlobData(session, headers_json, headers_stream, base_url, data_type, target_data_file_id, target_data_file_link, dataBinary)      

def determineISAstructureFromRelationships(data_type, input_relationships):
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
            #if(source_data_type==structure_people[y]):
            #if(dtype==structure_people[y]):
            if(item['type']==structure_people[y]):
                isa_structure_people.append(item)              
             
    if(len(isa_structure_up)>0):
        print("ISA STRUCTURE UP:");print(json_normalize(isa_structure_up));print()
    if(len(isa_structure_down)>0):        
        print("ISA STRUCTURE DOWN:");print(json_normalize(isa_structure_down));print()
    if(len(isa_structure_people)>0): 
        print("ISA STRUCTURE PEOPLE:");print(json_normalize(isa_structure_people));print()
        
    return isa_structure_up, isa_structure_down, isa_structure_people

def getDISA(session, headers_json, url, data_id, data_type): 
    source_json = readJsonData(session, headers_json, url, data_id, data_type)
    source_relationships = formatJsonDataRelationshipsTitle(session, headers_json, url, source_json)
    isas = determineISAstructureFromRelationships(data_type, source_relationships)
    return isas[1]

def getFullDISA(session1, headers1, source_base_url, source_json_id, source_data_type):
    out = getDISA(session1, headers1, source_base_url, source_json_id, source_data_type)
    isa_struct = []
    isa_struct.append([{'type': source_data_type, 'id': source_json_id}, out])
    for entry_num in range(0, len(out)):
        out_temp = getDISA(session1, headers1, source_base_url, out[entry_num]['id'], out[entry_num]['type'])
        if(len(out_temp)>0):
            isa_struct.append([out[entry_num], out_temp])
        else:
            out_temp = [];
            isa_struct.append([out[entry_num], out_temp])
            
    return isa_struct

def download(headers1):
    session1 = authenticate(headers1)
    source_base_url = 'http://localhost:3000'

    source_study_id = 1 
    source_data_type = 'studies'    
    source_json_id = source_study_id
    source_json = readJsonData(session1, headers1, source_base_url, source_json_id, source_data_type)
    source_relationships = formatJsonDataRelationships(source_json)

    isas = determineISAstructureFromRelationships(source_data_type,source_relationships)

    out = getDISA(session1, headers1, source_base_url, source_json_id, source_data_type)
    return session1, source_base_url, source_json_id, source_data_type #source_json, 

def upload(headers1):
    session2 = authenticate(headers1)
    target_base_url = 'http://doroteadesktop:4000'
    target_project_id = 2 # Project Alpha
    target_project_type = 'projects'
    target_investigation_id = 4 # investigation two
    target_investigation_data_type = 'investigatons'
    target_creator_id = 1  
    
    return session2, target_base_url, target_project_id, target_investigation_id, target_creator_id

def registerAndCopyStudyAndBelow(session1, session2, headers1, headers2, headers3, 
                                 source_base_url, source_json_id, source_data_type, 
                                 target_base_url, target_project_id, target_investigation_id, target_creator_id):

    isa_structure = getFullDISA(session1, headers1, source_base_url, source_json_id, source_data_type)    
    source_json = readJsonData(session1, headers1, source_base_url, source_json_id, source_data_type)
    out_json = registerStudy(session2, source_json, target_project_id, target_investigation_id, target_creator_id)#out_json[0] - json   ;  #out_json[1] - id
    target_study_id = out_json[1]
    if(len(isa_structure)>1):
        for x in range(0, len(isa_structure)):
            if(isa_structure[x][0]['type']=='assays'):
                assay_json = readJsonData(session1, headers1, source_base_url, isa_structure[x][0]['id'], isa_structure[x][0]['type'])
                target_assay_id = registerAssay(session2, assay_json, target_project_id, target_investigation_id, target_study_id, target_creator_id)
                if(len(isa_structure[x][1])>0):
                    for y in range(0, len(isa_structure[x][1])):
                        if(isa_structure[x][1][y]['type']=='data_files'):#if it is a data file
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

def deleteISA(session, headers_json, base_url, data_id, data_type):
    isa_structure = getFullDISA(session, headers_json, base_url, data_id, data_type)
    
    for x in reversed(range(0, len(isa_structure))):  
        json_entry = json_for_resource(session, headers_json, base_url, isa_structure[x][0]['type'], isa_structure[x][0]['id'])
        json_entry_url = json_entry['data']['links']['self']
        session.delete(base_url + json_entry_url)

def test():
    API_TOKEN = "aHVpbWluZzpKaWFuZ0BuYW4hMTI="
    headers1 = {
        "Accept": "application/vnd.api+json", 
        "Content-type": "application/vnd.api+json",
        "Accept-Charset": "ISO-8859-1" 
    } 
    session1, source_base_url, source_json_id, source_data_type = download(headers1)
    return

    headers2 = { 
        "Authorization": "Basic %s" %API_TOKEN,
        "Accept": "application/vnd.api+json", 
        "Content-type": "application/vnd.api+json",
        "Accept-Charset": "ISO-8859-1" 
    }

    headers3 = { 
        "Authorization": "Basic %s" %API_TOKEN,    
        "Accept": "application/octet-stream",
        "Content-Type": "application/octet-stream"
    } 

    session2, target_base_url, target_project_id, target_investigation_id, target_creator_id = upload(headers1)
    registerAndCopyStudyAndBelow(session1, session2, headers1, headers2, headers3, 
        source_base_url, source_json_id, source_data_type, #source_json, 
        target_base_url, target_project_id, target_investigation_id, target_creator_id)#, isa_structure

    deleteISA(session2, headers1, target_base_url, 11, 'studies')
    
    session1.close()
    session2.close()


def main():
    parser = argparse.ArgumentParser(description="Download and upload a snapshot of ISA from a Seek to another Seek.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    test2()
    return


if __name__ == "__main__":
    main()
