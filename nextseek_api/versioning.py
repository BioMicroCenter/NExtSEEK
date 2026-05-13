import re

from rest_framework.versioning import BaseVersioning

_VENDOR_RE = re.compile(r'application/vnd\.nextseek\.(v\d+)\+json', re.IGNORECASE)


class VendorMediaTypeVersioning(BaseVersioning):
    """Extract API version from vendor media type in Accept header.

    Maps application/vnd.nextseek.{version}+json -> version string.
    Falls back to DEFAULT_VERSION for application/json or no Accept header.
    Sets version on the underlying wsgi_request so resp.wsgi_request.version works.
    """

    def determine_version(self, request, *args, **kwargs):
        accept = request.META.get('HTTP_ACCEPT', '')
        m = _VENDOR_RE.search(accept)
        version = m.group(1) if m else self.default_version
        if hasattr(request, '_request'):
            request._request.version = version
        return version
