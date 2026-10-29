"""Shared DRF permission classes for nextseek_api."""

from rest_framework.permissions import BasePermission

from nextseek_api.authentication import _error_response


class IsSuperUser(BasePermission):
    """Gate an endpoint to true superusers only.

    Deliberately NOT ``rest_framework.permissions.IsAdminUser``: that checks
    ``is_staff``, and accounts made before the clear-staff migration (users API and the old
    SEEK login) carry ``is_staff`` without being admins. Nothing sets it now, and
    /admin/ is superuser-only, but ``is_superuser`` stays the one admin signal.

    Same predicate as ``seek.views.verifySuperUser``, in DRF shape.
    """

    message = "Superuser access required."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(
            user
            and getattr(user, "is_authenticated", False)
            and getattr(user, "is_superuser", False)
        )

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)


def may_read_any_users_data(user) -> bool:
    """True when ``user`` may read records belonging to somebody else.

    The per-session assistant endpoints are ownership-scoped: they answer only
    for the caller's own sessions, which is right for the chat UI and useless
    for an operator diagnosing a report from another account. Superusers are
    let past that scoping so a support request can be answered without shelling
    into the box.

    ``is_superuser`` alone, for the reason given on :class:`IsSuperUser`:
    old accounts still carry ``is_staff`` without being admins (the users API no
    longer sets it), so an ``is_staff`` test here would widen these endpoints.
    """
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_superuser", False)
    )


def is_turn_pass(request) -> bool:
    """True when ``request`` was authenticated by a Container-CC turn pass (``request.auth`` is its CCTurn)."""
    from nextseek_api.assistant.models_db import CCTurn  # lazy: this module is imported before models load

    return isinstance(getattr(request, "auth", None), CCTurn)


def may_read_any(request) -> bool:
    """``may_read_any_users_data`` for a request, and never under a turn pass.

    A pass acts as its turn's user inside that turn's chat (operator ruling 2026-09-28): an admin's pass keeps the
    admin's own data reach (graph and search scope) and loses reading other users' chats, tasks, bundles and
    artifacts. Every read-any check goes through here
    (nextseek_api/tests/repo_guards/test_read_any_goes_through_the_request.py).
    """
    if is_turn_pass(request):
        return False
    return may_read_any_users_data(getattr(request, "user", None))


def plan_mode_refused(request, mode) -> bool:
    """True when ``request`` may not start a ``mode="plan"`` turn (SEC-002).

    The chat panel never sends plan; the Container-CC ``nextseek-plan`` op does, through its turn pass, for any
    user. So plan is for a superuser or a turn pass. The planner's model-written code runs in the sandbox either way.
    """
    if mode != "plan" or is_turn_pass(request):
        return False
    return not IsSuperUser().has_permission(request, None)


def require_session_owner(request, session, *, admin_read: bool = False):
    """The 403 response for a chat the caller does not own, else None.

    ``admin_read`` lets a reader of any user's data (``may_read_any``) through, for the read endpoints.
    """
    if session.user_id == request.user.pk or (admin_read and may_read_any(request)):
        return None
    return _error_response("Forbidden", "You do not own this session.", 403)
