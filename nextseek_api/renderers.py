"""Per-version JSON renderers for the vendor media-type versioning scheme.

Each renderer advertises a vendor-specific Content-Type so DRF returns the
correct media type to clients that requested it via `Accept`. Wired into
`REST_FRAMEWORK.DEFAULT_RENDERER_CLASSES` in `dmac/settings.py`.
"""
from rest_framework.renderers import JSONRenderer


class V1JSONRenderer(JSONRenderer):
    """Renderer for application/vnd.nextseek.v1+json media type."""
    media_type = "application/vnd.nextseek.v1+json"


class V2JSONRenderer(JSONRenderer):
    """Renderer for application/vnd.nextseek.v2+json media type."""
    media_type = "application/vnd.nextseek.v2+json"
