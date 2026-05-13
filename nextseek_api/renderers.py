from rest_framework.renderers import JSONRenderer


class V1JSONRenderer(JSONRenderer):
    """Renderer for application/vnd.nextseek.v1+json media type."""
    media_type = "application/vnd.nextseek.v1+json"


class V2JSONRenderer(JSONRenderer):
    """Renderer for application/vnd.nextseek.v2+json media type."""
    media_type = "application/vnd.nextseek.v2+json"
