# NExtSEEK deployment & operations runbook

This is the **authoritative deployment-hygiene document** for NExtSEEK. It is
written for *any* operator (a human or a coding agent) deploying or
operating the stack on *any* host: the current dev server, a production
server, or a brand-new machine. It is deliberately procedural: CI runs on every
push to `dev` and `main` and on every pull request
([`ci/README.md`](ci/README.md)), but deploys are still manual, so **this
document is the deploy pipeline**. Follow it exactly and in order.

Related docs (each has a distinct job; don't cross-purpose them):

| Doc | Job |
|---|---|
| [`README.md`](README.md) | 5-minute localhost quick start |
| [`NExtSTEPS.md`](NExtSTEPS.md) | production hardening: credential rotation, TLS, DEBUG, backups |
| [`NessieAI/docs/architecture.md`](NessieAI/docs/architecture.md) | deep reference for the assistant: services, data flows, security model |
| [`NessieAI/cc/DEPLOY.md`](NessieAI/cc/DEPLOY.md) | Container-CC subsystem specifics + paid acceptance gates |
| [`startup/README.md`](startup/README.md) | `./startup.sh` subcommand reference + known failure modes |
| [`ci/README.md`](ci/README.md) | what CI runs, and what can fail a job |
| [`NessieAI/tests/README.md`](NessieAI/tests/README.md) | every AI test lane, including the paid acceptance runs |

---

## 0. Topology at a glance

One `docker-compose.yml` at the repo root defines the whole stack:
**12 services, 3 networks, and 10 named volumes**. Seven volumes are external;
three are Compose-managed: the attribute Celery SQLite broker, the batch-upload
queue state and `nextseek-media` (`/media`, shared by `nextseek` and `web`).
No service is profile-gated: `docker compose ps` shows the whole stack, and
`docker compose up -d` starts the whole stack.

| Service | Image | Host port | Network(s) | Role |
|---|---|---|---|---|
| `nextseek` (the worker) | built from root `Dockerfile` | none | default | The background runtimes that run app code, no web server: Celery worker for `batch_upload`, Celery worker for `attribute_mutations`, the beat, the outbox dispatcher, the sync-recovery loop, the assay-registration and share drains and the graph sync. It is the only service that runs `migrate`, and it holds no Docker socket. One `wait -n`, so any of them exiting restarts the container. Healthchecked by `docker/scripts/attribute_runtime_healthcheck.py app`. Keeps the name `nextseek`, so every `docker exec nextseek ...` works |
| `web` | the `nextseek` image (no second build) | none (internal :8000) | default + laya-net + dmac-spawner-net | The web server (daphne, or gunicorn via `NEXTSEEK_SERVER`) and the chat turn. No `container_name`: compose names it `nextseek-web-N`, so a rebuild can run two and swap them (§3.3). Holds the Docker socket for the one dev cycle of the spawner switch, the Luria key and ssh volume, and `dmac-cc-users`. Healthcheck: `GET /login/` answers 200. `stop_grace_period: 5m` |
| `cc-spawner` | built from `docker/cc-spawner/` | none | dmac-spawner-net (internal) | Starts Container-CC turns and runs the SEEK admin scripts for `web`, behind a token. The only other holder of the Docker socket; read-only, all capabilities dropped. `web` uses it unless `NEXTSEEK_SPAWNER_URL` is set empty in the project-root `.env` |
| `nextseek_nginx` | `nginx:${NGINX_IMAGE_TAG:-1.31.2}` | `127.0.0.1:${NEXTSEEK_PORT:-8000}` | default **+ dmac-cc-net** | static files + reverse proxy to the `web` service (or, during a swap, to the one container `docker/nginx-upstream/pin.conf` names); the **only dual-homed** service |
| `db` | `mysql:8.0` | `127.0.0.1:3306` | default | two schemas: `dmac` (NExtSEEK) + `seek_production` (SEEK) |
| `neo4j` | `neo4j:${NEO4J_IMAGE_TAG:-2026.05.0}` | `127.0.0.1:7474` / `:7687` | default | sample/assay graph |
| `seek` / `seek_workers` | `fairdom/seek:1.15.1` | `127.0.0.1:3000` / none | default | upstream SEEK Rails app + delayed-job workers |
| `solr` | `fairdom/seek-solr:8.11` | none | default | SEEK search index |
| `bedrock-proxy` | built from `NessieAI/docker/bedrock-proxy/` | none | dmac-cc-net | holds the Bedrock token; model-allowlist auth proxy (container name `dmac-bedrock-proxy`) |
| `nextseek-sidecar` | built from `NessieAI/docker/ns-sidecar/` | none | dmac-cc-net | NS sidecar for the CC agent (healthchecked) |
| `cc-agent` | built from `NessieAI/docker/cc-runtime/` → `dmac-assistant:poc` | none | none | **build-target only**: never runs as a service; per-turn agent containers are spawned from this image by the app via the docker socket |

Key facts every operator must internalize:

- **App code is baked into the `nextseek` image** (`COPY . /app/`). Changing
  `nextseek_api/`, `NessieAI/`, `seek/`, `dmac/`, templates, etc.
  **requires an image rebuild**. Only these are bind-mounted at runtime and
  changeable without a rebuild: `themes/NextSeek/`, `dmac/local_settings.py`,
  `./outputs/`, `./logs/` (plus the docker socket and the `dmac-cc-users`
  volume).
- Secrets **never** enter images: `.dockerignore` excludes `docker/db.env`,
  `docker/nextseek.env`, `NessieAI/docker/bedrock-proxy/proxy-secret.env`,
  `dmac/local_settings.py`. They are injected at runtime via compose
  `env_file:` / bind mount.
- The entrypoint (`docker/scripts/entrypoint.sh`, modes `worker` and `web`) is
  **fail-fast**: `collectstatic` (web), a bounded DB-readiness probe, and
  `migrate --noinput` (worker) each abort the container on failure (markers
  `[COLLECTSTATIC-FAILED]`, `[DB-UNREACHABLE]` in `docker compose logs web`,
  `[MIGRATE-FAILED]` in `docker logs nextseek`). A crash-looping container after a deploy means one
  of those failed. **Never** "fix" a migration wedge with `migrate --fake`.
- `NEXTSEEK_SERVER` selects the web server: `gunicorn` (WSGI, **the deployed
  norm**) or `daphne` (ASGI/WebSocket, **the code default when unset**). Set
  `NEXTSEEK_SERVER=gunicorn` explicitly in `docker/nextseek.env` on every
  deployment unless you have decided otherwise; under gunicorn the chat UI
  uses HTTP polling for progress (no WebSocket).
- The `web` container is capped at `${NEXTSEEK_MEMORY:-8G}` of RAM and the
  `nextseek` worker at `${NEXTSEEK_WORKER_MEMORY:-8G}`, with no swap beyond
  either (`deploy.resources.limits.memory` and `memswap_limit` in
  `docker-compose.yml`). A request that outgrows the cap has its process killed
  inside the container instead of exhausting the host. At rest web holds about
  3.7 GiB and the worker about 5.3 GiB, so set the caps in the project-root
  `.env` well above that; during a swap two web containers overlap for one to
  five minutes. A changed cap takes effect when the service is recreated.
- The `seek` container is capped the same way at `${SEEK_MEMORY:-4G}`. SEEK's
  puma workers grow with the requests they serve and never hand the memory
  back: one reached 10 GiB on a workstation on 2026-09-16 and left the host
  with nothing free. The kernel kills a runaway worker inside the container
  instead, and puma respawns it. Raise `SEEK_MEMORY` in the project-root
  `.env` on a box serving real SEEK traffic; the cap takes effect when `seek`
  is recreated. The image starts one single-threaded puma worker per CPU unless
  told otherwise, so compose sets `PUMA_WORKERS_NUM` from `${SEEK_PUMA_WORKERS:-4}`
  and `MALLOC_ARENA_MAX=2` on both SEEK services. Fewer workers alone does not
  keep the sum under the cap: under load each worker still grows, and a cgroup
  sitting at its cap stalls every process in memory reclaim (SEEK answered nothing
  for 7 minutes on a dev box, 2026-10-12). So the `seek` command also recycles
  any puma worker over `${SEEK_WORKER_MAX_MB:-2500}` MB of RSS: it gets SIGTERM,
  puma forks a fresh one at once, and the old one has puma's
  `worker_shutdown_timeout` (30 s) to finish its request before puma SIGKILLs
  it, so a request still running 30 s after a recycle (a long blob download) is
  cut off. `docker logs seek` shows each `recycling puma worker` line. Set
  `SEEK_MEMORY`, `SEEK_PUMA_WORKERS` and `SEEK_WORKER_MAX_MB` together, per box,
  so that workers x `SEEK_WORKER_MAX_MB` plus about 0.5 GB for the master stays
  well under `SEEK_MEMORY`, leaving room for one request's peak: 8 x 2500 MB
  under 24G, 6 x 2500 MB under 20G. At the compose defaults (a 4G cap) the cap
  is reached before any worker passes 2500 MB, so the recycler never acts there.
  The users admin API writes through a fresh `bin/rails runner`
  in this container, so near the cap that is killed too: the write answers 502
  and its detail says the runner exited 137.
- The `neo4j` container is capped at `${NEO4J_MEMORY:-6G}`, its heap and page
  cache are sized explicitly (`NEO4J_HEAP`, `NEO4J_PAGECACHE`, 2G each), and a
  single transaction may allocate at most `${NEO4J_TRANSACTION_MAX:-1g}`. Before
  2026-09-16 none of these were set: the JVM sized its heap from host RAM, and
  one graph sync transaction at schema 1.2 needed over 512 MiB, which on this
  service would have grown unchecked. A transaction that asks for more is now
  killed and reported to its caller instead. Raise the bounds together on a box
  with a larger graph; the caps take effect when `neo4j` is recreated.
- Pulled images are pinned, so a rebuild never moves a version. `NGINX_IMAGE_TAG` defaults to `1.31.2` (what prod
  runs; dev was on `1.31.1` and moves to it at its rebuild, set `NGINX_IMAGE_TAG=1.31.1` in dev's `.env` to stay).
  `NEO4J_IMAGE_TAG` defaults to `2026.05.0`, the version both boxes run, so the store format does not change. The
  Container-CC runtime and the app image copy `uv` from `ghcr.io/astral-sh/uv:0.12.8` (no download piped to a shell).
- `NEO4J_PROCEDURES_ALLOWLIST` now defaults to the read-only list in `.env.example` instead of `*`. A box that
  loads APOC (`NEO4J_PLUGINS`) gets the read-only families the graph agent uses; neither box loads APOC today.
- CI runs a `mariadb:10.5` service for the MariaDB-fixture tests (`SPIKE_DB_*`); the dev compose `db` stays `mysql:8.0`.
- Every other long-lived service carries a cap too: `seek_workers`
  (`${SEEK_WORKERS_MEMORY:-6G}`), `solr` (`${SOLR_MEMORY:-2G}`), `db`
  (`${SEEK_DB_MEMORY:-6G}`) and `nextseek-sidecar` (`${SIDECAR_MEMORY:-1G}`).
  Capping only some services moves the kill rather than preventing it: on
  2026-09-16 `seek` was capped and `seek_workers`, which is the same Rails
  process with the same leak, was not. The two together held 11.2 GiB across 24
  `bundle` processes, the host ran out at 29.5 GiB resident, and the kernel
  killed the operator's desktop instead of a worker.
- **The defaults are sized for the smallest box we run, and they do not add up
  to any host.** They bound a single runaway; they do not bound the sum. Every
  box sets its own values in the project-root `.env`, and a box whose total must
  fit lowers `NEXTSEEK_MEMORY` first (web holds about 3.7 GiB at rest). The
  defaults total 45G. A 30 GiB workstation sets `NEXTSEEK_MEMORY=4G`,
  `SEEK_DB_MEMORY=3G` and `SEEK_WORKERS_MEMORY=3G`, which totals 28G. Each cap takes effect when its own
  service is recreated.
- Asynchronous attribute mutations use Celery's SQLAlchemy transport over
  SQLite at `/var/lib/attribute-broker/broker.sqlite3`. The worker and outbox
  dispatcher share the named `attribute_mutation_broker` volume. Routine
  `./startup.sh rebuild` recreation preserves and reattaches it; image rollback
  tags and GHCR do **not** contain or back up broker data. `./startup.sh reset`
  deletes the queue volumes (this broker among them) and keeps the data
  volumes; `reset --drop-data` deletes those too.

---

## 1. Golden rules (deployment hygiene)

1. **Deploy only committed code from `origin/dev` or `origin/main`** (§3 says
   which). Never hot-patch a
   running container: `docker cp` into a container is **ephemeral**; it is
   silently lost on the next recreate, and it makes the running system
   diverge from git. If you are testing an ephemeral patch, say so out loud,
   and rebuild the image before calling anything done.
2. **Take a rollback tag before every image rebuild** (§5). The rebuild CLI
   creates and identity-verifies these tags before building and aborts if it
   cannot. An unverified rollback plan is not a rollback plan.
3. **mysqldump gate:** before any deploy whose commit range includes a Django
   migration, dump the affected table(s) (or the `dmac` schema) first (§5.3).
4. **Scope your recreation.** Recreate only the service(s) whose image or
   config actually changed, normally `--no-deps nextseek web`. Do not touch
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
   A prod deploy that changes `router.baml`, the selection or pipeline-agent
   prompts, an nf-core atlas, an `nfcore` template or a reingest map needs a
   fresh paid pipeline-scenarios run
   (`NessieAI/tests/pipeline_scenarios/README.md`) with no FAIL and at least as
   many PASSes as the last accepted run, once one exists, approved by the owner.
   CI does not enforce this; the deployer checks it.
9. **Pruning is not cleanup: it destroys backups.** Rollback tags and cached
   layers on the deploy host are the rollback path. Get the owner's explicit
   approval per item before removing images, tags, or volumes.

---

## 2. Greenfield install (a new box)

### 2.1 Prerequisites

- **Docker Engine ≥ 26** (API 1.45+, required for the volume *subpath*
  mounts the sidecar uses) and **Compose plugin ≥ 2.26**. Check:
  `docker version` and `docker compose version`.
- [`uv`](https://docs.astral.sh/uv/) on PATH (the startup CLI runs under it).
- Disk: plan for **≥ 60 GB free** for a comfortable single instance
  (~19 GB of images, seed volumes, plus Docker build cache which grows into
  tens of GB over repeated rebuilds; see §11).
- RAM: ≥ 8 GB free. Do not co-host a second full instance on a small box.
- Outbound network for: Docker Hub / GHCR pulls, the S3 filestore seed
  (~215 MB), and (first use only) Hugging Face model downloads (§2.4).

### 2.2 Procedure

```bash
# 1. Clone the deploy branch (dev, or main where §3 says so)
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
`NessieAI/docker/bedrock-proxy/proxy-secret.env`, root `.env`) → create the 7 external
volumes → seed MySQL + Neo4j from committed dumps (idempotent; skipped if
populated) → **build and start everything, including the CC services and the
`dmac-assistant:poc` agent image** → verify demo users → health checks. The
SEEK filestore blobs (~215 MB) are fetched from S3 and streamed in.

```bash
# 4. Hand-fill the rendered docker/nextseek.env (placeholders ship as
#    SET_IN_LOCAL_ENV; chat/LLM features stay disabled until filled):
#      GCP_API_KEY=...                  # Gemini (memory summarizer etc.)
#      AWS_BEARER_TOKEN_BEDROCK=...    # ALSO here: separate consumer from
#                                       # the proxy's copy, see §8
#      FDH_API=...                      # FAIRDOMHub, if used
#    And pin the server mode to the deployed norm:
#      NEXTSEEK_SERVER=gunicorn

# 5. Apply the env changes:
docker compose up -d --no-deps --force-recreate nextseek web

# 6. Verify:
./startup.sh doctor          # 13 read-only checks (4 prereq + instance
                             # state + 8 health), non-zero on failure
#    ...then run the full checklist in §6.
```

**7. Before exposing to anyone you don't trust:** work through
[`NExtSTEPS.md`](NExtSTEPS.md): rotate `demo`/`user` passwords, MySQL and
Neo4j credentials, ensure `DJANGO_DEBUG` stays **unset** (the code enables
debug only for `1`, `true` or `yes`, case-insensitive and whitespace-stripped;
anything else, including absent/empty/`false`, is debug-off), configure
`ALLOWED_HOSTS`/CSRF, and put a TLS-terminating reverse proxy in front
(nginx here serves plain HTTP on `127.0.0.1:${NEXTSEEK_PORT:-8000}`; TLS is
out of scope of this repo).

### 2.3 What install does **not** provision

The two embedding models are not on this list: the app image bakes both
(schema_rag's `BAAI/bge-small-en-v1.5` and the semantic shortlist's
`sentence-transformers/all-MiniLM-L6-v2`) and runs them offline, so install and
every rebuild bring them, and nothing downloads at run time. The build reaches
Hugging Face for them whenever the models stage is not in the build cache; a
model that will not load stops the build before anything restarts. Pins and how
to bump one: `docker/README.md` "Embedding models".

Budget these as explicit post-install steps:

- **Solr index.** The `seek-solr-data` volume starts empty; this repo's CLI
  has no reindex step. If search returns nothing, reindex from the SEEK side
  (upstream SEEK tooling).
- **TLS / public exposure.** See NExtSTEPS.md §5.
- **Backups.** See §11 and NExtSTEPS.md.

### 2.4 Known greenfield sharp edges

- **`--instance` multi-instance support does not cover the CC subsystem.**
  Core volumes/containers/ports are correctly prefixed per instance, but
  `dmac-cc-users` (volume), `dmac-bedrock-proxy` + `nextseek-sidecar`
  (container names), and `dmac-cc-net` (network) are pinned literals: a
  second instance on the same box will collide with (or silently share CC
  user data with) the first. One CC-enabled instance per box until this is
  fixed.
- Re-running `install` (or `reset --keep-config`) **keeps every value already
  in `docker/db.env` and `docker/nextseek.env`**: the secret key, rotated
  passwords (install's own database steps log in with them), filled API keys,
  the `NEXTSEEK_SERVER=gunicorn` pin and hand-added CC knobs. Keys the template
  does not name follow under a "Kept from the previous ..." header. It
  re-renders only a value naming a pre-NessieAI path, a `forbidden` key in
  `startup/env_registry.toml`, a placeholder, and its own port-derived values
  when the port moved; step 4 prints both lists by name. It cannot tell an old
  template default from a hand edit, so a template default changed later
  reaches an existing box only by hand. A password holding `$` must be
  single-quoted (`KEY='...'`), or install stops before writing anything. An
  existing `dmac/local_settings.py` is left alone; the template's version goes
  beside it as `dmac/local_settings.py.new` to diff by hand. `reset` without
  `--keep-config` still starts every file fresh, carrying only the SEEK URL,
  `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` across.

---

## 3. Redeploying a code change (existing box)

This is the routine "ship a change" procedure, the manual CI/CD stand-in.

**Production deploys from:** `origin/dev` or `origin/main`. `dev` is the most up to date,
and `main` is synced from `dev`. This line is the one record of it; other docs link here.

### 3.1 Standard procedure

```bash
cd <deploy-clone>                      # the clone the images are built from

# 1. Sync to the exact commit being deployed (fast-forward only). <branch> is
#    dev or main ("Production deploys from", above):
git fetch origin <branch>
git log --oneline HEAD..origin/<branch>     # review what you are about to ship
git merge --ff-only origin/<branch>

# 2. Pre-deploy gate:
#    a. Migration check: does the range add migrations?
git diff --name-only HEAD@{1} HEAD -- '*migrations*'
#       If yes: mysqldump gate first (§5.3).
#    b. CI check: `rebuild` runs the smoke suite afterwards and it needs three
#       things on the box. On an install predating them, add them by hand:
#         · "ci_profile": "dev"  (or "prod") in startup/.instance.json. An absent
#           key means prod, the most restrictive, so a dev box silently loses the
#           routes only dev may call. A fresh `install --ci-profile dev` sets it.
#         · ~/.config/nextseek/ci.env (mode 600) naming CI_SMOKE_USER and
#           CI_SMOKE_PASS. Without it the readiness gate exits 2 and the rebuild
#           reports failure after having succeeded.
#         · DJANGO_CSRF_TRUSTED_ORIGINS in docker/nextseek.env must include the
#           loopback origins the suite posts from, http://127.0.0.1:<nextseek port>
#           and http://localhost:<nextseek port>, alongside the public https ones.
#           nginx forwards Host: localhost on that port, so Django cannot match a
#           browser's Origin against its own host and falls back to this list;
#           without them every scripted request passes and the six browser flows
#           fail with a 403 on the login form (measured on fairdata-dev,
#           2026-09-02). An env change takes effect on recreate, which rebuild does.
#         · On a box declaring local or dev, an app rebuild (and ./startup.sh ci)
#           also runs the Nessie lane, which sends four real chat turns (about
#           $0.30) and needs two more (ci/smoke/README.md, "Nessie lane"):
#           CI_WRITE_USER / CI_WRITE_PASS in ci.env for a superuser that is a
#           member of a SEEK project listed in ASSISTANT_PARTICIPATING_PROJECTS
#           (dmac/local_settings.py), and a non-empty AWS_BEARER_TOKEN_BEDROCK in
#           NessieAI/docker/bedrock-proxy/proxy-secret.env. Startup checks the token, the
#           first-party images and the CC services before the suite starts and
#           stops naming the missing one; the lane's first tests check the
#           account. A prod box never runs the lane.
#       `./startup.sh doctor` reports the first two, read-only. Or skip CI:
#       rebuild --no-ci. Or skip only the Nessie lane: rebuild --no-nessie.
#    c. SEEK's config mount: docker/seek-nginx.conf is an untracked host file  (`./startup.sh install` now creates it)
#       that SEEK's entrypoint (uid 33) rewrites on every start. It must exist as
#       a FILE (chmod 666) before the seek service is ever recreated; if it is
#       missing, Docker creates a directory there and SEEK crash-loops. Render it
#       with the image's own template if absent:
#         docker run --rm fairdom/seek:1.15.1 sh -c 'cd /seek && SEEK_LOCATION= \
#           SEEK_SUB_URI= envsubst "\${SEEK_LOCATION} \${SEEK_SUB_URI}" \
#           < docker/nginx.conf.template' > docker/seek-nginx.conf
#       Never `compose up` a single service without --no-deps on this stack.

# 3. Rebuild exactly what changed. Each verb first creates+verifies local
#    rollback tags, then builds, recreates long-running targets with
#    --no-deps --force-recreate, and attempts the §5.2 GHCR baseline:
./startup.sh rebuild                               # app cohort (common case); swaps web without a gap (§3.3)
./startup.sh rebuild --stop-first                  # the same, but stop and recreate web (about a minute of the restart page)
./startup.sh rebuild --component cc-spawner        # docker/cc-spawner/**
./startup.sh rebuild --component cc-agent          # NessieAI/docker/cc-runtime/**
./startup.sh rebuild --component bedrock-proxy     # NessieAI/docker/bedrock-proxy/**
./startup.sh rebuild --component nextseek-sidecar  # NessieAI/docker/ns-sidecar/**
./startup.sh rebuild --component custom-stack      # every first-party image
#    cc-agent is build-only: the next chat turn uses the new image; there is no
#    persistent agent container. The app cohort is the two services on the app
#    image, `nextseek` (the worker) and `web` (§0).
#    The worker and dispatcher reattach the existing
#    attribute_mutation_broker SQLite volume; rebuild does not remove or renew
#    that volume.

# 4. Watch the boot (entrypoint runs collectstatic → DB probe → migrate):
docker compose logs -f web     # until gunicorn workers are up; no FAILED markers (the worker: docker logs -f nextseek)

# 5. Run the §6 verification checklist. Record results.
```

A change to `docker/nginx.conf` alone does not reach nginx: it is a single-file bind
mount that a `git pull` orphans, so the running `nextseek_nginx` keeps the old file.
`./startup.sh rebuild` compares the served file with the checkout's and prints
"nginx serves an old config; run ./startup.sh rebuild --recreate-nginx" when they differ
(`doctor` shows it red; if nginx is down or `docker exec` fails, it says it could not
check). It never recreates nginx on its own. `--recreate-nginx` runs
`docker compose up -d --no-deps --force-recreate nextseek_nginx` as its own step, then
checks the app (200) and SEEK (200/302, container start time unchanged). The deploy
skill's runner passes it when the range changes `docker/nginx.conf` and the brief
allows `nginx recreate ok`.

### 3.2 What change needs what action

Every command in this table measures free disk before it builds anything, and
prints what it found. Above the floor for this box's `ci_profile` (20 GB on
local and dev, 30 GB on prod and on a box with no profile set) that is one line
and nothing else. Below it, the rebuild stops and opens an interactive review of
what may be deleted: old rollback tags behind the current rollback point, local
registry baseline tags, dangling layers, and the build cache, in that order.
Nothing is ever deleted without an answer, no volume is reachable from it, and
the frozen `baseline-20260805` and every image a container is using are never
offered. `--no-disk-check` keeps the measurement and skips the gate;
`--disk-floor N` overrides the floor. A run with no terminal prints the plan and
exits non-zero rather than blocking on a question nobody can answer.

| You changed | Required action |
|---|---|
| Python / templates / anything baked (`nextseek_api/`, `NessieAI/chat_nextseek/`, `seek/`, `dmac/` except `local_settings.py`) | `./startup.sh rebuild`: rebuild the shared app image, recreate the `nextseek` worker and swap `web` (§3.3). No `COMPOSE_PROFILES` to remember: until 2026-09-02 four workers lived in their own profile-gated services, and a rebuild without the variable exported left them running old code under `restart: unless-stopped`, looking healthy |
| `static/` assets | rebuild + recreate, **then** `docker compose exec web uv run manage.py collectstatic --noinput` |
| `NessieAI/chat_frontend/` React source | `npm run build:embedded` in `NessieAI/chat_frontend/`, commit the emitted assets, then rebuild + recreate + collectstatic |
| `NessieAI/docker/cc-runtime/**` (agent plugin/skills/CLAUDE.md/deps) | `./startup.sh rebuild --component cc-agent`: next turn uses it; no service restart. Also the recovery command when `dmac-assistant:poc` has been pruned: a first build with no rollback source is announced and allowed, not refused |
| The canonical context files in `NessieAI/chat_nextseek/src/chat_nextseek/context/` (`capabilities.md`, `projects_db.json`, the four `min_*.json`, `ONTOLOGIES.json` and the `ontology_*.json` files; list in `startup/lib/layout.py`) | both `./startup.sh rebuild` and `./startup.sh rebuild --component cc-agent`: the agent image bakes its own copy through the compose named context `chat_nextseek`. Skip the second and every rebuild's stack health fails `cc-agent context`, naming the files, until it is run |
| `NessieAI/dmac_assistant/baml_src/**` (BAML prompts and schemas) | both `./startup.sh rebuild` (the router client in the app image) and `./startup.sh rebuild --component cc-agent` (the judge client; the agent image reads this tree through the compose named context `dmac_assistant_baml`) |
| `docker/nextseek.env` / `dmac/local_settings.py` (config only) | no build: `docker compose up -d --no-deps --force-recreate nextseek web` |
| `NessieAI/docker/bedrock-proxy/**` or its secret env | `./startup.sh rebuild --component bedrock-proxy` |
| `NessieAI/docker/ns-sidecar/**` | `./startup.sh rebuild --component nextseek-sidecar` |
| More than one first-party image family | run each affected component, or `--component custom-stack` |
| A Django migration (in the range) | mysqldump gate (§5.3) → rebuild + recreate (migrate runs at boot) → verify with `showmigrations` |

### 3.3 Swapping the web server (no gap)

`./startup.sh rebuild` replaces `web` without a gap by default; `--stop-first` keeps
the old stop-and-start. Steps (`startup/lib/web_swap.py`):

1. Build, then check the NEW image in a throwaway container: `manage.py migrate --check`.
   A pending migration prints why and falls back to stop-first on its own (old code must
   not run on a new schema); users then see the restart page for about a minute and
   running chat turns get up to 240 s to finish (`NEXTSEEK_DRAIN_SECONDS`). Stop-first
   recreates `web` alone first, so the old one has drained and stopped before the worker
   is recreated and migrates; the new one waits in its entrypoint for the migrations.
2. Recreate the `nextseek` worker (background jobs pause for its boot; users do not see it).
3. Pin nginx to the old web container (`docker/nginx-upstream/pin.conf`, then
   `nginx -t` and a graceful reload), start a second `web` beside it on the new image
   (`up -d --no-deps --no-recreate --scale web=2 web`), wait for its healthcheck
   (180 s), warm it (parallel `GET /login/`).
4. Pin nginx to the new container, `docker stop` the old one (its `stop_grace_period`
   lets running chat turns finish; a turn still running at the cap is ended with a plain
   "please ask again"), `docker rm` it, delete the pin.

The deploy that brings `0030_spill_json_fields` (the chat size guard) is such a deploy:
the migration changes no column, but it is unapplied, so `migrate --check` sends that one
deploy to stop-first. Plan its minute of the restart page.

Any failure before the switch removes the new container and the pin; the old one never
stopped serving. A pin left by a killed swap may name a container that is gone: `doctor`
reports it, and `rebuild` refuses to build or replace `web` until it is deleted and nginx
reloaded. If `docker compose ps web` shows two containers after an aborted rebuild, the
next rebuild refuses with the fix (stop and remove the older one, delete the pin file,
reload nginx).

**The first deploy of the split is a switchover, not a swap.** It creates `web`, turns
`nextseek` into the worker and needs nginx to mount the new upstream directory. `web`
sends Container-CC turns and the SEEK user scripts to `cc-spawner`, which the app rebuild
does not start, so build the spawner first, once per box:

```bash
./startup.sh rebuild --component cc-spawner
./startup.sh rebuild --recreate-nginx      # about a minute of the restart page
```

Every rebuild after it swaps. `rebuild` also writes the spawner token pair
(`NEXTSEEK_SPAWNER_TOKEN` in `docker/nextseek.env`, `SPAWNER_TOKEN` in
`docker/cc-spawner.env`) on a box that lacks it; both files stay out of git. The
spawner reads its Container-CC limits (`NEXTSEEK_CC_MAX_TURNS`, `_MEM_LIMIT`, `_NANO_CPUS`,
`_PIDS_LIMIT`, `_FSIZE_BYTES`, `_AUTOMODE_GCP`) from the project-root `.env`, not from
`docker/nextseek.env`: a box that overrides one in `nextseek.env` must copy it to `.env`.

Rollback of the spawner: `NEXTSEEK_SPAWNER_URL=` (empty) in `.env`, then recreate `web`, puts
Container-CC turns back on the direct Docker path. SEEK user create and patch always need the
spawner and answer 503 while it is down or the switch is empty.

### 3.4 If the deploy clone is owned by a different account

On shared servers the installed runtime checkout may be owned by a service
account or carry unrelated operator-owned files. Do not stash, discard, or
bake those files. Fast-forward that checkout to the exact deployed commit,
create a separate clean detached worktree at the same SHA, and use it only as
the image source. `--source-tree` accepts only the exact `origin/dev` commit
(`startup/lib/deploy_source.py`), so a box deploying `origin/main` can use it
only while `main` and `dev` point at the same commit, as they do right after a
sync; otherwise use §3.1:

```bash
git fetch origin dev
git worktree add --detach <clean-path> origin/dev
./startup.sh rebuild --source-tree <clean-path>
```

The CLI proves the source is clean and exactly `origin/dev`, proves the
runtime checkout is at the same commit, and refuses dirty runtime deployment
controls (`docker-compose.yml` and `startup/`). It builds from the clean tree
but recreates services from the installed runtime checkout, so `outputs/`,
`logs/`, local settings, and other existing bind mounts retain their original
host paths. A helper container acting as the owning uid may still be required
to fast-forward or invoke the service-account checkout; never edit or commit
from that checkout.

---

## 4. Config-only changes

`docker/nextseek.env`, `docker/db.env`, and `dmac/local_settings.py` are
runtime-injected, so **no rebuild**:

```bash
docker compose -p nextseek up -d --no-deps --force-recreate nextseek web
```

That recreates `web` in place: about a minute of the restart page, and running chat
turns get the drain (up to 240 s) first. Plan a config change like a `--stop-first` deploy.

Then §6. Remember the entrypoint reruns migrate/collectstatic on every start;
a config typo that breaks Django settings shows up as a crash-loop, and the
prior image + rollback tag are your way back.

### Host nginx restart page

The system nginx on each server (TLS on 80/443, vhost files under
`/etc/nginx/conf.d/`) answers a bare "502 Bad Gateway" while SEEK or the app
container restarts. `docker/host-nginx-restart-page.conf` gives it the same
page as the app's own nginx (a Reload link, no auto-refresh): status 502, text/html. It is not
picked up by any deploy; install it by hand, as root, once per server, on the
dev server first and on prod only after dev checks out.

What it changes: in every server block that includes it, a 502 the host nginx
makes itself (connection refused, the upstream container is down) gets the
page. In a location that also sets `proxy_intercept_errors on`, a 502 the
upstream sends gets the page too; every other upstream status passes through.
Set that line **only** in locations that proxy to `127.0.0.1:3000` (SEEK),
never in one that proxies to `127.0.0.1:8000`: the app sends real JSON 502s
that clients must see unchanged.

Install, from the deploy clone, on `<host>` (dev first):

```bash
sudo install -m 644 -o root -g root docker/host-nginx-restart-page.conf /etc/nginx/nextseek-restart-page.conf
sudo restorecon -v /etc/nginx/nextseek-restart-page.conf    # SELinux: httpd_config_t
sudo cp -a /etc/nginx/conf.d/<app-vhost>.conf /root/<app-vhost>.conf.bak
sudo cp -a /etc/nginx/conf.d/<seek-vhost>.conf /root/<seek-vhost>.conf.bak
```

Then edit both vhost files (`sudo -e`). In the HTTPS `server { ... }` block of
each (the one with `listen 443 ssl`, not the port-80 redirect block), add
directly under `server_name`:

```nginx
    include /etc/nginx/nextseek-restart-page.conf;
```

and add as the first line inside every `location` whose `proxy_pass` is
`http://127.0.0.1:3000`: `location /` in `<seek-vhost>.conf`, and on the dev
server also `location /fairdata/` in `<app-vhost>.conf`:

```nginx
        proxy_intercept_errors on;
```

Do not put the file in `conf.d/` (it is included at `http` level there, where
a named location is refused), and do not add the intercept line to any `:8000`
location.

Updating an installed server: `install.sh` refuses when the include already
exists, so an update copies the new snippet over the installed one (the vhost
files stay as they are), then checks and reloads. As root, on dev first:

```bash
sudo install -m 644 -o root -g root docker/host-nginx-restart-page.conf /etc/nginx/nextseek-restart-page.conf && sudo restorecon -v /etc/nginx/nextseek-restart-page.conf && sudo nginx -t && sudo systemctl reload nginx
```

The current snippet also answers a 504 the host nginx makes itself (a wedged
`web` that outlasts the host's proxy timeout) with the page. The app's own 504s
still pass through, because no `:8000` location sets `proxy_intercept_errors`.

Where the page's 502s are logged: the named location logs to its server
block's `access_log`. A server block with none at server level (each location
sets its own) logs them to the http-level `/var/log/nginx/access.log`, so a
restart window measured from a location's log looks shorter than it was. Read
that file for those 502s, or add an `access_log` at server level.

Check and reload:

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -sI https://<seek-host>/ | head -1     # still 200 or 302 with SEEK up
curl -sI https://<app-host>/  | head -1
```

To see the page, restart SEEK in a quiet minute (`docker compose -p nextseek
restart seek`) and load `https://<seek-host>/`: it shows "NExtSEEK is
not answering right now" until SEEK is back; Reload then shows SEEK again.

Rollback, on the same server. First check that nothing else changed in the
vhost files since the install (a `certbot --nginx` run or a hand edit would be
lost by copying the backups back): each diff must show only the `include` line
and the `proxy_intercept_errors` lines. If it shows more, delete those lines by
hand with `sudo -e` instead of copying.

```bash
sudo diff /root/<app-vhost>.conf.bak /etc/nginx/conf.d/<app-vhost>.conf
sudo diff /root/<seek-vhost>.conf.bak /etc/nginx/conf.d/<seek-vhost>.conf
sudo cp -a /root/<app-vhost>.conf.bak /etc/nginx/conf.d/<app-vhost>.conf
sudo cp -a /root/<seek-vhost>.conf.bak /etc/nginx/conf.d/<seek-vhost>.conf
sudo restorecon -v /etc/nginx/conf.d/<app-vhost>.conf /etc/nginx/conf.d/<seek-vhost>.conf
sudo nginx -t && sudo systemctl reload nginx
sudo rm /etc/nginx/nextseek-restart-page.conf
```

A later change to the page: edit both copies (the guard
`nextseek_api/tests/repo_guards/test_nginx_error_page.py` fails if their bodies
differ), then repeat only the `install`, `restorecon`, `nginx -t` and reload
steps.

---

## 5. Rollback

### 5.1 Mechanism

Rollback = repoint `:latest` at a known-good tag and recreate **without
building**:

```bash
docker image inspect nextseek-nextseek:pre-<name> --format '{{.Id}}'   # MUST succeed first
docker tag nextseek-nextseek:pre-<name> nextseek-nextseek:latest
docker compose -p nextseek up -d --no-build --no-deps --force-recreate nextseek web
```

This recreates `web` in place (about a minute of the restart page). For a gap-free
rollback check out the previous good commit and run `./startup.sh rebuild` there: it
rebuilds that commit's image and swaps `web` (§3.3). Going back to a commit from before
the split leaves `web` and `cc-spawner` running as orphans of the old compose file; run
`docker compose up -d --remove-orphans` after it.

Then §6. If the bad deploy applied a **data** migration, rolling back the
image does not roll back the data; that is what the §5.3 dump is for;
restoring it is a deliberate, owner-approved action, not part of routine
rollback. A deploy that applied `0025_cc_turn` is the case in point: reverse it
before you repoint the image (`NessieAI/cc/DEPLOY.md`, "Approach 1 (unit B)"),
or chats that ran a Container-CC turn cannot be deleted.

A deploy that applied `0030_spill_json_fields` (the chat size guard,
`nextseek_api/assistant/spill.py`) needs one step before you repoint: since then each
chat value over 256 KB sits in a file under `/media/chat_spill` and its row keeps a link,
which the code before the guard reads as data. Stop `web` and the worker so no turn
writes, then, still on the new image, put the values back in their rows:

```bash
docker compose -p nextseek stop web nextseek
docker compose -p nextseek run --rm --no-deps --entrypoint /app/.venv/bin/python nextseek manage.py chat_spill --inline --dry-run
docker compose -p nextseek run --rm --no-deps --entrypoint /app/.venv/bin/python nextseek manage.py chat_spill --inline
```

`--inline` refuses a column that would come back at 60 MB or more (MySQL refuses a
statement over 64 MB, the failure the guard exists for) and names each one on stderr
(`refused: <table>.<column> <id> would be <n> bytes inline`). A refused column keeps its
links: the old code reads each as `{"$spill": ..., "bytes": ...}` where the value was,
so that chat opens without its big parts, and a `results_history` or `progress` that is
one link as a whole is not a list to it (delete that chat first). Leave
`/media/chat_spill` in place either way: a later roll-forward reads the links again.

### 5.2 Tag conventions and their care

- `<local-repository>:pre-<timestamp>-<sha>`: per-deploy safety tag created
  and identity-verified automatically before the rebuild CLI replaces an app,
  agent, sidecar, or proxy image.
- A long-lived known-good baseline tag (historically
  `nextseek-nextseek:dev-rollback`) should always exist on a deploy host.
  **If you find a host with no baseline tag** (it has happened: tags have
  been lost to disk cleanups), create one immediately from the current
  known-good image before doing anything else:
  `docker tag nextseek-nextseek:latest nextseek-nextseek:baseline-<YYYYMMDD>`.
- **Tags are backups. Verify they exist before you rely on them**:
  `docker image list` tags have historically been lost to well-intentioned
  disk cleanups. Any rollback script must *fail loudly* (`set -e`) if its
  source tag is missing, otherwise it will silently "roll back" onto the
  current image.
- **Off-box copies:** local tags die with the host (or with a prune). For
  retention, push tags to a **private** registry (e.g. GHCR) or `docker save`
  them to off-box storage. **Never push a `docker commit` snapshot of a
  running container off the box**: such snapshots embed the container's
  runtime environment (i.e. real secrets) in the image config. Only push
  images produced by `docker build` / `docker compose build`, whose env is
  injected at runtime and never baked.
- **Automated off-box baselines (startup CLI):** every rebuild component on
  the canonical instance runs the pre-push gate below on each fresh image,
  tags it `baseline-<YYYYMMDD>-<sha>` in its private org package, pushes it,
  and logs docker out again. Destinations are `nextseek`,
  `nextseek-cc-agent`, `nextseek-sidecar`, and `nextseek-bedrock-proxy` under
  `ghcr.io/biomicrocenter/`. The step
  **never fails the deploy**: missing/expired credentials, a gate failure, or
  a registry error each print an unmissable banner with the fix, are recorded
  in `startup/.ghcr-push-state.json` (gitignored), and stay red in
  `./startup.sh doctor` until a push succeeds. Credential: a classic PAT with
  `write:packages`, whose owner must be a **BioMicroCenter org member** (repo
  roles are not enough; this was learned the hard way), stored per deploying
  user at `~/.config/nextseek/ghcr.env` (`GHCR_USER=…`/`GHCR_TOKEN=…`, mode
  600, path overridable via `NEXTSEEK_GHCR_ENV`). When a token expires, any
  org member can mint their own and drop it in their own home; no shared
  credential. The gate encodes one accepted deviation: `/app/.env` passes
  only if its sole key is `LURIAKEY` (a file path, verified non-credential,
  accepted 2026-08-05); any other key name fails the gate.
- **Pre-push gate (mandatory before ANY off-box push):** even a
  build-produced image can carry secrets if the build context was dirty:
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
  `.dockerignore` (env files are excluded by *pattern*, not exact name; see
  `.dockerignore` and `test_build_context_env_guard.py`), rebuild, re-run
  the gate.

### 5.3 mysqldump gate (before migration-applying deploys)

```bash
docker exec <mysql-container> mysqldump -u<user> -p<pass> dmac <affected-table> \
  > backup-<table>-pre-<migration>-<YYYYMMDD>.sql
chmod 600 backup-*.sql     # contains real data: treat as a secret
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
#    your shell. Source it first (default-port boxes may skip this):
NEXTSEEK_PORT=$(grep '^NEXTSEEK_PORT=' .env | cut -d= -f2)

# 1. Site up. Expect: 200
curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:${NEXTSEEK_PORT:-8000}/"

# 2. Server mode. Expect: NEXTSEEK_SERVER=gunicorn; 'gunicorn dmac.wsgi'
#    workers + a 'celery … batch_upload' worker; ZERO daphne lines
docker compose exec web printenv NEXTSEEK_SERVER
docker compose top web | grep -c daphne          # expect: 0

# 3. No crash-loop. Expect: 0
docker inspect nextseek $(docker compose ps -q web) --format '{{.Name}} {{.RestartCount}}'

# 4. Boot clean. Expect: no FAILED/UNREACHABLE markers; only expected
#    'Applying <migration>... OK' lines
{ docker compose logs --no-log-prefix web; docker logs nextseek; } 2>&1 | grep -E '(COLLECTSTATIC-FAILED|DB-UNREACHABLE|MIGRATE-FAILED|Applying)'

# 5. Migrations. Expect: all [X]
docker exec nextseek uv run manage.py showmigrations nextseek_api | tail -5

# 6. CC route wired. Expect: (True, 'ok')
#    (the image has NO bare `python` on PATH; use `uv run --no-sync`, which
#    executes in the app env /app/.venv without modifying it)
#    Step 9 below now runs this same command for you, as doctor's "CC runner"
#    check, and `./startup.sh rebuild` additionally fails when any first-party
#    image is absent. Run it by hand when you want the answer on its own.
docker compose exec web uv run --no-sync python -c "from NessieAI.cc import cc_engine; print(cc_engine.cc_runner_available())"

# 7. OI-3 peers untouched (app-only deploy). Expect: uptime/health
#    unchanged from before the deploy
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -e nextseek-sidecar -e dmac-bedrock-proxy

# 8. Rollback tag present. Expect: succeeds (prints an image ID)
docker image inspect nextseek-nextseek:pre-<name> --format '{{.Id}}'

# 9. Health suite. Expect: exit 0. Run from a checkout with uv on PATH
#    (NOT via the §3.4 docker:cli helper: it has no uv, and doctor's HTTP
#    probes need host-loopback access). Includes "first-party images" (all four
#    built images present on the host) and "CC runner" (step 6, run in the app
#    container).
./startup.sh doctor
```

`./startup.sh rebuild` and `ci` also print a "graph sync health" line, which fails the run when the graph sync is
failing (rows stuck past their retry, a failed run, drift). To read it by hand, run
`docker exec nextseek uv run --no-sync python manage.py graph_sync_health`: it prints one line per problem and exits
1 when there is one.

For CC-touching deploys, additionally run the OI-3 checks in §9. For a full
greenfield acceptance (all plugin ops live, paid), see the Appendix.

---

## 7. Test lanes

This runbook carries no test commands. Each lane has one owner.

`NessieAI/tests/cc/test_deploy_docs_guard.py` guards this file and
`NessieAI/cc/DEPLOY.md`: keep `./startup.sh install` and the DEPLOY.md link, and
never reintroduce the lettered phase names its "legacy phase marker" pattern forbids.

| Lane | Owner |
|---|---|
| Django suites, the blocking gates (route registry, docs map), the pytest baseline | [`ci/README.md`](ci/README.md) |
| startup CLI | [`startup/CLAUDE.md`](startup/CLAUDE.md), "Test command" |
| Every AI lane, including the paid acceptance runs | [`NessieAI/tests/README.md`](NessieAI/tests/README.md) |
| Post-deploy smoke (also run by `./startup.sh rebuild` unless `--no-ci`) | [`ci/smoke/README.md`](ci/smoke/README.md) |

Paid/live lanes (`RUN_REALSTACK=1`, `-k realstack`, the harness `--tier full` and
`--bayesian`) are **approval-gated: never run them without the owner's explicit
per-run sign-off** (they spend real LLM budget against the live stack).

---

## 8. Configuration & secrets inventory

Five gitignored files, all rendered by a first `./startup.sh install` (and by
`reset` without `--keep-config`); a re-install keeps what they hold (§2.4):

| File | Rendered from | Holds |
|---|---|---|
| `docker/db.env` | `startup/templates/db.env.template` | MySQL root + app credentials |
| `docker/nextseek.env` | `startup/templates/nextseek.env.template` | Django secret, Neo4j password, SEEK URL, LLM keys (`GCP_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`, `FDH_API`); `NEXTSEEK_SERVER` + CC knobs are **hand-added** (not in the template; a re-install keeps them, `reset` without `--keep-config` drops them, §2.4); every key is in the table below |
| `dmac/local_settings.py` | `startup/templates/local_settings.py.template` | Django settings overlay (PROD ChatConfig block, etc.); install never overwrites an existing one and writes `local_settings.py.new` beside it |
| `NessieAI/docker/bedrock-proxy/proxy-secret.env` | rendered programmatically (0600) | `AWS_BEARER_TOKEN_BEDROCK` + `AWS_REGION` **for the proxy** |
| `.env` (repo root) | rendered (0600) | compose interpolation: `COMPOSE_PROJECT_NAME`, ports, `INSTANCE_PREFIX`, and `NEO4J_PASSWORD`, without which compose refuses every verb (see `.env.example`); install rewrites these and keeps every other line already there, such as the memory caps and `SEEK_PUMA_WORKERS` |

Every env var and Django setting the runtime reads, generated from `startup/env_registry.toml` (do not edit by
hand; regenerate with `uv run --project startup python -m startup.lib.env_registry --write` from the repo root, and
`./startup.sh doctor` warns when a rendered file disagrees with it). `files` says where a value goes: a
rendered env file, `local_settings` (a Django setting, not env), `image` (Dockerfile, compose or the
entrypoint) or `internal` (set by code, never by an operator). Secrets show no default.

<!-- ENV-REGISTRY:BEGIN (generated: uv run --project startup python -m startup.lib.env_registry --write) -->

| Key | Files | Default | Required | Alias of | What it does |
|---|---|---|---|---|---|
| `MYSQL_HOST` | db.env |  | yes |  | MySQL host, the db container's name. |
| `MYSQL_ROOT_PASSWORD` | db.env | secret | yes |  | MySQL root password. Install renders the public demo value; rotate it on any exposed box. |
| `MYSQL_DATABASE` | db.env |  | yes |  | SEEK's database name. |
| `NEXTSEEK_MYSQL_DATABASE` | db.env |  | yes |  | NExtSEEK's own database name. |
| `MYSQL_USER` | db.env |  | yes |  | Application user for NExtSEEK and SEEK. |
| `MYSQL_PASSWORD` | db.env | secret | yes |  | Application user's password. Install renders the public demo value; rotate it on any exposed box. |
| `SEEK_HOST` | nextseek.env |  | yes |  | SEEK container name; switches on the SEEK settings in dmac/settings.py. |
| `SEEK_HOSTNAME` | nextseek.env |  | yes |  | Internal SEEK base URL, http://seek:3000. |
| `SEEK_PUBLIC_URL` | nextseek.env | "" |  |  | Browser-reachable SEEK base URL for links. Install keeps a hand-set value. |
| `NEXTSEEK_HOSTNAME` | nextseek.env |  | yes |  | NExtSEEK host and published port; NEXTSEEK_BASE_URL derives from it. |
| `NEXTSEEK_NEO4J_PASSWORD` | nextseek.env | secret | yes |  | Neo4j password the app uses. Must match NEO4J_PASSWORD in the root .env. |
| `NEXTSEEK_NEO4J_HOST` | nextseek.env |  | yes |  | Neo4j container name; switches on the graph settings. |
| `DJANGO_SECRET_KEY` | nextseek.env | secret | yes |  | Django signing key. Install generates a new one. |
| `DJANGO_ALLOWED_HOSTS` | nextseek.env | "" | yes |  | Space-separated Django allowed hosts. Install keeps a hand-set value. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | nextseek.env | "" | yes |  | Space-separated trusted origins. Install keeps a hand-set value and adds its port's loopback pair. |
| `DJANGO_SECURE_COOKIES` | nextseek.env | "" |  |  | 1/true/yes sets the Secure flag on the session and CSRF cookies. Off by default; turn on once the box is served over https. |
| `DJANGO_TRUST_X_FORWARDED_PROTO` | nextseek.env | "" |  |  | 1/true/yes trusts the edge's X-Forwarded-Proto header. Only where the edge overwrites it. |
| `DJANGO_CORS_ALLOWED_ORIGINS` | nextseek.env | "" |  |  | Space-separated origins allowed to call /nextseek_api/ cross-origin. The Vite dev origin is added only under DJANGO_DEBUG. |
| `DJANGO_THROTTLE_ANON` | nextseek.env | 120/min |  |  | API request limit per client address for callers who are not signed in. |
| `DJANGO_THROTTLE_USER` | nextseek.env | 1200/min |  |  | API request limit per signed-in user. Generous: the chat frontend polls and the CI suite runs as one user. |
| `DJANGO_THROTTLE_TURN_START` | nextseek.env | 30/min |  |  | Limit per user on starting an agent turn (cc-assistant and assistant query/async, nessie/query). |
| `DJANGO_THROTTLE_UPLOAD` | nextseek.env | 10/min |  |  | Limit per user on starting an upload job (batch-upload start, cc-assistant upload). |
| `DJANGO_NUM_PROXIES` | nextseek.env | 2 |  |  | Proxies that append to X-Forwarded-For before Django (TLS edge plus the inner nginx). Sets which entry counts as the client address for rate limits and sign-in counting. Set 1 if the box has no edge in front of nginx. |
| `DJANGO_THROTTLE_CACHE_DIR` | nextseek.env | /var/tmp/nextseek-throttle |  |  | Directory of the file cache that holds the rate-limit and sign-in counters, shared by every process in the container. |
| `DJANGO_LOGIN_FAILS_PER_USER` | nextseek.env | 10 |  |  | Failed sign-ins allowed per window for one client address and username (form login and Basic API credentials). |
| `DJANGO_LOGIN_FAILS_PER_ADDRESS` | nextseek.env | 30 |  |  | Failed sign-ins allowed per window for one client address, across usernames. |
| `DJANGO_LOGIN_WINDOW_SECONDS` | nextseek.env | 900 |  |  | Length in seconds of the failed sign-in window. |
| `NEXTSEEK_BASE_URL` | nextseek.env, internal |  |  |  | Public NExtSEEK base URL, derived from NEXTSEEK_HOSTNAME. cc_engine hands the agent its own copy. |
| `NEXTSEEK_INTERNAL_BASE_URL` | nextseek.env |  |  |  | Container-internal URL for chat_nextseek's REST self-calls; preferred over NEXTSEEK_BASE_URL. |
| `LOG_DIR` | nextseek.env | /app/logs |  |  | Log folder inside the app container. |
| `NEXTSEEK_OUTPUTS_DIR` | nextseek.env |  |  |  | Per-turn output folder inside the app container. |
| `CATALOG_FILE` | nextseek.env |  |  |  | The agent model catalog Nessie loads; must point under /app/NessieAI/chat_nextseek/ on a box. |
| `NEXTSEEK_POSTERIOR_ROUTING_ENABLED` | nextseek.env |  |  |  | Posterior routing switch; off unless 1, true or yes. |
| `NESSIE_LAYA_SHADOW` | nextseek.env | 0 |  |  | Log laya's route next to the BAML router's without routing on it. |
| `NESSIE_LAYA_LIVE` | nextseek.env | "" |  |  | Route on laya when it equals the calibrated revision; empty is off. |
| `LAYA_API_KEY` | nextseek.env, laya.env | secret |  |  | Shared key between the app and the laya sidecar. |
| `GCP_API_KEY` | nextseek.env | secret |  |  | Gemini API key. |
| `AWS_BEARER_TOKEN_BEDROCK` | nextseek.env, proxy-secret.env | secret |  |  | Bedrock bearer token, for the app and for the Container-CC proxy. |
| `FDH_API` | nextseek.env | secret |  |  | FAIRDOMHub API token for protocol downloads. |
| `NEXTSEEK_CC_MAX_BUDGET_USD` | nextseek.env | 0.50 |  |  | Per-turn spend cap for a Container-CC turn; 0 disables it. A security control. |
| `NEXTSEEK_CC_TIMEOUT_HARD_MAX` | nextseek.env | 180 |  |  | Hard ceiling in seconds on one Container-CC turn. |
| `SESSION_DB_TYPE` | nextseek.env | sqlite |  |  | chat_nextseek session store, mysql or sqlite. |
| `SESSION_DB_HOST` | nextseek.env |  |  |  | chat_nextseek session store host; the template interpolates MYSQL_HOST. |
| `SESSION_DB_USER` | nextseek.env |  |  |  | chat_nextseek session store user; the template interpolates MYSQL_USER. |
| `SESSION_DB_PASSWORD` | nextseek.env | secret |  | `MYSQL_PASSWORD` | chat_nextseek session store password; the template interpolates MYSQL_PASSWORD. |
| `SESSION_DB_NAME` | nextseek.env |  |  |  | chat_nextseek session store database; the template interpolates NEXTSEEK_MYSQL_DATABASE. |
| `MYSQL_HOST_PROD` | nextseek.env |  |  |  | ChatConfig's prod-mode MySQL host; install writes the local value. |
| `MYSQL_PROD_PASSWORD` | nextseek.env | secret |  |  | ChatConfig's prod-mode MySQL password; install writes the local value. Chosen by mode, not an alias. |
| `MYSQL_HOST_DEV` | nextseek.env |  |  |  | ChatConfig's dev-mode MySQL host; install writes the local value. |
| `MYSQL_DEV_PASSWORD` | nextseek.env | secret |  |  | ChatConfig's dev-mode MySQL password; install writes the local value. Chosen by mode, not an alias. |
| `NEO4J_URI` | nextseek.env | bolt://localhost:7687 |  |  | Neo4j URI for chat_nextseek; the same server as NEXTSEEK_NEO4J_HOST in another shape. |
| `NEO4J_USER` | nextseek.env | neo4j |  |  | Neo4j user for chat_nextseek. |
| `NEO4J_PASSWORD` | nextseek.env, root.env | secret | yes | `NEXTSEEK_NEO4J_PASSWORD` | Neo4j password: chat_nextseek's copy in nextseek.env, and compose's seed for a new neo4j-data volume in the root .env. |
| `NEXTSEEK_GRAPH_SYNC_STUDY_LINKS` | nextseek.env |  |  |  | graph_sync: follow also removes IN_STUDY links SEEK no longer holds. Set only after the box's study merge. |
| `NEO4J_TIMEOUT_SECONDS` | nextseek.env | 5 |  |  | Seconds the sample download's lineage lookup may run before falling back to Parent. |
| `DJANGO_DEBUG` | nextseek.env |  |  |  | Django debug mode; on only for 1, true or yes. |
| `DJANGO_SESSION_COOKIE_DOMAIN` | nextseek.env |  |  |  | Session cookie domain; unset on a local install. |
| `FAIRDATA_SOP_HOSTS` | nextseek.env |  |  |  | Space-separated hosts whose SOP links the app may fetch. The code default names one host. |
| `PROJECT_CONNECTIONS_CACHE_SECONDS` | nextseek.env | 3600 |  |  | Cache lifetime in seconds for project connections. |
| `NESSIE_VOCAB_PRERUN` | nextseek.env | on |  |  | Nessie: run the vocabulary step before routing, on or off. |
| `NESSIE_PARSER_START` | nextseek.env | after_route |  |  | Nessie: when the parser starts relative to the router. |
| `NEXTSEEK_CC_OPS_ROAD` | nextseek.env, internal | "" |  |  | Container-CC op road, direct or sidecar; cc_engine hands it to the agent. |
| `GS_RUN_DIR` | nextseek.env |  |  |  | graph_sync run directories; unset means graph_sync under the log folder. |
| `NEXTSEEK_GRAPH_SYNC_LABEL_CHANGES` | nextseek.env | "" |  |  | graph_sync loop: label-change handling switch. |
| `SEEK_RAILS_RUNNER_TIMEOUT` | nextseek.env |  |  |  | Seconds a SEEK rails runner call may take. |
| `ATTRIBUTE_MUTATION_AFFECTED_ROW_THRESHOLD` | nextseek.env | 5000 |  |  | Rows above which an attribute change runs as a background job. |
| `ATTRIBUTE_MUTATION_IN_JOB_PARALLELISM` | nextseek.env | 1 |  |  | Parallelism inside one attribute mutation job. |
| `ASSAY_REGISTRATION_SYNC_ROW_THRESHOLD` | nextseek.env | 5000 |  |  | Rows above which an assay registration runs as a background job. |
| `ATTRIBUTE_MUTATION_OUTBOX_BATCH_SIZE` | nextseek.env | 50 |  |  | Attribute job outbox batch size. |
| `ATTRIBUTE_MUTATION_OUTBOX_IDLE_SECONDS` | nextseek.env | 0.5 |  |  | Attribute job outbox idle wait in seconds. |
| `BATCH_UPLOAD_MAX_TOTAL_BYTES` | nextseek.env |  |  |  | Largest batch upload in bytes; the code default is 200 MB. |
| `BATCH_UPLOAD_MAX_ROWS` | nextseek.env |  |  |  | Rows per batch-upload batch. |
| `BATCH_UPLOAD_MEM_LIMIT_MB` | nextseek.env |  |  |  | Memory limit in MB for one batch upload; unset is none. |
| `BATCH_UPLOAD_UPDATE_EXISTING` | nextseek.env |  |  |  | Batch upload updates rows that already exist. |
| `ENABLE_AUTO_PERMISSIONS` | nextseek.env |  |  |  | Batch upload sets SEEK permissions on what it creates. |
| `PERMISSIONS_DEFAULT_ACCESS_TYPE` | nextseek.env |  |  |  | Batch upload's default SEEK access type. |
| `PERMISSIONS_DEFAULT_CONTRIBUTOR_TYPE` | nextseek.env |  |  |  | Batch upload's default SEEK contributor type. |
| `ASSAY_CACHE_MAX` | nextseek.env |  |  |  | Batch upload assay cache size. |
| `TITLE_TO_ID_CACHE_MAX` | nextseek.env |  |  |  | Batch upload title-to-id cache size. |
| `ATTRIBUTE_CACHE_MAX` | nextseek.env |  |  |  | Batch upload attribute cache size. |
| `NEO4J_NODE_CHUNK` | nextseek.env | 10000 |  |  | Batch upload Neo4j node write chunk. |
| `NEO4J_REL_CHUNK` | nextseek.env | 20000 |  |  | Batch upload Neo4j relationship write chunk. |
| `BATCH_TASK_SOFT_LIMIT` | nextseek.env | 7200 |  |  | Celery soft time limit in seconds for a batch task. |
| `BATCH_TASK_HARD_LIMIT` | nextseek.env | 7800 |  |  | Celery hard time limit in seconds for a batch task. |
| `CELERY_BROKER_URL` | nextseek.env, internal | sqla+sqlite:////var/celery/broker.sqlite |  |  | Batch upload Celery broker; the entrypoint sets its own for the attribute workers. |
| `CELERY_RESULT_BACKEND` | nextseek.env | db+sqlite:////var/celery/results.sqlite |  |  | Batch upload Celery result backend. |
| `NEXTSEEK_MODE` | nextseek.env |  |  |  | ChatConfig model mode; unset or invalid falls back. |
| `NEXTSEEK_PUBLISHED_UMBRELLA_PROJECTS` | nextseek.env | "" |  |  | Comma-separated umbrella projects treated as published. |
| `CHAT_NEXTSEEK_CONFIG_VERBOSE` | nextseek.env |  |  |  | ChatConfig prints where each value came from. |
| `CHAT_NEXTSEEK_CONFIG_SOURCE_ENV_NAMES` | nextseek.env |  |  |  | JSON map of config value to env name, for the config report. |
| `XDG_STATE_HOME` | nextseek.env | ~/.local/state |  |  | Standard XDG state folder; chat_nextseek's fallback log, output and session paths sit under it. |
| `PROMPTS_DIR` | nextseek.env |  |  |  | chat_nextseek prompts folder; defaults beside the package. |
| `CONTEXT_DIR` | nextseek.env |  |  |  | chat_nextseek context folder; defaults beside the package. |
| `NESSIE_DOCS_DIR` | nextseek.env |  |  |  | User docs folder Nessie reads; defaults to themes/NextSeek/docs. |
| `SEQ_TEMPLATE_PATH` | nextseek.env |  |  |  | GEO submission template workbook. |
| `AGENT_MODEL_CATALOG` | nextseek.env |  |  |  | Inline JSON agent model catalog; wins over CATALOG_FILE when set. |
| `SESSION_DB_PATH` | nextseek.env |  |  |  | chat_nextseek SQLite session store path when SESSION_DB_TYPE is sqlite. |
| `SESSION_DB_PORT` | nextseek.env | 3306 |  |  | chat_nextseek session store port. |
| `MYSQL_PORT` | nextseek.env |  |  |  | ChatConfig's MySQL port. |
| `NEO4J_DATABASE` | nextseek.env | neo4j |  |  | Neo4j database name for chat_nextseek. |
| `ANTHROPIC_API_KEY` | nextseek.env | secret |  |  | Anthropic API key, for an anthropic model mode. |
| `OPENAI_API_KEY` | nextseek.env | secret |  |  | OpenAI API key, for an openai model mode. |
| `OPENAI_BASE_URL` | nextseek.env |  |  |  | OpenAI-compatible base URL. |
| `OPENAI_API_BASE` | nextseek.env |  |  |  | Older name of OPENAI_BASE_URL, read as a fallback. |
| `AWS_REGION` | nextseek.env, proxy-secret.env, internal | us-east-1 |  |  | Bedrock region: the app's calls, the proxy's upstream host, and the agent's copy. |
| `AWS_DEFAULT_REGION` | nextseek.env |  |  |  | Fallback for AWS_REGION. |
| `GCP_LLM_MODEL` | nextseek.env |  |  |  | Gemini model override for a gcp model mode. |
| `ANTH_LLM_MODEL` | nextseek.env |  |  |  | Anthropic model override for an anthropic model mode. |
| `BEDROCK_LLM_MODEL` | nextseek.env |  |  |  | Bedrock model override for a bedrock model mode. |
| `NEXTSEEK_LLM_MODEL` | nextseek.env | gpt-5.2 |  |  | Model for the openai model mode. |
| `NEXTSEEK_LLM_LOG` | nextseek.env |  |  |  | Also append every LLM prompt and reply to this file. |
| `API_USER` | nextseek.env, internal | "" |  |  | chat_nextseek's REST user; cc_engine hands the agent the turn's user under this name. |
| `API_PASS` | nextseek.env | secret |  |  | chat_nextseek's REST password. |
| `SEMANTIC_SHORTLIST_ENABLED` | nextseek.env |  |  |  | Semantic catalog shortlist for sample types and assays; off by default. |
| `SEMANTIC_ENDPOINTS_ENABLED` | nextseek.env |  |  |  | Semantic shortlist for the parser's API endpoints; off by default. |
| `SEMANTIC_MODEL_NAME` | nextseek.env | sentence-transformers/all-MiniLM-L6-v2 |  |  | Sentence-transformers model for the semantic shortlist. |
| `SEMANTIC_RATIO` | nextseek.env | 0.7 |  |  | Semantic shortlist: keep items scoring at least this share of the top score. |
| `SEMANTIC_MIN_K` | nextseek.env | 10 |  |  | Semantic shortlist: always keep at least this many items. |
| `SEMANTIC_MAX_K` | nextseek.env | 80 |  |  | Semantic shortlist: never keep more than this many items. |
| `PIPELINE_LAUNCH_MODE` | nextseek.env |  |  |  | Where nf-core pipelines launch, luria or tower. |
| `LURIAKEY` | nextseek.env, root.env | /dev/null |  |  | Path of the Luria SSH key file; compose mounts it at the same path. A path, not the key. |
| `LURIA_USER` | nextseek.env |  |  |  | Luria cluster user. |
| `LURIA_WORKING_PATH` | nextseek.env |  |  |  | Luria cluster working folder. |
| `LURIA_CONDA_ENV` | nextseek.env |  |  |  | Conda environment a Luria run script activates. |
| `LURIA_MAIL_USER` | nextseek.env |  |  |  | Mail recipient for Luria job notices. |
| `SEQERA_AUTO_LAUNCH` | nextseek.env |  |  |  | Launch a Seqera pipeline without a confirmation. |
| `TOWER_ACCESS_TOKEN` | nextseek.env | secret |  |  | Seqera Platform (Tower) access token. |
| `TOWER_WORKSPACE_ID` | nextseek.env |  |  |  | Seqera Platform workspace id. |
| `TOWER_API_ENDPOINT` | nextseek.env |  |  |  | Seqera Platform API endpoint. |
| `SEQERA_COMPUTE_ENV` | nextseek.env |  |  |  | Seqera compute environment. |
| `SEQERA_WORK_BUCKET` | nextseek.env |  |  |  | Seqera work bucket. |
| `SEQERA_PIPELINE_REVISIONS_JSON` | nextseek.env |  |  |  | JSON map of pipeline to pinned revision for Seqera launches. |
| `SEQERA_DEFAULT_PROFILE` | nextseek.env |  |  |  | Default Nextflow profile for Seqera launches. |
| `SEQERA_INPUT_DIR` | nextseek.env |  |  |  | Input folder for Seqera launches. |
| `NEXTSEEK_EVALUATOR_MODE` | nextseek.env |  |  |  | Model mode for the chat_nextseek evaluator. |
| `NEXTSEEK_PROD_URL` | nextseek.env |  |  |  | Evaluator: the prod NExtSEEK URL it compares against. |
| `API_PASS_PROD` | nextseek.env | secret |  |  | Evaluator: the prod REST password. |
| `NEO4J_PASSWORD_PROD` | nextseek.env | secret |  |  | Evaluator: the prod Neo4j password. |
| `NEO4J_PASSSWORD_PROD` | nextseek.env | secret |  |  | Known misspelling (three S) of NEO4J_PASSWORD_PROD, read as spelt by the evaluator and the Nessie CLI; declared as is by ruling. |
| `CHAT_NEXTSEEK_GRAPH_ADMIN` | nextseek.env |  |  |  | Operator opt-in, exactly 1, for unscoped graph reads in the evaluator; no served process reads it. |
| `NESSIE_FOLLOWUP_ROUTING` | nextseek.env |  |  |  | How the router treats a follow-up (NessieAI/router/followup.py). |
| `NEXTSEEK_CC_IMAGE` | nextseek.env | dmac-assistant:poc |  |  | Image a Container-CC turn runs in. |
| `NEXTSEEK_CC_NETWORK` | nextseek.env | dmac-cc-net |  |  | Docker network a Container-CC turn joins. |
| `DMAC_BEDROCK_PROXY_URL` | nextseek.env | http://bedrock-proxy:8080 |  |  | Bedrock proxy URL the agent calls. |
| `NEXTSEEK_CC_MAX_TURNS` | nextseek.env, root.env | 50 |  |  | Most agent turns in one Container-CC turn. |
| `NEXTSEEK_CC_TIMEOUT_SECONDS` | nextseek.env |  |  |  | Default length in seconds of one Container-CC turn. |
| `NEXTSEEK_CC_MEM_LIMIT` | nextseek.env, root.env | 4g |  |  | Memory limit of the agent container. |
| `NEXTSEEK_CC_NANO_CPUS` | nextseek.env, root.env |  |  |  | CPU limit of the agent container, in nano CPUs. |
| `NEXTSEEK_CC_PIDS_LIMIT` | nextseek.env, root.env | 512 |  |  | Process limit of the agent container. |
| `NEXTSEEK_CC_FSIZE_BYTES` | nextseek.env, root.env |  |  |  | Largest file the agent may write, in bytes; the code default is 10 GiB. |
| `NEXTSEEK_CC_MAX_RETRIES` | nextseek.env |  |  |  | Claude Code request retries inside a turn. |
| `NEXTSEEK_CC_API_TIMEOUT_MS` | nextseek.env |  |  |  | Claude Code request timeout in milliseconds. |
| `NEXTSEEK_CC_DEFAULT_SONNET_MODEL` | nextseek.env |  |  |  | Classifier model id Claude Code uses; must be one the proxy allows. |
| `NEXTSEEK_SIDECAR_HOST` | nextseek.env, internal | nextseek-sidecar |  |  | Sidecar host for the sidecar op road; cc_engine hands it to the agent. |
| `NEXTSEEK_SIDECAR_PORT` | nextseek.env, internal | 8765 |  |  | Sidecar port for the sidecar op road; cc_engine hands it to the agent. |
| `NEXTSEEK_URL` | nextseek.env, internal |  |  |  | Last fallback for the NExtSEEK URL; cc_engine hands the agent the URL under this name. |
| `DMAC_USER_ROOT_MOUNT` | nextseek.env |  |  |  | Mount point of the Container-CC users volume inside the app container. |
| `DMAC_CC_MEMORY_WINDOW` | nextseek.env | 10 |  |  | Container-CC memory: turns kept in the window. |
| `DMAC_CC_MEMORY_MAX_ITEMS` | nextseek.env | 8 |  |  | Container-CC memory: most items kept. |
| `DMAC_CC_MEMORY_TRUNCATE_CHARS` | nextseek.env | 500 |  |  | Container-CC memory: characters kept per item. |
| `DMAC_CC_MEMORY_SWEEP_IDLE_SECONDS` | nextseek.env | 900 |  |  | Container-CC memory: idle seconds before a sweep. |
| `DMAC_CC_MEMORY_SWEEP_MAX_PER_RUN` | nextseek.env | 20 |  |  | Container-CC memory: most sessions one sweep run summarizes, oldest first. |
| `NEXTSEEK_CC_SUMMARY_SWEEP` | nextseek.env | 1 |  |  | Container-CC memory: 0, false or off stops the scheduled paid summary sweep (the beat keeps running). |
| `DMAC_CC_MEMORY_SUMMARY_MODEL` | nextseek.env | gemini-flash |  |  | Container-CC memory: summary model. |
| `STEP7_LLM_LEDGER` | nextseek.env |  |  |  | Container-CC: write the per-call LLM cost ledger when 1. |
| `DJANGO_BASE_DIR` | nextseek.env | /app |  |  | App root the LLM cost ledger writes under. |
| `NESSIE_TESTQUESTIONS_DIR` | nextseek.env | testquestions-2026-08-07 |  |  | Folder of the paired-evidence test questions archive. |
| `NEXTSEEK_EVAL_PARSER_FORCE` | nextseek.env |  |  |  | Eval opt-in, 1, that lets a superuser force the parser. |
| `DMAC_USERS` | nextseek.env | secret |  |  | dmac_assistant bridge users as JSON, each with a password. |
| `DMAC_BRIDGE_HOST` | nextseek.env | 127.0.0.1 |  |  | dmac_assistant bridge listen host. |
| `DMAC_BRIDGE_PORT` | nextseek.env | 8000 |  |  | dmac_assistant bridge listen port. |
| `DMAC_SIDECAR_NETWORK` | nextseek.env |  |  |  | dmac_assistant bridge: docker network of the sidecar. |
| `DMAC_DEV_MODE` | nextseek.env | "" |  |  | dmac_assistant bridge dev mode; on only for a true value. |
| `DMAC_ROUTE_CAPABILITIES_FILE` | internal |  |  |  | Override of the route capabilities file. On a box it beats the package default and routing drops to the heuristic. Must not be set. |
| `DMAC_ROUTER_MODEL_CLASS_MAP_FILE` | internal |  |  |  | Override of the router model class map. On a box every CC turn loses its model id. Must not be set. |
| `COMPOSE_PROJECT_NAME` | root.env | nextseek |  |  | Compose project name; install writes it per instance. |
| `INSTANCE_PREFIX` | root.env | "" |  |  | Prefix for container and volume names of a second instance; install writes it. |
| `NEXTSEEK_PORT` | root.env | 8000 |  |  | Host port of the published nginx; install writes it. |
| `SEEK_PORT` | root.env | 3000 |  |  | Host port of SEEK; install writes it. |
| `DB_PORT` | root.env | 3306 |  |  | Host port of MySQL; install writes it. |
| `NEO4J_HTTP_PORT` | root.env | 7474 |  |  | Host port of the Neo4j browser; install writes it. |
| `NEO4J_BOLT_PORT` | root.env | 7687 |  |  | Host port of Neo4j bolt; install writes it. |
| `NEXTSEEK_MEMORY` | root.env | 8G |  |  | Memory cap of the web container. |
| `NEXTSEEK_WORKER_MEMORY` | root.env | 8G |  |  | Memory cap of the worker container (the nine background loops). |
| `NEXTSEEK_DRAIN_SECONDS` | root.env | 240 |  |  | How long a stopping web container lets running chat turns finish before it cuts them. web's stop_grace_period must stay at least this plus 30 s. |
| `NEXTSEEK_SPAWNER_TOKEN` | nextseek.env | secret |  |  | Token web sends to cc-spawner. Install and rebuild write it, with the same value as SPAWNER_TOKEN in docker/cc-spawner.env. |
| `NEXTSEEK_SPAWNER_URL` | root.env | http://cc-spawner:8080 |  |  | Where web sends Container-CC turns. Unset: the cc-spawner service. Set empty in .env: today's direct Docker path (the switch lives one dev cycle). |
| `NEXTSEEK_CC_AUTOMODE_GCP` | root.env | 1 |  |  | 1 when the auto-mode classifier may use GCP (the app has GCP_API_KEY set); the spawner reads this flag, never the key. |
| `CC_SPAWNER_NEO4J_URI` | root.env | neo4j://neo4j:7687 |  |  | Neo4j URI the cc-spawner names in the Container-CC auto-mode trust text. |
| `SPAWNER_MEMORY` | root.env | 512M |  |  | Memory cap of the cc-spawner container. |
| `SEEK_MEMORY` | root.env | 4G |  |  | Memory cap of SEEK. |
| `SEEK_PUMA_WORKERS` | root.env | 4 |  |  | SEEK puma workers. Set with SEEK_MEMORY and SEEK_WORKER_MAX_MB. |
| `SEEK_WORKER_MAX_MB` | root.env | 2500 |  |  | RSS in whole MB at which the recycler restarts a SEEK puma worker. |
| `SEEK_WORKERS_MEMORY` | root.env | 6G |  |  | Memory cap of the SEEK job workers. |
| `SEEK_DB_MEMORY` | root.env | 6G |  |  | Memory cap of MySQL. |
| `NEO4J_IMAGE_TAG` | root.env | 2026.05.0 |  |  | Neo4j image tag. |
| `NGINX_IMAGE_TAG` | root.env | 1.31.2 |  |  | nginx image tag. |
| `NEO4J_MEMORY` | root.env | 6G |  |  | Memory cap of Neo4j. |
| `NEO4J_HEAP` | root.env | 2g |  |  | Neo4j heap size. |
| `NEO4J_PAGECACHE` | root.env | 2g |  |  | Neo4j page cache size. |
| `NEO4J_TRANSACTION_MAX` | root.env | 1g |  |  | Neo4j memory cap per transaction. |
| `NEO4J_PLUGINS` | root.env | "" |  |  | Neo4j plugins to load, for example the read-only APOC families. |
| `NEO4J_PROCEDURES_ALLOWLIST` | root.env | db.*,dbms.*,tx.*,fleetManagement.*,apoc.path.*,apoc.meta.*,apoc.text.*,apoc.coll.*,apoc.agg.*,apoc.map.*,apoc.number.*,apoc.convert.*,apoc.date.*,apoc.temporal.*,apoc.node.*,apoc.label.*,apoc.any.*,apoc.math.*,apoc.stats.* |  |  | Neo4j procedures allowlist. |
| `SOLR_MEMORY` | root.env | 2G |  |  | Memory cap of Solr. |
| `SIDECAR_MEMORY` | root.env | 1G |  |  | Memory cap of the Nessie sidecar. |
| `LAYA_CPUS` | root.env | 4 |  |  | CPU cap of the laya router. |
| `LAYA_MEMORY` | root.env | 3500M |  |  | Memory cap of the laya router. |
| `LAYA_REVISION` | root.env | unset |  |  | laya model revision the router loads. |
| `ATTRIBUTE_MUTATION_WORKER_CONCURRENCY` | root.env | 1 |  |  | Attribute mutation worker concurrency; compose passes it into the app container. |
| `CC_PERSIST_STRICT` | local_settings | False |  |  | Container-CC: a failed transcript save fails the turn. |
| `CC_TRANSCRIPT_MAX_BYTES` | local_settings |  |  |  | Largest stored Container-CC transcript in bytes. |
| `NEXTSEEK_CHAT_CONFIG_PROD` | local_settings | secret |  |  | The prod ChatConfig local_settings builds from its PROD overrides; holds prod credentials. |
| `NEXTSEEK_RISK_OVERLAY_ENABLED` | local_settings | False |  |  | Router risk overlay switch. |
| `NEXTSEEK_EXPORT_ROOT` | local_settings, internal |  |  |  | Folder the private sample exports are written to; compose sets it for web on the shared media volume, and a local_settings value wins. |
| `BATCH_UPLOAD_JOB_TTL_HOURS` | local_settings |  |  |  | Hours a batch upload job record is kept. |
| `BATCH_UPLOAD_STATE_DIR` | nextseek.env | /var/celery |  |  | Batch upload: directory holding the queue's broker, results, job index, saved sheets and beat schedule (the batch_upload_state volume). |
| `SAMPLE_RETRIEVE_NEO4J_CONNECT_TIMEOUT` | local_settings |  |  |  | Sample retrieve: Neo4j connect timeout in seconds. |
| `SAMPLE_RETRIEVE_NEO4J_QUERY_TIMEOUT` | local_settings |  |  |  | Sample retrieve: Neo4j query timeout in seconds. |
| `DJANGO_SETTINGS_MODULE` | image, internal | dmac.settings |  |  | Django settings module; asgi and wsgi set it, tests set their own. |
| `DMAC_CC_USERS_VOLUME` | image |  |  |  | Container-CC users volume name; compose sets it with INSTANCE_PREFIX, so a nextseek.env line is ignored. |
| `SIDECAR_STAGING_DIR` | image |  |  |  | Nessie sidecar staging folder; compose sets it. |
| `SIDECAR_WS_PORT` | image | 8765 |  |  | Nessie sidecar websocket port; compose sets it. |
| `SENTENCE_TRANSFORMERS_HOME` | image |  |  |  | Folder of the embedding models baked into the app image; the Dockerfile sets /opt/models (#28). Unset, schema_rag reads schema_rag/embedding_models. |
| `NEXTSEEK_SERVER` | nextseek.env | daphne |  |  | App server the entrypoint starts, daphne or gunicorn. |
| `NEXTSEEK_GRAPH_SYNC_LOOP` | nextseek.env | 1 |  |  | Entrypoint starts the graph_sync loop unless 0. |
| `GRAPH_SYNC_RESTART_DELAY` | nextseek.env | 60 |  |  | Entrypoint: seconds before a stopped graph_sync loop restarts. |
| `DB_WAIT_ATTEMPTS` | nextseek.env | 30 |  |  | Entrypoint: database probe attempts before giving up. |
| `DB_WAIT_INTERVAL` | nextseek.env | 2 |  |  | Entrypoint: seconds between database probes. |
| `NEXTSEEK_USERNAME` | internal |  |  |  | The turn's user, which cc_engine hands the agent. |
| `NEXTSEEK_TURN_PASS` | internal | secret |  |  | The one-turn pass cc_engine hands the agent; its one credential. |
| `NEXTSEEK_CHAT_SESSION_ID` | internal | "" |  |  | The live chat session id cc_engine hands the agent. |
| `NEXTSEEK_CC_TURN_DEADLINE_EPOCH` | internal | "" |  |  | Unix time by which the agent's turn stops; cc_engine sets it. |
| `NEXTSEEK_ASSISTANT_PREFIX` | internal | nextseek_api/assistant |  |  | API prefix the agent's op tools call. |
| `NEXTSEEK_SCRATCH_DIR` | internal | /data/scratch |  |  | The agent's scratch folder. |
| `NEXTSEEK_DRY_RUN` | internal |  |  |  | The agent's op tools return a stub when 1 (tests). |
| `NEXTSEEK_SKIP_SCHEMA_WARM` | internal |  |  |  | Skip the schema warm-up at app start; the test settings set it. |
| `TESTING` | internal | 0 |  |  | Test-run flag the batch upload policies read. |
| `ATTRIBUTE_TEST_FAULT_CONTROL` | internal |  |  |  | Fault-injection control for the attribute worker tests. |
| `CHAT_NEXTSEEK_SKIP_BAML_BOOTSTRAP` | internal |  |  |  | Skip the evaluator's BAML bootstrap (tests). |
| `NEXTSEEK_TURN_DIR` | internal | /tmp/nextseek-turns |  |  | Folder of running-chat-turn marker files and the .draining flag (a stopping web container drains on them). |
| `NEXTSEEK_DRAIN_POLL` | internal | 10 |  |  | Entrypoint (web): seconds between the drain's turn counts. |
| `NEXTSEEK_MIGRATE_WAIT_SECONDS` | internal | 300 |  |  | Entrypoint (web): seconds to wait for the worker's migrations before giving up. |
| `NEXTSEEK_MIGRATE_WAIT_INTERVAL` | internal | 5 |  |  | Entrypoint (web): seconds between migration checks. |
| `NEXTSEEK_LABS_FILE` | internal |  |  |  | Path of labs_db.json when set (two containers share one copy on a volume); default is the context dir. |

<!-- ENV-REGISTRY:END -->

Sharp edges an operator must know:

- **The Bedrock token is needed in TWO files** for two different code paths:
  `docker/nextseek.env` (native chat_nextseek direct-Bedrock path) **and**
  `NessieAI/docker/bedrock-proxy/proxy-secret.env` (the sandboxed CC agent's only
  route). Filling only one leaves the other chat path dead, with no
  automated cross-check.
- **Neo4j's password lives in three places.** `NEO4J_PASSWORD` in the root
  `.env` seeds the credential when the `neo4j-data` volume is created;
  `NEO4J_PASSWORD` and `NEXTSEEK_NEO4J_PASSWORD` in `docker/nextseek.env` are
  what the app uses. After first start the credential lives in the volume, so
  rotation is a cypher `ALTER` plus a recreate:
  [`docs/neo4j-programmatic-access.md`](docs/neo4j-programmatic-access.md), "Passwords".
- **`NEXTSEEK_INTERNAL_BASE_URL` vs `NEXTSEEK_BASE_URL`:** the internal URL
  is fixed `http://127.0.0.1:8000` (container-internal) and must **not**
  derive from the host-published port; the public URL derives from
  `NEXTSEEK_HOSTNAME`+port. Both templates carry the warning; `doctor`
  checks for a stale hand-maintained overlay missing the guard.
- **Switching the Container-CC ops road** (`NEXTSEEK_CC_OPS_ROAD`, `direct` by default, `sidecar` for one release):
  set it in `docker/nextseek.env`, then `docker compose up -d --no-deps --force-recreate nextseek` (a restart does
  not re-read the env file). Check `docker compose exec -T nextseek printenv NEXTSEEK_CC_OPS_ROAD`, then, during the
  next Container-CC turn, `docker exec <dmac-cc-agent-...> printenv NEXTSEEK_CC_OPS_ROAD` in the agent container.
- `SEEK_PUBLIC_URL` must agree in three places (`startup/.instance.json`,
  `docker/nextseek.env`, SEEK's DB `site_base_host` row); `doctor` detects
  drift but never auto-fixes it.

---

## 9. Security invariants (Container-CC / OI-3): never regress these

The per-turn CC agent container is sandboxed. These invariants are enforced
by tests and were live-verified; any deploy that would weaken one is wrong by
definition:

1. **Zero shared credentials in the agent env, and no user password.** The agent gets only:
   Bedrock-via-proxy pointers, the user's NExtSEEK name and a one-turn pass
   (`NEXTSEEK_TURN_PASS`: Django holds the login for the turn and revokes the pass when the turn ends),
   the ops road (`NEXTSEEK_CC_OPS_ROAD`, `direct` or `sidecar`; on the sidecar road only, also the sidecar
   host/port), the non-secret path mappings, the turn's stop time
   (`NEXTSEEK_CC_TURN_DEADLINE_EPOCH`, a number), and three non-secret model-call
   settings: `CLAUDE_CODE_MAX_RETRIES`, `API_TIMEOUT_MS` and
   `ANTHROPIC_DEFAULT_SONNET_MODEL` (a model id). The 16 forbidden shared-cred keys
   (AWS/Bedrock token, Neo4j, MySQL, GCP, Anthropic) and the three user-password keys
   (`NEXTSEEK_PASSWORD`, `API_PASS`, `SEEK_PASSWORD`) are enumerated in
   `NessieAI/tests/cc/validate_cc_acceptance.py`; the env
   builder is `NessieAI/cc/cc_engine.py` (`build_agent_environment`), the single source
   of truth.
2. **Bedrock only via the proxy.** The proxy holds the token, allowlists the
   model, and must never log the token.
3. **Network segmentation:** `dmac-cc-net` members are a **closed set**:
   nginx (dual-homed), `dmac-bedrock-proxy`, `nextseek-sidecar`, and per-turn
   `dmac-cc-agent-<run_id>` containers. The `nextseek` service itself must
   **never** join `dmac-cc-net` (agents reach the app only through nginx).
   Note: `dmac-cc-net` is a normal bridge (not Docker `--internal`): the
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

Zero-spend re-verification of a recorded acceptance run, and the paid
acceptance runs: [`NessieAI/cc/DEPLOY.md`](NessieAI/cc/DEPLOY.md) and
[`NessieAI/tests/README.md`](NessieAI/tests/README.md).

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
  the internal URL, no SSRF guard). From inside the container, the app's own
  *public* URL is typically unreachable (NAT hairpin); ingest against the
  internal URL. Tracked as GitHub #19.
- **Two BAML clients** are generated at build time in two different images
  (root Dockerfile: router client; cc-runtime: judge client) from the one
  `NessieAI/dmac_assistant/baml_src/` tree; do not assume one covers the other.
- **Only the marked blocks of `NessieAI/docker/cc-runtime/container/CLAUDE.md`
  are generated** (see `NessieAI/build_tools/README.md`); hand-edit only
  outside them. The file is a required image input.

### Periodic jobs (Celery beat)

The app container runs one `celery ... beat` process (schedule file `/var/celery/celerybeat-schedule`).
It queues four jobs, each dropped if it waits longer than its own interval: the
Container-CC summary sweep (300 s), the stuck-turn reaper (300 s), the durable-state purge (daily) and Django `clearsessions` (daily). A long upload
delays the reaper, the purge and `clearsessions`.
The sweep runs on its own `cc_summaries` worker, so it never delays an upload. It sends idle Container-CC transcripts (already scrubbed) to the summary model: set
`NEXTSEEK_CC_SUMMARY_SWEEP=0` in `nextseek.env` and restart the app to stop it; the rest of the beat keeps running.
`DMAC_CC_MEMORY_SWEEP_MAX_PER_RUN` (20) bounds one run's summaries.

### Durable upload queue

`/var/celery` is the compose volume `batch_upload_state` (`rebuild` keeps it, `startup.sh reset` drops it; it is a queue volume, not a data volume). It holds the
batch_upload broker and results, the per-user job index (`jobs/`), saved sheets (`uploads/`) and the beat schedule, so a
queued, not-yet-started upload survives a recreate and runs afterwards on the new code. Celery keeps its early ack: a
job the worker had already taken is never redelivered. At boot, `manage.py mark_interrupted_batch_jobs` (after migrate,
before the worker) turns every job left STARTED/PROGRESS into FAILURE ("interrupted by a server restart: re-upload the
sheet; rows already saved are skipped"). Rows with no UID, no Name and no File_PrimaryData cannot be recognized, so a
re-upload of a half-done sheet inserts them again (true of any re-upload). `BATCH_UPLOAD_STATE_DIR` moves the directory.
A daily beat task (`batch_upload.purge_durable_state`) removes results and consumed broker messages older than the job TTL
(`BATCH_UPLOAD_JOB_TTL_HOURS`) and saved sheets older than it that no queued job names; the entrypoint logs a warning if
`BATCH_UPLOAD_STATE_DIR` is not under `/var/celery`.

---

## 11. Disk, logs, and backups

- **Images & rollback tags** are the largest disk consumers (the app image
  alone is ~8 GB; each rollback tag pins layers). Treat tags as backups
  (Golden rule 9): inventory with `docker images`, and prefer pushing old
  tags to a private registry / `docker save` archive over deleting them.
- **Docker build cache** grows unbounded across rebuilds (tens of GB within
  weeks). `docker system df` shows it; reclaiming it (`docker builder prune`)
  is safe for *cache* (unlike images/volumes) but still coordinate with the
  box owner: a cold cache makes the next build slow, and on shared boxes a
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
  responsibility and should land on storage **off the deploy host**; see
  NExtSTEPS.md's backup section for what to dump.

---

## Appendix: paid acceptance & evidence bundles

The full greenfield acceptance bar ("every shipped plugin op works live through
the real stack") is paid and gated. The commands, and the zero-spend re-checks of
a recorded bundle, are in [`NessieAI/tests/README.md`](NessieAI/tests/README.md)
("Paid acceptance", "Bundle re-check"); the CC acceptance procedure is
[`NessieAI/cc/DEPLOY.md`](NessieAI/cc/DEPLOY.md). Bundles are real artifacts from
real runs: markdown is never proof.
