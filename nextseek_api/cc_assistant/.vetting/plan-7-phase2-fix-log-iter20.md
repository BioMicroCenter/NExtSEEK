# PLAN-7 Phase 2 fix-log — iter-20 hardening (HARDENER, not a reviewer)

Closes the iter-20 fresh-review findings on PLAN-7 (+ the cross-plan marker-handshake
thread that also implicates PLAN-3 Task 13 Step 8). Surgical, full-thread. Each fix closed
against its own oracle. Vetting Log / status lines / defect-lineage ledger / PLAN-3 cc_traces
mirror were **not** touched.

Files edited:
- `PLAN-7-compose-native-prod-deploy.md` — Task 2 line 132 (markers, Engine/Compose floor, scan
  oracle), Task 10 Step 4 (seed mechanism, live sentinel, capture binding, oracle, mutation proof,
  success condition), Task 1 line 90 (LOW dedup).
- `PLAN-3-ui-based-io.md` — **Task 13 Step 8 ONLY** (producer marker allowlist + `showmigrations`).
- `SPEC-7-compose-native-prod-deploy.md` — **§8 ONLY**, additive/strengthening (scan authenticity).

---

## Thread B — marker handshake (HIGH, BOTH files) — CLOSED

### Real producer commands (PLAN-3 Task 13, re-read verbatim)
- Step 0: `celery … inspect registered | grep cc_assistant.upload` → stdout contains
  **`cc_assistant.upload`** (idempotent: present on any worker that registered the task).
- Step 3: `python manage.py migrate nextseek_api 0007_ccsessiontranscript` → emits
  **`Applying nextseek_api.0007_ccsessiontranscript… OK`** ONLY when 0007 is unapplied; an
  already-applied DB prints `No migrations to apply.` (the marker vanishes — the iter-20 HIGH).
- Step 6: saved `GET …?include=turns` excerpt → contains the JSON key **`cc_traces`**.

### Idempotency-robust fix
Added an OR fallback for the migration marker keyed on `python manage.py showmigrations
nextseek_api`, whose stdout prints **`[X] 0007_ccsessiontranscript`** on *any* already-applied DB.
PLAN-3 Task 13 Step 8 is now contracted to run `showmigrations nextseek_api` and save its stdout,
so the migration marker is present whether or not 0007 was freshly applied. The two non-idempotency
markers (`cc_assistant.upload`, `cc_traces`) are already deterministic and survive re-runs.

### Byte-identical allowlist — both sides, side by side
| Marker | PLAN-7 Task 2 (validator side) | PLAN-3 Task 13 Step 8 (producer side) |
|---|---|---|
| migration (form 1, fresh) | `` `Applying nextseek_api.0007` `` | `` `Applying nextseek_api.0007` `` |
| migration (form 2, idempotent) | `` `[X] 0007_ccsessiontranscript` `` | `` `[X] 0007_ccsessiontranscript` `` |
| celery | `` `cc_assistant.upload` `` | `` `cc_assistant.upload` `` |
| traces | `` `cc_traces` `` | `` `cc_traces` `` |

Validator grep for the migration OR (quoted on both sides):
`grep Applying nextseek_api\.0007|\[X\] 0007_ccsessiontranscript`.
Confirmed byte-identical via `grep -onE` over both files (4 marker strings + the shared
`showmigrations nextseek_api` producer command appear verbatim on both sides). PLAN-7 still
explicitly REFUSES the command substrings `migrate nextseek_api 0007` / `inspect registered`, and
PLAN-3 Step 8 now states the same refusal — both name only the saved **stdout** strings.

### Oracle
- Already-applied (idempotent) run: `migrate` prints `No migrations to apply.` but
  `showmigrations nextseek_api` prints `[X] 0007_ccsessiontranscript` → migration marker PRESENT
  via form 2 → transcript **PASSES**. (Previously FAILED — the regression this fix closes.)
- Transcript missing the 0007 marker entirely (neither `Applying nextseek_api.0007` nor
  `[X] 0007_ccsessiontranscript`) → grep empty → **FAILS**. Gate teeth intact.

---

## Thread A — isolation gate (2 MEDIUM, PLAN-7 + SPEC-7 §8) — CLOSED

### A1 — Foreign-sentinel SEEDING now specified (Task 10 Step 4 "Seed")
A cold implementer no longer has to invent the seeding step. Added the exact prefix-aware
mechanism: a helper container mounting the **named volume at its root** writes the foreign subtree
at the volume root (not under the user subtree):

```bash
vol=$(docker volume ls -q | grep -E '(^|_)dmac-cc-users$' | head -1)
docker run --rm -v "$vol":/v alpine sh -c \
  'mkdir -p /v/otherproj/bob/input && touch /v/otherproj/bob/input/SENTINEL_FOREIGN'
```

…and the own-marker write into the user's own input subpath (`personal-ccacc-ccacc/ccacc/input/
OWN_<run_id>`, equivalently via Django `input_mnt`). The oracle then asserts the foreign subtree is
ABSENT from the agent's `find` view. Foreign tokens / own_marker / live_sentinel are required to be
pairwise disjoint.

### A2 — Scan-file AUTHENTICITY bound to the live capture (Task 10 Step 4 + Task 2 + SPEC §8)
Two binding mechanisms added so a hand-edited clean scan cannot pass the catastrophe gate:
1. **Harness writes the file** directly from the live `docker exec … find` subprocess stdout
   (polled by `label=nextseek.cc.run=<run_id>` while the container is alive) — no operator
   hand-authoring step exists.
2. **In-container live sentinel cross-check**: the forced-turn prompt instructs the agent to write
   `LIVE_<sentinel>` (unpredictable per-run token) into its **own** `/data/scratch` during the
   turn; the `find` (which already covers `/data/scratch`) captures it; recorded as
   `meta.json.live_sentinel`. Validator requires the scan to contain `own_marker` AND
   `live_sentinel` AND none of `foreign_tokens`.

### A2 oracle (mutation + fabrication)
- `Subpath=""` mutation: `/data/input` and `/data/scratch` become the whole volume root → the
  `find` lists `otherproj/bob/input/SENTINEL_FOREIGN` (foreign grep matches) AND the agent's own
  `/data/scratch` is no longer the user's scratch so `LIVE_<sentinel>` is missing → gate RED on
  two independent checks.
- Fabricated/stale scan: the harness writes the file from live stdout, so a hand-typed clean file
  has no insertion point; even a substituted clean text from a *different* (clean) run lacks
  **this** run's agent-authored `LIVE_<sentinel>` → live-sentinel cross-check FAILS.

### SPEC-7 §8 change (additive / strengthening only)
- BEFORE: "...The validator **fails** the bundle if this artifact is absent, empty, or shows any
  foreign user path / seeded foreign token. This enforces the OI-3 / G7-10 ... invariant..."
- AFTER: same, plus an inserted "**Authenticity binding (amended 2026-06-30 — additive/
  strengthening)**" clause requiring (1) the harness to write the file directly from the live
  `docker exec … find` subprocess stdout (never operator hand-authored), and (2) an in-container
  live sentinel (`LIVE_<sentinel>`, recorded as `meta.json.live_sentinel`) whose filename the scan
  MUST contain, with the validator failing if absent.
- Requirement only STRENGTHENED: §8 already mandated live-during-turn capture; this adds the
  harness-authored binding + live-sentinel cross-check. No requirement weakened.

---

## Compose/Engine floor (MEDIUM, PLAN-7) — CLOSED
Re-read the real requirement: per-user subpaths are applied at runtime by `_mount_volume_subpath`
via **docker-py (Engine API) `VolumeOptions.Subpath`** — the compose YAML mounts the **whole**
`dmac-cc-users` volume at `/dmac/users` with **no** `volume.subpath:` syntax (Task 5/Task 6).

Corrected validator clause (Task 2 line 132):
- Engine ≥26 / API v1.45 is the **unconditional, real floor** (gates the isolation mount); fail
  when `docker_engine_meets_subpath_floor` is not `true`.
- The Compose ≥2.26 floor is now **CONDITIONAL** — enforced (and `docker_compose_meets_subpath_floor
  == true` required) **only when `compose_config.json` shows the compose YAML uses `volume.subpath:`
  syntax**. Since this plan uses no YAML subpath syntax, Compose ≥2.26 is not hard-required and a
  valid host whose compose never uses subpath syntax must not be rejected on the Compose floor.
- Reconciled with the already-conditional DEPLOY Step 0 (line 443) and Task 1 Step 2 (line 100)
  wording — the prior internal inconsistency (unconditional validator vs conditional DEPLOY) is
  removed. No other unconditional Compose floor remains in PLAN-7 (verified).

---

## LOW / cosmetic (PLAN-7 iter-20) — addressed
- **Duplicated `deploy_commit`** in Task 1 (lines ~86/~90): removed the redundant repeat from the
  line-90 field list; added "(`deploy_commit` is defined above — not repeated here.)".
- **`/data/output` omission** in the Task 10 `find` roots: added a one-line note that it is
  intentionally excluded (not a sibling RO subpath mount).
- **`INTEGRATION_PLAN_PATH` default on MBP fresh clone** (LOW, line 29): already covered by the
  MBP snapshot exception, which Task 10 Step 1 already wires
  (`INTEGRATION_PLAN_PATH=<run_dir>/integration_plan_snapshot.json`). No change needed —
  non-blocking and already handled; flagged here for completeness.
- Vetting Log row-number gap (cosmetic): NOT touched (Vetting Log is out of scope).

---

## Self-verification
- Re-read each edited section in all three files after editing (PLAN-7 line 132 full re-read;
  Task 10 Step 4 seed/capture/oracle/mutation; PLAN-3 Step 8; SPEC §8).
- Thread B: confirmed both sides name the byte-identical 4-marker allowlist + the
  `showmigrations nextseek_api` producer command via `grep -onE` (counts match). Confirmed the
  chosen markers survive an already-applied (idempotent) migration run (form 2 `[X]
  0007_ccsessiontranscript` from `showmigrations`, plus the idempotent `cc_assistant.upload` and
  `cc_traces`). Quoted the real producer command stdout forms above.
- Thread A: quoted the seeding helper-container step and the harness-writes-from-stdout +
  live-sentinel binding; walked the `Subpath=""` mutation (foreign grep matches AND live sentinel
  missing → RED) and the fabricated-scan path (no insertion point + missing live sentinel → RED).
- Compose/Engine floor: quoted the corrected requirement (Engine ≥26 unconditional; Compose ≥2.26
  conditional on YAML `volume.subpath:`).
- SPEC-7 §8: quoted before/after; requirement only strengthened.

Defects unresolved: none.
