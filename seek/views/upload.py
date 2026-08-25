"""Batch sample upload and data-file upload, and their validation."""

import logging

from ..dbtable_sample import DBtable_sample
from django.http import HttpResponse
import MySQLdb
from ..seekdb import SeekDB
import datetime
from dmac.conversion import handle_uploaded_file
import json
from ..responses import json_response
import pandas as pd
from ..responses import plain_text
from django.shortcuts import render
from ..decorators import requires_seek_login_redirect
from django.conf import settings
import simplejson
from ..decorators import verifySuperUser

from .shared import DOWNLOAD_DIRECTORY, DOWNLOAD_DIRECTORY_LINK, SEEK_DATABASE, UPLOAD_DIRECTORY, report

logger = logging.getLogger(__name__)

@requires_seek_login_redirect('/seek/samples/batchupload/', whetherFullInfo=True)
def batchUpload(request):
    seekdb = request.seekdb
    user_seek = request.user_seek

    isSupervisor = verifySuperUser(request)

    lab_options = seekdb.getObjectsToOptions("/institutions")

    all_lab_users = {}
    for lab in lab_options:
        lab_id = int(lab['id'])
        if lab_id != 0:
            lab_info = seekdb.getInfoObject("/institutions/", lab_id)
            if isSupervisor:
                people = lab_info["relationships"]["people"]["data"]
                all_people = []
                for person in people:
                    all_people.append({'id': person['id'], 'title': seekdb.getUserFullname(person['id'])})
                all_lab_users[lab_id] = all_people
            else:
                all_lab_users = {}
                all_lab_users[lab_id] = [{'id': user_seek['person_id'], 'title': seekdb.getUserFullname(user_seek['person_id'])}]

    report['lab_options'] = json.dumps(lab_options, default=str)
    report['all_lab_users'] = json.dumps(all_lab_users, default=str)
    
    return render(request,"batchUpload.html", {'report': report})

def sampleUploadAjax(request):
    logger.debug('sampleUploadAjax')
    username = str(request.user)  # noqa: F841 (kept: resolves the lazy request.user)
    seekdb = SeekDB(None, None, None)
    seekdb.getSeekLogin(request)
    msg = "Error: File not valid"
    message = ''
    status = 0
    data = {'msg':msg, 'status': status, 'link':''}
    if request.method == "POST":
        if request.FILES and request.FILES.get('excelfile_upload'):
            excelfile = request.FILES['excelfile_upload']
            if excelfile:
                inputfile = excelfile.name
                #logger.debug(inputfile)
                instituion_id = request.POST.get('instituion_id')
                creator_id = request.POST.get('people_id')
                if verifySuperUser(request)==1:
                    #logger.debug(creator_id)
                    try:
                        creator_id = int(creator_id)
                    except:
                        msg = 'Error: You login as admin and must choose the creator.'
                        status = 0
                        logger.error(msg)
                        return json_response(msg, status, message='')
                        
                    if int(creator_id)>0:
                        status, msg = seekdb.updateCreator(instituion_id, creator_id)
                        logger.debug(msg)
                        if not status:
                            logger.error(msg)
                            return json_response(msg, status, message='')
                    else:
                        msg = 'Error: You login as admin and must choose the creator.'
                        status = 0
                        logger.error(msg)
                        return json_response(msg, status, message='')
                
                names = inputfile.split(".")
                n = len(names)
                
                datenow = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
                filename = '.'.join(names[:(n-1)]) + '_feedback-' + datenow + '.xls'
                feedbackfile = DOWNLOAD_DIRECTORY + filename
                link = DOWNLOAD_DIRECTORY_LINK + filename
                logger.debug(feedbackfile)
                
                backupfile = '.'.join(names[:(n-1)]) + '_v' + datenow + '.' + names[-1]
                backupfile = UPLOAD_DIRECTORY + backupfile
                logger.debug(backupfile)
                handle_uploaded_file(excelfile, backupfile)
                
                sample = DBtable_sample()
                msgi, status = sample.batchUpload(excelfile, feedbackfile, seekdb)
                if status:
                    msg = 'Batch sample uploading successful. To find the UIDs for samples uploaded, refer to the feedback excel file: ' + filename
                    message = msg + '\n\n' + msgi
                else:
                    message = msgi
                    terms = msgi.split("<")
                    msg = terms[0] + "<br/><br/>"
                    msg += "Refer to the log and the excel file: " + filename + '.<br/>'
                data = {'msg':msg, 'status': status, 'link':link}
                #logger.debug(message)
            else:
                message = 'Error: Not a valid file from client side'
                data = {'msg':message, 'status': 0, 'link':''}
                logger.error(message)
        else:
            message = 'Error: Not a valid file from client side'
            data = {'msg':message, 'status': 0, 'link':''}
            logger.error(message)
    else:
        message = 'Error: Not a valid http POST request'
        data = {'msg':message, 'status': 0, 'link':''}
        logger.error(message)
                
    data['message'] = plain_text(message)
                
    return HttpResponse(simplejson.dumps(data, default=str))       

def samplesValidate(request):
    logger.debug('samplesValidate')
    username = str(request.user)  # noqa: F841 (kept: resolves the lazy request.user)
    seekdb = SeekDB(None, None, None)
    seekdb.getSeekLogin(request)

    msg = "Error: File not valid"
    message = ''
    status = 0
    data = {'msg':msg, 'status': status, 'link':''}

    if request.method == "POST":
        if request.FILES and request.FILES.get('excelfile_upload'):
            excelfile = request.FILES['excelfile_upload']
            if excelfile:
                # validate

                db = settings.DATABASES[SEEK_DATABASE]
                conn = MySQLdb.connect(host=db['HOST'], user=db['USER'], passwd=db['PASSWORD'], db=db['NAME'])

                df = pd.read_sql(f'''
                    SELECT
                        sa.id AS attribute_id,
                        sa.title AS attribute_title,
                        sa.sample_type_id, st.title AS sample_type_title
                    FROM
                        {db["NAME"]}.sample_attributes sa
                    JOIN
                        {db["NAME"]}.sample_types st ON sa.sample_type_id = st.id
                ''', con=conn)

                df['Instructions'] = df.apply(lambda row: f"{row['sample_type_title']}::{row['attribute_title']}", axis=1)

                # Load the Excel workbook
                workbook = pd.ExcelFile(excelfile)

                logger.debug('Validating Structure of the Assay Sheet:')

                # Validate the number of sheets
                expected_sheets = ['Instructions', 'Samples', 'Ontology', 'Assay']
                actual_sheets = workbook.sheet_names

                if set(expected_sheets) != set(actual_sheets):
                    missing_sheets = set(expected_sheets) - set(actual_sheets)
                    extra_sheets = set(actual_sheets) - set(expected_sheets)  # noqa: F841 (LATENT_BUGS #40)
                    if set(['Instructions', 'Samples', 'Assay']) & missing_sheets:
                        message += f"Missing sheets: {missing_sheets}. Please fix this and reupload sheet."
                        status += 1
                        data = {'msg': message, 'status': status, 'link': ''}
                        data['message'] = plain_text(message)
                        return HttpResponse(simplejson.dumps(data, default=str))       

                    message += "Extra sheets: {extra_sheets}"
                    status += 1
                else:
                    message += "\n\nSheets match what is expected ✅"

                logger.debug('Validating Structure of the Instructions Page:')
                
                # Validate the structure of the Instructions sheet
                instructions_sheet = pd.read_excel(workbook, 'Instructions')
                expected_columns = ['Field', 'Database Field', 'Field Type', 'Ontology']
                actual_columns = instructions_sheet.columns
                
                if set(expected_columns) != set(actual_columns):
                    missing_columns = set(expected_columns) - set(actual_columns)
                    extra_columns = set(actual_columns) - set(expected_columns)
                    message += f"\n\nError in Instructions sheet: Missing columns: {list(missing_columns)}, Extra columns: {list(extra_columns)}"
                    status += 1
                else:
                    message += "\n\nInstructions sheet has correct structure ✅"

                logger.debug('Validating Instructions(Database Field) values to the Database:')

                # Validate that all entries in 'Database Field' exist in the 'Instructions' column of the modified CSV
                # Assuming the modified CSV is already loaded into a DataFrame called 'df'

                df_instructions = df['Instructions'].tolist()
                try:
                    database_field_column = instructions_sheet['Database Field'].tolist()
                except:
                    message = 'Error: No database field column in the Instructions sheet' 
                    data = {'msg':message, 'status': 0, 'link':''}
                    logger.error(message)
                    return HttpResponse(simplejson.dumps(data, default=str))       

                statusChanged = False
                for entry in database_field_column:
                    if entry not in df_instructions:
                        message += f"\n\nError: {entry} in 'Database Field' column does not exist in Database for that Sample Type"
                        if not statusChanged:
                            status += 1
                            statusChanged = True

                if not statusChanged:
                    message += "\n\nAll Database Fields in Instructions sheet match values in database ✅"

                logger.debug('Validating Headers(Samples) to Instructions(Field):')

                # Validate the structure of the Samples sheet
                if 'Samples' not in workbook.sheet_names:
                    message += "\n\nError: 'Samples' sheet does not exist"
                    status += 1

                samples_sheet = pd.read_excel(workbook, 'Samples')
                samples_headers = samples_sheet.columns.tolist()
                field_column = instructions_sheet['Field'].tolist()
                samples_headers.append('Field')

                # Check for mismatches between Samples headers and Instructions 'Field' column
                samples_set = set(samples_headers)
                field_set = set(field_column)
                mismatches = {
                    'missingSamples': samples_set - field_set,
                    'missingInstructions': field_set - samples_set
                }

                if mismatches['missingSamples']:
                    message += "\n\nHeaders in 'Samples' sheet not found in 'Field' column of 'Instructions' sheet: ❌"
                    status += 1
                    for header in mismatches['missingSamples']:
                        message += "\n- " + header
                else:
                    message += "\n\nAll headers in Samples sheet found in Instructions sheet ✅" 

                if mismatches['missingInstructions']:
                    message += f"\n\nValues in 'Field' column of 'Instructions' sheet not found in headers of 'Samples' sheet: ❌"
                    status += 1
                    for value in mismatches['missingInstructions']:
                        message += "\n- " + value
                else:
                    message += "\n\nAll headers in Instructions sheet found in Samples sheet ✅"

                logger.debug('Validating Assay Page Setup:')
                assay_sheet = pd.read_excel(workbook, 'Assay')
                expected_columns = ['SampleType', 'AssayType', 'Assay', 'Direction']
                actual_columns = assay_sheet.columns

                if set(expected_columns) != set(actual_columns):
                    missing_columns = set(expected_columns) - set(actual_columns)
                    extra_columns = set(actual_columns) - set(expected_columns)
                    message += f"\n\nError in Assay Sheet: Missing columns: {list(missing_columns)}, Extra columns: {list(extra_columns)}"
                else:
                    message += "\n\nAssay Sheet columns have correct structure ✅"

                data = {'msg': message, 'status': status, 'link':''}
            else:
                message = 'Error: Not a valid file from client side'
                data = {'msg':message, 'status': 0, 'link':''}
                logger.error(message)
        else:
            message = 'Error: Not a valid file from client side'
            data = {'msg':message, 'status': 0, 'link':''}
            logger.error(message)
    else:
        message = 'Error: Not a valid http POST request'
        data = {'msg':message, 'status': 0, 'link':''}
        logger.error(message)
                
    data['message'] = plain_text(message)
                
    return HttpResponse(simplejson.dumps(data, default=str))       

@requires_seek_login_redirect('/seek/data/upload/', whetherFullInfo=True)
def datafileUpload(request):
    seekdb = request.seekdb
    user_seek = request.user_seek

    isSupervisor = verifySuperUser(request)

    lab_options = seekdb.getObjectsToOptions("/institutions")

    all_lab_users = {}
    for lab in lab_options:
        lab_id = int(lab['id'])
        if lab_id != 0:
            lab_info = seekdb.getInfoObject("/institutions/", lab_id)
            if isSupervisor:
                people = lab_info["relationships"]["people"]["data"]
                all_people = []
                for person in people:
                    all_people.append({'id': person['id'], 'title': seekdb.getUserFullname(person['id'])})
                all_lab_users[lab_id] = all_people
            else:
                all_lab_users = {}
                all_lab_users[lab_id] = [{'id': user_seek['person_id'], 'title': seekdb.getUserFullname(user_seek['person_id'])}]

    report['lab_options'] = json.dumps(lab_options, default=str)
    report['all_lab_users'] = json.dumps(all_lab_users, default=str)
    report['seek_url'] = settings.SEEK_PUBLIC_URL

    return render(request,"dataFileUpload.html", {'report':report})
