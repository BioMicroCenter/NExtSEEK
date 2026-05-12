"""ASGI entry point for NExtSEEK.

Handles both HTTP and WebSocket protocols.
Run with: daphne -b 0.0.0.0 -p 8001 dmac.asgi:application
"""

import os

from django.core.asgi import get_asgi_application
from mezzanine.utils.conf import real_project_name

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "%s.settings" % real_project_name("dmac"),
)

# Must call get_asgi_application() BEFORE importing channels routing,
# so that Django apps are loaded first.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.auth import AuthMiddlewareStack  # noqa: E402
from nextseek_api.assistant.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})
