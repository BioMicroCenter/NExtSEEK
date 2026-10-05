# Runs ON THE BOX (fetch.sh sends it, with BOX_REPO and BOX_PREFIX set). Read-only facts before a fetch:
# source commit, disk, containers and images, MySQL version and sizes, accounts, sync state, Neo4j version,
# filestore size, load. Prints no secrets.
cd "${BOX_REPO:?}" || exit 1
P="${BOX_PREFIX:-}"
echo "== commit"; git log -1 --format='%H %ad %s' --date=iso
echo "== df"; df -h / /tmp 2>/dev/null
echo "== containers"; docker ps --format '{{.Names}}|{{.Image}}|{{.Status}}'
for c in seek-mysql neo4j seek nextseek; do echo "== image $P$c"; docker inspect "$P$c" --format '{{.Config.Image}} {{.Image}}'; done
echo "== mysql"; docker exec "${P}seek-mysql" sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -e "SELECT VERSION(); SELECT table_schema, COUNT(*), ROUND(SUM(data_length+index_length)/1e6) FROM information_schema.tables WHERE table_schema IN (\"seek_production\",\"dmac\") GROUP BY table_schema; SELECT COUNT(*) FROM seek_production.users; SELECT COUNT(*) FROM seek_production.people; SELECT id, title FROM seek_production.projects; SELECT COUNT(*) FROM dmac.graph_sync_outbox WHERE done_at IS NULL; SELECT id, kind, status, started_at FROM dmac.graph_sync_run WHERE finished_at IS NULL;"'
echo "== neo4j"; docker exec "${P}neo4j" sh -c 'echo $NEO4J_TARBALL; ls /var/lib/neo4j/plugins 2>/dev/null'
echo "== filestore"; docker exec "${P}seek" sh -c 'du -sh /seek/filestore; du -sh /seek/filestore/* | sort -h | tail -12'
echo "== app"; docker exec "${P}nextseek" sh -c 'ls /app/.venv/bin/python 2>&1'
echo "== load"; uptime; free -g
