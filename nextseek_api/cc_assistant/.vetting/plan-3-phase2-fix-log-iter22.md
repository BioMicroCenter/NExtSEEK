# PLAN-3 Phase-2 hardening fix-log — iter-22 (HARDENER)

Source review: `.vetting/plan-3-phase2-review-22-fresh.md` — CONDITIONAL_ACCEPTANCE (0 CRITICAL, 0 HIGH, 1 MEDIUM, 1 LOW).
Edited ONLY: `PLAN-3-ui-based-io.md`. SPEC-3, PLAN-7, Vetting Log, Phase-2 status line, `defect-lineage.md` untouched.

---

## Finding 1 (MEDIUM) — Task 9b `upload_list` NameErrors: local import omits `ProjectResolutionError`

**Real source verified:** `services/cc_assistant.py` imports `ProjectResolutionError` ONLY locally inside `_run`
(`from …cc_provision import … ProjectResolutionError` at line 243; used at line 249 `except ProjectResolutionError as exc:`).
There is NO module-top `ProjectResolutionError` import; the module-top import is added by **Task 10 Step 1**, which runs
*after* Task 9b. So Task 9b's `except ProjectResolutionError:` (PLAN line 1518) would `NameError` at call time, and the
hermetic suite never exercises the action body (only the pure `list_input_files` helper) — latent until the paid Task 13 gate.

**Fix applied:** Task 9b Step 2 local import changed to mirror Task 9's `upload` action **verbatim**.

Side-by-side (now identical to Task 9):

```
Task 9  `upload`  (PLAN ~lines 1393-1394, UNCHANGED — the oracle):
        from nextseek_api.cc_assistant.cc_provision import (
            resolve_user_project, ProjectResolutionError, build_user_dirs)

Task 9b `upload_list` (PLAN ~lines 1512-1513, AFTER fix):
        from nextseek_api.cc_assistant.cc_provision import (
            resolve_user_project, ProjectResolutionError, build_user_dirs)
```

Before the fix the import read `from …cc_provision import resolve_user_project, build_user_dirs` (no
`ProjectResolutionError`) while line 1518 used it → guaranteed `NameError: name 'ProjectResolutionError' is not defined`.
Added a one-line **self-containment note** under the action paste stating Task 9b must NOT depend on Task 10's
module-top import. Task 9b is now self-contained: every name in its paste (`resolve_user_project`,
`ProjectResolutionError`, `build_user_dirs`, `CCPaths`, `list_input_files`, `Response`) is imported within the action or
(for `Response`) at module top (review §2A confirms `Response` is module-top, line 27). No later-task dependency remains.

**Self-verification:** NameError WOULD occur without the fix (the `except` clause dereferences the name; nothing in scope
defines it pre-Task-10). The two import statements are now byte-identical. Confirmed.

---

## Finding 2 (LOW) — Task 6 Step 5b under-specified the rewrite of the two EXISTING `test_cc_engine_publish.py` functions

**Real source verified:** live `_publish_artifacts` (`cc_engine.py:637`) signature is
`(scratch_mount, output_mount, *, output_host_root, before) -> list[str]` (no `turn_id`); the two existing tests
(`test_publish_artifacts_copies_nested_scratch_changes`, `test_publish_artifacts_skips_symlinks`) call it WITHOUT
`turn_id` and assert the old `list[str]` / `output/<rel>` shape — so both `TypeError`/assert-fail against the reworked
keyword-only-`turn_id` + dict-return + `output/artifacts/<turn_id>/` contract. The `_publish_artifacts` rewrite itself was
already pasted (Step 5, PLAN lines 843-912); the gap was the missing paste of the updated **test bodies** (below the
plan's paste-ready standard).

**Fix applied:** Task 6 Step 5b now pastes the two replacement functions verbatim PLUS a third
`test_publish_artifacts_zips_multiple_and_splits_raw` (covers the >1-deliverable zip branch + the `raw/` split that the
old single-file tests never reached). `test_safe_relpath_rejects_escape_paths` is explicitly left unchanged.

**Contract cross-check against the pasted Step-5 `_publish_artifacts` (traced line-by-line):**

| Test | Input | Asserted return (matches Step-5 code) |
|---|---|---|
| copies_nested | one deliverable `run1/result.txt`, `turn_id="run1"` | single-file branch → `key="run1/run1/result.txt"` (`f"{turn_id}/{dst.relative_to(art_dir)}"`), `label="result.txt"`, `file_format="txt"` (`dst.suffix.lstrip(".")`); on-disk `output/artifacts/run1/run1/result.txt`; `raw=[]`, `raw_zip=None`, `files_created=["run1/result.txt"]`, `files_modified=[]` |
| skips_symlinks | only a symlink | `_snapshot_tree` skips symlinks → `changed` empty → early-return empty dict `{"artifacts":[],"raw":[],"raw_zip":None,"files_created":[],"files_modified":[]}`; no leak; no `output/artifacts` dir |
| zips_multiple_and_splits_raw | `a.txt`,`b.txt` + `raw/debug.log`, `turn_id="run9"` | `len(art_files)>1` → single zip artifact `key="run9/artifacts.zip"`, `file_format="zip"`; on-disk `output/artifacts/run9/artifacts.zip`; raw split (prefix stripped) → `output/raw/debug.log`, `raw=["/host/users/42-px/alice/output/raw/debug.log"]`, `raw_zip=None`; `files_created=["a.txt","b.txt","raw/debug.log"]` |

Each asserted field was derived directly from the Step-5 paste (`partition_changed`, `_copy` with `strip_raw_prefix`, the
`f"{turn_id}/…"` key construction, `_snapshot_tree`'s symlink skip at `cc_engine.py:617-618`). Signatures/return shape and
the `output/artifacts/<turn_id>/` path contract (Global Constraint "Turn-scoped artifacts", PLAN line 29) all align.
The existing coverage command (run both `test_cc_artifacts_split.py` + `test_cc_engine_publish.py`, `--cov-fail-under=95`)
was left intact — gate not lowered; the added zip/raw test only widens `cc_artifacts` coverage.

**Self-verification:** the pasted rewrite's signature (`*, turn_id: str`) and dict return match the plan's contract; the
updated tests assert the new dict shape and `output/artifacts/<turn_id>/` paths. Confirmed.

---

## Finding 3 — remaining cosmetic notes (review §"Non-blocking cosmetic notes")

- `run_cc_turn` docstring still says "scoped Dropbox mounts" — the reviewer themselves note this is **already scheduled**
  by Task 6 Step 6's "Docstring hygiene" item (PLAN line 1023). No plan change required.
- Approximate line anchors (`:580-587`, `~181-212`, etc.) — reviewer spot-checked them against live source and found them
  reliable. No change required.

No cosmetic edits were needed; both notes are already resolved within the plan or confirmed non-issues by the reviewer.

---

## Defects unresolved
None.

## Collateral check
Two surgical edits to `PLAN-3-ui-based-io.md` only (Task 9b import + self-containment note; Task 6 Step 5b test paste).
No other task, gate, command, SPEC-3, PLAN-7, Vetting Log, status line, or lineage file touched. No gate lowered.
Target NOT marked "vetted".
