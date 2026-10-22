"""Make JSON text safe to place inside an inline <script> block."""
import json

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

_ESCAPES = {
    ord("<"): "\\u003c",
    ord(">"): "\\u003e",
    ord("&"): "\\u0026",
    0x2028: "\\u2028",
    0x2029: "\\u2029",
}


@register.filter
def json_script_safe(value):
    """Escape <, >, &, U+2028 and U+2029 in already-JSON text.

    The JS engine reads the same value back; the HTML parser can no longer see a
    closing tag. None or an empty string becomes ``null``; any other non-string
    value is serialised first.
    """
    if value is None or value == "":
        return mark_safe("null")
    if not isinstance(value, str):
        value = json.dumps(value)
    return mark_safe(value.translate(_ESCAPES))
