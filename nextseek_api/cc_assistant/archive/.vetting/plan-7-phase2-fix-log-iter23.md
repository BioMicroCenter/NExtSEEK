# PLAN-7 Phase 2 — Hardener fix-log (iter-23)

Role: HARDENER (not a reviewer). Source review: `.vetting/plan-7-phase2-review-23-fresh.md`
(0 HIGH, 3 MEDIUM [one root cause = thread F: hermetic Subpath-value net incomplete], 3 LOW + cosmetics).
Target edited: `PLAN-7-compose-native-prod-deploy.md`. SPEC-7 §8: **not edited** (already covers the
live `/data/shared` find root; no hermetic enumeration lives there — see "SPEC-7 §8" below).
Out-of-scope per instructions: PLAN-3, Vetting Log table, Phase 2 status line, `defect-lineage.md`.

## Canonical source re-read VERBATIM (cc_provision.py `build_user_dirs` / `UserDirs`)

`build_user_dirs` (cc_provision.py:79–109) computes, with `mount_root = paths.user_root_mount`
(`/dmac/users`), `project_mount = {mount_root}/{project_dirname}`, `user_mount = {project_mount}/{user_id}`:

- `input_src   = f"{user_host}/input"`            (L100) — **host path; there is NO `input_mnt`**
- `shared_src  = f"{project_host}/shared"`        (L101) — **PROJECT-scoped (no `{user_id}`); NO `shared_mnt`**
- `scratch_src = f"{user_host}/scratch"`          (L102)
- `scratch_mnt = f"{user_mount}/scratch"`         (L105)
- `cc_state_mnt= f"{user_mount}/cc-state/{session_id}"` (L107)
- `memory_mnt  = f"{user_mount}/_memory/{session_id}"`  (L108) — transcripts is a `/transcripts` CHILD

Spawn set = current `_build_volumes` (cc_engine.py:380–394): input(ro `/data/input`),
shared(ro `/data/shared`), scratch(rw `/data/scratch`), cc-state(rw `/home/user/.claude`, conditional),
`user_memory_file`(ro, conditional — **DROPPED** post-G7-10 by Step 4 point 3), transcripts(ro
`/home/user/.cc-memory/transcripts`, conditional). `output` is a **publish target** (`path_mappings`),
**not** a bind in the spawn set. Post-cutover spawn set = **5 mounts**: input, shared, scratch, cc-state, transcripts.

## Per-mount verification table (spawn set == enumerated set; no mount un-enumerated)

| Mount | real `build_user_dirs` source + path produced | enumerated `Subpath` (volume-relative tail) | per-mount assertion + anti-empty control |
|---|---|---|---|
| input | `input_src` (L100) `{user}/input` → vol `/dmac/users/proj/alice/input` | `proj/alice/input` | `== "proj/alice/input"`; `""`/`"/"`/root/const FAIL |
| shared | `shared_src` (L101) `{project}/shared` → vol `/dmac/users/proj/shared` (**no user seg**) | `proj/shared` | `== "proj/shared"`; `""`/`"proj"`/`"/"`/const FAIL |
| scratch | `scratch_mnt` (L105) `/dmac/users/proj/alice/scratch` | `proj/alice/scratch` | `== "proj/alice/scratch"`; `""`/`"/"`/root/const FAIL |
| cc-state | `cc_state_mnt` (L107) `/dmac/users/proj/alice/cc-state/sess` | `proj/alice/cc-state/sess` | `== "proj/alice/cc-state/sess"`; `""`/`"/"`/root/const FAIL |
| transcripts | `memory_mnt` (L108) `/dmac/users/proj/alice/_memory/sess` **+ `/transcripts`** | `proj/alice/_memory/sess/transcripts` | `== "proj/alice/_memory/sess/transcripts"`; `""`/`"/"`/root/const FAIL |

Enumerated set {input, shared, scratch, cc-state, transcripts} == post-cutover spawn set. **No mount un-enumerated.**
Legit `shared` value `proj/shared` **PASSES** the per-mount control (exact equality to its own expected value);
`Subpath=""` (or `"proj"`) on shared **FAILS** it. The old blanket `{project}/{user}/`-prefix rule is removed
(it would have falsely failed `proj/shared`).

## Defects fixed

1. **MEDIUM (thread F core) — `shared` absent from enumeration + blanket control misclassifies it.**
   PLAN-7 Step 1 (L304–312): retitled "Per-mount Subpath VALUE isolation"; enumerated **all five** spawn-set
   mounts including `shared → "proj/shared"`; replaced the blanket `{project}/{user}/`-prefix negative control
   with a **per-mount exact-equality** assertion + a **per-mount anti-empty/anti-root/anti-constant** control,
   explicitly noting the legit project-scoped `shared` passes while `Subpath=""` on shared fails the *hermetic*
   suite (not only the live `find /data/shared` gate). **FIXED.**

2. **MEDIUM — wrong strip-`*_mnt` derivation (input has no `input_mnt`; transcripts = `memory_mnt`+`/transcripts`).**
   PLAN-7 Step 1 (L304) replaced the single strip-prefix rule with the concrete per-source layout read from real
   source (no `input_mnt`/`shared_mnt`; `shared` project-scoped; transcripts = `_memory/<session>` tail + `/transcripts`;
   tails supplied post-G7-10 by `*_subpath` fields). Step 2 "Subpath derivation" (L332) rewritten the same way
   (dropped "strip `user_root_mount` from `*_mnt`"; states per-mount enumerated values incl. shared/transcripts).
   Step 2 hermetic-test line (L334) reworded to per-mount equality with a shared example. **FIXED.**

3. **MEDIUM (2D, same root) — cheapest hermetic fake = mutate un-enumerated `shared` Subpath.**
   Closed by fix 1: `shared` is now enumerated with a per-mount anti-empty control, so a `dirs.shared`
   `Subpath=""`/constant mutation goes RED in the **hermetic** suite. **FIXED.**

   Consistency: **Global Constraints L32** repeated the same stale 4-mount enumeration + blanket-prefix rule
   (would have re-introduced the defect / contradicted Step 1). Updated to enumerate all five (incl.
   `shared → "proj/shared"`, flagged project-scoped) with the per-mount anti-empty control and an explicit note
   that the blanket prefix rule is wrong. **FIXED.**

4. **LOW / cosmetic notes.**
   - 2B-LOW (Subpath-target precondition / transcripts child): Step 4 point 6 (L352) strengthened to require
     mkdir of the **`_memory/<session>/transcripts` child specifically, not just `_memory/<session>`**, noting
     Engine `VolumeOptions.Subpath` fails container start if the exact subdir is absent. **FIXED.**
   - 2A-LOW (`cc_config` "measured 100%" predates the refactor): L31 annotated "**measured 100% pre-refactor**
     … read as the current baseline, not a post-refactor guarantee; the `--cov-fail-under=95` floor stays
     commit-blocking." **FIXED.**
   - 2A-LOW (permissions table complete): reviewer recorded **NO defect** — nothing to change.
   - Cosmetic (Vetting Log iteration jumps 25→27→29): in the **Vetting Log table** — **out of scope**
     (instructed not to edit it). Left unchanged.
   - Cosmetic (Global Constraints cites `cc_engine.py:398–607` "off-by-two"): re-checked real source —
     `run_cc_turn` is `def` at 398, last statement `pass` at **607** (lines 608–609 blank, next `def _snapshot_tree`
     at 610). The plan's `398–607` is the **accurate code span**; changing it to 609 would introduce an error.
     Left unchanged (documented here per "claim ↔ reality"; the reviewer's "~609" counts trailing blanks).
   - Cosmetic (transcripts "= `*_mnt`" duplication): resolved by the Step 1/Step 2/Global-Constraints rewrites
     above — all three now use the concrete enumerated value.

## SPEC-7 §8

Not edited. §8 (SPEC-7-compose-native-prod-deploy.md:194–195) governs the **live** gate artifacts and already
lists `/data/shared` in the `subpath_isolation_scan.txt` find roots (`find /data/input /data/scratch /data/shared …`).
The hermetic per-mount enumeration is a PLAN-7 concern; no §8 change is required and none was made (per authority
SPEC > plan, and the "additive/strengthening only" rule — nothing to add).

## Self-verification against real source

- Re-Read edited Step 1 (L303–317) after edits: five mounts enumerated, per-mount control present, shared correct.
- Built the per-mount table above: every spawn-set mount's `build_user_dirs` attribute + produced path quoted
  verbatim from cc_provision.py; each enumerated `Subpath` matches the real path's volume-relative tail.
- Confirmed enumerated set == spawn set (`_build_volumes` cc_engine.py:380–394), `user_memory_file` dropped,
  `output` correctly excluded (publish target, not bind).
- Confirmed legit `shared` (`proj/shared`) PASSES the per-mount control and `Subpath=""` on shared FAILS it.
- Confirmed the wrong strip-prefix shorthand is removed in Step 1, Step 2 derivation, Step 2 hermetic test, and
  Global Constraints L32; no enumerated value contradicts real source.
- SPEC-7 §8 not edited (not required).

## Defects unresolved

None. (Two cosmetics intentionally left unchanged with documented justification: the Vetting-Log
iteration-number gap is out of scope; the `398–607` citation is already accurate and changing it would be wrong.)
