"""Resolve, adopt, or create the Django user behind a SEEK OAuth login.

Three cases, in the order they are tried.

**Returning user** -- a token row already carries this ``seek_person_id``. That
link is authoritative over the username, which SEEK can rename out from under
us, so it is checked first.

**Existing user, first OAuth login: silent adoption.** Matched by username, the
token row is attached and the Django password is made unusable. No confirmation
step. The mapping being trusted -- SEEK login name equals Django username -- is
not a new assumption: ``userSynchronization`` already looks accounts up by
``User.objects.get(username__exact=...)`` (``dmac/views.py:68``) and mirrors the
SEEK password into that row, so production has been running on it for years. An
association flow would guard against a username collision the current system is
already fully exposed to, at the cost of a UI and a pending-link state that
sub-project 5 would then delete. Every adoption is logged.

Clearing the password does not strand an adopted user while sub-projects 2-4
are unfinished and the password path is still the only one reaching SEEK's API.
``login_seek`` fails ``authenticate()`` against the unusable hash, falls into
its ``else`` branch, and ``userSynchronization`` calls ``set_password()`` from
the already-SEEK-validated credentials and re-authenticates
(``dmac/views.py:136-145``, ``:94``). The password path repairs itself; the
hash simply oscillates until sub-project 5 removes that path.

**New user** -- created with the SEEK login as the username, and **no
permission flags**. This is a deliberate break with ``userSynchronization``,
which sets ``is_staff = 1`` on every user at registration and again at every
login (``dmac/views.py:80,97``). That is why ``is_superuser`` alone is the admin
predicate everywhere in this codebase (``nextseek_api/views.py:266-272``,
``permissions.py:10``, ``services/cc_assistant.py:368``,
``services/sample_types.py:29``): ``is_staff`` admits everyone and so means
nothing. The OAuth path does not inherit that. Grants are made by hand in the
Mezzanine admin, except for the bootstrap below.

Identity details come from the mirrored ``users`` and ``people`` tables rather
than from the API response. Those tables are SEEK's own and always carry a
login; which attributes ``/people/current`` exposes varies by version, so the
API is the fallback, not the source.
"""

import logging

from django.contrib.auth.models import User
from django.db import transaction

from seek.models.seek_mirror import People, Users

log = logging.getLogger(__name__)

# Adoptions are auditable on their own logger so the first flag-on window can be
# reviewed in isolation: who was linked to which SEEK identity, and when.
adopt_log = logging.getLogger("seek.oauth.adopt")


class ProvisioningError(Exception):
    """The SEEK identity could not be turned into a Django user."""


def resolve_or_provision(seek_person_id, attributes=None):
    """Return ``(user, outcome)`` for a SEEK person who has just authenticated.

    ``outcome`` is one of "returning", "adopted", or "created", for the caller
    to log and for tests to assert on.
    """
    attributes = attributes or {}

    existing = _user_by_person_id(seek_person_id)
    if existing is not None:
        return existing, "returning"

    login_name = _seek_login(seek_person_id) or _attribute_login(attributes)
    if not login_name:
        # Refuse rather than invent a username: a wrong guess here creates a
        # duplicate account that owns data, which is far harder to undo than a
        # failed login.
        raise ProvisioningError(
            f"SEEK person {seek_person_id} has no resolvable login name, so no "
            f"NExtSEEK account can be matched or created for them."
        )

    profile = _seek_profile(seek_person_id, attributes)

    with transaction.atomic():
        user = User.objects.filter(username=login_name).first()
        if user is not None:
            return _adopt(user, login_name, seek_person_id, profile), "adopted"
        return _create(login_name, seek_person_id, profile), "created"


# -- the three cases ---------------------------------------------------------


def _adopt(user, login_name, seek_person_id, profile):
    """Attach an existing account to its SEEK identity.

    Permission flags are pointedly NOT touched. Adoption changes how someone
    authenticates; it must not change what they are allowed to do. Removing a
    live account's is_staff as a side effect of its owner's first OAuth login is
    exactly the disruption this project forbids -- cleaning up the legacy
    universal is_staff is sub-project 5's call, on its own evidence.
    """
    user.set_unusable_password()
    _apply_profile(user, profile)
    user.save()

    adopt_log.info(
        "adopted django_user_id=%s username=%s -> seek_person_id=%s seek_login=%s",
        user.pk,
        user.username,
        seek_person_id,
        login_name,
    )
    return user


def _create(login_name, seek_person_id, profile):
    user = User(username=login_name, is_staff=False, is_superuser=False, is_active=True)
    user.set_unusable_password()
    _apply_profile(user, profile)

    # Bootstrap: an instance with no superuser at all has no way to grant the
    # first one through the admin, because reaching the admin requires being one.
    # The first person through the door on a fresh instance therefore becomes it,
    # and hands out every later grant by hand.
    #
    # Two simultaneous first-ever logins could in principle both win this. The
    # outcome is two admins on an empty instance, which is recoverable and not
    # worth a lock; a race that only exists before the instance has any users is
    # not a race worth engineering against.
    if not User.objects.filter(is_superuser=True).exists():
        user.is_staff = True
        user.is_superuser = True
        log.warning(
            "seek_oauth: no superuser existed, so the first SEEK login (%s, "
            "seek_person_id=%s) has been made one. Every later grant is manual, "
            "through the admin.",
            login_name,
            seek_person_id,
        )

    user.save()
    log.info(
        "seek_oauth: created django_user_id=%s username=%s for seek_person_id=%s",
        user.pk,
        user.username,
        seek_person_id,
    )
    return user


# -- identity lookups --------------------------------------------------------


def _user_by_person_id(seek_person_id):
    # Imported here rather than at module scope: seek.models.nextseek imports
    # seek.oauth.crypto, so a top-level model import in this package risks a
    # cycle during app loading.
    from seek.models.nextseek import SeekOAuthToken

    row = (
        SeekOAuthToken.objects.filter(seek_person_id=seek_person_id)
        .select_related("user")
        .first()
    )
    return row.user if row is not None else None


def _seek_login(seek_person_id):
    """The SEEK login name, from SEEK's own users table.

    The inverse of ``SeekDB.__getSeekPersonID`` (``seek/seekdb.py:236``), which
    already maps a login to a person id through this table.
    """
    logins = list(
        Users.objects.filter(person_id=seek_person_id)
        .values_list("login", flat=True)[:2]
    )
    if len(logins) != 1:
        # Zero is a person with no user account; more than one is a SEEK data
        # problem. Either way, guessing which is wrong.
        log.warning(
            "seek_oauth: seek_person_id=%s matched %d rows in SEEK's users "
            "table; cannot resolve a login name from it.",
            seek_person_id,
            len(logins),
        )
        return None
    return (logins[0] or "").strip() or None


def _attribute_login(attributes):
    """Fallback: a login carried in the /people/current attributes, if any.

    Which keys SEEK 1.15.1 exposes here is unconfirmed, so several plausible
    ones are tried. This is a fallback for the case where the mirrored users
    table is unreachable, not the primary path.
    """
    for key in ("login", "username", "user_login"):
        value = (attributes.get(key) or "").strip()
        if value:
            return value
    return None


def _seek_profile(seek_person_id, attributes):
    """Name and email, preferring SEEK's people table over the API response."""
    person = People.objects.filter(id=seek_person_id).first()
    if person is not None:
        return {
            "first_name": (person.first_name or "").strip(),
            "last_name": (person.last_name or "").strip(),
            "email": (person.email or "").strip(),
        }
    return {
        "first_name": (attributes.get("first_name") or "").strip(),
        "last_name": (attributes.get("last_name") or "").strip(),
        "email": (attributes.get("email") or "").strip(),
    }


def _apply_profile(user, profile):
    """Copy across only what SEEK actually gave us.

    Empty values are skipped rather than written: SEEK omitting an email must
    not blank out one the Django account already has.
    """
    for field in ("first_name", "last_name", "email"):
        value = profile.get(field)
        if value:
            setattr(user, field, value[: User._meta.get_field(field).max_length])
