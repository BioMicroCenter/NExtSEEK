"""Map a lab/PI name to its 3-letter UID code. The lab is encoded in NExtSEEK
sample UIDs as a 3-letter code (e.g. D.SEQ-221031SHA-67 -> SHA), so a lab scope
is matched by searching this code in the UID, not by the lab's full name."""
from __future__ import annotations


def lab_code(name: str | None) -> str:
    """Return the first 3 alphabetic characters of ``name``, uppercased.

    'Kamm' -> 'KAM', 'Shalek lab' -> 'SHA'. Returns '' when fewer than 3
    alphabetic characters are present (too ambiguous to map)."""
    if not isinstance(name, str):
        return ""
    alpha = "".join(ch for ch in name if ch.isalpha())
    return alpha[:3].upper() if len(alpha) >= 3 else ""
