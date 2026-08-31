"""Encryption at rest for stored SEEK OAuth tokens.

Today NExtSEEK keeps the user's SEEK *password* in the Django session in
plaintext (``dmac/views.py:127-128``). Storing OAuth tokens encrypted is
strictly better than that, but it is not free: a durable secret has to live
somewhere, and losing it has to be survivable.

Two decisions follow from that.

**The key is its own setting, not derived from ``DJANGO_SECRET_KEY``.**
Rotating the Django secret is a routine operation; it must not invalidate every
stored SEEK token as a side effect.

**A row that will not decrypt reads as ``None``, never as an exception.** A
missing key, a key retired too early, or a corrupted column yields ``None``,
which ``get_valid_access_token`` treats exactly like a user who has no token
row: send them through the login flow again. Key loss therefore costs everyone
one re-login rather than taking the site down. A misconfiguration -- no keys at
all -- is the opposite case and does raise, because that is an operator error
to fix, not a user state to recover from.

``SEEK_OAUTH_TOKEN_KEYS`` is a comma-separated list of urlsafe-base64 Fernet
keys. The first encrypts; all of them are tried for decryption. Rotation is
therefore: generate a key, prepend it, restart, and let rows re-encrypt as they
refresh. Drop the old key only once no row can still be carrying it -- in
practice, once every refresh token predating the rotation has expired.

Generate one with::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

log = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def _build_fernet(keys_csv):
    """Build a MultiFernet from the comma-separated key list.

    Cached on the setting's *value* rather than on nothing, so that
    ``override_settings`` in tests and a key rotation across a restart both get
    a correctly rebuilt cipher instead of a stale one.
    """
    keys = [k.strip() for k in keys_csv.split(",") if k.strip()]
    if not keys:
        raise ImproperlyConfigured(
            "SEEK_OAUTH_TOKEN_KEYS is empty. It must list at least one "
            "urlsafe-base64 Fernet key; the first is used to encrypt and all "
            "are tried when decrypting. Generate one with: python -c "
            '"from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            "SEEK_OAUTH_TOKEN_KEYS contains a value that is not a valid "
            "Fernet key (32 urlsafe-base64-encoded bytes)."
        ) from exc


def _fernet():
    return _build_fernet(getattr(settings, "SEEK_OAUTH_TOKEN_KEYS", "") or "")


def encrypt(plaintext):
    """Encrypt a token for storage. Raises ImproperlyConfigured if unkeyed."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext):
    """Decrypt a stored token, or return None if it cannot be read.

    ImproperlyConfigured propagates: an unkeyed deployment is an operator error
    and must be loud. Everything else -- a token encrypted under a key no longer
    in the list, a truncated or corrupted column -- is a recoverable per-row
    condition that returns None so the user is simply asked to log in again.
    """
    fernet = _fernet()
    try:
        return fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        log.warning(
            "seek_oauth: a stored token could not be decrypted with any "
            "configured key; treating it as absent. The owning user will be "
            "asked to log in again. If this is widespread, a key was probably "
            "retired from SEEK_OAUTH_TOKEN_KEYS too early."
        )
        return None


class EncryptedTextField(models.TextField):
    """A TextField whose value is Fernet-encrypted at rest.

    Not queryable. ``get_prep_value`` encrypts with a fresh nonce every call, so
    ``filter(access_token=...)`` can never match and equality lookups against
    this column are meaningless. That is fine for its only use -- tokens are
    always read via the owning user's row -- but it is why no index is declared
    on either token column.
    """

    description = "Text, Fernet-encrypted at rest"

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None:
            return None
        return encrypt(value)

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        return decrypt(value)
