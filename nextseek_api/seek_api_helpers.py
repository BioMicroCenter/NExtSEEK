import requests
from typing import Tuple

from django.conf import settings
from seek.seekdb import SeekDB
from seek.dbtable_sample import DBtable_sample

from nextseek_api.helpers import basic_auth_header


SEEK_API_BASE = settings.SEEK_URL
PEOPLE_API_BASE = SEEK_API_BASE + "/people/"
SOPS_API_BASE = SEEK_API_BASE + "/sops/"

# HEADERS = {'Accept': 'application/json'}
HEADERS = {'Accept': 'application/vnd.api+json'}


def convert_sample_uid_to_id(sample_uid: str) -> int:
    """
    Convert a sample UID to a sample ID.
    """
    dbsample = DBtable_sample()
    sample_id = dbsample.getSampleID(sample_uid)
    return sample_id


def get_current_logged_in_user(request) -> Tuple[str, str]:
    """
    Get the current logged in user to pass in authorization for API request.

    Args:
        request: The request object.

    Returns:
        auth: A tuple of (username, password) for the current user.
    """
    seekdb = SeekDB(None, None, None)
    user = seekdb.getSeekLogin(request, False)
    auth = (user['username'], user['password'])
    return auth


def call(auth, url, query_params=None):
    """
      Send a GET request to the given
      url and return the response as
      json.

      auth should be in the format:
        Tuple: ('username', 'password')
    """
    # Basic auth is encoded here rather than passed as requests' auth= so a
    # non-Latin-1 password doesn't raise UnicodeEncodeError. See
    # nextseek_api.helpers.basic_auth_header.
    headers = {**HEADERS, **basic_auth_header(auth)}
    if query_params is None:
        response = requests.get(url,
                                headers=headers)
    else:
        response = requests.get(url,
                                params=query_params,
                                headers=headers)
    try:
        data = response.json()
        return data
    except requests.exceptions.JSONDecodeError:
        print("Error: Response content is not valid JSON.")
    except Exception as e:
        print(f"An unexpected error occured: {e}")


def post_call(auth, url, data):
    """
      Send a POST request to the given
      url with the payload 'data' and
      return the response as json.

      auth should be in the format:
        Tuple: ('username', 'password')
    """
    response = requests.post(url,
                             json=data,
                             headers={**HEADERS, **basic_auth_header(auth)})
    try:
        data = response.json()
        return data
    except requests.exceptions.JSONDecodeError:
        print("Error: Response content is not valid JSON.")
    except Exception as e:
        print(f"An unexpected error occured: {e}")


def fetch_current_user(auth):
    try:
        call(auth, PEOPLE_API_BASE + "/current")
    except Exception as e:
        print(f"An unexpected error occured: {e}")


def list_sops(auth):
    try:
        return call(auth, SOPS_API_BASE)
    except Exception as e:
        print(f"An unexpected error occured: {e}")


def get_sop(auth, id):
    try:
        return call(auth, SOPS_API_BASE + str(id))
    except Exception as e:
        print(f"An unexpected error occured: {e}")
