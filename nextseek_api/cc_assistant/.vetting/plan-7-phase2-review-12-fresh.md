# PLAN-7 Phase 2 Pre-Execution Review — Iter 12 (Fresh / Cold Context)

**Target:** `nextseek_api/cc_assistant/PLAN-7-compose-native-prod-deploy.md`  
**Locked design:** `nextseek_api/cc_assistant/SPEC-7-compose-native-prod-deploy.md` (G7-1–G7-10)  
**Reviewer:** Independent cold-context adversarial review (iter 12)  
**Date:** 2026-06-30  
**Baseline repo state verified:** `docker-compose.yml` still host-binds `/srv/dmac/users`; no `bedrock-proxy` / `dmac-cc-net` / `dmac-cc-users`; no `test_step7_*` or `validate_step7_*`; `cc_engine._build_volumes` returns host-bind `volumes` dict; `cc_runner_available()` still cites `make image-build`; tracker step 3 `not_started`.

**Iter-11 hardening spot-check (plan text only):** Task 6 Step 3 items 7–8 cover `path_mappings` / `_publish_artifacts`; Task 6 Step 2b adds `cc_config` refactor; Task 5 Step 3 updates `cc_runner_available()` strings; Task 9 Step 2 adds `host_label` + dev `migration_policy`; Task 2 lists compose/engine subpath floor fields in preflight/validator scope.

---

## 2A — Vet (permissions, snags, execution readiness)

### Finding 2A-1 — MEDIUM — Minimum Docker/Compose floors not in operator-facing deploy path

**Location:** Global Constraints / Task 1 preflight + Task 2 validator vs Task 8 DEPLOY rewrite  
**Quote:** *"`docker_engine_meets_subpath_floor` (bool), `docker_compose_meets_subpath_floor` (bool, Compose plugin ≥2.26)"`* (Task 1); Task 8 Step 2 documents pull → secrets → `./startup.sh install` → build → up → verifier only.  
**Why defect:** Task 6 sibling spawns require Engine ≥26 / API v1.45 for `docker.types.Mount` volume subpaths (docker-py ≥7.1.0). Preflight and validator gate on these floors, but DEPLOY.md rewrite and `docker/nextseek.env.example` never tell operators the minimum Engine/Compose versions. A cold implementer can pass doc guards and fail only at MBP Task 10 with an opaque mount error.  
**Fix:** Task 8 Step 2 + Task 7 env-template: add explicit minimums (Engine ≥26, Compose plugin ≥2.26 when subpath compose syntax is used; Engine ≥26 mandatory for runtime sibling mounts). Task 8 doc guard: fail if numbered procedure omits a “prerequisites / Docker version” step when subpath mounts are required.

### Finding 2A-2 — MEDIUM — Task 3 port scope vs in-tree `dmac_assistant/` package unstated

**Location:** Task 3 Purpose + File Structure vs live imports  
**Quote:** *"Replace dependency on the standalone `dmac-assistant` checkout with tracked NExtSEEK-owned runtime source."*  
**Why defect:** Live `cc_engine.py` imports `dmac_assistant.run_tracker.diff_files`; root `pyproject.toml` already vendors `dmac-assistant = { path = "dmac_assistant" }`. Task 3 scopes `docker/cc-runtime/` for the **agent image** but never states whether `dmac_assistant/` Python modules stay vendored, move into `docker/cc-runtime/`, or get inlined. A cold implementer may duplicate or break router/summary/run_tracker imports during the port.  
**Fix:** Task 3 Step 2: explicit boundary — `docker/cc-runtime/` = CC **container image** assets; in-tree `dmac_assistant/` package remains the Django-side Python dependency unless a named sub-step ports specific modules; list modules that must remain importable from nextseek (`run_tracker`, router BAML client, etc.).

---

## 2B — Stress Test

### Finding 2B-1 — HIGH — Subpath floor validator uses wrong boolean logic

**Location:** Task 2 Step 2  
**Quote:** *"fail when `preflight.json.docker_engine_meets_subpath_floor` is not `true` (Engine ≥26 / API v1.45) **and** `docker_compose_meets_subpath_floor` is not `true` (Compose ≥2.26)."*  
**Why defect:** `(not engine) AND (not compose)` passes when **either** flag is true. A host with Engine 25 + Compose 2.30 **passes** the validator but sibling `Mount(subpath=…)` fails at runtime — the catastrophic Task 6 failure mode. Engine floor is mandatory for programmatic subpath mounts; the current AND gate does not enforce it.  
**Fix:** Fail when `docker_engine_meets_subpath_floor != true` (always required for Task 6). Treat `docker_compose_meets_subpath_floor` separately: fail when compose YAML uses `volume.subpath` long syntax and compose floor is false; or document compose floor as advisory-only and drop the combined AND clause.

### Finding 2B-2 — HIGH — Task 6 Step 2b “gate Step 3” collides with tracker Step 3 gate language

**Location:** Task 6 Step 2b  
**Quote:** *"update `test_cc_config_paths.py` — **gate Step 3 on passing config tests**."*  
**Why defect:** Global Constraints and Task 1 already use “Step 3” for the **tracker UI-I/O deploy gate** (`step3_deploy_gate`, PLAN-3 Task 13). This line reads as “do not start tracker step 3 until cc_config tests pass” — the opposite of the real sequencing (Step 7 blocked **until** tracker step 3 is `done`). Likely intent: gate **Task 6 Step 3** (path builder + sibling spawn) on Step 2b passing. Ambiguity stalls or mis-orders a careful implementer.  
**Fix:** Replace with explicit wording: *"Complete Step 2b before Task 6 Step 3; hermetic `test_cc_config_paths.py` must pass before volume subpath spawn refactor."* Never use bare “Step 3” without task prefix in Task 6.

### Finding 2B-3 — MEDIUM — Post-cutover `DMAC_PATH_MAPPINGS` / display-path contract undefined

**Location:** Task 6 Step 3 items 7–8 + Success conditions  
**Quote:** *"Refactor `path_mappings` / `DMAC_PATH_MAPPINGS` in `run_cc_turn` to volume-relative logical roots (retire `output_src` / `scratch_src` host strings)"*; *"`_publish_artifacts` display paths … use mount-relative logical roots"*  
**Why defect:** Current JSON uses `"host_root": dirs.output_src` (host bind). After G7-10, `output_src`/`scratch_src` retire. Plan never defines replacement keys (`logical_root`, `volume_subpath`, in-container root only?) or what users/agents see in `artifacts_published` / `query_complete`. PLAN-3 Task 6 will change `_publish_artifacts` return shape — Step 7 cutover can silently break UI path display or agent path translation.  
**Fix:** Lock a schema in Task 6: e.g. `path_mappings` uses `{container_root, logical_root}` where `logical_root` is `{user_root_mount}/{project}/{user}/output|scratch`; `_publish_artifacts` returns paths under `logical_root`; add hermetic assertions in `test_cc_engine_publish.py` with concrete expected strings (not grep-only).

### Finding 2B-4 — MEDIUM — Task 6 step numbering out of order (2b after 3)

**Location:** Task 6 Steps 1 → 2 → 3 → **2b** → 4  
**Why defect:** Step 2b (`cc_config.CCPaths` refactor) is documented **after** Step 3 (path builder + memory mounts) but Step 2b text says it gates Step 3. Checkbox order invites implementing Step 3 on stale `host_user_root` types.  
**Fix:** Renumber: Step 2b → Step 3, current Step 3 → Step 4, current Step 4 → Step 5; or move Step 2b block immediately after Step 2 with explicit “must complete before Step 3”.

### Finding 2B-5 — MEDIUM — SPEC-7 §8 preflight schema omits `docker_compose_meets_subpath_floor`

**Location:** SPEC-7 §8 `preflight.json` vs PLAN Task 1/2  
**Quote (SPEC):** lists `docker_engine_meets_subpath_floor` only; PLAN also requires `docker_compose_meets_subpath_floor`.  
**Why defect:** Authority hierarchy is locked spec > plan. Validator implementer reading SPEC §8 alone may omit the compose floor field; plan Task 2 expects both. Drift between locked §8 and plan preflight collector creates a spec-side gap unless `/ultraplan amend` extends §8.  
**Fix:** Amend SPEC-7 §8 to add `docker_compose_meets_subpath_floor` with the same semantics as Task 1, **or** drop compose floor from plan if only Engine is mandatory (see 2B-1).

---

## 2C — Validate External Dependencies

### Finding 2C-1 — LOW — `docker>=7.1.0` “add to root pyproject.toml” is redundant

**Location:** Task 6 Step 2  
**Quote:** *"Add `docker>=7.1.0` to root `pyproject.toml`"*  
**Why note:** Root `pyproject.toml` already pulls `dmac-assistant = { path = "dmac_assistant" }`, and `dmac_assistant/pyproject.toml` pins `docker>=7.1.0` (present in `uv.lock` @ 7.1.0). Adding a duplicate direct dep is harmless but may confuse implementers into thinking docker-py was absent.  
**Fix:** Task 6 Step 2: “Confirm docker-py ≥7.1.0 available (via dmac-assistant path dep); add explicit root pin only if hermetic cc_engine imports need it outside dmac_assistant.”

### Finding 2C-2 — MEDIUM — Volume subpath pre-create requirement under-specified for bootstrap

**Location:** Task 6 Step 3 / Permission model  
**Quote:** *"Spawn only after provision mkdir for every subpath"*  
**Why defect:** Docker Engine volume subpaths require the subdirectory to **exist inside the named volume** before mount; Engine does not auto-create subpath (moby#47842). Plan relies on Django mkdir via `*_mnt` before spawn but does not require a hermetic negative test that mount fails when subpath is missing, nor document one-time empty-volume bootstrap on greenfield MBP (no `/srv/dmac/users` copy).  
**Fix:** Task 6 Step 1: failing test — spawn with missing subpath must error; Task 10 success: document first-user provision creating nested layout on empty `dmac-cc-users`.

**External verification (2C):** Docker Compose volume `subpath` long syntax requires Compose plugin ≥2.26.0; Engine subpath mounts require Engine ≥26 / API v1.45. Confirmed via Docker docs / moby release notes (2024–2025).

---

## 2D — Gameproof

### Finding 2D-1 — MEDIUM — `forced_cc_result.json.cost` field provenance unspecified

**Location:** Task 2 Step 2 + SPEC-7 §8  
**Quote:** *"`forced_cc_result.json.cost <= meta.json.budget_cap_usd` (default 2.0)"*  
**Cheapest fake:** Write `"cost": 0.01` in generated JSON with no tie to stream/ledger/proxy window.  
**No-op test:** Validator passes with hand-crafted cost field while real turn exceeded cap.  
**Mutation test:** Corrupting cost extraction in `run_cc_turn` leaves validator green if JSON is manually fixed.  
**Remedy:** Task 10 Step 4: specify cost source (e.g. `query_complete` `total_cost_usd`, stream-json line, or cross-check `ledger.json` if retained); Task 2 validator must reject when cost field absent or when cost exceeds cap without matching terminal event payload hash/correlation.

### Finding 2D-2 — MEDIUM — Realstack → Step 7 artifact rename/field map not in Task 5 interfaces

**Location:** Task 5 Step 3 vs Task 10 Step 4 vs SPEC-7 §8  
**Quote:** Task 5 Step 3 updates `test_cc_realstack.py` for DNS/container names only; SPEC §8 requires `forced_cc_result.json`, `proxy_log_window.txt`, `network_inspect.json`, `cc_runner_available.json`; live realstack writes `forced_result.json`, `proxy_log.txt`, `network.json`.  
**Cheapest fake:** Task 5 “updates realstack” for grep/DNS only; Task 10 adapter never lands; synthetic Task 2 bundles pass while MBP bundle uses wrong filenames.  
**Remedy:** Task 5 Step 3 or Task 10 Step 4: explicit artifact rename table (`forced_result.json` → `forced_cc_result.json`, `total_cost_usd` → `cost`, etc.); Task 2 negative control rejects legacy filenames under `acceptance_evidence/step7/`.

### Finding 2D-3 — LOW — Task 9 evidence capture still prose-level vs §8 filenames

**Location:** Task 9 Step 2  
**Quote:** *"Capture compose config, image ids, service status, network inspect, and `cc_runner_available()`"*  
**Cheapest fake:** Capture stdout in a single `notes.txt` instead of `compose_config.json`, `cc_runner_available.json`, etc.; rely on Task 9 “validator passes” without Step 2 listing required filenames for Task 9.  
**Remedy:** Task 9 Step 2: bullet list matching SPEC-7 §8 artifact basenames (subset acceptable for smoke if validator documents dev-smoke optional artifacts — currently it does not).

---

## Non-blocking cosmetic notes

- Phase 2 Vetting Log row 24 still says “pending fresh re-vet” for iter-11 hardening — update after this review lands.
- Task 6 Permission model `chmod 0777` is coarse but user-locked; not re-litigated here.
- `cc_runner_available()` correctly omits proxy health check; Step 7 validator adds proxy/segmentation — consistent with Dependency Validation table.

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH | 2 |
| MEDIUM | 8 |
| LOW | 3 |

**Top findings:**
1. **2B-1 (HIGH)** — Subpath floor validator AND logic lets Engine&lt;26 hosts pass.
2. **2B-2 (HIGH)** — Task 6 Step 2b “gate Step 3” collides with tracker Step 3 deploy gate wording.
3. **2B-3 (MEDIUM)** — No locked schema for post-volume `DMAC_PATH_MAPPINGS` / publish display paths.
4. **2D-1 (MEDIUM)** — `forced_cc_result.json.cost` gameable without provenance rules.
5. **2D-2 (MEDIUM)** — Realstack artifact rename map missing between Task 5 and SPEC §8 names.

**FINAL VERDICT: CONDITIONAL_ACCEPTANCE**
