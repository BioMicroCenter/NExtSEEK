"""
Test settings that use SQLite in-memory database.
Usage: uv run python manage.py test --settings=dmac.test_settings ...

SchemaGenerator and product-seam tests mount this module via
``DJANGO_SETTINGS_MODULE=dmac.test_settings`` (see OPS-TESTING-HARNESSES.md §3.4a).
"""

import os
import tempfile

# dmac.settings creates LOG_DIR at import time and defaults it to /app/logs, the
# container path. Anywhere else (a GitHub runner, a clean checkout) that is a
# PermissionError before a single test runs. Point it somewhere writable first;
# an explicit LOG_DIR in the environment still wins.
os.environ.setdefault("LOG_DIR", os.path.join(tempfile.gettempdir(), "nextseek-test-logs"))

from dmac.settings import *  # noqa: E402, F401, F403

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

# HTTP / schema tests touch chat config / ledger paths that read these attrs.
SECRET_KEY = "nextseek-test-secret"
NEO4J_DATABASE = {
    "NAME": "neo4j",
    "URI": "neo4j://127.0.0.1",
    "AUTH": ("neo4j", "test"),
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
PUBLISH_STATS_FILE = "/path/to/published_stats.xlsx"
SMART_SEARCH_URL = ""

# Static files are never collected in a test run, so there is no manifest and no
# hashed copies on disk. Any manifest-backed storage therefore raises on the
# first {% static %} a template renders -- 49 of the rendering tests, measured
# 2026-09-04. Hashing is a deployment concern (see dmac/storage.py); tests want
# the plain name.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}
