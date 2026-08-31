"""``CustomRouter.allow_migrate`` must route each model to exactly one alias.

The original implementation was:

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == 'default':
            return db == 'default'
        return None

``'default'`` is a *database alias*, never an app label, so that branch could
not fire. Every real migration returned None, which Django's ConnectionRouter
resolves to True, allowing every migration on every alias.

Because ``manage.py migrate`` is never invoked with ``--database``
(docker/scripts/entrypoint.sh, fabfile.py) and Django's default is ``default``,
the damage ran the opposite way from the obvious guess: instead of NExtSEEK
migrations leaking into the Rails-owned SEEK database, ``seek/0001_initial``
created all 19 seek_mirror models as dead duplicate tables in the *NExtSEEK*
database, where ``db_for_read``/``db_for_write`` guarantee nothing ever reads
them. Both seed dumps show it -- ``dmac.sql.gz`` carries a ``samples`` table
with no ``name_identity`` column, while the real one lives in
``seek_production.sql.gz``.

The trap in the fix, which these tests exist to hold shut: ``hints["model"]``
is the *historical* model rebuilt from migration state. ``_DATABASE`` is a
plain class attribute rather than a Meta option, so it does not survive that
reconstruction, and reading it there yields "default" for every model in the
project -- a router that looks correct in review and silently routes
everything to one alias. ``test_a_historical_model_hint_does_not_fool_the_router``
is the specific regression guard.
"""

from importlib import import_module
from types import SimpleNamespace

import pytest

from seek.dbrouters import CustomRouter
from seek.models.nextseek import Internal_assays
from seek.models.seek_mirror import People, Samples


@pytest.fixture
def router():
    return CustomRouter()


# -- mirror models belong to `seek` and nowhere else -------------------------


@pytest.mark.parametrize("model", [People, Samples])
def test_seek_mirror_models_are_refused_on_the_nextseek_database(router, model):
    """The bug this whole change exists to fix: these are Rails-owned tables and
    a duplicate on `default` is dead weight that nothing can ever read."""
    assert router.allow_migrate("default", "seek", model_name=model._meta.model_name) is False


@pytest.mark.parametrize("model", [People, Samples])
def test_seek_mirror_models_are_allowed_on_the_seek_database(router, model):
    assert router.allow_migrate("seek", "seek", model_name=model._meta.model_name) is True


# -- NExtSEEK-owned models in the same app belong to `default` ---------------


def test_nextseek_owned_models_are_allowed_on_the_nextseek_database(router):
    """`seek` is a mixed app: seek_mirror models live on the `seek` alias and
    nextseek models on `default`. Routing must follow the model, not the app."""
    name = Internal_assays._meta.model_name
    assert router.allow_migrate("default", "seek", model_name=name) is True


def test_nextseek_owned_models_are_refused_on_the_seek_database(router):
    name = Internal_assays._meta.model_name
    assert router.allow_migrate("seek", "seek", model_name=name) is False


# -- models with no _DATABASE fall to `default` -----------------------------


def test_third_party_models_fall_to_the_default_alias(router):
    """auth/sessions/mezzanine models declare no `_DATABASE`."""
    assert router.allow_migrate("default", "auth", model_name="user") is True
    assert router.allow_migrate("seek", "auth", model_name="user") is False


# -- no model to consult means no opinion -----------------------------------


def test_no_model_name_yields_no_opinion(router):
    """RunSQL/RunPython operations arrive without a model. Returning False here
    would silently skip data migrations; None lets Django's default apply."""
    assert router.allow_migrate("default", "seek") is None
    assert router.allow_migrate("seek", "seek", model_name=None) is None


def test_a_model_deleted_by_a_later_migration_yields_no_opinion(router):
    """Historical migrations legitimately reference models the app registry no
    longer has. LookupError must not become a hard failure mid-migrate."""
    assert router.allow_migrate("default", "seek", model_name="a_model_that_never_existed") is None


# -- the regression guard for the fix's own failure mode --------------------


def test_a_historical_model_hint_does_not_fool_the_router(router):
    """A historical model carries no `_DATABASE`, so trusting `hints["model"]`
    would report "default" for a table that belongs on `seek`. The router must
    resolve the real class from the app registry and ignore the hint.

    The stand-in is a bare namespace rather than a real ``models.Model``
    subclass on purpose: declaring one here would register it in Django's app
    registry for the rest of the session. All this needs to be is an object
    without ``_DATABASE``, which is precisely what migration-state
    reconstruction hands the router.
    """
    historical = SimpleNamespace()  # no _DATABASE, as reconstruction produces
    assert router.allow_migrate(
        "default", "seek", model_name="samples", model=historical
    ) is False


# -- the paired gate in seek/0002 -------------------------------------------
#
# RunPython never consults a router, so correcting allow_migrate in isolation
# would break the --no-seed install path: the seek_mirror tables stop being
# created on `default`, 0002's ALTER TABLE samples then hits a missing table,
# and docker/scripts/entrypoint.sh refuses to serve on a failed migrate. These
# assert the gate that closes that hole. They go through django.db.router (the
# ConnectionRouter, which resolves None to True across all configured routers),
# not CustomRouter directly, because that is what the migration calls.


@pytest.fixture
def migration_0002():
    return import_module("seek.migrations.0002_samples_name_identity")


def test_0002_does_not_touch_the_nextseek_database(migration_0002):
    """`samples` is a SEEK-owned mirror table. Before this gate, 0002 was
    adding name_identity to the dead duplicate on the NExtSEEK database -- the
    only alias `manage.py migrate` ever targets -- while the column the code
    actually queries lives on `seek` and arrives there from the seed dump."""
    editor = SimpleNamespace(connection=SimpleNamespace(alias="default"))
    assert migration_0002._targets_this_alias(editor) is False


def test_0002_still_applies_to_the_seek_database(migration_0002):
    """The gate must not make the migration unconditionally dead: pointed at
    the alias `samples` really lives on, it still applies."""
    editor = SimpleNamespace(connection=SimpleNamespace(alias="seek"))
    assert migration_0002._targets_this_alias(editor) is True


# -- read/write routing is unchanged ----------------------------------------


def test_read_and_write_routing_is_untouched(router):
    assert router.db_for_read(Samples) == "seek"
    assert router.db_for_write(Samples) == "seek"
    assert router.db_for_read(Internal_assays) == "default"
    assert router.db_for_write(Internal_assays) == "default"
