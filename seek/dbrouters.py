from django.conf import settings


class CustomRouter(object):

    def db_for_read(self, model, **hints):
        return getattr(model, "_DATABASE", "default")

    def db_for_write(self, model, **hints):
        return getattr(model, "_DATABASE", "default")

    def allow_relation(self, obj1, obj2, **hints):
        return obj1._state.db == obj2._state.db

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # SEEK owns the schema behind its alias; Django must not migrate it.
        if db == settings.SEEK_DATABASE and not getattr(settings, "SEEK_ALIAS_MIGRATABLE", False):
            return False
        return None
