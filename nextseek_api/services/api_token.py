"""Self-service NExtSEEK API tokens (#16, sub-project 5).

The missing middle of "remove passwords, use tokens". After cutover, HTTP Basic
against NExtSEEK's own API stops working, and until now nothing issued the DRF
token that replaces it: ``obtain_auth_token`` is routed nowhere, and the token
minted at OAuth login is invisible to the user who owns it. Without this
endpoint, every scripted API consumer would be locked out at cutover with no
recovery that did not involve a DBA.

**Session authentication only, deliberately.** A caller must prove they are the
user *interactively* -- through a browser session established by "Log in with
SEEK" -- before they can read a credential that works unattended. Allowing
``TokenAuthentication`` here would let a leaked token mint its own replacement
and read itself back, turning a single compromised token into a permanent one.
Allowing Basic would reintroduce the password dependency this whole endpoint
exists to remove.

Issuing marks the token self-service, exempting it from logout revocation --
otherwise it would evaporate the moment the user closed the tab that created it.
It is still destroyed if SEEK rejects their refresh token; see
``nextseek_api/local_tokens.py``.
"""

import logging

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from nextseek_api import local_tokens
from nextseek_api.services.assistant import CsrfExemptSessionAuthentication

log = logging.getLogger(__name__)


class ApiTokenViewSet(viewsets.ViewSet):
    """Read, issue/rotate, or revoke the caller's own NExtSEEK API token."""

    # Session only. See the module docstring: a credential that works
    # unattended may only be handed out to a caller who proved themselves
    # interactively.
    authentication_classes = [CsrfExemptSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def list(self, request):
        """Whether the caller has a token, and whether it survives logout.

        The key itself is NOT returned. A DRF token is not recoverable by
        design: it is shown once, at the moment it is issued, so that a
        read-only leak of this endpoint cannot hand over a working credential.
        Someone who has lost their key rotates it.
        """
        key = local_tokens.get_for(request.user)
        return Response(
            {
                "has_token": bool(key),
                "self_service": local_tokens.is_self_service(request.user),
                "detail": (
                    "POST to this endpoint to issue or rotate a token. The key is "
                    "shown once and cannot be read back."
                ),
            }
        )

    def create(self, request):
        """Issue a token, or rotate an existing one. Returns the key once.

        Always rotates rather than returning the existing key, for the same
        reason the key is not readable above: this endpoint must never be a way
        to *retrieve* a credential, only to obtain a new one. Rotation is also
        the honest answer to "I lost my token" and to "my token leaked", which
        are the two reasons anyone calls this twice.
        """
        local_tokens.revoke_for(request.user, force=True)
        key = local_tokens.ensure_for(request.user)
        if not key:
            return Response(
                {"detail": "Could not issue an API token. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        local_tokens.mark_self_service(request.user)
        log.info(
            "api_token: user_id=%s issued a self-service API token via the endpoint",
            request.user.pk,
        )
        return Response(
            {
                "token": key,
                "self_service": True,
                "detail": (
                    "Send this as `Authorization: Token <key>`. It is shown only "
                    "once. It survives logout, and is revoked if your SEEK access "
                    "ends or you revoke it here."
                ),
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["delete"], url_path="revoke")
    def revoke(self, request):
        """Revoke the caller's token.

        A list-route action rather than ``destroy``, because there is nothing to
        address: a user has at most one token and it is always their own. A
        detail route would demand a pk that could only ever be ignored.

        ``force=True`` -- destroying it is the entire point, exemption or not.
        """
        removed = local_tokens.revoke_for(request.user, force=True)
        return Response({"revoked": bool(removed)})
