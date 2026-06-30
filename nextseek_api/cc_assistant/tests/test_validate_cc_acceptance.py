"""Hermetic proof that the Container-CC acceptance VALIDATOR works — exercised
over synthetic pass/fail evidence bundles so the gate itself is trusted before
the one paid live turn is ever run. No Docker, no spend.
"""
import json

from nextseek_api.cc_assistant.tests.validate_cc_acceptance import validate_run

OPUS = "us.anthropic.claude-opus-4-8"


def _write(d, name, obj):
    p = d / name
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")


def _pass_bundle(d):
    _write(d, "meta.json", {"run_id": "r1", "user_id": "demo", "sentinel": "SENT123",
                            "model_id": OPUS, "budget_cap_usd": 5.0})
    _write(d, "routed_route_decided.json", {"route": "container_cc", "source": "baml",
                                            "model_class": "opus"})
    _write(d, "forced_result.json", {"is_error": False,
                                     "reply": "done; the marker is SENT123 here",
                                     "total_cost_usd": 0.12})
    _write(d, "proxy_log.txt",
           f"POST /model/{OPUS}/invoke-with-response-stream -> 200\n"
           f"POST /model/{OPUS}/invoke-with-response-stream -> 200\n")
    _write(d, "agent_env_scan.txt",
           "CLAUDE_CODE_USE_BEDROCK=1\n"
           "ANTHROPIC_BEDROCK_BASE_URL=http://bedrock-proxy:8080\n"
           "CLAUDE_CODE_SKIP_BEDROCK_AUTH=1\nNEXTSEEK_USERNAME=demo\n"
           "NEXTSEEK_URL=http://nextseek_nginx\n")
    _write(d, "network.json", {"containers": ["dmac-bedrock-proxy",
                                              "nextseek-nextseek_nginx-1", "cc-agent-r1"]})
    _write(d, "published_files.json", {"files": ["demo/r1/report.json"]})
    _write(d, "ledger.json", {"total_cost_usd": 0.12})


def test_clean_bundle_passes(tmp_path):
    _pass_bundle(tmp_path)
    all_ok, checks = validate_run(tmp_path)
    failed = [c for c in checks if not c[1]]
    assert all_ok, f"expected all pass, failed: {failed}"
    assert len(checks) == 10


def test_leaked_token_and_key_fail(tmp_path):
    _pass_bundle(tmp_path)
    _write(tmp_path, "agent_env_scan.txt",
           "CLAUDE_CODE_USE_BEDROCK=1\nAWS_BEARER_TOKEN_BEDROCK=ABSKsecrettoken\n"
           "NEO4J_PASSWORD=demopassword\n")
    all_ok, checks = validate_run(tmp_path)
    d = dict((n, ok) for n, ok, _ in checks)
    assert not all_ok
    assert d["agent_env_no_shared_keys"] is False
    assert d["agent_env_no_leak_markers"] is False


def test_proxy_403_and_token_logged_fail(tmp_path):
    _pass_bundle(tmp_path)
    _write(tmp_path, "proxy_log.txt",
           f"POST /model/{OPUS}/invoke -> 403\nAuthorization: Bearer ABSKleak\n")
    all_ok, checks = validate_run(tmp_path)
    d = dict((n, ok) for n, ok, _ in checks)
    assert d["proxy_opus_invoke_200"] is False   # only a 403, no 200
    assert d["proxy_never_logs_token"] is False  # Authorization + ABSK logged


def test_heuristic_route_fails(tmp_path):
    _pass_bundle(tmp_path)
    _write(tmp_path, "routed_route_decided.json",
           {"route": "container_cc", "source": "heuristic"})
    all_ok, checks = validate_run(tmp_path)
    assert dict((n, ok) for n, ok, _ in checks)["router_is_baml"] is False


def test_missing_sentinel_and_error_turn_fail(tmp_path):
    _pass_bundle(tmp_path)
    _write(tmp_path, "forced_result.json", {"is_error": True, "reply": "boom"})
    all_ok, checks = validate_run(tmp_path)
    d = dict((n, ok) for n, ok, _ in checks)
    assert d["turn_completed_no_error"] is False
    assert d["reply_echoes_sentinel"] is False


def test_network_with_backend_peer_fails(tmp_path):
    _pass_bundle(tmp_path)
    _write(tmp_path, "network.json",
           {"containers": ["dmac-bedrock-proxy", "neo4j", "seek-mysql", "cc-agent-r1"]})
    assert dict((n, ok) for n, ok, _ in validate_run(tmp_path)[1])["network_segmented"] is False


def test_cost_over_cap_fails(tmp_path):
    _pass_bundle(tmp_path)
    _write(tmp_path, "ledger.json", {"total_cost_usd": 9.0})
    assert dict((n, ok) for n, ok, _ in validate_run(tmp_path)[1])["cost_under_cap"] is False


def test_unpublished_fails(tmp_path):
    _pass_bundle(tmp_path)
    _write(tmp_path, "published_files.json", {"files": []})
    assert dict((n, ok) for n, ok, _ in validate_run(tmp_path)[1])["copier_published_scoped"] is False
