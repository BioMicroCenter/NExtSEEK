"""
Test settings that use SQLite in-memory database.
Usage: uv run python manage.py test --settings=dmac.test_settings ...

Plan 018 V4-2 Lane C product-seam tests mount this module via
``DJANGO_SETTINGS_MODULE=dmac.test_settings`` (see evidence/plan018-v4-2-lane-c.sidecar.json).
"""

from dmac.settings import *  # noqa: F401, F403

# Override databases to use SQLite for tests
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
    "seek": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
}

# Speed up password hashing for tests
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Required by nextseek_api.services.assistant at import time (normally in local_settings.py)
ASSISTANT_PARTICIPATING_PROJECTS = set(["1"])
TEST_CASES = {
    "test_case_1": {
        "question_name": {
            "prompt": "Example prompt",
            "reasoning": "Example reasoning",
            "output": "",
        }
    },
}

# Lane C HTTP cross tests touch chat config / ledger paths that read these attrs.
SECRET_KEY = "plan018-lane-c-test-secret"
NEO4J_DATABASE = {
    "NAME": "neo4j",
    "URI": "neo4j://127.0.0.1",
    "AUTH": ("neo4j", "lane-test"),
}
SEEK_URL = "http://seek:3000"
PUBLISH_URL = SEEK_URL
SEEK_HOSTNAME = "127.0.0.1:8000"
SEEK_SERVER = "seek"
SEEK_JS_URL = "seek"
SERVER_IPADDRESS = "127.0.0.1:8000"
VIRTUOSO_URL = "http://seek:8890/sparql/"
VIRTUOSO_JS_URL = "http://seek:8890/sparql"
SEEK_DATAFILE_ROOT = "/tmp/seek-data"
SEEK_DATAFILE_ROOT_WEBLINK = "/uploads/"
SEEK_DATAFILE_SERVER = "http://localhost"
SAMPLE_TEMPLATES_FOLDER = "/templates"
SAMPLE_TEMPLATES_FOLDER_PROJECT = "1"
PUBLISH_STATS_FILE = "/path/to/published_stats.xlsx"
SMART_SEARCH_URL = ""
