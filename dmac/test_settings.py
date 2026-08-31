"""
Test settings that use SQLite in-memory database.
Usage: uv run python manage.py test --settings=dmac.test_settings ...

SchemaGenerator and product-seam tests mount this module via
``DJANGO_SETTINGS_MODULE=dmac.test_settings`` (see OPS-TESTING-HARNESSES.md §3.4a).
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
SAMPLE_TEMPLATES_FOLDER = "/templates"
SAMPLE_TEMPLATES_FOLDER_PROJECT = "1"
PUBLISH_STATS_FILE = "/path/to/published_stats.xlsx"
SMART_SEARCH_URL = ""

# SEEK OAuth (issue #16). Disabled by default so the suite exercises the same
# password path production runs; tests that need the OAuth path flip it with
# override_settings. dmac/settings.py derives the two endpoint URLs from
# SEEK_PUBLIC_URL/SEEK_URL at import time, before this module reassigns
# SEEK_URL above, so both are stated outright here rather than inherited.
SEEK_OAUTH_ENABLED = False
SEEK_OAUTH_CLIENT_ID = "test-client-id"
SEEK_OAUTH_CLIENT_SECRET = "test-client-secret"
SEEK_OAUTH_REDIRECT_URI = "https://nextseek.test/oauth/seek/callback"
SEEK_OAUTH_SCOPE = "read write"
SEEK_OAUTH_AUTHORIZE_URL = "http://seek-public:3000/oauth/authorize"
SEEK_OAUTH_TOKEN_URL = "http://seek:3000/oauth/token"
# A fixed, deliberately non-secret Fernet key: EncryptedTextField encrypts on
# every save, so the suite cannot store a token row without one. Never reuse
# this anywhere real -- it is bytes 0..31, and it is in version control.
SEEK_OAUTH_TOKEN_KEYS = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
SEEK_OAUTH_REVOKE_ON_LOGOUT = False
SEEK_OAUTH_HTTP_TIMEOUT = 10
