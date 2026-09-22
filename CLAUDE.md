# NExtSEEK: the map

NExtSEEK extends FAIRDOM SEEK (a Django/Mezzanine app for active scientific data curation) with a
Neo4j sample graph and the Nessie chat assistant. This repo is the Docker bring-up of the whole stack,
driven by `./startup.sh`. It is the PUBLIC repo BioMicroCenter/NExtSEEK.
- All AI code lives in `NessieAI/`; the API surface stays in `nextseek_api/`. Read `NessieAI/README.md` and `NessieAI/CLAUDE.md` before working there.
- The chat panel sends every turn to `POST /nextseek_api/cc-assistant/query/async/`, where a router picks `nextseek_query`, `container_cc` or `unrelated` (`NessieAI/router/README.md`).
- Each folder documents itself beside its code: `README.md` for everyone, `CLAUDE.md` for agent rules. This file only maps and links.

## Rules for every task

- Public repo: no credentials, emails, personal home paths, or anything from `NessieAI/history/` evidence, in any doc, issue or commit.
- Never commit secrets (`DEPLOYMENT.md` §8) or local data: `filestore/`, `startup/seed/filestore.tar.gz`, `logs/`, `outputs/`.
- uv, not pip: `uv add`, `uv run`. Never hand-edit dependency pins.
- Conventional commits with module scopes: `feat(startup): ...`, `fix(pipeline): ...`, `refactor(schemas): ...`.
- Stage files by name, never `git add -A`: the shared tree holds other sessions' work. `.gitattributes` keeps bytes as-is, so edit CRLF files in binary mode.
- Deferred work becomes a GitHub issue, never a silent TODO: draft per `docs/ISSUE-CONVENTIONS.md`, validate with `scripts/validate_issue.py`, file only after a person approves.
- Before editing inside a folder, read its `CLAUDE.md`. Deploy hygiene is `DEPLOYMENT.md` §1.

## I need to...

| Task | Skill | Read |
|---|---|---|
| Bring up a stack | | `README.md`, then `startup/README.md` |
| Find out why the stack misbehaves | | `./startup.sh doctor`; `startup/README.md` "When bring-up misbehaves"; `docker logs nextseek`; `logs/django.log`; `outputs/<timestamp>_<user>/console.txt` per chat turn |
| Deploy, roll back or verify | `deploy` | `DEPLOYMENT.md` §3, §5, §6; `NessieAI/cc/DEPLOY.md` for Container-CC |
| Know which rebuild a change needs | `deploy` | `DEPLOYMENT.md` §3.2 |
| Change config or a secret | | `DEPLOYMENT.md` §8; render source `startup/templates/nextseek.env.template`; `.env.example` |
| Harden an instance before exposing it | | `NExtSTEPS.md` (rotating the demo passwords is the minimum) |
| Add or change an endpoint | `nextseek-viewset` | `nextseek_api/CLAUDE.md`, `docs/endpoint-authorization-register.md`, `ci/README.md` |
| Add a Django migration | | `nextseek_api/CLAUDE.md` (the chain forks; check heads first) |
| Change a page, template or static file | | `themes/README.md`, `seek/README.md`, `docs/UI.md` |
| Change settings, URLs or the SEEK login | | `dmac/README.md`, `dmac/CLAUDE.md` |
| Work on ingest, attributes or assay registration | | `nextseek_api/README.md` (children table) |
| Work on sample downloads | | `docs/sample-download-workflow.md`, `nextseek_api/services/README.md` |
| Query Neo4j or rotate its password | | `docs/neo4j-programmatic-access.md` |
| Add a Container-CC operation | `add-cc-op` | `.claude/skills/add-cc-op/SKILL.md` (`/add-cc-op`) |
| Make any other AI change | | `NessieAI/README.md` "To change X, edit Y" |
| Review or grade a Nessie run | `nessie-run-review`, `nessie-bayes-report` | Skills table below |
| Run Django, startup or AI tests | | Build and test, below |
| Know what CI blocks | | `ci/README.md` "What can fail a job, and what is only a report" |
| File an issue | `nextseek-issues` | `docs/ISSUE-CONVENTIONS.md` |
| Find a doc, or history | | `docs/INDEX.md`, `docs/archive/INDEX.md`, `NessieAI/history/INDEX.md` |
| Add, move or retire a doc | | Editing these docs, below |

## Skills

<!-- BEGIN DOCS-MAP:skills -->
| Skill | Use when | Path | Loads |
|---|---|---|---|
| `add-cc-op` | adding or wiring a `nextseek-*` op or CC tool; `ops.py` is the source of truth, never `plugin.json` or `discover_ops` | `.claude/skills/add-cc-op/SKILL.md` | auto (`/add-cc-op`) |
| `deploy` | install, redeploy, rollback or post-deploy verification on any box | `.claude/skills/deploy/SKILL.md` | auto |
| `nextseek-issues` | a deferred bug, plan residuals, or any request to file an issue | `.claude/skills/nextseek-issues/SKILL.md` | auto |
| `nextseek-viewset` | adding or changing a `nextseek_api` ViewSet; finish with `scripts/validate_viewset_conventions.py` | `.claude/skills/nextseek-viewset/SKILL.md` | auto |
| `nessie-run-review` | triaging a finished nessie_tests run into an HTML review | `NessieAI/tests/nessie_tests/output-skill/SKILL.md` | by path |
| `nessie-bayes-report` | grading a paired `--bayesian` run and merging it into HiBayes | `NessieAI/tests/nessie_tests/output-skill-bayesian/SKILL.md` | by path |
<!-- END DOCS-MAP:skills -->

Personal and external tools (session handoff, the parallel fix lane, the dmac-curation plugin) live outside this repo. The handoff skill writes only into ignored paths of the checkout (a local session-index `CLAUDE.md` and `reports/`, both under `.claude/`); the fix-lane skills work in their own worktrees and branches.

## Folders

<!-- BEGIN DOCS-MAP:folders -->
| Folder | What it does | Read first | Area label |
|---|---|---|---|
| `.claude/` | committed project skills only; everything else under it is local | Skills, above | by subject |
| `.github/` | CI workflows (`ci-pytest.yml`, `ci-smoke.yml`) and the structured issue form | `ci/README.md` | `deployment` |
| `NessieAI/` | all AI code: router, NS and CC engines, HiBayes, AI images, AI tests, AI docs and history | `NessieAI/README.md`, `NessieAI/CLAUDE.md` | `router`, `cc_assistant`, `chat_nextseek`, `schema-rag` |
| `api_app/` | the original REST app: installed, imported, never mounted | `api_app/README.md` | `nextseek_api` |
| `ci/` | route registry, blocking gates, baseline differ, post-deploy smoke suite, docs checker | `ci/README.md` | `deployment` |
| `context/` | hand-owned source of truth for the catalog context Nessie reads: sample types, assays, assay mappings and projects. Feeds the `dmac.*_context` tables, which the JSON exports are generated FROM | `context/README.md` | `chat_nextseek` |
| `dmac/` | Django project package: settings modules, root URLconf, ASGI/WSGI, SEEK login views | `dmac/README.md` | `nextseek_api` |
| `docker/` | nginx config, app-container scripts, env docs; AI images are in `NessieAI/docker/` | `docker/README.md` | `deployment` |
| `docs/` | cross-cutting docs only; folder docs live beside their code | `docs/INDEX.md` | by subject |
| `nextseek_api/` | the Django app behind every `/nextseek_api/` URL, and the API half of the assistant | `nextseek_api/README.md` | `nextseek_api` |
| `scripts/` | validators, the macOS/worktree test runner, one-off programs | `scripts/README.md`, `scripts/CLAUDE.md` | by subject |
| `seek/` | Django app over SEEK's tables: table layer, search, the NExtSEEK pages | `seek/README.md` | `ui`, `sample-search` |
| `startup/` | the `./startup.sh` Typer CLI (its own uv project) and the seed data | `startup/README.md` | `installer` |
| `static/` | source static files (second entry of `STATICFILES_DIRS`, behind the theme) and the committed chat bundle `static/js/chat_assistant/` | `DEPLOYMENT.md` §3.2 (collectstatic); `themes/CLAUDE.md` for which twin wins | `ui` |
| `templates/` | upstream Mezzanine template tree; inert, nothing resolves here | `themes/README.md` | `ui` |
| `themes/` | the NextSeek theme: its templates and static win site-wide | `themes/README.md` | `ui` |
<!-- END DOCS-MAP:folders -->

Area labels are written without their `area:` prefix (`docs/ISSUE-CONVENTIONS.md` "Area labels").

- Root build files (`docker-compose.yml`, `Dockerfile`, `startup.sh`, `manage.py`, `gunicorn.conf.py`, `pyproject.toml`, `uv.lock`, `.env.example`): `DEPLOYMENT.md` §0. The empty root `__init__.py` stays: removing it can change pytest module naming.
- Untracked runtime dirs: `logs/`, `outputs/`, and the root `schema_rag/` (DuckDB data). Per-instance state is `startup/.instance.json`.
- Subfolders are listed in `NessieAI/README.md` and `nextseek_api/README.md`.

## Build and test

`./startup.sh` verbs: `install`, `doctor`, `rebuild` (`--component app|cc-agent|bedrock-proxy|nextseek-sidecar|custom-stack`; app is the default), `reset` (DESTRUCTIVE: drops volumes), `ci`, `seed-filestore`, `dump-db`. Details: `startup/README.md`.

| Lane | Where | Read |
|---|---|---|
| Django (`nextseek_api`, `seek`, `dmac`, `ci/gate`) | throwaway container over a read-only mount of the checkout | `ci/README.md` "Running and testing" |
| startup CLI | its own uv project, run from `startup/` | `startup/CLAUDE.md` "Test command" |
| Every AI lane (four runtimes) | see the lane table | `NessieAI/tests/README.md` |
| This checkout inside the stack image (macOS, worktrees) | `scripts/run_tests.sh`; copy `dmac/local_settings.py` in first | `scripts/README.md` |

A bare root `pytest` loads the real `dmac.settings` and walks the whole tree, so always pass the test settings module and explicit paths. Host `uv sync` fails without MySQL client headers. `host_only` marks source-tree tests that in-container runs exclude (`pyproject.toml`). Docs check: `python3 ci/docs_map.py`.

## Where work stands

<!-- BEGIN DOCS-MAP:status -->
Live state comes from GitHub and git, never from spec headers or plan checkboxes.
- Boards: [needs-ruling](https://github.com/BioMicroCenter/NExtSEEK/issues?q=is%3Aopen+label%3Aneeds-ruling), [priority: high](https://github.com/BioMicroCenter/NExtSEEK/issues?q=is%3Aopen+label%3A%22priority%3A+high%22), [area labels](https://github.com/BioMicroCenter/NExtSEEK/labels), [open PRs](https://github.com/BioMicroCenter/NExtSEEK/pulls).
- Commands: `git branch -r --no-merged origin/dev` and `gh issue list --label needs-ruling`.
- SEEK OAuth consumer, `origin/feat/seek-oauth-consumer-c`; removes password login (#16).
- CI coverage increments 2 to 5, and the prod cutover (#104).
- Session reports are local and never committed.
<!-- END DOCS-MAP:status -->

## Gotchas

- Admin gates read `is_superuser`, never `is_staff`: the SEEK login sets `is_staff` on everyone (`dmac/CLAUDE.md`).
- A new `router.register` goes into `ci/routes.py` in the same change, or the blocking gate fails (`nextseek_api/CLAUDE.md`).
- Nothing may be registered after the Mezzanine catch-all (`dmac/CLAUDE.md`).
- A `static/` change needs `collectstatic` after the rebuild (`DEPLOYMENT.md` §3.2). Schema fixups run only on `install`, never on `rebuild` (`startup/CLAUDE.md`).
- A chat UI change is two commits, source then the rebuilt bundle; only `npm run build:embedded` ships (`NessieAI/chat_frontend/CLAUDE.md`).
- A rebuild wipes `MEDIA_ROOT`, including batch-upload jobs (`nextseek_api/batch_upload/CLAUDE.md`).
- A theme template or static file name wins site-wide, and theme CSS is cached 30 days: hard-reload before blaming a deploy (`themes/CLAUDE.md`).
- Never add a published port on dev or prod; multiplex onto 443 (`docker/CLAUDE.md`).
- Proxy ViewSets share one SEEK session; do not copy the pattern (`nextseek_api/CLAUDE.md`).
- `docker/seek-nginx.conf` must exist as a file before `seek` is recreated (`DEPLOYMENT.md` §3.1).
- Rebuild never re-renders `docker/nextseek.env`, and the `DMAC_*` override lines must be absent from it (`NessieAI/CLAUDE.md`).
- Importing `dmac.settings` creates directories, so read-only mounts need them made first (`ci/CLAUDE.md`).
- `seek/views/` is a package; patch the owning module, not `seek.views` (`seek/README.md`).
- Paid lanes (`RUN_REALSTACK=1`, `--tier full`, `--bayesian`) need the owner's approval per run (`DEPLOYMENT.md` §1, §7).

## Editing these docs

| Fact | Its one home |
|---|---|
| What a folder is and its rules | that folder's `README.md` + `CLAUDE.md` |
| Deploy, config, secrets, rebuild table | `DEPLOYMENT.md` |
| Test lanes | `ci/README.md`, `startup/CLAUDE.md`, `NessieAI/tests/README.md` |
| The list of cross-cutting docs | `docs/INDEX.md` |
| AI facts | `NessieAI/` |

- Cite docs as `FILE §N` or `FILE "Heading"`, and code by symbol. No line numbers in map files. Write no dated counts or run results into a README or CLAUDE file (cite the command that produces them); older ones go when their section is next edited.
- A new folder gets a `README.md` (plus a `CLAUDE.md` only if it has invariants) and one row in its parent map.
- Retire a doc with `git mv` into `docs/archive/<yyyy-mm>/` and add its row to `docs/archive/INDEX.md`. A spec names `Tracking: #N`.
- `NessieAI/history/**` and `docs/archive/**` are frozen.
- Run `python3 ci/docs_map.py` before pushing; its failures print the row to add.
- Never add a Session reports section to a tracked file.
