# `docker/`

## What this is

The front nginx configuration, its optional drop-ins, the scripts baked into the application image,
and the documentation copy of the app's env file. The four AI image contexts (`cc-runtime`,
`bedrock-proxy`, `ns-sidecar`, `eval`) are in `NessieAI/docker/` (`NessieAI/docker/README.md`).

Nothing here is a Python package. The rendered env files `docker/db.env` and `docker/nextseek.env`
also live here, untracked: the startup CLI renders them from `startup/templates/` (`DEPLOYMENT.md` §8).

## Surface

| Path | Used by | What it is |
|---|---|---|
| `docker/nginx.conf` | compose service `nextseek_nginx`, mounted as `/etc/nginx/nginx.conf` | the front proxy for the app |
| `docker/nginx-optional/` | compose service `nextseek_nginx`, mounted as `/etc/nginx/optional` | operator drop-ins; one example ships |
| `docker/scripts/entrypoint.sh` | the app image (the repo-root `Dockerfile` copies the whole tree) | the `nextseek` container's start command |
| `docker/scripts/attribute_runtime_healthcheck.py` | the `nextseek` service's healthcheck | probes the attribute runtimes without starting a second Django process |
| `docker/scripts/db/` | compose service `db`, mounted as `/docker-entrypoint-initdb.d` | creates the NExtSEEK schema and its grants at first MySQL start |
| `docker/nextseek.env.example` | readers, and the image-secret gate, which allows exactly this env file | documentation of the app env, not the render source |
| `docker/seek-nginx.conf` | compose service `seek`, mounted as `/seek/nginx.conf` | an untracked host file, rendered per box (`DEPLOYMENT.md` §3.1) |

### nginx

`docker/nginx.conf` listens on port 80 inside the container, serves collected static from the
`nextseek-static-files` volume, and proxies everything else to the Django upstream through a
variable (`set $upstream "nextseek"`), so Docker's embedded resolver re-resolves it on every request.
It maps the WebSocket `Upgrade` header, forces a Django-safe upstream `Host` (the compose service
name contains an underscore), and includes `/etc/nginx/optional/*.conf`.

The one shipped drop-in, `docker/nginx-optional/neo4j.conf.example`, is a Neo4j Browser and HTTP
Query API reverse proxy. It is inert until copied to a `*.conf` name, and enabled copies are
untracked (`docker/nginx-optional/.gitignore`). `docs/neo4j-programmatic-access.md` covers using it.

### The app container's start command

`docker/scripts/entrypoint.sh` runs `collectstatic` and `migrate` as fail-fast gates, either side of
a bounded database-readiness probe, then starts these as background processes of the one `nextseek`
container:

- the web server: daphne (ASGI, serves the assistant WebSocket) by default, or gunicorn (WSGI, no WebSocket) when `NEXTSEEK_SERVER=gunicorn`;
- the batch-upload Celery worker (`batch_upload` queue, the container-local default broker);
- the attribute-mutation Celery worker (`attribute_mutations` queue, on its own durable SQLite broker) and its outbox dispatcher, `dispatch_attribute_outbox`;
- the sync-job recovery loop, `recover_attribute_sync_jobs`;
- the assay-registration drain loop, `run_assay_registration_jobs`;
- the sample-share worker, `run_share_jobs` (the studies tool's share mode).

It then waits on all of them, so any one exiting takes the container down for compose to restart.
The attribute runtimes used to be separate compose services; the comment block in the script
records what folding them in gave up.

## Running and testing

Nothing here has a suite of its own. The guards that pin this folder's behaviour are in
`nextseek_api/tests/repo_guards/` (`test_entrypoint_migrate_failfast.py`,
`test_entrypoint_attribute_runtimes.py`, `test_compose_db_healthcheck.py`,
`test_build_context_env_guard.py`) and `startup/tests/test_layout.py`. Images are built with
`./startup.sh rebuild` (`DEPLOYMENT.md` §3.2).

## Depends on / depended on by

- `startup/templates/nextseek.env.template` and `startup/templates/db.env.template` are rendered into
  this folder by `startup/steps/config.py`.
- `docker-compose.yml` mounts `nginx.conf`, `nginx-optional/`, `scripts/db/` and `seek-nginx.conf`,
  and the `nextseek` service's healthcheck runs `/app/docker/scripts/attribute_runtime_healthcheck.py`.
- The repo-root `.dockerignore` decides what of this folder reaches the app image: the real env files
  are excluded and `docker/nextseek.env.example` is re-included, the one file that
  `startup/steps/registry_push.py` allows in its image-secret gate.
- `NessieAI/tests/e2e/import_env.py` reads the rendered env files by path.

See `docker/CLAUDE.md` for the rules and traps.
