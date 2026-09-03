# Phase 2 fresh review (iter-18) — TARGET: PLAN-7-compose-native-prod-deploy.md

Cold-context independent review. Authority: SPEC-7 (locked) > PLAN-7 > task specs.
External reality verified empirically where load-bearing (docker-py 7.1.0 Mount, PLAN-3 Task 13
contract, cc_engine/cc_config source, compose, volumes.py).

**Reality checks that PASSED (no finding):**
- docker-py 7.1.0 IS installed in the dmac venv; `docker.types.Mount` IS a `dict` subclass with
  PascalCase keys (`Target/Source/Type/ReadOnly`); `m["VolumeOptions"] = {"Subpath": …}` works,
  and `HostConfig(version="1.45", mounts=[m]).get("Mounts")` transmits the injected
  `VolumeOptions.Subpath` verbatim to the Engine payload. The plan's primary mount strategy is
  **technically sound**.
- `cc_runner_available`, `_run_kwargs(volumes=…)`, `_build_volumes`, `DEFAULT_IMAGE=
  os.environ.get("NEXTSEEK_CC_IMAGE","dmac-assistant:poc")`, and the `make image-build` detail
  string all exist as the plan claims (cc_engine.py).
- `DEFAULT_NETWORK = os.environ.get("NEXTSEEK_CC_NETWORK","dmac-cc-net")` — agent already defaults
  to the segmented net; OI-3 segmentation default is correct.
- `docker/cc-runner/Dockerfile` exists (the lean image to avoid); `docker/cc-runtime/` absent (to
  create) — matches G7-3 negative test.
- `REQUIRED_VOLUMES` has exactly six SEEK volumes + prefix helper; compose has `/srv/dmac/users:
  /dmac/users` (line 28) + six `external: true` volumes — matches Task 6 "seven external" claim.
- PLAN-3 Task 13 writes `nextseek_api/cc_assistant/evidence/3-ui-based-io-live/
  live_gate_transcript.txt` and commits it (Step 9) — Task 1 `live_evidence_path` consumes exactly
  this. Migration command `migrate nextseek_api 0007_ccsessiontranscript` ⊇ marker
  `migrate nextseek_api 0007`. ✓

---

## 2A — Vet (permissions / inputs / execution snags)

### [MEDIUM] Task 3 Step 2 names no source path for the runtime port
Location: Task 3 Step 2 "Port the runtime assets" / Dependency Validation row "Standalone
`dmac-assistant` repo (port source) … Must-verify at execution".
Defect: The plan tells the implementer to "Port broadly enough to build the full…CC image" but
never states **from where** (no path, no clone instruction). Preflight only "records source
commit". A cold-start session executing PLAN-7 as a standalone contract has no source location.
(Partially mitigated: the project guide CLAUDE.md gives `/home/taishajo/work/dmac-assistant`, so a
reader of the guide can find it — hence MEDIUM not HIGH.)
Fix: Add to Task 3 Files/Step 2 the canonical port-source path (and the rule that Task 1 preflight
must pin its commit), e.g. "port from the `dmac-assistant` working clone recorded in preflight
`port_source_path`/`port_source_commit`".

### [LOW] Plugin-context generation command unspecified
Location: Task 3 Step 3 "Attempt generation first. If blocked, commit a snapshot…".
Defect: No generation command is named; the implementer must discover it. Mitigated by the G7-6
snapshot fallback (which records whatever command was attempted), so low impact.
Fix: Name the generation entrypoint (or state "discover from the dmac-assistant plugin build
tooling and record the exact command in evidence").

---

## 2B — Stress test

### [CRITICAL] Cross-user subpath isolation is unguarded by any REQUIRED check (catastrophic, gameable)
Location: Task 6 Step 1/2/4 hermetic assertions vs Task 10 "**Subpath isolation spot-check
(recommended)**" (line 518).
Defect: The whole G7-10 design rests on each sibling CC container mounting **only its own**
`{project}/{user}/…` subpath of `dmac-cc-users`. But every hermetic assertion in Task 6 checks key
**presence/casing only**: "assert `'VolumeOptions' in mount` and `'Subpath' in mount['VolumeOptions']`"
(line 318), "PascalCase keys … with nested `Subpath`" (line 295). **No required check asserts the
`Subpath` VALUE equals the expected per-user path.** Gameproof: if `_mount_volume_subpath` returned
`Subpath=""` (or a constant), the container would mount the **whole volume root** → every user sees
every other user's `input/scratch/cc-state/output` (cross-user data leak, OI-3 break). That
one-line mutation passes all required hermetic tests (`""` is still "in" VolumeOptions) and the
forced-CC turn (single user) still succeeds. The only check that would catch it — Task 10's subpath
spot-check — is labelled **"(recommended)"**, i.e. skippable, and is **not** a validator-enforced
§8 artifact. So a lazy implementer can ship a catastrophic isolation break that passes every gating
check. This is exactly the Risk-Register-#2 catastrophe ("cross-user leak") whose stated mitigation
("Hermetic `_build_volumes` tests; Step 2 isolation guards") does not actually exercise runtime
isolation.
Fix (both): (a) Make Task 6 hermetic tests assert the **concrete** per-user `Subpath` string for
each mount (e.g. `mount["VolumeOptions"]["Subpath"] == f"{project}/{user}/input"`), with an
explicit anti-empty/anti-constant negative control; (b) **promote** the Task 10 subpath isolation
spot-check from "recommended" to **required**, emit it as a named §8 evidence artifact (e.g.
`subpath_isolation_scan.txt`), and have the Step 7 validator fail the bundle if it is absent or
shows a foreign `{project}/{user}/` dir.

### [MEDIUM] Coverage floor targets the validator module, not the behavior-bearing Task 6 refactor
Location: Global Constraints "Coverage targets … `--cov=nextseek_api.cc_assistant.tests.
validate_step7_compose_deploy --cov-fail-under=95` (and per-module coverage for any other pure
modules added)".
Defect: The 95% floor is measured only on the **validator** module. The genuinely risky logic —
`cc_engine._build_volumes` / `_mount_volume_subpath`, `cc_config.CCPaths`, `cc_provision`,
`cc_sweep`, `services/cc_assistant` (the mount/Subpath/path-mapping refactor) — is **modified
existing** code, not a "pure module added", so the parenthetical doesn't bind it. Combined with the
CRITICAL above, branches of the mount-building code can be left untested while the suite is "green".
Fix: Declare an explicit coverage floor on the changed implementation modules (at least
`cc_engine.py`'s `_build_volumes`/`_mount_volume_subpath` and `cc_config.py`), or name the specific
behaviors that must be covered, rather than the vague "any other pure modules added".

### [MEDIUM] `migration_policy` required-ness is internally contradictory (greenfield dev-VM falsely rejected)
Location: Task 2 ("dev-only `migration_policy` when `host_label` is `dev-vm` or `nextseek-dev`")
+ Task 6 Step 5 ("validator fails if absent on dev-VM bundles") **vs** Task 9 Step 2 ("**Require**
`meta.json.migration_policy` … **when dev-VM had pre-cutover `/srv/dmac/users` data**").
Defect: Task 2 / Task 6 Step 5 read as **unconditional** on any dev-vm bundle; Task 9 makes it
**conditional** on prior host-bind data. A greenfield dev-VM (no `/srv/dmac/users` data, nothing to
migrate) would be **wrongly rejected** by the validator under the unconditional reading, and the
implementer cannot tell which rule to code into the validator.
Fix: State one rule: require `migration_policy` on dev-vm bundles **iff** `preflight` shows prior
`/srv/dmac/users` data (`had_host_bind_data == true`); otherwise it is optional. Align Task 2 and
Task 6 Step 5 wording to the Task 9 condition.

### [LOW] Rollback/Risk-Register mitigation for the leak case is mis-stated
Location: Risk Register rank 2 rollback "Hermetic `_build_volumes` tests; Step 2 isolation guards".
Defect: As shown in the CRITICAL, hermetic tests as specified cannot detect a wrong/empty Subpath;
the cited mitigation is not effective. Fix flows from the CRITICAL fix (concrete-value assertion +
required live isolation artifact); update this row to cite them.

---

## 2C — Validate external dependencies

### [PASS, verified] docker-py 7.1.0 Mount + Subpath
Location: Task 6 (lines 284, 308–318, 353).
Verified empirically (dmac venv): version 7.1.0; `Mount` is a `dict` subclass; injected
`VolumeOptions.Subpath` survives into `HostConfig.Mounts`. No `subpath` kwarg on `Mount.__init__`
(params are target/source/type/read_only/…), exactly as the plan states. The "do not pin
`docker>=7.2.0` (unsatisfiable)" and "raw-dict Subpath primary" guidance is correct. No finding.

### [LOW] Engine/Compose floor is a real prerequisite, not yet provable on a clean host
Location: DEPLOY Step 0 / Task 1 (`docker_engine_meets_subpath_floor`, Compose ≥2.26).
Note only: `VolumeOptions.Subpath` requires Engine API ≥v1.45 (Engine 26). The plan correctly
gates on this and validator re-parses version strings independent of the boolean flags — good. Flag
as must-verify on the actual MBP/dev hosts at execution (the plan already marks it so).

---

## 2D — Gameproof

### [HIGH] Transcript marker `celery inspect registered` does not match PLAN-3's actual command (false-fail or weakened oracle)
Location: Task 2 Step 2 "live transcript content markers … allowlist substrings: `migrate
nextseek_api 0007`, `cc_traces`, **`celery inspect registered`**, exit-code lines".
Defect: The committed `live_gate_transcript.txt` captures stdout/stderr of the **actual** command,
which per PLAN-3 (lines 1284, 2000) is `celery -A nextseek_api.batch_upload.celery_app inspect
registered`. The literal substring `celery inspect registered` is **not** present in that text (the
`-A <app>` segment sits between `celery` and `inspect registered`). If the validator does a literal
`in` check requiring this marker, it **false-fails a legitimate bundle**; a lazy implementer then
"fixes" it by deleting/weakening the marker — losing the anti-stub guarantee this marker exists to
provide. (The bare `celery inspect registered` at PLAN-3 line 2013 is prose in the .md, not in the
generated transcript.)
Fix: Change the required marker to `inspect registered` (or match `celery` AND `inspect registered`
as separate tokens, or pin the full `celery -A nextseek_api.batch_upload.celery_app inspect
registered`) so it matches the transcript PLAN-3 Task 13 actually produces.

### [Adequate, noted] Other gameproof oracles
- Task 10 `forced_cc_result.json.cost > 0` for the Opus forced turn + `cost <= budget_cap_usd`,
  legacy-filename rejection, `run_id` cross-correlation, `host_label` exact-match enum, Markdown-
  only rejection, screenshot OCR/manual-review requirement, subprocess `docker compose config`
  (not golden fixture) — all close the obvious cheap fakes. Good.
- The CRITICAL (2B) is the dominant remaining gameable hole; fix it first.

---

## Non-blocking cosmetic notes
- Permissions table row "Dev VM data migration … | **6 Step 4**" should read **6 Step 5** (the
  migration note is Task 6 **Step 5**; Step 4 is the path-builder refactor). Cross-ref error.
- `_run_kwargs` docstring in cc_engine.py still says the agent "joins … the nextseek compose
  network" while `DEFAULT_NETWORK` is `dmac-cc-net`; correct the stale comment during the Task 6
  cutover (harmless, but misleading to a reader checking segmentation).
- 1c memory: dropping the RO bind for a RW copy of merged `CLAUDE.md` into cc-state means the agent
  can transiently overwrite its own `CLAUDE.md` within a turn (re-rendered next turn). Deliberate
  G7-10 consequence; worth one line of note in Task 6, not a defect.
- Vetting Log skips iteration numbers (25 → 27 → 29); cosmetic.
