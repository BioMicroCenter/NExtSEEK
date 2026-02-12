"""
Django template tag to load Vite-built assets using the manifest.json file.

Usage in templates:
    {% load vite_assets %}
    {% vite_assets "src/main.embedded.tsx" "js/chat_assistant" %}

Generates <script> and <link> tags with correct hashed filenames.
"""

import json
import os

from django import template
from django.conf import settings
from django.utils.safestring import mark_safe

register = template.Library()

_manifest_cache: dict[str, dict] = {}


def _load_manifest(static_prefix: str) -> dict:
    """Load and cache the Vite manifest.json for a given static prefix."""
    if static_prefix in _manifest_cache and not settings.DEBUG:
        return _manifest_cache[static_prefix]

    manifest_path = None
    for static_dir in settings.STATICFILES_DIRS:
        candidate = os.path.join(static_dir, static_prefix, ".vite", "manifest.json")
        if os.path.isfile(candidate):
            manifest_path = candidate
            break

    if manifest_path is None:
        return {}

    with open(manifest_path) as f:
        manifest = json.load(f)

    _manifest_cache[static_prefix] = manifest
    return manifest


@register.simple_tag
def vite_assets(entry: str, static_prefix: str) -> str:
    """
    Render <script> and <link> tags for a Vite entry point.

    Args:
        entry: The Vite entry point key (e.g. "src/main.embedded.tsx")
        static_prefix: The subdirectory under static/ (e.g. "js/chat_assistant")
    """
    manifest = _load_manifest(static_prefix)

    if entry not in manifest:
        if settings.DEBUG:
            return mark_safe(
                f"<!-- vite_assets: entry '{entry}' not found in manifest -->"
            )
        return ""

    entry_data = manifest[entry]
    base_url = f"{settings.STATIC_URL}{static_prefix}/"
    tags = []

    # CSS files
    for css_file in entry_data.get("css", []):
        tags.append(f'<link rel="stylesheet" href="{base_url}{css_file}">')

    # JS entry
    js_file = entry_data.get("file", "")
    if js_file:
        tags.append(
            f'<script type="module" crossorigin src="{base_url}{js_file}"></script>'
        )

    return mark_safe("\n    ".join(tags))
