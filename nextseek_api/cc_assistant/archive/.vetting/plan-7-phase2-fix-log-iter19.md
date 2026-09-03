# PLAN-7 Phase 2 — Hardener fix-log (iter-19 findings)

Target: `PLAN-7-compose-native-prod-deploy.md` (+ `SPEC-7 §8` capture-mechanism correction).
Source review: `.vetting/plan-7-phase2-review-19-fresh.md` (CONDITIONAL_ACCEPTANCE).
Role: HARDENER (not reviewer). Did **not** touch the Phase 2 Vetting Log table or Phase 2 status line.

These were hardening-induced regressions from the iter-18 live-gate addition. All defects (2 HIGH,
1 MEDIUM, 4 LOW/cosmetic) fixed surgically. The required live gate was **strengthened, not lowered**:
the capture mechanism was repaired so the gate runs the real live component, and the oracle was made
mutation-sensitive.

---

## Canonical owners re-read VERBATIM before editing

- `cc_engine.py:598-607` — the `finally` that force-removes the transient agent (HIGH-1 root cause).
- `tests/test_cc_realstack.py:89-104` — `_capture_agent_env` live-poll-by-label pattern (mirrored).
- `tests/test_cc_realstack.py:152-167` — the run-on-background-thread driving of `run_cc_turn`.
- `PLAN-3-ui-based-io.md` Task 13 Step 0/3/6/8 — the real transcript producer contract (MEDIUM-3).
- `SPEC-7 §8` line 194 — the locked required `subpath_isolation_scan.txt` artifact.

---

## HIGH-1 — isolation scan captured after the agent is force-removed (infeasible)

**Defect:** Task 10 Step 4 said *"After the forced-CC turn, capture … a `docker exec <agent> ls -la
/data/input /data/scratch`"*, but `cc_engine.run_cc_turn`'s `finally` removes the agent, so no
container exists post-turn. SPEC-7 §8 mirrored the same post-turn `docker exec` example.

**Real removal code (verbatim, `cc_engine.py:598-607`):**
```python
    finally:
        if container is not None:
            try:
                container.stop(timeout=5)
            except Exception:
                pass
            try:
                container.remove(force=True)
            except Exception:
                pass
```

**Mirrored live-poll pattern (verbatim, `test_cc_realstack.py:89-104`):**
```python
    def _capture_agent_env(self, run_id, deadline_s=120):
        """Poll for the live sibling by label and capture its Config.Env."""
        end = time.time() + deadline_s
        while time.time() < end:
            ps = _docker("ps", "-q", "--filter", f"label=nextseek.cc.run={run_id}")
            cid = (ps.stdout or "").strip().split("\n")[0]
            if cid:
                ins = _docker("inspect", "-f", "{{json .Config.Env}}", cid)
                ...
```
(`test_cc_realstack.py:152-167` already drives `run_cc_turn` on a background `threading.Thread` and
calls `_capture_agent_env(self.run_id, …)` while the turn runs — the exact concurrency model copied.)

**Fix:** Rewrote Task 10 Step 4 to capture **DURING the turn**: run `run_cc_turn` on a background
thread, poll `docker ps -q --filter label=nextseek.cc.run=<run_id>` until the cid appears, then
`docker exec "$cid" find …` **while the container is alive**. Corrected SPEC-7 §8 line 194's example
to the during-turn capture (mechanism only — requirement unchanged; see §"SPEC-7 §8" below).

**Confirmed:** the new capture runs against a polled-by-label cid *before* `run_cc_turn` returns and
hits its `finally`, i.e. while the container is alive — identical to the working `_capture_agent_env`.

---

## HIGH-2 — non-recursive `ls` + slash-path matcher cannot surface the empty-Subpath leak

**Defect:** the leak (`Subpath=""`/`"/"` → whole `dmac-cc-users` root mounted at `/data/input`)
makes a non-recursive `ls -la /data/input` show only top-level project *names* (`otherproj`,
`personal-…`), never the slash-joined `otherproj/bob/` path nor the 3-deep `SENTINEL_FOREIGN`. A
validator grepping for a foreign `{project}/{user}/` *path* matches nothing → the gate the iter-18
CRITICAL added passes with the leak live.

**Fix (recursive listing + positive-allowlist/foreign-token oracle + pinned grep):**
- Capture command pinned to `docker exec "$cid" find /data/input /data/scratch /data/shared
  /home/user/.claude /home/user/.cc-memory/transcripts -maxdepth 4 -printf '%y %p\n'`. `-maxdepth 4`
  reaches the depth-4 `otherproj/bob/input/SENTINEL_FOREIGN` exposed by a root-mounted leak.
- Seed: a foreign tree `otherproj/bob/input/SENTINEL_FOREIGN` **plus** the forced-CC user's own
  marker file by **name** at `{project}/{user}/input/OWN_<run_id>`. (Correction caught during
  self-verify: a `find` listing shows *paths*, not file *contents*, so the liveness/anti-stub check
  must key on a planted *filename* — `OWN_<run_id>` — not the scratch *content* sentinel. Recorded
  `meta.json.own_marker` and `meta.json.foreign_tokens`.)
- Oracle (pinned, run by the Task 2 validator): PASS iff the scan is non-empty AND contains
  `meta.json.own_marker` (`OWN_<run_id>`) AND `grep -nE
  'SENTINEL_FOREIGN|(^| |/)otherproj(/|$| )|(^| |/)bob(/|$| )'` finds **zero** matches.

**Concrete RED walk-through under empty-Subpath mutation:** `_mount_volume_subpath` emitting
`Subpath=""` mounts the whole volume root at `/data/input`; `find … -maxdepth 4` then lists
`otherproj/bob/input/SENTINEL_FOREIGN` (and `otherproj`, `otherproj/bob`); the pinned foreign grep
matches `SENTINEL_FOREIGN`/`otherproj`/`bob` → validator fails the bundle → gate RED. In the correct
case `/data/input` is the `{project}/{user}/input` subpath, the foreign tree is unreachable, the grep
is empty, and `OWN_<run_id>` is present directly under `/data/input` → green. An empty hand-written
stub: foreign grep empty (no match) but `own_marker` absent → fails the anti-stub check.

---

## MEDIUM-3 — validator transcript markers are command-text, not contracted stdout

**Defect:** markers `migrate nextseek_api 0007` and `inspect registered` are *command* substrings;
PLAN-3 Task 13 Step 8 contracts only *"saved stdout/stderr + exit codes for every Task 13 command"*
(verbatim, `PLAN-3-ui-based-io.md:2100`), not echoed command lines. A legitimate already-committed,
SHA-frozen transcript could be falsely rejected at the hard start-gate.

**Real produced-output forms confirmed in PLAN-3 Task 13:**
- Step 3 runs `migrate nextseek_api 0007_ccsessiontranscript` (`:2073`); the migration's real stdout
  is the dot/module-path form `Applying nextseek_api.0007_ccsessiontranscript… OK`.
- Step 0 runs `… inspect registered | grep cc_assistant.upload` (`:2046`); the grep's stdout is the
  registered-task name `cc_assistant.upload`.
- Step 6 saves a `GET …?include=turns` excerpt showing non-empty `turns[*].cc_traces` (`:2091`) — so
  `cc_traces` is a guaranteed JSON-key substring of the saved output.

**Fix:** markers switched to `Applying nextseek_api.0007`, `cc_assistant.upload`, and `cc_traces`
(all guaranteed substrings of real stdout). Explicit instruction added to **not** require the command
substrings `migrate nextseek_api 0007` / `inspect registered`.

---

## LOW / cosmetic

- **LOW (2A) — permissions table omits live-container `docker exec`.** Added a Permissions row:
  `docker ps --filter label` + `docker exec` into the **live** transient agent (Tasks 9–10), noting
  the agent is force-removed at turn end (`cc_engine.py:598-607`) so capture must poll by label.
- **LOW (2A / cosmetic) — `docker>=7.1.0` already pinned; "coordinated bump" is a no-op.** Reworded
  Task 6 Files (line ~287) and Task 6 Step 2 (line ~317): "already present / confirm … no version
  change; regenerate the lockfile only if other deps change."
- **LOW (2B) — "exit-code lines" marker unpinned.** Removed `exit-code`/`exit-code lines` from the
  required transcript substring allowlist (PLAN-3 leaves the exit-code format unpinned, so a hard
  requirement would risk false rejection); folded the rationale into the MEDIUM-3 edit.
- **LOW (2D, stale) — Gameability row 6 remedy described the broken slash-path matcher.** Updated to
  cite the recursive-during-turn capture + positive-allowlist/foreign-token oracle.

**Cosmetic notes NOT actioned (with reason):**
- "Vetting-log row numbering skips rows 26/28" — that table is the **orchestrator-owned Phase 2
  Vetting Log**; the task prohibits touching it. Left as-is.
- "SPEC-7 line 185 / Task 2 both restate the `host_label` enum (redundant but consistent)" — the
  review itself calls it consistent; no defect, no change (avoids collateral churn).

---

## SPEC-7 §8 — capture-mechanism correction (requirement NOT weakened)

Per the authority note, §8 prescribed the broken post-turn `ls` capture, so I corrected the mechanism
to make the already-locked required artifact achievable.

**Before (`SPEC-7 §8:194`):**
> Listing of the transient CC agent's mounted trees (e.g. `docker exec <agent> ls -la /data/input
> /data/scratch` plus cc-state/transcripts) taken with at least one **other** user's tree seeded …
> proving the sibling container sees **only** the forced-CC user's own `<project>/<user>/` subpath and
> no foreign `<project>/<user>/` directory. The validator **fails** the bundle if this artifact is
> absent, empty, or shows any foreign user path. …

**After:**
> A **recursive** listing of the transient CC agent's mounted trees (e.g. `docker exec <cid> find
> /data/input /data/scratch /data/shared … -maxdepth 4`), **captured during the turn** from the live
> sibling polled by `label=nextseek.cc.run=<run_id>` — the agent is force-removed when the turn ends,
> so a post-turn `docker exec` cannot produce this artifact, and a non-recursive `ls` cannot reach a
> seeded foreign sentinel — taken with at least one **other** user's tree seeded … (e.g.
> `otherproj/bob/input/SENTINEL_FOREIGN`), proving the sibling container sees **only** the forced-CC
> user's own `<project>/<user>/` subpath and no foreign user tree. The validator **fails** the bundle
> if this artifact is absent, empty, or shows any foreign user path / seeded foreign token. …

**Confirmation:** only the *capture method* and *leak-detection depth* changed; the REQUIRED status,
the validator-fails-on-{absent,empty,foreign} requirement, and the OI-3/G7-10 invariant are intact.
No locked decision weakened or removed.

---

## Verification table (before/after, re-read at the cited line ranges post-edit)

| Defect | Location | Before | After | Landed |
|--------|----------|--------|-------|--------|
| HIGH-1 | PLAN-7 Task 10 Step 4 (L529-546) | post-turn `docker exec <agent> ls -la` | during-turn poll-by-label `find` on live cid (mirrors `_capture_agent_env`) | ✔ |
| HIGH-1 | SPEC-7 §8 (L194) | post-turn `ls` example | during-turn recursive `find`, mechanism-only correction | ✔ |
| HIGH-2 | PLAN-7 Task 10 Step 4 (L531-546) | non-recursive `ls`, slash-path matcher | `find -maxdepth 4` + own-marker presence + pinned foreign-token grep; mutation-RED walk-through | ✔ |
| HIGH-2 | PLAN-7 Task 2 validator (L132) | "shows no foreign `{project}/{user}/` dir" | recursive scan + own-marker + pinned `grep -nE` foreign-token oracle; old matcher explicitly rejected | ✔ |
| HIGH-2 | PLAN-7 Task 10 success cond (L573) | "own scratch sentinel" + slash matcher | own marker filename + foreign-token absence | ✔ |
| MEDIUM-3 | PLAN-7 Task 2 markers (L132) | `migrate nextseek_api 0007`, `inspect registered`, exit-code lines | `Applying nextseek_api.0007`, `cc_assistant.upload`, `cc_traces`; command-substring requirement removed | ✔ |
| LOW (perms) | PLAN-7 Permissions table | no live-exec row | added live `docker exec`/label-poll row | ✔ |
| LOW (lockfile) | PLAN-7 Task 6 Files (L287) + Step 2 (L317) | "Regenerate lockfile with coordinated bump" / "Pin docker>=7.1.0" | "already present, no version change; regenerate only if other deps change" | ✔ |
| LOW (exit-code) | PLAN-7 Task 2 markers (L132) | "exit-code lines" required substring | removed (format unpinned by PLAN-3) | ✔ |
| LOW (gameability) | PLAN-7 Gameability row 6 (L661) | "foreign `{project}/{user}/` ⇒ fail" | recursive during-turn + allowlist/foreign-token oracle | ✔ |

---

## Cross-target contracts copied verbatim (no invented strings)

- `label=nextseek.cc.run=<run_id>` filter — from `test_cc_realstack.py:93`.
- `cc_engine.py:598-607` `container.stop()`/`container.remove(force=True)` — quoted from source.
- `Applying nextseek_api.0007_ccsessiontranscript` — derived from PLAN-3 `:2073` migrate command's
  real Django migrate stdout form.
- `cc_assistant.upload` — the registered task name from PLAN-3 `:2046` / `:2059` / `:1276`.
- `cc_traces` — the JSON key saved in PLAN-3 Task 13 Step 6 (`:2091`) and defined throughout PLAN-3.
- Mount targets `/data/input`, `/data/scratch`, `/data/shared`, `/home/user/.claude`,
  `/home/user/.cc-memory/transcripts` — from PLAN-7 Task 6 Step 4 (lines 337-343, unchanged).

## Defects unresolved

None. All 2 HIGH + 1 MEDIUM + 4 LOW/cosmetic resolved, except the two cosmetic notes that fall under
the orchestrator-owned Vetting Log / are explicitly "consistent, no defect" (documented above).

## Follow-up suggestions (out of scope — not applied)

- Consider a dedicated long-lived probe container (created outside `run_cc_turn` with the *same*
  `VolumeOptions.Subpath` mounts) as an alternative isolation-scan vector if background-thread polling
  proves flaky under CI timing — the review floated this as an acceptable option. Left to execution.
