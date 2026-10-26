#!/usr/bin/env bash
# Turn a local copy of an nf-core results folder into a reingest manifest, and
# save it where Nessie's build-upload-xlsx finds it. Nothing touches the cluster.
# The manifest it saves is test-only (no launch record): only a superuser can
# build workbooks from it, and upload-reingest refuses them.
#
#   scripts/reingest/folder_to_manifest.sh <results-folder> [options]
#
#   --parents <csv>     sample,d_seq_uid rows: each nf-core sample's parent, as a
#                       Nessie launch record would give it. Samples left out are
#                       resolved the normal way (samplesheet FASTQ path against
#                       the database), or stay unresolved.
#   --run-dir <path>    the cluster path to record as the run's run_dir
#                       (default: $LURIA_WORKING_PATH/runs/<folder name>)
#   --allow-failed-run  save it even if the execution trace shows failures
#
# The folder is the pipeline's --outdir, laid out as nf-core writes it:
# pipeline_info/ (software versions, execution trace, params), the samplesheet,
# multiqc/, and the data files the map's output rules name. Data files are only
# listed (name, size, md5), never copied, so empty placeholders with the right
# names work: `touch star_salmon/CONTROL_REP1.markdup.sorted.bam`.
#
# It runs the SAME stage script the cluster runs (granular._STAGE_SCRIPT, here with
# this Mac's python3), then hands its output to the server's own harvest code
# inside the `nextseek` container, so the manifest is the one a real harvest
# would build. Prints the manifest_id to give Nessie.
set -euo pipefail

usage() { sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
[ $# -ge 1 ] || usage
folder=""; parents=""; run_dir=""; allow_failed=0
while [ $# -gt 0 ]; do
  case "$1" in
    --parents) parents="$2"; shift 2 ;;
    --run-dir) run_dir="$2"; shift 2 ;;
    --allow-failed-run) allow_failed=1; shift ;;
    -h|--help) usage ;;
    -*) echo "unknown option: $1" >&2; usage ;;
    *) folder="$1"; shift ;;
  esac
done
[ -d "$folder" ] || { echo "not a folder: $folder" >&2; exit 2; }
folder="$(cd "$folder" && pwd -P)"
[ -z "$parents" ] || [ -f "$parents" ] || { echo "no such file: $parents" >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 is needed on this machine" >&2; exit 2; }
docker exec nextseek test -d /app/NessieAI/ns/reingest \
  || { echo "the running nextseek has no reingest code; rebuild it from the reingest branch" >&2; exit 2; }

work="$(mktemp -d)"; ctr="/tmp/folder_to_manifest.$$"
trap 'rm -rf "$work"; docker exec nextseek rm -rf "$ctr" >/dev/null 2>&1 || true' EXIT
docker exec nextseek mkdir -p "$ctr"

# Container side: phase "cmd" prints the exact stage command the cluster would run;
# phase "harvest" parses that command's output into a manifest and saves it.
cat > "$work/inside.py" <<'PY'
import csv, json, os, sys
sys.path.insert(0, "/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
import django
django.setup()

import chat_nextseek.luria.ssh as ssh
import NessieAI.ns.granular as g
from NessieAI.ns.reingest import harvest, manifest, mapper, maps
from NessieAI.ns.reingest.store import save_manifest

phase, folder = sys.argv[1], sys.argv[2]
runs_root = os.path.dirname(folder)


class _Captured(Exception):
    pass


if phase == "cmd":
    def _capture(env, cmd, *, key_path, timeout=None):
        raise _Captured(cmd)
    ssh.ssh_run_bytes = _capture
    try:
        g._stage_run_dir({}, folder, runs_root, "/tmp", "/dev/null")
    except _Captured as captured:
        print(captured.args[0])
    sys.exit(0)

tar_path, run_dir, parents_csv, allow_failed = sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6] == "1"
tar_bytes = open(tar_path, "rb").read()
ssh.ssh_run_bytes = lambda env, cmd, *, key_path, timeout=None: tar_bytes

import tempfile
with tempfile.TemporaryDirectory() as staged:
    skipped, inventory = g._stage_run_dir({}, folder, runs_root, staged, "/dev/null")
    m = harvest.harvest_local(staged, run_dir=run_dir, lookup_by_fastq=g._d_seq_by_fastq,
                              sample_type_lookup=g._sample_types_for_uids, inventory=inventory)
m.warnings.extend(f"staging skipped {s['path']}: {s['reason']}" for s in skipped or [])

# An empty placeholder's md5 is the md5 of nothing, never the real file's:
# keep the file listed, but give it no checksum rather than a wrong one.
placeholders = sorted(o.path for o in m.outputs if o.bytes == 0)
for path in placeholders:
    m.checksums.pop(path, None)
if placeholders:
    m.warnings.append(f"{len(placeholders)} empty placeholder file(s) listed without a checksum")

if parents_csv:
    with open(parents_csv, newline="", encoding="utf-8") as handle:
        given = {r["sample"].strip(): r["d_seq_uid"].strip()
                 for r in csv.DictReader(handle) if (r.get("sample") or "").strip()}
    types = g._sample_types_for_uids(sorted(set(given.values())))
    unknown = sorted(u for u in given.values() if u not in types)
    if unknown:
        sys.exit(f"--parents names UID(s) not in this database: {', '.join(unknown)}")
    names = {s.nfcore_sample for s in m.samples}
    missing = sorted(set(given) - names)
    if missing:
        sys.exit(f"--parents names sample(s) this run does not have: {', '.join(missing)}; "
                 f"it has: {', '.join(sorted(names))}")
    for s in m.samples:
        if s.nfcore_sample in given:
            s.d_seq_uid = given[s.nfcore_sample]
            s.d_seq_uid_multirun = []
            s.uid_resolution = manifest.RESOLUTION_LAUNCH_RECORD
            s.parent_sample_type = types[s.d_seq_uid]

if m.execution.failed and not allow_failed:
    sys.exit(f"{m.execution.failed} process(es) failed in this run; "
             f"pass --allow-failed-run to save it anyway")

manifest_id = save_manifest(m)

out = sys.stderr
print(f"pipeline   {m.pipeline.name or '(unknown)'} {m.pipeline.version}   status: {m.run_status}", file=out)
print(f"run_dir    {m.run_dir}", file=out)
for s in m.samples:
    print(f"sample     {s.nfcore_sample}: {s.uid_resolution}"
          f"{' -> ' + s.d_seq_uid if s.d_seq_uid else ''}", file=out)
print(f"files      {len(inventory)} listed, checksums for {len(m.checksums)}", file=out)
try:
    rows = mapper.apply(m, maps.load(m.pipeline.name)).rows
    counts = {}
    for r in rows:
        counts[r.sample_type] = counts.get(r.sample_type, 0) + 1
    print("rows       " + (", ".join(f"{n} {t}" for t, n in sorted(counts.items())) or "none"), file=out)
except maps.UnknownPipelineMap as exc:
    print(f"rows       none: {exc}", file=out)
for w in m.warnings:
    print(f"warning    {w}", file=out)
print(manifest_id)
PY
docker cp -q "$work/inside.py" "nextseek:$ctr/inside.py"
run_inside() { docker exec -w /app nextseek /app/.venv/bin/python "$ctr/inside.py" "$@"; }

# 1. The stage command, built by the server exactly as for the cluster.
stage_cmd="$(run_inside cmd "$folder")"
[ -n "$stage_cmd" ] || { echo "could not build the stage command" >&2; exit 1; }

# 2. Run it here: copies the small text files into a tar, lists the data files.
sh -c "$stage_cmd" > "$work/stage.tar"
docker cp -q "$work/stage.tar" "nextseek:$ctr/stage.tar"

# 3. Parse and save on the server.
if [ -n "$parents" ]; then docker cp -q "$parents" "nextseek:$ctr/parents.csv"; parents_in="$ctr/parents.csv"; else parents_in=""; fi
if [ -z "$run_dir" ]; then
  base="$(docker exec nextseek printenv LURIA_WORKING_PATH 2>/dev/null || true)"
  run_dir="${base:-/local-test}/runs/$(basename "$folder")"
fi
run_inside harvest "$folder" "$ctr/stage.tar" "$run_dir" "$parents_in" "$allow_failed"
