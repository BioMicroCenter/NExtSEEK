"""Minimal Django app registration for the isolated Task 6 acceptance DB."""
from __future__ import annotations

from importlib import import_module

from django.apps import AppConfig


class Task6AssistantConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "nextseek_api.assistant"
    label = "nextseek_api"

    def import_models(self) -> None:
        self.models = self.apps.all_models[self.label]
        self.models_module = import_module("nextseek_api.assistant.models_db")
