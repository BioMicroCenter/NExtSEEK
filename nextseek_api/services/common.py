"""Service-layer shared helpers for v2 error translation."""
from nextseek_api.errors import translate_error_response_v2


def maybe_v2_error(resp, request):
    """Pass resp through translate_error_response_v2 if request is v2 & 4xx/5xx."""
    return translate_error_response_v2(resp, request)
