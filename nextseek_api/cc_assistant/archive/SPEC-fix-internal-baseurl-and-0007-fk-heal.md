# Spec: internal self-call base URL + 0007 FK charset heal (2026-07-07)

Two independent greenfield-surfaced defects on `feat/dmac-assistant-full-integration`,
both root-caused by systematic-debugging with adversarial verification
(workflow `wf_82350ab8-37c`; Step 7d reports 2026-07-07). Minimal fixes only —
no entrypoint hardening, no harness changes, no seed regeneration (flagged as
follow-ups, out of scope).

## Bug B — `NEXTSEEK_BASE_URL` targets the host-published port

**Root cause (confirmed):** `startup/templates/nextseek.env.template:12` derives
`NEXTSEEK_BASE_URL` from `NEXTSEEK_HOSTNAME` = `127.0.0.1:${NEXTSEEK_PORT}` (the
HOST publish port, auto-bumped when 8000 is busy), while the app listens
internally on the invariant `:8000` (`gunicorn.conf.py:1`, `entrypoint.sh` daphne
branch). Every in-Django chat_nextseek REST self-call
(`chat_nextseek/.../helpers/tools/nextseek_api.py:23`, reporter metadata, SOP
fetch, ChatConfig schema fetch, granular `api-read`/`api-write`/
`generate-submission`, viewset `query`/`plan`) reads that value raw
(`config.py:366`) → Connection refused whenever host port ≠ 8000. Masked on the
dev server only because 8000 == 8000.

**Consumer-map facts that pick the fix (verified exhaustively):**
- `NEXTSEEK_HOSTNAME` is the user-facing address (`dmac/settings.py:453` →
  `SEEK_DATAFILE_SERVER` → datafile/SOP weblinks, persisted into SEEK DB). It
  must stay host-published. It has zero overlap with `NEXTSEEK_BASE_URL`
  consumers.
- `NEXTSEEK_BASE_URL` consumers are all container-internal: chat_nextseek
  self-calls, agent env (via `_rewrite_loopback_url`, which passes loopback →
  `nextseek_nginx` and is unaffected), sidecar (compose-pinned
  `http://nextseek_nginx` separately), e2e/evaluator env passthrough.
- Live dev's hand-maintained env already has `http://127.0.0.1:8000` — the fix
  is bit-identical there (zero live delta, no live env edit needed).
- `sanitize_protocols_for_llm` (`protocols.py:229`) already rewrites
  loopback URLs out of LLM/report payloads; an internal loopback value stays
  sanitized (an `nextseek_nginx` value would NOT — one reason a1 beats a2).

**Fix (option c — USER-SELECTED, 2026-07-07):** split public vs internal URLs.
(Option a1 — pinning `NEXTSEEK_BASE_URL` itself to the internal listener — was
implemented first and retracted after the user challenged the design; a1's
"vendored chat_nextseek" argument was also wrong — chat_nextseek is
first-party in-tree.)
- `NEXTSEEK_BASE_URL` keeps its public meaning (derives from
  `NEXTSEEK_HOSTNAME`, unchanged).
- New `NEXTSEEK_INTERNAL_BASE_URL="http://127.0.0.1:8000"` rendered by
  `startup/templates/nextseek.env.template` and documented in
  `docker/nextseek.env.example`.
- `chat_nextseek/src/chat_nextseek/config.py`: new
  `_resolve_nextseek_base_url()` — prefers `NEXTSEEK_INTERNAL_BASE_URL`, falls
  back to `NEXTSEEK_BASE_URL` (so the live dev env, which has no internal var,
  is byte-for-byte unaffected).
- `nextseek_api/cc_assistant/cc_engine.py`: `build_agent_environment` and
  `_automode_settings_args` prefer the internal var too (loopback→nginx
  rewrite unchanged; converges on `http://nextseek_nginx` either way).
- `startup/templates/local_settings.py.template`: the `_PROD_OVERRIDES`
  overlay pops `NEXTSEEK_INTERNAL_BASE_URL` while building the PROD ChatConfig
  (else the internal var would shadow the prod URL) and restores it after.

**Tests:** `startup/tests/test_config.py` (real-template render on bumped
ports: public line unchanged, internal line port-independent; example parity;
PROD-overlay suppression present), `chat_nextseek/tests/test_config_base_url.py`
(resolver precedence/fallback/rstrip), `test_cc_engine_env.py` (internal var
preferred for the agent env; bumped-port loopback still rewritten).

**Non-goals (flagged, not fixed here):** ChatConfig import-time schema fetch
already fails soft at every boot (pre-existing); gunicorn self-call reentrancy;
cosmetic `:8000` echoes in debug/provenance fields.

## Bug C — migration 0007 FK charset mismatch (latin1 parent × utf8mb4 child)

**Root cause (confirmed):** the seed dump ships `assistant_chat_session` as
latin1 (all 65 tables); fresh DBs are utf8mb4 (`--character-set-server`), so
0007's `CreateModel` builds `assistant_cc_transcript` utf8mb4 and the deferred
`ADD FOREIGN KEY` to the latin1 `session_id` fails (errno 3780,
ER_FK_INCOMPATIBLE_COLUMNS — MySQL requires identical charset+collation).
MySQL DDL is non-transactional → table (with PK + unique triple) persists,
0007 unrecorded, every boot retries → 1050. Collateral: the abort also blocks
`seek.0002_samples_name_identity` on greenfields (`sorted(leaves)`:
`nextseek_api` < `seek`).

**Deployment states (all verified live/from evidence):**
- S1 fresh seed: latin1 parent, no child → FK fails (laptop repro, every seeded
  greenfield).
- S2 wedged: child exists FK-less, 0007 unrecorded (laptop after first boot).
- LIVE dev (4th state): 0007 RECORDED (2026-07-01 01:20 UTC — only consistent
  with an undocumented `migrate --fake`; Django provably cannot record it while
  the deferred FK ALTER fails), FK ABSENT, child utf8mb4 vs parent latin1.
  19 rows, 0 orphans, boot migrate clean.
- test_dmac / --no-seed: parent utf8mb4 → 0007 works natively.

**Fix:** shared idempotent heal + two vehicles.
1. `nextseek_api/migrations/_cc_transcript_heal.py` (underscore = ignored by the
   migration loader): `heal_cc_transcript(apps, schema_editor)` —
   - non-MySQL vendors: `schema_editor.create_model` if table missing (mirrors
     current behavior), else no-op;
   - MySQL: introspect parent `session_id` charset+collation from
     information_schema; CREATE the table if absent (hand DDL mirroring the
     model exactly: BigAutoField PK, varchar(128)×2, longblob, bigint,
     datetime(6); `chat_session_id char(32)` explicitly parent-charset/collation;
     Django's unique-index name `assistant_cc_transcript_chat_session_id_cc_sessi_bdda2d20_uniq`);
     else `MODIFY chat_session_id` to parent charset+collation when mismatched
     (lossless — ASCII UUID hex); then, if no FK to
     `assistant_chat_session(session_id)` in KEY_COLUMN_USAGE: orphan-guard
     (post-MODIFY same-collation join; fail loudly with counts) and
     `ADD CONSTRAINT assistant_cc_transcript_chat_session_id_fk FOREIGN KEY`
     (no ON DELETE clause — Django cascades in Python, DB FK is plain).
2. Rewrite `0007_ccsessiontranscript.py` as `SeparateDatabaseAndState`:
   `state_operations` = the existing `CreateModel` VERBATIM (model-state parity;
   gate: `makemigrations nextseek_api --check --dry-run` stays clean);
   `database_operations` = `RunPython(heal, noop)`; `atomic = False` (house
   precedent `0005_ensure...`). Converges S1 (create matched + FK) and S2
   (modify + FK) and records 0007; inert on live (recorded) and test_dmac
   (native path replaced by equivalent heal-create).
3. New `0008_heal_cc_transcript_fk.py`: `RunPython(heal, noop)` only, depends on
   0007, `atomic = False`. This is the only vehicle that reaches LIVE dev
   (migrate clean there) — performs the MODIFY + ADD FK on next deploy. No-ops
   everywhere 0007 already healed. No InconsistentMigrationHistory risk (new
   dependents are safe; only new *dependencies* of applied migrations break);
   rollback images tolerate the recorded-unknown 0008 row (loader skips them).

**Tests:**
- Content/structure (no DB): 0007 is SeparateDatabaseAndState with verbatim
  CreateModel state op + atomic=False; 0008 exists, RunPython-only, depends on
  0007; both share the single heal function.
- `@pytest.mark.django_db` (lane ii, fresh test_dmac runs the full chain incl.
  rewritten 0007): FK present, child charset/collation == parent's.
- Behavioral latin1-parent tests (raw pymysql on a scratch schema, idiom +
  env-gated skip copied from
  `nextseek_api/batch_upload/tests/test_migration_name_identity.py`):
  S1 (latin1 parent, no child) → heal → table + FK, charsets match;
  S2 (latin1 parent + utf8mb4 FK-less child, rows present) → heal → MODIFY +
  FK, data intact; live-state replay (same as S2 — 0008 path); idempotency
  (heal twice = no-op); orphan-guard raises with orphan rows present.

**Deploy sequence (authorized this session):** commit (one commit per bug) →
secret-scan → push → FF the SA clone → tag rollback images → compose build →
recreate nextseek → free verification: boot log shows 0008 applied cleanly,
live FK present + charsets aligned via information_schema, second boot "No
migrations to apply", site 200, gunicorn+celery, `cc_runner_available()`,
plus the pending free live re-verify of the advanced_search 3-term fix
(demo auth, no LLM spend).

**Follow-ups (out of scope, need separate sign-off):** entrypoint migrate is
non-fatal (masked Bug C); per-op harness scores backend-unreachable replies as
PASS (masked Bug B); seed dump latin1×utf8mb4 divergence (option e hygiene);
ChatConfig schema-fetch boot race; document what was actually run at the
2026-07-01 01:20 UTC deploy (`migrate --fake`?).
