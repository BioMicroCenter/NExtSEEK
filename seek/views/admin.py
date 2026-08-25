"""Supervisor-only pages: retrieval, clades and internal assays."""

import logging

from dmac.dbtable_assaysinternalassays import DBtable_assaysinternalassays
from dmac.dbtable_clades import DBtable_clades
from dmac.dbtable_internalassays import DBtable_internalassays
from dmac.dbtable_sampletypesclades import DBtable_sample_types_clades as DBtable_stc
from neo4j import GraphDatabase
from django.http import HttpResponse
import MySQLdb
from ..seekdb import SeekDB
import datetime
import json
from ..responses import json_response
import os
import pandas as pd
from django.shortcuts import render
from ..decorators import requires_seek_login
from ..decorators import requires_seek_login_redirect
from ..decorators import requires_supervisor
from django.conf import settings
import simplejson
from ..decorators import verifySuperUser
from nextseek_api.services.sample_workbook import write_samples_workbook

from .shared import DOWNLOAD_DIRECTORY, SEEK_DATABASE

logger = logging.getLogger(__name__)

def adminRetrieveSamples(request):
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    user_projects = seekdb.getCurrentUser()['data']['relationships']['projects']['data']
    user_project_ids = map(lambda x: x['id'], user_projects)

    if verifySuperUser(request) == 1:
        admin = True
    else:
        admin = False

    if not user_seek['status']:
        err = user_seek['err']
        msg = err
        status = 0
        docurl = ''
        return json_response(msg, status, docurl)
    else:
        if request.method == "POST":
            logger.debug(f"REQUEST: {request.POST.keys()}")
            uids = request.POST.get('retrieval_uids').strip().split()
            children_uids = get_children_uids(uids, user_project_ids, admin)

            datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
            filename = 'download-samples-' + datenow + '.xlsx'
            downloadfile = DOWNLOAD_DIRECTORY + filename

            sample_retrieval_data(children_uids, downloadfile)

            with open(downloadfile, 'rb') as fh:
                response = HttpResponse(fh.read(), content_type="application/vnd.ms-excel")
                response['Content-Disposition'] = 'inline; filename=' + os.path.basename(downloadfile)
                return response
        else:
            return render(request, "admin_retrieval.html")

def get_children_uids(sample_uids, user_project_ids, admin):
    db = settings.DATABASES[SEEK_DATABASE]
    NEO4J_DATABASE = settings.NEO4J_DATABASE
    with GraphDatabase.driver(NEO4J_DATABASE['URI'], auth=NEO4J_DATABASE['AUTH']) as driver:
        r,s,k = driver.execute_query("""
		UNWIND $sample_uids AS sample_uid
        MATCH (s:Sample {uuid: sample_uid})
        MATCH parents=(s)-[:DERIVED_FROM*0..]->(parent)
        MATCH children=(s)<-[:DERIVED_FROM*0..]-(child)
        RETURN collect(DISTINCT s.uuid) + collect(DISTINCT parent.uuid) + collect(DISTINCT child.uuid) AS uuids
        """,
        sample_uids=sample_uids,
        database_=NEO4J_DATABASE['NAME'])
        uids = r[0]['uuids']

    db = settings.DATABASES[SEEK_DATABASE]
    conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])
    cursor = conn.cursor()

    uids_str = ', '.join(f"'{uid}'" for uid in uids)
    project_ids_str = ', '.join(f"'{pid}'" for pid in user_project_ids)

    if admin:
        query = f"""
        SELECT id,sample_type_id,uuid,json_metadata
        FROM {db["NAME"]}.samples
        WHERE uuid IN ({uids_str})
        """
    else:
        query = f"""
        SELECT s.id, s.sample_type_id, s.uuid, s.json_metadata
        FROM {db["NAME"]}.samples s
        JOIN {db["NAME"]}.projects_samples ps
        ON s.id = ps.sample_id
        WHERE s.uuid IN ({uids_str}) AND ps.sample_id = s.id AND ps.project_id IN ({project_ids_str})
        """

    cursor.execute(query)
    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchall()
    samples_retrieved_df = pd.DataFrame(rows, columns=columns)

    cursor.close()
    conn.close()
    return samples_retrieved_df

def parse_json_metadata(metadata_series):
    return metadata_series.apply(lambda x: json.loads(x) if isinstance(x, str) else {})

def parse_children_uids(children_uids):
    children_uids['json_metadata'] = parse_json_metadata(children_uids['json_metadata'])

    metadata_df = pd.json_normalize(children_uids['json_metadata'])
    metadata_df = metadata_df.loc[:, ~metadata_df.columns.duplicated()]

    final_df = pd.concat([children_uids[['uuid']], metadata_df], axis=1)
    final_df.replace("", pd.NA, inplace=True)
    final_df.dropna(axis=1, how='all', inplace=True)

    return final_df

def sample_retrieval_data(children_uids, output):
    # Sheet layout, README included, is owned by
    # nextseek_api.services.sample_workbook so it cannot drift per call path.
    write_samples_workbook(parse_children_uids(children_uids), output)

@requires_seek_login_redirect('/seek/samples/attributes/')
@requires_supervisor('Error: You login as admin to view this page.', with_message_key=True)
def adminClades(request):
    cladedb = DBtable_clades()
    stcdb = DBtable_stc()
    stc = simplejson.dumps(stcdb.getAllWithTitles(), default=str)
    
    return render(request,"clades.html", {'clades': list(cladedb.getAll()), 'stc': stc})

@requires_seek_login
@requires_supervisor('The login user does not have the permission to perform this action.')
def cladesSyncSampleTypes(request):
    stcdb = DBtable_stc()
    stcdb.syncSampleTypes()
    
    return HttpResponse({})

@requires_seek_login
@requires_supervisor('The login user does not have the permission to add the clade.')
def cladeSave(request):
    cladedb = DBtable_clades()
    
    ret = request.GET
    clades = json.loads(ret['records'])

    for clade in clades:
        if 'id' not in clade:
            # New clade. Should create it in DB
            cladedb.new(title=clade['title'],
                        color=clade['color'],
                        order=clade['order'])
        else:
            # Existing clade. Should update
            cladedb.update(clade_id=clade['id'],
                           title=clade['title'],
                           color=clade['order'],
                           order=clade['order'])
        
    return HttpResponse({}, headers={"Refresh": 1})

@requires_seek_login(log_failure=True)
@requires_supervisor('The login user does not have the permission to delete the sample attribute.')
def cladeDelete(request):
    cladedb = DBtable_clades()

    ret = request.GET
    clades = json.loads(ret['records'])

    for clade in clades:
        cladedb.delete(clade['id'])
    
    return HttpResponse({}, headers={"Refresh": 1})

@requires_seek_login
@requires_supervisor('The login user does not have the permission to add the clade.')
def cladeSampleTypesSave(request):
    stcdb = DBtable_stc()
    
    ret = request.GET
    data = json.loads(ret['records'])

    for record in data:
        sample_type_id = record['sample_type_id']
        clade_id = record['clade_title']
        
        stcdb.update(sample_type_id, clade_id)
        
    return HttpResponse({}, headers={"Refresh": 1})

@requires_seek_login_redirect('/seek/samples/attributes/')
@requires_supervisor('Error: You login as admin to view this page.', with_message_key=True)
def internalAssays(request):
    db_ia = DBtable_internalassays()
    db_aia = DBtable_assaysinternalassays()
    internal_assays = simplejson.dumps(db_ia.getAll(), default=list)
    assay_associations = simplejson.dumps(db_aia.getAllWithTitles(), default=list)

    return render(request,"internal_assays.html", {"internal_assays": internal_assays, "assay_associations": assay_associations})

@requires_seek_login
@requires_supervisor('The login user does not have the permission to add the internal assay.')
def internalAssaySave(request):
    ia = DBtable_internalassays()
    
    ret = request.GET
    internal_assays = json.loads(ret['records'])

    for internal_assay in internal_assays:
        if 'id' not in internal_assay:
            ia.new(internal_assay_title=internal_assay['internal_assay_title'],)
        else:
            id = internal_assay["id"]
            internal_assay_title = internal_assay["internal_assay_title"]
            ia.update(internal_assay_id=id,
                      internal_assay_title=internal_assay_title)
        
    return HttpResponse({}, headers={"Refresh": 1})

@requires_seek_login(log_failure=True)
@requires_supervisor('You do not have the permission to delete the internal assay.')
def internalAssayDelete(request):
    ia = DBtable_internalassays()

    ret = request.GET
    internal_assays = json.loads(ret['records'])

    for internal_assay in internal_assays:
        ia.delete(internal_assay['id'])
    
    return HttpResponse({}, headers={"Refresh": 1})

@requires_seek_login
@requires_supervisor('You do not have the permission to add the assay association.')
def assayAssociationSave(request):
    aia = DBtable_assaysinternalassays()
    
    ret = request.GET
    data = json.loads(ret['records'])

    for record in data:
        assay_id = record['assay_id']
        internal_assay_id = record['internal_assay_id']
        
        aia.update(assay_id, internal_assay_id)
        
    return HttpResponse({}, headers={"Refresh": 1})

@requires_seek_login
@requires_supervisor('The login user does not have the permission to perform this action.')
def syncInternalAssays(request):
    aia = DBtable_assaysinternalassays()
    aia.syncAssays()
    
    return HttpResponse({})
