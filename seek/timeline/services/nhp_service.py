# backend/app/services/nhp_service.py

from ..core.database import execute_query
import json
from typing import List
from ..models.schemas import NHPInfo
from pydantic import ValidationError
import logging

logger = logging.getLogger(__name__)
cache = {}

def fetch_NHP(term):
    """
    Fetch the NHP metadata for a given term.

    Args:
        term (str): The UUID to search for in the database.

    Returns:
        list: A list of results from the database query.
    """
    try:
        query = """
        SELECT uuid, json_metadata
        FROM seek_production.samples
        WHERE uuid = %s;
        """
        nhp_metadata = execute_query(query, (term,))
        return nhp_metadata
    except Exception as e:
        logger.error(f"Error fetching NHP metadata: {e}")
        return None

def fetchChildren(term: str) -> List[dict]:
    """
    Fetch the children metadata for a given term from the database.

    Args:
        term (str): The UUID of the sample to search for in the database.

    Returns:
        List[dict]: A list of dictionaries containing the children metadata.
                    Returns None if an error occurs during the query.
    """
    try:
        query = """
        SELECT uuid, full 
        FROM dmac.seek_sample_tree
        WHERE uuid = %s;
        """
        children_metadata = execute_query(query, (term,), 'DB_NAME1')
        # print(children_metadata)
        return children_metadata
    except Exception as e:
        logger.error(f"Error fetching NHP metadata: {e}")
        return None

def fetch_all_descendants(term: str) -> List[str]:
    """
    Fetch all descendants of a given sample.

    Args:
        term (str): The UUID of the sample to search for in the database.

    Returns:
        list[str]: A list of all descendant UUIDs.
    """
    try:
        # Fetch the children metadata
        children_metadata = fetchChildren(term)
        if not children_metadata:
            return []

        # Parse the 'full' JSON structure
        full_structure = json.loads(children_metadata[0]['full'])

        # Recursive function to collect all descendants
        def collect_descendants(node):
            descendants = []
            if 'children' in node:
                for child in node['children']:
                    descendants.append(child['id'])
                    descendants.extend(collect_descendants(child))
            return descendants

        # Start collecting from the root node
        all_descendants = collect_descendants(full_structure[0])
        # print(all_descendants)
        return all_descendants

    except Exception as e:
        logger.error(f"Error fetching all descendants: {e}")
        return []


def fetchAllMetadata(term: str, filter: List[str] = None) -> List[dict]:
    """
    Fetch all metadata for a given term and its descendants.

    Args:
        term (str): The UUID of the sample to search for in the database.
        filter (List[str], optional): A list of strings to filter the UUIDs.

    Returns:
        List[dict]: A list of metadata dictionaries for the term and its descendants.
    """
    try:
        # Fetch all descendant UUIDs
        descendants_uuids = fetch_all_descendants(term)
        if filter:
            # Apply filter to the descendant UUIDs
            descendants_uuids = [child for child in descendants_uuids if any(f in child for f in filter)]
        
        # Add the original term to the list of UUIDs
        descendants_uuids.append(term)
        descendants_uuids = tuple(descendants_uuids)
        
        if not descendants_uuids:
            logger.warning("No descendants found for the given term.")
            return []

        # Prepare the SQL query with placeholders
        placeholders = ",".join(["%s"] * len(descendants_uuids))
        query = f"""
        SELECT uuid, json_metadata
        FROM seek_production.samples
        WHERE uuid IN ({placeholders});
        """
        
        # Execute the query
        all_metadata = execute_query(query, descendants_uuids, 'DB_NAME')
        logger.info(f"Fetched metadata for {len(all_metadata)} entries.")
        return all_metadata

    except Exception as e:
        logger.error(f"Error fetching metadata for term {term}: {e}")
        return []


def get_timeline_data(nhp_name):
    if not nhp_name or not isinstance(nhp_name, str):
        logger.error(f"Invalid NHP name provided: {nhp_name}")
        return []

    logger.debug(f"Using cache key: {nhp_name}")
    if nhp_name in cache:
        logger.debug(f"Cache hit for key: {nhp_name}")
        return cache[nhp_name]
    else:
        logger.debug(f"Cache miss for key: {nhp_name}, fetching data.")

    all_data = fetchAllMetadata(nhp_name, filter=["PAV", "TIS", "D.IMG"])

    # Parse JSON once here
    for entry in all_data:
        # if name in entry["json_metadata"]["Name"]:
        entry['parsed_metadata'] = json.loads(entry['json_metadata'])
    
    uuids = [entry['uuid'] for entry in all_data if "FLY" in entry["parsed_metadata"]["UID"]]

    # Convert set back to list if needed
    uuids = list(uuids)
    print(nhp_name in uuids)
    
    # print(uuids[:5])
    print(len(uuids))

    print(len(all_data))
    nhp_name = nhp_name.strip()
    cache[nhp_name] = all_data
    logger.debug(f"Data for {nhp_name} added to cache.")
    return all_data


def fetch_NHP_PAV(all_data):
    """
    Retrieves the NHP PAV metadata from all_data.

    Args:
        all_data : parsed json_metadata.

    Returns:
        list: A list of results from the database query.
    """
    try:
        results = [entry for entry in all_data if 'PAV' in entry['uuid'] and 'FLY' in entry['uuid']]
        return results
    except Exception as e:
        logger.error(f"Error fetching NHP PAV metadata: {e}")
        return []

def fetch_NHP_TIS(all_data):
    """
    Fetch the NHP TIS metadata.

    Args:
        all_data : parsed json_metadata.

    Returns:
        list: A list of results from the database query.
    """
    try:
        results = [entry for entry in all_data if 'TIS' in entry['uuid'] and 'FLY' in entry['uuid']]
        return results
    except Exception as e:
        logger.error(f"Error fetching NHP TIS metadata: {e}")
        return None

def fetch_NHP_IMG(all_data):
    """
    Fetch the NHP IMG metadata.

    Args:
        all_data : parsed json_metadata.
        NHP_name : the UID of the NHP.
    Returns:
        list: A list of results from the database query.
    """
    try:
        results = [entry for entry in all_data if 'D.IMG' in entry['uuid'] and 'FLY' in entry['uuid']]
        uuids = [entry['uuid'] for entry in results if len(results) > 0]
        print(uuids)
        return results
    except Exception as e:
        logger.error(f"Error fetching NHP IMG metadata: {e}")
        return None

def save_nhp_info_to_json(nhp_uid) -> List[NHPInfo]:
    """
    Save NHP metadata to a JSON file.

    Args:
        nhp_uid (str): The UID of the NHP.

    Returns:
        list: The NHP information saved to the file, or None if an error occurred.
    """
    nhp_uid = str(nhp_uid).strip()
    nhp_metadata = get_timeline_data(nhp_uid)
    print(nhp_metadata[:5])
    if not nhp_metadata:
        logger.error("No metadata found.")
        return []
    
    for entry in nhp_metadata:
        # if name in entry["json_metadata"]["Name"]:
        entry['parsed_metadata'] = json.loads(entry['json_metadata'])

    processed_data = []

    print(nhp_metadata[0])

    try:
        uuids = [entry['uuid'] for entry in nhp_metadata]
        print(uuids)
        idx = uuids.index(nhp_uid)
        # print(idx)
        metadata_dict = nhp_metadata[idx]['parsed_metadata']
        logger.info(metadata_dict)
    except json.JSONDecodeError:
        print("Error decoding JSON")
        metadata_dict = {}
        # Process each record and convert to NHPInfo
    try:
        processed_data.append(metadata_dict)
    except ValidationError as ve:
        # Log the validation error and skip the record
        logger.error(f"Validation error: {ve}")

    logger.info(processed_data)
    # Export data to JSON
    try:
        return processed_data
    except Exception as e:
        logger.error(f"Error saving NHP information to JSON: {e}")
        return []