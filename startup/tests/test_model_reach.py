"""Tests for startup.steps.model_reach_probe: the model ids the deployed config
would call, and the free metadata calls that say whether each can be reached.

No network and no docker: the derivation runs against the real checkout (so the
run-2 model switch, a catalog, map or BAML edit, needs no edit to the check), and
every HTTP answer is a canned tuple.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from startup.steps import model_reach_probe as mr

REPO_ROOT = Path(__file__).resolve().parents[2]
CREDS = {"GCP_API_KEY": "gcp-key-123", "AWS_BEARER_TOKEN_BEDROCK": "bedrock-token-456"}


def _ids(report, path=None):
    return {u["id"] for u in report["uses"] if path is None or u["path"] == path}


def _users(report, model, path):
    (use,) = [u for u in report["uses"] if u["id"] == model and u["path"] == path]
    return use["users"]


# ---- the files it reads are in the tree -----------------------------------------------

def test_every_file_the_derivation_reads_is_in_the_tree():
    for rel in (mr.CATALOG, mr.SCHEMA_HELPER, mr.CLASS_MAP, mr.CLASS_MAP_LOADER, mr.ROUTER):
        assert (REPO_ROOT / rel).is_file(), rel
    assert list((REPO_ROOT / mr.BAML_SRC).glob("*.baml"))


def test_the_fallback_rules_are_read_from_schema_helper_not_restated():
    chains, override_key, moves = mr.read_fallback_rules(REPO_ROOT)
    assert ("default", "gcp") in chains and ("default", "anth") in chains
    assert override_key in json.loads((REPO_ROOT / mr.CATALOG).read_text())
    assert isinstance(moves, int) and moves >= 1


def test_moved_fallback_rules_are_still_found(tmp_path):
    """The failover branch may move the constants; the probe looks under chat_nextseek."""
    tree = _copy_tree(tmp_path)
    helper = tree / mr.SCHEMA_HELPER
    source = helper.read_text()
    moved = "\n".join(line for line in source.splitlines() if line.startswith("MAX_PROVIDER_SWITCHES"))
    helper.write_text(source.replace(moved, ""))
    (tree / mr.NS_SRC / "schemas" / "call_budgets.py").write_text(moved + "\n")
    assert mr.read_fallback_rules(tree)[2] == mr.read_fallback_rules(REPO_ROOT)[2]


def test_missing_fallback_rules_fail_loudly(tmp_path):
    tree = _copy_tree(tmp_path)
    helper = tree / mr.SCHEMA_HELPER
    helper.write_text(helper.read_text().replace("_FALLBACK_CHAINS", "_RENAMED_CHAINS"))
    with pytest.raises(ValueError, match="no _FALLBACK_CHAINS"):
        mr.read_fallback_rules(tree)


# ---- the mirrors are pinned to the app's own code -------------------------------------

def _function(path: Path, name: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path} has no {name}")


def test_the_mode_list_matches_config_detect_model_mode():
    fn = _function(REPO_ROOT / mr.NS_SRC / "config.py", "_detect_model_mode")
    (valid,) = [n.value for n in ast.walk(fn) if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_VALID_MODES" for t in n.targets)]
    assert set(ast.literal_eval(valid)) == set(mr.VALID_MODES)


def test_every_catalog_key_config_resolves_is_one_the_probe_resolves():
    fn = _function(REPO_ROOT / mr.NS_SRC / "config.py", "_resolve_catalog_key")
    returned = {n.value.value for n in ast.walk(fn)
                if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)}
    assert returned == {mr.catalog_key(mode) for mode in mr.VALID_MODES}
    catalog_profiles = {k for k in json.loads((REPO_ROOT / mr.CATALOG).read_text()) if not k.startswith("_")}
    assert returned <= catalog_profiles


def test_the_tool_loop_agents_are_the_call_tools_call_sites():
    keys = set()
    for path in (REPO_ROOT / mr.NS_SRC).rglob("*.py"):
        text = path.read_text()
        if "call_tools(" in text and not path.name == "tool_loop.py":
            keys |= set(re.findall(r'^\w+_AGENT_KEY\s*=\s*"([^"]+)"', text, re.M))
    assert keys == set(mr.TOOL_LOOP_AGENTS)


def test_only_the_bedrock_client_has_a_tool_surface():
    source = (REPO_ROOT / mr.NS_SRC / "llm_clients.py").read_text()
    classes = [n for n in ast.parse(source).body if isinstance(n, ast.ClassDef)]
    with_tools = {c.name for c in classes
                  if any(isinstance(f, ast.FunctionDef) and f.name == "chat_with_tools" for f in c.body)}
    assert with_tools == {"BedrockClient"}
    assert mr.TOOL_CAPABLE_PROVIDERS == ("anth",)


# ---- the id list is derived from the catalog, the fallback rules, BAML and the map -----

def test_every_model_of_the_active_profile_is_listed_with_its_agents():
    report = mr.derive(REPO_ROOT, CREDS)
    assert (report["mode"], report["profile"]) == ("mixed", "default")
    models = json.loads((REPO_ROOT / mr.CATALOG).read_text())["default"]["models"]
    for model, entry in models.items():
        variants = entry if isinstance(entry, list) else [entry]
        path = {"gcp": mr.GEMINI, "anth": mr.BEDROCK}[variants[0]["provider"]]
        users = _users(report, model, path)
        for variant in variants:
            assert set(variant["agents"]) <= set(users), model


def test_each_agent_gets_the_one_fallback_the_ladder_moves_to():
    report = mr.derive(REPO_ROOT, CREDS)
    catalog = json.loads((REPO_ROOT / mr.CATALOG).read_text())
    override = catalog["_fallback"]
    for agent, entry in override.items():
        if isinstance(entry, dict):
            assert f"fallback of {agent}" in _users(report, entry["model"], mr.BEDROCK)
    # the Opus agents move across providers (the default profile's anth chain)
    assert "fallback of parser" in _users(report, "gemini-3.1-pro-preview", mr.GEMINI)
    assert "fallback of entity" in _users(report, "us.anthropic.claude-sonnet-4-6", mr.BEDROCK)
    # one move per call: nothing from the chains' second or third profile
    assert not _ids(report) & {"gemini-2.5-flash", "gemini-2.5-pro",
                               "anthropic.claude-sonnet-4-5-20250929-v1:0",
                               "anthropic.claude-opus-4-5-20251101-v1:0"}


def test_the_cc_ids_come_from_the_model_map_and_go_through_the_proxy():
    report = mr.derive(REPO_ROOT, CREDS)
    model_map = json.loads((REPO_ROOT / mr.CLASS_MAP).read_text())
    assert _users(report, model_map["opus"], mr.PROXY) == ["CC main"]
    assert "CC fallback" in _users(report, model_map["opus_fallback"], mr.PROXY)
    assert "CC classifier" in _users(report, model_map["sonnet"], mr.PROXY)
    assert model_map["haiku"] not in _ids(report)  # never passed to a CC turn


def test_every_baml_client_a_function_or_the_router_names_is_listed():
    report = mr.derive(REPO_ROOT, CREDS)
    texts = mr.baml_sources(REPO_ROOT)
    clients = mr.baml_clients(texts)
    functions = mr.baml_functions(texts)
    assert functions["RouteQuery"] in clients and functions["Summarize"] in clients
    for function, client in functions.items():
        spec = clients[client]
        use = [u for u in report["uses"] if u["id"] == spec["model"] and u["path"] == mr.GEMINI]
        assert use and f"BAML {function}" in use[0]["users"]
        assert use[0]["credential"] == spec["key_env"]
    for client, labels in mr.router_clients(REPO_ROOT).items():
        assert set(labels) <= set(_users(report, clients[client]["model"], mr.GEMINI))


def test_each_id_is_listed_once_per_path_and_credential():
    report = mr.derive(REPO_ROOT, CREDS)
    keys = [(u["id"], u["path"], u["credential"]) for u in report["uses"]]
    assert len(keys) == len(set(keys))


def _copy_tree(tmp_path: Path) -> Path:
    """The files the derivation reads, copied so a test can edit them."""
    for rel in (mr.CATALOG, mr.CLASS_MAP, mr.CLASS_MAP_LOADER, mr.ROUTER, mr.NS_SRC + "/config.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / rel, tmp_path / rel)
    (tmp_path / mr.SCHEMA_HELPER).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO_ROOT / mr.SCHEMA_HELPER, tmp_path / mr.SCHEMA_HELPER)
    shutil.copytree(REPO_ROOT / mr.BAML_SRC, tmp_path / mr.BAML_SRC)
    return tmp_path


def test_the_run_2_switch_needs_no_edit_to_the_check(tmp_path):
    """The operator's run-2 rulings, applied to copies of the real files: NS Opus 4.7
    -> Opus 5.5 and NS Gemini 3.5 Flash -> 3.8 Flash in the catalog (BAML unchanged),
    CC main Opus 5.5 with fallback Opus 4.8. The check lists what the switch calls."""
    tree = _copy_tree(tmp_path)
    catalog = json.loads((tree / mr.CATALOG).read_text())
    for profile in ("default", "gcp:current"):
        models = catalog[profile]["models"]
        models["gemini-3.8-flash"] = models.pop("gemini-3.5-flash")
    catalog["default"]["models"]["us.anthropic.claude-opus-5-5"] = \
        catalog["default"]["models"].pop("us.anthropic.claude-opus-4-7")
    (tree / mr.CATALOG).write_text(json.dumps(catalog))
    model_map = json.loads((tree / mr.CLASS_MAP).read_text())
    model_map.update(opus="us.anthropic.claude-opus-5-5", opus_fallback="us.anthropic.claude-opus-4-8")
    (tree / mr.CLASS_MAP).write_text(json.dumps(model_map))

    report = mr.derive(tree, CREDS)

    assert "parser" in _users(report, "us.anthropic.claude-opus-5-5", mr.BEDROCK)
    assert _users(report, "us.anthropic.claude-opus-5-5", mr.PROXY) == ["CC main"]
    assert _users(report, "us.anthropic.claude-opus-4-8", mr.PROXY) == ["CC fallback"]
    assert {"entity", "fallback of memory"} <= set(_users(report, "gemini-3.8-flash", mr.GEMINI))
    # Gemini 3.5 Flash is left only where BAML names it
    assert all(u.startswith(("BAML", "router")) for u in _users(report, "gemini-3.5-flash", mr.GEMINI))
    # Opus 4.7 is called by nothing any more (the tool loops move to Sonnet 4.6 first)
    assert "us.anthropic.claude-opus-4-7" not in _ids(report)
    assert "fallback of followup" in _users(report, "us.anthropic.claude-sonnet-4-6", mr.BEDROCK)


def test_nextseek_mode_picks_the_profile(tmp_path):
    report = mr.derive(REPO_ROOT, {**CREDS, "NEXTSEEK_MODE": "gcp:current"})
    assert report["profile"] == "gcp:current"
    assert "parser" in _users(report, "gemini-3.1-pro-preview", mr.GEMINI)


def test_a_catalog_in_the_env_replaces_the_file():
    catalog = {"default": {"models": {"gemini-9-flash": {"provider": "gcp", "agents": ["entity"]}}},
               "_fallback": {}}
    report = mr.derive(REPO_ROOT, {**CREDS, "AGENT_MODEL_CATALOG": json.dumps(catalog)})
    assert report["catalog"] == "AGENT_MODEL_CATALOG"
    assert _users(report, "gemini-9-flash", mr.GEMINI)[0] == "entity"


def test_without_a_bedrock_token_the_opus_agents_are_marked_no_credential():
    """ChatConfig gives an agent whose client was not built the Gemini client, with
    the Bedrock id: every such call fails over without a word."""
    report = mr.derive(REPO_ROOT, {"GCP_API_KEY": "k"})
    (use,) = [u for u in report["uses"] if u["id"] == "us.anthropic.claude-opus-4-7"
              and u["path"] == mr.GEMINI]
    assert use["preset"][0] == mr.NO_CREDENTIAL
    assert "AWS_BEARER_TOKEN_BEDROCK" in use["preset"][1]
    assert "parser" in use["users"]


def test_the_cc_classifier_override_is_used_only_when_it_is_a_bedrock_id():
    good = mr.derive(REPO_ROOT, {**CREDS, "NEXTSEEK_CC_DEFAULT_SONNET_MODEL": "us.anthropic.claude-sonnet-9"})
    assert _users(good, "us.anthropic.claude-sonnet-9", mr.PROXY) == ["CC classifier"]
    bad = mr.derive(REPO_ROOT, {**CREDS, "NEXTSEEK_CC_DEFAULT_SONNET_MODEL": "claude-sonnet-9"})
    assert "claude-sonnet-9" not in _ids(bad)
    sonnet = json.loads((REPO_ROOT / mr.CLASS_MAP).read_text())["sonnet"]
    assert "CC classifier" in _users(bad, sonnet, mr.PROXY)


def test_a_fallback_equal_to_the_failed_model_is_skipped():
    """schema_helper skips an entry that would ask the same provider for the same model."""
    catalog = {"default": {"models": {"gemini-x": {"provider": "gcp", "agents": ["entity"]}}},
               "_fallback": {"entity": {"provider": "gcp", "model": "gemini-x"}},
               "anth:current": {"models": {"us.anthropic.claude-y": {"provider": "anth", "agents": ["entity"]}}}}
    report = mr.derive(REPO_ROOT, {**CREDS, "AGENT_MODEL_CATALOG": json.dumps(catalog)})
    assert _users(report, "gemini-x", mr.GEMINI) == ["entity"]
    assert _users(report, "us.anthropic.claude-y", mr.BEDROCK) == ["fallback of entity"]


def test_a_baml_client_no_function_names_is_not_listed(tmp_path):
    tree = _copy_tree(tmp_path)
    (tree / mr.BAML_SRC / "spare.baml").write_text(
        'client<llm> Spare {\n  provider google-ai\n  options {\n    model "gemini-spare"\n'
        '    api_key env.GCP_API_KEY\n  }\n}\n')
    report = mr.derive(tree, CREDS)
    assert "gemini-spare" not in _ids(report)
    assert "Spare" in mr.baml_clients(mr.baml_sources(tree))


# ---- asking: Gemini models.get ----------------------------------------------------------

class FakeHttp:
    """Canned (status, headers, body) per URL fragment; records every request."""

    def __init__(self, answers):
        self.answers = answers
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, url, headers):
        self.requests.append((url, dict(headers)))
        for fragment, answer in self.answers.items():
            if fragment in url:
                status, body = answer[0], answer[1]
                rheaders = answer[2] if len(answer) > 2 else {}
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                return status, rheaders, raw, None
        return None, {}, b"", "URLError: no route"


def test_a_gemini_model_the_key_can_see_is_ok_and_the_key_is_a_header():
    http = FakeHttp({"/models/gemini-3.5-flash": (200, {"name": "models/gemini-3.5-flash",
                                                        "supportedGenerationMethods": ["generateContent"]})})
    assert mr.check_gemini("gemini-3.5-flash", "gcp-key-123", http)[0] == mr.OK
    ((url, headers),) = http.requests
    assert "gcp-key-123" not in url and headers["x-goog-api-key"] == "gcp-key-123"


@pytest.mark.parametrize("answer, status", [
    ((404, {"error": {"code": 404, "message": "models/gemini-9 is not found"}}), mr.NOT_FOUND),
    ((400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                      "details": [{"reason": "API_KEY_INVALID"}]}}), mr.DENIED),
    ((403, {"error": {"code": 403, "message": "Generative Language API has not been used"}}), mr.DENIED),
    ((429, {"error": {"code": 429, "message": "quota"}}), mr.UNKNOWN),
    ((503, {"error": {"code": 503, "message": "overloaded"}}), mr.UNKNOWN),
    ((200, {"supportedGenerationMethods": ["embedContent"]}), mr.NOT_FOUND),
])
def test_gemini_answers_map_to_a_mark(answer, status):
    assert mr.check_gemini("gemini-9", "k", FakeHttp({"/models/": answer}))[0] == status


def test_gemini_not_answering_is_unknown():
    state, why = mr.check_gemini("gemini-3.5-flash", "k", FakeHttp({}))
    assert state == mr.UNKNOWN and "did not answer" in why


# ---- asking: Bedrock metadata ------------------------------------------------------------

PROFILE = {"inferenceProfileId": "us.anthropic.claude-opus-4-7", "status": "ACTIVE",
           "models": [{"modelArn": "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-opus-4-7"},
                      {"modelArn": "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-opus-4-7"}]}
AVAILABLE = {"modelId": "anthropic.claude-opus-4-7", "authorizationStatus": "AUTHORIZED",
             "entitlementAvailability": "AVAILABLE", "regionAvailability": "AVAILABLE",
             "agreementAvailability": {"status": "AVAILABLE"}}


def _bedrock(**answers):
    base = {"/inference-profiles/": (200, PROFILE), "/foundation-model-availability/": (200, AVAILABLE)}
    base.update({k.replace("_", "-"): v for k, v in answers.items()})
    return FakeHttp(base)


def test_an_active_profile_whose_model_the_account_may_use_is_ok():
    http = _bedrock()
    state, why = mr.check_bedrock("us.anthropic.claude-opus-4-7", "bedrock-token-456", "us-east-1", http)
    assert state == mr.OK and "anthropic.claude-opus-4-7" in why
    urls = [u for u, _ in http.requests]
    assert urls == ["https://bedrock.us-east-1.amazonaws.com/inference-profiles/us.anthropic.claude-opus-4-7",
                    "https://bedrock.us-east-1.amazonaws.com/foundation-model-availability/anthropic.claude-opus-4-7"]
    assert all(h["Authorization"] == "Bearer bedrock-token-456" for _, h in http.requests)
    assert "bedrock-token-456" not in why


@pytest.mark.parametrize("field, value", [("entitlementAvailability", "NOT_AVAILABLE"),
                                          ("authorizationStatus", "NOT_AUTHORIZED"),
                                          ("regionAvailability", "NOT_AVAILABLE")])
def test_an_account_without_access_is_denied(field, value):
    http = _bedrock(**{"/foundation_model_availability/": (200, {**AVAILABLE, field: value})})
    state, why = mr.check_bedrock("us.anthropic.claude-opus-4-7", "t", "us-east-1", http)
    assert state == mr.DENIED and f"{field} {value}" in why


def test_no_marketplace_agreement_yet_is_not_proven_either_way():
    http = _bedrock(**{"/foundation_model_availability/": (
        200, {**AVAILABLE, "agreementAvailability": {"status": "NOT_AVAILABLE"}})})
    state, why = mr.check_bedrock("us.anthropic.claude-opus-5-5", "t", "us-east-1", http)
    assert state == mr.UNKNOWN and "agreement NOT_AVAILABLE" in why


def test_an_id_bedrock_does_not_know_is_not_found_after_both_lookups():
    missing = (404, {"message": "The specified resource could not be found"},
               {"x-amzn-errortype": "ResourceNotFoundException:http://internal.amazon.com/"})
    http = FakeHttp({"/inference-profiles/": missing, "/foundation-models/": missing})
    state, why = mr.check_bedrock("us.anthropic.claude-opus-9", "t", "us-east-1", http)
    assert state == mr.NOT_FOUND and "us.anthropic.claude-opus-9" in why
    assert len(http.requests) == 2


def test_a_refused_token_is_denied_and_an_unreadable_catalog_is_unknown():
    refused = (403, {"message": "Authentication failed: Please make sure your API Key is valid."},
               {"x-amzn-errortype": "UnrecognizedClientException"})
    assert mr.check_bedrock("us.anthropic.claude-opus-4-7", "t", "us-east-1",
                            FakeHttp({"/inference-profiles/": refused}))[0] == mr.DENIED
    no_read = (403, {"message": "User: arn:aws:iam::123456789012:user/BedrockAPIKey-x is not authorized "
                                "to perform: bedrock:GetInferenceProfile"},
               {"x-amzn-errortype": "AccessDeniedException"})
    state, why = mr.check_bedrock("us.anthropic.claude-opus-4-7", "t", "us-east-1",
                                  FakeHttp({"/inference-profiles/": no_read}))
    assert state == mr.UNKNOWN and "not proven" in why
    assert "123456789012" not in why and "arn:aws" not in why


def test_an_unreadable_availability_leaves_the_id_unknown():
    no_read = (403, {"message": "not authorized to perform: bedrock:GetFoundationModelAvailability"},
               {"x-amzn-errortype": "AccessDeniedException"})
    http = _bedrock(**{"/foundation_model_availability/": no_read})
    state, why = mr.check_bedrock("us.anthropic.claude-opus-4-7", "t", "us-east-1", http)
    assert state == mr.UNKNOWN and "GetFoundationModelAvailability" in why


def test_a_bare_model_id_without_on_demand_calls_is_not_found():
    model = {"modelDetails": {"modelId": "anthropic.claude-sonnet-4-5-20250929-v1:0",
                              "inferenceTypesSupported": ["INFERENCE_PROFILE"],
                              "modelLifecycle": {"status": "ACTIVE"}}}
    http = FakeHttp({"/foundation-models/": (200, model)})
    state, why = mr.check_bedrock("anthropic.claude-sonnet-4-5-20250929-v1:0", "t", "us-east-1", http)
    assert state == mr.NOT_FOUND and "inference profile" in why
    assert http.requests[0][0].endswith("/foundation-models/anthropic.claude-sonnet-4-5-20250929-v1%3A0")


def test_bedrock_not_answering_is_unknown():
    assert mr.check_bedrock("us.anthropic.claude-opus-4-7", "t", "us-east-1", FakeHttp({}))[0] == mr.UNKNOWN


def test_every_request_is_a_free_metadata_get():
    """The probe has one transport, a GET, and it only ever builds these URLs."""
    http = FakeHttp({"generativelanguage": (200, {"supportedGenerationMethods": ["generateContent"]}),
                     "/inference-profiles/": (200, PROFILE),
                     "/foundation-model-availability/": (200, AVAILABLE)})
    report = mr.run({"role": "app", "root": str(REPO_ROOT)}, CREDS, get=http)
    proxy = mr.run({"role": "proxy", "ids": ["us.anthropic.claude-opus-4-8"]}, CREDS, get=http)
    assert report["uses"] and proxy["results"] and len(http.requests) >= 5
    source = Path(mr.__file__).read_text()
    assert 'method="GET"' in source
    for forbidden in ("bedrock-runtime", ":generateContent", ":countTokens", "/converse", "/invoke",
                      "data=", "method=\"POST\""):
        assert forbidden not in source, forbidden
    allowed = re.compile(r"^https://(generativelanguage\.googleapis\.com/v1beta/models/[^/]+"
                         r"|bedrock\.[a-z0-9-]+\.amazonaws\.com/(inference-profiles|foundation-models"
                         r"|foundation-model-availability)/[^/]+)$")
    for url, _ in http.requests:
        assert allowed.match(url), url


# ---- the roles ------------------------------------------------------------------------

def test_the_app_role_asks_its_own_paths_and_leaves_the_cc_ids_to_the_proxy():
    http = FakeHttp({"generativelanguage": (200, {"supportedGenerationMethods": ["generateContent"]}),
                     "/inference-profiles/": (200, PROFILE),
                     "/foundation-model-availability/": (200, AVAILABLE)})
    report = mr.run({"role": "app", "root": str(REPO_ROOT)}, CREDS, get=http)
    by_path = {}
    for use in report["uses"]:
        by_path.setdefault(use["path"], set()).add(use["status"])
    assert by_path[mr.GEMINI] == {mr.OK} and by_path[mr.BEDROCK] == {mr.OK}
    assert by_path[mr.PROXY] == {mr.PENDING}
    # each (id, credential) once; the fake names one foundation model behind every profile
    asked = [u for u, _ in http.requests if "availability" not in u]
    assert len(asked) == len(set(asked)), "an id was asked twice"
    assert {u.rsplit("/", 1)[1] for u in asked} == {
        "gemini-3.5-flash", "gemini-3.1-pro-preview",
        "us.anthropic.claude-opus-4-7", "us.anthropic.claude-sonnet-4-6"}


def test_the_app_role_marks_a_missing_key_without_asking():
    http = FakeHttp({})
    report = mr.run({"role": "app", "root": str(REPO_ROOT)}, {"AWS_BEARER_TOKEN_BEDROCK": "t"}, get=http)
    gemini = [u for u in report["uses"] if u["path"] == mr.GEMINI]
    assert gemini and {u["status"] for u in gemini} == {mr.NO_CREDENTIAL}
    assert not [u for u, _ in http.requests if "generativelanguage" in u]


def test_the_app_role_reports_a_derivation_failure(tmp_path):
    tree = _copy_tree(tmp_path)
    (tree / mr.CLASS_MAP).write_text("{}")
    report = mr.run({"role": "app", "root": str(tree)}, CREDS, get=FakeHttp({}))
    assert "could not derive the model list" in report["error"] and "opus" in report["error"]


def test_the_proxy_role_asks_with_the_proxys_own_token():
    http = _bedrock()
    report = mr.run({"role": "proxy", "ids": ["us.anthropic.claude-opus-4-8"]},
                    {"AWS_BEARER_TOKEN_BEDROCK": "proxy-token", "AWS_REGION": "us-east-2"}, get=http)
    assert report["results"]["us.anthropic.claude-opus-4-8"][0] == mr.OK
    assert all(h["Authorization"] == "Bearer proxy-token" for _, h in http.requests)
    assert http.requests[0][0].startswith("https://bedrock.us-east-2.amazonaws.com/")


def test_a_spent_budget_stops_the_asking():
    ticks = iter(range(0, 1000, 25))
    uses = [{"id": f"gemini-{i}", "path": mr.GEMINI, "credential": "GCP_API_KEY", "users": ["x"]}
            for i in range(4)]
    http = FakeHttp({"/models/": (200, {})})
    mr.ask(uses, {"GCP_API_KEY": "k"}, (mr.GEMINI,), bedrock_token=None, bedrock_region="us-east-1",
           get=http, clock=lambda: next(ticks), budget_s=60)
    assert [u["status"] for u in uses] == [mr.OK, mr.OK, mr.UNKNOWN, mr.UNKNOWN]
    assert "were spent" in uses[-1]["why"]


def test_the_probe_runs_as_a_piped_script_and_prints_no_secret():
    """The shape stack health runs it in: the source on stdin, the request as argv.
    With no credentials in the env it makes no request at all."""
    env = {"PATH": "/usr/bin:/bin", "SECRET_ELSEWHERE": "do-not-print"}
    out = subprocess.run([sys.executable, "-", json.dumps({"role": "app", "root": str(REPO_ROOT)})],
                         input=Path(mr.__file__).read_text(), capture_output=True, text=True,
                         env=env, timeout=60, check=True).stdout
    line = [l for l in out.splitlines() if l.startswith(mr.MARKER + " ")][-1]
    report = json.loads(line[len(mr.MARKER) + 1:])
    assert {u["status"] for u in report["uses"]} <= {mr.NO_CREDENTIAL, mr.PENDING}
    assert "do-not-print" not in out


def test_the_probe_imports_only_the_standard_library_at_module_level():
    tree = ast.parse(Path(mr.__file__).read_text())
    top = {alias.name.split(".")[0] for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
           for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])}
    assert top <= set(sys.stdlib_module_names) | {"__future__"}


def test_masking_removes_arns_accounts_and_the_credential():
    text = "User: arn:aws:iam::123456789012:user/k is denied; token abc123 in 210987654321"
    masked = mr.mask(text, ("abc123",))
    assert "arn:aws" not in masked and "123456789012" not in masked and "abc123" not in masked
    assert "210987654321" not in masked
