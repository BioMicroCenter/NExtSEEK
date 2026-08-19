# backend/app/services/timeline_service.py

# import mysql.connector
import json
import pandas as pd
import time
# import argparse  # Add this import at the top
from ..services.nhp_service import get_timeline_data, fetch_NHP_PAV, fetch_NHP_TIS, fetch_NHP_IMG
# from ..utils.helpers import get_NHP_name
import logging
from datetime import datetime
from typing import Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def convert_to_serializable(obj: Any) -> Any:
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif pd.isna(obj):
        return None
    # Add more conversions if needed
    return obj


##Transform json_metadata to a dataframe            
def transform_json_metadata_to_dataframe(metadata):
    data = []
    # Process each metadata entry
    for entry in metadata:
        # Extract the json_metadata field
        json_metadata = entry['parsed_metadata']
        # Load the JSON data
        try:
            metadata_dict = json_metadata #json.loads(json_metadata)
        except json.JSONDecodeError:
            print("Error decoding JSON")
            metadata_dict = {}
        # Append the dictionary to the data list
        data.append(metadata_dict)
    # Convert the list of dictionaries into a DataFrame
    df = pd.DataFrame(data)
    # Drop columns that are completely empty or contain only empty strings
    df = df.dropna(axis=1, how='all')  # Drop columns where all values are NaN
    df = df.loc[:, (df != '').any(axis=0)]  # Drop columns where all values are empty strings
    return df

##########################
# PAV functions
##########################

##runs json_metadata transformation for PAV
def PAV_to_dataframe(all_data):
    """
    Fetches and transforms NHP PAV metadata into a DataFrame.

    Args:
        all_data (list): The metadata for the NHP.

    Returns:
        pd.DataFrame: A DataFrame containing the transformed PAV metadata.
    """
    try:
        # Fetch the NHP metadata
        PAV_Metadata = fetch_NHP_PAV(all_data)
        # Transform the json_metadata into a DataFrame
        return transform_json_metadata_to_dataframe(PAV_Metadata)
    except Exception as e:
        print(f"Error processing PAV metadata: {e}")
        return pd.DataFrame()  # Return an empty DataFrame on error


##format treatments for the treatment track
def process_treatments(all_data):
    # Fetch the data for NHP_Name
    df = PAV_to_dataframe(all_data)
    # Sort the DataFrame by 'Name'
    df = df.sort_values(by='Name')
    # Filter rows where 'Type' contains 'Treatment'
    df = df[df['Type'].str.contains('Treatment', na=False)]
    # Define the function to expand treatment columns
    def expand_treatments(df):
        new_rows = []
        # Iterate over each row in the original DataFrame
        for _, row in df.iterrows():
            # Iterate over treatment columns 1 to 5
            for i in range(1, 6):
                treatment_col = f'Treatment{i}'
                if pd.notna(row.get(treatment_col, None)):
                    new_row = {
                        'UID': row.get('UID', None),
                        'Name': row.get('Name', None),
                        'SampleCreationDate': row.get('SampleCreationDate', None),
                        'Treatment': row.get(treatment_col, None),
                        'TreatmentType': row.get(f'Treatment{i}Type', None),
                        'TreatmentRoute': row.get(f'Treatment{i}Route', None),
                        'TreatmentDose': row.get(f'Treatment{i}Dose', None),
                        'TreatmentDoseUnits': row.get(f'Treatment{i}DoseUnits', None),
                        'TreatmentParent': row.get(f'Treatment{i}Parent', None),
                    }
                    # Append the new row to the list
                    new_rows.append(new_row)
        # Create a new DataFrame from the list of new rows
        expanded_df = pd.DataFrame(new_rows)
        # Reset index
        expanded_df.reset_index(drop=True, inplace=True)
        # Drop rows where 'Treatment' is NaN or blank
        expanded_df = expanded_df[expanded_df['Treatment'].str.strip() != '']
        expanded_df['Name'] = expanded_df['Name'].str.split('-').str[0]
        # Drop unwanted columns
        columns_to_drop = ['Scientist', 'Type', 'Procedure', 'Notes']
        expanded_df.drop(columns=[col for col in columns_to_drop if col in expanded_df.columns], inplace=True)
        return expanded_df
    # Call the function to expand treatments
    expanded_df = expand_treatments(df)
    return expanded_df

#process visits for visit track
def process_visits(all_data):
    df = PAV_to_dataframe(all_data)
    if df.empty:
        print(f"No PAV data found.")
        return
    columns_to_keep = ["UID", "Name","SampleCreationDate", "Protocol", "Type"]
    for col in columns_to_keep:
        if col not in df.columns:
            df[col] = pd.NA  # Initialize missing columns with NA or a default value
    # Select only the required columns
    df = df.sort_values(by='Name')
    df['Name'] = df['Name'].str.split('-').str[0]
    df = df[columns_to_keep]
    return df

def edit_treatment_datafile(all_data):
    # Extract the NHP ID from the file path
    # Get and process the DataFrame
    df = process_treatments(all_data)
    if df.empty:
        print(f"No treatment data found.")
        return
    # Define the header for the treatment data file
    headers = [
        "PATIENT_ID", "START_DATE", "STOP_DATE", 
        "EVENT_TYPE", "UID", "TREATMENT", "TYPE", 
        "ROUTE", "DOSE", "DOSE_UNITS", "TREATMENT_PARENT", "PARENT"
    ]
    # Create START_DATE and STOP_DATE columns from SampleCreationDate
    df['START_DATE'] = df['SampleCreationDate'].astype(str)
    df['STOP_DATE'] = df['SampleCreationDate'].astype(str)
    # Map columns to new header names
    df.rename(columns={
        'Name': 'PATIENT_ID',
        'Treatment': 'TREATMENT',
        'TreatmentType': 'TYPE',
        'TreatmentRoute': 'ROUTE',
        'TreatmentDose': 'DOSE',
        'TreatmentDoseUnits': 'DOSE_UNITS',
        'TreatmentParent': 'TREATMENT_PARENT'
    }, inplace=True)
    # Add the EVENT_TYPE column
    df['EVENT_TYPE'] = 'TREATMENT'
    df['PARENT'] = df['UID']
    # Reorder columns to match the header
    df = df[headers]
    print(f"Updated treatment data file")
    return df

def edit_visits_datafile(all_data):
    # nhp_id = extract_id_from_raw_df(raw_df)
    df = process_visits(all_data)
    if df.empty:
        print(f"No visit data found.")
        return
    # Define the header for the treatment data file
    headers = [
        "PATIENT_ID", "START_DATE", "STOP_DATE", 
        "EVENT_TYPE", "UID", "TYPE","PROTOCOL"
    ]
    # Create START_DATE and STOP_DATE columns from SampleCreationDate
    df['START_DATE'] = df['SampleCreationDate'].astype(str)
    df['STOP_DATE'] = df['SampleCreationDate'].astype(str)
    df['EVENT_TYPE'] = 'PAV'
    df['Type'] = 'PAV'
    df.rename(columns={
        'Name': 'PATIENT_ID',
        'Protocol': 'PROTOCOL',
        'Type': 'TYPE'
    }, inplace=True)
        # Reorder columns to match the header
    df = df[headers]
    return df

##########################
# TIS functions
##########################

###runs json_metadata transformation for TIS
def TIS_to_dataframe(all_data):
    """
    Fetches and transforms NHP TIS metadata into a DataFrame.

    Args:
        all_data (list): The metadata for the NHP.

    Returns:
        pd.DataFrame: A DataFrame containing the transformed TIS metadata.
    """
    try:
        TIS_Metadata = fetch_NHP_TIS(all_data)
        # Transform the json_metadata into a DataFrame
        return transform_json_metadata_to_dataframe(TIS_Metadata)
    except Exception as e:
        print(f"Error processing TIS metadata: {e}")
        return pd.DataFrame()  # Return an empty DataFrame on error

## processes tissues for tissue track
def process_tissues(all_data):
    df = TIS_to_dataframe(all_data)
    if df.empty:
        print(f"No TIS data found.")
        return
    df = df.sort_values(by='Name')
    df['PATIENT_ID'] = df['Name'].str.split('-').str[0]
    columns_to_drop = ['Scientist', 'Protocol', 'Publish_uri', 'ArrayNumber','CFUUnits']
    df.drop(columns=[col for col in columns_to_drop if col in df.columns], inplace=True)
    df.loc[~df['Type'].isin(['Blood', 'Bronchoalveolar Lavage']), 'Type'] = 'Tissue Extraction'
    df['Type'] = df['Type']
    return df

def edit_specimen_datafile(all_data):
    df = process_tissues(all_data)
    if df.empty:
        print(f"No specimen data found.")
        return
    
    headers = ["PATIENT_ID", "START_DATE", "STOP_DATE", 
               "EVENT_TYPE", "SAMPLE_ID", "UID", "TYPE","PARENT", "ORGAN", "ORGAN_DETAIL", "CFU"]
    # Remove duplicate columns if any
    df = df.loc[:, ~df.columns.duplicated()]
    # Create START_DATE and STOP_DATE columns from SampleCreationDate
    df['START_DATE'] = df['SampleCreationDate'].astype(str)
    df['STOP_DATE'] = df['SampleCreationDate'].astype(str)
    # Map columns to new header names
    df.rename(columns={
        'Name': 'SAMPLE_ID',
        'Type': 'TYPE',
        'Parent':"PARENT",
        'Organ': 'ORGAN',
        'OrganDetail': 'ORGAN_DETAIL'
    }, inplace=True)
    # Add the EVENT_TYPE column
    df['EVENT_TYPE'] = 'TIS'
    # Ensure all required columns are present
    existing_columns = df.columns.intersection(headers)
    df = df[existing_columns]
    # Reindex DataFrame to match the headers, filling missing columns with empty strings
    df = df.reindex(columns=headers, fill_value='')
    df = df.sort_values(by='START_DATE')
    return df

##########################
# IMG functions
##########################

def IMG_to_dataframe(all_data):
    """
    Fetches and transforms NHP IMG metadata into a DataFrame.

    Args:
        all_data (list): The metadata for the NHP.
        NHP_name (str): The UID of the NHP.
    Returns:
        pd.DataFrame: A DataFrame containing the transformed IMG metadata.
    """
    try:
        IMG_Metadata = fetch_NHP_IMG(all_data)
        # print(IMG_Metadata)
        if IMG_Metadata is None:
            print(f"No IMG data found.")
            return pd.DataFrame()
        # Transform the json_metadata into a DataFrame
        return transform_json_metadata_to_dataframe(IMG_Metadata)
    except Exception as e:
        print(f"Error processing IMG metadata: {e}")
        return pd.DataFrame()  # Return an empty DataFrame on error


## processes images for image track
def process_images(all_data):
    """
    Processes image data for the image track by converting it into a DataFrame.

    Args:
        all_data (list): The metadata for the NHP.
    Returns:
        pd.DataFrame: A DataFrame containing processed image data, or None if an error occurs.
    """
    try:
        # Convert image data to DataFrame
        df = IMG_to_dataframe(all_data)
        print(f"Imaging data: {df}")
        if df.empty:
            print(f"No imaging data found.")
            return
        else:
            print(f"Processing Imaging data.")
        
        # Define columns to keep and initialize missing columns with default values
        columns_to_keep = ["UID", "File_PrimaryData", "Link_PrimaryData", "Type", "SampleCreationDate", "Parent"]
        for col in columns_to_keep:
            if col not in df.columns:
                df[col] = pd.NA  # Initialize missing columns with NA or a default value
        
        # Select only the required columns
        df = df[columns_to_keep]
        
        # Parse the JSON string into a Python dictionary
        json_metadata_dict = all_data[0]['parsed_metadata']
        # print(list(json_metadata_dict.keys()))
        
        # Extract the 'Name' value from the parsed dictionary
        if 'Name' in json_metadata_dict:
            name_value = json_metadata_dict['Name']
        elif 'File_PrimaryData' in json_metadata_dict:
            name_value = json_metadata_dict['File_PrimaryData'].split("_")[1]
        else:
            name_value = ""
        
        # Assign 'PATIENT_ID' to the DataFrame
        df['PATIENT_ID'] = name_value
        df['Type'] = df['Type']
        print(f"Successfully processed imaging data.")
        
        return df

    except KeyError as e:
        print(f"KeyError: Missing key {e} in metadata.")
    except json.JSONDecodeError:
        print("Error decoding JSON metadata.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    return pd.DataFrame()


def get_parent_date(df, record, all_data):
    """
    Extracts the START_DATE from the parent of a given record.

    This function searches for the parent UID of the specified record within the provided
    DataFrame and retrieves the corresponding START_DATE of the parent.

    Args:
        df (pd.DataFrame): The DataFrame containing records with 'UID', 'PARENT', and 'START_DATE' columns.
        record (Any): The UID of the record whose parent's start date needs to be extracted.

    Returns:
        Any: The START_DATE of the parent record if found; otherwise, None.
    """
    try:
        # Locate the parent UID for the given record
        print(f"Retrieving date for {record}")
        parent_uid = df[df["UID"] == record]["Parent"].iloc[0]
        print(f"Parent UID: {parent_uid}")
        if not parent_uid:
            logger.error(f"Record with UID '{record}' not found.")
            return datetime.today()

        # parent_uid = parent_series.iloc[0]

        if parent_uid.startswith("PAV"):
            parent_df = process_visits(all_data)
        elif parent_uid.startswith("TIS"):
            parent_df = process_tissues(all_data)
        
        # Locate the START_DATE for the parent UID
        parent_date_series = parent_df[parent_df["UID"] == parent_uid]["SampleCreationDate"]
        if parent_date_series.empty:
            logger.error(f"Parent record with UID '{parent_uid}' not found.")
            return datetime.today()

        parent_date = parent_date_series.iloc[0]
        print(f"New date for {record} is: {parent_date}")
        return parent_date

    except Exception as e:
        logger.error(f"Error retrieving parent date for UID '{record}': {e}")
        return datetime.today()

def edit_imaging_datafile(all_data):
    # Extract the NHP ID from the file path
    # nhp_id = extract_id_from_raw_df(raw_df)
    # Get and process the DataFrame
    df = process_images(all_data)
    if df.empty:
        print(f"No imaging data found.")
        return
    
    # check if SampleCreationDate is empty or NaT, if so, get the parent date
    for i in range(len(df)):
        if pd.isna(df["SampleCreationDate"].iloc[i]) or df["SampleCreationDate"].iloc[i] == "" or df["SampleCreationDate"].iloc[i].lower() == "nat":
            df.loc[i, "SampleCreationDate"] = get_parent_date(df, df["UID"].iloc[i], all_data)

    # Define the header for the imaging data file
    headers = [
        "PATIENT_ID", "START_DATE", "STOP_DATE", 
        "EVENT_TYPE","TYPE","NAME", "UID", "LINK", "PARENT"
    ]
    # Create START_DATE and STOP_DATE columns from SampleCreationDate
    df['START_DATE'] = df['SampleCreationDate'].astype(str)
    df['STOP_DATE'] = df['SampleCreationDate'].astype(str)
    # Handle NaT (Not a Time) for empty or missing SampleCreationDate
    df['START_DATE'] = df['START_DATE']#.replace('', pd.NaT)
    df['STOP_DATE'] = df['STOP_DATE']#.replace('', pd.NaT)
    df['EVENT_TYPE'] = 'D.IMG'
    # Map columns to new header names
    df.rename(columns={
        'File_PrimaryData': 'NAME',
        'Link_PrimaryData': 'LINK',
        'Type': 'TYPE',
        'UID': 'UID',
        'PATIENT_ID': 'PATIENT_ID',
        'Parent': 'PARENT'
    }, inplace=True)
    # Reorder columns to match the header
    df = df[headers]
    print(f"Updated imaging data file")
    return df

##########################
# Study Design functions
##########################

def edit_studydesign_datafile(all_data):
    # Define the vlookup dictionary for study design notes
    study_design_notes_dict = {
        "LibP": "Date of Primary MTB Infection",
        "LibS": "Date of Secondary MTB Infection",
        "Nx": "Date of NHP Sacrifice",
        "Depletion": "Date of Antibody Depletion",
        "Antibiotics": "Date of first Antibiotics Treatment",
        "Vaccination": "Date of BCG Vaccination",
        "SIV": "Date of SIV Infection",
        "RITUX_Start": "Date of Rituximab start",
        "RITUX_End": "Date of Rituximab end",
        "MTB": "Date of MTB Infection"
    }
    # Fetch the metadata using the extracted ID
    if not all_data:
        print("No NHP metadata found.")
        return
    # Define the header for the study design data file
    headers = [
        "UID","PATIENT_ID", "START_DATE", "STOP_DATE", 
        "EVENT_TYPE", "TYPE", "STUDY_DESIGN_NOTES", "PARENT"
    ]
    # Extract the required fields from nhp_metadata
    metadata = all_data[0] if all_data else {}
    json_metadata = metadata.get('parsed_metadata', {})
    name = json_metadata.get('Name', '')
    study_design_str = json_metadata.get('StudyDesign', '')
    uid = json_metadata.get('UID', '')
    # Parse the StudyDesign field
    study_design_entries = study_design_str.split(';')
    study_design_entries = [entry.strip() for entry in study_design_entries if entry.strip()]
    # Create content for the file
    content_lines = []
    for entry in study_design_entries:
        if ':' in entry:
            event_type, date = entry.split(':', 1)
            event_type = event_type.strip()
            date = str(date).strip()
            # Skip rows with "NaT" in the date
            if date.lower() == "nat" or not date:
                continue
            # Get study design notes
            notes = study_design_notes_dict.get(event_type, "")
            content_lines.append([uid,name, date, date, "STUDY_DESIGN", event_type, notes, uid])

    df = pd.DataFrame(data = content_lines, columns = headers)
    return df

##########################
# General functions & functions to combine data
##########################

def update_all_datafiles(input_list, all_data):
    new_dict = {}
    for replacement in input_list:
        try:
            # Call the specific function based on the file type
            if replacement == "imaging":
                new_dict[replacement] = edit_imaging_datafile(all_data)
            elif replacement == "treatment":
                new_dict[replacement] = edit_treatment_datafile(all_data)
            elif replacement == "studydesign":
                new_dict[replacement] = edit_studydesign_datafile(all_data)
            elif replacement == "specimen":
                new_dict[replacement] = edit_specimen_datafile(all_data)
            elif replacement == "visits":
                new_dict[replacement] = edit_visits_datafile(all_data)
            else:
                print(f"Unknown replacement type: {replacement}")
        except Exception as e:
            print(f"Error updating data file {replacement} : {e}")
    return new_dict

def generate_combined_object(events: dict, nhp_id: str):
    logger.info("Starting to generate combined JSON object.")
    
    # Step 1: Add nhp_id to each dataframe
    for df in events.values():
        df['nhp_id'] = nhp_id
    
    # Step 2: Get the union of all column names from all dataframes
    all_columns = set()
    for df in events.values():
        all_columns.update(df.columns)
    
    # Step 3: Create a list to hold all JSON objects
    combined_objects = []
    skipped_records = 0
    
    # Step 4: Iterate through each dataframe in the specified order and each row to create JSON objects
    order = ["visits", "treatment", "studydesign", "imaging", "specimen"]
    for key in order:
        df = events.get(key)
        if df is not None:
            for index, row in df.iterrows():
                # Convert each field to a serializable format
                row_dict = {
                    col: convert_to_serializable(row[col]) if col in df.columns else None 
                    for col in all_columns
                }
                try:
                    # Attempt to serialize the record
                    json.dumps(row_dict)
                    combined_objects.append(row_dict)
                except (TypeError, OverflowError) as e:
                    logger.error(f"Skipping record at index {index} due to serialization error: {e}")
                    skipped_records += 1
    
    logger.info(f"Total records to be saved: {len(combined_objects)}")
    logger.info(f"Total records skipped: {skipped_records}")

    return combined_objects

def get_event_data(nhp_uid, event_type, date):

    print(f"Getting event data for {nhp_uid} on {event_type} on {date}")
    start_time = time.time()

    nhp_metadata = get_timeline_data(nhp_uid)
    if not nhp_metadata:
        print(f"No NHP metadata found.")
        return []
    
    processed_data = []
    
    if event_type == "TIS":
        df = edit_specimen_datafile(nhp_metadata)
    elif event_type == "D.IMG":
        df = edit_imaging_datafile(nhp_metadata)
    elif event_type == "TREATMENT":
        df = edit_treatment_datafile(nhp_metadata)
    elif event_type == "STUDY_DESIGN":
        df = edit_studydesign_datafile(nhp_metadata)
    else:
        print(f"Unsupported event type: {event_type}")
        return []
    
    if df.empty:
        print(f"No data found for event type: {event_type}")
        return []
    
    try:
        # convert input date to similar format as df["START_DATE"]
        # from datetime import datetime

        # Parse the input date string to a datetime object
        try:
            input_date = datetime.strptime(date, '%Y-%m-%d').date()
        except ValueError as ve:
            logger.error(f"Incorrect date format for input date '{date}': {ve}")
            return []

        # Convert the "START_DATE" column to datetime.date objects
        df['START_DATE'] = pd.to_datetime(df['START_DATE'], errors='coerce').dt.date

        filtered_df = df.loc[(df['EVENT_TYPE'] == event_type) & (df['START_DATE'] == input_date)]
        # convert date back to string
        filtered_df.loc[:, "START_DATE"] = filtered_df["START_DATE"].astype(str) 
        print(filtered_df)
        # Convert the filtered DataFrame to a list of dictionaries
        filtered_objects = filtered_df.to_dict(orient='records')
        
        # Wrap each dictionary in its own list and append to processed_data
        for obj in filtered_objects:
            processed_data.append(obj)
        
        print(f"Processed data: {processed_data}")
    except Exception as e:
        print(f"Error getting event data: {e}")
        return []

    end_time = time.time()
    print(f"Total time to get event data: {end_time - start_time:.2f} seconds")
    return processed_data

## Run all function, aka my version of __MAIN__
## All you need to do is run 1) Gen Files and 2) Update_all_Files
def run_All(NHP_uid):
    start_time = time.time()
    nhp_metadata = get_timeline_data(NHP_uid) 
    # print(nhp_metadata)

    # Define the output directory and filename
    # output_dir = "./app/api/data/"
    # output_file_path = os.path.join(output_dir, filename)
    input_list = ["imaging", "treatment", "studydesign", "specimen", "visits"]

    # Generate data files
    myevents = update_all_datafiles(input_list, nhp_metadata)
    
    # Save combined JSON
    combined_json = generate_combined_object(myevents, NHP_uid)
    
    end_time = time.time()
    print(f"Total time for run_All: {end_time - start_time:.2f} seconds")
    print("\n")
    return combined_json

# if __name__ == "__main__":
#     parser = argparse.ArgumentParser(description="Process NHP metadata and generate JSON output.")
#     parser.add_argument("--NHP_name", type=str, default="NHP-220630FLY-15", help="The NHP name to process.")
#     parser.add_argument("--filename1", type=str, default="nhp_data.json", help="The output JSON filename.")
#     parser.add_argument("--event_type", type=str, default="TIS", help="The event type to process.")
#     parser.add_argument("--date", type=str, default="2019-05-06", help="The date to process.")
#     parser.add_argument("--filename2", type=str, default="event_data.json", help="The output JSON filename.")
    
#     args = parser.parse_args()
#     run_All(args.NHP_name)
#     get_event_data(args.NHP_name, args.event_type, args.date)