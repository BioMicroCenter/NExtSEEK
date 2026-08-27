"""Template context shared across the project.

Kept deliberately small: Django settings are not reachable from a template on
their own, and ``mezzanine.conf.context_processors.settings`` exposes only
settings registered through Mezzanine's own registry, not ours.
"""

from django.conf import settings


def seek_urls(request):
    """Browser-facing SEEK URLs.

    ``SEEK_URL`` is the *internal* docker hostname used for server-to-server API
    calls (``http://seek:3000``, and on dev ``http://seek:3000/fairdata``), so it
    is useless in a link a browser has to follow. ``SEEK_PUBLIC_URL`` is the
    browser-reachable one -- https://fairdata.mit.edu on production and
    https://fairdata-dev.mit.edu on dev -- which is why password reset is derived
    from it rather than hard-coded to either.

    Falls back to empty strings when ``SEEK_PUBLIC_URL`` is unset. That is a real
    case, not defensive padding: dmac/settings.py defines it as ``""`` at module
    level so the attribute always exists on env-less hosts, and only assigns the
    real value inside the ``SEEK_HOST`` guard. Templates check for the empty
    string and keep their previous behaviour rather than emitting a dead link.
    """
    base = (getattr(settings, "SEEK_PUBLIC_URL", "") or "").rstrip("/")
    return {
        "seek_public_url": base,
        "seek_forgot_password_url": f"{base}/forgot_password" if base else "",
    }
