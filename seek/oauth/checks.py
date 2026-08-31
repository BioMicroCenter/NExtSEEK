"""Startup checks for the SEEK OAuth configuration.

Refusing to boot beats a 500 at the callback. Every one of these settings is
only consulted partway through a redirect the user has already left the site
for, so a missing value would otherwise surface as a failed login with a
server-side traceback -- and the operator would be looking at the login flow
instead of at the env file.

All of them are inert while ``SEEK_OAUTH_ENABLED`` is off, which is the
production default, so an unconfigured deployment is not affected.
"""

from django.conf import settings
from django.core.checks import Error, register

REQUIRED_WHEN_ENABLED = (
    ("SEEK_OAUTH_CLIENT_ID", "the client id of the Doorkeeper application registered in SEEK"),
    ("SEEK_OAUTH_CLIENT_SECRET", "the matching client secret"),
    (
        "SEEK_OAUTH_REDIRECT_URI",
        "the callback URL registered in SEEK; Doorkeeper compares it byte for byte",
    ),
    (
        "SEEK_OAUTH_TOKEN_KEYS",
        "at least one Fernet key, or stored tokens cannot be encrypted",
    ),
    ("SEEK_OAUTH_AUTHORIZE_URL", "the browser-facing SEEK authorize endpoint"),
    ("SEEK_OAUTH_TOKEN_URL", "the internal SEEK token endpoint"),
)


@register()
def check_seek_oauth_settings(app_configs, **kwargs):
    if not getattr(settings, "SEEK_OAUTH_ENABLED", False):
        return []

    errors = []
    for name, description in REQUIRED_WHEN_ENABLED:
        if not (getattr(settings, name, "") or "").strip():
            errors.append(
                Error(
                    f"SEEK_OAUTH_ENABLED is on but {name} is empty.",
                    hint=f"Set {name} in docker/nextseek.env: {description}.",
                    id="seek.oauth.E001",
                )
            )
    return errors
