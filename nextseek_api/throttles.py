"""Request-rate limits for the API and the cost-carrying endpoints.

Counters live in the ``throttle`` cache alias (a file cache shared by every process in the container), so
the default cache, which Mezzanine's page cache uses, is untouched. Rates come from
``REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']`` (env knobs ``DJANGO_THROTTLE_*``), read per instance so a test
can override them.
"""
import hashlib
import ipaddress
import time

from django.conf import settings
from django.contrib.auth import signals
from django.core.cache import caches
from django.core.exceptions import PermissionDenied
from django.dispatch import receiver
from mezzanine.core.auth_backends import MezzanineBackend
from rest_framework.exceptions import Throttled
from rest_framework.settings import api_settings
from rest_framework.throttling import AnonRateThrottle, BaseThrottle, UserRateThrottle


class _LiveRates:
    cache = caches["throttle"]

    def __init__(self):
        self.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES
        super().__init__()


class AnonRate(_LiveRates, AnonRateThrottle):
    scope = "anon"


class _PerUser(_LiveRates, UserRateThrottle):
    def get_cache_key(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return None  # an anonymous call is AnonRate's
        ident = getattr(request.user, "pk", None) or self.get_ident(request)  # a stand-in user has no pk
        return self.cache_format % {"scope": self.scope, "ident": ident}


class UserRate(_PerUser):
    scope = "user"


class TurnStartRate(_PerUser):
    """Starts of a paid agent turn, per user."""
    scope = "turn_start"


class UploadRate(_PerUser):
    """Starts of an upload job, per user."""
    scope = "upload"


# Scoped sets for `@action(throttle_classes=...)`: naming throttle_classes replaces the project default, so the
# default pair is listed too (each counter is still counted once per request).
TURN_THROTTLES = [AnonRate, UserRate, TurnStartRate]
UPLOAD_THROTTLES = [AnonRate, UserRate, UploadRate]


def enforce(request, view, *throttles):
    """Apply scoped throttles by hand, for an alias that forwards to another view's action (DRF runs the
    throttles of the view that was routed, not of the one the action is forwarded to)."""
    for throttle in throttles:
        if not throttle.allow_request(request, view):
            raise Throttled(wait=throttle.wait())


# ---- failed sign-ins: one counter for the login form and for Basic credentials on any API route ----
# Per (client address, username) and, looser, per address. Never a username-only count: anyone could then lock a
# user out. A fixed window from the first failure (the cache's own incr would restart the timeout on each one).

def _login_keys(request, username):
    ip = BaseThrottle().get_ident(request)
    name = hashlib.sha256((username or "").strip().lower().encode()).hexdigest()[:16]
    return f"login-fail:{ip}:{name}", f"login-fail:{ip}"


def _failures(key):
    entry = caches["throttle"].get(key)  # (count, window start)
    return entry[0] if entry else 0


def _is_loopback(ip):
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def login_blocked(request, username):
    """The per-address cap never applies to loopback: the app's own calls to itself (chat self-calls) all come
    from 127.0.0.1, and one user's failures there must not refuse everyone. The per-username key still holds."""
    user_key, ip_key = _login_keys(request, username)
    if _failures(user_key) >= settings.LOGIN_FAILS_PER_USER:
        return True
    return (not _is_loopback(BaseThrottle().get_ident(request))
            and _failures(ip_key) >= settings.LOGIN_FAILS_PER_ADDRESS)


def login_failed(request, username):
    cache = caches["throttle"]
    now = time.time()
    for key in _login_keys(request, username):
        count, start = cache.get(key) or (0, now)
        cache.set(key, (count + 1, start), max(1, int(start + settings.LOGIN_WINDOW_SECONDS - now)))


def login_succeeded(request, username):
    caches["throttle"].delete(_login_keys(request, username)[0])


class LoginThrottleBackend(MezzanineBackend):
    """The project's one auth backend plus a limit: refuses a password check for a source that has failed too
    often. The refusal stops the chain, so Basic credentials on an API route cannot be guessed past the limit.
    (It subclasses the backend instead of sitting in front of it: force_login and sessions use the first one.)"""

    def authenticate(self, request=None, **kwargs):
        username = kwargs.get("username")
        if request is not None and username and login_blocked(request, username):
            raise PermissionDenied("Too many sign-in attempts.")
        return super().authenticate(**kwargs)


@receiver(signals.user_login_failed)
def _count_failed_credentials(sender, credentials, request=None, **kwargs):
    # Only a check that carried a request (Basic auth on the API); the login view counts its own failures.
    if request is not None and not login_blocked(request, credentials.get("username")):
        login_failed(request, credentials.get("username"))
