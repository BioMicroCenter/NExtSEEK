# Phase 2 fresh review (iter 19) — TARGET: PLAN-7-compose-native-prod-deploy.md

Cold-context independent review. Authority: SPEC-7 (locked) > PLAN-7 > task specs. Verified
docker-py 7.1.0 behavior, current `cc_engine.py`/`cc_provision.py` source, PLAN-3 Task 13
producer contract, root/`dmac_assistant` pyproject pins, and `startup/steps/volumes.py` prefix
logic against the live tree.

What checks out (no action): docker-py **7.1.0** is installed; `docker.types.Mount` IS a dict
subclass with no `subpath` kwarg, and `m["VolumeOptions"]={"Subpath": …}` injects a PascalCase
`VolumeOptions.Subpath` into the serialized mount dict (probe confirmed) — Task 6's primary
helper is correct. `dmac_assistant/pyproject.toml` already pins `docker>=7.1.0`; root
`pyproject.toml` path-deps `dmac-assistant = { path = "dmac_assistant" }` — Task 6 claims accurate.
`startup/steps/volumes.py` has `REQUIRED_VOLUMES` + `volume_names_for_prefix(prefix)` — the
validator's prefix oracle is real. PLAN-3 Task 13 Step 8 writes
`nextseek_api/cc_assistant/evidence/3-ui-based-io-live/live_gate_transcript.txt` — path matches
PLAN-7 Task 1 `live_evidence_path` exactly. Current `_run_kwargs`/`_build_volumes` use host-path
`volumes=` dict and the stale "joins … the nextseek compose network" docstring — Task 6's cutover
and docstring-fix targets are real. `DEFAULT_NETWORK = "dmac-cc-net"` confirmed.

---

## 2A — Vet (execution readiness / permissions)

**[LOW] Permissions table omits `docker exec` into the transient agent for the isolation scan.**
Location: "Permissions Required" + Task 10 Step 4 ("`docker exec <agent> ls -la /data/input
/data/scratch`"). The table lists "Docker socket (dev VM / MBP)" generally, which covers it, but
the new REQUIRED isolation artifact needs a *live-container* exec during the turn (see HIGH-1).
Not blocking on its own; folded into HIGH-1's fix.

**[LOW] `docker>=7.1.0` is already the pin; "regenerate lockfile with coordinated bump" is a
no-op.** Location: Task 6 Files ("Keep `docker>=7.1.0` … Regenerate lockfile with coordinated
bump"). Verified `dmac_assistant/pyproject.toml:20` already says `docker>=7.1.0`. There is nothing
to bump; the wording may send an implementer hunting for a version change that isn't required.
Fix: reword to "confirm `docker>=7.1.0` already present; no version change — regenerate lockfile
only if other deps change."

---

## 2B — Stress Test

**[HIGH-1] The REQUIRED `subpath_isolation_scan.txt` is captured by a method the runtime makes
infeasible: the agent container is force-removed when the turn ends.**
Location: Task 10 Step 4 — *"After the forced-CC turn, capture into `subpath_isolation_scan.txt`
a `docker exec <agent> ls -la /data/input /data/scratch`"*; mirrored in SPEC-7 §8
(`subpath_isolation_scan.txt` … "e.g. `docker exec <agent> ls -la`").
Why it is a defect: `cc_engine.run_cc_turn` runs the sibling with `detach=True` and, in its
`finally` block (verified `cc_engine.py:598-607`), calls `container.stop()` then
`container.remove(force=True)` on every exit. The forced turn is driven through
`cc_engine.run_cc_turn(...)` (verified `test_cc_realstack.py:154`). So **after the turn there is
no container to `docker exec` into** — the literal instruction cannot produce the artifact. The
existing harness already works around exactly this: `_capture_agent_env`
(`test_cc_realstack.py:90-104`) *polls for the live sibling by `label=nextseek.cc.run=<run_id>`
during the turn* and inspects it while alive, because the container is transient. A cold-start
implementer following Task 10 Step 4 verbatim will find no container and will either stall or
fabricate the file — defeating the iter-18 CRITICAL gate.
Fix: respecify capture to run **concurrently with the turn**: spawn `run_cc_turn` on a thread,
poll `docker ps -q --filter label=nextseek.cc.run=<run_id>` until the cid appears, then
`docker exec <cid> <listing>` while it is alive (mirror `_capture_agent_env`'s pattern). Update
SPEC-7 §8's `docker exec <agent>` example identically. Optionally allow a dedicated long-lived
probe container created outside `run_cc_turn` with the *same* `VolumeOptions.Subpath` mounts.

**[HIGH-2] The isolation oracle (non-recursive `ls` + a slash-bearing `{project}/{user}/` matcher)
will NOT surface the empty/root-`Subpath` leak it exists to catch.**
Location: Task 10 Step 4 ("`docker exec <agent> ls -la /data/input /data/scratch` … no foreign
`{project}/{user}/` directory or the seeded `SENTINEL_FOREIGN`") and Task 2 validator ("fail …
if `subpath_isolation_scan.txt` … contains any foreign `{project}/{user}/` path").
Why it is a defect: in the *correct* case, `/data/input` is mounted to subpath
`proj/alice/input`, so `ls -la /data/input` lists alice's file contents. In the *leak* case
(`Subpath=""`/`"/"` → whole `dmac-cc-users` root mounted), `ls -la /data/input` (non-recursive)
lists only the **top-level** volume entries — i.e. directory names like `otherproj` and
`personal-…` — NOT a slash-joined two-level `otherproj/bob/` string and NOT the 3-levels-deep
`SENTINEL_FOREIGN` (which lives at `otherproj/bob/input/SENTINEL_FOREIGN`). A validator that greps
for a foreign `{project}/{user}/` **path** (with a slash) therefore matches nothing, and the
seeded sentinel never appears. The single mutation this gate was added (iter-18 CRITICAL) to catch
slips straight through both the human-readable proof and the automated matcher. The gate is
ineffective/gameable as specified.
Fix: (a) make the listing reach the seed depth — use `find /data/input /data/scratch -maxdepth 3`
or `ls -R`; (b) change the oracle to a **positive allowlist** — assert the only project/user
subtree visible under each mount is the forced-CC user's own `{project}/{user}/`, failing if any
*other* top-level project name (e.g. `otherproj`), foreign user name (`bob`), or the
`SENTINEL_FOREIGN` token appears; (c) pin the exact command and the exact grep/parse the validator
runs so a literal implementer cannot under-build it.

**[MEDIUM-3] Hidden dependency: validator "live transcript content markers" are COMMAND-text
substrings PLAN-3's transcript is not contracted to contain.**
Location: Task 2 Step 2 — markers `migrate nextseek_api 0007`, `inspect registered` (the note even
argues `inspect registered` is "a true substring of PLAN-3 Task 13's actual command").
Why it is a defect: PLAN-3 Task 13 Step 8 defines `live_gate_transcript.txt` as *"saved
stdout/stderr + exit codes for every Task 13 command"* — it does **not** promise the command line
itself is echoed. `inspect registered` is the *command*; its *stdout* is a worker→task dict that
need not contain the literal phrase. `migrate nextseek_api 0007` (space form) is the *command*;
the migration's stdout is `Applying nextseek_api.0007_ccsessiontranscript… OK` (dot, module-path
form), which does not contain `migrate nextseek_api 0007`. If the Step-3 implementer saved
outputs only (per the literal PLAN-3 contract), PLAN-7's validator falsely **rejects a legitimate,
already-committed transcript** — and since this is a HARD start-gate, it blocks Step 7 with no
recourse (the transcript SHA is frozen on the branch). Conversely the marker check could be
satisfied trivially if commands are echoed. Either way the cross-target contract is unpinned.
Fix: grep for markers guaranteed to be in *output*: `Applying nextseek_api.0007` (migration
stdout) and `cc_assistant.upload` (the registered-task name that Step 0's
`inspect registered | grep cc_assistant.upload` emits), plus the `cc_traces` JSON key (Step 6
saves it). Alternatively, add a hard requirement to PLAN-3 Task 13 Step 8 that each command line
is echoed into the transcript, and cite that contract here.

**[LOW] "exit-code lines" marker is unpinned.** Location: Task 2 Step 2. PLAN-3 saves exit codes
but the format is unspecified, so "exit-code lines" is a loose match. Tighten to a concrete token
once PLAN-3 Step 8's transcript format is pinned (ties to MEDIUM-3).

---

## 2C — Validate External Dependencies

No dependency-risk findings. docker-py **7.1.0** is the installed/PyPI-latest version; the plan
correctly (a) refuses an unsatisfiable `docker>=7.2.0` pin, (b) uses the raw-dict
`VolumeOptions.Subpath` path as primary, and (c) flags the future `Mount.subpath` kwarg (PR #3270)
as must-verify-at-release. Probe confirmed `Mount` is a dict subclass and post-construction
injection of `VolumeOptions` survives into the serialized payload, so `containers.run(mounts=[…])`
will forward `Subpath` to an Engine ≥26 / API v1.45 daemon. The Engine≥26 + Compose≥2.26 floors
are enforced both as preflight booleans and by independent version-string parsing (good — closes
the "boolean lies" hole). Compose multi-network dual-homing and external-volume bootstrap claims
match the cited Docker docs and `startup/steps/volumes.py`.

---

## 2D — Gameproof

**Cross-user isolation gate (Risk-Register rank 2) — partially gameable.** Quoted success
condition (Task 10): *"`subpath_isolation_scan.txt` proves the sibling agent mounted only the test
user's own `{project}/{user}/` subpath — no foreign user tree visible despite a second user seeded
on `dmac-cc-users`."* Cheapest fake / no-op outcome: per HIGH-2, the empty-`Subpath` mutation
(the exact catastrophe this gate backstops) produces a non-recursive `ls` output containing only
top-level project names, which the slash-pattern matcher does not flag — so the bundle PASSES with
the leak live. Per HIGH-1, because the container is gone after the turn, an implementer can also
simply hand-write a clean-looking `subpath_isolation_scan.txt` (no live container forces the
content). Mutation test result: corrupting `_mount_volume_subpath` to emit `Subpath=""` would NOT
turn this gate red as specified → unguarded. Remedy = HIGH-1 (capture from a live container during
the turn) + HIGH-2 (recursive listing + positive-allowlist oracle + pinned grep).

**Hermetic Subpath-value assertions (Task 6 Step 1/2) — adequately gameproofed.** The concrete
per-user value assertions (`"proj/alice/input"` etc.) plus the anti-empty/anti-constant negative
control DO kill the empty-`Subpath` mutation at the hermetic layer, and I verified the expected
values match `build_user_dirs`' relative layout (`{project}/{user}/input|scratch`,
`cc-state/{session}`, `_memory/{session}` → transcripts). The remaining hole is purely the *live*
gate (HIGH-1/HIGH-2); the hermetic gate is sound. Note: `shared_src` is project-level
(`{project}/shared`, verified `cc_provision.py:101`), intentionally cross-user within a project, so
its omission from the isolation assertion is correct, not a gap.

**Other gates (Tasks 1,2,5,7,8,9) — gameproofing holds.** Generated-evidence-only + committed
`live_gate_transcript.txt` + `step3_deploy_gate` close the stale-state and Markdown-only fakes;
`docker compose config` subprocess (not golden fixture) closes the never-re-run fake; DEPLOY
full-file `/srv/dmac/users` + `mkdir|chmod` scan closes the appendix-dodge. Only MEDIUM-3's marker
fragility weakens the transcript oracle.

---

## Non-blocking cosmetic notes

- Vetting log row numbering skips (rows 26, 28 absent) — bookkeeping only.
- Task 6 Files says "Regenerate lockfile with coordinated bump" though `docker>=7.1.0` is already
  pinned (see 2A LOW) — wording, not logic.
- SPEC-7 line 185 / Task 2 both restate the `host_label` enum; redundant but consistent.
