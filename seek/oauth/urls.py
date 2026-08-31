"""SEEK OAuth routes, mounted at ``^oauth/seek/`` from ``dmac/urls.py``.

Registered unconditionally. The views 404 when ``SEEK_OAUTH_ENABLED`` is off,
rather than the urlconf being built behind the flag, so both states are
reachable in one test process and "this surface is not exposed in production"
can be asserted instead of assumed.

``callback`` must reverse to exactly the ``SEEK_OAUTH_REDIRECT_URI`` registered
in SEEK's Doorkeeper application -- it is compared byte for byte.
"""

from django.urls import path

from seek.oauth import views

urlpatterns = [
    path("login", views.seek_login, name="seek_oauth_login"),
    path("callback", views.seek_callback, name="seek_oauth_callback"),
]
