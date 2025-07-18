# backend/app/utils/helpers.py
from ..core.database import get_db_connection
from mysql.connector import Error
import re
import json
import logging

def execute_query(query, params):
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(query, params)
        result = cursor.fetchall()
        return result
    except Error as e:
        # print(f"Database error: {e}")
        logging.error(f"Database error: {e}")
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

def get_NHP_name(nhp_metadata, img = False):
    # Extract the NHP Name from the fetched metadata
    if nhp_metadata:
        json_metadata = nhp_metadata[0].get('json_metadata', {})
        metadata_dict = json.loads(json_metadata)
        extracted_name = metadata_dict.get('Name')
        if extracted_name:
            extracted_name = extracted_name.strip()
            safe_name = re.escape(extracted_name)
            # search_term = f'\\"?{safe_name}'
            # Modify the search term to include a single double quote before the extracted name
            if img:
                search_term = f'%{safe_name}%'
            else:
                search_term = f'%\"{safe_name}%'
            print(search_term)
            return extracted_name,search_term
        else:
            print("Error: 'Name' not found in JSON metadata.")
            return None
    else:
        print("Error: NHP metadata not found.")
        return None