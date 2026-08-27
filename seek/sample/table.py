"""``DBtable_sample`` itself: the class that combines the mixins.

The mixins are listed before ``DBtable`` so that this class's overrides
(``__init__`` here, ``reformatDataForClient`` in ``core``) win over the
base. They hold no state of their own; every one of them operates on the
``self`` this class builds."""

from dmac.dbtable import DBtable
from ..dbtable_sampleattribute import DBtable_sampleattribute
from ..dbtable_sops import DBtable_sops
from neo4j import GraphDatabase
from ..models import People
from ..models import Samples
from dmac.conversion import getDefaultDateTime
import html
from dmac.iocsv import saveDiclistIntoExcel
from django.conf import settings
import simplejson

from .constants import NEO4J_DATABASE, SAMPLE_FILE_ACCESSOR_NAME, SAMPLE_FILTER_MAPPING, SAMPLE_LINK_ACCESSOR_NAME, SAMPLE_PARENT_ACCESSOR_NAME, SAMPLE_PROTOCOL_ACCESSOR_NAME, SAMPLE_PUBLISH_ACCESSOR_NAME, SEEK_DATABASE, logger
from .upload import SampleUploadMixin
from .download import SampleDownloadMixin
from .search import SampleSearchMixin
from .api import SampleApiMixin
from .immport import SampleImmportMixin
from .queries import SampleQueriesMixin
from .trees import SampleTreesMixin
from .core import SampleCore


class DBtable_sample(SampleUploadMixin, SampleDownloadMixin, SampleSearchMixin, SampleApiMixin, SampleImmportMixin, SampleQueriesMixin, SampleTreesMixin, SampleCore, DBtable):

    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'SEEK', 'seek_development')
        self.tablename = 'samples'
        self.tablemodel = Samples
        self.fulltablename = self.tablemodel
        # Use the Django model for viewtablename so the Django backend can call .objects
        self.viewtablename = self.tablemodel
        self.fields = [
            'id',
            'title',
            'sample_type_id',
            'json_metadata',
            'uuid',
            'contributor_id',
            'policy_id',
            'created_at',
            'updated_at',
            'first_letter',
            'other_creators',
            'originating_data_file_id',
            'deleted_contributor'
        ]
        self.uniqueFields = ['uuid']
        self.primaryField = "id"
        self.fieldMapping = SAMPLE_FILTER_MAPPING
        self.excludeFields = []

    def deleteSampleNeo4j(self, sample_id):
        with GraphDatabase.driver(NEO4J_DATABASE['URI'], auth=NEO4J_DATABASE['AUTH']) as driver:
            records, summary, keys = driver.execute_query("MATCH (s:Sample {id: $id}) DETACH DELETE s",
                    id=sample_id,
                    database_=NEO4J_DATABASE['NAME'])
            logger.debug(f"NEO4J summary: {summary}")

    def _deleteOneSample(self, sample_id, policy_id):
        sqlqueries = []
        sqlquery = "DELETE FROM projects_samples where sample_id=" + str(sample_id) + ";"
        sqlqueries.append(sqlquery)
        sqlquery = "DELETE FROM sample_resource_links where sample_id=" + str(sample_id) + ";"
        sqlqueries.append(sqlquery)
        sqlquery = "DELETE FROM sample_auth_lookup where asset_id=" + str(sample_id) + ";"
        sqlqueries.append(sqlquery)
        sqlquery = "DELETE FROM assay_assets where asset_id=" + str(sample_id) + " AND asset_type='Sample';"
        sqlqueries.append(sqlquery)
        sqlquery = "DELETE FROM assets_creators where asset_id=" + str(sample_id) + " AND asset_type='Sample';"
        sqlqueries.append(sqlquery)
        sqlquery = "delete FROM samples where id=" + str(sample_id) + ";"
        sqlqueries.append(sqlquery)
        sqlquery = "DELETE FROM permissions where policy_id=" + str(policy_id) + ";"
        sqlqueries.append(sqlquery)
        sqlquery = "delete FROM policies where id=" + str(policy_id) + ";"
        sqlqueries.append(sqlquery)
        db_alias = SEEK_DATABASE
        status = self.db.run_custom_transaction(sqlqueries, db_alias)
        if status:
            msg = "Transaction successful"
            try:
                self.deleteSampleNeo4j(sample_id)
            except:
                None
        else:
            msg = "Error: The trandsaction of deletion failed. Delete this sample manually"
        
        return msg, status

    def _getSampleChildren(self, currentuid):
        records = self.db.retrieveRecords(self.tablemodel, 'json_metadata', currentuid)
        childrenList = []
        for record in records:
            uid = record['uuid']
            metadata = record['json_metadata']
            sampleDic = self._getRecordFromJson(metadata)
            parent_uids = self._getParentUIDs(sampleDic)
            if currentuid in parent_uids:
                sid = record['id']
                dici = {'id':sid, 'uid':uid}
                childrenList.append(dici)
            
        return childrenList

    def _deleteSampleList(self, user_seek, sample_ids, xlsfile):
        user_id = user_seek['user_id']
        roles_mask = self.db.retrieveFieldValue(People, user_id, 'roles_mask')
    
        status = 1
        msg = ''
        diclist = []
        for sample_id in sample_ids:
            dici = {}
            dici['id'] = sample_id
            record = self._retrieveSampleByID(sample_id)
            if record is None:
                msgi = 'Error: Sample ' + str(sample_id) +  ' not found in DB '
                status = 0
                dici['json_metadata'] = msgi
                msg += msgi + '<br/>'
                diclist.append(dici)
                continue
            
            contributor_id = record['contributor_id']
            policy_id = record['policy_id']
            currentuid = record['uuid']
            
            dici['uid'] = currentuid
            childrenList =  self._getSampleChildren(currentuid)
            if user_id==contributor_id or int(roles_mask)>0:
                if len(childrenList)==0:
                    msgi, statusi = self._deleteOneSample(sample_id, policy_id)
                    if statusi:
                        dici['statusi'] = 'DELETED'
                    else:
                        dici['statusi'] = msgi
                else:
                    msgi = 'Warning: Sample has child sample thus has to be deleted manually.'
                    msg += msgi + '<br/>'
                    status = 0
                    dici['statusi'] = msgi
            else:
                msgi = 'Error: Only admin or owner is allowed to delete sample.'
                msg += msgi + '<br/>'
                status = 0
                dici['statusi'] = msgi
            dici['json_metadata'] = msgi
            diclist.append(dici)
        
        headers = ['id', 'uid', 'sample_type', 'first_name', 'created_at', 'json_metadata', 'statusi']
        saveDiclistIntoExcel(diclist, xlsfile, headers, 'samples')
        return diclist, msg, status 

    def deleteSamples(self, user_seek, xlsfile, link, sample_ids):
        diclist, msg, status = self._deleteSampleList(user_seek, sample_ids, xlsfile)
        data = {}
        data['msg'] = msg
        data['status'] = status
        data['link'] = link
        data['diclist'] = diclist
        
        reportData = simplejson.dumps(data, default=str)
        return reportData

    def _formatSampleUIDLink(self, sample_uid):
        url = "/seek/sampletree/uid=" + str(sample_uid) + "/";
        # Rendered as raw HTML by the client; escape the untrusted uid in both
        # the href attribute and the link text to prevent HTML injection.
        weblink = '<a href="' + html.escape(url) + '" target="_blank">' + html.escape(str(sample_uid)) + '</a>'
        return weblink

    def _formatSopUIDLink(self, sop_uid):
        # This used to point at /seek/sop/uid=<uid>/, whose route and view were
        # both removed in 5834cda when the SOP pages moved to nextseek_api, so
        # every protocol link on the sample page 404'd. Link to the SOP in SEEK
        # instead, which is where the SOPs table already sends users. The SEEK
        # route is id-based, so resolve the UID (stored as sops.title) to its
        # id; only reached from getSampleInfo for a single sample, so the
        # lookup is per protocol on one detail page, not per grid row.
        sop_uid = str(sop_uid).strip()

        # Not every Protocol value is a NExtSEEK SOP UID: samples also record it
        # as a full URL into FAIRDOMHub or another SEEK instance (e.g.
        # https://fairdomhub.org/sops/795). Those are already addressable, so
        # link them straight through rather than failing a UID lookup and
        # degrading them to plain text. The 'http' prefix check blocks
        # javascript:/data: schemes, matching _formatExternalLink.
        if sop_uid[0:4].lower() == 'http':
            return '<a href="' + html.escape(sop_uid) + '" target="_blank">' + html.escape(sop_uid) + '</a>'

        sop_id = None
        try:
            dbsop = DBtable_sops("DEFAULT")
            records = dbsop.queryRecordsByConstraint({'title': sop_uid})
            if records is not None and len(records) == 1:
                sop_id = records[0]['id']
        except Exception:
            logger.exception('failed resolving SOP UID to a SEEK id: %s', sop_uid)
            sop_id = None

        # No unambiguous match: render the UID as plain text rather than emit a
        # link that is known to go nowhere.
        if sop_id is None:
            return html.escape(sop_uid)

        url = settings.SEEK_PUBLIC_URL + "/sops/" + str(sop_id)
        # Rendered as raw HTML by the client; escape the untrusted uid in both
        # the href attribute and the link text to prevent HTML injection.
        weblink = '<a href="' + html.escape(url) + '" target="_blank">' + html.escape(sop_uid) + '</a>'
        return weblink

    def _formatExternalLink(self, urlValue):
        weblink = urlValue
        if ";" in urlValue:
            weblink = ''
            vis = urlValue.split(";")
            i = 0
            for vi in vis:
                vi = vi.strip()
                if len(vi)>0:
                    if vi[0:4].lower()=='http':
                        if i>0:
                            weblink += ","
                        
                        # 'http' prefix check above blocks javascript:/data:
                        # schemes; escape to prevent href/text HTML injection.
                        weblink += '<a href="' + html.escape(vi) + '" target="_blank">' + html.escape(vi) + '</a>'
                        i += 1
        else:
            vi = urlValue.strip()
            if len(vi)>0:
                if vi[0:4].lower()=='http':
                    # 'http' prefix check blocks javascript:/data: schemes;
                    # escape to prevent href/text HTML injection.
                    weblink = '<a href="' + html.escape(vi) + '" target="_blank">' + html.escape(vi) + '</a>'
        
        return weblink

    def _formatLinkUrl(self, attrname, attrvalue):
        weblink = attrvalue
        value = attrvalue
        
        if SAMPLE_PARENT_ACCESSOR_NAME in attrname:
            if attrvalue is None:
                return weblink
            
            if ";" in value:
                weblink = ''
                vis = value.split(";")
                i = 0
                for vi in vis:
                    vi = vi.strip()
                    if len(vi)>0:
                        if i>0:
                            weblink += ","
                            
                        weblink += self._formatSampleUIDLink(vi)
                        i += 1
            else:
                value = value.strip()
                if len(value)>0:
                    weblink = self._formatSampleUIDLink(value)
        
        elif SAMPLE_PROTOCOL_ACCESSOR_NAME in attrname:
            if attrvalue is None:
                return weblink
            
            if ";" in value:
                weblink = ''
                vis = value.split(";")
                i = 0
                for vi in vis:
                    vi = vi.strip()
                    if len(vi)>0:
                        if i>0:
                            weblink += ","
                            
                        weblink += self._formatSopUIDLink(vi)
                        i += 1
            else:
                value = value.strip()
                if len(value)>0:
                    weblink = self._formatSopUIDLink(value)
        
        elif SAMPLE_LINK_ACCESSOR_NAME in attrname:
            if attrvalue is None:
                return weblink
            weblink = self._formatExternalLink(attrvalue)
            
        elif SAMPLE_FILE_ACCESSOR_NAME in attrname:
            if attrvalue is None:
                return weblink
            weblink = self._formatExternalLink(attrvalue)
            
        elif SAMPLE_PUBLISH_ACCESSOR_NAME in attrname:
            if attrvalue is None:
                return weblink
            weblink = self._formatExternalLink(attrvalue)
        
        return weblink

    def getSampleInfo(self, sample_id):
        record = self._retrieveSampleByID(sample_id)
        if record is None:
            return None, None
        
        sampletype_id = record['sample_type_id']
        sattr = DBtable_sampleattribute()
        attributeInfo = sattr.getAttributeInfo(sampletype_id)
        headers = attributeInfo['headers']
        json_metadata = record['json_metadata']
        dici = self._getRecordFromJson(json_metadata)
        
        diclist = []
        for header in headers:
            headerStripped = header.strip()
            attrdici = {}
            attrdici['attrname'] = header
            if headerStripped in dici:
                value = dici[headerStripped]
                if value is not None:
                    try:
                        valuestr = str(value)
                        if len(valuestr.strip())>0:
                            attrdici['attrvalue'] = self._formatLinkUrl(headerStripped, valuestr)
                            diclist.append(attrdici)
                    except:
                        attrdici['attrvalue'] = value
                        diclist.append(attrdici)
            
        return dici, diclist

    def getSampleType(self, user_seek, sampletype_id, attribute, project_id=0):
        if attribute=='none':
            msg = 'ignore validation'
        else:
            sattr = DBtable_sampleattribute()
            msg, status = sattr.validateFilters(sampletype_id, attribute, filter_rule, filter_valueFrom, filter_valueTo)
            if status==0:
                data = {'msg':msg, 'status': status}
                reportData = simplejson.dumps(data, default=str)
                return reportData
        
        data = self._retrieveSamplesInType(user_seek, sampletype_id, project_id)
        if attribute=='none':
            msg = 'ignore filtering'
        else:
            rows = self._filterSamples(data['rows'], sampletype_id, attribute, filter_rule, filter_valueFrom, filter_valueTo) 
            data['rows'] = rows
            data['total'] = len(rows)
        data['msg'] = 'okay'
        data['status'] = 1
        
        reportData = simplejson.dumps(data, default=str)
        return reportData

    def _updateSampleMeta(self, metadata_db, diclist_attributes, attri_renamed):
        metadata_out = {}
        
        for dici in diclist_attributes:
            accessor_name = dici['title']
            
            if accessor_name in metadata_db:
                metadata_out[accessor_name] = metadata_db[accessor_name]
            elif accessor_name in attri_renamed:
                accessor_name_old = attri_renamed[accessor_name]
                if accessor_name_old in metadata_db:
                    metadata_out[accessor_name] = metadata_db[accessor_name_old]
                else:
                    metadata_out[accessor_name] = ''
            else:
                metadata_out[accessor_name] = ''
        
        return metadata_out

    def _updateSamplesMeta(self, user_seek, samples, sampletype_id, attri_renamed):
        sattr = DBtable_sampleattribute()
        attributeInfo = sattr.getAttributeInfo(sampletype_id)
        diclist_attributes = attributeInfo['diclist']
        username = user_seek['username']
        
        n = 0
        nright = 0
        msg = ''
        status = 1
        for record in samples:
            json_metadata = record['json_metadata']
            metadata_db = self._getRecordFromJson(json_metadata)
            
            metadata_out = self._updateSampleMeta(metadata_db, diclist_attributes, attri_renamed)
            
            record['json_metadata'] = simplejson.dumps(metadata_out, default=str)
            record['updated_at'] = getDefaultDateTime()

            msgi, statusi, sample_id = self.storeOneRecord(username, record)
            if statusi:
                nright += 1
            else:
                status = 0
                msg += msgi +  '<br/>'
            
            n += 1
            
        return msg, status

    def updateSampleType(self, user_seek, sampletype_id, attri_renamed, project_id=0):
        data = self._retrieveSamplesInType(user_seek, sampletype_id, project_id)
        msg, status = self._updateSamplesMeta(user_seek, data['rows'], sampletype_id, attri_renamed)
        data['msg'] = msg
        data['status'] = status
        data['link'] = ''
        
        reportData = simplejson.dumps(data, default=str)
        return reportData
