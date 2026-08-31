"""Route models to the ``default`` (NExtSEEK) or ``seek`` (Rails/SEEK) database.

Models carry a ``_DATABASE`` class attribute naming their alias; anything
without one belongs to ``default``. ``seek/models/seek_mirror.py`` sets
``_DATABASE = SEEK_DATABASE`` on all 19 read-only mirrors of Rails-owned tables,
and ``seek/models/nextseek.py`` sets ``_DATABASE = NEXTSEEK_DATABASE`` on the
tables NExtSEEK itself writes.
"""

from django.apps import apps as global_apps


class CustomRouter(object):

    def db_for_read(self, model, **hints):
        return getattr(model, "_DATABASE", "default")

    def db_for_write(self, model, **hints):
        return getattr(model, "_DATABASE", "default")

    def allow_relation(self, obj1, obj2, **hints):
        db_list = ('default')
        return obj1._state.db in db_list and obj2._state.db in db_list

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """Allow a model's migration only on the alias that model lives in.

        The previous implementation tested ``app_label == 'default'``. That can
        never be true: ``'default'`` is a *database alias*, not an app label, so
        the branch was dead and every real migration returned None -- which
        Django's ConnectionRouter treats as "no opinion" and resolves to True,
        allowing every migration on every alias. Because ``manage.py migrate``
        is never invoked with ``--database`` (docker/scripts/entrypoint.sh,
        fabfile.py), the visible consequence was inverted from what you would
        expect: rather than NExtSEEK migrations leaking into the SEEK database,
        the seek_mirror models were created as dead duplicate tables in the
        NExtSEEK database, where nothing reads them.

        Do NOT read ``_DATABASE`` off ``hints["model"]``. Django passes the
        *historical* model, rebuilt from migration state, and ``_DATABASE`` is a
        plain class attribute rather than a Meta option, so it does not survive
        that reconstruction: ``getattr(historical, "_DATABASE", "default")``
        returns "default" for every model in the project and the router would
        silently route everything to ``default`` while looking correct. Resolve
        the real class from the app registry instead.

        Returns None (no opinion, Django's default of True applies) when there
        is no model to consult -- RunSQL/RunPython operations without a model
        hint, and models deleted by a later migration. RunPython in particular
        never consults a router at all; ``seek/migrations/0002`` therefore has
        to gate itself, and does.
        """
        if not model_name:
            return None
        try:
            model = global_apps.get_model(app_label, model_name)
        except LookupError:
            return None
        return getattr(model, "_DATABASE", "default") == db
