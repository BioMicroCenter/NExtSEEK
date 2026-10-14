"""Shared DRF authentication class and error envelope for the nextseek_api ViewSets.

Moved verbatim out of ``nextseek_api/services/assistant.py`` (NessieAI consolidation,
Phase B step B1). ``nextseek_api.services.assistant`` re-exports both names, so every
existing ``from nextseek_api.services.assistant import ...`` keeps working and returns
the same objects.
"""

from __future__ import annotations

from rest_framework.authentication import SessionAuthentication
from rest_framework.response import Response


class CsrfExemptSessionAuthentication(SessionAuthentication):
    """SessionAuthentication for the nextseek_api ViewSets.

    Session logins pass Django's CSRF check, exactly like DRF's default
    SessionAuthentication: browser calls send the ``X-CSRFToken`` header.
    Basic and token callers that send no session cookie never reach this
    check. A Basic caller that also sends a ``sessionid`` cookie authenticates
    by session and so needs the token. The class name is kept because imports
    and an isinstance check refer to it.
    """


def _error_response(title: str, detail: str, http_status: int) -> Response:
    """Return a NExtSEEK-convention error response."""
    return Response(
        {"errors": [{"title": title, "detail": detail}]},
        status=http_status,
    )
