"""Full hermetic acceptance replay for Plan 018 V4-9 Task 6."""
from __future__ import annotations

import json
import os
from pathlib import Path

from nextseek_api.eval.task6_replay import run_task6_replay


DELIVERY = Path("/home/taishajo/work/NExtSEEK-dev/testquestions-2026-08-07")


def test_authenticated_stored_evidence_to_local_routing_chain():
    result = run_task6_replay(DELIVERY)

    assert result["gate"] == "PASS"
    assert result["conservation"] == {
        "pairs": 149,
        "arms": 298,
        "retained_pairs": 149,
        "excluded_pairs": 0,
        "pending_pairs": 0,
        "balanced": True,
    }
    assert result["stored_judgments"]["eligible_arms"] == 274
    assert result["stored_judgments"]["ineligible_arms"] == 24
    assert result["stored_judgments"]["stored_attempts"] == 822
    assert result["stored_judgments"]["calls_per_eligible_arm"] == 3
    assert result["stored_judgments"]["provider_calls"] == 0
    assert result["stored_judgments"]["historical_provider_judgments_claimed"] is False
    assert result["fit"]["quality_mcmc"] is True
    assert result["fit"]["latency_mcmc"] is False
    assert result["fit"]["diagnostics_ok"] is True
    assert result["routing"] == {
        "graph_traversal": "nextseek_query",
        "unsupported": "container_cc",
        "sample_search": "legacy_fallback",
    }
    assert result["external_effects"] == {
        "new_paired_route_execution": False,
        "provider_calls": 0,
        "live_database": False,
        "deployment": False,
        "production_enablement": False,
    }

    output = os.environ.get("PLAN018_TASK6_RESULT")
    if output:
        Path(output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
