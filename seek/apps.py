from __future__ import unicode_literals

from django.apps import AppConfig


class DataConfig(AppConfig):
    name = 'seek'

    def ready(self):
        # Importing the module is what registers the check; it is inert while
        # SEEK_OAUTH_ENABLED is off. Imported here rather than at module scope
        # because it reads settings.
        from seek.oauth import checks  # noqa: F401
