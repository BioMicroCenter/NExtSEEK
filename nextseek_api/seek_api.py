import requests

from django.conf import settings

SEEK_API_BASE = settings.SEEK_URL
SAMPLES_API_BASE = SEEK_API_BASE + "/samples/"
PEOPLE_API_BASE = SEEK_API_BASE + "/people/"
SOPS_API_BASE = SEEK_API_BASE + "/sops/"

HEADERS = {'Accept': 'application/json'}


def call(auth, url, query_params=None):
    if query_params is None:
        response = requests.get(url,
                                auth=auth,
                                headers=HEADERS)
    else:
        response = requests.get(url,
                                auth=auth,
                                params=query_params,
                                headers=HEADERS)
    try:
        data = response.json()
        print(data)
    except requests.exceptions.JSONDecodeError:
        print("Error: Response content is not valid JSON.")
    except Exception as e:
        print(f"An unexpected error occured: {e}")


def post_call(auth, url, data):
    response = requests.post(url,
                             auth=auth,
                             json=data,
                             headers=HEADERS)
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


