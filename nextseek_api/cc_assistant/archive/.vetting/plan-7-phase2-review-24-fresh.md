# Phase 2 fresh review (iter-24, cold-context) — TARGET: PLAN-7-compose-native-prod-deploy.md

Reviewer: independent adversarial cold-context. Authority order applied: SPEC-7 (locked) > PLAN-7 > task specs.
Verification method: read TARGET + SPEC-7 + project guide in full; verified every load-bearing claim against
actual source (`cc_provision.py`, `cc_engine.py`, `cc_config.py`, `docker-compose.yml`, `startup/steps/volumes.py`,
`tests/test_cc_realstack.py`), the sibling `PLAN-3-ui-based-io.md` Task 13 Step 8, and the **installed docker-py
7.1.0** runtime behavior (live probe in `/home/taishajo/work/dmac-assistant/.venv`).

## Summary of independent verification (all PASSED)

1. **docker-py 7.1.0 Mount mechanism (the core feature).** Probed installed 7.1.0: `docker.types.Mount`
   IS a `dict` subclass; `m["VolumeOptions"] = {"Subpath": ...}` is accepted; the Mount serializes to PascalCase
   `Type/Source/Target/ReadOnly/VolumeOptions`. **Critically**, `HostConfig.__init__` does `self['Mounts'] = mounts`
   verbatim (no whitelist), so the manually-injected `VolumeOptions.Subpath` survives into the HostConfig sent to the
   Engine. The `_mount_volume_subpath` helper code in Task 6 Step 2 is correct and the primary path works on the
   pinned version. The "no `Mount.subpath` kwarg, patch PascalCase onto the dict" claim is accurate.
2. **Marker handshake byte-identical (thread B).** PLAN-7:137 and PLAN-3 Task 13 Step 8 (PLAN-3:67–72) name the
   same three allowlist strings verbatim: `Applying nextseek_api.0007` OR `[X] 0007_ccsessiontranscript`;
   `cc_assistant.upload`; `cc_traces`. Both sides explicitly exclude the command substrings. The `showmigrations`
   idempotency fallback is committed by PLAN-3 Step 8. Handshake is sound.
3. **Per-mount Subpath enumerated values (thread F, the open thread).** Verified against `build_user_dirs`
   (`cc_provision.py:99–109`): input=`proj/alice/input` (input_src), shared=`proj/shared` (shared_src, project-scoped,
   no user segment — SPEC-2 D5), scratch=`proj/alice/scratch`, cc-state=`proj/alice/cc-state/sess`,
   transcripts=`proj/alice/_memory/sess/transcripts` (memory_mnt tail + `/transcripts`). All five correct. The
   per-mount anti-empty/anti-constant negative control correctly avoids the blanket `{project}/{user}/` rule that
   would falsely fail the legitimately project-scoped `shared`. Thread F fix landed correctly.
4. **Live-capture necessity + lifecycle.** `cc_engine.py:598–607` `finally` does `container.stop()` + `remove(force=True)`
   on every exit, so post-turn `docker exec` is impossible — the during-turn poll-by-`label=nextseek.cc.run=<run_id>`
   capture is the only producible mechanism. `test_cc_realstack.py:89–104` `_capture_agent_env` and the background-thread
   pattern exist exactly as cited. Label is set at `cc_engine.py:352`.
5. **Paired isolation oracle soundness.** Depth math checks out: seed `/v/otherproj/bob/input/SENTINEL_FOREIGN` is
   reached by an in-turn `find /data/input -maxdepth 4` only under a root/empty-Subpath leak (depth-4), turning gate-1
   RED; an unseeded volume turns gate-0 (`pre_turn_seed_scan.txt`) RED before the turn. The live-sentinel is correctly
   described as an anti-stale binding (present under both correct and leaking mounts), NOT a leak detector.
6. **Existing-config claims.** `docker-compose.yml:28` has `/srv/dmac/users:/dmac/users` host bind + six `external: true`
   volumes (lines 134–144); `startup/steps/volumes.py` has `REQUIRED_VOLUMES` + `volume_names_for_prefix(prefix)`;
   `startup/lib/instance.py` / `.instance.json` infra exists. `cc_config.CCPaths` has `host_user_root` + `user_root_mount`.
   realstack `user_id="ccacc"`, `project_dirname="personal-ccacc-ccacc"` — matches the Task 10 seed path
   `personal-ccacc-ccacc/ccacc/input/OWN_<run_id>`. All accurate.
7. **Coverage exception legitimacy.** Hermetic floor rescoped to pure `cc_config` (commit-blocking `--cov-fail-under=95`);
   the behavior-bearing Subpath-value + publish-string assertions are ordinary RED-blocking tests (not the informational
   `--cov-report=term-missing` cc_engine surface); `run_cc_turn` runtime spawn covered by the non-deferrable live §8 gate.
   This is a legitimate, properly-paired exception, not a deferral.

## 2A — Vet (permissions / execution snags)
No blocking findings. The Permissions table (incl. `docker exec` into the live sibling, `alpine` pull for seed/scan
helpers, `uv` for `startup.sh install`, `INTEGRATION_PLAN_PATH`, Bedrock spend) is complete and matches the verified
runtime lifecycle. The HARD GATE (Step 3 fully deployed + committed `live_gate_transcript.txt` + tracker step 3 `done`)
is enforced in preflight and re-checked by the validator at `deploy_commit`.

## 2B — Stress Test
- Most likely failure mode (sibling Subpath wrong in docker-py): closed by RED-blocking concrete value assertions +
  the live paired gate. The primary-path serialization is now positively confirmed against installed 7.1.0.
- Most catastrophic (cross-user `Subpath=""` whole-volume leak): closed by the seed/in-turn paired oracle, with correct
  depth-4 reachability and the gate-0 vacuity backstop.
- Hidden dependency (Engine ≥26 / API v1.45 for Subpath): validator independently parses version strings and fails on
  Engine<26/API<1.45 even if the boolean flag lies. Compose ≥2.26 correctly made CONDITIONAL (no YAML subpath used here).
- Rollback conditions are explicit (pause-and-ask on failed MBP turn / secret-scan fail / gate mismatch).

## 2C — Validate External Dependencies
- docker-py **7.1.0** pinned in `dmac_assistant/pyproject.toml` (verified PyPI latest; no `Mount.subpath` kwarg; PascalCase
  raw-dict primary path confirmed working end-to-end). The plan correctly refuses to pin `docker>=7.2.0` (unsatisfiable).
- Docker Compose multi-network + external volumes: consistent with the project guide and current compose.

## 2D — Gameproof
- Task 6 per-user isolation: cheapest fake (`Subpath=""` with key present) is closed by per-mount exact-value
  assertions (incl. `shared`) whose RED blocks commit, plus the live paired gate.
- Task 10 authoritative gate: the per-run unpredictable `LIVE_<sentinel>` (agent-authored during the turn) and
  `OWN_<run_id>` cross-checks mean a bundle cannot pass without a real turn having run and written them. The plan is
  honest that "harness-written, never hand-authored" is a harness-implementation requirement a text-file validator
  cannot prove — this residual is the best achievable for a generated-evidence gate and is documented, not hidden.
- No-op/mutation heuristics: a stubbed `_build_volumes` or a one-line `Subpath` mutation goes RED on the hermetic
  value assertions; an empty/constant Subpath that slips past hermetic key-presence goes RED on the live foreign-token
  oracle. Oracles guard real behavior.

## Non-blocking cosmetic / LOW notes
- LOW: `test_cc_provision_input_mnt.py` is listed under Task 6 "Modify/extend" but does not yet exist in the repo. This
  is correct — it is a PLAN-3 Task 3 deliverable, and the Step-7 HARD GATE guarantees Step 3 is fully deployed (hence the
  file present) before Step 7 starts. A fresh implementer could be momentarily confused; a one-line "(created by PLAN-3
  Task 3)" annotation would remove all doubt. Not a defect.
- LOW: Phase 2 Vetting Log iteration numbering skips (…25, 27, 29…) — cosmetic.
- LOW: The cc_engine informational coverage run does not pin exactly which test files execute; the publish/value
  assertions are RED-blocking by ordinary pytest convention, but an explicit "these files' RED blocks the commit" line
  (as already stated for the Step 1/2 mount tests) would make the publish-string guard equally unambiguous.

## Verdict basis
Zero substantive (CRITICAL/HIGH/MEDIUM) defects found. Every load-bearing technical claim was independently verified
against actual source, the installed docker-py 7.1.0 runtime, and the sibling PLAN-3. The single open thread (F) is
correctly closed. Only LOW/cosmetic notes remain.
