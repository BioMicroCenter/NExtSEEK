import requests

from django.conf import settings

from nextseek_api.helpers import basic_auth_header

SEEK_API_BASE = settings.SEEK_URL
SAMPLES_API_BASE = SEEK_API_BASE + "/samples/"
PEOPLE_API_BASE = SEEK_API_BASE + "/people/"
SOPS_API_BASE = SEEK_API_BASE + "/sops/"

HEADERS = {'Accept': 'application/json'}


def call(auth, url, query_params=None):
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
        print(data)
    except requests.exceptions.JSONDecodeError:
        print("Error: Response content is not valid JSON.")
    except Exception as e:
        print(f"An unexpected error occured: {e}")


def post_call(auth, url, data):
    response = requests.post(url,
                             json=data,
                             headers={**HEADERS, **basic_auth_header(auth)})
    try:
        data = response.json()
        print(data)
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
        call(auth, SOPS_API_BASE)
    except Exception as e:
        print(f"An unexpected error occured: {e}")


def get_sop(auth, id):
    try:
        call(auth, SOPS_API_BASE + str(id))
    except Exception as e:
        print(f"An unexpected error occured: {e}")


