#!/usr/bin/env bash
set -uo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 {unit|db|schema|worker|openapi|collect|lint|benchmark|coverage|full|raw-full|mutants}" >&2
  exit 64
fi

lane="$1"
case "$lane" in
  unit) test_args=(nextseek_api/tests/test_attribute_api_harness.py nextseek_api/attributes/tests) ;;
  db) test_args=() ;; # populated from the manifest's exact task-specific DB node list below
  schema) test_args=() ;; # populated from the exact task-specific schema-node contract below
  worker) test_args=(nextseek_api/attributes/tests/test_tasks.py) ;;
  openapi) test_args=(nextseek_api/attributes/tests/test_openapi.py nextseek_api/attributes/tests/test_views.py) ;;
  collect) test_args=(nextseek_api/tests nextseek_api/attributes/tests startup/tests --collect-only) ;;
  lint) command=(python -m ruff check nextseek_api/attributes nextseek_api/tests/test_attribute_api_harness.py nextseek_api/tests/test_attribute_api_db_lane.py startup/steps/schema_fixups.py startup/tests) ;;
  benchmark) test_args=() ;; # populated from the exact task-specific integration-node contract below
  coverage) command=(python scripts/run_attribute_coverage.py) ;;
  full) test_args=(nextseek_api/tests nextseek_api/attributes/tests startup/tests) ;;
  raw-full) command=(python scripts/run_attribute_coverage.py --raw-full) ;;
  mutants) command=(python scripts/run_attribute_mutants.py) ;;
  *) echo "unknown lane: $lane" >&2; exit 64 ;;
esac

export PYTHONHASHSEED="${PYTHONHASHSEED:-0}"
export TZ="${TZ:-UTC}"
export LANG="${LANG:-C.UTF-8}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-dmac.test_settings}"
export ATTRIBUTE_TEST_SHARED_DB_DENYLIST="${ATTRIBUTE_TEST_SHARED_DB_DENYLIST:-dmac,seek_production,test_dmac}"
export ATTRIBUTE_TEST_REQUIRE_DISPOSABLE_DB_UUID="${ATTRIBUTE_TEST_REQUIRE_DISPOSABLE_DB_UUID:-1}"
export UV_PROJECT="${UV_PROJECT:-/home/taishajo/work/NExtSEEK-dev}"
export SEEK_HOST="${SEEK_HOST:-attribute-seek}"
export SEEK_HOSTNAME="${SEEK_HOSTNAME:-attribute-seek}"
export NEXTSEEK_HOSTNAME="${NEXTSEEK_HOSTNAME:-localhost}"
export NEXTSEEK_NEO4J_HOST="${NEXTSEEK_NEO4J_HOST:-attribute-neo4j}"
export NEXTSEEK_NEO4J_PASSWORD="${NEXTSEEK_NEO4J_PASSWORD:-attribute-test}"
export DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-attribute-test-secret-key}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"

repo_root="$(git rev-parse --show-toplevel)" || exit 65
cd "$repo_root" || exit 65
# Container-side git needs the real git-common-dir reachable at the identical host
# absolute path (linked worktrees resolve via absolute paths recorded on the host),
# and needs an explicit safe.directory allowance since the container's root user
# does not own the host-owned bind-mounted tree. Neither changes any test/runner argv.
git_common_dir="$(git rev-parse --path-format=absolute --git-common-dir)" || exit 65
git_common_root="$(dirname "$git_common_dir")"
extra_git_mount=()
if [[ "$git_common_root" != "$repo_root" ]]; then
  extra_git_mount=(-v "$git_common_root:$git_common_root")
fi
git_safe_env=(-e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*')
git_metadata_mount=()
if [[ -f "$repo_root/.git" ]]; then
  git_admin_dir="$(sed -n '1s/^gitdir: //p' "$repo_root/.git")"
  if [[ -d "$git_admin_dir" ]]; then
    git_metadata_mount=(-v "$git_admin_dir:$git_admin_dir:ro")
    git_common_dir="$(cd "$git_admin_dir" && cd "$(cat commondir)" && pwd)"
    if [[ "$git_common_dir" != "$git_admin_dir" && -d "$git_common_dir" ]]; then
      git_metadata_mount+=(-v "$git_common_dir:$git_common_dir:ro")
    fi
  fi
fi
branch_name="$(git branch --show-current)"
if [[ "$branch_name" =~ -t([0-9][0-9])$ ]]; then
  task_id="task-${BASH_REMATCH[1]}"
else
  task_id="${ATTRIBUTE_TEST_TASK_ID:-task-00}"
fi
if [[ "$lane" == "db" ]]; then
  mapfile -t test_args < <(python3 - "$task_id" <<'PY'
import json, sys
manifest = json.load(open("/home/taishajo/work/state/attribute-viewset/VERIFICATION-MANIFEST.json"))
contract = manifest["runner_contract"]["db_lane_contract"]
if contract["pytest_marker_expression"] is not None or not contract["forbid_deselection"]:
    raise SystemExit("invalid DB lane contract")
for node in contract[sys.argv[1]]["node_arguments"]:
    print(node)
PY
  )
  [[ ${#test_args[@]} -gt 0 ]] || { echo "missing exact DB lane selection for $task_id" >&2; exit 64; }
fi
if [[ "$lane" == "schema" ]]; then
  mapfile -t test_args < <(python3 - "$task_id" <<'PY'
import json, sys
manifest = json.load(open("/home/taishajo/work/state/attribute-viewset/VERIFICATION-MANIFEST.json"))
for node in manifest["runner_contract"]["schema_lane_contract"][sys.argv[1]]["node_arguments"]:
    print(node)
PY
  )
  [[ ${#test_args[@]} -gt 0 ]] || { echo "missing exact schema lane selection for $task_id" >&2; exit 64; }
fi
if [[ "$lane" == "benchmark" ]]; then
  mapfile -t test_args < <(python3 - "$task_id" <<'PY'
import json, sys
manifest = json.load(open("/home/taishajo/work/state/attribute-viewset/VERIFICATION-MANIFEST.json"))
for node in manifest["runner_contract"]["benchmark_lane_contract"][sys.argv[1]]["node_arguments"]:
    print(node)
PY
  )
  [[ ${#test_args[@]} -gt 0 ]] || { echo "missing exact benchmark integration node for $task_id" >&2; exit 64; }
  if [[ "$task_id" == "task-10" || "$task_id" == "task-11" ]]; then
    export ATTRIBUTE_PERFORMANCE_MATRIX_MODE=full
    export ATTRIBUTE_T06_CHUNK_SELECTION_POINTER=/home/taishajo/work/state/attribute-viewset/evidence/task-06/chunk-selection.pointer.json
    ATTRIBUTE_T06_CHUNK_SELECTION="$(python3 - "$ATTRIBUTE_T06_CHUNK_SELECTION_POINTER" <<'PY'
import hashlib, json, pathlib, sys
pointer_path = pathlib.Path(sys.argv[1])
root = pathlib.Path("/home/taishajo/work/state/attribute-viewset/evidence/task-06").resolve()
if pointer_path.is_symlink() or not pointer_path.is_file():
    raise SystemExit("T06 selection pointer is not an ordinary file")
pointer = json.loads(pointer_path.read_bytes())
if set(pointer) != {"schema_version", "path", "sha256"} or pointer["schema_version"] != "attribute-chunk-selection-pointer/v1":
    raise SystemExit("T06 selection pointer shape drift")
artifact = pathlib.Path(pointer["path"])
resolved = artifact.resolve(strict=True)
if artifact.is_symlink() or root not in resolved.parents:
    raise SystemExit("T06 selection artifact escapes task evidence or is symlinked")
payload = resolved.read_bytes()
if hashlib.sha256(payload).hexdigest() != pointer["sha256"] or resolved.name != pointer["sha256"] + ".json":
    raise SystemExit("T06 selection pointer hash/path mismatch")
selection = json.loads(payload)
if selection.get("schema_version") != "attribute-chunk-selection/v1":
    raise SystemExit("T06 selection schema drift")
print(resolved)
PY
)" || exit $?
    export ATTRIBUTE_T06_CHUNK_SELECTION
  fi
fi
run_id="${ATTRIBUTE_EVIDENCE_RUN_ID:-$(date --utc +%Y%m%dT%H%M%S.%NZ)-$$-${RANDOM}}"
[[ "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || { echo "invalid evidence run id" >&2; exit 64; }
evidence_root="/home/taishajo/work/state/attribute-viewset/evidence/${task_id}/${lane}/${run_id}"
mkdir -p "$(dirname "$evidence_root")"
mkdir "$evidence_root" || { echo "evidence run collision" >&2; exit 73; }
export ATTRIBUTE_EVIDENCE_TASK_ID="$task_id"
export ATTRIBUTE_EVIDENCE_RUN_ROOT="$evidence_root"
export ATTRIBUTE_TEST_FAULT_CONTROL="$evidence_root/fault-control.json"
stdout_path="$evidence_root/${lane}.stdout.log"
stderr_path="$evidence_root/${lane}.stderr.log"
started_at="$(date --utc +%Y-%m-%dT%H:%M:%SZ)"

if [[ "$lane" != "lint" && "$lane" != "coverage" && "$lane" != "mutants" ]]; then
  command=(python -m pytest -q -p no:cacheprovider)
  if [[ "$lane" == "full" ]]; then command+=(-p scripts.attribute_pytest_reporter); fi
  command+=("${test_args[@]}")
fi
reference_image_id="$(docker image inspect --format '{{.Id}}' nextseek-nextseek)" || exit 65
if [[ "$reference_image_id" != "sha256:1fc2e7175e9d215c1659945ad19a666c2e29a1360091babbaa310148dafb000c" ]]; then
  echo "reference image identity drift" >&2; exit 65
fi
chown_evidence_root() {
  docker run --rm \
    -v /home/taishajo/work/state/attribute-viewset:/home/taishajo/work/state/attribute-viewset \
    "$reference_image_id" chown -R "$(id -u):$(id -g)" "$evidence_root"
}
boundary_env="$evidence_root/.boundary.env"
unset ATTRIBUTE_TEST_DATABASE_PRECREATED ATTRIBUTE_TEST_DATABASE_NAME ATTRIBUTE_TEST_DISPOSABLE_DB_UUID
network_name="attribute-evidence-$(python3 -c 'import uuid; print(uuid.uuid4())')"
db_container="attribute-db-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
rails_container="attribute-rails-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
db_alias="attribute-db"
rails_alias="attribute-seek"
ATTRIBUTE_TEST_DB_USER=root
ATTRIBUTE_TEST_DB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
ATTRIBUTE_TEST_DB_PORT=3306
boundary_prepared=0
cleanup_complete=0
cleanup_boundary() {
  local status=0
  [[ "$cleanup_complete" -eq 1 ]] && return 0
  docker rm -f "$rails_container" >/dev/null 2>&1 || true
  if [[ "$boundary_prepared" -eq 1 ]]; then
    "${boundary_tool[@]}" finalize --identity "$evidence_root/boundary-identity.json" \
      --run-root "$evidence_root" || status=$?
    boundary_prepared=0
  fi
  docker rm -f "$db_container" >/dev/null 2>&1 || true
  docker network rm "$network_name" >/dev/null 2>&1 || true
  rm -f "$evidence_root/.rails-seed.json" "$evidence_root/.rails-oracle.json" \
    "$evidence_root/.rails-source-hashes.txt"
  cleanup_complete=1
  return "$status"
}
# Protection precedes network, database, and Rails creation, and is safe after any partial start.
trap cleanup_boundary EXIT INT TERM
database_image_id="$(docker image inspect --format '{{.Id}}' mysql:8.0)" || exit 65
[[ "$database_image_id" == "sha256:7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b" ]] || {
  echo "disposable database image identity drift" >&2; exit 65;
}
docker network create --internal --label com.nextseek.attribute-evidence=true "$network_name" >/dev/null || exit 65
network_id="$(docker network inspect --format '{{.Id}}' "$network_name")" || exit 65
docker run -d --name "$db_container" --network "$network_name" --network-alias "$db_alias" \
  --label com.nextseek.attribute-evidence=true --label "com.nextseek.attribute-network=$network_id" \
  -e MYSQL_ROOT_PASSWORD="$ATTRIBUTE_TEST_DB_PASSWORD" "$database_image_id" \
  --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci >/dev/null || exit 65
for _ in {1..120}; do
  docker exec "$db_container" mysqladmin ping -uroot -p"$ATTRIBUTE_TEST_DB_PASSWORD" --silent >/dev/null 2>&1 && break
  sleep 0.25
done
docker exec "$db_container" mysqladmin ping -uroot -p"$ATTRIBUTE_TEST_DB_PASSWORD" --silent >/dev/null 2>&1 || exit 65
ATTRIBUTE_TEST_DB_CONTAINER="$db_alias"
export ATTRIBUTE_TEST_DB_USER ATTRIBUTE_TEST_DB_PASSWORD ATTRIBUTE_TEST_DB_CONTAINER
export ATTRIBUTE_TEST_SHARED_DB_DENYLIST ATTRIBUTE_TEST_REQUIRE_DISPOSABLE_DB_UUID
boundary_tool=(docker run --rm --network "$network_name"
  -v "$repo_root:/work" -v /home/taishajo/work/state/attribute-viewset:/home/taishajo/work/state/attribute-viewset
  "${git_metadata_mount[@]}"
  -w /work -e PATH=/app/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/work
  -e DJANGO_SETTINGS_MODULE -e PYTHONHASHSEED -e TZ -e LANG
  -e ATTRIBUTE_TEST_DB_USER -e ATTRIBUTE_TEST_DB_PASSWORD
  -e ATTRIBUTE_TEST_DB_PORT="${ATTRIBUTE_TEST_DB_PORT:-3306}"
  -e ATTRIBUTE_TEST_DB_HOST="$ATTRIBUTE_TEST_DB_CONTAINER"
  -e ATTRIBUTE_TEST_DB_CONTAINER -e ATTRIBUTE_TEST_DOCKER_NETWORK="$network_name"
  -e ATTRIBUTE_TEST_NETWORK_ID="$network_id" -e ATTRIBUTE_TEST_SHARED_DB_DENYLIST
  -e ATTRIBUTE_TEST_DATABASE_NAME -e ATTRIBUTE_TEST_DISPOSABLE_DB_UUID
  -e ATTRIBUTE_TEST_DATABASE_PRECREATED
  "$reference_image_id" python -m nextseek_api.attributes.tests.lane_boundary)
"${boundary_tool[@]}" prepare --repo /work --task "$task_id" --run-root "$evidence_root" \
  --identity "$evidence_root/boundary-identity.json" \
  --dependencies "$evidence_root/dependency-shas.json" --env-file "$boundary_env" || {
    exit 65
  }
boundary_prepared=1
chown_evidence_root || exit 65
# lane_boundary writes a mode-0600, shell-quoted file containing only the random disposable
# network/database coordinates. It never writes credentials into evidence artifacts.
source "$boundary_env"
rm -f "$boundary_env"
export ATTRIBUTE_TEST_DOCKER_NETWORK ATTRIBUTE_TEST_DATABASE_NAME
export ATTRIBUTE_TEST_DISPOSABLE_DB_UUID ATTRIBUTE_TEST_DB_HOST ATTRIBUTE_TEST_DB_PORT
export ATTRIBUTE_TEST_DATABASE_PRECREATED
if [[ "$task_id" == "task-02" ]]; then
  seek_image_id="sha256:8b5c12a005d8bc9fea51b0f2e03c06ab210b2348ab4ec09bffbfde74ac3499fc"
  [[ "$(docker image inspect --format '{{.Id}}' fairdom/seek:1.15.1)" == "$seek_image_id" ]] || exit 65
  [[ "$(docker run --rm --network none "$seek_image_id" sha256sum config/version.yml | awk '{print $1}')" == \
     "20b99f01da799b64db3227473207e70062ae22a216fd5fdab5e138b1d25256f6" ]] || exit 65
  seek_version="$(docker run --rm --network none "$seek_image_id" ruby -ryaml -e \
    'v=YAML.safe_load(File.read("config/version.yml")); print [v["major"],v["minor"],v["patch"]].join(".")')"
  [[ "$seek_version" == "1.15.1" ]] || exit 65
  rails_database_url="mysql2://root:${ATTRIBUTE_TEST_DB_PASSWORD}@${db_alias}:3306/${ATTRIBUTE_TEST_DATABASE_NAME}?encoding=utf8mb4"
  oracle="$repo_root/nextseek_api/attributes/tests/rails_auth_oracle.rb"
  docker run --rm --network "$network_name" -e DATABASE_URL="$rails_database_url" \
    "$seek_image_id" bundle exec rake db:schema:load
  oracle_key="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  docker run --rm --network "$network_name" -e DATABASE_URL="$rails_database_url" \
    -e ATTRIBUTE_ORACLE_KEY="$oracle_key" -v "$oracle:/work/rails_auth_oracle.rb:ro" \
    "$seek_image_id" bundle exec rails runner /work/rails_auth_oracle.rb seed \
    >"$evidence_root/.rails-seed.json"
  chmod 600 "$evidence_root/.rails-seed.json"
  docker run --rm --network "$network_name" -e DATABASE_URL="$rails_database_url" \
    -e ATTRIBUTE_ORACLE_KEY="$oracle_key" -v "$oracle:/work/rails_auth_oracle.rb:ro" \
    "$seek_image_id" bundle exec rails runner /work/rails_auth_oracle.rb oracle \
    >"$evidence_root/.rails-oracle.json"
  docker run --rm --network none "$seek_image_id" sha256sum \
    app/models/user.rb lib/seek/roles/accessors.rb lib/seek/roles/target.rb \
    app/models/role.rb app/models/role_type.rb >"$evidence_root/.rails-source-hashes.txt"
  docker run -d --name "$rails_container" --network "$network_name" --network-alias "$rails_alias" \
    --label com.nextseek.attribute-evidence=true -e DATABASE_URL="$rails_database_url" \
    "$seek_image_id" bundle exec rails server -b 0.0.0.0 -p 3000 >/dev/null
  for _ in {1..240}; do
    docker exec "$rails_container" ruby -rnet/http -e \
      'r=Net::HTTP.get_response(URI("http://127.0.0.1:3000/people/current")); exit([401,403].include?(r.code.to_i) ? 0 : 1)' \
      >/dev/null 2>&1 && break
    sleep 0.25
  done
  docker exec "$rails_container" ruby -rnet/http -e \
    'r=Net::HTTP.get_response(URI("http://127.0.0.1:3000/people/current")); exit([401,403].include?(r.code.to_i) ? 0 : 1)' || exit 65
  ATTRIBUTE_ORACLE_KEY="$oracle_key" ATTRIBUTE_SEEK_IMAGE_ID="$seek_image_id" \
  ATTRIBUTE_SEEK_VERSION="$seek_version" ATTRIBUTE_RAILS_CONTAINER="$rails_container" \
  python3 - <<'PY'
import hashlib, hmac, json, os
from pathlib import Path
root = Path(os.environ["ATTRIBUTE_EVIDENCE_RUN_ROOT"])
oracle = json.loads((root / ".rails-oracle.json").read_text())
signed = json.dumps({"input_row_ids": oracle["input_row_ids"], "rows": oracle["rows"]},
                    separators=(",", ":"), sort_keys=True).encode()
expected = hmac.new(bytes.fromhex(os.environ["ATTRIBUTE_ORACLE_KEY"]), signed, hashlib.sha256).hexdigest()
if not hmac.compare_digest(expected, oracle["signature"]): raise SystemExit("Rails oracle signature mismatch")
hashes = {}
for line in (root / ".rails-source-hashes.txt").read_text().splitlines():
    digest, name = line.split(None, 1); hashes[name] = digest
payload = {"schema_version": "attribute-rails-boundary/v1", "image_id": os.environ["ATTRIBUTE_SEEK_IMAGE_ID"],
           "seek_version": os.environ["ATTRIBUTE_SEEK_VERSION"], "version_source": "config/version.yml",
           "version_source_sha256": "20b99f01da799b64db3227473207e70062ae22a216fd5fdab5e138b1d25256f6",
           "source_hashes": hashes, "container_name": os.environ["ATTRIBUTE_RAILS_CONTAINER"],
           "server_uuid": json.loads((root / "boundary-identity.json").read_text())["server_identity"]["server_uuid"],
           "database_uuid": os.environ["ATTRIBUTE_TEST_DISPOSABLE_DB_UUID"],
           "seek_url": "http://attribute-seek:3000", "oracle": oracle, "oracle_verified": True}
(root / "rails-boundary.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
  export ATTRIBUTE_TEST_SEEK_URL="http://${rails_alias}:3000"
  export ATTRIBUTE_TEST_RAILS_BOUNDARY="$evidence_root/rails-boundary.json"
  export ATTRIBUTE_TEST_RAILS_SEED="$evidence_root/.rails-seed.json"
fi
export ATTRIBUTE_TEST_DATABASE_PRECREATED=1
export ATTRIBUTE_TEST_DOCKER_NETWORK ATTRIBUTE_TEST_DATABASE_NAME
export ATTRIBUTE_TEST_DISPOSABLE_DB_UUID ATTRIBUTE_TEST_DB_HOST ATTRIBUTE_TEST_DB_PORT
finalize_boundary() {
  cleanup_boundary
}
lane_local_settings="$evidence_root/.lane-local_settings.py"
cat >"$lane_local_settings" <<'PY'
ASSISTANT_PARTICIPATING_PROJECTS = set(["1"])
TEST_CASES = {}
SAMPLE_TEMPLATES_FOLDER = "/templates"
SAMPLE_TEMPLATES_FOLDER_PROJECT = "1"
PUBLISH_URL = "http://attribute-seek:3000"
PUBLISH_STATS_FILE = "/path/to/published_stats.xlsx"
SMART_SEARCH_URL = ""

class _AttributeLaneChatConfig:
    CONFIG_VERBOSE = False

NEXTSEEK_CHAT_CONFIG = _AttributeLaneChatConfig()
NEXTSEEK_CHAT_CONFIG_PROD = None
PY
chmod 600 "$lane_local_settings"
container_args=(--rm --network "${ATTRIBUTE_TEST_DOCKER_NETWORK:?required disposable network}"
  -v "$repo_root:/work" -v /home/taishajo/work/state/attribute-viewset:/home/taishajo/work/state/attribute-viewset
  -v "$lane_local_settings:/work/dmac/local_settings.py:ro"
  "${git_metadata_mount[@]}"
  -w /work -e PATH=/app/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/work
  -e PYTHONHASHSEED -e TZ -e LANG -e DJANGO_SETTINGS_MODULE -e DJANGO_SECRET_KEY
  -e HF_HUB_OFFLINE -e TRANSFORMERS_OFFLINE
  -e SEEK_HOST -e SEEK_HOSTNAME -e NEXTSEEK_HOSTNAME -e NEXTSEEK_NEO4J_HOST -e NEXTSEEK_NEO4J_PASSWORD
  -e ATTRIBUTE_TEST_SHARED_DB_DENYLIST -e ATTRIBUTE_TEST_REQUIRE_DISPOSABLE_DB_UUID
  -e ATTRIBUTE_TEST_DB_HOST -e ATTRIBUTE_TEST_DB_PORT -e ATTRIBUTE_TEST_DB_USER
  -e ATTRIBUTE_TEST_DB_PASSWORD -e ATTRIBUTE_TEST_DATABASE_NAME -e ATTRIBUTE_TEST_DISPOSABLE_DB_UUID
  -e ATTRIBUTE_TEST_DATABASE_PRECREATED=1
  -e ATTRIBUTE_TEST_SEEK_URL -e ATTRIBUTE_TEST_RAILS_BOUNDARY -e ATTRIBUTE_TEST_RAILS_SEED
  -e ATTRIBUTE_PERFORMANCE_MATRIX_MODE -e ATTRIBUTE_T06_CHUNK_SELECTION_POINTER -e ATTRIBUTE_T06_CHUNK_SELECTION
  -e ATTRIBUTE_EVIDENCE_TASK_ID -e ATTRIBUTE_EVIDENCE_RUN_ROOT="$evidence_root"
  -e ATTRIBUTE_TEST_FAULT_CONTROL)
set -C
set +e
docker run "${container_args[@]}" "$reference_image_id" "${command[@]}" >"$stdout_path" 2>"$stderr_path"
child_exit=$?
chown_evidence_root || child_exit=74
set -e
set +C
if ! finalize_boundary; then child_exit=74; fi
chown_evidence_root || child_exit=74
trap - EXIT INT TERM
finished_at="$(date --utc +%Y-%m-%dT%H:%M:%SZ)"

export ATTRIBUTE_EVIDENCE_LANE="$lane" ATTRIBUTE_EVIDENCE_TASK_ID="$task_id"
export ATTRIBUTE_EVIDENCE_ROOT="$evidence_root" ATTRIBUTE_EVIDENCE_STDOUT="$stdout_path"
export ATTRIBUTE_EVIDENCE_STDERR="$stderr_path" ATTRIBUTE_EVIDENCE_STARTED="$started_at"
export ATTRIBUTE_EVIDENCE_FINISHED="$finished_at" ATTRIBUTE_EVIDENCE_EXIT="$child_exit"
export ATTRIBUTE_EVIDENCE_CWD="$repo_root"
export ATTRIBUTE_TEST_IMAGE_ID="$reference_image_id"
export ATTRIBUTE_TEST_INTEGRATION_BASE_SHA="$(python3 -c 'import json; print(json.load(open("/home/taishajo/work/state/attribute-viewset/VERIFICATION-MANIFEST.json"))["source_identity"]["base_sha"])')"
export ATTRIBUTE_TEST_TASK_HEAD_SHA="$(git rev-parse HEAD)"
python3 - <<'PY'
import ast, hashlib, json, os, pathlib, re, subprocess, tempfile

root = pathlib.Path(os.environ["ATTRIBUTE_EVIDENCE_ROOT"])
stdout = pathlib.Path(os.environ["ATTRIBUTE_EVIDENCE_STDOUT"])
stderr = pathlib.Path(os.environ["ATTRIBUTE_EVIDENCE_STDERR"])
text = stdout.read_text(errors="replace") + "\n" + stderr.read_text(errors="replace")
def count(label):
    matches = re.findall(rf"(?<!\w)(\d+)\s+{re.escape(label)}(?:s)?(?!\w)", text)
    return int(matches[-1]) if matches else 0
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def exclusive_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True); stream.write("\n")
            stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, path)  # atomic and fails rather than overwriting an existing run artifact
        os.chmod(path, 0o444)
    finally:
        pathlib.Path(temporary).unlink(missing_ok=True)
lane = os.environ["ATTRIBUTE_EVIDENCE_LANE"]
argv = ["bash", "scripts/attribute_api_test.sh", lane]
boundary_identity = json.loads((root / "boundary-identity.json").read_text())
if boundary_identity.get("torn_down") is not True:
    raise SystemExit("database/network boundary did not finalize")
if boundary_identity.get("teardown_server_uuid") != boundary_identity.get("server_identity", {}).get("server_uuid"):
    raise SystemExit("database teardown occurred against a different server")
manifest = json.loads(pathlib.Path("/home/taishajo/work/state/attribute-viewset/VERIFICATION-MANIFEST.json").read_text())
source_identity = manifest["source_identity"]
plan_path = pathlib.Path(source_identity["canonical_plan_path"])
decisions_path = pathlib.Path(source_identity["decisions_path"])
plan_sha256 = hashlib.sha256(plan_path.read_bytes()).hexdigest()
decisions_sha256 = hashlib.sha256(decisions_path.read_bytes()).hexdigest()
if plan_sha256 != source_identity["plan_sha256"]:
    raise SystemExit("live plan bytes drift from manifest binding")
if decisions_sha256 != source_identity["decisions_sha256"]:
    raise SystemExit("live decisions bytes drift from manifest binding")
network_probe = subprocess.run(
    ["docker", "network", "inspect", boundary_identity["network_name"]],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
if network_probe.returncode == 0:
    raise SystemExit("disposable network still exists after lane finalization")
paths = subprocess.check_output(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
).split(b"\0")
tree_hash = hashlib.sha256()
for raw_path in sorted(path for path in paths if path):
    path = pathlib.Path(os.fsdecode(raw_path))
    stat = path.lstat()
    content = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
    tree_hash.update(len(raw_path).to_bytes(8, "big") + raw_path)
    tree_hash.update(stat.st_mode.to_bytes(8, "big"))
    tree_hash.update(len(content).to_bytes(8, "big") + content)
tree = tree_hash.hexdigest()
if lane == "collect":
    node_ids = sorted(line.strip() for line in stdout.read_text(errors="replace").splitlines()
                      if "::" in line and not line.lstrip().startswith(("=", "<")))
    if len(node_ids) != len(set(node_ids)) or len(node_ids) < 340:
        raise SystemExit("collect lane requires at least 340 unique node IDs")
    assertion_counts = {}
    ast_cache = {}
    for node_id in node_ids:
        parts = node_id.split("::")
        relative, scopes = parts[0], [part.split("[", 1)[0] for part in parts[1:]]
        tree_ast = ast_cache.setdefault(relative, ast.parse(pathlib.Path(relative).read_text(), filename=relative))
        candidates = tree_ast.body
        selected = None
        for scope in scopes:
            selected = next((item for item in candidates if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == scope), None)
            if selected is None:
                raise SystemExit(f"cannot bind collected node ID to AST: {node_id}")
            candidates = getattr(selected, "body", ())
        if not isinstance(selected, (ast.FunctionDef, ast.AsyncFunctionDef)):
            raise SystemExit(f"collected node ID does not terminate at a test function: {node_id}")
        assertion_counts[node_id] = sum(isinstance(item, ast.Assert) for item in ast.walk(selected))
    if set(assertion_counts) != set(node_ids):
        raise SystemExit("assertion-count keys do not exactly equal collected node IDs")
    exclusive_json(root / "collected-nodeids.json", node_ids)
    exclusive_json(root / "assertion_counts.json", assertion_counts)
collected = 0 if lane in {"collect", "lint"} else (
    count("collected") or sum(count(x) for x in ("passed", "failed", "skipped", "xfailed", "deselected"))
)
artifact_paths = [stdout, stderr]
for name in ("coverage.json", "collected-nodeids.json", "assertion_counts.json", "node-results.json", "mutants.json", "mutant-pytest-reports.json", "fault-control.json", "rails-boundary.json", "boundary-identity.json", "dependency-shas.json"):
    candidate = root / name
    if candidate.is_file():
        artifact_paths.append(candidate)
artifact_paths.extend(sorted(root.glob("mutant-M-*.json")))
artifacts = [
    {"path": path.relative_to(root).as_posix(), "sha256": sha(path), "size_bytes": path.stat().st_size}
    for path in artifact_paths
]
record = {
  "schema_version": "attribute-viewset-evidence/v1", "task_id": os.environ["ATTRIBUTE_EVIDENCE_TASK_ID"],
  "lane": lane, "argv": argv, "cwd": os.environ["ATTRIBUTE_EVIDENCE_CWD"],
  "started_at": os.environ["ATTRIBUTE_EVIDENCE_STARTED"], "finished_at": os.environ["ATTRIBUTE_EVIDENCE_FINISHED"],
  "exit_code": int(os.environ["ATTRIBUTE_EVIDENCE_EXIT"]), "stdout_sha256": sha(stdout), "stderr_sha256": sha(stderr),
  "collected": collected, "passed": count("passed"), "failed": count("failed"), "skipped": count("skipped"),
  "xfailed": count("xfailed"), "deselected": count("deselected"), "source_tree_sha256": tree,
  "plan_sha256": plan_sha256, "decisions_sha256": decisions_sha256,
  "base_sha": os.environ["ATTRIBUTE_TEST_INTEGRATION_BASE_SHA"],
  "task_head_sha": os.environ["ATTRIBUTE_TEST_TASK_HEAD_SHA"],
  "dependency_shas": json.loads((root / "dependency-shas.json").read_text()),
  "image_id": os.environ.get("ATTRIBUTE_TEST_IMAGE_ID"),
  "database_server_identity": boundary_identity["server_identity"],
  "disposable_database_uuid": boundary_identity["database_uuid"],
  "artifacts": artifacts,
}
if lane in {"coverage", "raw-full"}:
    record["coverage_sources"] = manifest["coverage_contract"]["required_source_paths"]
    hashes = {}
    for relative in record["coverage_sources"]:
        source = pathlib.Path(relative)
        files = sorted(source.rglob("*.py")) if source.is_dir() else [source]
        for path in files:
            hashes[path.as_posix()] = sha(path)
    record["coverage_source_hashes"] = hashes
record_path = root / f"{lane}.evidence.json"
exclusive_json(record_path, record)
for artifact in artifact_paths:
    os.chmod(artifact, 0o444)
PY
# RED and superseded attempts remain immutable candidates. Final task validation is a separate,
# explicit operation over selection.json; the runner never recursively validates every attempt.
exit "$child_exit"
