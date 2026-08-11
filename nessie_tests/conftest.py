from pathlib import Path

from nessie_tests.pathsetup import ensure_e2e_importable

ensure_e2e_importable()


def path_accessible(p) -> bool:
    """True when *p* exists and is readable; False on missing or permission denied."""
    try:
        return Path(p).exists()
    except OSError:
        return False
