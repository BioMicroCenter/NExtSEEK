"""Optional live integration tests against a running SEEK compose stack.

Run only when explicitly requested (creates/deactivates a real SEEK user):

    USERS_LIVE_SMOKE=1 docker run --rm --network container:nextseek \\
      -e NEXTSEEK_SPAWNER_URL -e NEXTSEEK_SPAWNER_TOKEN \\
      -v $PWD:/repo -w /repo nextseek-nextseek:latest \\
      /app/.venv/bin/python -m pytest nextseek_api/tests/test_users_live_integration.py -v
"""

import os
import time
import uuid

import pytest

from nextseek_api.services.seek_rails_runner import run_seek_op

pytestmark = pytest.mark.skipif(
    os.environ.get("USERS_LIVE_SMOKE") != "1",
    reason="Set USERS_LIVE_SMOKE=1 to run against live SEEK",
)


def test_live_create_and_compensate_user():
    login = f"live_smoke_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    payload = {
        "login": login,
        "password": "livepassword1",
        "password_confirmation": "livepassword1",
        "email": f"{login}@example.com",
        "first_name": "Live",
        "last_name": "Smoke",
        "project_id": 1,
        "institution_id": 1,
        "activate": True,
    }
    created = run_seek_op("create", payload)
    assert created["login"] == login
    user_id = int(created["user_id"])

    compensated = run_seek_op("compensate", {"user_id": user_id})
    assert compensated["compensated"] is True
