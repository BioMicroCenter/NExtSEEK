# Phase 2 fresh review (iter-20) — TARGET: PLAN-7-compose-native-prod-deploy.md

Cold-context adversarial review. Authority: SPEC-7 (locked) > PLAN-7 > task specs. Verified
PLAN-7's factual claims against live source (cc_engine.py, cc_provision.py, test_cc_realstack.py,
SPEC-2, PLAN-3 Task 13, docker-py 7.1.0). Most claims are accurate (see "Verified-accurate" note
at end). Substantive defects below.

---

## 2A — Vet (permissions / paths / resources)

### MEDIUM — Foreign-tree seeding mechanism for the isolation gate is unspecified
Location: Task 10 Step 4, "Seed (before the turn)": *"plant a **second** user's tree on
`dmac-cc-users` at `otherproj/bob/input/SENTINEL_FOREIGN`"* and the own marker at
`{project}/{user}/input/OWN_<run_id>`.
Why a defect: This is the single most important acceptance artifact (the rank-1/2 cross-user-leak
gate), but the plan never says **how** the operator writes `otherproj/bob/...` to the *root* of the
named volume before the turn. Normal provisioning (`build_user_dirs` + Django `mkdir`/`chmod` via
`*_mnt`) only ever creates the logged-in user's own `{project}/{user}/...` subtree — it has no path
that creates a *foreign* project/user dir. A cold-start implementer must invent a seeding step
(e.g. `docker run --rm -v dmac-cc-users:/v alpine sh -c 'mkdir -p /v/otherproj/bob/input && touch
/v/otherproj/bob/input/SENTINEL_FOREIGN'`) with no guidance, and must get the volume name right
(instance prefix!). Ambiguity that stalls a careful implementer on the gate that matters most.
Fix: Add an explicit seeding sub-step naming the mechanism (helper container mounting the named
volume at its root, prefix-aware) and the own-marker write (Django writing `OWN_<run_id>` into
`input_mnt` before spawn). Pin it so the foreign dirs land at the volume **root**, not under the
user subtree.

### LOW — `INTEGRATION_PLAN_PATH` default resolution under MBP fresh clone
Location: Task 1 Step 1 / Permissions table: default `../state/integration-plan.json` from NExtSEEK
root. On the MBP greenfield clone (Task 10), `../state/` does not exist (only the NExtSEEK repo is
cloned); the plan handles this via the `integration_plan_snapshot.json` exception, but the
*default* path will be a dangling relative path on that host. This is covered by the MBP exception,
so non-blocking — flagged only so the implementer wires the env var, not the default, on MBP.

---

## 2B — Stress Test

### HIGH — Cross-target transcript-marker handshake is contradictory (PLAN-7:132 vs PLAN-3 Task 13 Step 8)
Location: Task 2 Step 2, validator marker allowlist: greps **`Applying nextseek_api.0007`**,
**`cc_assistant.upload`**, **`cc_traces`**, and explicitly *"Do **not** require the command
substrings `migrate nextseek_api 0007` or `inspect registered`"*.
Predecessor reality (PLAN-3 Task 13 Step 8, verified): the producer instructs the PLAN-3
implementer to *"Ensure it contains the PLAN-7 §8 content-marker allowlist that PLAN-7 Task 1's
validator (PLAN-7:132) re-checks: **`migrate nextseek_api 0007`** (Step 3), `cc_traces`,
**`inspect registered`** (Step 0)…"* — i.e. it names the **command** strings that PLAN-7 explicitly
says it will NOT grep, and omits the two stdout strings PLAN-7 actually greps.
Why a defect: This is a **hard start-gate with no documented override** ("Step 7 MUST NOT start
until…"). Only `cc_traces` is named consistently across both docs. For the migration and celery
markers the two documents name **opposite** strings, and PLAN-3 has no test asserting PLAN-7's real
grep targets are present. The committed transcript (produced by an already-finished PLAN-3) can
satisfy PLAN-3's own checklist yet **lack `Applying nextseek_api.0007` / `cc_assistant.upload`**,
hard-blocking Step 7. Compounding fragility: `Applying nextseek_api.0007` is Django migrate **stdout
that only appears when 0007 is unapplied at run time** — on any re-run / already-migrated DB the
stdout is `No migrations to apply.` and the marker is absent, so a legitimately-deployed instance's
committed transcript can permanently fail the gate.
Fix: Reconcile the two contracts. In PLAN-7's validator, accept **either** form per marker — e.g.
grep `Applying nextseek_api\.0007|migrate nextseek_api 0007` (migration) and
`cc_assistant\.upload|inspect registered` (celery) — and/or require PLAN-3 Task 13 Step 8 to be
corrected to name PLAN-7's actual stdout strings. Add an explicit idempotency fallback for the
migration marker (accept a `0007` migration-state proof when output is `No migrations to apply.`).

### MEDIUM — Compose ≥2.26 floor hard-required even though runtime subpaths are applied via docker-py, not compose YAML
Location: Task 2 Step 2: *"require **both** Engine ≥26/API v1.45 **and** Compose ≥2.26"* …
*"fail when `docker_compose_meets_subpath_floor` is not `true`"* (unconditional in the validator);
Task 1 / DEPLOY Step 0 condition the Compose floor on *"when compose YAML uses volume subpaths."*
Why a defect: The **sibling** subpath mounts are emitted at runtime by `_mount_volume_subpath`
through the Engine API (docker-py) — verified; the compose file mounts the **whole** `dmac-cc-users`
into `nextseek` at `/dmac/users` with **no** `volume.subpath:` syntax. So the genuine floor is
**Engine ≥26 / API v1.45** (for the runtime `VolumeOptions.Subpath`); Compose ≥2.26 is only needed
*if* the compose YAML itself uses subpath syntax. The validator hard-fails on Compose <2.26
unconditionally, which can falsely reject a valid host whose compose file never uses subpath syntax.
Internal inconsistency between the conditional DEPLOY wording and the unconditional validator rule.
Fix: Either (a) make the Compose-floor check conditional on the compose file actually using
`volume.subpath:` (parse `compose_config.json`), or (b) state explicitly that the compose file WILL
use subpath syntax and pin where — and keep the unconditional floor consistent in both places.

### MEDIUM — Hidden dependency: `Applying nextseek_api.0007` requires fresh migration state (see HIGH above for full treatment)
Captured under the HIGH finding; listed here as the stress-test "hidden dependency" item: the gate
silently depends on migration-application order at PLAN-3 deploy time, a state not re-derivable from
the committed file.

---

## 2C — Validate External Dependencies

### (PASS) docker-py 7.1.0 Mount / VolumeOptions / Subpath — verified accurate
Ran the dmac venv (`/home/taishajo/work/dmac-assistant/.venv`, docker-py **7.1.0**):
`Mount.__init__` signature has **no `subpath`/`volume_options` kwarg** (confirmed), and Mount
serializes `VolumeOptions` as a PascalCase dict key. PLAN-7's helper
(`m = docker.types.Mount(...); m["VolumeOptions"] = {"Subpath": subpath}`) is sound: Mount is a dict
subclass passed straight into the HostConfig Mounts array, and when no labels/driver_config are
given `__init__` does not pre-set `VolumeOptions`, so the raw assignment is clean. The `docker>=7.1.0`
pin in `dmac_assistant/pyproject.toml` is present (verified); the "do not pin `docker>=7.2.0`
(unsatisfiable)" note is correct (PyPI latest 7.1.0). No dependency defect.

### LOW — `VolumeOptions.Subpath` is Engine-runtime-gated; "must-verify at execution" is appropriately flagged
The raw-dict approach only works if the **Engine** honors `VolumeOptions.Subpath` (API v1.45 /
Engine 26+). PLAN-7 gates this with `docker_engine_meets_subpath_floor` and labels the PR-#3270
release as "must-verify." Acceptable; no change required.

---

## 2D — Gameproof

### MEDIUM — `subpath_isolation_scan.txt` authenticity is not bound to a real live capture; the plan overclaims it
Location: Task 10 Step 4 Oracle / Task 2 validator: *"the bundle PASSES iff
`subpath_isolation_scan.txt` is non-empty AND contains the own marker filename … AND contains none
of `meta.json.foreign_tokens`"*; and *"proves a *live, correctly-mounted own subtree* was actually
listed."*
Quoted success condition: pure text grep on the file
(`grep -nE 'SENTINEL_FOREIGN|…otherproj…|…bob…'` must be empty; `own_marker` must be present).
Cheapest fake: the file is just text the operator places in the bundle; **nothing binds it to a
real `docker exec` against the live sibling**. An implementer who runs the real (paid) turn but hits
a real `Subpath=""` leak can simply hand-write/edit `subpath_isolation_scan.txt` to list a clean
`/data/input/OWN_<run_id>` and omit the foreign tokens — and pass the rank-1/2 catastrophe gate.
No-op test: if the live capture were silently skipped and replaced by an empty-but-for-own-marker
stub, the gate still passes — it guards provenance not at all. Mutation test: the plan's own
"mutation → grep RED" proof **only holds if the scan is genuinely captured**; the validator cannot
tell a genuine capture from a fabricated one, so the mutation is not actually guarded at the
artifact level.
Why a defect: This is the single runtime check for the locked OI-3 cross-user-leak invariant (the
whole reason for iter-18/19 hardening). The claim that the own-marker "proves a live, correctly-
mounted subtree was actually listed" is an overclaim — it proves the file *contains the string*,
which a forger who knows `<run_id>` can type. (Mitigant: a full forged bundle also needs a real
`forced_cc_result.json` cost>0 + sentinel + consistent proxy/network artifacts, so the *cheapest
overall* path is to capture honestly — which is why this is MEDIUM not HIGH — but the leak-cover-up
path is real and unclosed.)
Fix: Bind the scan to the run. Have the **test harness** (not the operator) write
`subpath_isolation_scan.txt` directly from the `docker exec` subprocess output during the turn
(operator never hand-authors it), and/or cross-correlate: require the scan to also contain a value
only the live correctly-mounted container could surface (e.g. the per-run scratch sentinel written
by the agent *inside* `/data/scratch` during the turn, captured in the same `find`), so a hand-typed
clean file cannot satisfy both the own-marker and the live-sentinel checks. Drop the "proves a live
… was actually listed" wording or back it with a provenance check.

---

## Non-blocking cosmetic notes
- Phase 2 Vetting Log jumps iteration/row numbers (25 → 27 → 29; rows 26/28 absent). Cosmetic.
- Task 1 lists `deploy_commit` twice in the same bullet block (lines ~86 and ~90). Harmless
  duplication.
- The `find` list in Task 10 Step 4 omits `/data/output` (intentional — output is not a sibling RO
  mount); fine, but a one-line note would prevent a reader thinking it was forgotten.

---

## Verified-accurate (no defect — recorded for the orchestrator's confidence)
- `cc_engine.py:338` docstring **does** stalely say the container "joins … the nextseek compose
  network"; PLAN-7 Task 6 Step 4 correctly flags it for fix. Actual `DEFAULT_NETWORK="dmac-cc-net"`
  (line 51).
- `finally` force-remove (`container.stop` + `container.remove(force=True)`) confirmed ~lines
  598–607; label `nextseek.cc.run=<run_id>` set at line 352 — the during-turn poll-by-label capture
  is genuinely necessary and producible.
- `_capture_agent_env` (test_cc_realstack.py ~89–104) and the background-thread run pattern
  (~152–167) exist as cited; realstack uses `project_dirname="personal-ccacc-ccacc"` matching the
  plan's `personal-ccacc-ccacc/ccacc` own-tree note.
- `build_user_dirs` (cc_provision.py) produces `{project}/{user}/input|scratch|cc-state/{session}|
  _memory/{session}` — matches the Task 6 Step 1 concrete Subpath assertions
  (`proj/alice/input`, etc.) and SPEC-2 §"Container mounts (D1)".
- `user_memory_file` RO file bind (cc_engine.py:391–392) and `_publish_artifacts(output_host_root=
  dirs.output_src)` (~line 575) exist as the plan describes for retirement.
- host_label enum, preflight schema fields, and the during-turn isolation-capture requirement in
  PLAN-7 match SPEC-7 §8 (no authority drift found).
