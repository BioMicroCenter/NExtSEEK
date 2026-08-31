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
        # Before the cutover this was the production default and meant "keep
        # using passwords". After it (#16, sub-project 5) there is no password
        # path left to fall back to, so booting with the flag off would serve a
        # login page with no way to sign in and an admin with no way to fix it.
        # Refusing to start is the only outcome an operator can act on.
        return [
            Error(
                "SEEK_OAUTH_ENABLED is off, but password login no longer exists.",
                hint=(
                    "Sub-project 5 removed userSynchronization, the session "
                    "password, and the password form, so SEEK OAuth is the only "
                    "way in. Set SEEK_OAUTH_ENABLED=1 in docker/nextseek.env."
                ),
                id="seek.oauth.E002",
            )
        ]

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
