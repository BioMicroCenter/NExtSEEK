# PLAN-7 Phase 2 Review — iter 17 (fresh, canonical prompt)

**Target:** `nextseek_api/cc_assistant/archive/PLAN-7-compose-native-prod-deploy.md`  
**Locked design:** `SPEC-7-compose-native-prod-deploy.md`  
**Reviewer:** Independent cold-context adversarial (2026-06-30)  
**Note:** Reviewer ran in Ask mode; findings persisted by orchestrator from subagent transcript.

---

## 2A — Vet

### MEDIUM — Branch topology for Step-3 gate unstated
**Location:** Task 1 `step3_deploy_gate` — *"`live_gate_transcript.txt` committed on the branch under test"*
**Why:** Step 3 implements on `cc-step3-ui-io` off `feat/dmac-assistant-full-integration`. Plan never locks whether Step 7 branches after merge back or off `cc-step3-ui-io`. Wrong ancestor → gate fails or passes for wrong reason.
**Fix:** Lock branch ancestry in Global Constraints (Step 7 from post-Step-3-merge commit on integration branch).

### LOW — GitHub egress source unnamed
**Location:** Permissions table — "GitHub egress (plugin context generation)"
**Why:** Repo/credentials not named; mitigated by snapshot fallback.

---

## 2B — Stress Test

| Lens | Assessment |
|------|------------|
| **Most likely / catastrophic** | Task 6 raw Mount dict wrong JSON key casing → silent full-volume mount (2D-1) |
| **HIGH** | Task 10 never pins literal MBP `host_label` — only vague pattern in Task 2 |
| **Coverage** | Low risk — single pure validator module |
| **Rollback** | Adequate — pause-and-ask on MBP failure, secret scan, gate mismatch |

---

## 2C — Validate External Dependencies

| Dependency | Verdict | Notes |
|------------|---------|-------|
| docker-py 7.1.0 (PyPI latest) | OK | Plan correctly rejects `docker>=7.2.0` |
| Mount subpath (PR #3270) | Must-verify | Merged to main, unreleased; workaround must use Engine-API PascalCase |
| Engine ≥26 / Compose ≥2.26 | OK | Floor values correct |
| `docker>=7.1.0` pin location | **MEDIUM defect** | Pin lives in `dmac_assistant/pyproject.toml`, not root `pyproject.toml` |

---

## 2D — Gameproof

### CRITICAL — Raw Mount dict uses wrong key casing (`volume_options` vs `VolumeOptions`)
**Location:** Task 6 Step 2 — `_mount_volume_subpath` returning `{"type": "volume", "source": ..., "volume_options": {"Subpath": ...}}`
**Why:** docker-py 7.1.0 passes Mount list untransformed to Engine API. Go unmarshaler requires PascalCase `VolumeOptions`; lowercase `volume_options` is dropped silently. Entire `dmac-cc-users` volume mounts at each target — cross-user exposure with no error. Hermetic tests asserting subpath *string values* in Python dicts pass; existing isolation tests don't round-trip to daemon.
**Fix:** Build via `Mount(target=..., source=..., type="volume")` then `mount["VolumeOptions"] = {"Subpath": subpath}`, OR emit exact PascalCase keys. Hermetic test asserts serialized JSON keys include `VolumeOptions`/`Subpath`. Task 10: optional `docker exec` listing proving sibling paths invisible.

### MEDIUM — Doc-guard scope dodge for `/srv/dmac/users` host prep
**Location:** Task 8 Step 1 — guard only scans *numbered procedure*
**Why:** Forbidden `mkdir`/`chmod` can move to appendix; same dodge plan flags for Phase A/B elsewhere.
**Fix:** Scan entire `DEPLOY.md` for `/srv/dmac/users` + `mkdir`/`chmod` co-occurrence.

---

## Summary

| Severity | Count |
|----------|------:|
| CRITICAL | 1 |
| HIGH | 1 |
| MEDIUM | 4 |
| LOW | 1 |

**Top findings:**
1. **CRITICAL** — Mount raw-dict lowercase keys silently drop subpath → full volume exposure.
2. **HIGH** — Task 10 MBP `host_label` literal not locked (`"mbp"` vs vague pattern).
3. **MEDIUM** — `docker>=7.1.0` pin location wrong (root vs `dmac_assistant/`).
4. **MEDIUM** — Step 7 branch ancestry for live-gate transcript unstated.
5. **MEDIUM** — Doc-guard `/srv/dmac` scope limited to numbered section.

FINAL VERDICT: CONDITIONAL_ACCEPTANCE
