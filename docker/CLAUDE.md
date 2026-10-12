# Working in `docker/`

The AI images' rules are in `NessieAI/docker/CLAUDE.md`.

## Invariants

Each is enforced from outside this folder. Breaking one is a silent deployment failure or a red
suite, never a refactor.

- **The entrypoint refuses to serve on stale static or an unmigrated schema.** Both gates in `docker/scripts/entrypoint.sh` exit rather than continue; under compose's restart policy that crash-loops until fixed, which is the chosen trade. A swallowed migrate failure once masked a wedged migration for a week. Never `migrate --fake` (`nextseek_api/tests/repo_guards/test_entrypoint_migrate_failfast.py`).
- **Every long-running process of the app container is started by the entrypoint and waited on together.** The attribute worker, its dispatcher, the recovery loop and the assay-registration loop are not compose services, and `wait -n` makes any one exit restart the whole container. Add a runtime here, not as a new compose service behind a profile, or a rebuild leaves it on the old image (`nextseek_api/tests/repo_guards/test_entrypoint_attribute_runtimes.py`).
- **The attribute queue and the batch-upload queue keep separate brokers.** The attribute worker and its dispatcher get `CELERY_BROKER_URL` inline, pointing at the durable SQLite broker. A container-wide `CELERY_BROKER_URL` would move batch upload onto a volume that survives a recreate, and a queued upload would re-run after a deploy.
- **The nginx upstream stays behind a variable.** `set $upstream` makes nginx use the resolver on every request; inlining the service name caches a dead container's address after a rebuild, and nginx answers 502 until it is itself restarted.
- **The restart page keeps the 502 status and an HTML body.** `docker/nginx-optional/error-pages.conf` replaces nginx's bare 502; `ci/smoke/assertions.py` `check_gateway` reads a non-JSON 502 as a dead stack and health probes must still see a failure, so never give its `error_page` an `=<code>` (`nextseek_api/tests/repo_guards/test_nginx_error_page.py`).
- **The upstream `Host` header stays overridden.** The compose service name contains an underscore, which Django's `request.get_host()` rejects.

## Landmines

- **`docker/seek-nginx.conf` is not in the repo and must exist on the host as a file.** Compose bind-mounts it into `seek`; if it is absent when `seek` is recreated, Docker creates a directory there and SEEK crash-loops. `DEPLOYMENT.md` §3.1 has the command that renders it.
- **Never add a new published port on the dev or prod hosts.** Every published port in `docker-compose.yml` binds `127.0.0.1`, so nothing is reachable except through the operator's edge proxy, and only ports 22, 80 and 443 reach those hosts (host configuration, not tracked here). Multiplex onto an existing port instead: `docs/neo4j-programmatic-access.md` has the bolt-over-443 recipe, and the `include` in `docker/nginx.conf` is the seam for a new subpath.
- **The shipped optional drop-in has no access control, deliberately.** `docker/nginx-optional/neo4j.conf.example` explains why an allow/deny there would match the wrong client, and that Neo4j Community has no read-only role: anyone who reaches the proxied paths with the password can delete the graph.
- **`docker/nextseek.env.example` is documentation, not the render source.** The CLI renders `startup/templates/nextseek.env.template`, and the two have drifted: the example lacks `SEEK_PUBLIC_URL` and the Container-CC budget cap. Hand-copying the example leaves `SEEK_PUBLIC_URL` empty, and SEEK links are built from it.
- **`NEXTSEEK_SERVER=gunicorn` switches the WebSocket off.** The entrypoint then serves WSGI only; the chat panel falls back to polling, so progress still arrives, only later.
- **Moving `docker/scripts/` breaks its consumers with no build-time error.** The `nextseek` healthcheck calls it by absolute in-image path, the `db` service mounts `docker/scripts/db` as MySQL's init directory, and the app image's start command is the entrypoint. The failure shows only at run time, as an unhealthy container or one that will not boot.

## Test command

`nextseek_api/tests/repo_guards/` runs in the Django lane (`ci/README.md` "Running and testing");
`startup/tests/test_layout.py` runs in the startup lane (`startup/CLAUDE.md` "Test command").

## See also

- `docker/README.md`: what each file is and who uses it.
- `NessieAI/docker/README.md`, `NessieAI/docker/CLAUDE.md`: the AI images.
- `DEPLOYMENT.md` §3.1 (the SEEK config mount) and §3.2 (rebuild verbs).
- `docs/neo4j-programmatic-access.md`: the Neo4j drop-in and the bolt-over-443 recipe.
