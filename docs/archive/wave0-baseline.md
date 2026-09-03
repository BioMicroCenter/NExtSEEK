# Wave 0 — baseline on untouched `dev`

**Branch:** `dev-260718`, forked from `origin/dev` @ `8f5479a9d5b9c0a7e4660bb1469fda2cfbed2c30` (taishajo, 2026-07-18)
**Captured:** 2026-07-21
**Purpose:** record what is already failing *before* any v3 feature is replayed, so later waves can be attributed.

Only three commits sit on top of the fork point, all confined to `startup/` and
`docker-compose.yml` (see §4). Nothing under `nextseek_api/`, `seek/`,
`api_app/`, or `chat_nextseek/` has been touched, so every result below is dev's
own.

---

## 1. Install

`./startup.sh install --instance v3-merge` → **9/9**, alongside the pre-existing
v3 stack.

| | |
|---|---|
| Instance / prefix | `v3-merge` / `v3-merge-` |
| Compose project | `nextseek-v3-merge` |
| NExtSEEK | http://localhost:8000 (HTTP 200) |
| SEEK | http://localhost:3001 |
| Neo4j HTTP / Bolt | 7475 / 7688 |
| **db** | **127.0.0.1:3307** (was hardcoded 3306 — see §4) |
| CC stack | `nextseek-sidecar` + `dmac-bedrock-proxy`, both healthy |
| Sidecar CC volume | `v3-merge-dmac-cc-users` (correctly instance-scoped) |

Two manual steps were required and are **not** yet fixed in the tree:

- v3's `nextseek-sidecar` / `dmac-bedrock-proxy` containers had to be `docker rm`'d
  first, because those names are unprefixed (§5, item 4). `docker stop` is not
  enough — Docker reserves names by container *existence*, not running state.
- The `dmac-cc-net` warning (`a network with name dmac-cc-net exists but was not
  created for project "nextseek-v3-merge"`) is expected and non-fatal (§5, item 5).

---

## 2. Test baseline

### 2.1 `startup/` — GREEN

```bash
cd startup && uv run --group test pytest tests/ -q
```
**179 passed.** (Includes 3 tests added by the commits in §4.)

### 2.2 Django suite — 38 real failures + 1 collection error

```bash
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings v3-merge-nextseek \
  uv run pytest seek nextseek_api api_app --no-migrations -q \
  --ignore=seek/timeline/services/nhp_cache_test.py
```
**3620 passed · 69 failed · 52 skipped**

Two caveats change how that 69 should be read:

**(a) `--ignore` is mandatory.** `seek/timeline/services/nhp_cache_test.py:5` does
`from ..services.nhp_service import ...` → `ImportError: attempted relative
import beyond top-level package`. This **aborts collection for the entire run**,
so the suite cannot be run at all without excluding it. Pre-existing on dev.

**(b) 32 of the 69 are environmental, not real.** All of
`nextseek_api/cc_assistant/tests/test_step7_compose_deploy.py` shells out to
`docker compose config`, and **there is no docker CLI inside the container** —
they fail with `FileNotFoundError: 'docker'`. They are host-side tests.

Copying the host `docker` binary + compose plugin into the container
(`/var/run/docker.sock` is already mounted) and re-running gives the true result:

> **116 passed · 1 failed**

The single real failure is `test_compose_config_nextseek_container_name_pinned`:
`assert 'v3-merge-nextseek' == 'nextseek'`. It passes with `INSTANCE_PREFIX=`
empty, i.e. **the test is incompatible with any `--instance` install**, by
design — see §5 item 5, which is the important consequence.

**Net real Django failures: 38** (37 non-compose + 1 compose), grouped:

| Count | File |
|---|---|
| 14 | `nextseek_api/tests/test_assistant_unit.py` |
| 10 | `nextseek_api/tests/test_services_assistant.py` |
| 6 | `nextseek_api/tests/test_views.py` |
| 2 | `nextseek_api/tests/test_services_samples.py` |
| 2 | `nextseek_api/tests/test_helpers.py` |
| 2 | `nextseek_api/batch_delete/tests/test_models.py` |
| 1 | `nextseek_api/tests/test_ws_origin.py` |
| 1 | `test_step7_compose_deploy.py` (instance-prefix, above) |

Sample causes (all pre-existing on dev):
- `test_helpers.py:296,313` — `KeyError: 'auth'`
- `test_services_samples.py:605,627` — asserts the nested `(a) OR ((b) OR (c))`
  string but gets a **list**; dev's UID fast-path changed the return shape and
  these two assertions were not updated
- `batch_delete/models.py:12,14` — `ValueError: block_reason must be None when
  can_delete=True`

### 2.3 `chat_nextseek/` — 7 failures + 18 errors, plus 3 harness-blocked files

```bash
cd chat_nextseek && uv run pytest tests/ --ignore=tests/evaluator -q
```
Aborts with **3 collection errors**: `test_lineage_leaves.py`,
`test_portable_contract.py`, `test_report_code.py`.

**These are a pytest path artifact, not broken source.** The same imports succeed
outside pytest:

```
OK    chat_nextseek.helpers.enumerate_lineage_leaves
OK    chat_nextseek.reports.outputs.generate_report_outputs
```

`reports/outputs.py:164` defines `generate_report_outputs`, and `helpers/` is a
real package with `__init__.py`. Under pytest the names resolve to
`(unknown location)` — a namespace-package shadow, most likely the
`chat_nextseek/` directory itself masking `src/chat_nextseek/`. Reproduces
identically in-container and on the host with a clean venv, so it is not a
container-packaging problem. `--import-mode=importlib` makes it worse (30 errors).

Excluding those three:

> **391 passed · 7 failed · 2 xfailed · 18 errors**

All 18 errors are in `tests/test_shortlist_recall.py` (semantic/legacy path
recall), which appear to need live services.

`tests/evaluator/` is excluded per `chat_nextseek/CLAUDE.md:21` (Django-stack
dependent). Its `test_normalization_additional.py` additionally imports
`_persist_bundle_reply`, which genuinely does not exist in `orchestrator.py` —
the only import of the three that is actually absent.

---

## 3. How to reproduce

```bash
# startup
cd startup && uv run --group test pytest tests/ -q

# django (note the mandatory --ignore)
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings v3-merge-nextseek \
  uv run pytest seek nextseek_api api_app --no-migrations -q \
  --ignore=seek/timeline/services/nhp_cache_test.py

# compose tests need a docker CLI inside the container:
docker cp /usr/bin/docker v3-merge-nextseek:/usr/local/bin/docker
docker exec v3-merge-nextseek mkdir -p /usr/local/lib/docker/cli-plugins
docker cp /usr/libexec/docker/cli-plugins/docker-compose \
  v3-merge-nextseek:/usr/local/lib/docker/cli-plugins/docker-compose
# (ephemeral — lost on container recreate)

# chat_nextseek
cd chat_nextseek && uv run pytest tests/ --ignore=tests/evaluator -q \
  --ignore=tests/test_lineage_leaves.py \
  --ignore=tests/test_portable_contract.py \
  --ignore=tests/test_report_code.py
```

---

## 4. Commits on top of the fork point

| Commit | Change |
|---|---|
| `32c0bfc` | `db` added to `DEFAULT_PORTS` + `port_env_map`; compose publish → `${DB_PORT:-3306}` |
| `e544856` | `DB_PORT` added to `render_root_env`'s allowlist so it reaches `.env` |
| `bcfe8e2` | `dmac-cc-users` volume → `name: ${INSTANCE_PREFIX:-}dmac-cc-users` |

All three verified live: db on 3307, `.env` carries `DB_PORT="3307"`, sidecar
mounted `v3-merge-dmac-cc-users`. All are backward compatible — an empty
`INSTANCE_PREFIX` resolves to the historical bare values.

---

## 5. Multi-instance findings for upstream

One root cause: **services and resources added after the `--instance` work were
never wired into it.** Items 1-3 are fixed above; 4-5 are not.

1. **db port never allocated per instance** — absent from `DEFAULT_PORTS`,
   absent from `port_env_map`, hardcoded `127.0.0.1:3306:3306` in compose. Neither
   `--instance` nor `--port-offset` moved it, so a second instance's `db` could
   never bind. *Fixed `32c0bfc`.*

2. **`DB_PORT` missing from `.env`** — `render_root_env`'s allowlist omitted it.
   Install worked (startup passes its env dict directly) but the documented
   workflow `docker compose up -d --build nextseek` reads `.env`, would fall back
   to 3306, and collide on the **first manual rebuild**. *Fixed `e544856`.*

3. **`dmac-cc-users` declared `external: true` with no `name:`** while
   `volumes.py` creates `{prefix}dmac-cc-users`. Compose resolved the bare name,
   so the sidecar would mount **another instance's** CC per-user tree at
   `SIDECAR_STAGING_DIR` — crossing an isolation boundary that is meant to be
   closed. Not a startup failure; a silent one. *Fixed `bcfe8e2`.*

4. **`container_name` unprefixed on two services** — `docker-compose.yml:69`
   (`dmac-bedrock-proxy`) and `:135` (`nextseek-sidecar`). The other six carry
   `${INSTANCE_PREFIX:-}`; `nextseek_nginx` and `cc-agent` declare none and get
   compose's project prefix automatically. Consequence: **only one instance on a
   host can run a CC stack at a time.** *Not fixed — see item 5 for why this is
   not a simple rename.*

5. **The network segmentation check fails open under `--instance`.** This is the
   one with real weight, and it explains item 4.

   `validate_step7_compose_deploy.check_network_segmentation_ok` flags a forbidden
   peer on the agent network with:

   ```python
   bad = sorted(nm for nm in names
                if nm == "nextseek" or any(rx.search(nm) for rx in _cc_peer_res().values()))
   ```

   The exact-equality arm exists because `_PEER_RE` deliberately has no
   `"nextseek"` stem (it would false-positive the legitimately dual-homed
   `nextseek-nextseek_nginx-1`). So correctness depends on the app container
   being named *exactly* `nextseek`.

   Under `--instance` it is `v3-merge-nextseek`, which matches **neither** arm:

   | container | `== "nextseek"` | `_PEER_RE` | flagged |
   |---|---|---|---|
   | `nextseek` | yes | no | **yes** |
   | `v3-merge-nextseek` | no | no | **no** |
   | `v3-merge-seek-mysql` | no | yes | yes |

   If the app container drifted onto `dmac-cc-net` in a prefixed install, the
   check would **not** detect it. Prefixing the two `container_name`s in item 4
   without updating this validator would extend the same blind spot to the proxy
   and sidecar.

   Related: `dmac-cc-net` is pinned to a literal name and owned by whichever
   project created it, so instances **share one CC network** — right now v3's
   network carries the new instance's nginx. `cc_engine.py:54` already reads
   `NEXTSEEK_CC_NETWORK`, so the plumbing for a per-instance network is half
   present.

   A complete fix touches: `docker-compose.yml` (2 container names + network),
   `validate_step7_compose_deploy.check_network_segmentation_ok`,
   `verify_merge_survivals.py:70-71` (exact-equality assertions on both names),
   `step7_gate3d_live.py:88,242,243` (hardcoded `docker inspect nextseek-sidecar`),
   and `test_step7_compose_deploy.py:1834`. `step7_gate3d_live.py:50` already
   supports `DMAC_PROXY_CONTAINER`.

---

## 6. Wave gate

Any later wave's test run is compared against §2. A regression is a **new**
failure, not merely a failing test — the counts above are the zero point:

- `startup/` — 179 passed
- Django — 3620 passed / 38 real failures (+1 mandatory `--ignore`)
- `chat_nextseek/` — 391 passed / 7 failed / 18 errors (+3 harness-blocked)
