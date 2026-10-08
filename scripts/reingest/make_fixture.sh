#!/usr/bin/env bash
# Turn a Nessie-launched nf-core -profile test run into a committed reingest
# fixture. Run from the Mac; it calls fetch_run.sh, which the user runs because
# Claude never connects to the cluster. Set REINGEST_SSH_HOST to the cluster's
# ssh host alias first.
#
#   scripts/reingest/make_fixture.sh <pipeline> <version>[-<variant>] <remote-run-dir> [<extra-prefix> ...]
#
# Writes NessieAI/tests/ns/fixtures/reingest_runs/<pipeline>/<version>/ with the
# staged text files, inventory.json and launch_record.json, every cluster path
# rewritten to /RUN, then refuses if any other absolute path remains.
# launch_record.json is filled from the PipelineRun the local stack holds for
# <remote-run-dir>, or left as a stub to complete by hand.
set -euo pipefail
[ $# -ge 3 ] || { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
pipeline="$1"; version="$2"; remote="$3"; shift 3
here="$(cd "$(dirname "$0")" && pwd)"
cd "$(git -C "$here" rev-parse --show-toplevel)"
dest="NessieAI/tests/ns/fixtures/reingest_runs/$pipeline/$version"
[ ! -e "$dest" ] || { echo "already exists: $dest" >&2; exit 2; }
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
"$here/fetch_run.sh" "fixture_tmp" "$remote" "$remote/samplesheet.csv" --into "$work"
mkdir -p "$dest"
cp -R "$work/fixture_tmp/." "$dest/"
python3 - "$dest" <<'PY'
import json, os, pathlib, sys
dest = pathlib.Path(sys.argv[1])
listing = []
for path in sorted(dest.rglob("*")):
    if path.is_file() and path.stat().st_size == 0:
        listing.append({"path": str(path.relative_to(dest)), "bytes": 0})
        path.unlink()
(dest / "inventory.json").write_text(json.dumps(listing, indent=1) + "\n")
PY
if [ ! -f "$dest/launch_record.json" ]; then
  printf '{"run_dir": "%s", "pipeline": "nf-core/%s", "revision": "%s", "cohort": []}\n' \
    "$remote" "$pipeline" "${version%%-*}" > "$dest/launch_record.json"
fi
python3 "$here/sanitize_fixture.py" "$dest" "$remote" "$@"
echo "fixture written: $dest"
echo "next: run the fixture test, review snapshot.json, commit the folder"
