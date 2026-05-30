"""Unit tests for the Container-CC engine's image, network, and credential wiring.

These cover the "make CC real" change: the CC sibling container must run the
full-capability image (``dmac-assistant:poc``), join the ``nextseek_default``
compose network (so service-name hosts like ``neo4j``/``db`` resolve), and
receive the chat_nextseek credential + topology env forwarded from the Django
container — with the one loopback URL (``NEXTSEEK_BASE_URL``) rewritten to the
``nextseek`` service name.

Pure-logic only: no Docker, no Django, no torch — runnable on the Intel-mac
host via ``uv run --no-project --with pytest`` (the full suite runs in-container).
"""
from nextseek_api.cc_assistant import cc_engine


# --- image + network defaults -------------------------------------------------

def test_default_image_targets_full_capability_poc():
    # With NEXTSEEK_CC_IMAGE unset, the engine must default to the image that
    # actually carries the nextseek plugin + chat_nextseek (NOT the empty lean one).
    assert cc_engine.DEFAULT_IMAGE == "dmac-assistant:poc"


def test_default_network_is_compose_default():
    assert cc_engine.DEFAULT_NETWORK == "nextseek_default"


# --- credential / topology forwarding ----------------------------------------

def test_forwards_present_credentials():
    src = {
        "API_USER": "demo",
        "API_PASS": "demopassword",
        "NEO4J_PASSWORD": "np",
        "GCP_API_KEY": "gk",
    }
    env = cc_engine._nextseek_environment(src)
    assert env["API_USER"] == "demo"
    assert env["API_PASS"] == "demopassword"
    assert env["NEO4J_PASSWORD"] == "np"
    assert env["GCP_API_KEY"] == "gk"


def test_omits_absent_and_empty_keys():
    env = cc_engine._nextseek_environment({"API_USER": "demo", "API_PASS": ""})
    assert "API_PASS" not in env          # empty string is dropped
    assert "MYSQL_HOST" not in env        # absent key is dropped
    assert "GCP_API_KEY" not in env


def test_service_name_hosts_are_preserved_verbatim():
    # NEO4J_URI / MYSQL_HOST already use compose service names; on the shared
    # network they resolve as-is and must NOT be rewritten.
    src = {"NEO4J_URI": "neo4j://neo4j", "MYSQL_HOST": "db", "MYSQL_HOST_DEV": "db"}
    env = cc_engine._nextseek_environment(src)
    assert env["NEO4J_URI"] == "neo4j://neo4j"
    assert env["MYSQL_HOST"] == "db"
    assert env["MYSQL_HOST_DEV"] == "db"


# --- loopback base-URL rewrite (route via nginx, which normalizes Host) -------
# daphne-direct (nextseek:8000) sends Host: nextseek -> Django ALLOWED_HOSTS 400.
# nginx (nextseek_nginx:80) forces a Django-safe upstream Host -> 200.

def test_loopback_base_url_rewritten_to_nginx_service():
    env = cc_engine._nextseek_environment({"NEXTSEEK_BASE_URL": "http://127.0.0.1:8000"})
    # loopback:8000 (daphne self-ref) -> nginx entrypoint on :80 (drop the port)
    assert env["NEXTSEEK_BASE_URL"] == "http://nextseek_nginx"
    # entrypoint falls back NEXTSEEK_BASE_URL <- NEXTSEEK_URL, so set both.
    assert env["NEXTSEEK_URL"] == "http://nextseek_nginx"


def test_localhost_base_url_rewritten_and_path_preserved():
    env = cc_engine._nextseek_environment({"NEXTSEEK_URL": "http://localhost:8000/api"})
    assert env["NEXTSEEK_BASE_URL"] == "http://nextseek_nginx/api"


def test_nonloopback_base_url_preserved():
    env = cc_engine._nextseek_environment({"NEXTSEEK_BASE_URL": "http://nextseek_nginx"})
    assert env["NEXTSEEK_BASE_URL"] == "http://nextseek_nginx"


def test_rewrite_helper_without_port():
    assert cc_engine._rewrite_loopback_url("http://127.0.0.1") == "http://nextseek_nginx"


def test_rewrite_helper_leaves_remote_host():
    assert cc_engine._rewrite_loopback_url("http://example.org:9000") == "http://example.org:9000"


# --- per-turn cost + time bounds (match headless E2E batch) -------------------

def test_command_includes_max_budget_usd_by_default():
    cmd = cc_engine._build_command(model_id="m")
    assert "--max-budget-usd" in cmd
    i = cmd.index("--max-budget-usd")
    assert cmd[i + 1] == str(cc_engine._DEFAULT_MAX_BUDGET_USD)
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "m"


def test_command_budget_can_be_overridden():
    cmd = cc_engine._build_command(model_id=None, max_budget_usd=0.10)
    # value is str(float) — matches the headless harness (run_headless.py:271)
    assert cmd[cmd.index("--max-budget-usd") + 1] == str(0.10)


def test_command_budget_disabled_when_zero():
    cmd = cc_engine._build_command(model_id=None, max_budget_usd=0)
    assert "--max-budget-usd" not in cmd


def test_command_resume_appended_after_budget():
    cmd = cc_engine._build_command(model_id=None, session_id="s1")
    assert cmd[-2:] == ["--resume", "s1"]


def test_default_budget_and_timeout_match_batch_harness():
    assert cc_engine._DEFAULT_MAX_BUDGET_USD == 0.50
    assert cc_engine._TIMEOUT_HARD_MAX == 180
    assert cc_engine._DEFAULT_TURN_TIMEOUT <= cc_engine._TIMEOUT_HARD_MAX


# --- per-request credential injection -----------------------------------------

def test_request_credentials_injected_under_both_name_schemes():
    # The NExtSEEK login is resolved per-request (Basic auth), NOT from env —
    # API_USER/API_PASS are unset in the container. Injected creds must land as
    # both API_USER/API_PASS (what ChatConfig reads) and NEXTSEEK_USERNAME/
    # PASSWORD (what the poc entrypoint maps to API_USER/API_PASS).
    env = cc_engine._nextseek_environment({}, api_user="demo", api_pass="demopassword")
    assert env["API_USER"] == "demo"
    assert env["API_PASS"] == "demopassword"
    assert env["NEXTSEEK_USERNAME"] == "demo"
    assert env["NEXTSEEK_PASSWORD"] == "demopassword"


def test_request_credentials_supplied_when_absent_from_env():
    # Real container case: env has topology but no API_USER/API_PASS.
    env = cc_engine._nextseek_environment({"NEO4J_URI": "neo4j://neo4j"},
                                          api_user="demo", api_pass="pw")
    assert env["API_USER"] == "demo"
    assert env["NEO4J_URI"] == "neo4j://neo4j"


def test_no_credentials_leaves_login_unset():
    env = cc_engine._nextseek_environment({})
    assert "API_USER" not in env
    assert "NEXTSEEK_USERNAME" not in env


# --- chat_nextseek LLM profile ------------------------------------------------

def test_mode_defaults_to_gcp():
    assert cc_engine._nextseek_environment({})["NEXTSEEK_MODE"] == "gcp"


def test_mode_respected_when_set():
    assert cc_engine._nextseek_environment({"NEXTSEEK_MODE": "aws"})["NEXTSEEK_MODE"] == "aws"


# --- container run kwargs (network attach) ------------------------------------

def test_run_kwargs_attaches_default_network():
    kw = cc_engine._run_kwargs(
        image="img", command=["claude"], environment={"A": "b"},
        volumes={}, run_id="r1", user_id="demo",
    )
    assert kw["network"] == "nextseek_default"
    assert kw["image"] == "img"
    assert kw["environment"] == {"A": "b"}
    assert kw["detach"] is True
    # Must run in the image WORKDIR (/home/user) so the baked CLAUDE.md + nextseek
    # plugin guidance are discovered; NOT the scratch dir (no CLAUDE.md there).
    assert kw["working_dir"] == "/home/user"
    assert kw["labels"]["nextseek.cc.user"] == "demo"
    assert kw["labels"]["nextseek.cc.run"] == "r1"
