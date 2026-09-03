# Nessie Tests: unified router-aware harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a top-level `nessie_tests/` harness that drives assistant test cases through the *real* top-level HTTP router, reusing `chat_nextseek/e2e`'s corpus + assertion DSL, so NS-vs-CC routing (#33) and cross-turn/attribute regressions (#32) become catchable, deterministic tests.

**Architecture:** One runner posts each case to `POST /nextseek_api/cc-assistant/query/async/`, polls `tasks/{id}/progress/`, and either stops at the `route_decided` event (fast "route" tier) or runs the turn to completion (paid "full" tier). It reconstructs the criteria-shaped `debug` dict from the event stream, **injects** `route`/`engine`/`bundle` keys, and evaluates with the vendored `check_pass` DSL — **zero edits to `chat_nextseek`**. New cases live in a top-level `overlay.json` using the same schema; the 366 vendored NS variants are imported read-only.

**Tech Stack:** Python 3, Pydantic v2, `urllib.request` (stdlib) for the driver, `requests` optional, pytest. The harness is its own isolated `uv` project (no `mysqlclient`, so pure-logic tests run on the host); Django-touching tests run in-container.

## Global Constraints

Every task's requirements implicitly include these:

- **NEVER edit anything under `chat_nextseek/`** — it is a vendored snapshot; hand-edits are overwritten on sync. Reuse by import + injection only. Do NOT add ops to `chat_nextseek/e2e/catalog.py`'s `Literal`, do NOT add prefix branches to `criteria.py`, do NOT edit its `manifest.py`/`report.py` (nessie ships its own).
- **Import shim:** `chat_nextseek/e2e/` is NOT in the installed dist; `from e2e.catalog import ...` only resolves when repo-root `chat_nextseek/` is on `sys.path`. `nessie_tests/conftest.py` and `nessie_tests/pathsetup.py` insert `Path(__file__).resolve().parents[1] / "chat_nextseek"` (works on host = repo root, and in-container = `/app`).
- **Endpoint contract:** `POST /nextseek_api/cc-assistant/query/async/` with body **exactly** `{"query": <str>, "mode": "standard"}` (+ optional `session_id`, `force_route`) — `QueryRequest` is `extra="forbid"`, so any stray key → HTTP 422. Response is `202 {"task_id", "session_id"}`. Poll `GET /nextseek_api/cc-assistant/tasks/{task_id}/progress/` → `{status, progress:[{event,data}], result}`.
- **Route authority:** the `route_decided` event's `data["route"]` ∈ `{"nextseek_query", "container_cc", "unrelated"}` (constants `ROUTE_NS`/`ROUTE_CC`/`ROUTE_UNRELATED`). It is emitted BEFORE the turn executes → the route tier reads it and aborts.
- **DSL:** `PassCriterion{field:str, op ∈ [eq,contains,nonempty,true,gte,lte,mentions,matches_re,trio_match], value}`. New fields (`route`, `engine`, `bundle.*`) are resolved by the existing dot-notation fallback because the harness injects them into `debug` — no new ops, no schema change. `check_pass(debug, criteria, last_reply=…)` accepts raw `{field,op,value}` dicts. An empty criteria list PASSES; `trio_match` trivially passes off-browser.
- **Test lanes (ALL tests run in-container):** `nessie_tests/` has **NO own `pyproject.toml`**; it is a plain package under the repo root (`__init__.py` + `conftest.py`), tested with the app's root env inside the `nextseek` container (host `uv` cannot build `mysqlclient` on Py 3.14, and `import e2e` needs the vendored `chat_nextseek/`, present at `/app/chat_nextseek/`). **Canonical command — every task step that shows `cd nessie_tests && uv run pytest <SEL>` is shorthand for running `<SEL>` this way:**
  ```bash
  docker cp nessie_tests nextseek:/app/nessie_tests   # or just the changed files
  docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
    sh -c 'cd /app && uv run pytest nessie_tests/<SEL> --no-migrations -v -p no:cacheprovider'
  ```
  `tests/` vs `tests_container/` is organizational only (pure-logic vs `@pytest.mark.django_db`); both run via the command above. Package imports (`from nessie_tests import ...`) resolve because pytest rootdir is `/app`.
- **Auth to the endpoint:** HTTP Basic `demo:demopassword` (dev), or a token. The full tier additionally needs a **seeded v2 instance** (participating project ids 2-14) for count assertions.
- **Commit after every task** (conventional commits, scope `nessie_tests`). Do not push.

## File Structure

```
nessie_tests/
  conftest.py            sys.path shim → chat_nextseek/ (pytest); no own pyproject
  pathsetup.py           same shim as a callable, for the CLI/live path
  __init__.py
  route_observer.py      progress payload → RouteObservation; has_route_decided()
  http_driver.py         POST + poll; route-only short-circuit vs full
  evaluate.py            build debug from stream + inject route/engine/bundle + check_pass
  bundle.py              read results_history (lazy Django) → richness summary
  consistency.py         run an N-phrasing group, assert route/count agreement (#33)
  manifest.py            NessieManifestEntry/Manifest (adds route/engine/cost/tier)
  report.py              minimal HTML report
  runner.py              orchestrate: select → drive(tier) → evaluate → record
  cli.py                 argparse flags (--tier/--scope/--family/--variant/…)
  __main__.py            python -m nessie_tests → cli.main
  overlay.json           NEW cases (route family, #32/#33 repros, green anchors)
  README.md              tiers, scoping, seed prerequisite, cadence
  tests/                 host-runnable unit tests (isolated env)
  tests_container/       Django/DB + live-stack tests (in-container)
```

Each module has one responsibility and is unit-tested with injected fakes so no live stack is needed except in `tests_container/`.

---

### Task 1: Scaffold the isolated package + import shim

**Files:**
- Create: `nessie_tests/__init__.py`, `nessie_tests/pathsetup.py`, `nessie_tests/conftest.py`, `nessie_tests/tests/__init__.py`, `nessie_tests/tests/test_import_smoke.py`

**Interfaces:**
- Produces: `nessie_tests.pathsetup.ensure_e2e_importable() -> None` (idempotent `sys.path` insert of `chat_nextseek/`).

- [ ] **Step 1: Create the package skeleton** (no `pyproject.toml` — `nessie_tests/` is a plain package tested in-container with the app env)

Create empty files `nessie_tests/__init__.py` and `nessie_tests/tests/__init__.py`.

- [ ] **Step 2: Write `pathsetup.py`**

```python
"""Put repo-root chat_nextseek/ on sys.path so `import e2e...` resolves.

The e2e/ package lives at chat_nextseek/e2e/ (sibling of src/), NOT in the
installed chat_nextseek dist — mirror chat_nextseek/conftest.py:11-14.
"""
from __future__ import annotations
import sys
from pathlib import Path

_CHAT_NEXTSEEK = Path(__file__).resolve().parents[1] / "chat_nextseek"


def ensure_e2e_importable() -> None:
    p = str(_CHAT_NEXTSEEK)
    if p not in sys.path:
        sys.path.insert(0, p)
```

- [ ] **Step 3: Write `conftest.py` and `__init__.py`**

`nessie_tests/conftest.py`:
```python
from nessie_tests.pathsetup import ensure_e2e_importable

ensure_e2e_importable()
```
`nessie_tests/__init__.py`: empty file. `nessie_tests/tests/__init__.py`: empty file.

- [ ] **Step 4: Write the failing smoke test** `nessie_tests/tests/test_import_smoke.py`

```python
from pathlib import Path
from nessie_tests.pathsetup import ensure_e2e_importable


def test_e2e_dsl_and_catalog_load():
    ensure_e2e_importable()
    from e2e.catalog import load_catalog, Catalog, Variant, Turn, PassCriterion  # noqa
    cat_path = Path(__file__).resolve().parents[2] / "chat_nextseek" / "e2e" / "catalog.json"
    cat = load_catalog(cat_path)
    assert isinstance(cat, Catalog)
    assert len(cat.families) == 11
    total = sum(len(f.variants) for f in cat.families.values())
    assert total >= 300
```

- [ ] **Step 5: Run it in-container — expect PASS**

```bash
docker cp nessie_tests nextseek:/app/nessie_tests
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nessie_tests/tests/test_import_smoke.py --no-migrations -v -p no:cacheprovider'
```
Expected: PASS. If it FAILS with `ModuleNotFoundError: e2e`, the shim path is wrong — verify `parents[1]` resolves to `/app` in-container (`nessie_tests/pathsetup.py` → `parents[1]` = repo root). If `ModuleNotFoundError: nessie_tests`, confirm both `__init__.py` files exist so pytest inserts `/app` on `sys.path`.

- [ ] **Step 6: Commit**

```bash
git add nessie_tests/__init__.py nessie_tests/pathsetup.py \
        nessie_tests/conftest.py nessie_tests/tests/__init__.py nessie_tests/tests/test_import_smoke.py
git commit -m "test(nessie_tests): scaffold package + chat_nextseek import shim (in-container lane)"
```

---

### Task 2: Corpus loader, overlay, merge, scope selection

**Files:**
- Create: `nessie_tests/corpus.py`, `nessie_tests/overlay.json`, `nessie_tests/tests/test_corpus.py`

**Interfaces:**
- Consumes: `e2e.catalog.{load_catalog, Catalog, Variant}`.
- Produces:
  - `load_base() -> list[Variant]` (tags each with `"base"`),
  - `load_overlay(path: Path) -> list[Variant]` (tags each with `"overlay"`),
  - `merged(overlay_path: Path | None = None) -> list[Variant]`,
  - `select(variants, *, scope="all", family=None, variant_id=None) -> list[Variant]` (scope `"specific"` keeps `"route_gate"` tag).

- [ ] **Step 1: Write the failing test** `nessie_tests/tests/test_corpus.py`

```python
from pathlib import Path
from nessie_tests import corpus

OVERLAY = Path(__file__).resolve().parents[1] / "overlay.json"


def test_base_loads_and_is_tagged():
    base = corpus.load_base()
    assert len(base) >= 300
    assert all("base" in v.tags for v in base)


def test_overlay_loads_and_is_tagged():
    ov = corpus.load_overlay(OVERLAY)
    assert all("overlay" in v.tags for v in ov)


def test_merged_is_base_plus_overlay():
    base, ov = corpus.load_base(), corpus.load_overlay(OVERLAY)
    assert len(corpus.merged(OVERLAY)) == len(base) + len(ov)


def test_select_scope_specific_keeps_route_gate():
    merged = corpus.merged(OVERLAY)
    specific = corpus.select(merged, scope="specific")
    assert specific and all("route_gate" in v.tags for v in specific)


def test_select_by_family_and_variant():
    merged = corpus.merged(OVERLAY)
    fam = corpus.select(merged, family="search_advanced")
    assert fam and all(v.family == "search_advanced" for v in fam)
    one = corpus.select(merged, variant_id="advanced.basic_ndma")
    assert len(one) == 1 and one[0].id == "advanced.basic_ndma"
```

- [ ] **Step 2: Write a minimal valid `overlay.json`** (cases added in Tasks 11-12; must be a valid `Catalog` with at least one `route_gate` variant so the test passes)

```json
{
  "version": "1.0",
  "families": {
    "nessie_route": {
      "description": "Top-level router assertions",
      "variants": [
        {
          "family": "nessie_route",
          "id": "route.cc_pipeline_launch",
          "name": "Pipeline launch routes to Container-CC",
          "tags": ["nessie", "route_gate", "specific"],
          "requires_env": [],
          "turns": [
            {"label": "main",
             "query": "Launch an nf-core rnaseq run for D.SEQ-240910LAU-135.",
             "pass_criteria": [{"field": "route", "op": "eq", "value": "container_cc"}]}
          ]
        }
      ]
    }
  }
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd nessie_tests && uv run pytest tests/test_corpus.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nessie_tests.corpus'`.

- [ ] **Step 4: Write `corpus.py`**

```python
from __future__ import annotations
from pathlib import Path
from nessie_tests.pathsetup import ensure_e2e_importable

ensure_e2e_importable()
from e2e.catalog import load_catalog, Catalog, Variant  # noqa: E402

_BASE_CATALOG = Path(__file__).resolve().parents[1] / "chat_nextseek" / "e2e" / "catalog.json"


def _flatten(cat: Catalog, source_tag: str) -> list[Variant]:
    out: list[Variant] = []
    for fam in cat.families.values():
        for v in fam.variants:
            if source_tag not in v.tags:
                v.tags = [*v.tags, source_tag]
            out.append(v)
    return out


def load_base() -> list[Variant]:
    return _flatten(load_catalog(_BASE_CATALOG), "base")


def load_overlay(path: Path) -> list[Variant]:
    return _flatten(load_catalog(path), "overlay")


def merged(overlay_path: Path | None = None) -> list[Variant]:
    ov = load_overlay(overlay_path) if overlay_path else []
    return load_base() + ov


def select(variants, *, scope: str = "all", family: str | None = None,
           variant_id: str | None = None) -> list[Variant]:
    out = list(variants)
    if variant_id:
        return [v for v in out if v.id == variant_id]
    if family:
        out = [v for v in out if v.family == family]
    if scope == "specific":
        out = [v for v in out if "route_gate" in v.tags]
    return out
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd nessie_tests && uv run pytest tests/test_corpus.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add nessie_tests/corpus.py nessie_tests/overlay.json nessie_tests/tests/test_corpus.py
git commit -m "feat(nessie_tests): corpus loader + overlay merge + scope selection"
```

---

### Task 3: Route observer

**Files:**
- Create: `nessie_tests/route_observer.py`, `nessie_tests/tests/test_route_observer.py`

**Interfaces:**
- Produces: `ROUTE_NS/ROUTE_CC/ROUTE_UNRELATED` constants; `has_route_decided(payload) -> bool`; `observe(payload) -> RouteObservation` with fields `route, model_class, source, reasoning, parser_mode, engine`.

- [ ] **Step 1: Write the failing test** `nessie_tests/tests/test_route_observer.py`

```python
from nessie_tests import route_observer as ro

NS_PAYLOAD = {"status": "completed", "progress": [
    {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None, "source": "baml", "reasoning": "r"}},
    {"event": "agent_complete", "data": {"agent": "parser", "summary": {"mode": "new_search", "endpoint": "advanced_search"}}},
    {"event": "query_complete", "data": {"reply": "ok", "debug": {"parser_plan": {"mode": "new_search"}, "api_plan": {"endpoint": "advanced_search"}}, "bundle_id": 1}},
]}
CC_PAYLOAD = {"status": "completed", "progress": [
    {"event": "route_decided", "data": {"route": "container_cc", "model_class": "opus", "source": "baml", "reasoning": "r"}},
    {"event": "query_complete", "data": {"reply": "done", "total_cost_usd": 0.03, "cc_session_id": "s"}},
]}
EARLY_PAYLOAD = {"status": "running", "progress": [
    {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None, "source": "baml", "reasoning": "r"}},
]}


def test_has_route_decided():
    assert ro.has_route_decided(EARLY_PAYLOAD) is True
    assert ro.has_route_decided({"progress": []}) is False


def test_observe_ns():
    obs = ro.observe(NS_PAYLOAD)
    assert obs.route == ro.ROUTE_NS
    assert obs.parser_mode == "new_search"
    assert obs.engine == "advanced_search"


def test_observe_cc():
    obs = ro.observe(CC_PAYLOAD)
    assert obs.route == ro.ROUTE_CC
    assert obs.model_class == "opus"
    assert obs.engine == "container_cc:opus"


def test_observe_early_has_route_no_mode():
    obs = ro.observe(EARLY_PAYLOAD)
    assert obs.route == ro.ROUTE_NS
    assert obs.parser_mode is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd nessie_tests && uv run pytest tests/test_route_observer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nessie_tests.route_observer'`.

- [ ] **Step 3: Write `route_observer.py`**

```python
from __future__ import annotations
from dataclasses import dataclass

ROUTE_NS = "nextseek_query"
ROUTE_CC = "container_cc"
ROUTE_UNRELATED = "unrelated"


@dataclass
class RouteObservation:
    route: str | None
    model_class: str | None
    source: str | None
    reasoning: str | None
    parser_mode: str | None
    engine: str | None


def _events(payload: dict) -> list[dict]:
    return payload.get("progress") or []


def _first(payload, name):
    for ev in _events(payload):
        if ev.get("event") == name:
            return ev.get("data") or {}
    return None


def _last(payload, name):
    data = None
    for ev in _events(payload):
        if ev.get("event") == name:
            data = ev.get("data") or {}
    return data


def has_route_decided(payload: dict) -> bool:
    return _first(payload, "route_decided") is not None


def _parser_mode(payload, debug):
    pp = debug.get("parser_plan") or {}
    if pp.get("mode"):
        return pp["mode"]
    for ev in _events(payload):
        d = ev.get("data") or {}
        if ev.get("event") == "agent_complete" and d.get("agent") == "parser":
            return (d.get("summary") or {}).get("mode")
    return None


def _engine(route, parser_mode, debug, model_class):
    if route == ROUTE_CC:
        return f"container_cc:{model_class}" if model_class else "container_cc"
    if route == ROUTE_UNRELATED:
        return "unrelated"
    if route == ROUTE_NS:
        if parser_mode == "graph_query":
            return "graph_query"
        endpoint = (debug.get("api_plan") or {}).get("endpoint") \
            or (debug.get("parser_plan") or {}).get("target_endpoint")
        return endpoint or parser_mode
    return None


def observe(payload: dict) -> RouteObservation:
    rd = _first(payload, "route_decided") or {}
    debug = (_last(payload, "query_complete") or {}).get("debug") or {}
    mode = _parser_mode(payload, debug)
    return RouteObservation(
        route=rd.get("route"), model_class=rd.get("model_class"),
        source=rd.get("source"), reasoning=rd.get("reasoning"),
        parser_mode=mode, engine=_engine(rd.get("route"), mode, debug, rd.get("model_class")),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd nessie_tests && uv run pytest tests/test_route_observer.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add nessie_tests/route_observer.py nessie_tests/tests/test_route_observer.py
git commit -m "feat(nessie_tests): route observer (route_decided + parser mode + engine)"
```

---

### Task 4: HTTP driver with route-only short-circuit

**Files:**
- Create: `nessie_tests/http_driver.py`, `nessie_tests/tests/test_http_driver.py`

**Interfaces:**
- Consumes: `route_observer.{has_route_decided, observe, RouteObservation}`.
- Produces:
  - `make_default_clients(base_url, auth_header) -> (post_query, get_progress)`,
  - `basic_auth(user, pw) -> str`,
  - `drive(query, *, tier, post_query, get_progress, session_id=None, mode="standard", poll_interval_s=2.0, route_timeout_s=60.0, full_timeout_s=200.0, sleep=time.sleep, clock=time.monotonic) -> DriveResult`,
  - `DriveResult(session_id, task_id, payload, route_obs, aborted_early, status)`.

- [ ] **Step 1: Write the failing test** `nessie_tests/tests/test_http_driver.py`

```python
from nessie_tests import http_driver as hd


def _seq_get_progress(sequence):
    calls = {"n": 0}
    def get_progress(task_id):
        i = min(calls["n"], len(sequence) - 1)
        calls["n"] += 1
        return sequence[i]
    get_progress.calls = calls
    return get_progress


def _post(task_id="t1", session_id="s1"):
    def post_query(body):
        post_query.body = body
        return {"task_id": task_id, "session_id": session_id}
    return post_query


NO_ROUTE = {"status": "running", "progress": []}
ROUTED = {"status": "running", "progress": [
    {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None, "source": "baml", "reasoning": ""}}]}
DONE = {"status": "completed", "progress": ROUTED["progress"] + [
    {"event": "query_complete", "data": {"reply": "ok", "debug": {"parser_plan": {"mode": "new_search"}}}}]}


def test_route_tier_stops_at_route_decided():
    gp = _seq_get_progress([NO_ROUTE, ROUTED, DONE])
    res = hd.drive("q", tier="route", post_query=_post(), get_progress=gp,
                   sleep=lambda s: None, clock=lambda: 0.0)
    assert res.aborted_early is True
    assert res.route_obs.route == "nextseek_query"
    assert gp.calls["n"] == 2  # stopped as soon as route appeared, not to completion


def test_full_tier_polls_to_completion():
    gp = _seq_get_progress([NO_ROUTE, ROUTED, DONE])
    res = hd.drive("q", tier="full", post_query=_post(), get_progress=gp,
                   sleep=lambda s: None, clock=lambda: 0.0)
    assert res.aborted_early is False
    assert res.status == "completed"
    assert res.route_obs.parser_mode == "new_search"


def test_body_shape_and_session_threading():
    p = _post()
    hd.drive("hello", tier="route", post_query=p, get_progress=_seq_get_progress([ROUTED]),
             session_id="sess9", sleep=lambda s: None, clock=lambda: 0.0)
    assert p.body == {"query": "hello", "mode": "standard", "session_id": "sess9"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd nessie_tests && uv run pytest tests/test_http_driver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nessie_tests.http_driver'`.

- [ ] **Step 3: Write `http_driver.py`**

```python
from __future__ import annotations
import base64, json, time, urllib.request
from dataclasses import dataclass
from typing import Callable
from nessie_tests import route_observer as ro

BASE_PATH = "/nextseek_api/cc-assistant"


@dataclass
class DriveResult:
    session_id: str | None
    task_id: str | None
    payload: dict
    route_obs: ro.RouteObservation
    aborted_early: bool
    status: str


def basic_auth(user: str, pw: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def make_default_clients(base_url: str, auth_header: str):
    def post_query(body: dict) -> dict:
        req = urllib.request.Request(
            f"{base_url}{BASE_PATH}/query/async/", data=json.dumps(body).encode(),
            headers={"Authorization": auth_header, "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())

    def get_progress(task_id: str) -> dict:
        req = urllib.request.Request(
            f"{base_url}{BASE_PATH}/tasks/{task_id}/progress/",
            headers={"Authorization": auth_header})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())

    return post_query, get_progress


def drive(query: str, *, tier: str, post_query: Callable[[dict], dict],
          get_progress: Callable[[str], dict], session_id: str | None = None,
          mode: str = "standard", poll_interval_s: float = 2.0,
          route_timeout_s: float = 60.0, full_timeout_s: float = 200.0,
          sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic) -> DriveResult:
    body = {"query": query, "mode": mode}
    if session_id:
        body["session_id"] = session_id
    resp = post_query(body)
    task_id = resp.get("task_id")
    sess = resp.get("session_id") or session_id
    deadline = clock() + (route_timeout_s if tier == "route" else full_timeout_s)
    payload: dict = {"status": "pending", "progress": [], "result": None}
    aborted_early = False
    while True:
        payload = get_progress(task_id)
        if tier == "route" and ro.has_route_decided(payload):
            aborted_early = True
            break
        if payload.get("status") in ("completed", "error"):
            break
        if clock() >= deadline:
            break
        sleep(poll_interval_s)
    return DriveResult(sess, task_id, payload, ro.observe(payload),
                       aborted_early, payload.get("status", "pending"))
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd nessie_tests && uv run pytest tests/test_http_driver.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add nessie_tests/http_driver.py nessie_tests/tests/test_http_driver.py
git commit -m "feat(nessie_tests): HTTP driver with route-only short-circuit + full poll"
```

---

### Task 5: Evaluate — reconstruct debug, inject route/engine/bundle, reuse check_pass

**Files:**
- Create: `nessie_tests/evaluate.py`, `nessie_tests/tests/test_evaluate.py`

**Interfaces:**
- Consumes: `e2e.criteria.check_pass`; `route_observer.RouteObservation`.
- Produces:
  - `build_observed_debug(payload) -> dict` (NS: the `query_complete.data.debug`, backfilling `api_result_meta.ok`/`graph_result.ok` from `search_complete`, mirroring `e2e/playwright/poll.py:143-169`; CC: `{}`),
  - `augment_debug(debug, obs, bundle_summary=None) -> dict`,
  - `evaluate_turn(payload, criteria, obs, *, last_reply=None, bundle_summary=None) -> tuple[bool, list[dict]]`.

- [ ] **Step 1: Write the failing test** `nessie_tests/tests/test_evaluate.py`

```python
from nessie_tests import evaluate
from nessie_tests.route_observer import RouteObservation

NS_PAYLOAD = {"status": "completed", "progress": [
    {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None, "source": "baml", "reasoning": ""}},
    {"event": "search_complete", "data": {"api_ok": True}},
    {"event": "query_complete", "data": {"reply": "found", "debug": {"parser_plan": {"mode": "new_search"}, "api_plan": {"endpoint": "advanced_search"}}}},
]}
OBS_NS = RouteObservation("nextseek_query", None, "baml", "", "new_search", "advanced_search")


def test_build_debug_backfills_api_ok():
    debug = evaluate.build_observed_debug(NS_PAYLOAD)
    assert debug["parser_plan"]["mode"] == "new_search"
    assert debug["api_result_meta"]["ok"] is True


def test_route_and_mode_criteria_pass_via_injection():
    criteria = [
        {"field": "route", "op": "eq", "value": "nextseek_query"},
        {"field": "engine", "op": "eq", "value": "advanced_search"},
        {"field": "parser_plan.mode", "op": "eq", "value": "new_search"},
        {"field": "api_ok", "op": "true"},
    ]
    passed, results = evaluate.evaluate_turn(NS_PAYLOAD, criteria, OBS_NS, last_reply="found")
    assert passed, results


def test_bundle_richness_criteria():
    ok, _ = evaluate.evaluate_turn(NS_PAYLOAD, [{"field": "bundle.has_json_metadata", "op": "true"}],
                                   OBS_NS, bundle_summary={"has_json_metadata": True})
    assert ok
    bad, _ = evaluate.evaluate_turn(NS_PAYLOAD, [{"field": "bundle.has_json_metadata", "op": "true"}],
                                    OBS_NS, bundle_summary={"has_json_metadata": False})
    assert not bad
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd nessie_tests && uv run pytest tests/test_evaluate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nessie_tests.evaluate'`.

- [ ] **Step 3: Write `evaluate.py`**

```python
from __future__ import annotations
from nessie_tests.pathsetup import ensure_e2e_importable

ensure_e2e_importable()
from e2e.criteria import check_pass  # noqa: E402
from nessie_tests import route_observer as ro


def _last(payload, name):
    data = None
    for ev in payload.get("progress") or []:
        if ev.get("event") == name:
            data = ev.get("data") or {}
    return data


def build_observed_debug(payload: dict) -> dict:
    debug = dict((_last(payload, "query_complete") or {}).get("debug") or {})
    sc = _last(payload, "search_complete") or {}
    if "api_result_meta" not in debug and "api_ok" in sc:
        debug["api_result_meta"] = {"ok": sc.get("api_ok")}
    if "graph_result" not in debug and "neo4j_ok" in sc:
        debug["graph_result"] = {"ok": sc.get("neo4j_ok")}
    return debug


def augment_debug(debug: dict, obs: ro.RouteObservation, bundle_summary: dict | None = None) -> dict:
    debug = dict(debug)
    debug["route"] = obs.route
    debug["engine"] = obs.engine
    debug["route_source"] = obs.source
    if bundle_summary is not None:
        debug["bundle"] = bundle_summary
    return debug


def evaluate_turn(payload, criteria, obs, *, last_reply=None, bundle_summary=None):
    debug = augment_debug(build_observed_debug(payload), obs, bundle_summary)
    return check_pass(debug, criteria, last_reply=last_reply)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd nessie_tests && uv run pytest tests/test_evaluate.py -v`
Expected: PASS (3 tests). This proves `route`/`engine`/`bundle.*` resolve through the vendored `check_pass` dot-notation fallback with zero `criteria.py` edits.

- [ ] **Step 5: Commit**

```bash
git add nessie_tests/evaluate.py nessie_tests/tests/test_evaluate.py
git commit -m "feat(nessie_tests): evaluate via injected debug keys + vendored check_pass"
```

---

### Task 6: Bundle richness reader (Django/DB)

**Files:**
- Create: `nessie_tests/bundle.py`, `nessie_tests/tests_container/__init__.py`, `nessie_tests/tests_container/test_bundle.py`

**Interfaces:**
- Produces:
  - `richness_summary(bundle: dict) -> dict` with keys `row_count, has_json_metadata, sample_extra_keys, has_extra_keys, memory_payload_null` (PURE — no Django),
  - `read_results_history(session_id) -> list[dict]` (lazy Django import),
  - `summary_for_session(session_id) -> dict | None`.

- [ ] **Step 1: Write the failing test** `nessie_tests/tests_container/test_bundle.py`

```python
import pytest
from nessie_tests import bundle

RICH = {"memory_payload": {"data": {"samples": [
    {"id": 1, "uuid": "u1", "sample_type": "D.SEQ", "json_metadata": {"species": "Mus"}}]}}}
THIN = {"memory_payload": {"data": {"samples": [
    {"id": 1, "uuid": "u1", "sample_type": "NHP", "sample_type_description": "monkey"}]}}}
GRAPH = {"memory_payload": None, "graph_result": {"data": [
    {"id": 1, "uuid": "u1", "type": "D.SEQ"}]}}


def test_richness_rich_bundle():
    s = bundle.richness_summary(RICH)
    assert s["has_json_metadata"] is True and s["row_count"] == 1


def test_richness_thin_get_parents():
    s = bundle.richness_summary(THIN)
    assert s["has_json_metadata"] is False and s["has_extra_keys"] is False


def test_richness_graph_null_payload():
    s = bundle.richness_summary(GRAPH)
    assert s["memory_payload_null"] is True and s["row_count"] == 1


@pytest.mark.django_db
def test_summary_for_session_reads_orm():
    from django.contrib.auth import get_user_model
    from nextseek_api.assistant.models_db import ChatSession
    u = get_user_model().objects.create(username="nessie_t")
    sess = ChatSession.objects.create(user=u, results_history=[THIN, RICH])
    s = bundle.summary_for_session(sess.session_id)
    assert s["has_json_metadata"] is True  # latest bundle is RICH
```

- [ ] **Step 2: Run to verify it fails (in-container)**

```bash
docker cp nessie_tests nextseek:/app/nessie_tests
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nessie_tests/tests_container/test_bundle.py --no-migrations -v -p no:cacheprovider'
```
Expected: FAIL with `ModuleNotFoundError: No module named 'nessie_tests.bundle'`.

- [ ] **Step 3: Write `bundle.py`**

```python
from __future__ import annotations

THIN_KEYS = {"id", "uuid", "sample_type", "sample_type_description"}


def _samples(bundle: dict) -> list[dict]:
    for path in (("memory_payload", "data", "samples"), ("api_result_full", "data", "samples")):
        node = bundle
        for k in path:
            node = node.get(k) if isinstance(node, dict) else None
        if isinstance(node, list):
            return node
    gr = (bundle.get("graph_result") or {}).get("data")
    return gr if isinstance(gr, list) else []


def richness_summary(bundle: dict) -> dict:
    samples = _samples(bundle)
    extra = sorted({k for s in samples if isinstance(s, dict) for k in s} - THIN_KEYS)
    return {
        "row_count": len(samples),
        "has_json_metadata": any(bool(s.get("json_metadata")) for s in samples if isinstance(s, dict)),
        "sample_extra_keys": extra,
        "has_extra_keys": bool(extra),
        "memory_payload_null": bundle.get("memory_payload") is None,
    }


def read_results_history(session_id) -> list[dict]:
    from nextseek_api.assistant.models_db import ChatSession  # lazy: Django only at call time
    return ChatSession.objects.get(session_id=session_id).results_history or []


def summary_for_session(session_id) -> dict | None:
    hist = read_results_history(session_id)
    return richness_summary(hist[-1]) if hist else None
```

- [ ] **Step 4: Run to verify it passes (in-container)**

```bash
docker cp nessie_tests/bundle.py nextseek:/app/nessie_tests/bundle.py
docker cp nessie_tests/tests_container nextseek:/app/nessie_tests/tests_container
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nessie_tests/tests_container/test_bundle.py --no-migrations -v -p no:cacheprovider'
```
Expected: PASS (4 tests). Note: the 3 pure `richness_summary` tests ALSO pass on host (`cd nessie_tests && uv run pytest tests_container/test_bundle.py -k richness`), only the `django_db` one needs the container.

- [ ] **Step 5: Commit**

```bash
git add nessie_tests/bundle.py nessie_tests/tests_container/__init__.py nessie_tests/tests_container/test_bundle.py
git commit -m "feat(nessie_tests): results_history bundle richness reader (#32 oracle)"
```

---

### Task 7: Consistency group (#33)

**Files:**
- Create: `nessie_tests/consistency.py`, `nessie_tests/tests/test_consistency.py`

**Interfaces:**
- Produces:
  - `get_result_count(payload) -> int | None`,
  - `run_group(group: dict, drive_fn: Callable[[str], dict]) -> GroupResult` where `drive_fn(query)` returns `{"route": str|None, "count": int|None}` and `group = {id, name, queries:[str], assert:{same_route?:bool, same_count?:bool, count_not?:int}}`,
  - `GroupResult(id, passed, reasons, observations)`.

- [ ] **Step 1: Write the failing test** `nessie_tests/tests/test_consistency.py`

```python
from nessie_tests import consistency as c


def _fake_drive(mapping):
    return lambda q: mapping[q]


def test_group_passes_when_route_and_count_agree():
    g = {"id": "nhp", "name": "nhp", "queries": ["a", "b"],
         "assert": {"same_route": True, "same_count": True}}
    res = c.run_group(g, _fake_drive({"a": {"route": "nextseek_query", "count": 139},
                                      "b": {"route": "nextseek_query", "count": 139}}))
    assert res.passed and res.reasons == []


def test_group_fails_on_route_split():
    g = {"id": "nhp", "name": "nhp", "queries": ["a", "b"], "assert": {"same_route": True}}
    res = c.run_group(g, _fake_drive({"a": {"route": "nextseek_query", "count": 139},
                                      "b": {"route": "nextseek_query", "count": 250}}))
    # routes agree here → passes route check
    assert res.passed
    g2 = {**g, "assert": {"same_count": True, "count_not": 250}}
    res2 = c.run_group(g2, _fake_drive({"a": {"route": "x", "count": 139},
                                        "b": {"route": "x", "count": 250}}))
    assert not res2.passed
    assert any("differ" in r for r in res2.reasons)
    assert any("250" in r for r in res2.reasons)


def test_get_result_count_from_debug():
    payload = {"progress": [{"event": "query_complete",
                             "data": {"debug": {"api_result_meta": {"count": 42}}}}]}
    assert c.get_result_count(payload) == 42
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd nessie_tests && uv run pytest tests/test_consistency.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nessie_tests.consistency'`.

- [ ] **Step 3: Write `consistency.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class GroupResult:
    id: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)


def get_result_count(payload: dict) -> int | None:
    data = None
    for ev in payload.get("progress") or []:
        if ev.get("event") == "query_complete":
            data = ev.get("data") or {}
    debug = (data or {}).get("debug") or {}
    # NOTE: the executor confirms the exact count key against a live NS debug
    # (outputs/*/console.txt); these fallbacks cover the observed shapes.
    for path in (("api_result_meta", "count"), ("api_result_full", "data", "total"),
                 ("graph_result", "count")):
        node = debug
        for k in path:
            node = node.get(k) if isinstance(node, dict) else None
        if isinstance(node, int):
            return node
    gr = (debug.get("graph_result") or {}).get("data")
    return len(gr) if isinstance(gr, list) else None


def run_group(group: dict, drive_fn: Callable[[str], dict]) -> GroupResult:
    obs = [{"query": q, **drive_fn(q)} for q in group["queries"]]
    a = group.get("assert", {})
    reasons: list[str] = []
    routes = {o.get("route") for o in obs}
    counts = {o.get("count") for o in obs}
    if a.get("same_route") and len(routes) > 1:
        reasons.append(f"routes differ: {sorted(str(r) for r in routes)}")
    if a.get("same_count") and len({c for c in counts if c is not None}) > 1:
        reasons.append(f"counts differ: {sorted(c for c in counts if c is not None)}")
    if "count_not" in a and a["count_not"] in counts:
        reasons.append(f"count equals forbidden {a['count_not']} (likely a LIMIT cap)")
    return GroupResult(group["id"], not reasons, reasons, obs)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd nessie_tests && uv run pytest tests/test_consistency.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add nessie_tests/consistency.py nessie_tests/tests/test_consistency.py
git commit -m "feat(nessie_tests): consistency group (same-route/same-count, LIMIT-cap guard) for #33"
```

---

### Task 8: Manifest + HTML report

**Files:**
- Create: `nessie_tests/manifest.py`, `nessie_tests/report.py`, `nessie_tests/tests/test_manifest_report.py`

**Interfaces:**
- Produces:
  - `NessieManifestEntry{id, family, tier, status, route, engine, cost, elapsed_s, failed_criteria, reason, expected_fail}`,
  - `NessieManifest{started_at, ended_at, tier, scope, entries}`,
  - `write_manifest(m, path)`, `load_manifest(path)`,
  - `generate_html(manifest, out_dir) -> Path`.

- [ ] **Step 1: Write the failing test** `nessie_tests/tests/test_manifest_report.py`

```python
from nessie_tests import manifest as M
from nessie_tests import report


def _sample():
    return M.NessieManifest(
        started_at="t0", ended_at="t1", tier="route", scope="specific",
        entries=[M.NessieManifestEntry(id="route.cc_pipeline_launch", family="nessie_route",
                                       tier="route", status="passed", route="container_cc",
                                       engine="container_cc:opus")])


def test_manifest_roundtrip(tmp_path):
    p = tmp_path / "manifest.json"
    M.write_manifest(_sample(), p)
    loaded = M.load_manifest(p)
    assert loaded.entries[0].route == "container_cc"
    assert loaded.tier == "route"


def test_generate_html_contains_id(tmp_path):
    out = report.generate_html(_sample(), tmp_path)
    assert out.name == "report.html"
    assert "route.cc_pipeline_launch" in out.read_text()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd nessie_tests && uv run pytest tests/test_manifest_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nessie_tests.manifest'`.

- [ ] **Step 3: Write `manifest.py`**

```python
from __future__ import annotations
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field


class NessieManifestEntry(BaseModel):
    id: str
    family: str
    tier: Literal["route", "full"]
    status: Literal["passed", "failed", "skipped", "error"]
    route: str | None = None
    engine: str | None = None
    cost: float | None = None
    elapsed_s: float = 0.0
    failed_criteria: list[str] = Field(default_factory=list)
    reason: str = ""
    expected_fail: bool = False


class NessieManifest(BaseModel):
    started_at: str
    ended_at: str
    tier: str
    scope: str
    entries: list[NessieManifestEntry] = Field(default_factory=list)


def write_manifest(m: NessieManifest, path: Path) -> None:
    Path(path).write_text(m.model_dump_json(indent=2), encoding="utf-8")


def load_manifest(path: Path) -> NessieManifest:
    return NessieManifest.model_validate_json(Path(path).read_text(encoding="utf-8"))
```

- [ ] **Step 4: Write `report.py`**

```python
from __future__ import annotations
from pathlib import Path
from nessie_tests.manifest import NessieManifest

_ROW = "<tr class='{cls}'><td>{id}</td><td>{family}</td><td>{route}</td><td>{engine}</td><td>{status}</td><td>{reason}</td></tr>"


def generate_html(manifest: NessieManifest, out_dir: Path) -> Path:
    rows = "\n".join(
        _ROW.format(cls=e.status, id=e.id, family=e.family, route=e.route or "",
                    engine=e.engine or "", status=("xfail" if e.expected_fail else e.status),
                    reason=(e.reason or ", ".join(e.failed_criteria)))
        for e in manifest.entries)
    html = (f"<html><head><title>nessie {manifest.tier}/{manifest.scope}</title>"
            "<style>.failed{background:#fdd}.passed{background:#dfd}.error{background:#fbb}</style></head>"
            f"<body><h1>Nessie tests — tier={manifest.tier} scope={manifest.scope}</h1>"
            f"<p>{manifest.started_at} → {manifest.ended_at}</p>"
            "<table border=1 cellpadding=4><tr><th>id</th><th>family</th><th>route</th>"
            f"<th>engine</th><th>status</th><th>reason</th></tr>{rows}</table></body></html>")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / "report.html"
    p.write_text(html, encoding="utf-8")
    return p
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd nessie_tests && uv run pytest tests/test_manifest_report.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add nessie_tests/manifest.py nessie_tests/report.py nessie_tests/tests/test_manifest_report.py
git commit -m "feat(nessie_tests): router-aware manifest (route/engine/cost/tier) + HTML report"
```

---

### Task 9: Runner — wire selection → drive → evaluate → record

**Files:**
- Create: `nessie_tests/runner.py`, `nessie_tests/tests/test_runner.py`

**Interfaces:**
- Consumes: `corpus.{merged, select}`, `http_driver.drive`, `evaluate.evaluate_turn`, `consistency.run_group`, `manifest.*`, `report.generate_html`, `route_observer.ROUTE_NS`.
- Produces:
  - `default_route_criterion(variant) -> dict | None` (base variants → `{"field":"route","op":"eq","value":"nextseek_query"}`),
  - `run_suite(*, base_url, auth_header, tier, scope="specific", family=None, variant_id=None, overlay_path, out_dir, post_query=None, get_progress=None, bundle_reader=None, pace_s=0.0, sleep=..., clock=...) -> NessieManifest`.

- [ ] **Step 1: Write the failing test** `nessie_tests/tests/test_runner.py`

```python
from pathlib import Path
from nessie_tests import runner

OVERLAY = Path(__file__).resolve().parents[1] / "overlay.json"

CC_ROUTED = {"status": "running", "progress": [
    {"event": "route_decided", "data": {"route": "container_cc", "model_class": "opus", "source": "baml", "reasoning": ""}}]}


def _post():
    def post_query(body):
        return {"task_id": "t", "session_id": "s"}
    return post_query


def test_run_suite_route_tier_specific(tmp_path):
    # route tier + specific scope → only route_gate cases; injected clients, no live stack
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="route", scope="specific",
        overlay_path=OVERLAY, out_dir=tmp_path,
        post_query=_post(), get_progress=lambda tid: CC_ROUTED,
        sleep=lambda s: None, clock=lambda: 0.0)
    ids = {e.id for e in m.entries}
    assert "route.cc_pipeline_launch" in ids
    entry = next(e for e in m.entries if e.id == "route.cc_pipeline_launch")
    assert entry.status == "passed" and entry.route == "container_cc"
    assert (tmp_path / "manifest.json").exists() and (tmp_path / "report.html").exists()


def test_default_route_criterion_only_for_base():
    from e2e.catalog import Variant, Turn
    base = Variant(family="f", id="b", name="n", tags=["base"], turns=[Turn(label="m", query="q")])
    ov = Variant(family="nessie_route", id="o", name="n", tags=["overlay"], turns=[Turn(label="m", query="q")])
    assert runner.default_route_criterion(base) == {"field": "route", "op": "eq", "value": "nextseek_query"}
    assert runner.default_route_criterion(ov) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd nessie_tests && uv run pytest tests/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nessie_tests.runner'`.

- [ ] **Step 3: Write `runner.py`**

```python
from __future__ import annotations
import time
from pathlib import Path
from nessie_tests import corpus, evaluate, http_driver, report
from nessie_tests import route_observer as ro
from nessie_tests.manifest import NessieManifest, NessieManifestEntry, write_manifest


def default_route_criterion(variant) -> dict | None:
    return {"field": "route", "op": "eq", "value": ro.ROUTE_NS} if "base" in variant.tags else None


def _iso(clock):  # avoid datetime.now() so tests are deterministic
    return f"t={clock():.3f}"


def run_suite(*, base_url, auth_header, tier, scope="specific", family=None, variant_id=None,
              overlay_path, out_dir, post_query=None, get_progress=None, bundle_reader=None,
              pace_s=0.0, sleep=time.sleep, clock=time.monotonic) -> NessieManifest:
    if post_query is None or get_progress is None:
        post_query, get_progress = http_driver.make_default_clients(base_url, auth_header)
    variants = corpus.select(corpus.merged(overlay_path), scope=scope, family=family, variant_id=variant_id)
    started = _iso(clock)
    entries: list[NessieManifestEntry] = []
    for v in variants:
        expected_fail = "known_fail" in v.tags
        session_id = None
        v_status, v_route, v_engine, v_cost, failed, reason = "passed", None, None, None, [], ""
        t0 = clock()
        extra = default_route_criterion(v)
        try:
            for i, turn in enumerate(v.turns):
                if pace_s and i > 0:
                    sleep(pace_s)
                res = http_driver.drive(turn.query, tier=tier, post_query=post_query,
                                        get_progress=get_progress, session_id=session_id,
                                        sleep=sleep, clock=clock)
                session_id = res.session_id
                v_route, v_engine = res.route_obs.route, res.route_obs.engine
                qc = next((e["data"] for e in reversed(res.payload.get("progress") or [])
                           if e.get("event") == "query_complete"), {})
                v_cost = qc.get("total_cost_usd", v_cost)
                bundle_summary = None
                if tier == "full" and bundle_reader is not None and session_id is not None:
                    bundle_summary = bundle_reader(session_id)
                criteria = list(turn.pass_criteria) + ([extra] if extra else [])
                passed, results = evaluate.evaluate_turn(res.payload, criteria, res.route_obs,
                                                         last_reply=qc.get("reply"),
                                                         bundle_summary=bundle_summary)
                if not passed:
                    v_status = "failed"
                    failed += [f"{turn.label}:{r['field']}" for r in results if not r["passed"]]
        except Exception as exc:  # infra/endpoint failure ≠ assertion failure
            v_status, reason = "error", f"{type(exc).__name__}: {exc}"
        entries.append(NessieManifestEntry(
            id=v.id, family=v.family, tier=tier, status=v_status, route=v_route, engine=v_engine,
            cost=v_cost, elapsed_s=round(clock() - t0, 3), failed_criteria=failed,
            reason=reason, expected_fail=expected_fail))
    manifest = NessieManifest(started_at=started, ended_at=_iso(clock), tier=tier, scope=scope, entries=entries)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    write_manifest(manifest, Path(out_dir) / "manifest.json")
    report.generate_html(manifest, Path(out_dir))
    return manifest


def gate_failed(manifest: NessieManifest) -> int:
    """Count real failures (exclude expected_fail/known_fail)."""
    return sum(1 for e in manifest.entries if e.status in ("failed", "error") and not e.expected_fail)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd nessie_tests && uv run pytest tests/test_runner.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the whole host suite green**

Run: `cd nessie_tests && uv run pytest tests/ -v`
Expected: PASS (all tests from Tasks 1-9).

- [ ] **Step 6: Commit**

```bash
git add nessie_tests/runner.py nessie_tests/tests/test_runner.py
git commit -m "feat(nessie_tests): runner (select→drive→evaluate→record) + base route==ns + gate"
```

---

### Task 10: CLI

**Files:**
- Create: `nessie_tests/cli.py`, `nessie_tests/__main__.py`, `nessie_tests/tests/test_cli.py`

**Interfaces:**
- Consumes: `runner.{run_suite, gate_failed}`, `http_driver.basic_auth`, `bundle.summary_for_session`.
- Produces: `build_parser() -> argparse.ArgumentParser`, `main(argv=None) -> int`.

- [ ] **Step 1: Write the failing test** `nessie_tests/tests/test_cli.py`

```python
from nessie_tests import cli


def test_parser_defaults():
    a = cli.build_parser().parse_args(["--base-url", "http://h:8000"])
    assert a.tier == "route" and a.scope == "specific" and a.base_url == "http://h:8000"


def test_main_wires_run_suite(monkeypatch, tmp_path):
    captured = {}
    def fake_run_suite(**kw):
        captured.update(kw)
        from nessie_tests.manifest import NessieManifest
        return NessieManifest(started_at="a", ended_at="b", tier=kw["tier"], scope=kw["scope"], entries=[])
    monkeypatch.setattr(cli.runner, "run_suite", fake_run_suite)
    rc = cli.main(["--base-url", "http://h:8000", "--tier", "full", "--scope", "all",
                   "--user", "demo", "--password", "demopassword", "--out", str(tmp_path)])
    assert rc == 0
    assert captured["tier"] == "full" and captured["scope"] == "all"
    assert captured["auth_header"].startswith("Basic ")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd nessie_tests && uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nessie_tests.cli'`.

- [ ] **Step 3: Write `cli.py`**

```python
from __future__ import annotations
import argparse
from pathlib import Path
from nessie_tests import runner, http_driver

_OVERLAY = Path(__file__).resolve().parent / "overlay.json"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nessie_tests", description="Router-aware assistant e2e harness")
    p.add_argument("--base-url", required=True, help="e.g. http://localhost:8000")
    p.add_argument("--tier", choices=["route", "full"], default="route")
    p.add_argument("--scope", choices=["specific", "all"], default="specific")
    p.add_argument("--family", default=None)
    p.add_argument("--variant", default=None)
    p.add_argument("--user", default="demo")
    p.add_argument("--password", default="demopassword")
    p.add_argument("--pace", type=float, default=0.0)
    p.add_argument("--out", type=Path, default=Path("nessie_out"))
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    auth = http_driver.basic_auth(a.user, a.password)
    bundle_reader = None
    if a.tier == "full":
        from nessie_tests.bundle import summary_for_session
        bundle_reader = summary_for_session
    manifest = runner.run_suite(
        base_url=a.base_url, auth_header=auth, tier=a.tier, scope=a.scope,
        family=a.family, variant_id=a.variant, overlay_path=_OVERLAY,
        out_dir=a.out, bundle_reader=bundle_reader, pace_s=a.pace)
    fails = runner.gate_failed(manifest)
    print(f"nessie: {len(manifest.entries)} cases, {fails} real failures "
          f"(tier={a.tier} scope={a.scope}); report → {a.out}/report.html")
    return 1 if fails else 0
```

- [ ] **Step 4: Write `__main__.py`**

```python
import sys
from nessie_tests.cli import main

sys.exit(main())
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd nessie_tests && uv run pytest tests/test_cli.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add nessie_tests/cli.py nessie_tests/__main__.py nessie_tests/tests/test_cli.py
git commit -m "feat(nessie_tests): CLI (--tier/--scope/--family/--variant) + gate exit code"
```

---

### Task 11: Overlay content — route family + green anchors

**Files:**
- Modify: `nessie_tests/overlay.json`
- Create: `nessie_tests/tests/test_overlay_content.py`

**Interfaces:**
- Consumes: `corpus.load_overlay`.

- [ ] **Step 1: Write the failing test** `nessie_tests/tests/test_overlay_content.py`

```python
from pathlib import Path
from nessie_tests import corpus

OVERLAY = Path(__file__).resolve().parents[1] / "overlay.json"


def test_every_route_gate_case_asserts_route():
    ov = corpus.load_overlay(OVERLAY)
    gate = [v for v in ov if "route_gate" in v.tags]
    assert len(gate) >= 3
    for v in gate:
        fields = {c.field for t in v.turns for c in t.pass_criteria}
        assert "route" in fields, f"{v.id} missing a route assertion"


def test_has_cc_unrelated_and_green_families():
    ov = corpus.load_overlay(OVERLAY)
    fams = {v.family for v in ov}
    assert {"nessie_route", "nessie_green"} <= fams
    routes = {c.value for v in ov for t in v.turns for c in t.pass_criteria if c.field == "route"}
    assert {"container_cc", "unrelated", "nextseek_query"} <= routes
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd nessie_tests && uv run pytest tests/test_overlay_content.py -v`
Expected: FAIL (only 1 route_gate variant + no `nessie_green` family yet).

- [ ] **Step 3: Extend `overlay.json`** — add CC / NS / unrelated route-gate cases and green anchors. Replace the file with:

```json
{
  "version": "1.0",
  "families": {
    "nessie_route": {
      "description": "Top-level router assertions (route tier, no seed data needed)",
      "variants": [
        {"family": "nessie_route", "id": "route.cc_pipeline_launch",
         "name": "Pipeline launch → Container-CC", "tags": ["nessie", "route_gate", "specific"], "requires_env": [],
         "turns": [{"label": "main", "query": "Launch an nf-core rnaseq run for D.SEQ-240910LAU-135.",
                    "pass_criteria": [{"field": "route", "op": "eq", "value": "container_cc"}]}]},
        {"family": "nessie_route", "id": "route.cc_reingest",
         "name": "Reingest nf-core outputs → Container-CC", "tags": ["nessie", "route_gate", "specific"], "requires_env": [],
         "turns": [{"label": "main", "query": "Build a NExtSEEK upload sheet from the nf-core rnaseq outputs.",
                    "pass_criteria": [{"field": "route", "op": "eq", "value": "container_cc"}]}]},
        {"family": "nessie_route", "id": "route.ns_advanced",
         "name": "Keyword search → NS", "tags": ["nessie", "route_gate", "specific"], "requires_env": [],
         "turns": [{"label": "main", "query": "Find me mice treated with NDMA.",
                    "pass_criteria": [{"field": "route", "op": "eq", "value": "nextseek_query"}]}]},
        {"family": "nessie_route", "id": "route.unrelated",
         "name": "Off-topic → unrelated", "tags": ["nessie", "route_gate", "specific"], "requires_env": [],
         "turns": [{"label": "main", "query": "What's the weather in Boston tomorrow?",
                    "pass_criteria": [{"field": "route", "op": "eq", "value": "unrelated"}]}]}
      ]
    },
    "nessie_green": {
      "description": "Known-good anchors (full tier; require the seeded v2 instance)",
      "variants": [
        {"family": "nessie_green", "id": "green.global_count",
         "name": "Global sample count = 50,889", "tags": ["nessie", "green", "full"], "requires_env": [],
         "turns": [{"label": "main", "query": "How many samples are in the database?",
                    "pass_criteria": [{"field": "route", "op": "eq", "value": "nextseek_query"},
                                      {"field": "last_reply", "op": "mentions", "value": "50,889"}]}]},
        {"family": "nessie_green", "id": "green.mus_ndma",
         "name": "Mice + NDMA keyword search → 195", "tags": ["nessie", "green", "full"], "requires_env": [],
         "turns": [{"label": "main", "query": "Find mice treated with NDMA.",
                    "pass_criteria": [{"field": "route", "op": "eq", "value": "nextseek_query"},
                                      {"field": "parser_plan.mode", "op": "eq", "value": "new_search"},
                                      {"field": "entity_sampletype_codes", "op": "contains", "value": "MUS"},
                                      {"field": "api_ok", "op": "true"}]}]},
        {"family": "nessie_green", "id": "green.refine_recall",
         "name": "Refine 4-week study (0 → 303)", "tags": ["nessie", "green", "full"], "requires_env": [],
         "turns": [
           {"label": "seed", "query": "Find samples from a 4 week study.",
            "pass_criteria": [{"field": "route", "op": "eq", "value": "nextseek_query"}]},
           {"label": "refine", "query": "Just the 4 week ones.",
            "pass_criteria": [{"field": "parser_plan.mode", "op": "eq", "value": "refine_last_search"},
                              {"field": "api_ok", "op": "true"}]}]}
      ]
    }
  }
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd nessie_tests && uv run pytest tests/test_overlay_content.py tests/test_corpus.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add nessie_tests/overlay.json nessie_tests/tests/test_overlay_content.py
git commit -m "feat(nessie_tests): overlay route family (cc/ns/unrelated) + green anchors"
```

---

### Task 12: Overlay content — #32/#33 red repros + bonus-failure regressions + consistency wiring

**Files:**
- Modify: `nessie_tests/overlay.json` (add `nessie_repro` family + a top-level `consistency_groups` key)
- Modify: `nessie_tests/corpus.py` (add `load_consistency_groups(path) -> list[dict]`)
- Modify: `nessie_tests/runner.py` (run consistency groups; honor `known_fail`)
- Create: `nessie_tests/tests/test_repro_and_consistency_wiring.py`

**Interfaces:**
- Produces: `corpus.load_consistency_groups(path) -> list[dict]`; `runner.run_suite(..., run_consistency=True)` appends group results as `NessieManifestEntry(family="nessie_consistency", tier=..., status=...)`.

- [ ] **Step 1: Write the failing test** `nessie_tests/tests/test_repro_and_consistency_wiring.py`

```python
from pathlib import Path
from nessie_tests import corpus, runner

OVERLAY = Path(__file__).resolve().parents[1] / "overlay.json"


def test_repro_cases_are_known_fail():
    ov = corpus.load_overlay(OVERLAY)
    repro = [v for v in ov if v.family == "nessie_repro"]
    assert len(repro) >= 3
    assert all("known_fail" in v.tags for v in repro)


def test_consistency_groups_present_with_count_not_250():
    groups = corpus.load_consistency_groups(OVERLAY)
    assert any(g["assert"].get("count_not") == 250 for g in groups)


def test_runner_reports_consistency_group(tmp_path):
    ROUTED = {"status": "running", "progress": [
        {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None, "source": "baml", "reasoning": ""}}]}
    m = runner.run_suite(
        base_url="http://x", auth_header="Basic x", tier="route", scope="specific",
        overlay_path=OVERLAY, out_dir=tmp_path, run_consistency=True,
        post_query=lambda b: {"task_id": "t", "session_id": "s"},
        get_progress=lambda tid: ROUTED, sleep=lambda s: None, clock=lambda: 0.0)
    assert any(e.family == "nessie_consistency" for e in m.entries)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd nessie_tests && uv run pytest tests/test_repro_and_consistency_wiring.py -v`
Expected: FAIL (`load_consistency_groups` missing; no `nessie_repro`; `run_consistency` kwarg unknown).

- [ ] **Step 3: Add cases + `consistency_groups` to `overlay.json`** — insert this `nessie_repro` family and a sibling `consistency_groups` array (the `Catalog` model ignores extra top-level keys since Pydantic default; if it rejects, add `model_config = ConfigDict(extra="ignore")` is NOT allowed in vendored code — instead store groups under a key we read manually via `json.loads`, not through `load_catalog`). Add inside `families`:

```json
    "nessie_repro": {
      "description": "RED repros — expected to FAIL until #32/#33 + bonus bugs are fixed",
      "variants": [
        {"family": "nessie_repro", "id": "repro.parent_attr_aggregate",
         "name": "#32a child→parent sex/species aggregate", "tags": ["nessie", "known_fail", "full"], "requires_env": [],
         "turns": [
           {"label": "seed", "query": "Find me sequencing data associated with non human primates.",
            "pass_criteria": [{"field": "route", "op": "eq", "value": "nextseek_query"}]},
           {"label": "aggregate", "query": "Give me the unique counts of sex and species of all of those monkeys.",
            "pass_criteria": [{"field": "last_reply", "op": "matches_re", "value": "(male|female|specie|mulatta|fascicularis)"}]}]},
        {"family": "nessie_repro", "id": "repro.thin_bundle_recall",
         "name": "#32b recalled bundle carries attributes", "tags": ["nessie", "known_fail", "full"], "requires_env": [],
         "turns": [
           {"label": "seed", "query": "Find sequencing data for the parents of NHP samples.",
            "pass_criteria": [{"field": "route", "op": "eq", "value": "nextseek_query"}]},
           {"label": "recall", "query": "Which of those are RNA-seq?",
            "pass_criteria": [{"field": "bundle.has_extra_keys", "op": "true"}]}]},
        {"family": "nessie_repro", "id": "repro.eof_truncation_reporter",
         "name": "bonus: reporter plan EOF truncation", "tags": ["nessie", "known_fail", "full"], "requires_env": [],
         "turns": [{"label": "main", "query": "Write me an nf-core rnaseq report for the last results.",
                    "pass_criteria": [{"field": "last_reply", "op": "matches_re", "value": "^(?!.*could not be completed).*$"}]}]},
        {"family": "nessie_repro", "id": "repro.cypher_uid_dot",
         "name": "bonus: cypher UID / D.SEQ dot defect", "tags": ["nessie", "known_fail", "full"], "requires_env": [],
         "turns": [{"label": "main", "query": "Find sequencing data for NHP-220524FLY-1-PUB and NHP-220524FLY-2-PUB.",
                    "pass_criteria": [{"field": "last_reply", "op": "matches_re", "value": "(NHP-220524FLY|D\\.SEQ)"}]}]}
      ]
    }
```

And add a top-level sibling key (same file, after `families`):

```json
  ,"consistency_groups": [
    {"id": "cons.nhp_sequencing_engine", "name": "#33 NHP sequencing routes+counts must agree",
     "tags": ["nessie", "known_fail", "full"],
     "queries": ["Find NHP sequencing data.", "Find sequencing data for non-human primates."],
     "assert": {"same_route": true, "same_count": true, "count_not": 250}}
  ]
```

- [ ] **Step 4: Add `load_consistency_groups` to `corpus.py`**

```python
import json  # add to imports


def load_consistency_groups(path) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload.get("consistency_groups", [])
```

- [ ] **Step 5: Wire consistency into `runner.py`** — add param + loop. In `run_suite` signature add `run_consistency: bool = False`, and before writing the manifest:

```python
    if run_consistency:
        from nessie_tests import consistency
        for g in corpus.load_consistency_groups(overlay_path):
            def _drive(q):
                r = http_driver.drive(q, tier="full" if tier == "full" else "route",
                                      post_query=post_query, get_progress=get_progress,
                                      sleep=sleep, clock=clock)
                return {"route": r.route_obs.route, "count": consistency.get_result_count(r.payload)}
            gr = consistency.run_group(g, _drive)
            entries.append(NessieManifestEntry(
                id=g["id"], family="nessie_consistency", tier=tier,
                status="passed" if gr.passed else "failed",
                failed_criteria=gr.reasons, expected_fail="known_fail" in g.get("tags", [])))
```

- [ ] **Step 6: Run to verify it passes**

Run: `cd nessie_tests && uv run pytest tests/test_repro_and_consistency_wiring.py tests/test_runner.py -v`
Expected: PASS. Note the `repro.*` and consistency cases are `expected_fail=True`, so `gate_failed()` ignores them when they (correctly) fail on a live stack.

- [ ] **Step 7: Full host suite green**

Run: `cd nessie_tests && uv run pytest tests/ -v`
Expected: PASS (all unit tests).

- [ ] **Step 8: Commit**

```bash
git add nessie_tests/overlay.json nessie_tests/corpus.py nessie_tests/runner.py \
        nessie_tests/tests/test_repro_and_consistency_wiring.py
git commit -m "feat(nessie_tests): #32/#33 red repros + bonus regressions + consistency wiring"
```

---

### Task 13: README + live smoke against the stack

**Files:**
- Create: `nessie_tests/README.md`, `nessie_tests/tests_container/test_endpoint_contract.py`

**Interfaces:**
- Consumes: DRF `APIClient` fixtures from `nextseek_api/conftest.py`.

- [ ] **Step 1: Write the endpoint-contract test** `nessie_tests/tests_container/test_endpoint_contract.py` (proves the driver's body shape + route observer against the REAL view, in-process, no paid turn)

```python
import pytest


@pytest.mark.django_db
def test_query_async_accepts_minimal_body_and_returns_task(auth_client, mock_assistant_permission):
    # QueryRequest is extra="forbid"; the exact body the driver sends must validate.
    resp = auth_client.post("/nextseek_api/cc-assistant/query/async/",
                            {"query": "Find mice treated with NDMA.", "mode": "standard"}, format="json")
    assert resp.status_code == 202, resp.content
    body = resp.json()
    assert "task_id" in body and "session_id" in body


@pytest.mark.django_db
def test_stray_key_is_rejected(auth_client, mock_assistant_permission):
    resp = auth_client.post("/nextseek_api/cc-assistant/query/async/",
                            {"query": "x", "mode": "standard", "bogus": 1}, format="json")
    assert resp.status_code == 422
```

- [ ] **Step 2: Run it in-container**

```bash
docker cp nessie_tests/tests_container nextseek:/app/nessie_tests/tests_container
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nessie_tests/tests_container/test_endpoint_contract.py --no-migrations -v -p no:cacheprovider'
```
Expected: PASS (the `auth_client`/`mock_assistant_permission` fixtures come from `nextseek_api/conftest.py`; if the async task thread needs the stack, keep the assertion to the 202 + validation, not the turn result). If the background thread errors without live services, that's fine — this test only asserts the POST contract, not completion.

- [ ] **Step 3: Write `README.md`**

````markdown
# nessie_tests

Router-aware e2e harness for the Nessie assistant. Drives cases through the real
top-level router (`POST /nextseek_api/cc-assistant/query/async/`), reusing
`chat_nextseek/e2e`'s corpus + `PassCriterion` DSL, with **zero edits** to the
vendored `chat_nextseek`.

## Tiers
- **route** (fast, pre-merge): stops at the `route_decided` event; asserts
  `route`/`engine`/parser `mode`. No seed data or paid turn.
- **full** (paid, nightly): runs the turn to completion; asserts counts + bundle
  richness. Requires the **seeded v2 instance** (project ids 2-14).

## Scope
- `--scope specific` → only `route_gate`-tagged cases (+ consistency groups).
- `--scope all` → the full imported NS corpus + overlay.

## Run
Unit tests (host, isolated env): `cd nessie_tests && uv run pytest tests/ -v`
DB/contract tests (in-container): `docker cp` then
`docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek sh -c 'cd /app && uv run pytest nessie_tests/tests_container/ --no-migrations -v'`

Live route gate: `python -m nessie_tests --base-url http://localhost:8000 --tier route --scope specific`
Full pass: `python -m nessie_tests --base-url http://localhost:8000 --tier full --scope all`

## Cadence
Route gate = pre-merge (cheap). Full pass = nightly / pre-release on a seeded box.

## Known-fail (RED) cases
`nessie_repro` family + the `#33` consistency group are tagged `known_fail`:
they encode #32/#33 + the EOF/cypher bugs and are EXPECTED to fail until fixed.
`gate_failed()` excludes them, so they don't break the gate. Remove the
`known_fail` tag when the corresponding fix lands (that flips them into real
regressions).
````

- [ ] **Step 4: Commit**

```bash
git add nessie_tests/README.md nessie_tests/tests_container/test_endpoint_contract.py
git commit -m "docs(nessie_tests): README (tiers/scope/cadence) + endpoint-contract test"
```

---

## Self-Review

**1. Spec coverage:**
- Home `nessie_tests/` at repo root → Task 1. ✅
- Import corpus + DSL read-only → Tasks 1-2, 5. ✅
- Overlay catalog, same schema → Tasks 2, 11, 12. ✅
- Enter via real HTTP router → Task 4 + Task 13 contract test. ✅
- Two tiers (route short-circuit / full) → Task 4, wired in Task 9, CLI Task 10. ✅
- Scope specific/all → Task 2 `select`, CLI Task 10. ✅
- Three assertion primitives: route/engine field (Task 5), bundle-richness (Tasks 5+6), consistency group (Task 7, wired Task 12). ✅
- Red repros #32/#33 + bonus regressions → Task 12; green anchors → Task 11. ✅
- Structural DSL only, no LLM-judge → nothing added. ✅
- Assertion families by tier (route/mode both; count/bundle full-only) → route cases are route_gate/route tier; green + repro are `full`-tagged. ✅
- Manifest gains route/cost → Task 8. ✅
- Determinism/known-fail handling → `expected_fail`/`gate_failed` Task 9/12. ✅
- Seeded-instance prerequisite (full tier) → README Task 13 + `full` tags. ✅

**2. Placeholder scan:** No TBD/TODO. The only deferred item is the exact `count` key in `consistency.get_result_count` (Task 7 Step 3), which is coded with real fallbacks + an inline note to confirm against a live debug — not a placeholder, a tolerant default with tests on known payloads.

**3. Type consistency:** `RouteObservation` fields consistent across Tasks 3/4/5. `drive(... tier, post_query, get_progress ...)` signature identical in Tasks 4/9/12. `check_pass(debug, criteria, last_reply=…)` matches the reference. `NessieManifestEntry` fields consistent across Tasks 8/9/12. `bundle_reader(session_id) -> dict|None` matches `summary_for_session` (Tasks 6/9/10).

**One risk flagged for execution:** the `Catalog` Pydantic model may reject the extra top-level `consistency_groups` key. Task 12 Step 3 handles it by reading that key with a raw `json.loads` (`load_consistency_groups`) rather than through `load_catalog`, so the vendored model never sees it. If `load_catalog` itself errors on the extra key, the executor must keep `consistency_groups` in a **separate** `overlay_consistency.json` file — note this in Task 12 if hit.
