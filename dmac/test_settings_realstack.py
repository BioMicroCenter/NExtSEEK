"""Real-stack test settings for the native assistant granular-ops work.

Inherits the default (MySQL) settings so the test database is built by the
project's normal, MySQL-targeted migrations on the live seek-mysql — the same
path the project's TestCases were written for. (The shipped ``dmac.test_settings``
points at SQLite, but on this branch a non-nextseek_api migration uses MySQL-only
``CHARACTER SET`` DDL that SQLite cannot parse, so SQLite is not viable here.)

The only thing the normal migration chain gets wrong on a *fresh* DB is the
``assistant_chat_session.extra_state`` column (``0004`` is a state-only
``SeparateDatabaseAndState`` because the column was added to the live dev DB by a
now-rewritten earlier migration). Migration
``0005_chatsession_extra_state_column`` adds that column idempotently, so this
settings module needs no DB or migration overrides.

The granular ops reach real samples / Neo4j / the NExtSEEK REST API through
chat_nextseek's own connections (``config.NEXTSEEK_BASE_URL``,
``config._connect_db``, the Neo4j driver), independent of Django's ``DATABASES`` —
so real-stack provenance assertions remain real even though Django's ORM test DB
is the throwaway ``test_dmac``.

Usage (inside the nextseek container):
    uv run python manage.py test <path> --settings=dmac.test_settings_realstack
"""

from dmac.settings import *  # noqa: F401, F403

# Speed up password hashing for tests.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]
