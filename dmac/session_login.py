"""The SEEK password held in a Django session, encrypted.

The session row used to keep the plain SEEK password, so anyone who could read ``django_session`` (a database dump,
a backup) read every signed-in user's password. It is now AES-GCM ciphertext under a key derived from the Django
secret key, bound to the SEEK username. The cipher is the turn pass's (``nextseek_api.assistant.turn_pass``) with its
own key-derivation label, so there is one copy of the crypto.
"""
import base64
import os

from cryptography.exceptions import InvalidTag

_INFO = b"nextseek-session-password"
_KEY = "seek_password_enc"


def store_seek_password(session, username, password):
    """Keep the password in ``session`` as ciphertext and drop any plain copy."""
    from nextseek_api.assistant.turn_pass import _cipher  # lazy: it imports models

    nonce = os.urandom(12)
    sealed = _cipher(_INFO).encrypt(nonce, password.encode("utf-8"), str(username).encode("utf-8"))
    session[_KEY] = base64.b64encode(nonce + sealed).decode("ascii")
    session.pop("password", None)


def seek_password(session, username):
    """The SEEK password, or None when absent, plain (legacy, erased here) or not decryptable for ``username``."""
    if "password" in session:
        session.pop("password", None)
        return None
    from nextseek_api.assistant.turn_pass import TurnPassError, _cipher  # lazy: it imports models

    value = session.get(_KEY)
    if not isinstance(value, str) or not isinstance(username, str):
        return None
    try:
        raw = base64.b64decode(value, validate=True)
        return _cipher(_INFO).decrypt(raw[:12], raw[12:], username.encode("utf-8")).decode("utf-8")
    except (InvalidTag, ValueError, TurnPassError):
        return None
