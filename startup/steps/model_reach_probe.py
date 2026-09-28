"""Which model ids the deployed config would call, and whether each one can be
reached with the credentials its calls really carry. Free metadata calls only.

This file runs INSIDE the containers. The stack-health check ``model ids
reachable`` (``startup/steps/deploy_checks.py``) pipes it into ``python -``:

* in the running app container, role ``app``: it derives every model id from the
  deployed files (the agent model catalog and its fallback rules, the BAML clients
  and the router's client names, the Container-CC model map) and the container's
  own env, the way the app resolves them, then asks about the ids the app calls
  itself: Gemini with the app's own key, Bedrock with the app's own token (the NS
  Bedrock client dials Bedrock directly, never through the proxy);
* in the running bedrock-proxy, role ``proxy``: it asks Bedrock about the
  Container-CC ids with the proxy's own token, the one every CC call carries.

Only GET requests on metadata endpoints: Gemini ``models.get`` and Bedrock
``GetInferenceProfile``, ``GetFoundationModel`` and
``GetFoundationModelAvailability``. None of them runs a model or is billed. What
``ok`` proves, and what no free call can (that the credential may INVOKE the
model, and has quota for it), is in ``deploy_checks.check_model_reach``.

Standard library only, Python 3.11 or later: the proxy image has no boto3, and the
startup tests import this module (the derivation runs against the real checkout,
every HTTP answer is stubbed). Nothing here prints a key or a token, and AWS
messages are printed with ARNs and account numbers masked.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

MARKER = "MODEL-REACH"

# The files the ids come from, repo-relative (the app image holds the tree at /app).
# Pinned to the real tree by startup/tests/test_model_reach.py.
NS_ROOT = "NessieAI/chat_nextseek"
NS_SRC = NS_ROOT + "/src/chat_nextseek"
CATALOG = NS_ROOT + "/agent_model_catalog.json"
SCHEMA_HELPER = NS_SRC + "/schemas/schema_helper.py"
CLASS_MAP = "NessieAI/dmac_assistant/build_context/router_model_class_map.json"
CLASS_MAP_LOADER = "NessieAI/dmac_assistant/src/dmac_assistant/router/models.py"
BAML_SRC = "NessieAI/dmac_assistant/baml_src"
ROUTER = "NessieAI/router/router.py"

# What each id is marked. The first three are red: a call to that id cannot succeed.
OK = "ok"
NOT_FOUND = "not found"
DENIED = "access denied"
NO_CREDENTIAL = "no credential"
UNKNOWN = "unknown"
PENDING = "pending"  # asked in the other container
RED = (NOT_FOUND, DENIED, NO_CREDENTIAL)

# How a call reaches its model.
GEMINI = "gemini"    # the Gemini API, from the app, with the app's key
BEDROCK = "bedrock"  # Bedrock directly, from the app, with the app's token
PROXY = "proxy"      # Bedrock through the bedrock-proxy, with the proxy's token
OTHER = "other"      # a provider this check does not ask

BEDROCK_TOKEN_ENV = "AWS_BEARER_TOKEN_BEDROCK"

HTTP_TIMEOUT_S = 8.0
BUDGET_S = 60.0


# --------------------------------------------------------------------------- #
# the NS engine: agent model catalog, profile, fallback chains
# --------------------------------------------------------------------------- #

# Mirrors of chat_nextseek/config.py ChatConfig._detect_model_mode and
# _resolve_catalog_key (a gunicorn worker has no -m flag, so only NEXTSEEK_MODE
# counts). Pinned to that file by startup/tests/test_model_reach.py.
VALID_MODES = (
    "oai", "gcp", "mixed",
    "gcp:current", "gcp:lite",
    "anth", "anth:current", "anth:lite",
    "aws:son", "aws:opus", "aws:ds", "aws:qwen-nxt", "aws:glm",
)

# The agents that run a tool loop (tool_loop.call_tools). A loop moves only to a
# client with a tool surface (tool_loop._tool_capable), which is the Bedrock client
# alone. Pinned to the call sites by startup/tests/test_model_reach.py.
TOOL_LOOP_AGENTS = ("followup", "pipeline_agent")
TOOL_CAPABLE_PROVIDERS = ("anth",)

# Catalog provider -> (path, the env var holding its credential).
_PROVIDER_PATHS = {
    "gcp": (GEMINI, "GCP_API_KEY"),
    "anth": (BEDROCK, BEDROCK_TOKEN_ENV),
    "oai": (OTHER, "OPENAI_API_KEY"),
}


def detect_mode(env: Mapping[str, str]) -> str:
    mode = (env.get("NEXTSEEK_MODE") or "mixed").strip().lower()
    return mode if mode in VALID_MODES else "mixed"


def catalog_key(mode: str) -> str:
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


def primary_provider(mode: str) -> str:
    """The catalog provider of the mode's LLM_CLIENT, the client an agent with no
    provider, or with a provider whose client was not built, is given."""
    if mode in ("gcp", "mixed") or mode.startswith("gcp:"):
        return "gcp"
    if mode == "anth" or mode.startswith(("anth:", "aws:")):
        return "anth"
    return "oai"


def _llm_model_env(mode: str) -> str:
    if mode in ("gcp", "mixed") or mode.startswith("gcp:"):
        return "GCP_LLM_MODEL"
    if mode == "anth" or mode.startswith("anth:"):
        return "ANTH_LLM_MODEL"
    if mode.startswith("aws:"):
        return "BEDROCK_LLM_MODEL"
    return "NEXTSEEK_LLM_MODEL"


def available_providers(mode: str, env: Mapping[str, str]) -> set[str]:
    """ChatConfig._build_secondary_clients: the primary client always, a second one
    only when its credential is set."""
    have = {primary_provider(mode)}
    for provider, (_path, credential) in _PROVIDER_PATHS.items():
        if env.get(credential):
            have.add(provider)
    return have


def load_catalog(root: Path, env: Mapping[str, str]) -> tuple[dict, str]:
    """The catalog the app loads: AGENT_MODEL_CATALOG (JSON in the env), else the
    file CATALOG_FILE names, else the one beside chat_nextseek."""
    raw = env.get("AGENT_MODEL_CATALOG")
    if raw:
        return json.loads(raw), "AGENT_MODEL_CATALOG"
    path = env.get("CATALOG_FILE") or str(root / CATALOG)
    return json.loads(Path(path).read_text(encoding="utf-8")), path


def _expand_routes(name: str, routes: list) -> dict:
    out: dict[str, dict] = {}
    for route in routes:
        if not isinstance(route, dict) or not isinstance(route.get("agents"), list):
            raise ValueError(f"catalog profile {name!r} has a route without an agents list")
        for agent in route["agents"]:
            out[agent] = {"provider": route.get("provider"), "model": route.get("model")}
    return out


def _expand_models(name: str, models: dict) -> dict:
    out: dict[str, dict] = {}
    for model, entry in models.items():
        for variant in (entry if isinstance(entry, list) else [entry]):
            if not isinstance(variant, dict) or not isinstance(variant.get("agents"), list):
                raise ValueError(f"catalog profile {name!r} model {model!r} has no agents list")
            for agent in variant["agents"]:
                out[agent] = {"provider": variant.get("provider"),
                              "model": None if model == "__default__" else model}
    return out


def normalize_catalog(raw: dict) -> dict:
    """ChatConfig._normalize_agent_model_catalog: every profile as {agent: entry};
    keys starting with "_" (the fallback block) pass through."""
    if not isinstance(raw, dict):
        raise ValueError("the agent model catalog is not a JSON object")
    out: dict[str, Any] = {}
    for name, value in raw.items():
        if name.startswith("_"):
            out[name] = value
        elif isinstance(value, list):
            out[name] = _expand_routes(name, value)
        elif isinstance(value, dict) and "models" in value:
            out[name] = _expand_models(name, value["models"])
        elif isinstance(value, dict) and "routes" in value:
            out[name] = _expand_routes(name, value["routes"])
        elif isinstance(value, dict):
            out[name] = value
        else:
            raise ValueError(f"catalog profile {name!r} is neither an object nor a list")
    return out


def agent_entry(catalog: dict, key: str, agent: str) -> tuple[str | None, str | None]:
    """ChatConfig.agent_config: the active profile's entry, filled from "default"."""
    over = (catalog.get(key) or {}).get(agent) or {}
    base = (catalog.get("default") or {}).get(agent) or {}
    model = over.get("model") or base.get("model") or None
    provider = over.get("provider") if "provider" in over else base.get("provider")
    return provider, model


def _module_constants(path: Path, names: tuple[str, ...]) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, Any] = {}
    for node in tree.body:
        target = value = None
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        if isinstance(target, ast.Name) and target.id in names and value is not None:
            try:
                found[target.id] = ast.literal_eval(value)
            except ValueError:
                continue
    return found


FALLBACK_NAMES = ("_FALLBACK_CHAINS", "FALLBACK_OVERRIDE_KEY", "MAX_PROVIDER_SWITCHES")


def read_fallback_rules(root: Path) -> tuple[dict, str, int]:
    """The provider chains, the catalog's per-agent fallback block and the number of
    moves a call gets, read (never restated) from schema_helper.py, or from wherever
    under chat_nextseek they have moved to."""
    found = _module_constants(root / SCHEMA_HELPER, FALLBACK_NAMES)
    if len(found) < len(FALLBACK_NAMES):
        for path in sorted((root / NS_SRC).rglob("*.py")):
            for name, value in _module_constants(path, FALLBACK_NAMES).items():
                found.setdefault(name, value)
    missing = [n for n in FALLBACK_NAMES if n not in found]
    if missing:
        raise ValueError(f"no {', '.join(missing)} under {NS_SRC}")
    return found["_FALLBACK_CHAINS"], found["FALLBACK_OVERRIDE_KEY"], int(found["MAX_PROVIDER_SWITCHES"])


def fallback_calls(catalog: dict, key: str, agent: str, failed: tuple[str, str | None], *,
                   chains: dict, override_key: str, have: set[str], primary: str,
                   llm_model: str | None) -> list[tuple[str, str | None]]:
    """schema_helper._get_fallback_agent_configs, and the tool loop's filter: the
    ordered (provider, model) a call moves to after its primary fails."""
    candidates: list[Any] = []
    override = catalog.get(override_key)
    if isinstance(override, dict) and isinstance(override.get(agent), dict):
        candidates.append(override[agent])
    for profile in chains.get((key, failed[0]), []):
        candidates.append((catalog.get(profile) or {}).get(agent))
    out: list[tuple[str, str | None]] = []
    for cfg in candidates:
        if not isinstance(cfg, dict) or not cfg:
            continue
        provider = cfg.get("provider")
        model = cfg.get("model") or llm_model
        if provider and provider not in have:
            continue  # its client was never built
        route = provider or primary
        if (route, model) == failed:
            continue
        if agent in TOOL_LOOP_AGENTS and route not in TOOL_CAPABLE_PROVIDERS:
            continue
        out.append((route, model))
    return out


def ns_uses(root: Path, env: Mapping[str, str]) -> tuple[list[dict], dict]:
    """Every NS agent's model and the one fallback each call moves to."""
    mode = detect_mode(env)
    key = catalog_key(mode)
    primary = primary_provider(mode)
    raw, source = load_catalog(root, env)
    catalog = normalize_catalog(raw)
    chains, override_key, switches = read_fallback_rules(root)
    have = available_providers(mode, env)
    llm_env = _llm_model_env(mode)
    llm_model = env.get(llm_env) or None

    agents = sorted({agent for profile in (key, "default")
                     for agent, entry in (catalog.get(profile) or {}).items()
                     if isinstance(entry, dict)})
    uses: list[dict] = []

    def add(provider: str, model: str | None, user: str, misrouted_from: str | None = None) -> None:
        path, credential = _PROVIDER_PATHS.get(provider, (OTHER, None))
        if model is None:
            uses.append({"id": f"LLM_MODEL of mode {mode}", "path": OTHER, "credential": None,
                         "users": [user],
                         "preset": [UNKNOWN, f"config.py picks it for mode {mode}; set {llm_env} "
                                             "in the app's env to make it checkable"]})
            return
        use = {"id": model, "path": path, "credential": credential, "users": [user]}
        if misrouted_from:
            want = _PROVIDER_PATHS.get(misrouted_from, (OTHER, misrouted_from))[1]
            use["preset"] = [NO_CREDENTIAL, f"the app has no {want}, so these calls go to "
                                            f"its {provider} client with this id"]
        uses.append(use)

    for agent in agents:
        provider, model = agent_entry(catalog, key, agent)
        model = model or llm_model
        route = provider if provider in have else primary
        add(route, model, agent, provider if provider and provider not in have else None)
        moves = fallback_calls(catalog, key, agent, (route, model), chains=chains,
                               override_key=override_key, have=have, primary=primary,
                               llm_model=llm_model)
        for fb_route, fb_model in moves[:switches]:
            add(fb_route, fb_model, f"fallback of {agent}")
    return uses, {"mode": mode, "profile": key, "catalog": source, "moves": switches}


# --------------------------------------------------------------------------- #
# the router and the CC summary: BAML clients
# --------------------------------------------------------------------------- #

_CLIENT_HEAD = re.compile(r"\bclient<llm>\s+(\w+)\s*\{")
_FUNCTION_HEAD = re.compile(r"\bfunction\s+(\w+)\s*\(")
_CLIENT_REF = re.compile(r"\bclient\s+(?:\"([^\"]+)\"|(\w+))")
_ROUTER_CLIENT_CONST = re.compile(r"^ROUTER_(\w+)_CLIENT$")
# BAML's own default key variable per provider, used when a client names none.
_BAML_DEFAULT_KEY = {"google-ai": "GOOGLE_API_KEY"}
_BAML_PATHS = {"google-ai": GEMINI}


def _strip_line_comments(text: str) -> str:
    """Drop whole-line ``//`` comments, which can name a client in prose."""
    return "\n".join("" if line.lstrip().startswith("//") else line for line in text.splitlines())


def _braced(text: str, open_at: int) -> str:
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at:i + 1]
    return text[open_at:]


def baml_sources(root: Path) -> list[str]:
    return [_strip_line_comments(p.read_text(encoding="utf-8"))
            for p in sorted((root / BAML_SRC).glob("*.baml"))]


def baml_clients(texts: list[str]) -> dict[str, dict]:
    clients: dict[str, dict] = {}
    for text in texts:
        for m in _CLIENT_HEAD.finditer(text):
            body = _braced(text, m.end() - 1)
            provider = re.search(r"\bprovider\s+\"?([\w-]+)", body)
            model = re.search(r"\bmodel\s+\"([^\"]+)\"", body)
            key = re.search(r"\bapi_key\s+env\.(\w+)", body)
            strategy = re.search(r"\bstrategy\s*\[([^\]]*)\]", body)
            clients[m.group(1)] = {
                "provider": provider.group(1) if provider else None,
                "model": model.group(1) if model else None,
                "key_env": key.group(1) if key else None,
                "members": re.findall(r"\w+", strategy.group(1)) if strategy else [],
            }
    return clients


def baml_functions(texts: list[str]) -> dict[str, str]:
    """Each function and the client it names (a client name or a "provider/model")."""
    functions: dict[str, str] = {}
    for text in texts:
        for m in _FUNCTION_HEAD.finditer(text):
            opening = text.find("{", m.end())
            if opening < 0:
                continue
            head = text[opening:]
            stop = min([i for i in (head.find("prompt"), head.find("function", 1)) if i > 0]
                       or [len(head)])
            ref = _CLIENT_REF.search(head[:stop])
            if ref:
                functions[m.group(1)] = ref.group(1) or ref.group(2)
    return functions


def router_clients(root: Path) -> dict[str, list[str]]:
    """The router's per-call client names (ROUTER_<ROLE>_CLIENT in router.py)."""
    path = root / ROUTER
    if not path.is_file():
        return {}
    out: dict[str, list[str]] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            m = _ROUTER_CLIENT_CONST.match(node.targets[0].id)
            if m:
                out.setdefault(node.value.value, []).append(
                    "router " + m.group(1).lower().replace("_", " "))
    return out


def baml_uses(root: Path) -> list[dict]:
    texts = baml_sources(root)
    clients = baml_clients(texts)
    users: dict[str, list[str]] = {}
    for function, ref in baml_functions(texts).items():
        users.setdefault(ref, []).append(f"BAML {function}")
    for client, labels in router_clients(root).items():
        users.setdefault(client, []).extend(labels)

    uses: list[dict] = []

    def leaves(ref: str, seen: tuple = ()) -> list[tuple[str | None, str | None, str | None]]:
        if "/" in ref and ref not in clients:
            provider, model = ref.split("/", 1)
            return [(provider, model, None)]
        spec = clients.get(ref)
        if spec is None or ref in seen:
            return [(None, None, None)]
        if spec["members"]:
            return [leaf for member in spec["members"] for leaf in leaves(member, seen + (ref,))]
        return [(spec["provider"], spec["model"], spec["key_env"])]

    for ref, labels in users.items():
        for provider, model, key_env in leaves(ref):
            path = _BAML_PATHS.get(provider or "", OTHER)
            credential = key_env or _BAML_DEFAULT_KEY.get(provider or "")
            use = {"id": model or f"BAML client {ref}", "path": path,
                   "credential": credential if path != OTHER else None, "users": list(labels)}
            if model is None:
                use["preset"] = [UNKNOWN, f"no BAML client {ref} with a model in {BAML_SRC}"]
            elif path == OTHER:
                use["preset"] = [UNKNOWN, f"BAML provider {provider} is not asked by this check"]
            uses.append(use)
    return uses


# --------------------------------------------------------------------------- #
# Container-CC: the model map (through the bedrock-proxy)
# --------------------------------------------------------------------------- #

CC_MAP_ENV = "DMAC_ROUTER_MODEL_CLASS_MAP_FILE"
CC_CLASSIFIER_ENV = "NEXTSEEK_CC_DEFAULT_SONNET_MODEL"
# (map key, who calls it): dmac_assistant.router.models resolve_cc_model,
# resolve_cc_fallback_model and resolve_cc_classifier_model. The map's other keys
# (haiku) are never passed to a CC turn.
CC_KEYS = (("opus", "CC main"), ("opus_fallback", "CC fallback"), ("sonnet", "CC classifier"))


def _class_map_id_pattern(root: Path) -> str:
    """models.py's _BEDROCK_ID_RE, the check a fallback id and the classifier
    override must pass to be used at all."""
    path = root / CLASS_MAP_LOADER
    if path.is_file():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "_BEDROCK_ID_RE" for t in node.targets) \
                    and isinstance(node.value, ast.Call) and node.value.args \
                    and isinstance(node.value.args[0], ast.Constant):
                return node.value.args[0].value
    return r".+"


def cc_uses(root: Path, env: Mapping[str, str]) -> list[dict]:
    path = (env.get(CC_MAP_ENV) or "").strip() or str(root / CLASS_MAP)
    model_map = json.loads(Path(path).read_text(encoding="utf-8"))
    pattern = re.compile(_class_map_id_pattern(root))
    override = (env.get(CC_CLASSIFIER_ENV) or "").strip()
    uses: list[dict] = []
    for key, user in CC_KEYS:
        model = model_map.get(key)
        if key == "sonnet" and override and pattern.match(override):
            model = override
        if key == "opus" and not model:
            raise ValueError(f"the CC model map {path} has no opus entry")
        if not model or (key != "opus" and not pattern.match(model)):
            continue  # the engine runs the turn without it
        uses.append({"id": model, "path": PROXY, "credential": BEDROCK_TOKEN_ENV, "users": [user]})
    return uses


# --------------------------------------------------------------------------- #
# one list
# --------------------------------------------------------------------------- #

def merge(uses: list[dict]) -> list[dict]:
    """One entry per (id, path, credential), its users in first-seen order."""
    merged: dict[tuple, dict] = {}
    for use in uses:
        k = (use["id"], use["path"], use.get("credential"))
        if k not in merged:
            merged[k] = {**use, "users": []}
        entry = merged[k]
        if use.get("preset") and not entry.get("preset"):
            entry["preset"] = use["preset"]
        for user in use["users"]:
            if user not in entry["users"]:
                entry["users"].append(user)
    return list(merged.values())


def derive(root: Path, env: Mapping[str, str]) -> dict:
    ns, about = ns_uses(root, env)
    return {**about, "uses": merge(ns + baml_uses(root) + cc_uses(root, env))}


# --------------------------------------------------------------------------- #
# asking, for free
# --------------------------------------------------------------------------- #

HttpGet = Callable[[str, dict], tuple]  # (url, headers) -> (status|None, headers, body, error)


def http_get(url: str, headers: dict) -> tuple[int | None, dict, bytes, str | None]:
    """One GET. Never raises; a transport failure is (None, {}, b"", reason)."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read(), None
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, exc.read() or b"", None
    except Exception as exc:  # noqa: BLE001 - any transport failure is "could not tell"
        return None, {}, b"", f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"


_ARN = re.compile(r"arn:aws[\w-]*:[^\s\"',]+")
_ACCOUNT = re.compile(r"\b\d{12}\b")


def mask(text: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<credential>")
    return _ACCOUNT.sub("<account>", _ARN.sub("<arn>", text)).strip()[:160]


def _json(body: bytes) -> dict:
    try:
        value = json.loads(body.decode("utf-8", "replace") or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models/"


def check_gemini(model: str, key: str, get: HttpGet = http_get) -> tuple[str, str]:
    """``models.get`` with the key in a header, never in the URL."""
    status, _headers, body, error = get(GEMINI_BASE + urllib.parse.quote(model, safe=""),
                                        {"x-goog-api-key": key, "Accept": "application/json"})
    doc = _json(body)
    err = doc.get("error") if isinstance(doc.get("error"), dict) else {}
    message = mask(str(err.get("message") or error or ""), (key,))
    if status == 200:
        methods = doc.get("supportedGenerationMethods")
        if isinstance(methods, list) and "generateContent" not in methods:
            return NOT_FOUND, "the model takes no generateContent calls"
        return OK, "models.get answered"
    if status == 404:
        return NOT_FOUND, f"models.get 404: {message}"
    if status in (400, 401, 403):
        reasons = " ".join(str(d.get("reason", "")) for d in err.get("details") or []
                           if isinstance(d, dict))
        if status == 400 and "API_KEY" not in reasons and "API key" not in message:
            return NOT_FOUND, f"models.get 400: {message}"
        return DENIED, f"the key is refused ({status}): {message}"
    if status is None:
        return UNKNOWN, f"Gemini did not answer: {message}"
    return UNKNOWN, f"models.get {status}: {message}"


_AUTH_FAILURES = ("UnrecognizedClientException", "InvalidSignatureException",
                  "ExpiredTokenException", "IncompleteSignatureException",
                  "MissingAuthenticationTokenException", "InvalidClientTokenId")
_AUTH_WORDS = ("api key", "authentication failed", "security token", "bearer token")


def _aws_error(status: int | None, headers: dict, body: bytes, error: str | None,
               token: str) -> tuple[str, str]:
    doc = _json(body)
    kind = (headers.get("x-amzn-errortype") or doc.get("__type") or doc.get("code") or "")
    kind = str(kind).split(":", 1)[0].rsplit("#", 1)[-1]
    message = mask(str(doc.get("message") or doc.get("Message") or error or ""), (token,))
    return kind, message


def _aws_verdict(api: str, status: int | None, kind: str, message: str) -> tuple[str, str]:
    """What an error answer to a metadata call says about the id."""
    if status is None:
        return UNKNOWN, f"Bedrock did not answer {api}: {message}"
    if status == 401 or kind in _AUTH_FAILURES or any(w in message.lower() for w in _AUTH_WORDS):
        return DENIED, f"the token is refused ({status} {kind}): {message}"
    if status == 404 or kind == "ResourceNotFoundException":
        return NOT_FOUND, f"{api}: {kind or status}: {message}"
    if status == 400 or kind == "ValidationException":
        return NOT_FOUND, f"{api}: {kind or status}: {message}"
    if status == 403 or kind == "AccessDeniedException":
        return UNKNOWN, (f"the token may not read model metadata ({api}: {kind or status}), "
                         f"so reach is not proven: {message}")
    return UNKNOWN, f"{api} {status} {kind}: {message}"


_PROFILE_PREFIX = re.compile(r"^(us|eu|apac|global|us-gov|jp|au|ca|ap)\.")
_GOOD_AVAILABILITY = {"authorizationStatus": "AUTHORIZED", "entitlementAvailability": "AVAILABLE",
                      "regionAvailability": "AVAILABLE"}


def check_bedrock(model: str, token: str, region: str, get: HttpGet = http_get) -> tuple[str, str]:
    """GetInferenceProfile or GetFoundationModel, then the account's availability of
    the foundation model behind the id, all with the path's own bearer token."""
    base = f"https://bedrock.{region}.amazonaws.com"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    quoted = urllib.parse.quote(model, safe="")
    order = (["inference-profiles", "foundation-models"] if _PROFILE_PREFIX.match(model)
             else ["foundation-models", "inference-profiles"])
    api_name = {"inference-profiles": "GetInferenceProfile", "foundation-models": "GetFoundationModel"}
    found: tuple[str, dict] | None = None
    verdict: tuple[str, str] = (NOT_FOUND, "")
    for kind_path in order:
        status, rheaders, body, error = get(f"{base}/{kind_path}/{quoted}", headers)
        if status == 200:
            found = (kind_path, _json(body))
            break
        verdict = _aws_verdict(api_name[kind_path], status,
                               *_aws_error(status, rheaders, body, error, token))
        if verdict[0] != NOT_FOUND:
            return verdict
    if found is None:
        return NOT_FOUND, f"Bedrock knows no inference profile or model {model} in {region} ({verdict[1]})"

    kind_path, doc = found
    notes: list[str] = []
    if kind_path == "inference-profiles":
        if doc.get("status") not in (None, "ACTIVE"):
            return UNKNOWN, f"the inference profile is {doc.get('status')}"
        arns = [str(m.get("modelArn", "")) for m in doc.get("models") or [] if isinstance(m, dict)]
        here = [a for a in arns if f":{region}:" in a] or arns
        if not here or "foundation-model/" not in here[0]:
            return UNKNOWN, "the inference profile names no foundation model"
        foundation = here[0].split("foundation-model/", 1)[1]
        notes.append(f"profile over {foundation}")
    else:
        details = doc.get("modelDetails") or {}
        foundation = details.get("modelId") or model
        if "ON_DEMAND" not in (details.get("inferenceTypesSupported") or ["ON_DEMAND"]):
            return NOT_FOUND, (f"{model} takes no on-demand calls; a call must name an "
                               "inference profile instead")
        lifecycle = (details.get("modelLifecycle") or {}).get("status")
        if lifecycle and lifecycle != "ACTIVE":
            notes.append(f"lifecycle {lifecycle}")

    status, rheaders, body, error = get(
        f"{base}/foundation-model-availability/{urllib.parse.quote(foundation, safe='')}", headers)
    if status != 200:
        kind, message = _aws_error(status, rheaders, body, error, token)
        state, why = _aws_verdict("GetFoundationModelAvailability", status, kind, message)
        if state != DENIED:
            state = UNKNOWN  # the id exists; only the account's access is unread
        return state, f"{'; '.join(notes + [why])}"
    avail = _json(body)
    bad = [f"{field} {avail.get(field)}" for field, good in _GOOD_AVAILABILITY.items()
           if avail.get(field) not in (None, good)]
    if bad:
        return DENIED, "; ".join(notes + bad)
    agreement_doc = avail.get("agreementAvailability")
    agreement = agreement_doc.get("status") if isinstance(agreement_doc, dict) else None
    if agreement not in (None, "AVAILABLE"):
        return UNKNOWN, "; ".join(notes + [
            f"agreement {agreement}: no Marketplace agreement yet (the first call makes one "
            "only if the token's principal may subscribe)"])
    return OK, "; ".join(notes + ["authorized, entitled, available in " + region])


def ask(uses: list[dict], env: Mapping[str, str], paths: tuple[str, ...], *,
        bedrock_token: str | None, bedrock_region: str, get: HttpGet = http_get,
        clock: Callable[[], float] = time.monotonic, budget_s: float = BUDGET_S) -> list[dict]:
    """Mark every use on ``paths``; each (id, credential) is asked once."""
    started = clock()
    asked: dict[tuple, tuple[str, str]] = {}
    for use in uses:
        if use.get("preset"):
            use["status"], use["why"] = use.pop("preset")
            continue
        if use["path"] not in paths:
            use.setdefault("status", PENDING if use["path"] == PROXY else UNKNOWN)
            use.setdefault("why", "asked in the bedrock-proxy" if use["path"] == PROXY
                           else "not asked by this check")
            continue
        secret = bedrock_token if use["path"] in (BEDROCK, PROXY) else env.get(use["credential"] or "")
        k = (use["id"], use["path"], use.get("credential"))
        if k not in asked:
            if not secret:
                where = "bedrock-proxy" if use["path"] == PROXY else "app"
                asked[k] = (NO_CREDENTIAL, f"the {where} has no {use['credential']}")
            elif clock() - started > budget_s:
                asked[k] = (UNKNOWN, f"not asked: the check's {int(budget_s)} s were spent")
            else:
                try:
                    asked[k] = (check_gemini(use["id"], secret, get) if use["path"] == GEMINI
                                else check_bedrock(use["id"], secret, bedrock_region, get))
                except Exception as exc:  # noqa: BLE001 - a malformed answer is "could not tell"
                    asked[k] = (UNKNOWN, f"the answer could not be read: {type(exc).__name__}")
        use["status"], use["why"] = asked[k]
    return uses


# --------------------------------------------------------------------------- #
# in the container
# --------------------------------------------------------------------------- #

def _proxy_credentials(env: Mapping[str, str]) -> tuple[str, str]:
    """The proxy's own token and region, by its own rule (app.config.ProxyConfig)."""
    try:
        from app.config import ProxyConfig  # the bedrock-proxy image's package

        config = ProxyConfig.from_env()
        return config.token, config.region
    except Exception:  # noqa: BLE001 - outside the proxy image: its documented env
        return (env.get(BEDROCK_TOKEN_ENV) or "").strip(), env.get("AWS_REGION", "us-east-1")


def run(request: dict, env: Mapping[str, str], get: HttpGet = http_get) -> dict:
    role = request.get("role")
    if role == "app":
        try:
            report = derive(Path(request.get("root") or "/app"), env)
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            return {"error": f"could not derive the model list: {type(exc).__name__}: {exc}"}
        region = env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or "us-east-1"
        ask(report["uses"], env, (GEMINI, BEDROCK),
            bedrock_token=env.get(BEDROCK_TOKEN_ENV), bedrock_region=region, get=get)
        return report
    if role == "proxy":
        token, region = _proxy_credentials(env)
        uses = [{"id": model, "path": PROXY, "credential": BEDROCK_TOKEN_ENV, "users": []}
                for model in request.get("ids") or []]
        ask(uses, env, (PROXY,), bedrock_token=token, bedrock_region=region, get=get)
        return {"results": {u["id"]: [u["status"], u["why"]] for u in uses}}
    return {"error": f"unknown role {role!r}"}


def main(argv: list[str]) -> int:
    request = json.loads(argv[1]) if len(argv) > 1 else {}
    print(MARKER + " " + json.dumps(run(request, os.environ)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
