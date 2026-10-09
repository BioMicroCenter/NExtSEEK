---
name: clean_up
description: >-
  Use when a box's disk is filling or low ("dev has 23 GB free", "the preflight wants 30 GB", "clean up old
  images", "prune the build cache"), or after a rebuild leaves rollback tags behind. Surveys read-only, sorts
  every candidate into keep / retire / prune, hands back one command block per kind and runs a block only on the
  operator's go, with `df -h /` before and after. Never prunes on its own.
---

# clean_up: free disk on a NExtSEEK box

The `deploy` skill's gate 8 forbids pruning images, tags or volumes without per-item owner approval, and
`startup/CLAUDE.md` keeps the disk preflight from ever pruning. This skill is the approved way to ask: it lists
the items by name, the operator says go, then you run. Box host, account and path come from
`~/.config/nextseek/boxes.json` (see `deploy/references/boxes.md`); run everything on the box as its service
account, one ssh at a time, and quote the whole remote command (or base64 a script).

## 1. Survey (read-only)

`df -h /` is the truth. Docker's own numbers mislead on the containerd image store (docker 29): image sizes
double-count shared layers and the build cache overlaps the images. Check which store you have:
`docker info | grep -i "storage driver\|driver-type"` showing `io.containerd.snapshotter` means **all images and
build cache are under `/var/lib/containerd`** (root-only; `du` it with a throwaway container that mounts it
`:ro`), while `/var/lib/docker` holds only volumes and logs.

Look at: `docker system df -v`, `docker ps -a` (stopped containers pin images), `docker volume ls -f dangling=true`,
`/var/lib/docker/containers/*/*-json.log`, the service account's `~/backups`, launch folders and caches
(`~/.cache`), the MySQL volume (binlogs) and the Neo4j volume (`transactions/`), `journalctl --disk-usage`. Write
the table (item, size, what, used by the running stack?). Done when the top items sum to within 10% of `df` used.

## 2. Sort

- **Keep:** running containers' images and named volumes; the newest 2 `pre-*` rollback tags of each image name;
  `:latest` and every non-`pre-` tag (`dmac-assistant:poc` is the cc-agent build target);
  `ghcr.io/biomicrocenter/nextseek:baseline-20260805`; the newest dated backup of each database.
- **Retire (a):** stopped containers and the other `pre-*` tags. Candidates:
  `docker images --format '{{.Repository}}:{{.Tag}}' | grep ':pre-' | sort -t: -k1,1 -k2,2r | awk -F: '++n[$1]>2'`
  (tags sort by their UTC stamp). `docker rmi` refuses a tag a container uses.
- **Prune (b):** build cache older than 72 h, so the next rebuild is not cold.
- **Other (c):** unused anonymous volumes (64-hex names only), superseded dumps, old Playwright chromium versions.
- **Ask, never decide:** a backup that is the only copy of something, a stopped container's volume, old podman
  stores, anything on a live database.

## 3. Three blocks, each on the operator's go, one at a time

Print `df -h / | tail -1` at the start and end of each. Remove only items you named.

```bash
# (a) stopped containers and old rollback tags: docker rm <names>; docker rmi <each tag listed>
# (b) build cache; run AFTER (a): layers the cache holds are not freed until it goes
docker builder prune -f --filter until=72h; docker image prune -f
# (c) unused anonymous volumes (plus any dump or cache dir you listed by name)
docker volume ls -qf dangling=true | grep -E '^[0-9a-f]{64}$' | xargs -r docker volume rm
```

Judge (a) and (b) together. Never run while a build is running, never `docker volume prune` (it takes named
volumes too), never `--volumes`, never `docker system prune`. Record the before/after in the session ledger. In a
launch report, the rollback tags in `left_behind[]` are the `clean_up` dispositions this skill takes.

## 4. Live databases: propose only

Binlogs (MySQL keeps 30 days by default): `SET PERSIST binlog_expire_logs_seconds = 604800;` then
`PURGE BINARY LOGS BEFORE '<date>';`. Neo4j transaction logs: shorten
`db.tx_log.rotation.retention_policy`, which needs a config change and a Neo4j restart. Container json logs are
usually tiny (73 MB on dev): do not add a log cap unless `du` shows they matter.

## Seen on fairdata-dev, 2026-10-09

228 G used, 23 G free. 181 G was images and build cache (14 `nextseek-nextseek:pre-*` tags at ~6.6 G unique each,
10 `dmac-assistant` tags, 80 G of cache), 24 G docker volumes (6.5 G MySQL binlogs, 5.8 G Neo4j tx logs), 12 G home.
Blocks (a) then (b): 23 G -> 141 G free; (a) alone freed 86 G, (b) 32 G.
