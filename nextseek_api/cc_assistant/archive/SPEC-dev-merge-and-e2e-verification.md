# SPEC — Merge `feat/dmac-assistant-full-integration` → `origin/dev`, + full-UI E2E verification

| | |
|---|---|
| **Status** | DESIGN v2 — review-hardened (2026-07-07 evening); **re-pinned 2026-07-08** to feat tip `d3837a6` (was `6c52ab6`) after the BUGFIX-0707 R1-R8 merge; **evidence-binding hardened 2026-07-09** after Codex plan-vetting. Execution gated on the plan (writing-plans) + explicit user approval for the paid E2E. |
| **Branches** | feat `d3837a6` (= origin/feat; was `6c52ab6`, advanced +10 commits via BUGFIX-0707) → dev `origin/dev` @ `c5f23e7`; merge-base `935f5fa`. **Re-pin rule:** re-fetch `origin/dev` and re-`rev-parse` feat/dev/merge-base immediately before the merge; STOP if `origin/dev` advanced past `c5f23e7` or feat past `d3837a6`. |
| **v2 provenance** | v1 (feat `f093325`) + both 2026-07-07 vetting reviews (`DEV-MERGE-E2E-ADVERSARIAL-RISK-REVIEW`, `DEV-MERGE-E2E-GAMEABILITY-REVIEW`) with **every material claim code-verified** (workflows wf_7421bf33/wf_03d39f04/wf_85585547), + 4 user MCQ decisions (§10). |
| **Location note** | Lives in `work/state/` (shared clone is active territory; `docs/superpowers/` gitignored). Move byte-identical (cmp-proof) into `nextseek_api/cc_assistant/` and commit from the isolated merge worktree. |

---

## 0. What changed since v1 (all verified)

1. **All three former blockers are committed and pushed** — origin/feat = `6c52ab6`:
   `5e583a3` (installer: CC stack wired into `./startup.sh install`), `41bfac9` ((b) `NEXTSEEK_INTERNAL_BASE_URL` split), `6c52ab6` ((c) 0007/0008 FK charset heal). Blockers (b)/(c) are **cleared on feat** but must be **re-proven on the merged candidate** (§8).
2. **Merge surface re-verified at `6c52ab6`**: same **6 conflicts** as v1; both-sides-changed set is now **25** (adds `startup/README.md`, `startup/steps/config.py`, `startup/templates/nextseek.env.template`, `startup/tests/test_config.py` — all auto-merge). Merge-base unchanged (`935f5fa`). **(Re-pin 2026-07-08:** at the current feat tip `d3837a6` the surface is **7 conflicts** — BUGFIX-0707 R2 collectstatic fail-fast (`408b5e0`) now collides with dev's `/media` mkdir insert (`9a7c587`) in `docker/scripts/entrypoint.sh`; both-sides set remains 25, byte-identical. See §3/§4.)
3. **Installer fix verified sound but carries 3 confirmed defects** (D1–D3, §5.0) — user decided they are fixed **in this plan, pre-merge**.
4. **v1 §4 defect corrected**: the `SEEK_PUBLIC_URL` resolution as previously written would NameError on native env-less hosts (see §4).
5. `uv.lock` at HEAD is **stale** (committed lock lacks orjson/zstandard required by committed `pyproject.toml`); the catch-up sits uncommitted in the shared clone — fold into the pre-merge wave (§5.0).

## 1. Goal & non-goals

**Goal.** A reviewed, surgical merge of feat into `dev` that (a) adopts dev's newer `chat_nextseek` (NF-core `pipeline_agent` refactor, `report_coder`) **without discarding any feat change**, (b) fixes the three installer defects pre-merge, and (c) is proven by hermetic lanes + a **machine-validated** paid full-UI E2E on a **non-8000 second instance**, as the last step before the `dev`→prod push (tracker steps 6–7).

**Non-goals.** Off-server (laptop) greenfield 7d re-run — remains a separate later gate. nextseek_api/cc_assistant/DEPLOY.md rewrite (tracker 7c) unless trivially folded in. **(Correction 2026-07-08:** entrypoint migrate/collectstatic fail-fast hardening is NO LONGER a non-goal — it **landed on feat** (FU4 migrate fail-fast at `9c7ab66`; R2 collectstatic fail-fast + R3 DB-readiness probe in BUGFIX-0707 at `d3837a6`) and is now a shipped feat change that MUST be preserved through the merge. Do not accept dev's bare `collectstatic`/`migrate` lines — see the §4 `docker/scripts/entrypoint.sh` row and survival assertion S10.)

---

## 2. Merge model & strategy (unchanged, evidence-refreshed)

Plain three-way `git merge` + surgical conflict resolution — **not** "take dev's tree":

- Dev changed **nothing** under `nextseek_api/` or `docker/ns-sidecar/` (re-verified at tip).
- `portable.py` is unchanged on dev (dev blob == merge-base); feat adds `generate_report_outputs`, which the plain merge auto-adopts — a forced take-dev would revert it → ImportError in `_generate_submission`.
- **New landmine (verified)**: dev's `config.py:353` is the raw `os.getenv("NEXTSEEK_BASE_URL")` read. The plain merge auto-preserves feat's `_resolve_nextseek_base_url` (HEAD `config.py:17` def, applied at `:457` — re-verified at `d3837a6`) because the hunks are disjoint — but any take-dev resolution of `config.py` **silently reverts the (b) fix**. §6 adds a static survival assertion.
- Even inside `chat_nextseek`, feat's F821 import fixes (`parser.py`, `planner/tools.py`) are called bare by dev's surviving code.

**Resolution rule (user-mandated, unchanged).** `dev`-authoritative is scoped to **`chat_nextseek/` only**, and only as a tiebreaker for genuine conflicts (`wizard.py`). Everything else: preserve feat, adopting dev hunks only for genuine improvements. Per-hunk, dates inform, code decides. **Never discard a feat change trivially.**
Dev-side deletions of chat_nextseek files feat never touched (feat `pipeline/{builder_tools,tools,wizard}.py`, 5 wizard test files, `prompts/wizard_agent.txt`, feat's 10 `test_pipeline_agent_*` vs dev's suite) auto-apply — correct under the rule; the merge-resolution ledger (§6.7) records each.

## 3. Blast radius (re-verified at `d3837a6`)

`git merge-tree --write-tree d3837a6 origin/dev`: **7 conflicts**, **25 both-sides-changed** (rest auto-merge), plus feat-only clean adds. The 7th conflict vs v1's six is **`docker/scripts/entrypoint.sh`** (new at `d3837a6`: BUGFIX-0707 R2 collectstatic fail-fast `408b5e0` collides with dev's `9a7c587` `/media` mkdir — resolved as a per-hunk union, §4). Ops (`granular.py`, `portable.py`, `ns-sidecar/*`), migrations, and all five installer-fix core files are **not** conflicted (`startup/steps/{build,validate}.py` are feat-only; `cli.py`/`config.py` auto-merge). **Amendment 2026-07-08 (post-Phase-A): the Phase A pre-merge wave turned `startup/lib/docker_ops.py` from an auto-merge into a REAL content conflict (Task 2's `compose_ps_running` vs dev's `compose_port`), so at the pushed feat tip `c848478` the surface is 8 conflicts (adds `startup/lib/docker_ops.py`); both-sides still 25 — resolved take-feat, see the docker_ops.py row in §4 + assertion S11.**

---

## 4. Per-file resolution matrix (v2 — corrections **bold**)

**HIGH risk — operator reviews each hunk personally at merge time (per-change sign-off, OI-3 surface):**

| File | Resolution | Decision |
|---|---|---|
| `dmac/settings.py` | merge-both | Keep feat's env guards + `cc_assistant` app registration (`:170`) verbatim. Add dev's SEEK_PUBLIC_URL **with a guard-safe fallback** — v1's "unconditional line after the guard" is WRONG (NameError: `SEEK_URL` exists only inside feat's `SEEK_HOST` guard; empirically proven). **Correct form:** place *inside* the `if os.getenv("SEEK_HOST"):` guard as `SEEK_PUBLIC_URL = os.getenv("SEEK_PUBLIC_URL", SEEK_URL)` **plus** an unconditional pre-guard default `SEEK_PUBLIC_URL = os.getenv("SEEK_PUBLIC_URL", "")` so the attribute always exists (native hosts overriding via local_settings.py, exec'd at ~`:240`, keep working; dev's 4 unguarded consumers — `seek/views.py:483,1721,1732`, `seek/dbtable_sample.py:1683` — never AttributeError). Post-merge gate: §6.4. |
| `docker-compose.yml` | merge-both | Keep feat's CC/OI-3 topology verbatim, **including the `networks.dmac-cc-net.name: dmac-cc-net` pin** (defeats COMPOSE_PROJECT_NAME renaming — verified load-bearing). Apply dev's `${INSTANCE_PREFIX:-}` to exactly the **6 `container_name`** sites (nextseek, seek-mysql, neo4j, seek, seek-workers, seek-solr) **and 6 `volumes.*.name`** sites (seek-filestore, seek-mysql-db, seek-solr-data, seek-cache, nextseek-static-files, neo4j-data) — 12 sites, **never** the CC identities (`dmac-cc-net`, `nextseek-sidecar`, `dmac-bedrock-proxy`, `dmac-cc-users` — the acceptance validator matches container names by exact literal equality and `cc_engine.py:205` hardcodes `nextseek_nginx`), and never attach core services to `dmac-cc-net`. Dev's `nextseek_nginx` has no `container_name` — keep it that way. |

**MEDIUM risk — compiled frontend, never hand-merge:**

| File | Resolution | Decision |
|---|---|---|
| `.vite/manifest.json` + `assets/main.embedded-*.js` (+css) | rebuild-from-source | Merge frontend **source**, then `npm run build:embedded` (`tsc -b --noEmit && vite build --config vite.config.embedded.ts` — config byte-identical both sides). Explicit `emptyOutDir:true` purges the 4 lingering hashed bundles the naive merge leaves. **Then restart gunicorn** — `vite_assets` caches the manifest in-process when `DEBUG=False`. §6.6 asserts manifest↔assets consistency. |

**LOW risk — verified safe (plain merge keeps both intents unless noted):**

| File | Resolution | Note |
|---|---|---|
| `.gitignore` | union | Feat's bare `CLAUDE.md` rule + `!docker/cc-runtime/container/CLAUDE.md` negation + `docker/bedrock-proxy/proxy-secret.env` + `dmac_assistant/src/dmac_assistant/router/baml_client/` (root `.env` is base-common, not a resolution decision); dev's `/filestore/` + seed tarball + whitespace trim. |
| `agents/wizard.py` | take-dev (delete) | Zero importers remain on dev (every 'wizard' hit is a comment/string/fixture — verified). Feat-side removal surface: `agents/__init__.py:30-35`, `orchestrator.py:18` (`_execute_nfcore_wizard`), `planner/tools.py:407` (lazy), `schemas/__init__.py:17,74`, `config.py:187` prompt load, 5 test files — all resolved by dev's tree; ledger records each. |
| `chat_nextseek/.../config.py` | merge-both | Auto-merges. **MUST retain feat's `_resolve_nextseek_base_url` (def `:17`, applied at `:457` — re-verified at `d3837a6`) + `chat_nextseek/tests/test_config_base_url.py`** (dev lacks the resolver — take-dev reverts fix (b)). Also feat's dynamic DB maps + dev's `REPORT_CODER_SYSTEM_PROMPT`/wizard-prompt removal. |
| `agents/parser.py`, `planner/tools.py`, `reports/outputs.py`, `seqera/emitter.py`, `MessageInput.tsx`, `seek/views.py`, `seek/dbtable_sample.py`, `startup/cli.py`, `startup/steps/config.py`, `startup/README.md`, `startup/templates/nextseek.env.template`, `startup/tests/test_config.py`, `startup/steps/seed.py`, `startup/tests/test_seed.py`, `startup/pyproject.toml`, `startup/uv.lock` | merge-both | All verified disjoint/auto-merging (v1 notes stand; startup files gained feat-side installer+(b) hunks, dev-side filestore/SEEK_PUBLIC_URL/UNWIND hunks — non-overlapping). The four `startup/{steps/seed.py, tests/test_seed.py, pyproject.toml, uv.lock}` became both-sides via the byte-identical `a666761`/`4fd5b66` UNWIND cherry-pick (present at the SPEC pin; trivial auto-merge). **NOTE (2026-07-08): `docker/scripts/entrypoint.sh` was removed from this row — at `d3837a6` it is a REAL content conflict; see the dedicated entrypoint row below. NOTE (2026-07-08, post-Phase-A): `startup/lib/docker_ops.py` was ALSO removed from this row — the Phase A wave (Task 2 added `compose_ps_running`) makes it a REAL content conflict; see the docker_ops.py row below.** |

**CONFLICT — new at `d3837a6` (BUGFIX-0707); per-hunk union, no logic rewrite (user-confirmed 2026-07-08):**

| File | Resolution | Decision |
|---|---|---|
| `docker/scripts/entrypoint.sh` | merge-both — **REAL content conflict** at `d3837a6` (NOT auto-merge) | **Union, top-of-file.** Both sides edit the region around the first `collectstatic` line: feat R2 (`408b5e0`) wrapped `collectstatic` in a fail-fast guard; dev (`9a7c587`) inserted a `/media` mkdir block. Resolve, **in order**: (1) **dev's** `mkdir -p /media/download /media/uploads /media/uploads/production /media/dropbox /media/reserved` block (+ its comment) immediately after the shebang; (2) **feat's** R2 collectstatic fail-fast (`uv run manage.py collectstatic --noinput \|\| { … [COLLECTSTATIC-FAILED] … exit 1; }`); (3) **feat's** R3 bounded DB-readiness probe (`DB_WAIT_ATTEMPTS` / `[DB-UNREACHABLE]`); (4) **feat's** FU4 migrate fail-fast (`… migrate --noinput \|\| { … [MIGRATE-FAILED] … exit 1; }`); (5) **feat's** `NEXTSEEK_SERVER` daphne\|gunicorn toggle + celery worker (feat-only, auto-merge). **Net:** resolved file = feat's `entrypoint.sh` with dev's `/media` mkdir spliced in before `collectstatic`. `git checkout --ours/--theirs` FORBIDDEN. Covered by survival assertion **S10** (§6.5) + ledger row (§6.7); regressing either dev's `/media` fix or feat's R2/R3/FU4 markers = fail. |
| `startup/lib/docker_ops.py` | merge-both — **REAL content conflict** after the Phase A wave (NOT auto-merge) | **Take feat's file (verified strict superset).** Phase A Task 2 appended `compose_ps_running`; dev independently added `compose_port` to the same region (after `compose_exec`) → overlapping-insertion conflict. Ground truth (`git diff origin/dev c848478 -- startup/lib/docker_ops.py`): feat is a strict functional superset — `compose_port` is **byte-identical** on both sides; feat additionally has the `force_recreate` param, `compose_build`, `compose_ps_running` (Task 2), `bootstrap_staging_dir`; the only dev-side line not already in feat is feat's own improved `compose_up` docstring. Accept feat's version (result = byte-identical to feat's file): preserves every feat change AND loses nothing from dev (consistent with "preserve feat outside `chat_nextseek/`"). `git checkout --theirs` FORBIDDEN. Covered by **S11** (§6.5) + ledger row (§6.7); a take-dev resolution would drop `compose_ps_running`/`compose_build`/`bootstrap_staging_dir` and fail S11. |

## 5. Execution procedure

### 5.0 Pre-merge wave on feat (NEW — user decision)

1. **Installer defect fixes (TDD, on feat):**
   - **D1**: token-less install must not end silently "Ready" with a broken proxy — render-time loud warning + a post-`start_cc_stack` health probe of the proxy (`/healthz` + a token-presence check or invoke-probe that distinguishes 500 `proxy misconfigured`), surfaced in the install summary.
   - **D2**: `render_proxy_secret_env` must **preserve an existing non-empty token** when the parent env has none (write-if-absent/merge semantics), so re-install never clobbers a hand-filled secret.
   - **D3**: write `proxy-secret.env` mode **600**.
   - Harden the substring-grep CLI wiring tests into behavioral ones (they currently pass with the call commented out — proven).
   - Noted follow-ups (not this wave): `DJANGO_SECRET_KEY` rotation-driven convergence fragility; nginx double-bounce; `--skip-cc` escape hatch.
2. **`uv.lock` catch-up**: commit the lock regeneration (orjson/zstandard) so `uv lock --check` passes at the merge base; coordinate with the parallel session that authored it (it's their uncommitted file).
3. Re-run `git merge-tree` at the resulting feat tip; refresh §3/§4 if the surface moved.

### 5.1–5.4 Merge (as v1, re-pinned)

1. **Isolated worktree** off feat at the post-wave tip (record `git rev-parse HEAD` + empty `git status --porcelain` **before** merging; branch `merge/dev-into-feat`). Never the shared clone.
2. `git merge origin/dev` → resolve strictly per §4; operator personally signs off the 2 HIGH-risk files' hunks. Maintain the **merge-resolution ledger** (§6.7) as you go.
3. Frontend: resolve source → `npm run build:embedded` → commit regenerated manifest+assets.
4. `uv lock --check` in **both** root and `docker/cc-runtime` (record cwd, exit code, lock hash before/after). Commit; secret-scan the diff range before any push.

## 6. Post-merge free verification (pre-E2E gate; full-adoption hardening)

1. **Lane A — container behavioral**: rebuild `nextseek` from the merged tree; `docker exec -w /app nextseek /app/.venv/bin/python -m pytest -m "not host_only"`. `host_only` marker governed by a **reviewed allowlist** committed with the merge (checkout-only tests exactly: the 4 source-tree hygiene tests + startup-template render tests + build_tools tests); artifact records image ID, collection/skip/deselect counts.
2. **Lane B — checkout hygiene**: the 4 source-tree tests against the merged **checkout** from the isolated worktree (or `git archive HEAD` extract); artifact records `git status --short` + collected node IDs.
3. **Migration health (clean-seed, not boot-green)**: on a **fresh DB volume seeded from committed dumps** (the §7 second instance provides this): `manage.py migrate --noinput` exits 0; `django_migrations` has the `0007` row; FK present; `information_schema` shows child charset/collation == parent; second `migrate` idempotent ("No migrations to apply"); negative grep for 3780/1050 in logs. SQL evidence bundle to the run dir.
4. **Settings gate**: clean-env import (only `LOG_DIR` set — import-time `os.makedirs`) of merged `dmac.settings` succeeds **and** `hasattr(settings, "SEEK_PUBLIC_URL")` is true (bare import success is NOT sufficient — proven).
5. **Static survival assertions** (script, exit non-zero): `portable.py` exports `generate_report_outputs`; `parser.py`/`planner/tools.py` F821 imports present; `config.py` `_resolve_nextseek_base_url` present + used; settings has `cc_assistant` app + guard-safe `SEEK_PUBLIC_URL`; compose preserves literal CC identities + the `dmac-cc-net` name pin, and INSTANCE_PREFIX appears on exactly the 12 sanctioned sites; `orchestrator.py` routes NFCORE → `pipeline_agent.start`; zero wizard imports remain; **(S10, new 2026-07-08)** merged `docker/scripts/entrypoint.sh` unions dev's `/media` mkdir with feat's R2/R3/FU4 boot-hardening — assert BOTH `mkdir -p /media/download` (dev) AND all three markers `[COLLECTSTATIC-FAILED]`, `[DB-UNREACHABLE]`, `[MIGRATE-FAILED]` (feat) AND that the guards are wired not reverted to bare (`collectstatic --noinput || {` and `migrate --noinput || {` both present), so a take-theirs resolution fails; **(S11, new 2026-07-08 post-Phase-A)** the merged `startup/lib/docker_ops.py` retains ALL of feat's `compose_ps_running`, `compose_port`, `compose_build`, `bootstrap_staging_dir` (a take-dev resolution would drop `compose_ps_running`/`compose_build`/`bootstrap_staging_dir` and fail).
6. **Ops smoke — real invocation, not import**: fixture-level calls of each granular op adapter against structurally-real contexts; `_generate_submission` must call the merged `generate_report_outputs` and produce a nonzero-byte workbook with a downloadable session-scoped path; print `chat_nextseek.__file__` proving editable-install parity. Frontend: parse the full Vite manifest, verify every referenced js/css/asset exists, verify the assets directory contains no stale hashed siblings, and later curl the served asset URL on instance 2 after gunicorn restart.
7. **Merge-resolution ledger**: one row per conflict (now **8**, incl. `docker/scripts/entrypoint.sh` + `startup/lib/docker_ops.py`) + per both-sides file (25): resolution rule applied, commands, and which §6.5 assertion covers it. Reviewer (operator) sign-off tied to the ledger.
8. **Rebuild all 4 images** from the merged tree (record commit SHA, context, Dockerfile, image ID per image; at least the cc-runtime image also built from a `git archive` extract to protect the standalone-build invariant); re-inspect tags after provenance capture and fail if any `:devmerge` tag no longer resolves to the recorded image ID. **Secret re-scan** each image with structured per-image/per-category JSON: filename hits, value hits (via mounted file, never `-e`), key-name/entropy hits, and `Config.Env`; record image ID, export member count, scanned byte count, command, exit code, and allowlist decisions. A green free-form PASS file is not proof.

## 7. Live E2E (paid, approval-gated; **second instance on the dev VM** — user decision)

**Environment**: `./startup.sh install` a second, port-bumped instance (auto-bump gives non-8000) on this VM from the merged candidate — this single stack exercises the installer fix end-to-end, clean-seed migrations (§6.3 evidence), and un-masks (b) by construction. Torn down after evidence capture. Record: host URL, compose project/env-file hash, container IDs/image IDs, container `NEXTSEEK_BASE_URL`/`NEXTSEEK_INTERNAL_BASE_URL`, in-container probe showing the internal route succeeds while the host-port loopback is not used, and a real app-level self-call proof bound to the instance-2 container IDs. All `docker compose` commands against instance 2 must consume the recorded project/env-file identity and abort if current container IDs differ from `identity.json`.

**Mechanism**: Playwright driving the real chat UI + real OI-4 router, building on the in-repo page objects (`chat_nextseek/e2e/playwright/`) and `work/state/pw/` drivers. The runner must be explicitly bound to the approval artifact's `base_url`; constructing its target from ambient `NEXTSEEK_*` env is forbidden because it can validate live/stale instances. **Bar**: 1 clean full pass, $15 hard cap (`STEP7_PER_OP_TOTAL_BUDGET_USD`), per-turn `NEXTSEEK_CC_MAX_BUDGET_USD`, real per-turn `cost_usd` from `extra_state.cc_traces[*]` (the CC result frame `total_cost_usd`) — **not** a `cost_source` field (see the Task 12 F2 fix) — never guesstimates.

**Oracles (full adoption)**: every question gets a **machine-checkable validator wired to the existing `chat_nextseek/e2e/criteria.py` DSL / `catalog.json`** (pass_criteria on answer content + artifacts), with forbidden-phrase failure ("backend unreachable", empty result, "I cannot access"); weak validators such as `nonempty` alone or broad `contains` without ground truth are rejected by the approval linter for the required families. The harness `all_pass` verdict is explicitly insufficient (verified: only empty answers/tool-errors red). Immutable run-dir: Playwright trace, transcripts, per-op verdict JSON, cost ledger, summary, actual browser URL, DB identity, and instance-2 identity hash. A rerunnable validator command exits non-zero on any failure and must recompute from raw artifacts/DB rows, not trust `summary.json`.

**Question set (A–D)** as v1, hardened: A) 8 ops with ground-truth predicates; api-write must **reach** the L3 gate (trace shows classification + confirmation-required) with pre/post DB state unchanged and no "yes" sent. B) NF-core **multi-turn** via `pipeline_agent` (state carryover in `ChatSession.extra_state` — verified mechanism), pinned ENA-resolvable UIDs at run-prep, final samplesheet validated. C) Memory with a **per-run nonce** (not fixed BANANA), transcript-append evidence for 1b, fresh-ChatSession + store/recall artifacts for 1c. D) File I/O with nonce-carrying upload proven ingested, workbook fetched + parsed + session-ownership asserted, negative cross-session download test.

**Approval artifact**: exact prompts, per-prompt validators, pinned UIDs, cap, target URL/port — frozen pre-approval; the runner consumes the artifact and fails on drift. The pre-spend linter must run read-only DB/catalog probes for pinned counts/UIDs, embed probe output hashes, require fresh nonce metadata + pre-run absence checks, and reject missing negative validators. Shown to the user before any spend (standing rule: no paid runs without explicit per-run approval).

## 8. Gates before prod push

- **(b)/(c) clearance on the FINAL candidate** (not just feat): §6.3 migration evidence + §7 non-8000 functional pass on the merged SHA/images. Cleared-on-feat commits: `41bfac9`, `6c52ab6`.
- **DoD manifest** (full adoption): generated post-verification — merged SHA, parent SHAs, image IDs, artifact paths + sha256 + producer command/exit code (tests, migration, E2E run dir, cost ledger, ledger, scans), blocker-fix SHAs, second-instance identity, approval hash, and rollback snapshot hash. A `verify_prod_readiness_manifest` command re-checks everything and exits non-zero on gaps: it must invoke E2E `--validate-only`, re-inspect image tags, parse structured lane/secret/DB artifacts, recompute dump checksums, and fail on placeholder/free-form artifacts.
- Doc drift folded in or explicitly deferred: 9-vs-8 op reconciliation (SPEC-7 §8/PLAN-7 Task 15 vs `BIN_OPS`=8 + stale 'nine' docstrings), nextseek_api/cc_assistant/DEPLOY.md rewrite (7c).

## 9. Rollback & safety

- **Images**: tag all 4 `:pre-devmerge` **before** redeploy; record image IDs at tag time and verify post-rebuild divergence.
- **DB (user decision)**: **mysqldump gate** — dump the dmac schema (at minimum `assistant_chat_session` + `assistant_cc_transcript`) before any migration-applying deploy or paid E2E; dump path recorded in the run dir.
- **Secrets**: real Bedrock/Django/GCP secret values must never appear inline in shell commands, transcripts, process lists, manifests, or evidence. For the second instance, provide the Bedrock token via an approved secret file/stdin/pre-created 0600 env file and record only file hashes/permissions.
- **Live CC rollback identity**: before the second-instance window, snapshot live CC container IDs, image IDs, env-file hashes, network memberships, mounts, SA clone SHA, and `cc_runner_available()`. Rollback success requires recreating live CC from the verified SA clone and matching those IDs/hashes/memberships or explicitly stopping on mismatch before declaring success.
- **OI-3**: §4 compose notes + §6.5 assertions are the guard; post-merge re-verify segmentation (network membership table, zero agent creds, negative reachability).

## 10. Decisions (locked this session — user MCQ) & residual openings

| Decision | Ruling |
|---|---|
| Installer defects D1–D3 | Fix **in this plan, pre-merge**, TDD on feat (§5.0). |
| Non-8000 + clean-seed + paid E2E environment | **Second port-bumped instance on the dev VM** from the merged candidate; off-server laptop greenfield remains a separate later gate. |
| Reviews' hardening machinery | **Full adoption** (§6–§8). |
| DB rollback | **mysqldump gate** before migration-applying deploys (§9). |
| HIGH-risk conflict hunks | Operator personally reviews `dmac/settings.py` + `docker-compose.yml` hunks (house per-change sign-off rule; adopted, not newly asked). |
| VM headroom for the second instance | **Sign-off-gated prune gate before Task 16** (user MCQ, 2026-07-07 onboard). Measured at onboard: disk 92% full (11 GB free; 77.2 GB reclaimable images + 8.3 GB build cache), RAM ~6 GB available, swap 3/3 GB used. Prune only user-approved dangling/unused images (keep live-stack images + ALL rollback tags), then re-check disk+RAM; if RAM still critical, STOP and surface the laptop fallback. Encoded as plan Task 16 Step 0. |

**Still open (resolve at plan/run time):** NF-core ENA-resolvable UIDs (live-DB question, pin at run-prep); exact `host_only` allowlist contents (drafted in the plan, reviewed at merge).
