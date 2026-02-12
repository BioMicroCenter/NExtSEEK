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
