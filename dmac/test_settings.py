"""
Test settings that use SQLite in-memory database.
Usage: uv run python manage.py test --settings=dmac.test_settings ...
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
