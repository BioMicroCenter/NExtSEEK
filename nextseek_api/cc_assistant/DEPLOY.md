# Container-CC (dmac_assistant) deployment — reproducible procedure

Standing up the dmac_assistant integration on a NExtSEEK server. Two phases:
Phase A is pure Docker + repo files (no elevated privilege); Phase B edits the
running compose project and so runs as the project owner.

Topology (preserves OI-3 agent isolation):

```
            dmac-cc-net  (dedicated; NO neo4j/mysql/seek/solr)
        ┌───────────────┬───────────────────┐
        │               │                   │
  dmac-bedrock-proxy   nextseek_nginx     <per-query CC agent>   (uid 1001, ZERO aws creds)
  (holds AWS bearer    (dual-homed, also    reaches Bedrock only via the proxy,
   token; allowlist     on nextseek_default) NExtSEEK only via nginx as the user
   opus-4-8 only)
```

## Phase A — security sidecar + segmented network (Docker only)

```bash
DMAC=<dmac-assistant repo>          # latest main (post-OI-3)
NGINX=nextseek-nextseek_nginx-1     # the running nginx container

# 1. Images
cd "$DMAC"
mkdir -p build_context/docs/nextseek-api          # unblock an empty COPY source
make proxy-build                                  # -> dmac-bedrock-proxy:poc
docker buildx build --platform=linux/amd64 --load -t dmac-assistant:poc .   # -> dmac-assistant:poc
#   (do NOT `make image-build`/`image-stage`: the former needs github egress for an
#    unused vendor-sync, the latter rmtree's the committed build_context/plugins tree.)

# 2. Dedicated segmented network
docker network create dmac-cc-net

# 3. Bedrock auth-proxy: seed its OWN secret (token never leaves the proxy) + bring up
TOK=$(docker exec nextseek printenv AWS_BEARER_TOKEN_BEDROCK)
printf 'AWS_BEARER_TOKEN_BEDROCK=%s\nAWS_REGION=us-east-1\n' "$TOK" > bedrock-proxy/proxy-secret.env
chmod 600 bedrock-proxy/proxy-secret.env          # gitignored
DMAC_BEDROCK_PROXY_NETWORK=dmac-cc-net docker compose -f bedrock-proxy/docker-compose.yml up -d

# 4. Dual-home nginx onto the segmented net (non-disruptive; no restart)
docker network connect --alias nextseek_nginx dmac-cc-net "$NGINX"
```

Verify the proxy contract (from a container ON dmac-cc-net, unsigned like the agent):

```bash
docker run --rm --network dmac-cc-net --entrypoint sh dmac-assistant:poc -c '
  B=http://bedrock-proxy:8080
  curl -s -o /dev/null -w "healthz=%{http_code}\n" $B/healthz                       # 200
  curl -s -o /dev/null -w "opus=%{http_code}\n"   -X POST $B/model/us.anthropic.claude-opus-4-8/invoke \
       -H content-type:application/json -d "{\"anthropic_version\":\"bedrock-2023-05-31\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"   # 200
  curl -s -o /dev/null -w "sonnet=%{http_code}\n" -X POST $B/model/us.anthropic.claude-sonnet-4-6/invoke -d "{}"  # 403
'
docker logs dmac-bedrock-proxy 2>&1 | grep -c -E "ABSK|Authorization"   # 0  (token never logged)
```

## Phase B — host dirs, code, env on the running stack (as the project owner)

```bash
SA=<running compose project dir>
MINE=<this integration checkout>

# host root: CC sibling bind sources + nextseek worker mount. The Django worker
# runs as root; the agent writes scratch as uid 1001 -> world-writable on dev.
mkdir -p /srv/dmac/users
chmod -R 777 /srv/dmac

# ensure docker-compose.yml mounts the same host root set in docker/nextseek.env:
#   - /var/run/docker.sock:/var/run/docker.sock
#   - /srv/dmac/users:/dmac/users
```

Append to `docker/nextseek.env` (the CC route config):

```
# --- Container-CC route (dmac_assistant) ---
NEXTSEEK_CC_IMAGE=dmac-assistant:poc
NEXTSEEK_CC_NETWORK=dmac-cc-net
DMAC_BEDROCK_PROXY_URL=http://bedrock-proxy:8080
NEXTSEEK_CC_MAX_BUDGET_USD=2.00
DMAC_ROUTER_ENABLED=1
DMAC_ROUTE_CAPABILITIES_FILE=/app/dmac_assistant/build_context/route_capabilities.json
DMAC_ROUTER_MODEL_CLASS_MAP_FILE=/app/dmac_assistant/build_context/router_model_class_map.json
# consolidated Step-2 host root. This MUST match the compose bind source.
DMAC_USER_ROOT=/srv/dmac/users
DMAC_USER_ROOT_MOUNT=/dmac/users
```

Deploy hygiene for code/image changes:

1. Commit in the working clone; do not hot-patch the running container.
2. Snapshot the running service first, e.g. `docker commit nextseek nextseek-nextseek:pre-step2`.
3. Fast-forward the service-account build-context clone from the working clone using
   the established helper (`--user 1000:994`, `HOME=/tmp`, `safe.directory='*'`,
   `protocol.file.allow=always`, `merge --ff-only`).
4. Apply gitignored `docker/nextseek.env` changes directly in the service-account clone.
   Never commit or force-add `docker/nextseek.env`, `docker/db.env`, or `dmac/local_settings.py`.
5. Rebuild + recreate only the `nextseek` service through the service-account
   `docker:cli` helper with `--no-deps`.

Then the route is live:

```bash
docker run --rm --user 1000:994 -e HOME=/tmp \
  -v /var/run/docker.sock:/var/run/docker.sock -v "$SA":"$SA" -w "$SA" docker:cli \
  docker compose -p nextseek build nextseek
docker run --rm --user 1000:994 -e HOME=/tmp \
  -v /var/run/docker.sock:/var/run/docker.sock -v "$SA":"$SA" -w "$SA" docker:cli \
  docker compose -p nextseek up -d --no-deps nextseek
docker exec nextseek python -c "from nextseek_api.cc_assistant import cc_engine; print(cc_engine.cc_runner_available())"
# -> (True, 'ok')
```

## Acceptance (paid, gated)

```bash
# native 8/8 regression baseline
docker exec -e RUN_REALSTACK=1 -e SEEK_TEST_USER=.. -e SEEK_TEST_PASS=.. nextseek sh -lc \
  'cd /app && uv run python manage.py test nextseek_api.assistant.tests.test_granular_realstack \
   --settings=dmac.test_settings_realstack --noinput --keepdb -v2'

# the dmac Container-CC route, end-to-end (router=baml -> real Opus via proxy -> publish)
docker exec -e RUN_REALSTACK=1 -e SEEK_TEST_USER=.. -e SEEK_TEST_PASS=.. nextseek sh -lc \
  'cd /app && uv run python manage.py test nextseek_api.cc_assistant.tests.test_cc_realstack \
   --settings=dmac.test_settings_realstack --noinput -v2'

# reproducible re-check of the committed evidence bundle (zero spend)
docker exec nextseek python -m nextseek_api.cc_assistant.tests.validate_cc_acceptance \
  outputs/cc_acceptance/<run_id>
```
