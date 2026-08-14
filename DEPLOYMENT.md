# NExtSEEK deployment & operations runbook

This is the **authoritative deployment-hygiene document** for NExtSEEK. It is
written for *any* operator — a human or a coding agent — deploying or
operating the stack on *any* host: the current dev server, a production
server, or a brand-new machine. It is deliberately procedural: it stands in
for a CI/CD pipeline that does not exist yet (tracker Step 4). Until such a
pipeline exists, **this document is the pipeline** — follow it exactly and in
order.

Related docs (each has a distinct job — don't cross-purpose them):

| Doc | Job |
|---|---|
| [`README.md`](README.md) | 5-minute localhost quick start |
| [`NExtSTEPS.md`](NExtSTEPS.md) | production hardening: credential rotation, TLS, DEBUG, backups |
| [`architecture.md`](architecture.md) | deep architecture reference (services, data flows, security model) |
| [`nextseek_api/cc_assistant/DEPLOY.md`](nextseek_api/cc_assistant/DEPLOY.md) | Container-CC subsystem specifics + paid acceptance gates |
| [`startup/README.md`](startup/README.md) | `./startup.sh` subcommand reference + known failure modes |

---

## 0. Topology at a glance

One `docker-compose.yml` at the repo root defines the whole stack:
**10 services, 2 networks, 7 external named volumes** (volumes are created by
`./startup.sh install`, never by compose itself).

| Service | Image | Host port | Network(s) | Role |
|---|---|---|---|---|
| `nextseek` | built from root `Dockerfile` | — (internal :8000) | default | Django app: gunicorn/daphne + Celery worker (queue `batch_upload`) |
| `nextseek_nginx` | `nginx:latest` | `127.0.0.1:${NEXTSEEK_PORT:-8000}` | default **+ dmac-cc-net** | static files + reverse proxy; the **only dual-homed** service |
| `db` | `mysql:8.0` | `127.0.0.1:3306` | default | two schemas: `dmac` (NExtSEEK) + `seek_production` (SEEK) |
| `neo4j` | `neo4j` | `127.0.0.1:7474` / `:7687` | default | sample/assay graph |
| `seek` / `seek_workers` | `fairdom/seek:1.15.1` | `127.0.0.1:3000` / — | default | upstream SEEK Rails app + delayed-job workers |
| `solr` | `fairdom/seek-solr:8.11` | — | default | SEEK search index |
| `bedrock-proxy` | built from `docker/bedrock-proxy/` | — | dmac-cc-net | holds the Bedrock token; model-allowlist auth proxy (container name `dmac-bedrock-proxy`) |
| `nextseek-sidecar` | built from `docker/ns-sidecar/` | — | dmac-cc-net | NS sidecar for the CC agent (healthchecked) |
| `cc-agent` | built from `docker/cc-runtime/` → `dmac-assistant:poc` | — | none | **build-target only** — never runs as a service; per-turn agent containers are spawned from this image by the app via the docker socket |

Key facts every operator must internalize:

- **App code is baked into the `nextseek` image** (`COPY . /app/`). Changing
  `nextseek_api/`, `chat_nextseek/`, `seek/`, `dmac/`, templates, etc.
  **requires an image rebuild**. Only these are bind-mounted at runtime and
  changeable without a rebuild: `themes/NextSeek/`, `dmac/local_settings.py`,
  `./outputs/`, `./logs/` (plus the docker socket and the `dmac-cc-users`
  volume).
- Secrets **never** enter images: `.dockerignore` excludes `docker/db.env`,
  `docker/nextseek.env`, `docker/bedrock-proxy/proxy-secret.env`,
  `dmac/local_settings.py`. They are injected at runtime via compose
  `env_file:` / bind mount.
- The `nextseek` container's entrypoint (`docker/scripts/entrypoint.sh`) is
  **fail-fast**: `collectstatic`, a bounded DB-readiness probe, and
  `migrate --noinput` each abort the container on failure (markers
  `[COLLECTSTATIC-FAILED]`, `[DB-UNREACHABLE]`, `[MIGRATE-FAILED]` in
  `docker logs nextseek`). A crash-looping container after a deploy means one
  of those failed — **never** "fix" a migration wedge with `migrate --fake`.
- `NEXTSEEK_SERVER` selects the web server: `gunicorn` (WSGI, **the deployed
  norm**) or `daphne` (ASGI/WebSocket, **the code default when unset**). Set
  `NEXTSEEK_SERVER=gunicorn` explicitly in `docker/nextseek.env` on every
  deployment unless you have decided otherwise; under gunicorn the chat UI
  uses HTTP polling for progress (no WebSocket).

---

## 1. Golden rules (deployment hygiene)

1. **Deploy only committed code from `origin/dev`.** Never hot-patch a
   running container: `docker cp` into a container is **ephemeral** — it is
   silently lost on the next recreate, and it makes the running system
   diverge from git. If you are testing an ephemeral patch, say so out loud,
   and rebuild the image before calling anything done.
2. **Take a rollback tag before every image rebuild** (§5). Verify it exists
   afterwards (`docker image inspect`). An unverified rollback plan is not a
   rollback plan.
3. **mysqldump gate:** before any deploy whose commit range includes a Django
   migration, dump the affected table(s) (or the `dmac` schema) first (§5.3).
4. **Scope your recreation.** Recreate only the service(s) whose image or
   config actually changed — normally `--no-deps nextseek`. Do not touch
   `bedrock-proxy` / `nextseek-sidecar` / databases on an app-only deploy;
   their uptime is part of the post-deploy verification.
5. **Run the verification checklist (§6) after every deploy** and record the
   results. "It built" is not "it works".
6. **Never weaken the CC agent isolation** (§9). No credentials in the agent
   env, Bedrock only via the proxy, `nextseek` never joins `dmac-cc-net`.
7. **Secrets live only in the gitignored files** (§8). Never commit them,
   never bake them into images, never echo their values into logs or docs.
8. **Paid/live LLM lanes are approval-gated** (§7, Appendix). Never run them
   as a side effect of a deploy.
9. **Pruning is not cleanup — it destroys backups.** Rollback tags and cached
   layers on the deploy host are the rollback path. Get the owner's explicit
   approval per item before removing images, tags, or volumes.

---

## 2. Greenfield install (a new box)

### 2.1 Prerequisites

- **Docker Engine ≥ 26** (API 1.45+ — required for the volume *subpath*
  mounts the sidecar uses) and **Compose plugin ≥ 2.26**. Check:
  `docker version` and `docker compose version`.
- [`uv`](https://docs.astral.sh/uv/) on PATH (the startup CLI runs under it).
- Disk: plan for **≥ 60 GB free** for a comfortable single instance
  (~19 GB of images, seed volumes, plus Docker build cache which grows into
  tens of GB over repeated rebuilds — see §11).
- RAM: ≥ 8 GB free. Do not co-host a second full instance on a small box.
- Outbound network for: Docker Hub / GHCR pulls, the S3 filestore seed
  (~215 MB), and (first use only) Hugging Face model downloads (§2.4).

### 2.2 Procedure

```bash
# 1. Clone the deploy branch
git clone -b dev https://github.com/BioMicroCenter/NExtSEEK.git
cd NExtSEEK

# 2. (Container-CC route) export the Bedrock token BEFORE install so the
#    proxy secret is rendered non-interactively (written 0600, gitignored):
export AWS_BEARER_TOKEN_BEDROCK=<institutional token>
export AWS_REGION=us-east-1        # optional; default us-east-1

# 3. One-command bring-up (interactive confirmation unless --yes):
./startup.sh install --seek-public-url https://<public-host>   # omit flag for localhost
```

`./startup.sh install` runs 9 phases: prereq checks → vendored-tree check →
instance/port resolution → **render config** (`docker/db.env`,
`docker/nextseek.env`, `dmac/local_settings.py`,
`docker/bedrock-proxy/proxy-secret.env`, root `.env`) → create the 7 external
volumes → seed MySQL + Neo4j from committed dumps (idempotent; skipped if
populated) → **build and start everything, including the CC services and the
`dmac-assistant:poc` agent image** → verify demo users → health checks. The
SEEK filestore blobs (~215 MB) are fetched from S3 and streamed in.

```bash
# 4. Hand-fill the rendered docker/nextseek.env (placeholders ship as
#    SET_IN_LOCAL_ENV; chat/LLM features stay disabled until filled):
#      GCP_API_KEY=...                  # Gemini (memory summarizer etc.)
#      AWS_BEARER_TOKEN_BEDROCK=...    # ALSO here — separate consumer from
#                                       # the proxy's copy, see §8
#      FDH_API=...                      # FAIRDOMHub, if used
#    And pin the server mode to the deployed norm:
#      NEXTSEEK_SERVER=gunicorn

# 5. Apply the env changes:
docker compose up -d --no-deps --force-recreate nextseek

# 6. Verify:
./startup.sh doctor          # 13 read-only checks (4 prereq + instance
                             # state + 8 health), non-zero on failure
#    ...then run the full checklist in §6.
```

**7. Before exposing to anyone you don't trust:** work through
[`NExtSTEPS.md`](NExtSTEPS.md) — rotate `demo`/`user` passwords, MySQL and
Neo4j credentials, ensure `DJANGO_DEBUG` stays **unset** (the code enables
debug only for `1`, `true` or `yes`, case-insensitive and whitespace-stripped;
anything else, including absent/empty/`false`, is debug-off), configure
`ALLOWED_HOSTS`/CSRF, and put a TLS-terminating reverse proxy in front
(nginx here serves plain HTTP on `127.0.0.1:${NEXTSEEK_PORT:-8000}`; TLS is
out of scope of this repo).

### 2.3 What install does **not** provision

Budget these as explicit post-install steps:

- **Embedding models (two of them).** Nothing pre-downloads:
  1. schema_rag's `BAAI/bge-small-en-v1.5` (lazy-loaded into
     `schema_rag/embedding_models/`, gitignored) — first schema ingest needs
     Hugging Face egress, or pre-provision offline via
     `startup/dev/provision_embedding_model.sh`;
  2. chat_nextseek's catalog matcher `sentence-transformers/all-MiniLM-L6-v2`
     — lazy-loaded on first NL-routing use.
  An air-gapped box fails on whichever path is exercised first.
- **Solr index.** The `seek-solr-data` volume starts empty; this repo's CLI
  has no reindex step. If search returns nothing, reindex from the SEEK side
  (upstream SEEK tooling).
- **TLS / public exposure.** See NExtSTEPS.md §5.
- **Backups.** See §11 and NExtSTEPS.md.

### 2.4 Known greenfield sharp edges

- **`--instance` multi-instance support does not cover the CC subsystem.**
  Core volumes/containers/ports are correctly prefixed per instance, but
  `dmac-cc-users` (volume), `dmac-bedrock-proxy` + `nextseek-sidecar`
  (container names), and `dmac-cc-net` (network) are pinned literals — a
  second instance on the same box will collide with (or silently share CC
  user data with) the first. One CC-enabled instance per box until this is
  fixed.
- Re-running `install` re-renders `docker/nextseek.env` wholesale and
  **rotates `DJANGO_SECRET_KEY`**. Only `--seek-public-url` and the proxy
  token have read-back preservation — hand-filled API keys, the
  **`NEXTSEEK_SERVER=gunicorn` pin** (its loss silently flips the next boot
  to daphne), and any hand-added CC-knob keys are all wiped. Re-apply every
  §2.2-step-4 edit after any re-install/reset **before** recreating
  `nextseek`.

---

## 3. Redeploying a code change (existing box)

This is the routine "ship a change" procedure — the manual CI/CD stand-in.

### 3.1 Standard procedure

```bash
cd <deploy-clone>                      # the clone the images are built from

# 1. Sync to the exact commit being deployed (fast-forward only):
git fetch origin dev
git log --oneline HEAD..origin/dev     # review what you are about to ship
git merge --ff-only origin/dev

# 2. Pre-deploy gates:
#    a. Migration check — does the range add migrations?
git diff --name-only HEAD@{1} HEAD -- '*migrations*'
#       If yes: mysqldump gate first (§5.3).
#    b. Rollback tag the CURRENT image before it is replaced:
docker tag nextseek-nextseek:latest nextseek-nextseek:pre-<short-change-name>
docker image inspect nextseek-nextseek:pre-<short-change-name> --format '{{.Id}}'  # verify

# 3. Rebuild what changed. For the common case (app change) PREFER the CLI —
#    it also auto-pushes the §5.2 off-box rollback baseline to GHCR:
./startup.sh rebuild                               # app change (the common case)
#    Granular builds when other images changed:
docker compose -p nextseek build cc-agent          # ALSO, if docker/cc-runtime/** changed
docker compose -p nextseek build bedrock-proxy     # only if docker/bedrock-proxy/** changed
docker compose -p nextseek build nextseek-sidecar  # only if docker/ns-sidecar/** changed

# 4. Recreate ONLY the rebuilt long-running service(s) (`./startup.sh rebuild`
#    already recreated nextseek; after raw compose builds do it yourself):
docker compose -p nextseek up -d --no-deps --force-recreate nextseek
#    (cc-agent is a build-target: the NEXT chat turn picks up the new image
#     automatically — nothing to restart. Recreate proxy/sidecar only if you
#     rebuilt them.)
#    If you rebuilt nextseek WITHOUT `./startup.sh rebuild`, the off-box
#    baseline was NOT pushed — `./startup.sh doctor` will flag it; run
#    `./startup.sh rebuild` next time or push per §5.2.

# 5. Watch the boot (entrypoint runs collectstatic → DB probe → migrate):
docker logs -f nextseek        # until gunicorn workers are up; no FAILED markers

# 6. Run the §6 verification checklist. Record results.
```

### 3.2 What change needs what action

| You changed | Required action |
|---|---|
| Python / templates / anything baked (`nextseek_api/`, `chat_nextseek/`, `seek/`, `dmac/` except `local_settings.py`) | rebuild `nextseek` + recreate |
| `static/` assets | rebuild + recreate, **then** `docker compose exec nextseek uv run manage.py collectstatic --noinput` |
| `chat_frontend/` React source | `npm run build:embedded` in `chat_frontend/`, commit the emitted assets, then rebuild + recreate + collectstatic |
| `docker/cc-runtime/**` (agent plugin/skills/CLAUDE.md/deps) | `docker compose build cc-agent` — next turn uses it; no service restart |
| `docker/nextseek.env` / `dmac/local_settings.py` (config only) | no build: `docker compose up -d --no-deps --force-recreate nextseek` |
| `docker/bedrock-proxy/**` or its secret env | `docker compose up -d --build --force-recreate bedrock-proxy` |
| `docker/ns-sidecar/**` | `docker compose up -d --build --force-recreate nextseek-sidecar` |
| A Django migration (in the range) | mysqldump gate (§5.3) → rebuild + recreate (migrate runs at boot) → verify with `showmigrations` |

### 3.3 If the deploy clone is owned by a different account

On shared servers the deploy clone (build context) may be owned by a service
account while operators work from their own accounts without sudo. All reads,
writes, and compose commands against the deploy clone then go through a
helper container that acts as the owning uid + the docker group:

```bash
SA=<absolute path to the deploy clone>
docker run --rm --user <owner-uid>:<docker-gid> -e HOME=/tmp \
  -v /var/run/docker.sock:/var/run/docker.sock -v "$SA":"$SA" -w "$SA" \
  docker:cli docker compose -p nextseek build nextseek
```

Sync the deploy clone from a working clone with a fast-forward-only fetch
(run inside the same helper pattern, using an image that has git):
`git fetch <working-clone-path> <branch> && git merge --ff-only FETCH_HEAD`
(needs `git config --global --add safe.directory '*'` and
`-c protocol.file.allow=always` inside the helper). Never edit the deploy
clone in place; never commit from it.

---

## 4. Config-only changes

`docker/nextseek.env`, `docker/db.env`, and `dmac/local_settings.py` are
runtime-injected — **no rebuild**:

```bash
docker compose -p nextseek up -d --no-deps --force-recreate nextseek
```

Then §6. Remember the entrypoint reruns migrate/collectstatic on every start;
a config typo that breaks Django settings shows up as a crash-loop, and the
prior image + rollback tag are your way back.

---

## 5. Rollback

### 5.1 Mechanism

Rollback = repoint `:latest` at a known-good tag and recreate **without
building**:

```bash
docker image inspect nextseek-nextseek:pre-<name> --format '{{.Id}}'   # MUST succeed first
docker tag nextseek-nextseek:pre-<name> nextseek-nextseek:latest
docker compose -p nextseek up -d --no-build --no-deps --force-recreate nextseek
```

Then §6. If the bad deploy applied a **data** migration, rolling back the
image does not roll back the data — that is what the §5.3 dump is for;
restoring it is a deliberate, owner-approved action, not part of routine
rollback.

### 5.2 Tag conventions and their care

- `nextseek-nextseek:pre-<short-change-name>` — per-deploy safety tag
  (created in §3.1 step 2b).
- A long-lived known-good baseline tag (historically
  `nextseek-nextseek:dev-rollback`) should always exist on a deploy host.
  **If you find a host with no baseline tag** (it has happened — tags have
  been lost to disk cleanups), create one immediately from the current
  known-good image before doing anything else:
  `docker tag nextseek-nextseek:latest nextseek-nextseek:baseline-<YYYYMMDD>`.
- **Tags are backups. Verify they exist before you rely on them** —
  `docker image list` tags have historically been lost to well-intentioned
  disk cleanups. Any rollback script must *fail loudly* (`set -e`) if its
  source tag is missing, otherwise it will silently "roll back" onto the
  current image.
- **Off-box copies:** local tags die with the host (or with a prune). For
  retention, push tags to a **private** registry (e.g. GHCR) or `docker save`
  them to off-box storage. **Never push a `docker commit` snapshot of a
  running container off the box** — such snapshots embed the container's
  runtime environment (i.e. real secrets) in the image config. Only push
  images produced by `docker build` / `docker compose build`, whose env is
  injected at runtime and never baked.
- **Automated off-box baseline (startup CLI):** `./startup.sh rebuild` on the
  canonical instance automatically runs the pre-push gate below on the fresh
  image, tags it `ghcr.io/biomicrocenter/nextseek:baseline-<YYYYMMDD>-<sha>`,
  pushes it to the private org package, and logs docker out again. The step
  **never fails the deploy**: missing/expired credentials, a gate failure, or
  a registry error each print an unmissable banner with the fix, are recorded
  in `startup/.ghcr-push-state.json` (gitignored), and stay red in
  `./startup.sh doctor` until a push succeeds. Credential: a classic PAT with
  `write:packages` — its owner must be a **BioMicroCenter org member** (repo
  roles are not enough; this was learned the hard way) — stored per deploying
  user at `~/.config/nextseek/ghcr.env` (`GHCR_USER=…`/`GHCR_TOKEN=…`, mode
  600, path overridable via `NEXTSEEK_GHCR_ENV`). When a token expires, any
  org member can mint their own and drop it in their own home — no shared
  credential. The gate encodes one accepted deviation: `/app/.env` passes
  only if its sole key is `LURIAKEY` (a file path, verified non-credential,
  accepted 2026-08-05); any other key name fails the gate.
- **Pre-push gate (mandatory before ANY off-box push):** even a
  build-produced image can carry secrets if the build context was dirty —
  ad-hoc copies of rendered env files (e.g. `docker/nextseek.env.bak.<date>`)
  have been swept into an image by `COPY . /app` on a real deploy host.
  Before any tag leaves the box, prove the image is free of baked
  config/secret files:

  ```bash
  docker run --rm --network none --entrypoint sh <image> -c \
    'ls /app/.env /app/docker/*env* /app/dmac/local_settings.py 2>/dev/null; true'
  ```

  PASS = nothing printed except (at most) `docker/nextseek.env.example`.
  Anything else → do **not** push: clean the build context / fix
  `.dockerignore` (env files are excluded by *pattern*, not exact name — see
  `.dockerignore` and `test_build_context_env_guard.py`), rebuild, re-run
  the gate.

### 5.3 mysqldump gate (before migration-applying deploys)

```bash
docker exec <mysql-container> mysqldump -u<user> -p<pass> dmac <affected-table> \
  > backup-<table>-pre-<migration>-<YYYYMMDD>.sql
chmod 600 backup-*.sql     # contains real data — treat as a secret
```

Dump the specific affected table(s) when known (faster, smaller), the whole
`dmac` schema when not. Store outside any git checkout; note the path in your
deploy record.

---

## 6. Post-deploy verification checklist

Run all of these after every deploy (all read-only, zero spend). They are
given as a fenced block so they copy correctly from the raw file:

```bash
# 0. The published port lives in the root .env (compose interpolation), not
#    your shell — source it first (default-port boxes may skip this):
NEXTSEEK_PORT=$(grep '^NEXTSEEK_PORT=' .env | cut -d= -f2)

# 1. Site up — expect: 200
curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:${NEXTSEEK_PORT:-8000}/"

# 2. Server mode — expect: NEXTSEEK_SERVER=gunicorn; 'gunicorn dmac.wsgi'
#    workers + a 'celery … batch_upload' worker; ZERO daphne lines
docker exec nextseek printenv NEXTSEEK_SERVER
docker top nextseek | grep -c daphne          # expect: 0

# 3. No crash-loop — expect: 0
docker inspect nextseek --format '{{.RestartCount}}'

# 4. Boot clean — expect: no FAILED/UNREACHABLE markers; only expected
#    'Applying <migration>... OK' lines
docker logs nextseek 2>&1 | grep -E '(COLLECTSTATIC-FAILED|DB-UNREACHABLE|MIGRATE-FAILED|Applying)'

# 5. Migrations — expect: all [X]
docker exec nextseek uv run manage.py showmigrations nextseek_api | tail -5

# 6. CC route wired — expect: (True, 'ok')
#    (the image has NO bare `python` on PATH — use `uv run --no-sync`, which
#    executes in the app env /app/.venv without modifying it)
docker exec nextseek uv run --no-sync python -c "from nextseek_api.cc_assistant import cc_engine; print(cc_engine.cc_runner_available())"

# 7. OI-3 peers untouched (app-only deploy) — expect: uptime/health
#    unchanged from before the deploy
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -e nextseek-sidecar -e dmac-bedrock-proxy

# 8. Rollback tag present — expect: succeeds (prints an image ID)
docker image inspect nextseek-nextseek:pre-<name> --format '{{.Id}}'

# 9. Health suite — expect: exit 0. Run from a checkout with uv on PATH
#    (NOT via the §3.3 docker:cli helper — it has no uv, and doctor's HTTP
#    probes need host-loopback access).
./startup.sh doctor
```

For CC-touching deploys, additionally run the OI-3 checks in §9. For a full
greenfield acceptance (all plugin ops live, paid), see the Appendix.

---

## 7. Test lanes

Free lanes (run before/after deploys as appropriate):

| Lane | Command | Notes |
|---|---|---|
| Hermetic cc_assistant (no DB, no spend) | `PYTHONPATH="$PWD:$PWD/dmac_assistant/src" uv run --no-project --with pytest --with orjson --with 'pydantic>=2.13' --with 'baml-py==0.222.0' python -m pytest nextseek_api/cc_assistant/tests/ --noconftest -p no:cacheprovider -q --ignore=nextseek_api/cc_assistant/tests/test_cc_realstack.py` | from repo root |
| In-container DB-backed **clean lane** | `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/ --create-db -k 'not realstack' --ignore=nextseek_api/cc_assistant/tests/test_step7_compose_deploy.py --ignore=nextseek_api/cc_assistant/tests/test_cc_realstack.py` | the canonical behavioral suite; runs in the live container (has secrets, db network, `test_dmac` grant). Do **not** run the whole `nextseek_api/` tree in-container — it has ~407 known environmental harness errors that are not regressions |
| Source-tree hygiene (`host_only`) | `docker run --rm -v <WRITABLE checkout copy>:/repo -w /repo -v /usr/bin/docker:/usr/local/bin/docker:ro -v /usr/libexec/docker/cli-plugins:/usr/local/lib/docker/cli-plugins:ro nextseek-nextseek:latest uv run --project /app --no-sync python -m pytest -m host_only nextseek_api/cc_assistant/tests/ -q` | needs a **writable** checkout (settings import mkdirs) and **both** the docker CLI and compose plugin mounted; asserts on the checkout, not the image (the image strips `.gitignore` by design). `--project /app` keeps uv on the image env (`/app/.venv`), not the mounted checkout's |
| startup CLI | `uv run --project startup --group test pytest startup/tests -q` | isolated uv project |
| Doc guards | included in the hermetic lane (`test_deploy_docs_guard.py`) | keeps this file and DEPLOY.md compose-native |

Paid/live lanes (`RUN_REALSTACK=1`, `-k realstack`) are **approval-gated —
never run them without the owner's explicit per-run sign-off** (they spend
real LLM budget against the live stack). See the Appendix.

---

## 8. Configuration & secrets inventory

Five gitignored files, all rendered by `./startup.sh install` (and re-rendered
by `reset`):

| File | Rendered from | Holds |
|---|---|---|
| `docker/db.env` | `startup/templates/db.env.template` | MySQL root + app credentials |
| `docker/nextseek.env` | `startup/templates/nextseek.env.template` | Django secret, Neo4j password, SEEK URL, LLM keys (`GCP_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`, `FDH_API`); `NEXTSEEK_SERVER` + CC knobs are **hand-added** (not in the template — re-add after any re-render, §2.4) |
| `dmac/local_settings.py` | `startup/templates/local_settings.py.template` | Django settings overlay (PROD ChatConfig block, etc.) |
| `docker/bedrock-proxy/proxy-secret.env` | rendered programmatically (0600) | `AWS_BEARER_TOKEN_BEDROCK` + `AWS_REGION` **for the proxy** |
| `.env` (repo root) | rendered | non-secret compose interpolation: `COMPOSE_PROJECT_NAME`, ports, `INSTANCE_PREFIX` |

Sharp edges an operator must know:

- **The Bedrock token is needed in TWO files** for two different code paths:
  `docker/nextseek.env` (native chat_nextseek direct-Bedrock path) **and**
  `docker/bedrock-proxy/proxy-secret.env` (the sandboxed CC agent's only
  route). Filling only one leaves the other chat path dead, with no
  automated cross-check.
- **Neo4j's password exists in two places with no interpolation binding
  them:** the `NEO4J_AUTH` literal in `docker-compose.yml` and
  `NEXTSEEK_NEO4J_PASSWORD` in `docker/nextseek.env`. Rotate both together
  (and note Neo4j only applies AUTH on a fresh volume — see NExtSTEPS.md
  §2b).
- **`NEXTSEEK_INTERNAL_BASE_URL` vs `NEXTSEEK_BASE_URL`:** the internal URL
  is fixed `http://127.0.0.1:8000` (container-internal) and must **not**
  derive from the host-published port; the public URL derives from
  `NEXTSEEK_HOSTNAME`+port. Both templates carry the warning; `doctor`
  checks for a stale hand-maintained overlay missing the guard.
- **Template gap (known drift, tracked as Step 7b):** ~16 Container-CC env
  keys read by `nextseek_api/cc_assistant/` (e.g. `NEXTSEEK_CC_IMAGE`,
  `NEXTSEEK_CC_NETWORK`, `DMAC_BEDROCK_PROXY_URL`,
  `NEXTSEEK_CC_MAX_BUDGET_USD`, `NEXTSEEK_CC_TIMEOUT_SECONDS`,
  `NEXTSEEK_SIDECAR_HOST/PORT`, `DMAC_CC_MEMORY_*`) are absent from the
  render template and fall back to safe in-code defaults (budget default:
  **$0.50/turn**). A fresh install works, but if you need to tune these,
  add them to `docker/nextseek.env` by hand. Two settings-level knobs
  (`CC_PERSIST_STRICT`, `CC_TRANSCRIPT_MAX_BYTES`) must be set as plain
  attributes in `dmac/local_settings.py` if needed.
- `SEEK_PUBLIC_URL` must agree in three places (`startup/.instance.json`,
  `docker/nextseek.env`, SEEK's DB `site_base_host` row); `doctor` detects
  drift but never auto-fixes it.

---

## 9. Security invariants (Container-CC / OI-3) — never regress these

The per-turn CC agent container is sandboxed. These invariants are enforced
by tests and were live-verified; any deploy that would weaken one is wrong by
definition:

1. **Zero shared credentials in the agent env.** The agent gets only:
   Bedrock-via-proxy pointers, per-request SEEK user credentials, sidecar
   host/port, non-secret path mappings. The 16 forbidden shared-cred keys
   (AWS/Bedrock token, Neo4j, MySQL, GCP, Anthropic) are enumerated in
   `nextseek_api/cc_assistant/tests/validate_cc_acceptance.py`; the env
   builder is `cc_engine.py` (`build_agent_environment`) — the single source
   of truth.
2. **Bedrock only via the proxy.** The proxy holds the token, allowlists the
   model, and must never log the token.
3. **Network segmentation:** `dmac-cc-net` members are a **closed set**:
   nginx (dual-homed), `dmac-bedrock-proxy`, `nextseek-sidecar`, and per-turn
   `dmac-cc-agent-<run_id>` containers. The `nextseek` service itself must
   **never** join `dmac-cc-net` (agents reach the app only through nginx).
   Note: `dmac-cc-net` is a normal bridge (not Docker `--internal`) — the
   isolation is credential-absence + the proxy allowlist, not egress
   blocking.
4. **Scratch-only writes:** agent mounts are read-only except
   `/data/scratch` and its own CC state; per-user subpaths keep users/projects
   disjoint.

Spot-check commands (read-only; enumerate env key *names*, never values):

```bash
# proxy contract, from a container ON dmac-cc-net (unsigned, like the agent):
docker run --rm --network dmac-cc-net --entrypoint sh dmac-assistant:poc -c '
  B=http://bedrock-proxy:8080
  curl -s -o /dev/null -w "healthz=%{http_code}\n" $B/healthz'          # 200
docker logs dmac-bedrock-proxy 2>&1 | grep -c -E "ABSK|Authorization"   # 0

# per-container env key names:
docker inspect nextseek-sidecar --format '{{range .Config.Env}}{{println .}}{{end}}' | cut -d= -f1
docker inspect dmac-bedrock-proxy --format '{{range .Config.Env}}{{println .}}{{end}}' | cut -d= -f1
```

Full zero-spend re-verification of a recorded acceptance run — on the host,
from the repo root (`python3`; the module is stdlib-only):
`python3 -m nextseek_api.cc_assistant.tests.validate_step7_compose_deploy <run_dir>`
(61 checks: topology, de-credentialing, closed-set network membership,
cross-user isolation, plugin-ops matrix).

---

## 10. Known gotchas

- **`docker cp` fixes are ephemeral** (Golden rule 1). Rebuild.
- **`cc-agent` is not a runnable service.** Its compose stanza exists only so
  `docker compose build` produces `dmac-assistant:poc`. Never add `restart:`,
  ports, or networks to it.
- **Rebuild ≠ collectstatic.** Static-asset changes need an explicit
  `collectstatic` after the rebuild (the entrypoint's boot-time collectstatic
  covers the new image's own assets, but hand-check when in doubt).
- **nginx resolves `nextseek` dynamically** (Docker DNS, 10s TTL) so an app
  recreate does not 502 through a stale IP; nginx itself rarely needs
  recreation.
- **SEEK boot order matters:** `seek` must create the filestore tree before
  `seek_workers` starts (the startup CLI sequences this; if bringing services
  up by hand, start `seek` first).
- **schema_rag ingest fetches caller-supplied URLs verbatim** (no rewrite to
  the internal URL, no SSRF guard) — from inside the container, the app's own
  *public* URL is typically unreachable (NAT hairpin); ingest against the
  internal URL. Tracked as GitHub #19.
- **Two BAML clients** are generated at build time in two different images
  (root Dockerfile: router client; cc-runtime: judge client) — do not assume
  one covers the other.
- **`docker/cc-runner/` is dead weight** (an unused lean proof image) — not
  part of the build graph; do not wire it anywhere.
- **`docker/cc-runtime/container/CLAUDE.md` is generated.** Never hand-edit;
  refresh via `python -m build_tools.ingest_nextseek_docs`.

---

## 11. Disk, logs, and backups

- **Images & rollback tags** are the largest disk consumers (the app image
  alone is ~8 GB; each rollback tag pins layers). Treat tags as backups
  (Golden rule 9): inventory with `docker images`, and prefer pushing old
  tags to a private registry / `docker save` archive over deleting them.
- **Docker build cache** grows unbounded across rebuilds (tens of GB within
  weeks). `docker system df` shows it; reclaiming it (`docker builder prune`)
  is safe for *cache* (unlike images/volumes) but still coordinate with the
  box owner — a cold cache makes the next build slow, and on shared boxes a
  running build may be using it.
- **Container logs:** Docker's default `json-file` driver with **no rotation
  configured** grows without bound and is deleted on container recreate. For
  long-lived deployments set rotation (daemon-wide `log-opts` `max-size`/
  `max-file`, or per-service compose `logging:` options).
- **App logs:** `./logs/` is bind-mounted into the container
  (`logs/django.log`, root-owned) and grows on the deploy host; rotate or
  clear deliberately.
- **Database backups:** the §5.3 mysqldump gate covers deploys; scheduled
  backups (MySQL dumps, Neo4j exports, SEEK filestore) are the operator's
  responsibility and should land on storage **off the deploy host** — see
  NExtSTEPS.md's backup section for what to dump.

---

## Appendix: paid acceptance & evidence bundles

The full greenfield acceptance bar ("every shipped plugin op works live
through the real stack") is paid and gated:

```bash
# native assistant regression baseline (paid, gated):
docker exec -e RUN_REALSTACK=1 -e SEEK_TEST_USER=<u> -e SEEK_TEST_PASS=<p> nextseek sh -lc \
  'cd /app && uv run python manage.py test nextseek_api.assistant.tests.test_granular_realstack \
   --settings=dmac.test_settings_realstack --noinput --keepdb -v2'

# Container-CC route end-to-end (paid, gated):
docker exec -e RUN_REALSTACK=1 -e SEEK_TEST_USER=<u> -e SEEK_TEST_PASS=<p> nextseek sh -lc \
  'cd /app && uv run python manage.py test nextseek_api.cc_assistant.tests.test_cc_realstack \
   --settings=dmac.test_settings_realstack --noinput -v2'
```

Both are skipped unless `RUN_REALSTACK=1`. Evidence bundles produced by
acceptance runs are re-verifiable forever at zero spend:

```bash
# in-container (no bare `python` on the image PATH — use `uv run --no-sync`,
# which executes in the app env /app/.venv):
docker exec nextseek uv run --no-sync python -m nextseek_api.cc_assistant.tests.validate_cc_acceptance outputs/cc_acceptance/<run_id>
# on the host, from the repo root (stdlib-only module):
python3 -m nextseek_api.cc_assistant.tests.validate_step7_compose_deploy <run_dir> [repo_root]
```

Bundles are real artifacts from real runs — "markdown is never proof"
(`nextseek_api/cc_assistant/tests/acceptance_evidence/step7/README.md`).
