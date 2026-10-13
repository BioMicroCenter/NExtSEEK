"""Importing the router must not load HiBayes.

``posterior_selector`` reaches the HiBayes generation store only inside
``get_active_snapshot``. A module-scope import there would load
``NessieAI.hibayes`` (and, through the generation store, the ORM) on every
router import. The check runs in a fresh interpreter, because a pytest process
has usually imported HiBayes already.
"""
from __future__ import annotations

import os
import subprocess
import sys

from NessieAI import paths

# Configure Django first when the lane has settings, so the assertion is about
# HiBayes being loaded, not about the ORM being unready.
_PROBE = (
    "import os, sys\n"
    "if os.environ.get('DJANGO_SETTINGS_MODULE'):\n"
    "    import django\n"
    "    django.setup()\n"
    "import NessieAI.router.router\n"
    "print(sorted(m for m in sys.modules if m.split('.')[:2] == ['NessieAI', 'hibayes']))\n"
    "print(sorted(m for m in sys.modules if m.split('.')[0] in ('torch', 'laya')"
    " or m.split('.')[:3] in (['NessieAI', 'router', 'laya'], ['NessieAI', 'router', 'laya_common'])))\n"
)


def test_importing_the_router_does_not_load_hibayes():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(paths.REPO_ROOT), env.get("PYTHONPATH", "")) if p
    )
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=paths.REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    hibayes, laya = result.stdout.strip().splitlines()[-2:]
    assert hibayes == "[]", result.stdout
    # laya (JevLevROUTING) is imported inside decide(), never at import time; no torch either.
    assert laya == "[]", result.stdout
