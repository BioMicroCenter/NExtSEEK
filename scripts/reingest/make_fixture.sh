#!/usr/bin/env bash
# Turn a Nessie-launched nf-core -profile test run into a committed reingest
# fixture. Run from the Mac; it calls fetch_run.sh, which the user runs because
# Claude never connects to the cluster. Set REINGEST_SSH_HOST to the cluster's
# ssh host alias first.
#
#   scripts/reingest/make_fixture.sh <pipeline> <version>[-<variant>] <remote-run-dir> [<extra-prefix> ...]
#
# Builds NessieAI/tests/ns/fixtures/reingest_runs/<pipeline>/<version>/ with the
# staged text files, inventory.json and launch_record.json, every cluster path
# rewritten to /RUN, then refuses if any other absolute path remains. The
# fixture is built in a temporary folder and moved into the repo only after the
# sanitizer passes, so a failure leaves nothing under NessieAI/tests/.
# launch_record.json is a stub (run_dir, pipeline, revision, empty cohort): no
# lookup happens, so complete the cohort by hand from the PipelineRun.
set -euo pipefail
[ $# -ge 3 ] || { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
pipeline="$1"; version="$2"; remote="$3"; shift 3
here="$(cd "$(dirname "$0")" && pwd)"
cd "$(git -C "$here" rev-parse --show-toplevel)"
dest="NessieAI/tests/ns/fixtures/reingest_runs/$pipeline/$version"
[ ! -e "$dest" ] || { echo "already exists: $dest" >&2; exit 2; }
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
stage="$work/fixture"
"$here/fetch_run.sh" "fixture_tmp" "$remote" "$remote/samplesheet.csv" --into "$work"
mkdir "$stage"
cp -R "$work/fixture_tmp/." "$stage/"
python3 - "$stage" <<'PY'
import json, pathlib, sys
dest = pathlib.Path(sys.argv[1])
listing = []
for path in sorted(dest.rglob("*")):
    if path.is_file() and path.stat().st_size == 0:
        listing.append({"path": str(path.relative_to(dest)), "bytes": 0})
        path.unlink()
(dest / "inventory.json").write_text(json.dumps(listing, indent=1) + "\n")
PY
if [ ! -f "$stage/launch_record.json" ]; then
  printf '{"run_dir": "%s", "pipeline": "nf-core/%s", "revision": "%s", "cohort": []}\n' \
    "$remote" "$pipeline" "${version%%-*}" > "$stage/launch_record.json"
fi
python3 "$here/sanitize_fixture.py" "$stage" "$remote" "$@"
mkdir -p "$(dirname "$dest")"
mv "$stage" "$dest"
echo "fixture written: $dest"
echo "next: run the fixture test, review snapshot.json, commit the folder"
