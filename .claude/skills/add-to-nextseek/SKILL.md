---
name: add-to-nextseek
description: >-
  Start here for ANY change to NExtSEEK: "add X", "change Y", "fix Z", "how do I add ... to NExtSEEK". Covers
  pages and templates, static assets, API endpoints, Nessie (CC ops, NS prompts, agents, router), catalog
  entries (sample types, assays, projects), user docs pages, upload and ingest rules, the Neo4j graph, Django
  migrations, tests and CI checks, new folders and docs, config and secrets, deferred bugs, run reviews and
  shipping. A thin index: it names the steps and points at the doc or skill that owns the details.
---

# Add to NExtSEEK

This skill is an index, not a copy. Each recipe says what to do in a line or two and names the doc that owns
the details. When a doc and this skill disagree, the doc wins; fix the row here.

## 1. Start

- Branch and worktree off `origin/dev`: `git worktree add ../wt-<topic> -b <type>/<topic> origin/dev`. Never work on `dev`
  itself: other sessions share the checkout. Stage files by name, never `git add -A` (`AGENTS.md`).
- Read `ARCHITECTURE.md`, then the owning folder's `README.md` and `CLAUDE.md` (the root `CLAUDE.md` "Folders" table
  says which folder owns what, and its "I need to..." table routes common tasks).
- Public repo: no credentials, emails, personal paths or host names in any file, issue or commit message.
- Write the failing test first where the change has logic.

## 2. Recipes

`Gate` = also run section 3. Docs named are the owners; read them before editing.

| Change | Steps | Owner doc | Must pass | Skill |
|---|---|---|---|---|
| Page or template | URL in `seek/urls.py` (or before the Mezzanine catch-all in `dmac/urls.py`), view re-exported in `seek/views/__init__.py`, template extends `base.html`, `Route` in `ci/routes.py`, bump `OWNED_ROUTE_COUNT` in `ci/smoke/test_registry_contents.py` | `docs/ui/README.md`, `docs/ui/pages.md` "How to add a page" | route gate, page render test, hard-reload before blaming a deploy | |
| Static asset, CSS, JS | Theme twin wins over root `static/`: edit `themes/NextSeek/static/`. Theme templates go live on the next request after a pull; theme static needs an app restart; root `static/`, `seek/templates/` and Python need `./startup.sh rebuild`, then `collectstatic` | `docs/ui/ci-and-deploy.md`, `themes/CLAUDE.md`, `DEPLOYMENT.md` §3.2 | page tests | `deploy` |
| Chat UI | edit `NessieAI/chat_frontend/src/`, `npm run build:embedded`, commit the rebuilt `static/js/chat_assistant/` as a second commit | `NessieAI/chat_frontend/CLAUDE.md` | vitest in the package | |
| API endpoint | ViewSet under `nextseek_api/services/`, import in `nextseek_api/views.py`, `router.register` (longest prefix first) in `nextseek_api/urls.py`, models and `*_DESC`, `Route` in `ci/routes.py` plus `OWNED_ROUTE_COUNT` (`ci/smoke/test_registry_contents.py`); check `docs/endpoint-authorization-register.md` | `nextseek_api/CLAUDE.md`, the skill's own checklist | `python3 scripts/validate_viewset_conventions.py`, the conventions test pair, gate | `nextseek-create-endpoint` |
| Nessie action (Container-CC op) | shim, runner entry, `OpSpec` row in `NessieAI/cc/op_registry/ops.py`, regenerate surfaces | the skill | Audit A, no-write checks, `NessieAI/tests/cc/` | `add-cc-op` |
| Nessie NS change (prompt, agent, catalog, router, models) | find the row in "To change X, edit Y" and edit only that file; a routing, parser, schema-prose or CC-skill edit is a brain change: show the operator before and after and wait | `NessieAI/README.md` "To change X, edit Y", the folder's `CLAUDE.md` | the lane in that row, `NessieAI/tests/README.md` for the runner | |
| Catalog entry, sample type, assay, project | edit `context/*.json` only, never the tables; `python scripts/context_gen.py --emit update`, then `--emit exports` in the same change; the operator reviews an xlsx workbook before ANY database write | `context/README.md`, `scripts/README.md` group C | `NessieAI/tests/api/test_context_gen.py`, `NessieAI/tests/cc/test_cc_context_drift_guard.py` | |
| User docs page (`/docs/`) | `themes/NextSeek/docs/<slug>.md` with one H1, a line in that folder's `README.md`, images under `themes/NextSeek/static/docs/img/`; then from the repo root `python -m NessieAI.build_tools.ingest_nextseek_docs` and commit the snapshot (CI blocks without it; a brain change, show the operator the diff) | `docs/ui/docs-and-help.md` "Writing a docs page" | `seek/tests/test_docs_pages.py`, the docs snapshot test | |
| Upload or ingest rule | work in `nextseek_api/batch_upload/` (UIDs, policies, attribute keys, MEDIA_ROOT wipe on rebuild) | `nextseek_api/batch_upload/CLAUDE.md`, `nextseek_api/README.md` | the folder's tests | |
| Graph (Neo4j) change | the one writer is `nextseek_api/graph_sync/`; every writer of a graph-read table is registered in `ci/writers.py`; update the schema doc with the code | `docs/neo4j-schema.md`, `nextseek_api/graph_sync/README.md`, `ARCHITECTURE.md` "The Neo4j graph" | graph tests (blocking lane), gate | |
| Django migration | list `nextseek_api/migrations/` and find the current heads first: the chain has forked before and needs a merge migration. Never `migrate --fake`. A migration in the deploy range means a mysqldump first | `nextseek_api/CLAUDE.md`, `DEPLOYMENT.md` §3.1, §5.3 | migration check (section 3), `showmigrations` after deploy | `deploy` |
| Test or CI check | a new blocking test joins `ci/blocking_lanes.py` by its file name and must pass with SQLite, no network, no MySQL, no Neo4j; smoke tests need a box | `ci/README.md`, `ci/CLAUDE.md` | `ci/gate/test_blocking_lanes.py` | |
| New folder or doc | folder gets a `README.md` (R11) and a row in its parent map (R1); a doc is linked from another live doc (R6) and listed in `docs/INDEX.md`; retire with `git mv` into `docs/archive/<yyyy-mm>/` plus its row in `docs/archive/INDEX.md` | root `CLAUDE.md` "Editing these docs" | `python3 ci/docs_map.py` | |
| Config value or secret | add the key to `startup/templates/nextseek.env.template` and `.env.example` with a placeholder; real values live only in the gitignored env files; config-only changes need a force-recreate, not a build | `DEPLOYMENT.md` §8, §3.2 | no secret in `git diff` | `deploy` |
| Deferred bug or leftover | no silent TODO: draft the issue, validate it, ask a person before filing | `docs/ISSUE-CONVENTIONS.md` | `scripts/validate_issue.py` | `nextseek-issues` |
| Review a Nessie run | pull turns read-only, build the report; paired `--bayesian` runs use the second skill | the skills | | `nessie-run-review`, `nessie-bayes-report` |
| Ship it | rollback tag, scoped recreate, verification checklist; every deploy needs the operator's go | `DEPLOYMENT.md` §1, §3, §6 | smoke lane after rebuild | `deploy` |

## 3. Finish (every change)

1. Test lane: the throwaway container, never a bare root `pytest` (it loads the real settings and walks the tree).
   Details and the other lanes: `ci/README.md` "Running and testing", `startup/CLAUDE.md`, `NessieAI/tests/README.md`.
   ```bash
   mkdir -p schema_rag/duckdb schema_rag/embedding_models
   docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs -e DJANGO_SETTINGS_MODULE=dmac.test_settings \
     -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/src/NessieAI/chat_nextseek/src -v "$PWD":/src:ro,z -w /src \
     nextseek-nextseek:latest /app/.venv/bin/python -m pytest <paths> -q -p no:cacheprovider
   ```
2. Blocking CI gates: `ci/gate` (the same command with `ci/gate` as the path), the unit tests that
   `python3 ci/blocking_lanes.py` prints, and the migration check `.github/workflows/ci-pytest.yml` runs: the same
   container with `/app/.venv/bin/python manage.py makemigrations --check --dry-run --skip-checks nextseek_api`
   in place of `-m pytest ...` (exit 1 means a model change has no migration).
3. `python3 ci/docs_map.py` must be clean.
4. Docs: update the owning folder's README rows, keep root `CLAUDE.md` under its line cap (R7), list new docs in
   `docs/INDEX.md`. No dated counts or run results in a README.
5. Code graph: if files moved or were added, run `/graphify . --update`, then `python3 scripts/graph_files.py`, and commit
   the three files in `docs/graph/` (`docs/graph/README.md`).
6. Deferred work: `nextseek-issues` drafts, a person approves.
7. Nessie brain changes (routing, parser, schema prose, CC ops or skills, the docs snapshot): show the operator the
   before and after and wait for a yes. Paid lanes (`RUN_REALSTACK=1`, `--tier full`, `--bayesian`) need approval per run.
8. Ship through the `deploy` skill. The operator's go is required for every deploy; never rebuild or touch a box on your own.
