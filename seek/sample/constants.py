"""Module-level constants for the sample table."""

import logging
from django.conf import settings

logger = logging.getLogger(__name__)
NEO4J_DATABASE = settings.NEO4J_DATABASE
SEEK_DATABASE = settings.SEEK_DATABASE
NEXTSEEK_DATABASE = settings.NEXTSEEK_DATABASE
DOWNLOAD_DIRECTORY  = settings.MEDIA_ROOT + "/download/"
DOWNLOAD_DIRECTORY_LINK = settings.MEDIA_URL + 'download/'
SAMPLE_FILTER_MAPPING = {
    "id":"A.id",
    "title":"A.title",
    "sample_type_id":"A.sample_type_id",
    "sample_type":"B.title",
    "uid":"A.uuid",
    "contributor_id":"A.contributor_id",
    "first_name":"C.first_name",
    "created_at":"A.created_at",
    "json_metadata":"A.json_metadata",
    "assay_id":"D.assay_id",
    "assayname":"E.assayname",
    "work_group_id":"F.work_group_id",
    "project_id":"G.project_id",
    "institution_id":"G.institution_id",
    "projectname":"H.title",
    "institution":"I.title"
}
SAMPLE_HEADERS = [
    "id",
    "title",
    "sample_type_id",
    "sample_type",
    "uid",
    "contributor_id",
    "first_name",
    "created_at",
    "json_metadata",
    "assays"
    #"assay_id",
    #"assayname",
    #"work_group_id",
    #"project_id",
    #"institution_id",
    #"projectname",
    #"institution"
]
SAMPLE_DEFAULT = {
    #'id':'',
    'title':'',
    'sampleType_id':0,
    'json_metadata':'',
    'uuid':'',
    'contributor_id':0,
    'policy_id':'',
    'created_at':'',
    'updated_at':'',
    'first_letter':'',
    'other_creators':'',
    'originating_data_file_id':None,  
    'deleted_contributor':None         
}
SAMPLE_SHEET_NAMES = ["INSTRUCTIONS", "SAMPLES", "ASSAY", "ONTOLOGY"]
ATTRIBUTETYPE_ID_WEBLINK = 5
ATTRIBUTETYPE_ID_URI = 19
SAMPLE_PARENT_ATTRIBUTOR = "CreatedFromSample"
SAMPLE_PARENT_ACCESSOR_NAME = "Parent"
SAMPLE_PROTOCOL_ACCESSOR_NAME = "Protocol"
SAMPLE_FILE_ACCESSOR_NAME = "File_"         
SAMPLE_LINK_ACCESSOR_NAME = "Link_"        
SAMPLE_CONTRIBUTOR_ACCESSOR_NAME = "Scientist"
SAMPLE_PUBLISH_ACCESSOR_NAME = "Publish"
SAMPLE_ERRORCODE = {
    '101': 'Error: Excel file in incorrect format.',
    '102': 'Error: Assay sheet does not contain required sheet - ',
    '103': 'Error: Assay sheet does not contain valid "Instruction" sheet.',
    '104': 'Error: Assay sheet does not contain valid "Samples" sheet.',
    '105': 'Error: Sample type not identified in assay sheet - ',
    '106': 'Error: Excel file failed to load - ',
    '201': 'Error: Sample type not uniquely defined in database - ',
    '202': 'Error: Unknown attribute in assay sheet - ',
    '301': 'Error: Sample has an invalid Parent UID. ',
    '302': 'Error: Sample is missing data for either the "Name" or "File_PrimaryData" attribute - ',
    '303': 'Error: Sample has invalid entry for either the "Name" or "File_PrimaryData" attribute. ',
    '304': 'Error: Sample has no entry for the required "Scientist" attribute',
    '305': 'Error: "Scientist" name for the sample not registered in Seek: ',
    '401': 'Error: User has already uploaded a sample with this name to the database; please include the UID in order to update the sample metadata - ',
    '402': 'Error: Sample UID does not match sample name in the SEEK database - ',
    '403': 'Error: Sample name corresponds to more than one record in the database; please ask an admin for help.',
    '501': 'Error: No information provided for sample.',
    '502': 'Error: Required data is missing - ',
    '503': 'Error: Assay sheet is missing the "UID" attribute. ',
    '504': 'Error: Sample not saved into DB - ',
    '601': 'Warning: Sample not saved to the SEEK database - ',
    '602': 'Warning: Data file not associated with a sample in the SEEK database - ',
    '603': 'Warning: Sample saved, but its lineage (parent relationships) was NOT saved to the graph database for UID - ',

    '701': 'Warning: Assay sheet does not contain valid "Update_assay" sheet.',
    
}
DELIMITER_DBFIELD = "::"
SAMPLE_TEMPLATE_FILE = settings.MEDIA_ROOT + "/reserved/SAMPLE_TEMPLATE.xlsx"
IMMPORT_TEMPLATE_FILE_PREFIX = settings.MEDIA_ROOT + "/reserved/IMMPORT_TEMPLATE-"
IMMPORT_TEMPLATE_FILE = settings.MEDIA_ROOT + "/reserved/IMMPORT_TEMPLATE-MAPPING.xlsx"
IMMPORT_TEMPLATES = {'protocols':'protocols',
    'subjectanimals':'subjectanimals',
    'biosamples':'biosamples',
    'experiments':'experiments',
    'experimentsamples':'mass_spec_proteomics'   
}
IMMPORT_TEMPLATES_VERSION = 'Schema Version 3.32'
PUBLISH_SERVER = settings.PUBLISH_URL
RESERVED_REMOVE_VALUE_FOR_UPDATE = "-null"
RESERVED_DEFAULT_VALUE_FOR_UPDATE = "-none"
