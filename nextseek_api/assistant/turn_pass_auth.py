"""TurnPassAuthentication: the one place a Container-CC turn pass is checked (SPEC-APPROACH-1 piece 1).

The container sends ``Authorization: NextseekTurn <pass>``. This class answers only that scheme; any other request
falls through to the next authenticator unchanged. Before any view runs it refuses:

* 401 AUTH_FAILED: an unknown, revoked or expired pass; a turn whose task is no longer running; an inactive user;
  a request that also carries another credential (X-SEEK-Authorization, or the Django session cookie; the
  Authorization header itself can hold only one scheme, and a second one in it is malformed);
* 403 PASS_NOT_ALLOWED: a route and method outside ``turn_pass_allow.ALLOW_TABLE``, a failed chat check, or a body
  that sets an admin-only setting;
* 422 VALIDATION: a ``session_id`` on the ops that keep a throwaway session.

The allow table is enforced here, not in a permission class: many viewsets set their own ``permission_classes``,
and a permission added to the defaults would not run on them. On success ``request.user`` is the turn's user and
``request.auth`` is its CCTurn. Nothing here logs the pass. Models are imported inside ``authenticate`` because DRF
imports this module when it builds its default authentication list, which can happen before the app registry is
ready.
"""
from __future__ import annotations

from django.conf import settings
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header

# Fixed sentences per code (spec piece 2): never exception text, never a value from the request. Plan 03's
# nextseek_api/assistant/op_errors.py becomes the single home of these; until then they live here.
_MESSAGES = {
    "AUTH_FAILED": "The turn pass was not accepted.",
    "PASS_NOT_ALLOWED": "This request is not allowed with a turn pass.",
    "VALIDATION": "The request is not valid.",
}


def _envelope(code: str, errors: list[dict] | None = None) -> dict:
    return {"code": code, "reason": None, "message": _MESSAGES[code], "errors": list(errors or [])}


class TurnPassAuthFailed(exceptions.AuthenticationFailed):
    def __init__(self):
        super().__init__()
        # Assigned after __init__ so DRF sends the envelope as given (its detail coercion would turn None into
        # the string "None").
        self.detail = _envelope("AUTH_FAILED")


class PassNotAllowed(exceptions.PermissionDenied):
    def __init__(self):
        super().__init__()
        self.detail = _envelope("PASS_NOT_ALLOWED")


class PassSessionNotAccepted(exceptions.APIException):
    status_code = 422

    def __init__(self):
        super().__init__()
        self.detail = _envelope(
            "VALIDATION", [{"field": "session_id", "type": "not_accepted_with_turn_pass"}],
        )


def _carries_another_credential(request) -> bool:
    if request.META.get("HTTP_X_SEEK_AUTHORIZATION"):
        return True
    # The Django session cookie only: a csrftoken cookie alone is not a credential.
    return bool(request.COOKIES.get(settings.SESSION_COOKIE_NAME))


class TurnPassAuthentication(BaseAuthentication):
    keyword = "NextseekTurn"

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != self.keyword.lower().encode("ascii"):
            return None
        if len(parts) != 2 or _carries_another_credential(request):
            raise TurnPassAuthFailed()
        try:
            raw = parts[1].decode("ascii")
        except UnicodeDecodeError:
            raise TurnPassAuthFailed() from None

        from nextseek_api.assistant import turn_pass, turn_pass_allow

        turn = turn_pass.find_turn(raw)
        if turn is None or not self._live(turn):
            raise TurnPassAuthFailed()
        ok, reason = turn_pass_allow.allowed(request, turn)
        if not ok:
            if reason == turn_pass_allow.REASON_SESSION_NOT_ACCEPTED:
                raise PassSessionNotAccepted()
            raise PassNotAllowed()
        return turn.user, turn

    @staticmethod
    def _live(turn) -> bool:
        """Not revoked, not expired, its task still running as its user, its user active."""
        if turn.revoked_at is not None or turn.expires_at is None or turn.expires_at <= timezone.now():
            return False
        if turn.task.status != "running" or turn.task.user_id != turn.user_id:
            return False
        return bool(turn.user.is_active)

    def authenticate_header(self, request) -> str:
        return self.keyword
