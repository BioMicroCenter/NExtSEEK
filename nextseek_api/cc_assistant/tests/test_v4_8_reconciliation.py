"""V4-8 reconciliation artifact tests (Lane C)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from nextseek_api.eval.reconciliation import build_reconciliation, write_reconciliation_artifact
from nextseek_api.eval.run_authorization import approve_run_manifest, reconcile_reservation, reserve_budget
from nextseek_api.eval.tests.v4_8_fixtures import sample_run_manifest

pytestmark = pytest.mark.django_db


def test_reconciliation_artifact_balanced(tmp_path):
    approved = approve_run_manifest(sample_run_manifest())
    reserve_budget(
        approved.manifest_hash,
        attempt_id="a1",
        idempotency_key="k1",
        max_cost_usd=Decimal("0.20"),
    )
    reconcile_reservation("a1", actual_usd=Decimal("0.18"))
    recon = build_reconciliation(
        approved,
        attempts=[{"attempt_id": "a1", "status": "succeeded"}],
        cache_hits=0,
        retained_arm_count=1,
    )
    out = tmp_path / "recon.json"
    write_reconciliation_artifact(out, recon)
    assert out.is_file()
    assert recon.conservation["approved_max_usd"] == str(approved.max_spend_usd)
