#!/bin/bash
# Throwaway containers for a seed refresh. Never touches a compose stack or its volumes.
#   containers.sh work      <prefix>-mysql + <prefix>-neo4j (the copy that gets cleaned), fresh volumes
#   containers.sh verify    <prefix>-verify-mysql + <prefix>-verify-neo4j shaped like install's (repo db init script)
#   containers.sh down      remove all four and their volumes
# Images: MYSQL_IMAGE (default mysql:8.0), NEO4J_IMAGE (default neo4j:latest; use the source box's version).
set -euo pipefail
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$KIT/../../../.." && pwd)"
P=${SEED_PREFIX:-seedrefresh}
MPW=${SEED_MYSQL_PW:-${P}root}
NPW=${SEED_NEO4J_PW:-${P}pass}
MYSQL_IMAGE=${MYSQL_IMAGE:-mysql:8.0}
NEO4J_IMAGE=${NEO4J_IMAGE:-neo4j:latest}

rm_set() { for n in "$@"; do docker rm -f "$n" >/dev/null 2>&1 || true; docker volume rm "$n-data" >/dev/null 2>&1 || true; done; }

wait_up() {  # MYSQL_CONTAINER NEO4J_CONTAINER [DB_TO_WAIT_FOR]
  for _ in $(seq 1 90); do
    docker exec -e MYSQL_PWD="$MPW" "$1" mysql -uroot -N -e "SHOW DATABASES LIKE '${3:-mysql}'" 2>/dev/null | grep -q . && break; sleep 2
  done
  for _ in $(seq 1 90); do docker exec "$2" cypher-shell -u neo4j -p "$NPW" "RETURN 1" >/dev/null 2>&1 && break; sleep 2; done
}

neo4j_run() {  # NAME PORT
  docker run -d --name "$1" --memory 4g --memory-swap 4g -e NEO4J_AUTH="neo4j/$NPW" \
    -e NEO4J_server_memory_heap_max__size=2G -e NEO4J_server_memory_pagecache_size=1G -e NEO4J_db_transaction_timeout=0 \
    -p "127.0.0.1:$2:7687" -v "$1-data:/data" "$NEO4J_IMAGE" >/dev/null
}

case "${1:?work|verify|down}" in
  work)
    rm_set "$P-mysql" "$P-neo4j"
    docker run -d --name "$P-mysql" --memory 3g --memory-swap 3g -e MYSQL_ROOT_PASSWORD="$MPW" -v "$P-mysql-data:/var/lib/mysql" \
      "$MYSQL_IMAGE" --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci \
      --innodb-buffer-pool-size=1G --max-allowed-packet=1G --skip-log-bin >/dev/null
    neo4j_run "$P-neo4j" "${NEO4J_PORT:-17687}"
    wait_up "$P-mysql" "$P-neo4j"
    echo "CREATE DATABASE seek_production; CREATE DATABASE dmac; CREATE DATABASE ${P}_work;" \
      | docker exec -i -e MYSQL_PWD="$MPW" "$P-mysql" mysql -uroot
    echo "work containers up: $P-mysql, $P-neo4j (bolt 127.0.0.1:${NEO4J_PORT:-17687})" ;;
  verify)
    rm_set "$P-verify-mysql" "$P-verify-neo4j"
    # install's db service: compose's flags, MYSQL_DATABASE=seek_production, and the repo's init script creates dmac
    docker run -d --name "$P-verify-mysql" --memory 3g --memory-swap 3g -e MYSQL_ROOT_PASSWORD="$MPW" \
      -e MYSQL_DATABASE=seek_production -e MYSQL_USER=seek -e MYSQL_PASSWORD="${P}user" -e NEXTSEEK_MYSQL_DATABASE=dmac \
      -v "$REPO/docker/scripts/db:/docker-entrypoint-initdb.d:ro,z" -v "$P-verify-mysql-data:/var/lib/mysql" \
      "$MYSQL_IMAGE" --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci --log-error-verbosity=1 >/dev/null
    neo4j_run "$P-verify-neo4j" "${VERIFY_NEO4J_PORT:-17688}"
    wait_up "$P-verify-mysql" "$P-verify-neo4j" dmac
    echo "verify containers up: $P-verify-mysql, $P-verify-neo4j (bolt 127.0.0.1:${VERIFY_NEO4J_PORT:-17688})" ;;
  down)
    rm_set "$P-mysql" "$P-neo4j" "$P-verify-mysql" "$P-verify-neo4j"; echo "removed" ;;
esac
