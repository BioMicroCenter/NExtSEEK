"""The Container-CC turn pass: issue it, find it, hold the login behind it, revoke it.

A Container-CC turn gets a random one-turn pass instead of the user's password (SPEC-APPROACH-1 piece 1). Only the
pass's sha256 is stored. The user's NExtSEEK login, which Django still needs for its own REST calls and for SEEK,
is held on the turn's row as AES-GCM ciphertext: the key is derived with HKDF-SHA256 from the Django secret key, so
no new secret has to reach the boxes; every row has its own random 96-bit nonce; the task row id and the user id
are the associated data, so a ciphertext copied onto another row does not decrypt. The login is emptied when the
turn ends (``revoke``) and, for a turn whose worker died, by the clean-up every issue runs (``wipe_expired``).

Only Django writes these rows. Never log a raw pass or a login.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from nextseek_api.assistant.models_db import CCTurn

_HKDF_INFO = b"nessie-cc-turn-login"
_NONCE_BYTES = 12
# secrets.token_urlsafe(32): 43 characters of the URL-safe base64 alphabet, no padding.
_PASS_RE = re.compile(r"[A-Za-z0-9_-]{43}")

#: How long after the turn's watchdog deadline its pass still answers.
EXPIRY_GRACE = timedelta(seconds=60)
# Ceiling: applies only until set_deadline runs; a normal pass lives deadline + 60 s (hard max default 180 s, boxes 300 s).
#: The expiry a pass gets at issue, until the engine fixes the watchdog deadline (seconds later). It only bounds a
#: row whose turn died before set_deadline ran.
PROVISIONAL_TTL = timedelta(minutes=15)


class TurnPassError(Exception):
    """No pass can be issued (the server has no secret key), or a turn's login is gone (wiped or unreadable)."""


def _cipher() -> AESGCM:
    try:
        secret = settings.SECRET_KEY
    except ImproperlyConfigured:  # Django raises this for an empty SECRET_KEY
        secret = ""
    if not isinstance(secret, str) or not secret:
        raise TurnPassError("no server secret key is configured")
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(secret.encode("utf-8"))
    return AESGCM(key)


def _associated_data(task_pk: int, user_pk: int) -> bytes:
    return f"cc-turn-login:{task_pk}:{user_pk}".encode("ascii")


def _hash(raw_pass: str) -> str:
    return hashlib.sha256(raw_pass.encode("ascii")).hexdigest()


def wipe_expired() -> int:
    """Revoke every live row past its expiry and empty its login; return how many. One indexed update."""
    now = timezone.now()
    return CCTurn.objects.filter(revoked_at__isnull=True, expires_at__lt=now).update(
        revoked_at=now, login_nonce=None, login_ciphertext=None,
    )


def issue_pass(*, task, chat, user, login: tuple[str, str]) -> tuple[CCTurn, str]:
    """Create the turn's row and return it with the raw pass (the only copy; it goes into the container env).

    Raises TurnPassError, before anything is written, when the server has no secret key.
    """
    cipher = _cipher()
    username, password = login
    if not isinstance(username, str) or not isinstance(password, str):
        raise TypeError("login must be a (username, password) pair of strings")
    wipe_expired()
    raw = secrets.token_urlsafe(32)
    nonce = os.urandom(_NONCE_BYTES)
    plaintext = json.dumps([username, password], ensure_ascii=False).encode("utf-8")
    ciphertext = cipher.encrypt(nonce, plaintext, _associated_data(task.pk, user.pk))
    turn = CCTurn.objects.create(
        task=task, user=user, chat=chat, pass_hash=_hash(raw),
        login_nonce=nonce, login_ciphertext=ciphertext,
        expires_at=timezone.now() + PROVISIONAL_TTL,
    )
    return turn, raw


def set_deadline(turn: CCTurn, deadline_epoch: float) -> None:
    """Record the turn's watchdog deadline; the pass expires EXPIRY_GRACE after it."""
    deadline = datetime.fromtimestamp(float(deadline_epoch), tz=dt_timezone.utc)
    expires = deadline + EXPIRY_GRACE
    CCTurn.objects.filter(pk=turn.pk).update(deadline_at=deadline, expires_at=expires)
    turn.deadline_at, turn.expires_at = deadline, expires


def find_turn(raw_pass: str) -> CCTurn | None:
    """The live row whose hash matches ``raw_pass``, or None (a revoked or expired pass is never returned)."""
    if not isinstance(raw_pass, str) or not _PASS_RE.fullmatch(raw_pass):
        return None
    digest = _hash(raw_pass)
    turn = CCTurn.objects.select_related("task", "user").filter(
        pass_hash=digest, revoked_at__isnull=True, expires_at__gt=timezone.now(),
    ).first()
    if turn is None or not hmac.compare_digest(turn.pass_hash, digest):
        return None
    return turn


def login_for(turn: CCTurn) -> tuple[str, str]:
    """Decrypt the login the turn holds. Raises TurnPassError when it was wiped or does not decrypt."""
    nonce, ciphertext = turn.login_nonce, turn.login_ciphertext
    if not nonce or not ciphertext:
        raise TurnPassError("the turn's login has been wiped")
    try:
        plaintext = _cipher().decrypt(
            bytes(nonce), bytes(ciphertext), _associated_data(turn.task_id, turn.user_id),
        )
    except InvalidTag as exc:
        raise TurnPassError("the turn's login does not decrypt") from exc
    username, password = json.loads(plaintext.decode("utf-8"))
    return str(username), str(password)


def revoke(turn: CCTurn) -> None:
    """End the pass and empty the login. Idempotent: the first revocation time is kept."""
    now = timezone.now()
    CCTurn.objects.filter(pk=turn.pk, revoked_at__isnull=True).update(revoked_at=now)
    CCTurn.objects.filter(pk=turn.pk).update(login_nonce=None, login_ciphertext=None)
    turn.refresh_from_db(fields=["revoked_at", "login_nonce", "login_ciphertext"])
