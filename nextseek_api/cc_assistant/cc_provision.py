"""Step 2 multi-user provisioning primitives.

This module runs host-side in the trusted Django process. SEEK credentials are
used only for host-side project resolution; the sandboxed CC agent receives
only paths and its existing per-request NExtSEEK login.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify_project(title: str) -> str:
    """Return the strict filesystem-safe project slug required by SPEC-2 D2."""
    if not isinstance(title, str):
        title = str(title or "")
    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return _NON_SLUG_RE.sub("-", ascii_only.lower()).strip("-")


@dataclass(frozen=True)
class ProjectIdentity:
    """Resolved SEEK project identity or a synthetic personal namespace."""

    id: str
    title: str
    slug: str

    @property
    def dirname(self) -> str:
        return f"{self.id}-{self.slug}"
