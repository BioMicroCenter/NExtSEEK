import os
from chat_nextseek.config import ChatConfig

SEEK_URL = "http://seek:3000"
PUBLISH_URL = SEEK_URL

ASSISTANT_PARTICIPATING_PROJECTS = set(["1"])

# Required by seek/views.py and friends — these don't have defaults in
# dmac/settings.py, so local_settings.py MUST provide them or Django's
# import-time check fails with AttributeError: 'Settings' has no attribute X.
PUBLISH_STATS_FILE = "/path/to/published_stats.xlsx"
SMART_SEARCH_URL = ""

# Optional: question/answer fixtures for the chat assistant evaluator.
# Startup leaves this empty; populate when you build out eval workflows.
TEST_CASES = {}

NEXTSEEK_CHAT_CONFIG = ChatConfig()


# ---------------------------------------------------------------------------
# Optional PROD ChatConfig (admin-only PROD toggle in the UI).
#
# Fill in the values below with your real prod credentials. This file is
# gitignored. When *any* override is set, a second ChatConfig is built by
# temporarily overlaying the values on the standard env names that ChatConfig
# reads at construction. Leave any line as None to skip that override.
# ---------------------------------------------------------------------------
_PROD_OVERRIDES: dict[str, str | None] = {
    "NEXTSEEK_BASE_URL": None,
    "API_USER": None,
    "API_PASS": None,
    "NEO4J_URI": None,
    "NEO4J_USER": None,
    "NEO4J_PASSWORD": None,
    "NEO4J_DATABASE": None,
    "MYSQL_HOST_PROD": None,
    "MYSQL_PROD_PASSWORD": None,
    "MYSQL_USER": None,
    "MYSQL_PORT": None,
}

NEXTSEEK_CHAT_CONFIG_PROD = None
if any(v is not None for v in _PROD_OVERRIDES.values()):
    _prev_env = {
        k: os.environ.get(k)
        for k in (*_PROD_OVERRIDES, "NEXTSEEK_INTERNAL_BASE_URL")
    }
    try:
        for _k, _v in _PROD_OVERRIDES.items():
            if _v is not None:
                os.environ[_k] = _v
        _prod_config_map = {}
        if _PROD_OVERRIDES["NEXTSEEK_BASE_URL"] is not None:
            # ChatConfig prefers NEXTSEEK_INTERNAL_BASE_URL (the dev
            # container's internal transport URL) over NEXTSEEK_BASE_URL, so
            # it would shadow the PROD override — suppress it for this build
            # (defense-in-depth) ...
            os.environ.pop("NEXTSEEK_INTERNAL_BASE_URL", None)
            # ... and, authoritatively, pass the prod URL via config_map:
            # config_map wins over ALL env resolution (env fills gaps only),
            # so even a stale copy of this file missing the pop above can
            # never let the internal var point the PROD config at the dev
            # backend. Trailing slash normalized like the env resolver's.
            _prod_config_map["NEXTSEEK_BASE_URL"] = _PROD_OVERRIDES[
                "NEXTSEEK_BASE_URL"
            ].rstrip("/")
        NEXTSEEK_CHAT_CONFIG_PROD = ChatConfig(config_map=_prod_config_map)
    finally:
        for _k, _v in _prev_env.items():
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v
