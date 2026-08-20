import json
import os
import re
import sys
import threading
from contextlib import nullcontext, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import requests
from dotenv import load_dotenv

from .llm_clients import BaseLLMClient, build_llm_client


def _resolve_nextseek_base_url() -> str | None:
    """Transport URL for NExtSEEK REST self-calls.

    Prefers NEXTSEEK_INTERNAL_BASE_URL — the container-internal listener URL
    rendered by startup, decoupled from the published host port — over the
    public NEXTSEEK_BASE_URL (which derives from NEXTSEEK_HOSTNAME and tracks
    the host-published port, auto-bumped when 8000 is busy on the host). The
    self-calls execute inside the nextseek container, so a bumped host port in
    the public URL points at nothing (Step 7d greenfield: connection refused).
    """
    url = os.getenv("NEXTSEEK_INTERNAL_BASE_URL") or os.getenv("NEXTSEEK_BASE_URL")
    if url is None:
        return None
    return url.rstrip("/")


_VALID_LAUNCH_MODES = {"tower", "luria"}


def detect_pipeline_launch_mode(env: dict | None = None) -> str:
    """Return the default launch backend ('tower' | 'luria'). Invalid -> 'luria'."""
    env = env if env is not None else os.environ
    mode = (env.get("PIPELINE_LAUNCH_MODE") or "luria").strip().lower()
    if mode not in _VALID_LAUNCH_MODES:
        print(f"[CONFIG] Invalid PIPELINE_LAUNCH_MODE {mode!r}; defaulting to 'luria'.")
        return "luria"
    return mode


def build_luria_env(env: dict | None = None) -> dict:
    """Assemble the Luria SSH/SLURM env dict from process env (host hardcoded)."""
    env = env if env is not None else os.environ
    return {
        "user": env.get("LURIA_USER"),
        "key": env.get("LURIAKEY"),
        "working_path": env.get("LURIA_WORKING_PATH"),
        "host": "luria.mit.edu",
    }


def luria_env_complete(luria_env: dict) -> bool:
    """True when the required Luria fields are all present."""
    return all(luria_env.get(k) for k in ("user", "key", "working_path"))


def db_conn_is_alive(conn) -> bool:
    """
    Actively probe a MySQL connection.

    mysql-connector's ``is_connected()`` pings the server, so this catches a
    connection the server hung up on — which a truthiness test cannot, because a
    dead connection object is still truthy. Connections without the method (test
    doubles, other drivers) are taken at face value.
    """
    probe = getattr(conn, "is_connected", None)
    if probe is None:
        return True
    try:
        return bool(probe())
    except Exception:
        return False


def live_db_conn(config, env: str = "prod"):
    """
    Return a usable MySQL connection for ``config``, replacing a dead cached one.

    ``config._db_conn`` is opened once per gunicorn worker in ``ChatConfig.__init__``
    and outlives every request, so the server drops it long before the process ends.
    A dead mysql-connector connection is still *truthy*, which is why the former

        conn = config._db_conn or config._connect_db(env="prod")

    idiom could never recover: ``or`` only falls through on ``None``, so the corpse
    went straight to ``cursor()`` and every caller on that worker got

        OperationalError(-1, 'MySQL Connection not available', None)

    until the process restarted. Evict the corpse before reconnecting so it is never
    served twice, and cache the replacement so the report runners (which never close
    what they are handed) stop opening a fresh connection per report.

    Takes ``config`` rather than ``self`` because the report runners are handed
    lightweight config stand-ins, not always a real ``ChatConfig``. Anything exposing
    ``_db_conn``/``_connect_db`` works.

    Returns ``None`` when the database is genuinely unreachable; callers already
    treat that as a clean "DB connection failed".
    """
    conn = getattr(config, "_db_conn", None)
    if conn is not None and not db_conn_is_alive(conn):
        print("[CONFIG][DB] Cached connection is dead; reconnecting.")
        try:
            conn.close()
        except Exception:
            pass
        conn = None
    if conn is None:
        connect = getattr(config, "_connect_db", None)
        conn = connect(env=env) if connect is not None else None
    try:
        config._db_conn = conn
    except Exception:  # frozen/slotted stand-ins: caching is best-effort
        pass
    return conn


class ChatConfig:
    def __init__(self, config_map={}):
        """Load configuration, provider clients, prompts, and cached context for one process."""
        self.CATALOG_ENDPOINT_METHODS: dict[str, str] = {}
        self.CATALOG_ENDPOINT_METHODS_ALL: dict[str, set[str]] = {}
        self.METHOD_PRIORITY = ["GET", "POST", "PUT", "PATCH", "DELETE"]

        # Name -> id maps, built DYNAMICALLY from the live DB in
        # _initialize_runtime_state (NOT hardcoded — the old literal held ids from
        # another instance). PROJECT_NAME_TO_ID = seek_production.projects only;
        # INVESTIGATION_NAME_TO_ID = seek_production.investigations, kept SEPARATE
        # so the report can offer an investigation-scoped path without touching the
        # project path. Both UPPER-cased keys; both {} when the DB is unreachable.
        self.PROJECT_NAME_TO_ID: dict[str, int] = {}
        self.INVESTIGATION_NAME_TO_ID: dict[str, int] = {}

        # Lazy API_SCHEMA state must exist before config_map is applied (a
        # config_map may assign API_SCHEMA, which routes through its setter).
        self._init_api_schema_state()

        # Apply config_map first so _get_env_config can reference self.MODEL_MODE,
        # self.GCP_API_KEY, etc. when they are provided via DB/JSON config object.
        self._load_config_map(config_map)
        if not hasattr(self, "CONFIG_VERBOSE"):
            self.CONFIG_VERBOSE = self._coerce_bool(os.getenv("CHAT_NEXTSEEK_CONFIG_VERBOSE"), default=False)

        log_context = nullcontext() if self.CONFIG_VERBOSE else redirect_stdout(StringIO())
        with log_context:
            self._initialize_runtime_state()

    def _initialize_runtime_state(self) -> None:
        """Populate runtime-only state after config values are sourced from config_map and env."""
        # Env vars fill in gaps only — they do NOT override values already set from config_map.
        # This means .env provides defaults; a DB/JSON config_map takes precedence.
        self.env_config_map = self._get_env_config()
        self._load_config_map({k: v for k, v in self.env_config_map.items() if v is not None and getattr(self, k, None) is None})

        # Build secondary provider clients for cross-provider agent routing.
        # LLM_CLIENTS maps provider key → client; used by get_agent_model().
        self.LLM_CLIENTS: dict[str, BaseLLMClient] = self._build_secondary_clients()

        # First merge, then get connections
        self._db_conn = self._connect_db(env="prod")
        self.CONTEXT_JSON_PATHS = self._ensure_context_files(env="prod")

        # Full JSON (generated from DB) — list form and code-keyed dict for fast lookup
        self.FULL_SAMPLETYPES: list = self._load_json("sampletypes_db.json", "full sampletypes (db)") or []
        self.FULL_ASSAYS: list = self._load_json("assays_db.json", "full assays (db)") or []
        self.FULL_PROJECTS: list = self._load_json_list("projects_db.json", "full projects (db)")
        self.FULL_SAMPLETYPES_MAP: dict = {
            item["SampleType"]: item for item in self.FULL_SAMPLETYPES if item.get("SampleType")
        }
        self.FULL_ASSAYS_MAP: dict = {
            item["Name"]: item for item in self.FULL_ASSAYS if item.get("Name")
        }
        self.FULL_PROJECTS_MAP: dict = {
            item["name"]: item for item in self.FULL_PROJECTS if item.get("name")
        }
        # Dynamic maps from the live DB (replace the removed hardcoded literal):
        # projects and investigations kept in SEPARATE maps.
        self.PROJECT_NAME_TO_ID = self._load_name_to_id_from_db("seek_production.projects", env="prod")
        self.PROJECT_NAME_TO_ID = self._merge_project_name_to_id(self.PROJECT_NAME_TO_ID, self.FULL_PROJECTS)
        self.INVESTIGATION_NAME_TO_ID = self._load_name_to_id_from_db("seek_production.investigations", env="prod")

        # Published-report umbrella projects (DEV-ONLY opt-in; DEFAULT EMPTY so
        # prod is unaffected). For a project listed here, run_project_published_report
        # reports ALL investigations' samples instead of filtering investigation
        # titles by the project-name hint — needed where one umbrella project
        # (e.g. the dev "Published Data") contains every investigation and so
        # matches no investigation title. Set via NEXTSEEK_PUBLISHED_UMBRELLA_PROJECTS
        # (comma-separated project names and/or ids). See issue #1 / option 2.
        self.PUBLISHED_UMBRELLA_PROJECTS = self._parse_umbrella_projects(
            os.environ.get("NEXTSEEK_PUBLISHED_UMBRELLA_PROJECTS", "")
        )

        # Min JSONs actually used by the agents
        self.MIN_SAMPLETYPES = self._load_json_list("min_sampletypes_db.json", "min sampletypes (db)")
        self.MIN_ASSAYS = self._load_json_list("min_assays_db.json", "min assays (db)")
        self.MIN_PROJECTS = self.FULL_PROJECTS
        self.MIN_API_ENDPOINTS = self._load_json_list("min_api_endpoints_enriched.json", "min API endpoints")

        # FULL catalogs used only as embedding sources for the semantic indexes
        # (richer fields → better cosine separation). LLM still sees the MIN
        # catalogs; _hybrid_shortlist remaps results back to MIN by id_field.
        self.FULL_SAMPLETYPES = self._load_json_list("sampletypes_db.json", "full sampletypes (embedding source)")
        self.FULL_ASSAYS = self._load_json_list("assays_db.json", "full assays (embedding source)")

        # ── Semantic shortlist configuration ─────────────────────────
        self.SEMANTIC_SHORTLIST_ENABLED = self._coerce_bool(
            os.getenv("SEMANTIC_SHORTLIST_ENABLED"), default=False
        )
        self.SEMANTIC_ENDPOINTS_ENABLED = self._coerce_bool(
            os.getenv("SEMANTIC_ENDPOINTS_ENABLED"), default=False
        )
        self.SEMANTIC_MODEL_NAME = os.getenv(
            "SEMANTIC_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"
        )
        self.SEMANTIC_RATIO = float(os.getenv("SEMANTIC_RATIO", "0.7"))
        self.SEMANTIC_MIN_K = int(os.getenv("SEMANTIC_MIN_K", "10"))
        self.SEMANTIC_MAX_K = int(os.getenv("SEMANTIC_MAX_K", "80"))

        self.SAMPLETYPE_INDEX = None
        self.ASSAY_INDEX = None
        self.ENDPOINT_INDEX = None
        if self.SEMANTIC_SHORTLIST_ENABLED:
            from chat_nextseek.helpers.tools.catalog_semantic import (  # noqa: PLC0415
                SemanticIndex, doc_sampletype, doc_assay,
            )
            self.SAMPLETYPE_INDEX = SemanticIndex(
                catalog=self.FULL_SAMPLETYPES,
                name="sampletypes",
                doc_fn=doc_sampletype,
                id_fn=lambda x: x.get("SampleType", ""),
                cache_dir=Path(self.CONTEXT_DIR),
                model_name=self.SEMANTIC_MODEL_NAME,
            )
            self.ASSAY_INDEX = SemanticIndex(
                catalog=self.FULL_ASSAYS,
                name="assays",
                doc_fn=doc_assay,
                id_fn=lambda x: x.get("Name", ""),
                cache_dir=Path(self.CONTEXT_DIR),
                model_name=self.SEMANTIC_MODEL_NAME,
            )
        if self.SEMANTIC_ENDPOINTS_ENABLED:
            from chat_nextseek.helpers.tools.catalog_semantic import (  # noqa: PLC0415
                SemanticIndex, doc_endpoint,
            )
            self.ENDPOINT_INDEX = SemanticIndex(
                catalog=self.MIN_API_ENDPOINTS,
                name="endpoints",
                doc_fn=doc_endpoint,
                id_fn=lambda x: x.get("path", ""),
                cache_dir=Path(self.CONTEXT_DIR),
                model_name=self.SEMANTIC_MODEL_NAME,
            )

        self.MIN_GRAPH_SCHEMA = self._load_json("min_graph_schema.json", "min graph schema") or {}
        if not getattr(self, "AGENT_MODEL_CATALOG", None):
            if getattr(self, "CATALOG_FILE", None):
                self.AGENT_MODEL_CATALOG = json.loads(Path(self.CATALOG_FILE).read_text(encoding="utf-8"))
            else:
                raise RuntimeError("Neither AGENT_MODEL_CATALOG nor CATALOG_FILE is set.")
        self.AGENT_MODEL_CATALOG = self._normalize_agent_model_catalog(self.AGENT_MODEL_CATALOG)

        # Resolve catalog key: e.g. gcp+flash-lite → "gcp:3.1-flash", gcp+pro → "gcp:3.1-pro"
        # aws:son, aws:opus → direct match; anything else → "default"
        self._CATALOG_KEY = self._resolve_catalog_key()

        for ep in self.MIN_API_ENDPOINTS:
            path = ep.get("path")
            method = ep.get("method")
            if isinstance(path, str) and isinstance(method, str):
                candidate = method.upper()
                current = self.CATALOG_ENDPOINT_METHODS.get(path)
                self.CATALOG_ENDPOINT_METHODS[path] = self._prefer_method(current, candidate)
                self.CATALOG_ENDPOINT_METHODS_ALL.setdefault(path, set()).add(candidate)

        # API_SCHEMA is deliberately NOT fetched here (review follow-up FU6,
        # 2026-07-07): the fetch is a REST self-call, and at cold boot every
        # gunicorn worker serialized on its own accept backlog for the full
        # read timeout before the fail-soft branch left the schema empty for
        # the process lifetime. It is a lazy property now — first access
        # fetches, one retry when the first attempt came back empty.

        self.ENTITY_SYSTEM_PROMPT = self._load_prompt("entity_agent.txt")
        self.PARSER_CORE_ROUTING_PROMPT = self._load_prompt("parser_core_routing.txt")
        self.PARSER_SYSTEM_PROMPT = self._load_composed_parser_prompt("parser_agent.txt")
        self.REPORTER_SYSTEM_PROMPT = self._load_prompt("reporter_agent.txt")
        self.REPORT_WRITER_SYSTEM_PROMPT = self._load_prompt("report_writer_agent.txt")
        self.REPORT_CODER_SYSTEM_PROMPT = self._load_prompt("report_coder_agent.txt")
        self.API_AGENT_SYSTEM_PROMPT = self._load_prompt("api_agent.txt")
        self.CHATTER_SYSTEM_PROMPT = self._load_prompt("chatter_agent.txt")
        self.MEMORY_SYSTEM_PROMPT = self._load_prompt("memory_agent.txt")
        self.MEMORY_CODER_SYSTEM_PROMPT = self._load_prompt("memory_coder_agent.txt")
        self.GRAPH_AGENT_SYSTEM_PROMPT = self._load_prompt("graph_agent.txt")
        self.SYSTEM_AGENT_SYSTEM_PROMPT = self._load_prompt("system_agent.txt")
        self.PLANNER_SYSTEM_PROMPT = self._load_prompt("planner_agent.txt")
        self.MULTI_PARSER_SYSTEM_PROMPT = self._load_composed_parser_prompt("multi_parser_agent.txt")
        self.CONTEXT_ENGINEER_SYSTEM_PROMPT = self._load_prompt("context_engineer.txt")
        self.EVALUATOR_V1_SYSTEM_PROMPT = self._load_prompt("evaluator-v1-agent.txt")
        self.SEQERA_AGENT_SYSTEM_PROMPT = self._load_prompt("seqera_agent.txt")
        self.CAPABILITIES_DOC = self._load_capabilities_doc()

        # Seqera / Tower environment for the NFCORE flow.
        self.SEQERA_AUTO_LAUNCH = self._coerce_bool(os.getenv("SEQERA_AUTO_LAUNCH"), default=False)
        self.TOWER_ENV: dict[str, str | None] = {
            "access_token": os.getenv("TOWER_ACCESS_TOKEN"),
            "workspace": os.getenv("TOWER_WORKSPACE_ID"),
            "compute_env": os.getenv("SEQERA_COMPUTE_ENV"),
            "work_bucket": os.getenv("SEQERA_WORK_BUCKET"),
            "pipeline_revisions": os.getenv("SEQERA_PIPELINE_REVISIONS_JSON"),
            "api_endpoint": os.getenv("TOWER_API_ENDPOINT"),
            "default_profile": os.getenv("SEQERA_DEFAULT_PROFILE"),
            "input_dir": os.getenv("SEQERA_INPUT_DIR"),
        }
        self.TOWER_ENV_COMPLETE = all(
            self.TOWER_ENV[k] for k in ("access_token", "workspace", "compute_env", "work_bucket")
        )
        if self.CONFIG_VERBOSE:
            print(
                f"[ChatConfig] SEQERA tower env complete: {self.TOWER_ENV_COMPLETE} "
                f"(auto_launch={self.SEQERA_AUTO_LAUNCH})"
            )

        # Pipeline launch backend selector + Luria SSH/SLURM env. Sibling of the
        # TOWER_ENV block above: Tower and Luria are the two launch backends,
        # selected by PIPELINE_LAUNCH_MODE (default 'tower', so this is inert
        # until an operator opts in).
        self.PIPELINE_LAUNCH_MODE = detect_pipeline_launch_mode()
        self.LURIA_ENV = build_luria_env()
        self.LURIA_ENV_COMPLETE = luria_env_complete(self.LURIA_ENV)
        if self.CONFIG_VERBOSE:
            print(
                f"[ChatConfig] pipeline launch mode: {self.PIPELINE_LAUNCH_MODE} "
                f"(luria env complete: {self.LURIA_ENV_COMPLETE})"
            )

        self.NEO4J_SCHEMA = self._ensure_neo4j_schema()
        self.PROTOCOL_SCHEMA = self._ensure_protocol_schema()
        self.ASSAY_SAMPLE_CONNECTIONS = self._ensure_assay_sample_connections()

    def _load_config_map(self, config_map):
        """Assign each config_map entry directly onto the config object."""
        for key, value in config_map.items():
            setattr(self, key, value)

    # ------------------------------------------------------------------
    # Lazy API_SCHEMA (review follow-up FU6, 2026-07-07)
    # ------------------------------------------------------------------

    _API_SCHEMA_MAX_ATTEMPTS = 2  # first access + one retry after an empty result

    def _init_api_schema_state(self) -> None:
        # A shared mutable CELL, not plain instance attributes: the
        # orchestrator and the granular ops copy.copy() the config per
        # request (orchestrator.py, services/assistant.py), and a per-copy
        # cache would die with each request — the process singleton would
        # re-fetch on every query forever (cold-review finding, 2026-07-08).
        # Shallow copies share this dict, so the first successful fetch (and
        # the attempt cap) benefits the whole process.
        self._api_schema_cell: dict = {"schema": None, "attempts": 0, "lock": threading.Lock()}

    @property
    def API_SCHEMA(self) -> dict:
        """Remote body-schema registry, fetched lazily on first access.

        Eager construction-time fetching serialized every gunicorn worker's
        cold boot (~read-timeout each: the master binds :8000 pre-fork, so
        the self-call connects into an accept backlog no still-importing
        worker can serve) and a failed boot fetch left the schema empty for
        the process lifetime. Lazy + one-shot retry removes the boot stall
        and lets the first post-boot access recover; after the retry the
        result is cached even when empty so a dead backend isn't hammered.
        Fail-soft: a RAISING load counts against the same cap and yields {}
        — never an exception into the calling request handler.
        """
        cell = self._api_schema_cell
        cached = cell["schema"]
        if cached is not None and (
            cached or cell["attempts"] >= self._API_SCHEMA_MAX_ATTEMPTS
        ):
            return cached
        with cell["lock"]:
            cached = cell["schema"]
            if cached is not None and (
                cached or cell["attempts"] >= self._API_SCHEMA_MAX_ATTEMPTS
            ):
                return cached
            cell["attempts"] += 1
            try:
                result = self._load_api_schema_from_remote()
            except Exception as e:
                print(f"[CONFIG][SCHEMA] Schema load raised: {e!r}")
                result = {}
            if result or cell["schema"] is None:
                cell["schema"] = result
            return cell["schema"]

    @API_SCHEMA.setter
    def API_SCHEMA(self, value: dict) -> None:
        # Explicit assignment (e.g. via config_map) wins outright — never
        # overwritten by a later lazy fetch. Rebinds a FRESH cell so an
        # override on a per-request copy stays local to that copy and can
        # never clobber the process singleton's shared cache.
        self._api_schema_cell = {
            "schema": value,
            "attempts": self._API_SCHEMA_MAX_ATTEMPTS,
            "lock": threading.Lock(),
        }

    def _coerce_bool(self, value, default: bool = False) -> bool:
        """Coerce common truthy string forms into bool while preserving a default for None."""
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    
    def _load_prompt(self, name: str) -> str:
        """
        Load a prompt template file by name from the prompts directory with UTF-8 decoding.
        Raises if the file is missing so calling code can surface template errors early.
        """
        return (Path(self.PROMPTS_DIR).resolve() / name).read_text(encoding="utf-8")

    def _load_composed_parser_prompt(self, name: str) -> str:
        """Load a parser wrapper prompt and inject the shared parser routing core."""
        wrapper = self._load_prompt(name)
        return wrapper.replace("{{PARSER_CORE_ROUTING}}", self.PARSER_CORE_ROUTING_PROMPT)

    def _load_capabilities_doc(self) -> str:
        """Load capabilities.md from the context directory. Returns empty string if not found."""
        cap_path = Path(self.CONTEXT_DIR).resolve() / "capabilities.md"
        if cap_path.exists():
            return cap_path.read_text(encoding="utf-8")
        print("[CONFIG] capabilities.md not found — system agent will have no capabilities document.")
        return ""
    
    def _get_env_config(self):
        """Collect environment-backed configuration values and derive repository-relative defaults."""
        env_config_map = {}

        load_dotenv()

        env_config_map["MODEL_MODE"] = self._detect_model_mode()
        env_config_map["BASE_DIR"] = Path(__file__).resolve().parent
        env_config_map["REPO_ROOT"] = env_config_map["BASE_DIR"].parent.parent
        env_config_map["XDG_STATE_HOME"] = os.path.expanduser(os.getenv("XDG_STATE_HOME", "~/.local/state"))
        env_config_map["LOG_DIR"] = os.path.expanduser(os.getenv("LOG_DIR", env_config_map["XDG_STATE_HOME"] + "/chat_nextseek/logs/"))
        env_config_map["OUTPUTS_DIR"] = os.path.expanduser(os.getenv("NEXTSEEK_OUTPUTS_DIR", env_config_map["XDG_STATE_HOME"] + "/chat_nextseek/outputs/"))
        env_config_map["PROMPTS_DIR"] = os.getenv("PROMPTS_DIR", str(env_config_map["BASE_DIR"]) + "/prompts")
        env_config_map["CONTEXT_DIR"] = os.getenv("CONTEXT_DIR", str(env_config_map["BASE_DIR"]) + "/context")
        env_config_map["SEQ_TEMPLATE_PATH"] = os.getenv("SEQ_TEMPLATE_PATH", str(env_config_map["BASE_DIR"]) + "/reports/templates/GEO_template.xlsx")
        env_config_map["CATALOG_FILE"] = os.getenv("CATALOG_FILE")
        raw_catalog = os.getenv("AGENT_MODEL_CATALOG")
        env_config_map["AGENT_MODEL_CATALOG"] = json.loads(raw_catalog) if raw_catalog else None

        # ======================================================
        # Session State
        # ======================================================

        env_config_map["SESSION_DB_TYPE"] = os.getenv("SESSION_DB_TYPE", "sqlite")
        env_config_map["SESSION_DB_PATH"] = os.path.expanduser(os.getenv("SESSION_DB_PATH", env_config_map["XDG_STATE_HOME"] + "/chat_nextseek/db.sqlite"))
        env_config_map["SESSION_DB_HOST"] = os.getenv("SESSION_DB_HOST")
        env_config_map["SESSION_DB_PORT"] = os.getenv("SESSION_DB_PORT", 3306)
        env_config_map["SESSION_DB_USER"] = os.getenv("SESSION_DB_USER")
        env_config_map["SESSION_DB_PASSWORD"] = os.getenv("SESSION_DB_PASSWORD")
        env_config_map["SESSION_DB_NAME"] = os.getenv("SESSION_DB_NAME")

        # ======================================================
        # Model config (OpenAI / GCP / Anthropic)
        # ======================================================

        env_config_map["GCP_API_KEY"] = os.getenv("GCP_API_KEY")
        env_config_map["ANTHROPIC_API_KEY"] = os.getenv("ANTHROPIC_API_KEY")

        # OpenAI-specific optional overrides
        env_config_map["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")
        env_config_map["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL")

        # AWS Bedrock — boto3 resolves AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY automatically.
        # AWS_BEARER_TOKEN_BEDROCK is passed as aws_session_token (needed for temporary/STS credentials).
        env_config_map["AWS_BEARER_TOKEN_BEDROCK"] = os.getenv("AWS_BEARER_TOKEN_BEDROCK")
        env_config_map["AWS_REGION"] = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"

        _mode = env_config_map["MODEL_MODE"]
        if _mode in ("gcp", "mixed") or _mode.startswith("gcp:"):
            if not env_config_map["GCP_API_KEY"] and not getattr(self, "GCP_API_KEY", None):
                raise RuntimeError("GCP mode selected but GCP_API_KEY is not set.")
            _gcp_defaults = {
                "gcp:lite": "gemini-2.5-flash",
                "gcp:current": "gemini-3.5-flash",
            }
            env_config_map["LLM_MODEL"] = os.getenv("GCP_LLM_MODEL", _gcp_defaults.get(_mode, "gemini-3.5-flash"))
        elif _mode == "anth" or _mode.startswith("anth:"):
            # Routes through AWS Bedrock (Claude models via cross-region inference).
            if not env_config_map["AWS_BEARER_TOKEN_BEDROCK"] and not getattr(self, "AWS_BEARER_TOKEN_BEDROCK", None):
                raise RuntimeError("Anthropic mode uses AWS Bedrock; AWS_BEARER_TOKEN_BEDROCK is not set.")
            _anth_defaults = {
                "anth:lite":    "anthropic.claude-sonnet-4-5-20250929-v1:0",
                "anth:current": "us.anthropic.claude-sonnet-4-6",
                "anth":         "us.anthropic.claude-sonnet-4-6",
            }
            env_config_map["LLM_MODEL"] = os.getenv("ANTH_LLM_MODEL", _anth_defaults.get(_mode, "us.anthropic.claude-sonnet-4-6"))
        elif _mode.startswith("aws:") or (getattr(self, "MODEL_MODE", "") or "").startswith("aws:"):
            if not env_config_map["AWS_BEARER_TOKEN_BEDROCK"] and not getattr(self, "AWS_BEARER_TOKEN_BEDROCK", None):
                raise RuntimeError("AWS Bedrock mode selected but AWS_BEARER_TOKEN_BEDROCK is not set.")
            # Model alias -> Bedrock model ID
            # aws:son    → Claude Sonnet 4.6
            # aws:opus   → Claude Opus 4.6
            # aws:ds     → DeepSeek V3.2
            # aws:qwen-nxt → Qwen3 80B
            # aws:glm      → GLM-4.7
            # Override the default model via BEDROCK_LLM_MODEL
            _BEDROCK_ALIAS_MAP = {
                "aws:son":      "us.anthropic.claude-sonnet-4-6",   # cross-region inference profile
                "aws:opus":     "us.anthropic.claude-opus-4-7",  # cross-region inference profile
                "aws:ds":       "deepseek.v3.2",
                "aws:qwen-nxt": "qwen.qwen3-next-80b-a3b",
                "aws:glm":      "zai.glm-4.7",
            }
            alias_default = _BEDROCK_ALIAS_MAP.get(
                env_config_map["MODEL_MODE"],
                "us.anthropic.claude-sonnet-4-6",
            )
            env_config_map["LLM_MODEL"] = os.getenv("BEDROCK_LLM_MODEL", alias_default)
        else:
            env_config_map["LLM_MODEL"] = os.getenv("NEXTSEEK_LLM_MODEL", "gpt-5.2")

        env_config_map["LLM_CLIENT"] = build_llm_client(
            env_config_map["MODEL_MODE"],
            gcp_api_key=env_config_map["GCP_API_KEY"],
            anthropic_api_key=env_config_map["ANTHROPIC_API_KEY"],
            openai_api_key=env_config_map["OPENAI_API_KEY"],
            openai_base_url=env_config_map["OPENAI_BASE_URL"],
            aws_bearer_token=env_config_map.get("AWS_BEARER_TOKEN_BEDROCK"),
            aws_region=env_config_map["AWS_REGION"],
        )

        print(f"[CONFIG] MODEL_MODE={env_config_map["MODEL_MODE"]}")
        print(f"[CONFIG] LLM_MODEL={env_config_map["LLM_MODEL"]}")
        if _mode in ("gcp", "mixed") or _mode.startswith("gcp:"):
            print("[CONFIG] Using native Gemini client")
        if _mode == "anth" or _mode.startswith("anth:"):
            print("[CONFIG] Using Anthropic/Bedrock client")
        if _mode == "oai":
            print(f"[CONFIG] OPENAI_API_KEY={'SET' if env_config_map["OPENAI_API_KEY"] else 'NOT SET'}")
            print(f"[CONFIG] OPENAI_BASE_URL={env_config_map["OPENAI_BASE_URL"] or 'default'}")
        if _mode.startswith("aws:"):
            print(f"[CONFIG] Using AWS Bedrock client (region={env_config_map['AWS_REGION']})")
            print(f"[CONFIG] AWS_BEARER_TOKEN_BEDROCK={'SET' if env_config_map.get('AWS_BEARER_TOKEN_BEDROCK') else 'NOT SET'}")

        # ======================================================
        # NExtSEEK API config
        # ======================================================

        env_config_map["NEXTSEEK_BASE_URL"] = _resolve_nextseek_base_url()
        env_config_map["API_USER"] = os.getenv("API_USER")
        env_config_map["API_PASS"] = os.getenv("API_PASS")

        _base_url_source = (
            "NEXTSEEK_INTERNAL_BASE_URL"
            if os.getenv("NEXTSEEK_INTERNAL_BASE_URL")
            else "NEXTSEEK_BASE_URL"
        )
        print(f"[CONFIG] NEXTSEEK_BASE_URL={env_config_map["NEXTSEEK_BASE_URL"] or 'NOT SET'} (from {_base_url_source})")
        print(f"[CONFIG] API_USER={'SET' if env_config_map["API_USER"] else 'NOT SET'}")
        print(f"[CONFIG] API_PASS={'SET' if env_config_map["API_PASS"] else 'NOT SET'}")
        if env_config_map["AGENT_MODEL_CATALOG"]:
            print(f"[CONFIG] AGENT_MODEL_CATALOG=SET (from env)")
        else:
            print(f"[CONFIG] CATALOG_FILE={env_config_map['CATALOG_FILE'] or 'NOT SET'}")

        # ======================================================
        # Database config (MySQL)
        # ======================================================

        env_config_map["MYSQL_HOST_PROD"] = os.getenv("MYSQL_HOST_PROD")
        env_config_map["MYSQL_HOST_DEV"] = os.getenv("MYSQL_HOST_DEV")
        env_config_map["MYSQL_PORT"] = self._coerce_port(os.getenv("MYSQL_PORT"))
        env_config_map["MYSQL_USER"] = os.getenv("MYSQL_USER")
        env_config_map["MYSQL_PROD_PASSWORD"] = os.getenv("MYSQL_PROD_PASSWORD")
        env_config_map["MYSQL_DEV_PASSWORD"] = os.getenv("MYSQL_DEV_PASSWORD")

        print(f"[CONFIG][DB] MYSQL_HOST_PROD={'SET' if env_config_map["MYSQL_HOST_PROD"] else 'NOT SET'}")
        print(f"[CONFIG][DB] MYSQL_HOST_DEV={'SET' if env_config_map["MYSQL_HOST_DEV"] else 'NOT SET'}")
        print(f"[CONFIG][DB] MYSQL_PORT={env_config_map["MYSQL_PORT"] or 'NOT SET'}")
        print(f"[CONFIG][DB] MYSQL_USER={'SET' if env_config_map["MYSQL_USER"] else 'NOT SET'}")
        print(f"[CONFIG][DB] MYSQL_PROD_PASSWORD={'SET' if env_config_map["MYSQL_PROD_PASSWORD"] else 'NOT SET'}")
        print(f"[CONFIG][DB] MYSQL_DEV_PASSWORD={'SET' if env_config_map["MYSQL_DEV_PASSWORD"] else 'NOT SET'}")

        # ======================================================
        # Neo4j / Graph DB config
        # ======================================================

        env_config_map["NEO4J_URI"] = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        env_config_map["NEO4J_USER"] = os.getenv("NEO4J_USER", "neo4j")
        env_config_map["NEO4J_PASSWORD"] = os.getenv("NEO4J_PASSWORD")
        env_config_map["NEO4J_DATABASE"] = os.getenv("NEO4J_DATABASE", "neo4j")

        print(f"[CONFIG][GRAPHDB] NEO4J_URI={env_config_map['NEO4J_URI']}")
        print(f"[CONFIG][GRAPHDB] NEO4J_USER={env_config_map['NEO4J_USER']}")
        print(f"[CONFIG][GRAPHDB] NEO4J_DATABASE={env_config_map['NEO4J_DATABASE']}")
        print(f"[CONFIG][GRAPHDB] NEO4J_PASSWORD={'SET' if env_config_map['NEO4J_PASSWORD'] else 'NOT SET'}")

        return env_config_map

    def _coerce_port(self, value: str | None, default: int = 3306) -> int:
        """
        Convert a string port value to int, returning a default when unset or invalid.
        Guards against ValueError to keep config import safe even with bad env inputs.
        """
        try:
            return int(value) if value is not None else default
        except ValueError:
            return default

    # ======================================================
    # Mode selection (OpenAI vs GCP OpenAI-compatible)
    # ======================================================

    def _detect_model_mode(self) -> str:
        """
        Determine which backend to use based on CLI flag, env, or default.
        Priority order is CLI flag (-m/--mode) then NEXTSEEK_MODE env, falling back to 'gcp' when unset or invalid.
        Normalizes the value to lower-case and constrains it to the supported set {oai, gcp, anth}.
        """
        cli_mode = None
        argv = sys.argv[1:]
        for i, arg in enumerate(argv):
            if arg in ("-m", "--mode"):
                if i + 1 < len(argv):
                    cli_mode = argv[i + 1]
            elif arg.startswith("--mode="):
                cli_mode = arg.split("=", 1)[1]
            elif arg.startswith("-m="):
                cli_mode = arg.split("=", 1)[1]

        mode = (cli_mode or os.getenv("NEXTSEEK_MODE") or "mixed").strip().lower()
        _VALID_MODES = {
            "oai", "gcp", "mixed",
            "gcp:current", "gcp:lite",
            "anth", "anth:current", "anth:lite",
            "aws:son", "aws:opus", "aws:ds", "aws:qwen-nxt", "aws:glm",
        }
        if mode not in _VALID_MODES:
            print(f"[CONFIG] Invalid mode {mode!r}; defaulting to 'mixed'.")
            return "mixed"
        return mode

    def _connect_db(self, env: str = "dev"):
        """
        Connect to a MySQL database using .env credentials.
        Pass env="prod" to target production; defaults to dev.
        Returns a mysql.connector connection or None on failure.
        """
        target = (env or "dev").strip().lower()
        if target == "dev":
            if hasattr(self, "MYSQL_HOST_DEV") and hasattr(self, "MYSQL_DEV_PASSWORD"):
                host = self.MYSQL_HOST_DEV
                password = self.MYSQL_DEV_PASSWORD
            else:
                host = None
                password = None
        if target == "prod":
            if hasattr(self, "MYSQL_HOST_PROD") and hasattr(self, "MYSQL_PROD_PASSWORD"):
                host = self.MYSQL_HOST_PROD
                password = self.MYSQL_PROD_PASSWORD
            else:
                host = None
                password = None

        if not host:
            print(f"[CONFIG][DB] Host not configured for env '{target}'.")
            return None
        if not hasattr(self, "MYSQL_USER") or not password:
            print(f"[CONFIG][DB] Credentials not configured for env '{target}'.")
            return None

        try:
            import mysql.connector  # type: ignore
        except ImportError:
            print("[CONFIG][DB] mysql-connector-python not installed; install with 'uv add mysql-connector-python'.")
            return None

        try:
            conn = mysql.connector.connect(
                host=host,
                port=self.MYSQL_PORT,
                user=self.MYSQL_USER,
                password=password,
                charset="utf8mb4",
                collation="utf8mb4_unicode_ci",
                use_pure=True,
            )
            print(f"[CONFIG][DB] Connected to {target} at {host}:{self.MYSQL_PORT} as {self.MYSQL_USER}")
            return conn
        except Exception as e:
            print(f"[CONFIG][DB] Connection to {target} failed: {e!r}")
            return None

    def _live_db_conn(self, env: str = "prod"):
        """Return a usable connection, replacing the cached one once it has died."""
        return live_db_conn(self, env=env)

    def _fetch_context_files_from_db(self, env: str = "prod") -> dict[str, Path]:
        """
        Pull context tables from MySQL and write them to JSON files under context/.
        Currently exports:
          - dmac.sample_types_context -> full + min JSON
          - dmac.assay_context -> full + min JSON
          - dmac.projects_context -> full JSON
        Returns a dict of {name: Path} for successfully written files.
        """
        exports: dict[str, Path] = {}
        conn = self._live_db_conn(env=env)
        if conn is None:
            return exports

        try:
            try:
                cursor = conn.cursor(dictionary=True)
            except Exception:
                cursor = conn.cursor()

            def normalize_rows(rows: list, columns: list[str]) -> list[dict]:
                normalized: list[dict] = []
                if rows and isinstance(rows[0], dict):
                    normalized = rows  # type: ignore[assignment]
                    if not columns:
                        columns = sorted({k for row in normalized for k in row.keys()})
                else:
                    if not columns:
                        return []
                    for row in rows:
                        if isinstance(row, (list, tuple)):
                            normalized.append({col: row[i] if i < len(row) else None for i, col in enumerate(columns)})
                        else:
                            normalized.append({col: None for col in columns})
                return normalized

            def write_json(dest: Path, payload) -> Path:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
                return dest

            def export_table(table: str, label: str, mapper, min_mapper=None) -> dict[str, Path]:
                try:
                    cursor.execute(f"SELECT * FROM {table}")
                    rows = cursor.fetchall() or []
                    columns = getattr(cursor, "column_names", None) or []

                    normalized = normalize_rows(rows, columns)
                    if not normalized:
                        print(f"[CONFIG][DB] {label}: No data returned; skipping write.")
                        return {}

                    mapped = [mapper(r) for r in normalized]
                    paths: dict[str, Path] = {}

                    full_dest = Path(self.CONTEXT_DIR) / f"{label}_db.json"
                    write_json(full_dest, mapped)
                    print(f"[CONFIG][DB] Wrote {label} JSON to {full_dest} ({len(mapped)} rows).")
                    paths["full"] = full_dest

                    if min_mapper is not None:
                        min_mapped = [min_mapper(r) for r in mapped]
                        min_dest = Path(self.CONTEXT_DIR) / f"min_{label}_db.json"
                        write_json(min_dest, min_mapped)
                        print(f"[CONFIG][DB] Wrote min {label} JSON to {min_dest} ({len(min_mapped)} rows).")
                        paths["min"] = min_dest

                    return paths
                except Exception as e:
                    print(f"[CONFIG][DB] Failed to export {label}: {e!r}")
                    return {}

            def map_sampletype(row: dict) -> dict:
                lower = {k.lower(): v for k, v in row.items()}
                return {
                    "ID": lower.get("sampletype_id"),
                    "SampleType": lower.get("sample_type"),
                    "Name": row.get("Name") or lower.get("name"),
                    "Description": row.get("Description") or lower.get("description"),
                    "Tags": row.get("Tags") or lower.get("tags"),
                    "Required Metadata": row.get("Required_Metadata") or lower.get("required_metadata"),
                    "Standard Metadata": row.get("Standard_Metadata") or lower.get("standard_metadata"),
                    "Possible Metadata Fields": row.get("Possible_Metadata_Fields") or lower.get("possible_metadata_fields"),
                    "Clade": row.get("Clade") or lower.get("clade"),
                    "SampleType File Link": row.get("SampleType_File_Link") or lower.get("sampletype_file_link"),
                    "Associated Assay Parents": row.get("Associated_Assay_Parents") or lower.get("associated_assay_parents"),
                    "Associated Assay Children": row.get("Associated_Assay_Children") or lower.get("associated_assay_children"),
                    "Parent_SampleTypes": row.get("Parent_SampleTypes") or lower.get("parent_sampletypes"),
                    "Child_SampleTypes": row.get("Child_SampleTypes") or lower.get("child_sampletypes"),
                }

            def map_sampletype_min(row: dict) -> dict:
                return {
                    "SampleType": row.get("SampleType"),
                    "ID": row.get("ID"),
                    "Name": row.get("Name"),
                    "Description": row.get("Description"),
                    "Tags": row.get("Tags"),
                }

            def map_assay(row: dict) -> dict:
                lower = {k.lower(): v for k, v in row.items()}
                return {
                    "Name": row.get("assay_name") or row.get("Name") or lower.get("assay_name"),
                    "Description": row.get("Description") or lower.get("description"),
                    "Tags": row.get("Tags") or lower.get("tags"),
                    "Alternative Assay Names": row.get("Alternative_Assay_Names") or lower.get("alternative_assay_names"),
                    "Required Parent Sample Types": row.get("Required_Parent_Sample_Types") or lower.get("required_parent_sample_types"),
                    "Optional Parent Sample Types": row.get("Optional_Parent_Sample_Types") or lower.get("optional_parent_sample_types"),
                    "Children Sample Types": row.get("Children_Sample_Types") or lower.get("children_sample_types"),
                    "Parent Clade Type": row.get("Parent_Clade_Type") or lower.get("parent_clade_type"),
                    "Child Clade Type": row.get("Child_Clade_Type") or lower.get("child_clade_type"),
                    "AssaySheet Link": row.get("AssaySheet_Link") or lower.get("assaysheet_link"),
                    "AssociatedRepository": row.get("AssociatedRepository") or lower.get("associatedrepository"),
                    "Critical Attributes": row.get("Critical_Attributes") or lower.get("critical_attributes"),
                    "Protocols_Phrases": row.get("Protocols_Phrases") or lower.get("protocols_phrases"),
                    "Protocols_UIDs": row.get("Protocols_UIDs") or lower.get("protocols_uids"),
                    "Internal Assay ID": row.get("internal_assay_id") or lower.get("internal_assay_id"),
                }

            def map_assay_min(row: dict) -> dict:
                return {
                    "Name": row.get("Name"),
                    "Description": row.get("Description"),
                    "Tags": row.get("Tags"),
                }

            def map_project(row: dict) -> dict:
                lower = {k.lower(): v for k, v in row.items()}

                def _coerce_json_list(value):
                    if value in (None, ""):
                        return []
                    if isinstance(value, list):
                        return value
                    if isinstance(value, tuple):
                        return list(value)
                    if isinstance(value, str):
                        text = value.strip()
                        if not text:
                            return []
                        try:
                            parsed = json.loads(text)
                        except Exception:
                            return [s.strip() for s in text.split("|") if s.strip()]
                        if isinstance(parsed, list):
                            return parsed
                        return [text]
                    return []

                alt_names = _coerce_json_list(
                    row.get("alternative_names")
                    or row.get("Alternative_Names")
                    or lower.get("alternative_names")
                    or []
                )
                key_data_types = _coerce_json_list(
                    row.get("key_data_types")
                    or row.get("Key_Data_Types")
                    or lower.get("key_data_types")
                    or []
                )
                return {
                    "name": row.get("name") or row.get("Name") or lower.get("name"),
                    "alternative_names": alt_names,
                    "entity_type": row.get("entity_type") or row.get("Entity_Type") or lower.get("entity_type"),
                    "project_id": row.get("project_id") or row.get("Project_ID") or lower.get("project_id"),
                    "parent_project": row.get("parent_project") or row.get("Parent_Project") or lower.get("parent_project"),
                    "pi": row.get("pi") or row.get("PI") or lower.get("pi"),
                    "research_focus": row.get("research_focus") or row.get("Research_Focus") or lower.get("research_focus"),
                    "key_data_types": key_data_types,
                    "description": row.get("description") or row.get("Description") or lower.get("description"),
                    "nih_reporter_link": row.get("nih_reporter_link") or row.get("NIH_Reporter_Link") or lower.get("nih_reporter_link"),
                    "fairdomhub_published_link": row.get("fairdomhub_published_link") or row.get("Fairdomhub_Published_Link") or lower.get("fairdomhub_published_link"),
                    "tags": row.get("tags") or row.get("Tags") or lower.get("tags"),
                }

            sample_paths = export_table(
                "dmac.sample_types_context",
                "sampletypes",
                map_sampletype,
                min_mapper=map_sampletype_min,
            )
            if sample_paths:
                exports.update({f"sampletypes_{k}": v for k, v in sample_paths.items()})

            assay_paths = export_table(
                "dmac.assay_context",
                "assays",
                map_assay,
                min_mapper=map_assay_min,
            )
            if assay_paths:
                exports.update({f"assays_{k}": v for k, v in assay_paths.items()})

            project_paths = export_table(
                "dmac.projects_context",
                "projects",
                map_project,
            )
            if project_paths:
                exports.update({f"projects_{k}": v for k, v in project_paths.items()})

            return exports
        finally:
            try:
                cursor.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            if conn is self._db_conn:
                self._db_conn = None

    # ======================================================
    # Context file freshness helpers
    # ======================================================


    # A refresh happened today iff this marker exists with a today mtime. Only a
    # SUCCESSFUL _fetch_context_files_from_db writes it, and it is never baked
    # into the image (runtime-only), so a stale context file baked with a today
    # mtime can no longer masquerade as a fresh cache (2026-07-05 BUG-2).
    _REFRESH_MARKER_NAME = ".context_db_refresh"

    def _is_today(self, path: Path) -> bool:
        """
        Check whether a file's mtime falls on today's UTC date.
        Used to decide if cached context exports are fresh enough to reuse.
        """
        try:
            ts = path.stat().st_mtime
        except FileNotFoundError:
            return False
        today = datetime.now(timezone.utc).date()
        return datetime.fromtimestamp(ts, timezone.utc).date() == today


    def _write_refresh_marker(self) -> None:
        """Record that a successful DB refresh happened now. Best-effort: if the
        context dir is read-only the marker simply never registers as today, so
        the gate degrades to always-refresh (safe) rather than crashing."""
        try:
            marker = Path(self.CONTEXT_DIR) / self._REFRESH_MARKER_NAME
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("", encoding="utf-8")
        except Exception as e:
            print(f"[CONFIG][DB] Could not write refresh marker: {e!r}")


    def _ensure_context_files(self, env: str = "prod") -> dict[str, Path]:
        """
        Ensure DB-driven context JSON files exist and are fresh for today.
        Returns a dict of {label: Path}.

        Freshness is tracked by ``_REFRESH_MARKER_NAME`` (written only by a
        successful DB refresh), NOT by the context files' own mtime — a file
        baked into the image on run-day carries a today mtime but stale content,
        so trusting file mtime silently skipped the refresh (BUG-2). We still
        refresh when any target file is missing.
        """
        targets = {
            "sampletypes_full": Path(self.CONTEXT_DIR) / "sampletypes_db.json",
            "sampletypes_min": Path(self.CONTEXT_DIR) / "min_sampletypes_db.json",
            "assays_full": Path(self.CONTEXT_DIR) / "assays_db.json",
            "assays_min": Path(self.CONTEXT_DIR) / "min_assays_db.json",
            "projects_full": Path(self.CONTEXT_DIR) / "projects_db.json",
        }

        marker = Path(self.CONTEXT_DIR) / self._REFRESH_MARKER_NAME
        needs_refresh = (not self._is_today(marker)) or any(not p.exists() for p in targets.values())
        if needs_refresh:
            print("[CONFIG][DB] No verified DB refresh today (or a file is missing); refreshing from DB.")
            fetched = self._fetch_context_files_from_db(env=env)
            if fetched:  # a real DB pull produced at least one file
                self._write_refresh_marker()
            paths = dict(fetched)
            # Merge expected keys with returned paths for convenience
            for key, path in targets.items():
                if path.exists():
                    paths[key] = path
            return paths

        print("[CONFIG][DB] DB refresh already verified for today; skipping DB export.")
        return {k: v for k, v in targets.items() if v.exists()}

    # ======================================================
    # Context loading helpers (sampletypes, assays, endpoints)
    # ======================================================

    # Export context tables to JSON on import so downstream loaders can use them (once per day).

    def _load_json(self, path: str, label: str, base: str | None = None):
        """
        Load JSON content from context/<path> (or base/<path> if base is given) and log success or failures.
        Returns the parsed object on success, otherwise None so callers can default safely.
        """
        p = Path(base or self.CONTEXT_DIR) / path
        if not p.exists():
            print(f"[CONFIG][CONTEXT] {label}: file {p} not found.")
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            print(f"[CONFIG][CONTEXT] {label}: loaded from {p}")
            return data
        except Exception as e:
            print(f"[CONFIG][CONTEXT] {label}: failed to parse {p}: {e!r}")
            return None


    def _load_json_list(self, path: str, label: str) -> list:
        """
        Load a JSON list from disk, validating type and logging mismatch details.
        Returns an empty list on errors so downstream code can continue with safe defaults.
        """
        data = self._load_json(path, label)
        if isinstance(data, list):
            print(f"[CONFIG][CONTEXT] {label}: {len(data)} entries")
            return data
        if data is not None:
            print(f"[CONFIG][CONTEXT] {label}: expected list, got {type(data)}")
        return []

    @staticmethod
    def _norm_project_key(value) -> str:
        """Case/space-insensitive key for project name or id matching."""
        return re.sub(r"\s+", "", str(value).strip().lower())

    def _parse_umbrella_projects(self, raw: str) -> set[str]:
        """Parse NEXTSEEK_PUBLISHED_UMBRELLA_PROJECTS (comma-separated names/ids)
        into a normalized set. Empty/blank -> empty set (prod-safe default)."""
        return {
            self._norm_project_key(tok)
            for tok in (raw or "").split(",")
            if tok and tok.strip()
        }

    def is_umbrella_published_project(self, project, project_id=None) -> bool:
        """True when the published report should SKIP the investigation-title
        hint (report ALL samples) for this project. Opt-in via
        NEXTSEEK_PUBLISHED_UMBRELLA_PROJECTS; empty by default so prod is
        unchanged. Matches on normalized project name OR id."""
        umbrella = getattr(self, "PUBLISHED_UMBRELLA_PROJECTS", None) or set()
        if not umbrella:
            return False
        keys = set()
        if project is not None:
            keys.add(self._norm_project_key(project))
        if project_id is not None:
            keys.add(self._norm_project_key(project_id))
        return bool(keys & umbrella)

    def _load_name_to_id_from_db(self, table: str, env: str = "prod") -> dict[str, int]:
        """Build an UPPER-cased ``title -> id`` map from one live DB table (e.g.
        ``seek_production.projects`` or ``seek_production.investigations``) instead
        of a hardcoded literal. ``table`` is a fixed internal constant (never user
        input). Returns ``{}`` if the DB is unreachable — callers must NOT fall back
        to hardcoded ids.
        """
        mapping: dict[str, int] = {}
        conn = self._live_db_conn(env=env)
        if conn is None:
            return mapping
        try:
            try:
                cursor = conn.cursor(dictionary=True)
                dict_rows = True
            except Exception:
                cursor = conn.cursor()
                dict_rows = False
            cursor.execute(f"SELECT id, title FROM {table}")
            for row in cursor.fetchall() or []:
                if dict_rows and isinstance(row, dict):
                    rid, title = row.get("id"), row.get("title")
                elif isinstance(row, (list, tuple)) and len(row) >= 2:
                    rid, title = row[0], row[1]
                else:
                    continue
                if isinstance(rid, int) and isinstance(title, str) and title.strip():
                    mapping.setdefault(title.strip().upper(), rid)
            try:
                cursor.close()
            except Exception:
                pass
        except Exception as e:
            print(f"[CONFIG][DB] name->id load failed for {table}: {e!r}")
        return mapping

    def _merge_project_name_to_id(self, base_map: dict[str, int], projects: list[dict]) -> dict[str, int]:
        """
        Extend the existing project-name lookup with canonical names and aliases from projects_db.json.
        Entries without a numeric Project ID are skipped.
        """
        merged = dict(base_map)
        for project in projects:
            project_id = project.get("project_id")
            if not isinstance(project_id, int):
                continue
            names = [project.get("name"), *(project.get("alternative_names") or [])]
            for name in names:
                if not isinstance(name, str):
                    continue
                key = name.strip().upper()
                if key:
                    merged[key] = project_id
        return merged


    # ------------------------------------------------------------------
    # Agent model catalog helpers
    # ------------------------------------------------------------------

    _THINKING_BUDGET_MAP: dict[str | None, int | None] = {
        None: None,
        "low": 4000,
        "medium": 8000,
        "high": 16000,
    }

    def _resolve_catalog_key(self) -> str:
        """
        Map the current provider mode to a catalog key used in AGENT_MODEL_CATALOG.

        "mixed"       (no -m flag)   → "default"
        "gcp"                        → "gcp:current"  (legacy alias)
        "gcp:current"                → "gcp:current"
        "gcp:lite"                   → "gcp:lite"
        "anth"                       → "anth:current" (legacy alias)
        "anth:current"               → "anth:current"
        "anth:lite"                  → "anth:lite"
        "aws:*"                      → "aws"
        """
        mode = getattr(self, "MODEL_MODE", "")
        if mode == "mixed":
            return "default"
        if mode in ("gcp", "gcp:current"):
            return "gcp:current"
        if mode == "gcp:lite":
            return "gcp:lite"
        if mode in ("anth", "anth:current"):
            return "anth:current"
        if mode == "anth:lite":
            return "anth:lite"
        if mode.startswith("aws:"):
            return "aws"
        return "default"

    def _build_secondary_clients(self) -> dict[str, BaseLLMClient]:
        """
        Build auxiliary provider clients so individual agents can route to a
        different provider than the primary LLM_CLIENT (e.g. GCP flash as default
        but Bedrock opus for parser/reporter).

        Keys mirror the catalog 'provider' field: 'gcp', 'anth', 'oai'.
        The primary client is always registered under its MODEL_MODE key.
        Secondary clients are only built when the credentials are present and the
        mode is not already that provider.
        """
        from .llm_clients import GeminiClient, BedrockClient, OpenAIClient

        clients: dict[str, BaseLLMClient] = {self.MODEL_MODE: self.LLM_CLIENT}

        _is_gcp_primary = self.MODEL_MODE in ("gcp", "mixed") or self.MODEL_MODE.startswith("gcp:")
        _is_bedrock_primary = (
            self.MODEL_MODE == "anth"
            or self.MODEL_MODE.startswith("anth:")
            or self.MODEL_MODE.startswith("aws:")
        )

        # Always register the primary client under its canonical provider key so
        # _get_fallback_agent_configs can find it via catalog provider= lookups.
        if _is_gcp_primary:
            clients["gcp"] = self.LLM_CLIENT
        elif _is_bedrock_primary:
            clients["anth"] = self.LLM_CLIENT

        # Secondary GCP
        if getattr(self, "GCP_API_KEY", None) and not _is_gcp_primary:
            try:
                clients["gcp"] = GeminiClient(api_key=self.GCP_API_KEY)
                print("[CONFIG] Secondary GCP client initialized.")
            except Exception as e:
                print(f"[CONFIG] Secondary GCP client failed: {e!r}")

        # Secondary Bedrock/Anthropic
        if getattr(self, "AWS_BEARER_TOKEN_BEDROCK", None) and not _is_bedrock_primary:
            try:
                clients["anth"] = BedrockClient(
                    region=getattr(self, "AWS_REGION", "us-east-1"),
                    bearer_token=self.AWS_BEARER_TOKEN_BEDROCK,
                )
                print("[CONFIG] Secondary Bedrock client initialized.")
            except Exception as e:
                print(f"[CONFIG] Secondary Bedrock client failed: {e!r}")

        # Secondary OpenAI
        if getattr(self, "OPENAI_API_KEY", None) and self.MODEL_MODE != "oai":
            try:
                clients["oai"] = OpenAIClient(
                    api_key=self.OPENAI_API_KEY,
                    base_url=getattr(self, "OPENAI_BASE_URL", None),
                )
                print("[CONFIG] Secondary OpenAI client initialized.")
            except Exception as e:
                print(f"[CONFIG] Secondary OpenAI client failed: {e!r}")

        return clients

    def get_agent_model(self, agent_label: str) -> tuple[BaseLLMClient, str, int | None]:
        """Return (client, model_name, thinking_budget) for the given agent label."""
        cfg = self.agent_config(agent_label)
        provider = cfg.get("provider")
        client = self.LLM_CLIENTS.get(provider) if provider else None
        return client or self.LLM_CLIENT, cfg["model"], cfg["thinking_budget"]

    def _normalize_agent_model_catalog(self, catalog: dict | None) -> dict:
        """Accept either legacy agent-keyed profiles or grouped model-keyed profiles."""
        if not isinstance(catalog, dict):
            raise RuntimeError("AGENT_MODEL_CATALOG must be a JSON object.")

        normalized: dict[str, dict] = {}
        for profile_name, profile_value in catalog.items():
            if profile_name.startswith("_"):
                normalized[profile_name] = profile_value
                continue
            normalized[profile_name] = self._normalize_agent_model_profile(profile_name, profile_value)
        return normalized

    def _normalize_agent_model_profile(self, profile_name: str, profile_value: dict | list) -> dict:
        """Normalize one catalog profile, accepting legacy flat maps or grouped route/model forms."""
        if isinstance(profile_value, list):
            return self._expand_grouped_agent_routes(profile_name, profile_value)
        if not isinstance(profile_value, dict):
            raise RuntimeError(f"AGENT_MODEL_CATALOG profile '{profile_name}' must be an object or list.")
        if "models" in profile_value:
            models = profile_value.get("models")
            if not isinstance(models, dict):
                raise RuntimeError(f"AGENT_MODEL_CATALOG profile '{profile_name}'.models must be an object.")
            expanded = self._expand_grouped_agent_models(profile_name, models)
            if "_comment" in profile_value:
                expanded["_comment"] = profile_value["_comment"]
            return expanded
        if "routes" in profile_value:
            routes = profile_value.get("routes")
            if not isinstance(routes, list):
                raise RuntimeError(f"AGENT_MODEL_CATALOG profile '{profile_name}'.routes must be a list.")
            expanded = self._expand_grouped_agent_routes(profile_name, routes)
            if "_comment" in profile_value:
                expanded["_comment"] = profile_value["_comment"]
            return expanded
        return profile_value

    def _expand_grouped_agent_routes(self, profile_name: str, routes: list[dict]) -> dict:
        """Expand a route list with shared config blocks into a per-agent mapping."""
        expanded: dict[str, dict] = {}
        for route in routes:
            if not isinstance(route, dict):
                raise RuntimeError(f"AGENT_MODEL_CATALOG profile '{profile_name}' contains a non-object route.")
            agents = route.get("agents")
            if not isinstance(agents, list) or not agents:
                raise RuntimeError(f"AGENT_MODEL_CATALOG profile '{profile_name}' route is missing a non-empty 'agents' list.")
            route_cfg = {
                "provider": route.get("provider"),
                "model": route.get("model"),
                "thinking_level": route.get("thinking_level"),
            }
            for agent_label in agents:
                if not isinstance(agent_label, str) or not agent_label:
                    raise RuntimeError(f"AGENT_MODEL_CATALOG profile '{profile_name}' has an invalid agent label in routes.")
                if agent_label in expanded:
                    raise RuntimeError(f"AGENT_MODEL_CATALOG profile '{profile_name}' assigns agent '{agent_label}' more than once.")
                expanded[agent_label] = route_cfg.copy()
        return expanded

    def _expand_grouped_agent_models(self, profile_name: str, models: dict) -> dict:
        """Expand grouped model declarations into explicit agent-level model assignments."""
        expanded: dict[str, dict] = {}
        for model_name, model_entry in models.items():
            if isinstance(model_entry, dict):
                variants = [model_entry]
            elif isinstance(model_entry, list):
                variants = model_entry
            else:
                raise RuntimeError(f"AGENT_MODEL_CATALOG profile '{profile_name}' model '{model_name}' must be an object or list.")

            for variant in variants:
                if not isinstance(variant, dict):
                    raise RuntimeError(f"AGENT_MODEL_CATALOG profile '{profile_name}' model '{model_name}' has a non-object variant.")
                agents = variant.get("agents")
                if not isinstance(agents, list) or not agents:
                    raise RuntimeError(
                        f"AGENT_MODEL_CATALOG profile '{profile_name}' model '{model_name}' is missing a non-empty 'agents' list."
                    )
                route_cfg = {
                    "provider": variant.get("provider"),
                    "model": None if model_name == "__default__" else model_name,
                    "thinking_level": variant.get("thinking_level"),
                }
                for agent_label in agents:
                    if not isinstance(agent_label, str) or not agent_label:
                        raise RuntimeError(
                            f"AGENT_MODEL_CATALOG profile '{profile_name}' model '{model_name}' has an invalid agent label."
                        )
                    if agent_label in expanded:
                        raise RuntimeError(f"AGENT_MODEL_CATALOG profile '{profile_name}' assigns agent '{agent_label}' more than once.")
                    expanded[agent_label] = route_cfg.copy()
        return expanded

    def agent_config(self, agent_label: str) -> dict:
        """
        Return {model, thinking_budget} for a given agent label (e.g. "parser").
        Falls back to the "default" catalog entry if no provider-specific override exists.
        model=None means use the globally configured LLM_MODEL.
        thinking_budget=None means no thinking (disabled).
        """
        catalog = self.AGENT_MODEL_CATALOG or {}
        provider_entry = catalog.get(self._CATALOG_KEY, {})
        default_entry = catalog.get("default", {})
        agent_override = provider_entry.get(agent_label) or {}
        agent_default = default_entry.get(agent_label) or {}

        model = agent_override.get("model") or agent_default.get("model") or None
        thinking_level = agent_override.get("thinking_level") if "thinking_level" in agent_override else agent_default.get("thinking_level")
        thinking_budget = self._THINKING_BUDGET_MAP.get(thinking_level)
        provider = agent_override.get("provider") if "provider" in agent_override else agent_default.get("provider")

        return {
            "model": model or self.LLM_MODEL,
            "thinking_budget": thinking_budget,
            "provider": provider,
        }

    def _prefer_method(self, existing: str | None, candidate: str) -> str:
        """
        Choose the preferred HTTP method between an existing value and a candidate.
        Respects METHOD_PRIORITY ordering so GET/POST outrank less common methods.
        """
        if existing is None:
            return candidate
        try:
            if self.METHOD_PRIORITY.index(candidate) < self.METHOD_PRIORITY.index(existing):
                return candidate
        except ValueError:
            pass
        return existing

    def _load_api_schema_from_remote(self) -> dict:
        """
        Load a minimal body-schema registry from the live NExtSEEK OpenAPI YAML at {NEXTSEEK_BASE_URL}/nextseek_api/schema/.
        Fetches the spec, resolves JSON schemas, and annotates allowed/required body fields per endpoint/method.
        Merges catalog method hints so even GET-only endpoints get coverage when remote schema is incomplete.
        """
        try:
            import yaml  # type: ignore
        except ImportError:
            print("[CONFIG][SCHEMA] PyYAML not installed; skipping remote schema load.")
            return {}

        if not self.NEXTSEEK_BASE_URL:
            print("[CONFIG][SCHEMA] NEXTSEEK_BASE_URL not set; cannot fetch remote schema.")
            return {}

        schema_url = f"{self.NEXTSEEK_BASE_URL}/nextseek_api/schema/"

        # The schema route requires authentication (#77); an anonymous fetch
        # gets a 401 and silently disables body validation (#94). Send the same
        # Basic credentials every other REST call already carries — see
        # helpers/tools/nextseek_api.py, and orchestrator.py which binds them
        # per turn onto a copy of this config.
        api_user = getattr(self, "API_USER", None)
        api_pass = getattr(self, "API_PASS", None)
        auth = (api_user, api_pass) if api_user and api_pass else None

        print(f"[CONFIG][SCHEMA] Fetching schema from {schema_url}")
        try:
            # (2s connect, 5s read): connect SUCCEEDS against a bound-but-
            # unserved listener (cold boot), so the read timeout is the only
            # lever bounding the stall — keep it short; the lazy property
            # retries once on the next access anyway.
            resp = requests.get(schema_url, timeout=(2, 5), auth=auth)
        except Exception as e:
            print(f"[CONFIG][SCHEMA] Request to schema URL failed: {e!r}")
            return {}

        if resp.status_code != 200:
            print(f"[CONFIG][SCHEMA] Non-200 when fetching schema: {resp.status_code}")
            if resp.status_code in (401, 403):
                # "Rejected" and "never sent" are different failures and the
                # operator reading this log has to tell them apart: one means
                # the account is wrong, the other means it is missing.
                if auth is None:
                    cause = (
                        "NO CREDENTIALS SENT: API_USER / API_PASS are not both "
                        "set, so the request went out anonymously"
                    )
                else:
                    # Name the account only. NEVER the password -- not the
                    # value, not a mask, not a length hint (orchestrator.py).
                    cause = (
                        "CREDENTIALS REJECTED: the schema endpoint refused user "
                        f"{api_user!r}"
                    )
                print(
                    f"[CONFIG][SCHEMA] {cause}. Set API_USER / API_PASS to an "
                    "account that can read /nextseek_api/schema/. "
                    "Request-body validation is now DISABLED: "
                    "validate_request_body will pass every body through unchecked."
                )
            return {}

        try:
            spec = yaml.safe_load(resp.text)
        except Exception as e:
            print(f"[CONFIG][SCHEMA] Failed to parse remote schema YAML: {e!r}")
            return {}

        if not isinstance(spec, dict):
            # A misrouted 200 (HTML error page, maintenance banner) parses to
            # a YAML scalar — treat it as no schema, not an AttributeError.
            print("[CONFIG][SCHEMA] Remote schema is not a mapping; skipping.")
            return {}

        api_schema: dict[str, dict] = {}

        components = (spec or {}).get("components", {}).get("schemas", {}) or {}

        paths = (spec or {}).get("paths", {}) or {}

        def _ensure_entry(ep: str) -> dict:
            entry = api_schema.setdefault(ep, {})
            entry.setdefault("allowed_body_fields", [])
            entry.setdefault("required_body_fields", [])
            entry.setdefault("allowed_body_fields_by_method", {})
            entry.setdefault("required_body_fields_by_method", {})
            entry.setdefault("request_schemas", {})
            entry.setdefault("methods", set())
            return entry

        def resolve_ref(node: dict) -> dict:
            if not isinstance(node, dict):
                return {}
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                name = ref.split("/")[-1]
                return components.get(name, {}) or {}
            return {}

        def deep_resolve(node: dict, seen: set[str] | None = None) -> dict:
            seen = seen or set()
            if not isinstance(node, dict):
                return node
            if "$ref" in node:
                ref = node["$ref"]
                if isinstance(ref, str):
                    if ref in seen:
                        return {}
                    seen.add(ref)
                    target = resolve_ref(node)
                    return deep_resolve(target, seen)
            out: dict = {}
            for k, v in node.items():
                if k == "properties" and isinstance(v, dict):
                    out[k] = {pk: deep_resolve(pv, seen) for pk, pv in v.items()}
                elif k in ("items", "additionalProperties") and isinstance(v, dict):
                    out[k] = deep_resolve(v, seen)
                else:
                    out[k] = v
            return out
        for endpoint, methods in paths.items():
            if not isinstance(methods, dict):
                continue

            for method, op in methods.items():
                if not isinstance(op, dict):
                    continue

                method_lower = method.lower()
                if method_lower not in ("post", "put", "patch"):
                    continue

                method_upper = method_lower.upper()

                req_body = op.get("requestBody", {}) or {}
                content = req_body.get("content", {}) or {}
                if not isinstance(content, dict) or not content:
                    continue

                media = None
                if "application/json" in content:
                    media = content["application/json"]
                else:
                    media = next(iter(content.values()), None)

                if not isinstance(media, dict):
                    continue

                schema_node = media.get("schema")
                if not schema_node:
                    continue

                resolved = deep_resolve(schema_node)
                if not isinstance(resolved, dict):
                    continue

                props = resolved.get("properties", {}) or {}
                if not isinstance(props, dict):
                    props = {}

                required = resolved.get("required", []) or []
                if not isinstance(required, list):
                    required = []

                allowed_fields = sorted(list(props.keys()))
                required_fields = sorted(list(set(required)))

                entry = _ensure_entry(endpoint)
                existing_allowed = set(entry.get("allowed_body_fields", []))
                existing_required = set(entry.get("required_body_fields", []))

                entry["allowed_body_fields"] = sorted(existing_allowed | set(allowed_fields))
                entry["required_body_fields"] = sorted(existing_required | set(required_fields))
                entry["allowed_body_fields_by_method"][method_upper] = allowed_fields
                entry["required_body_fields_by_method"][method_upper] = required_fields
                entry["request_schemas"][method_upper] = resolved
                entry["methods"].add(method_upper)

        # Merge catalog methods so GET-only endpoints still get a method
        for path, method in self.CATALOG_ENDPOINT_METHODS.items():
            entry = _ensure_entry(path)
            entry["methods"].add(method)

        # Also merge all catalog methods (for dual GET/POST endpoints)
        for path, methods in self.CATALOG_ENDPOINT_METHODS_ALL.items():
            entry = _ensure_entry(path)
            entry["methods"].update(methods)

        # Finalize method + methods list with priority ordering
        for ep, entry in api_schema.items():
            methods_set = entry.get("methods") or set()
            if not isinstance(methods_set, set):
                methods_set = set(methods_set)
            methods_sorted = sorted(
                methods_set,
                key=lambda m: self.METHOD_PRIORITY.index(m) if m in self.METHOD_PRIORITY else len(self.METHOD_PRIORITY),
            )
            entry["methods"] = methods_sorted
            if methods_sorted:
                entry["method"] = methods_sorted[0]

        print(f"[CONFIG][SCHEMA] Loaded API_SCHEMA for {len(api_schema)} endpoints (remote + catalog).")
        return api_schema

    def get_schema_for_endpoint(self, endpoint: str) -> dict | None:
        """
        Retrieve the cached API schema entry for a given endpoint path.
        Returns None when the endpoint is unknown so callers can skip validation gracefully.
        """
        return self.API_SCHEMA.get(endpoint)

    def validate_request_body(self, endpoint: str, body: dict, method: str | None = None):
        """
        Validate requestBody against a minimal schema for known endpoints.
        Returns (ok: bool, error_payload_or_none: dict|None).
        """
        schema = self.API_SCHEMA.get(endpoint)
        if not schema:
            return True, None

        method_upper = method.upper() if isinstance(method, str) else None
        schema_methods = schema.get("methods") or []
        if isinstance(schema_methods, list):
            schema_methods = [m for m in schema_methods if isinstance(m, str)]

        # If method is GET (or method not in schema and GET-like), allow empty body
        if method_upper == "GET":
            return True, None
        # If the chosen method is not among schema methods, skip validation
        if schema_methods and method_upper and method_upper not in schema_methods:
            return True, None

        allowed_by_method = schema.get("allowed_body_fields_by_method", {})
        required_by_method = schema.get("required_body_fields_by_method", {})
        allowed = set(allowed_by_method.get(method_upper, [])) or set(schema.get("allowed_body_fields", []))
        required = set(required_by_method.get(method_upper, [])) or set(schema.get("required_body_fields", []))
        provided = set(body.keys())

        unknown_fields = [f for f in provided if f not in allowed]
        missing_required = [f for f in required if f not in provided]

        if unknown_fields or missing_required:
            print("[CONFIG][SCHEMA] Validation failed:")
            print(f"  endpoint: {endpoint}")
            print(f"  provided fields: {sorted(provided)}")
            print(f"  unknown_fields: {unknown_fields}")
            print(f"  missing_required: {missing_required}")

            error_payload = {
                "ok": False,
                "error_type": "schema_validation",
                "endpoint": endpoint,
                "provided_fields": sorted(provided),
                "unknown_fields": unknown_fields,
                "missing_required_fields": missing_required,
                "allowed_fields": sorted(allowed),
            }
            return False, error_payload

        print("[CONFIG][SCHEMA] Validation OK for endpoint", endpoint)
        return True, None

    # ======================================================
    # Neo4j / Graph DB helpers
    # ======================================================

    def _connect_neo4j(self):
        """
        Return a connected neo4j Driver, or None when unconfigured or unavailable.
        Caller is responsible for closing the driver after use.
        """
        try:
            from neo4j import GraphDatabase  # type: ignore
        except ImportError:
            print("[CONFIG][GRAPHDB] neo4j driver not installed; run 'uv add neo4j'.")
            return None

        if not getattr(self, "NEO4J_PASSWORD", None):
            print("[CONFIG][GRAPHDB] NEO4J_PASSWORD not set; skipping Neo4j connection.")
            return None

        try:
            try:
                driver = GraphDatabase.driver(
                    self.NEO4J_URI,
                    auth=(self.NEO4J_USER, self.NEO4J_PASSWORD),
                    notifications_min_severity="OFF",
                )
            except TypeError:
                driver = GraphDatabase.driver(
                    self.NEO4J_URI,
                    auth=(self.NEO4J_USER, self.NEO4J_PASSWORD),
                )
            driver.verify_connectivity()
            print(f"[CONFIG][GRAPHDB] Connected to Neo4j at {self.NEO4J_URI}")
            return driver
        except Exception as e:
            print(f"[CONFIG][GRAPHDB] Failed to connect to Neo4j: {e!r}")
            return None

    def _close_neo4j_driver(self, driver) -> None:
        """Close a Neo4j driver instance without surfacing cleanup errors."""
        try:
            driver.close()
        except Exception:
            pass

    def _fetch_neo4j_schema(self) -> dict:
        """
        Introspect the connected Neo4j instance and return its schema as a dict.
        Saves to CONTEXT_DIR/neo4j_schema.json (overwritten on each fetch).
        Returns an empty dict when Neo4j is unavailable.
        """
        driver = self._connect_neo4j()
        if driver is None:
            return {}

        schema: dict = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "node_labels": [],
            "relationship_types": [],
            "node_properties": {},
            "node_property_types": {},
            "relationship_properties": {},
            "relationship_property_types": {},
            "relationship_patterns": [],
            # Vocabulary: actual values for key lookup fields (used by graph agent for value mapping)
            "vocabulary": {
                "study_titles": [],
                "investigation_titles": [],
                "internal_assay_titles": [],
                "sampletype_titles": [],
            },
        }

        try:
            with driver.session(database=self.NEO4J_DATABASE) as db_session:
                result = db_session.run("CALL db.labels()")
                schema["node_labels"] = [r["label"] for r in result]

                result = db_session.run("CALL db.relationshipTypes()")
                schema["relationship_types"] = [r["relationshipType"] for r in result]

                for label in schema["node_labels"]:
                    try:
                        result = db_session.run(
                            f"MATCH (n:`{label}`) UNWIND keys(n) AS k RETURN DISTINCT k LIMIT 200"
                        )
                        schema["node_properties"][label] = [r["k"] for r in result]
                    except Exception as e:
                        print(f"[CONFIG][GRAPHDB] Props for label {label!r} failed: {e!r}")

                for rel in schema["relationship_types"]:
                    try:
                        result = db_session.run(
                            f"MATCH ()-[r:`{rel}`]-() UNWIND keys(r) AS k RETURN DISTINCT k LIMIT 200"
                        )
                        schema["relationship_properties"][rel] = [r["k"] for r in result]
                    except Exception as e:
                        print(f"[CONFIG][GRAPHDB] Props for rel {rel!r} failed: {e!r}")

                try:
                    result = db_session.run(
                        "CALL db.schema.visualization() YIELD nodes, relationships "
                        "RETURN nodes, relationships"
                    )
                    for record in result:
                        for rel in (record.get("relationships") or []):
                            try:
                                pattern = {
                                    "start": list(rel.start_node.labels)[0] if rel.start_node.labels else None,
                                    "type": rel.type,
                                    "end": list(rel.end_node.labels)[0] if rel.end_node.labels else None,
                                }
                                schema["relationship_patterns"].append(pattern)
                            except Exception:
                                pass
                except Exception as e:
                    print(f"[CONFIG][GRAPHDB] Schema visualization failed (non-fatal): {e!r}")

                # Property types per node label (e.g. {Investigation: {project_id: Long, title: String}})
                try:
                    result = db_session.run(
                        "CALL db.schema.nodeTypeProperties() YIELD nodeLabels, propertyName, propertyTypes "
                        "RETURN nodeLabels, propertyName, propertyTypes"
                    )
                    node_prop_types: dict = {}
                    for r in result:
                        for label in (r["nodeLabels"] or []):
                            node_prop_types.setdefault(label, {})[r["propertyName"]] = r["propertyTypes"]
                    schema["node_property_types"] = node_prop_types
                    print(f"[CONFIG][GRAPHDB] Fetched node property types for {len(node_prop_types)} labels")
                except Exception as e:
                    print(f"[CONFIG][GRAPHDB] nodeTypeProperties failed (non-fatal): {e!r}")

                # Property types per relationship type
                try:
                    result = db_session.run(
                        "CALL db.schema.relTypeProperties() YIELD relType, propertyName, propertyTypes "
                        "RETURN relType, propertyName, propertyTypes"
                    )
                    rel_prop_types: dict = {}
                    for r in result:
                        rel = (r["relType"] or "").strip("`")
                        if r["propertyName"]:
                            rel_prop_types.setdefault(rel, {})[r["propertyName"]] = r["propertyTypes"]
                    schema["relationship_property_types"] = rel_prop_types
                    print(f"[CONFIG][GRAPHDB] Fetched rel property types for {len(rel_prop_types)} rel types")
                except Exception as e:
                    print(f"[CONFIG][GRAPHDB] relTypeProperties failed (non-fatal): {e!r}")

                # Vocabulary: Study titles
                try:
                    result = db_session.run(
                        "MATCH (s:Study) WHERE s.title IS NOT NULL "
                        "RETURN DISTINCT s.title AS title ORDER BY s.title"
                    )
                    schema["vocabulary"]["study_titles"] = [r["title"] for r in result]
                    print(f"[CONFIG][GRAPHDB] Fetched {len(schema['vocabulary']['study_titles'])} study titles")
                except Exception as e:
                    print(f"[CONFIG][GRAPHDB] Study title fetch failed: {e!r}")

                # Vocabulary: Investigation titles
                try:
                    result = db_session.run(
                        "MATCH (i:Investigation) WHERE i.title IS NOT NULL "
                        "RETURN DISTINCT i.title AS title ORDER BY i.title"
                    )
                    schema["vocabulary"]["investigation_titles"] = [r["title"] for r in result]
                    print(f"[CONFIG][GRAPHDB] Fetched {len(schema['vocabulary']['investigation_titles'])} investigation titles")
                except Exception as e:
                    print(f"[CONFIG][GRAPHDB] Investigation title fetch failed: {e!r}")

                # Vocabulary: DERIVED_FROM.internal_assay_title (assay in user language)
                try:
                    result = db_session.run(
                        "MATCH ()-[r:DERIVED_FROM]-() WHERE r.internal_assay_title IS NOT NULL "
                        "RETURN DISTINCT r.internal_assay_title AS title ORDER BY title"
                    )
                    schema["vocabulary"]["internal_assay_titles"] = [r["title"] for r in result]
                    print(f"[CONFIG][GRAPHDB] Fetched {len(schema['vocabulary']['internal_assay_titles'])} internal assay titles")
                except Exception as e:
                    print(f"[CONFIG][GRAPHDB] Internal assay title fetch failed: {e!r}")


        except Exception as e:
            print(f"[CONFIG][GRAPHDB] Schema introspection failed: {e!r}")
        finally:
            self._close_neo4j_driver(driver)

        # Derive a human-readable topology from relationship_patterns for the graph agent
        schema["graph_topology"] = [
            f"{p['start']} -[:{p['type']}]-> {p['end']}"
            for p in schema["relationship_patterns"]
            if p.get("start") and p.get("type") and p.get("end")
        ]

        schema_path = Path(self.CONTEXT_DIR) / "neo4j_schema.json"
        try:
            schema_path.parent.mkdir(parents=True, exist_ok=True)
            schema_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")
            print(f"[CONFIG][GRAPHDB] Neo4j schema saved to {schema_path} "
                  f"({len(schema['node_labels'])} labels, {len(schema['relationship_types'])} rel types)")
        except Exception as e:
            print(f"[CONFIG][GRAPHDB] Failed to save neo4j schema: {e!r}")

        return schema

    def _ensure_schema_file(self, filename: str, fetch_fn, label: str) -> dict:
        """Load a daily-cached schema JSON; fall back to fetch_fn() when stale or missing."""
        schema_path = Path(self.CONTEXT_DIR) / filename
        if self._is_today(schema_path):
            try:
                data = json.loads(schema_path.read_text(encoding="utf-8"))
                print(f"[CONFIG][GRAPHDB] {label} is fresh; loaded from {schema_path}")
                return data
            except Exception as e:
                print(f"[CONFIG][GRAPHDB] Failed to load {schema_path}: {e!r}")
        print(f"[CONFIG][GRAPHDB] {label} stale or missing; fetching from Neo4j.")
        return fetch_fn()

    def _ensure_neo4j_schema(self) -> dict:
        """Load the cached Neo4j schema when fresh, otherwise fetch and persist a new copy."""
        return self._ensure_schema_file("neo4j_schema.json", self._fetch_neo4j_schema, "Neo4j schema")

    def _fetch_assay_sample_connections(self) -> dict:
        """
        Fetch distinct (assay, parent_type, child_type) tuples from DERIVED_FROM relationships.
        Saves to CONTEXT_DIR/neo4j_assay-sample-conn.json.
        Kept separate from neo4j_schema.json — injected into the graph agent only when needed.
        Returns an empty dict when Neo4j is unavailable.
        """
        driver = self._connect_neo4j()
        if driver is None:
            return {}

        connections: list[dict] = []
        try:
            with driver.session(database=self.NEO4J_DATABASE) as db_session:
                result = db_session.run(
                    "MATCH (c:Sample)-[r:DERIVED_FROM]->(p:Sample) "
                    "WHERE r.internal_assay_title IS NOT NULL "
                    "RETURN DISTINCT r.internal_assay_title AS assay, p.type AS parent_type, c.type AS child_type "
                    "ORDER BY assay LIMIT 300"
                )
                connections = [
                    {"assay": r["assay"], "parent_type": r["parent_type"], "child_type": r["child_type"]}
                    for r in result
                ]
                print(f"[CONFIG][GRAPHDB] Fetched {len(connections)} assay-sample connections")
        except Exception as e:
            print(f"[CONFIG][GRAPHDB] Assay-sample connections fetch failed: {e!r}")
            return {}
        finally:
            self._close_neo4j_driver(driver)

        payload = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "connections": connections,
        }
        schema_path = Path(self.CONTEXT_DIR) / "neo4j_assay-sample-conn.json"
        try:
            schema_path.parent.mkdir(parents=True, exist_ok=True)
            schema_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(f"[CONFIG][GRAPHDB] Assay-sample connections saved to {schema_path}")
        except Exception as e:
            print(f"[CONFIG][GRAPHDB] Failed to save assay-sample connections: {e!r}")

        return payload

    def _ensure_assay_sample_connections(self) -> dict:
        """Load or refresh the cached assay-to-sample connection lookup used by graph prompts."""
        return self._ensure_schema_file("neo4j_assay-sample-conn.json", self._fetch_assay_sample_connections, "Assay-sample connections")

    def _fetch_protocol_schema(self) -> dict:
        """
        Fetch distinct DERIVED_FROM.protocol_title values from Neo4j.
        Saves to CONTEXT_DIR/neo4j_protocol_schema.json (overwritten on each fetch).
        Kept separate from neo4j_schema.json because protocol context is only injected
        into the graph agent prompt when the user query is protocol-related.
        Returns an empty dict when Neo4j is unavailable.
        """
        driver = self._connect_neo4j()
        if driver is None:
            return {}

        titles: list[str] = []
        try:
            with driver.session(database=self.NEO4J_DATABASE) as db_session:
                result = db_session.run(
                    "MATCH ()-[r:DERIVED_FROM]-() WHERE r.protocol_title IS NOT NULL "
                    "RETURN DISTINCT r.protocol_title AS title ORDER BY title"
                )
                titles = [r["title"] for r in result]
                print(f"[CONFIG][GRAPHDB] Fetched {len(titles)} protocol titles")
        except Exception as e:
            print(f"[CONFIG][GRAPHDB] Protocol title fetch failed: {e!r}")
            return {}
        finally:
            self._close_neo4j_driver(driver)

        payload = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "protocol_titles": titles,
        }
        schema_path = Path(self.CONTEXT_DIR) / "neo4j_protocol_schema.json"
        try:
            schema_path.parent.mkdir(parents=True, exist_ok=True)
            schema_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(f"[CONFIG][GRAPHDB] Protocol schema saved to {schema_path}")
        except Exception as e:
            print(f"[CONFIG][GRAPHDB] Failed to save protocol schema: {e!r}")

        return payload

    def _ensure_protocol_schema(self) -> dict:
        """Load or refresh the cached protocol-title vocabulary for protocol-oriented graph queries."""
        return self._ensure_schema_file("neo4j_protocol_schema.json", self._fetch_protocol_schema, "Protocol schema")

    def get_config_snapshot(self) -> dict[str, object]:
        """
        Return a sanitized snapshot of key configuration values for logging.
        Secrets are reduced to boolean SET/NOT SET flags.
        """
        planner_agents = {
            label: self.agent_config(label)
            for label in ("multi_parser", "planner", "context_engineer", "evaluator")
        }
        source_env_names = getattr(self, "CONFIG_SOURCE_ENV_NAMES", None)
        if source_env_names is None:
            raw_source_env_names = os.getenv("CHAT_NEXTSEEK_CONFIG_SOURCE_ENV_NAMES")
            if raw_source_env_names:
                try:
                    source_env_names = json.loads(raw_source_env_names)
                except Exception:
                    source_env_names = {"raw": raw_source_env_names}
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "paths": {
                "base_dir": str(self.BASE_DIR),
                "context_dir": str(self.CONTEXT_DIR),
            },
            "models": {
                "mode": self.MODEL_MODE,
                "llm_model": self.LLM_MODEL,
                "catalog_key": getattr(self, "_CATALOG_KEY", "default"),
            },
            "api": {
                "base_url_set": bool(self.NEXTSEEK_BASE_URL),
                "api_user_set": bool(self.API_USER),
                "api_pass_set": bool(self.API_PASS),
                "source_env_names": (source_env_names or {}).get("api", {}),
            },
            "db": {
                "host_prod_set": bool(self.MYSQL_HOST_PROD),
                "host_dev_set": bool(self.MYSQL_HOST_DEV),
                "port": self.MYSQL_PORT,
                "user_set": bool(self.MYSQL_USER),
                "prod_password_set": bool(self.MYSQL_PROD_PASSWORD),
                "dev_password_set": bool(self.MYSQL_DEV_PASSWORD),
            },
            "context_counts": {
                "full_sampletypes": len(self.FULL_SAMPLETYPES) if isinstance(self.FULL_SAMPLETYPES, list) else 0,
                "full_assays": len(self.FULL_ASSAYS) if isinstance(self.FULL_ASSAYS, list) else 0,
                "min_sampletypes": len(self.MIN_SAMPLETYPES),
                "min_assays": len(self.MIN_ASSAYS),
                "min_api_endpoints": len(self.MIN_API_ENDPOINTS),
                "min_graph_schema_keys": len(self.MIN_GRAPH_SCHEMA) if isinstance(self.MIN_GRAPH_SCHEMA, dict) else 0,
                "agent_model_catalog_key": getattr(self, "_CATALOG_KEY", "default"),
            },
            "planner": {
                "plan_agents": planner_agents,
            },
            "agent_routes": self.AGENT_MODEL_CATALOG.get(getattr(self, "_CATALOG_KEY", "default"), {}),
            "api_schema": {
                "endpoints": len(self.API_SCHEMA),
                "catalog_methods": len(self.CATALOG_ENDPOINT_METHODS),
            },
            "graph_db": {
                "neo4j_uri": self.NEO4J_URI,
                "neo4j_user": self.NEO4J_USER,
                "neo4j_password_set": bool(self.NEO4J_PASSWORD),
                "source_env_names": (source_env_names or {}).get("graph_db", {}),
                "schema_node_labels": len(self.NEO4J_SCHEMA.get("node_labels", [])) if self.NEO4J_SCHEMA else 0,
                "schema_rel_types": len(self.NEO4J_SCHEMA.get("relationship_types", [])) if self.NEO4J_SCHEMA else 0,
            },
            "context_json_paths": {k: str(v) for k, v in self.CONTEXT_JSON_PATHS.items()},
        }
