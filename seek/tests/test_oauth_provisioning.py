"""Turning a SEEK identity into a Django user.

Two decisions are encoded here that a reader should not have to infer.

**Silent adoption.** An existing account is matched by username and linked, with
no confirmation step. That trusts SEEK login == Django username -- which is not
a new assumption: ``userSynchronization`` already looks accounts up that way
(``dmac/views.py:68``) and mirrors the SEEK password into the row it finds.

**No permission flags on provisioning.** ``userSynchronization`` sets
``is_staff = 1`` on every user at registration and at every login
(``dmac/views.py:80,97``), which is why ``is_superuser`` alone is the admin
predicate across this codebase. The OAuth path does not inherit that, and --
just as importantly -- adoption does not *remove* flags from an account that
already has them. Adoption changes how someone signs in, never what they may do.

``test_adoption_does_not_touch_permission_flags`` is the one to keep: silently
demoting a live account as a side effect of its owner's first OAuth login is
precisely the disruption this project forbids.
"""

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from seek.models.nextseek import SeekOAuthToken
from seek.oauth import provisioning

pytestmark = pytest.mark.django_db(databases=["default", "seek"])

KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


@pytest.fixture(autouse=True)
def _encryption_key(settings):
    settings.SEEK_OAUTH_TOKEN_KEYS = KEY_A


# `seek_person` and `make_seek_user` come from seek/tests/conftest.py, which
# fills every NOT NULL column in the mirrored Rails tables.


# -- new user ----------------------------------------------------------------


def test_a_new_user_is_created_with_the_seek_login_as_the_username(seek_person):
    person_id = seek_person(login="newcomer")
    User.objects.create_superuser(username="someone-else", password="x", email="")

    user, outcome = provisioning.resolve_or_provision(person_id)

    assert outcome == "created"
    assert user.username == "newcomer"
    assert user.first_name == "Ada" and user.email == "ada@example.org"
    assert not user.has_usable_password()


def test_a_new_user_gets_no_permission_flags(seek_person):
    """The deliberate break with userSynchronization, which sets is_staff on
    everyone and thereby makes it useless as a permission."""
    person_id = seek_person(login="newcomer")
    User.objects.create_superuser(username="already-admin", password="x", email="")

    user, _ = provisioning.resolve_or_provision(person_id)

    assert user.is_staff is False
    assert user.is_superuser is False


def test_the_first_user_on_an_empty_instance_becomes_the_superuser(seek_person):
    """Bootstrap: reaching the admin to grant the first superuser requires
    being one, so a fresh instance has to mint it here."""
    person_id = seek_person(login="founder")
    assert not User.objects.filter(is_superuser=True).exists()

    user, outcome = provisioning.resolve_or_provision(person_id)

    assert outcome == "created"
    assert user.is_superuser is True and user.is_staff is True


def test_only_the_very_first_user_is_bootstrapped(seek_person):
    first = seek_person(person_id=1, login="founder")
    provisioning.resolve_or_provision(first)

    second = seek_person(person_id=2, login="latecomer")
    user, _ = provisioning.resolve_or_provision(second)

    assert user.is_superuser is False and user.is_staff is False


# -- adoption ----------------------------------------------------------------


def test_an_existing_user_is_adopted_rather_than_duplicated(seek_person):
    person_id = seek_person(login="veteran")
    User.objects.create_user(username="veteran", password="old-password")

    user, outcome = provisioning.resolve_or_provision(person_id)

    assert outcome == "adopted"
    assert User.objects.filter(username="veteran").count() == 1
    assert user.pk == User.objects.get(username="veteran").pk
    assert not user.has_usable_password()


def test_adoption_does_not_touch_permission_flags(seek_person):
    """Adoption changes credentials, never permissions. Demoting a live account
    as a side effect of its owner's first OAuth login is the disruption this
    project forbids; promoting one would be worse."""
    person_id = seek_person(login="veteran")
    User.objects.create_user(
        username="veteran", password="old", is_staff=True, is_superuser=True
    )

    user, _ = provisioning.resolve_or_provision(person_id)

    assert user.is_staff is True
    assert user.is_superuser is True


def test_adoption_does_not_blank_out_details_seek_omits(seek_person):
    """SEEK having no email on file must not erase one the account already has."""
    person_id = seek_person(login="veteran", email="")
    User.objects.create_user(
        username="veteran", password="old", email="kept@example.org"
    )

    user, _ = provisioning.resolve_or_provision(person_id)

    assert user.email == "kept@example.org"


# -- returning user ----------------------------------------------------------


def test_a_returning_user_is_matched_on_person_id_not_username(seek_person):
    """The token row's seek_person_id is authoritative: SEEK can rename a login
    out from under us, and the account must survive that."""
    person_id = seek_person(login="renamed-in-seek")
    existing = User.objects.create_user(username="original-name", password="x")
    User.objects.create_superuser(username="admin", password="x", email="")
    SeekOAuthToken.objects.create(
        user=existing, seek_person_id=person_id, access_token="at",
        refresh_token="rt",
        access_token_expires_at=timezone.now() + timedelta(hours=1),
    )

    user, outcome = provisioning.resolve_or_provision(person_id)

    assert outcome == "returning"
    assert user.pk == existing.pk
    assert user.username == "original-name"
    assert User.objects.filter(username="renamed-in-seek").count() == 0


# -- refusing to guess -------------------------------------------------------


def test_an_unresolvable_login_refuses_rather_than_inventing_a_username():
    """A wrong guess creates a second account that then owns data -- far harder
    to undo than a failed login."""
    with pytest.raises(provisioning.ProvisioningError):
        provisioning.resolve_or_provision(999)


def test_ambiguous_seek_users_refuse_rather_than_picking_one(make_seek_user):
    for login in ("one", "two"):
        make_seek_user(login, person_id=7)
    with pytest.raises(provisioning.ProvisioningError):
        provisioning.resolve_or_provision(7)


def test_api_attributes_are_a_fallback_when_the_mirror_has_no_login():
    """Used when the mirrored users table cannot answer. Which keys SEEK
    exposes on /people/current is unconfirmed, so several are tried."""
    User.objects.create_superuser(username="admin", password="x", email="")

    user, outcome = provisioning.resolve_or_provision(
        123, {"login": "from-the-api", "first_name": "Grace"}
    )

    assert outcome == "created"
    assert user.username == "from-the-api"
    assert user.first_name == "Grace"
