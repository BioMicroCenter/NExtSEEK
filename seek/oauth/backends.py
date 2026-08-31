"""Authentication backend for SEEK OAuth sessions.

Strictly speaking this is optional. ``django.contrib.auth.login`` with an
explicit ``backend=`` string never calls ``authenticate()``, and Mezzanine's
backend -- the only one configured (``dmac/settings.py:58``) -- subclasses
ModelBackend, so its ``get_user`` would deserialise an OAuth session perfectly
well.

It earns its place for two reasons. ``user.backend`` becomes honest about how a
session was established, which matters while both login paths coexist. And
sub-project 5's cutover becomes deleting one entry from
``AUTHENTICATION_BACKENDS`` rather than untangling a path shared with password
login.

It is inert while the feature flag is off. Password logins call
``authenticate(username=..., password=...)``; those land in ``**kwargs``,
``seek_person_id`` is None, and this returns None before Mezzanine's backend is
reached.
"""

import logging

from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.models import User

log = logging.getLogger(__name__)


class SeekOAuthBackend(BaseBackend):
    def authenticate(self, request, seek_person_id=None, **kwargs):
        """Resolve a user from a SEEK person id.

        Only answers calls that name a ``seek_person_id``. Notably it does NOT
        accept a username or an access token: this is not a credential check.
        The callback has already proved possession of a SEEK identity through
        the authorization-code exchange, and this only maps that identity onto
        a local account.
        """
        if seek_person_id is None:
            return None

        from seek.models.nextseek import SeekOAuthToken

        row = (
            SeekOAuthToken.objects.filter(seek_person_id=seek_person_id)
            .select_related("user")
            .first()
        )
        if row is None:
            return None
        if not self.user_can_authenticate(row.user):
            log.info(
                "seek_oauth: seek_person_id=%s maps to inactive django_user_id=%s; "
                "refusing the login.",
                seek_person_id,
                row.user_id,
            )
            return None
        return row.user

    def user_can_authenticate(self, user):
        return bool(getattr(user, "is_active", False))

    def get_user(self, user_id):
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
        return user if self.user_can_authenticate(user) else None
