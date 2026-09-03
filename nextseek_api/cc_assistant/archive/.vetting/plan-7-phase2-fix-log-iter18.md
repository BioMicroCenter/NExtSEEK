# PLAN-7 Phase 2 — Hardener fix-log (iter-18 findings)

Hardener pass against `.vetting/plan-7-phase2-review-18-fresh.md` (CONDITIONAL_ACCEPTANCE).
Surgical edits only. Targets edited: `PLAN-7-compose-native-prod-deploy.md` and
`SPEC-7-compose-native-prod-deploy.md` (§8 additive only). **PLAN-3 NOT edited.**
Phase 2 Vetting Log / status line NOT touched (orchestrator-owned).

## Canonical-owner re-reads (verbatim)
- **PLAN-3 Task 13 celery command (live read):** line 1284 and line 2000 both read
  `celery -A nextseek_api.batch_upload.celery_app inspect registered | grep cc_assistant.upload`.
  → The literal `celery inspect registered` is **absent** (the `-A <app>` segment intervenes);
  `inspect registered` **is** a true contiguous substring. New marker = `inspect registered`. ✓
- **SPEC-7 §8 (locked):** evidence-contract artifact list re-read (lines 160-200); amended additively.
- **`cc_provision.build_user_dirs` (live read):** per-user tree is
  `{host_root}/{project_dirname}/{user_id}/{input|scratch|cc-state/<session>|_memory/<session>}`
  → the volume-relative `Subpath` for each sibling mount is exactly
  `{project_dirname}/{user_id}/input`, `…/scratch`, `…/cc-state/<session>`,
  `…/_memory/<session>/transcripts`. My representative assertions use
  `proj/alice/input`, `proj/alice/scratch`, `proj/alice/cc-state/sess`,
  `proj/alice/_memory/sess/transcripts` — value-identical to what the mount helper produces.
- **docker-py reality:** review-18 empirically verified (lines 113-118) docker-py 7.1.0 `Mount` is
  a `dict` subclass and an injected PascalCase `VolumeOptions.Subpath` survives into
  `HostConfig.Mounts`. I did not re-run a state-mutating docker call (box has no importable
  `docker` on the bare interpreter; the dmac venv lives in-container). My new assertions check the
  `Subpath` **value**, which is independent of the already-verified casing.

## Verification table (before → after)

| # | Sev | Defect | Before | After | Location |
|---|-----|--------|--------|-------|----------|
| 1a | CRITICAL | Subpath key-presence only, no value | Step 2: `assert "VolumeOptions" in mount and "Subpath" in mount["VolumeOptions"]` | Step 1 new bullet asserts concrete `Subpath == "proj/alice/input"` (+scratch/cc-state/transcripts) **and** anti-empty/anti-constant negative control; Step 2 strengthened to assert `mount["VolumeOptions"]["Subpath"]` **equals** the per-user value, reject empty/root/constant | PLAN-7 L299-304, L327 |
| 1b | CRITICAL | Live isolation check "(recommended)", not enforced | Task 10: "Subpath isolation spot-check (recommended)" | Promoted to REQUIRED `subpath_isolation_scan.txt`: seed a 2nd user, scan agent mounts, validator fails if absent/empty/foreign path; added to Task 2 validator, Task 10 success conditions, Risk Register, Gameability Audit; **added to SPEC-7 §8 as REQUIRED artifact + Amendment Log** | PLAN-7 L529, L132, L556, L623, L661; SPEC-7 L194, L354-364 |
| 2 | HIGH | Marker `celery inspect registered` absent from transcript | allowlist substring `celery inspect registered` | allowlist substring `inspect registered` (+ inline note citing PLAN-3's real command) | PLAN-7 L132 |
| 3 | MEDIUM | Coverage floor only on validator module | `--cov=…validate_step7… --cov-fail-under=95` (+vague "pure modules added") | Added explicit `--cov=…cc_engine --cov=…cc_config --cov-fail-under=95` over Task 6 hermetic tests; live `containers.run` block carries `# pragma: no cover` (justified, exercised in Tasks 9/10) so pure mount logic + Subpath-value branches meet ≥95% | PLAN-7 L30 |
| 4 | MEDIUM | `migration_policy` required-ness self-contradictory | Task 2/Task 6 Step 5 read unconditional; Task 9 conditional | One rule everywhere: required **iff** `host_label∈{dev-vm,nextseek-dev}` AND `preflight.had_host_bind_data==true`; greenfield dev-VM optional + must pass. Added `had_host_bind_data` bool to Task 1 preflight schema. Aligned Task 2, branching table, Task 6 Step 5 to Task 9 | PLAN-7 L91, L132, L134, L370 |
| 5 | MEDIUM | Task 3 names no runtime-port source path | "Port broadly enough…" with no source | Named `/home/taishajo/work/dmac-assistant` (per project guide CLAUDE.md, which also documents `NEXTSEEK_SERVER` toggle); pinned in preflight `port_source_path`/`port_source_commit`; Files block + Step 2 + Step 3 updated | PLAN-7 L92, L161, L175, L182 |
| 6a | LOW | Plugin-context gen command unspecified | "Attempt generation first." | Added "discover the generation entrypoint from dmac-assistant plugin build tooling at `port_source_path` and record exact command in evidence" | PLAN-7 L182 |
| 6b | LOW | Risk-Register rank-2 mitigation mis-stated | "Hermetic `_build_volumes` tests; Step 2 isolation guards" | Cites concrete `Subpath`-value assertions + anti-empty control + REQUIRED live scan | PLAN-7 L623 |
| 6c | cosmetic | Permissions cross-ref `6 Step 4` | "Dev VM data migration … 6 Step 4" | "6 Step 5" | PLAN-7 L600-area |
| 6d | cosmetic | stale `_run_kwargs` "compose network" docstring | (not noted in plan) | One-line instruction in Task 6 Step 4 to fix the stale docstring (agent defaults to `dmac-cc-net`) | PLAN-7 L353-area |
| 6e | cosmetic | 1c RW-copy CLAUDE.md note | (not noted in plan) | One-line note added in Task 6 Step 4 (deliberate G7-10 consequence, not a defect) | PLAN-7 L353-area |

## Finding 2 — celery marker proof (verbatim)
- PLAN-3 Task 13 actual command (live, L2000):
  `celery -A nextseek_api.batch_upload.celery_app inspect registered | grep cc_assistant.upload`
- New PLAN-7 marker: **`inspect registered`** — confirmed contiguous substring of the above.
  Old marker `celery inspect registered` is NOT a substring (broken by `-A <app>`). PLAN-3 unchanged.

## Finding 1 — exact per-user Subpath values asserted
New hermetic assertions (representative user project_dirname="proj", user_id="alice", session_id="sess"):
- `input  → "proj/alice/input"`
- `scratch → "proj/alice/scratch"`
- `cc-state → "proj/alice/cc-state/sess"`
- `transcripts → "proj/alice/_memory/sess/transcripts"`
These are value-identical to `build_user_dirs` output (project segment / user segment / leaf), i.e.
the volume-relative path the mount helper produces. Negative control rejects `""`, `"/"`, constant,
or any value missing the `{project_dirname}/{user_id}/` prefix.

### SPEC-7 §8 strengthening — additive only (quoted)
Added artifact (SPEC-7 §8): `subpath_isolation_scan.txt` — REQUIRED cross-user isolation proof;
validator **fails** if absent/empty/foreign-path. Amendment Log entry "add REQUIRED
`subpath_isolation_scan.txt`" explicitly records: "This amendment only **adds** a stricter required
artifact to raise the gate to the level the locked invariant already demands; it weakens, removes,
or contradicts **no** existing locked decision (G7-1…G7-10 unchanged)." Confirmed: no G7 decision
edited; §8 existing artifacts untouched; only one new required artifact appended. This enforces the
already-locked OI-3 / G7-10 per-user `VolumeOptions.Subpath` isolation invariant.

## Coverage cross-check (--cov modules vs modules the task edits)
- Task 6 "Modify" list (PLAN-7 L62/L279) includes `cc_engine.py` and `cc_config.py` — the mount/
  path-builder modules. My added `--cov=nextseek_api.cc_assistant.cc_engine` and
  `--cov=nextseek_api.cc_assistant.cc_config` target exactly those. ✓ (`_mount_volume_subpath`,
  `_build_volumes`, `_run_kwargs` live in `cc_engine.py`; `CCPaths` in `cc_config.py`.)

## Authority-hierarchy check
- SPEC-7 (locked) edited only in §8, additively (one new required artifact + Amendment Log). No
  existing locked decision weakened/removed/contradicted. PLAN-7 changes are subordinate and
  consistent. No escalation required.

## Collateral changes
None. Every edit maps to a numbered finding above. No refactors, no unrelated cleanup.

## Defects NOT fully resolved
- **None of the iter-18 findings.** All CRITICAL/HIGH/MEDIUM/LOW + cosmetics addressed.
- One cosmetic note in the review ("Vetting Log skips iteration numbers 25→27→29") is in the
  Phase 2 Vetting Log table, which the orchestrator owns and the hardener is forbidden to edit —
  intentionally left for the orchestrator. Not a plan-content defect.

## Follow-up suggestions (NOT applied — out of scope for this pass)
- Consider promoting `had_host_bind_data`, `port_source_path`, `port_source_commit` into SPEC-7 §8's
  preflight schema in a future user-approved amendment for full plan↔spec parity (PLAN-7 preflight
  is currently a documented superset of the §8 minimum; this pass did not edit §8 beyond finding 1).
