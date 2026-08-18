"""Minimal isolated SQLite settings for the Task 6 acceptance replay."""
from __future__ import annotations

SECRET_KEY = "plan018-task6-isolated-only"
INSTALLED_APPS = (
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "nextseek_api.assistant.task6_app.Task6AssistantConfig",
)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
MIGRATION_MODULES = {"nextseek_api": None}
NEXTSEEK_POSTERIOR_ROUTING_ENABLED = True
