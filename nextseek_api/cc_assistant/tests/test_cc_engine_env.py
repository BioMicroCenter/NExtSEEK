"""Unit tests for the Container-CC engine's image, network, command, and the
OI-3 zero-credential agent-env contract.

After the OI-3 integration the agent container holds ZERO AWS creds and NONE of
the shared backend credentials — it reaches Bedrock only via the auth-proxy
(``ANTHROPIC_BEDROCK_BASE_URL`` + ``CLAUDE_CODE_SKIP_BEDROCK_AUTH=1``) and
NExtSEEK data only via the authenticated REST API as the user. These tests are
the hermetic half of the security acceptance contract (no Docker, no spend); the
live half lives in ``test_cc_realstack.py``.

Pure-logic only: no Docker, no Django, no torch.
"""
import json

import pytest

from nextseek_api.cc_assistant import cc_engine


# --- image + network defaults -------------------------------------------------

def test_default_image_targets_full_capability_poc():
    assert cc_engine.DEFAULT_IMAGE == "dmac-assistant:poc"


def test_default_network_is_dedicated_segmented_net():
    # audit A1: NOT the shared nextseek_default (where neo4j/mysql live) — a
    # dedicated net the agent shares only with the proxy + nginx entrypoint.
    assert cc_engine.DEFAULT_NETWORK == "dmac-cc-net"


def test_run_kwargs_attaches_default_network():
    kw = cc_engine._run_kwargs(
        image="img", command=["claude"], environment={"A": "b"},
        volumes={}, run_id="r1", user_id="demo",
    )
    assert kw["network"] == "dmac-cc-net"
    assert kw["working_dir"] == "/home/user"
    assert kw["labels"]["nextseek.cc.user"] == "demo"
    assert kw["labels"]["nextseek.cc.run"] == "r1"


# --- OI-3 zero-credential agent env (THE security contract) -------------------

# The 16 shared backend credentials (keys + canary VALUES) that must NEVER reach
# the agent. Mirrors dmac tests/integration/test_sidecar_containment_canary.py.
SHARED_CRED_CANARIES = {
    "AWS_BEARER_TOKEN_BEDROCK": "ABSKcanary0token0value",
    "ANTHROPIC_API_KEY": "sk-ant-canaryvalue",
    "NEO4J_URI": "neo4j://neo4j",
    "NEO4J_USER": "neo4j",
    "NEO4J_PASSWORD": "demopassword",
    "NEO4J_DATABASE": "neo4jdbcanary",
    "MYSQL_HOST": "db",
    "MYSQL_HOST_DEV": "dbdevcanary",
    "MYSQL_HOST_PROD": "dbprodcanary",
    "MYSQL_PORT": "33060canary",
    "MYSQL_USER": "mysqlusercanary",
    "MYSQL_PASSWORD": "mysqlpwcanary",
    "MYSQL_DEV_PASSWORD": "mysqldevpwcanary",
    "MYSQL_PROD_PASSWORD": "mysqlprodpwcanary",
    "MYSQL_ROOT_PASSWORD": "mysqlrootpwcanary",
    "GCP_API_KEY": "gcpkeycanaryvalue",
}


def _shared_cred_hits(env: dict) -> list:
    """Return every shared-cred KEY that survived, or VALUE that appears in any
    env value (catches re-keying a secret under a new name)."""
    hits = []
    values = list(env.values())
    for key, val in SHARED_CRED_CANARIES.items():
        if key in env:
            hits.append(("key", key))
        if val and any(val in v for v in values):
            hits.append(("value", key))
    return hits


def test_scanner_flags_a_seeded_env_negative_control():
    # Prove the detector is not vacuous: a dict that DOES carry shared creds
    # (key and value) must be flagged.
    bad = {"AWS_BEARER_TOKEN_BEDROCK": "ABSKcanary0token0value",
           "SOMETHING": "neo4j://neo4j"}
    hits = _shared_cred_hits(bad)
    assert ("key", "AWS_BEARER_TOKEN_BEDROCK") in hits
    assert ("value", "NEO4J_URI") in hits


def test_agent_env_has_no_shared_cred_keys_or_values():
    hostile = dict(SHARED_CRED_CANARIES)
    hostile["NEXTSEEK_URL"] = "http://127.0.0.1:8000"  # positive-control topology
    env = cc_engine.build_agent_environment(
        source=hostile, api_user="demo", api_pass="userpw",
        path_mappings={"scratch": {"x": "y"}},
    )
    assert _shared_cred_hits(env) == []
    # positive control: non-secret topology DOES pass (scan isn't vacuously empty)
    assert env["NEXTSEEK_URL"] == "http://nextseek_nginx"


def test_agent_env_points_at_proxy_and_is_unsigned():
    env = cc_engine.build_agent_environment(
        source={}, api_user="demo", api_pass="pw", path_mappings={},
    )
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert env["CLAUDE_CODE_SKIP_BEDROCK_AUTH"] == "1"
    assert env["CLAUDE_CODE_ENABLE_AUTO_MODE"] == "1"
    assert env["ANTHROPIC_BEDROCK_BASE_URL"].startswith("http://")
    assert "AWS_BEARER_TOKEN_BEDROCK" not in env


def test_agent_env_proxy_url_overridable():
    env = cc_engine.build_agent_environment(
        source={"DMAC_BEDROCK_PROXY_URL": "http://dmac-bedrock-proxy:8080"},
        api_user="d", api_pass="p", path_mappings={},
    )
    assert env["ANTHROPIC_BEDROCK_BASE_URL"] == "http://dmac-bedrock-proxy:8080"


def test_request_credentials_injected_under_both_name_schemes():
    # The agent acts as the USER's own login (I-9): API_USER/API_PASS (ChatConfig)
    # and NEXTSEEK_USERNAME/PASSWORD (entrypoint). The password IS legitimately in
    # the agent — it is the user's own, not a shared secret.
    env = cc_engine.build_agent_environment(
        source={}, api_user="demo", api_pass="userpw", path_mappings={},
    )
    assert env["API_USER"] == env["NEXTSEEK_USERNAME"] == "demo"
    assert env["API_PASS"] == env["NEXTSEEK_PASSWORD"] == "userpw"


def test_no_credentials_leaves_login_unset():
    env = cc_engine.build_agent_environment(
        source={}, api_user=None, api_pass=None, path_mappings={},
    )
    assert "API_USER" not in env and "NEXTSEEK_USERNAME" not in env


def test_redact_env_masks_secret_keys():
    env = {"NEXTSEEK_PASSWORD": "pw", "API_PASS": "pw", "NEXTSEEK_URL": "u",
           "DMAC_PATH_MAPPINGS": "{}"}
    red = cc_engine._redact_env(env)
    assert red["NEXTSEEK_PASSWORD"] == "<REDACTED>"
    assert red["API_PASS"] == "<REDACTED>"
    assert red["DMAC_PATH_MAPPINGS"] == "<REDACTED>"
    assert red["NEXTSEEK_URL"] == "u"  # non-secret passes through


# --- loopback base-URL rewrite (route via nginx) ------------------------------

def test_loopback_base_url_rewritten_to_nginx_service():
    env = cc_engine.build_agent_environment(
        source={"NEXTSEEK_BASE_URL": "http://127.0.0.1:8000"},
        api_user="d", api_pass="p", path_mappings={},
    )
    assert env["NEXTSEEK_BASE_URL"] == "http://nextseek_nginx"
    assert env["NEXTSEEK_URL"] == "http://nextseek_nginx"


def test_rewrite_helper_without_port():
    assert cc_engine._rewrite_loopback_url("http://127.0.0.1") == "http://nextseek_nginx"


def test_rewrite_helper_leaves_remote_host():
    assert cc_engine._rewrite_loopback_url("http://example.org:9000") == "http://example.org:9000"


# --- command: auto-mode + caps + $defaults-first allowlist (NOT skip-perms) ---

def test_command_uses_auto_mode_not_dangerous_skip():
    cmd = cc_engine._build_command(model_id="us.anthropic.claude-opus-4-8")
    assert "--dangerously-skip-permissions" not in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "auto"
    assert cmd[cmd.index("--model") + 1] == "us.anthropic.claude-opus-4-8"


def test_command_has_turn_and_budget_caps():
    cmd = cc_engine._build_command(model_id="m")
    assert cmd[cmd.index("--max-turns") + 1] == cc_engine._DEFAULT_MAX_TURNS
    assert cmd[cmd.index("--max-budget-usd") + 1] == str(cc_engine._DEFAULT_MAX_BUDGET_USD)


def test_command_budget_disabled_when_zero():
    cmd = cc_engine._build_command(model_id=None, max_budget_usd=0)
    assert "--max-budget-usd" not in cmd
    assert "--max-turns" in cmd  # turn cap is unconditional


def test_command_resume_appended_last():
    cmd = cc_engine._build_command(model_id=None, session_id="s1")
    assert cmd[-2:] == ["--resume", "s1"]


def test_automode_settings_defaults_first_with_descriptors():
    cmd = cc_engine._build_command(
        model_id="m",
        source={"NEXTSEEK_URL": "http://127.0.0.1:8000",
                "NEO4J_URI": "neo4j://neo4j", "GCP_API_KEY": "SECRETGCPVALUE"},
    )
    settings = json.loads(cmd[cmd.index("--settings") + 1])
    env_list = settings["autoMode"]["environment"]
    assert env_list[0] == "$defaults"                       # extend, never replace
    assert any("NExtSEEK" in e for e in env_list)           # NS-API descriptor
    assert any("Neo4j" in e for e in env_list)              # neo4j descriptor
    assert any("Gemini" in e for e in env_list)             # gcp descriptor


def test_automode_settings_carry_no_secret_values():
    cmd = cc_engine._build_command(
        model_id="m",
        source={"NEXTSEEK_URL": "http://x", "GCP_API_KEY": "SECRETGCPVALUE"},
    )
    settings_json = cmd[cmd.index("--settings") + 1]
    assert "SECRETGCPVALUE" not in settings_json


def test_default_budget_and_timeout():
    assert cc_engine._DEFAULT_MAX_BUDGET_USD == 2.00
    assert cc_engine._TIMEOUT_HARD_MAX == 180
    assert cc_engine._DEFAULT_TURN_TIMEOUT <= cc_engine._TIMEOUT_HARD_MAX


# --- I-4: user_id / project validation before mount-path interpolation --------

@pytest.mark.parametrize("bad", ["..", "../x", "a/b", "x" * 65, "", ".", "a b"])
def test_validate_user_id_rejects_traversal_and_bad_chars(bad):
    with pytest.raises(ValueError):
        cc_engine._validate_user_id(bad)


def test_validate_user_id_accepts_real_usernames():
    for ok in ("demo", "rsuser", "john.doe", "a_b-c", "user@x"):
        cc_engine._validate_user_id(ok)  # must not raise


@pytest.mark.parametrize("bad", ["..", ".", "a/b", "x" * 129, "", "a\x00b"])
def test_validate_project_rejects_traversal(bad):
    with pytest.raises(ValueError):
        cc_engine._validate_project(bad)


def test_validate_project_accepts_real_names():
    cc_engine._validate_project("example-project")
    cc_engine._validate_project("Published Data")  # spaces are fine
