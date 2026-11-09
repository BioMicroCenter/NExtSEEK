#!/usr/bin/env bash
# Turn a Nessie-launched nf-core -profile test run into a committed reingest
# fixture. Run from the Mac; it calls fetch_run.sh, which the user runs because
# Claude never connects to the cluster. Set REINGEST_SSH_HOST to the cluster's
# ssh host alias first.
#
#   scripts/reingest/make_fixture.sh [--local] <pipeline> <version>[-<variant>] <run-dir> [<extra-prefix> ...]
#
# --local: <run-dir> is a -profile test,docker outdir on this machine; no ssh.
# Put the run's samplesheet in it as samplesheet.csv first, and pass the launch
# folder (the parent of work/) as an extra prefix so the sanitizer rewrites it.
#
# Builds NessieAI/tests/ns/fixtures/reingest_runs/<pipeline>/<version>/ with the
# staged text files, inventory.json and launch_record.json, every cluster path
# rewritten to /RUN, then refuses if any other absolute path remains. The
# fixture is built in a temporary folder and moved into the repo only after the
# sanitizer passes, so a failure leaves nothing under NessieAI/tests/.
# launch_record.json is a stub (run_dir, pipeline, revision, empty cohort): no
# lookup happens, so complete the cohort by hand from the PipelineRun.
set -euo pipefail
local_flag=()
if [ "${1:-}" = "--local" ]; then local_flag=(--local); shift; fi
[ $# -ge 3 ] || { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
pipeline="$1"; version="$2"; remote="$3"; shift 3
here="$(cd "$(dirname "$0")" && pwd)"
cd "$(git -C "$here" rev-parse --show-toplevel)"
dest="NessieAI/tests/ns/fixtures/reingest_runs/$pipeline/$version"
[ ! -e "$dest" ] || { echo "already exists: $dest" >&2; exit 2; }
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
stage="$work/fixture"
"$here/fetch_run.sh" "fixture_tmp" "$remote" "$remote/samplesheet.csv" --into "$work" ${local_flag[@]+"${local_flag[@]}"}
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
if [ ${#local_flag[@]} -gt 0 ]; then
  # Fixture bookkeeping, as import_evidence.py writes it: a local run is not a Nessie run.
  printf 'nf-core/%s %s, -profile test,docker, run on a workstation (make_fixture.sh --local), read %s.\nThe test profile'"'"'s own samplesheet and data; launch_record.json is a stub with an empty cohort.\n' \
    "$pipeline" "${version%%-*}" "$(date +%F)" > "$stage/EVIDENCE.txt"
fi
# Two trims, both recorded in EVIDENCE.txt. A MultiQC custom-content file's `#` lines are its
# display settings (and a tool's homepage URL, which the sanitizer refuses); the harvest skips
# them. An ataqv JSON lists every peak and fragment length; the harvest reads only its scalar
# metrics, so the series go and a fixture stays small.
python3 - "$stage" <<'PY'
import json, pathlib, sys
dest = pathlib.Path(sys.argv[1])
notes = []
for path in sorted(dest.rglob("*_mqc.tsv")):
    lines = path.read_text().splitlines(keepends=True)
    kept = [line for line in lines if not line.startswith("#")]
    if len(kept) < len(lines):
        path.write_text("".join(kept))
        notes.append(f"{path.relative_to(dest)}: MultiQC header lines (#) removed")
for path in sorted(dest.rglob("*.ataqv.json")):
    doc = json.loads(path.read_text())
    runs = doc if isinstance(doc, list) else [doc]
    for run in runs:
        metrics = run.get("metrics") or {}
        run["metrics"] = {k: v for k, v in metrics.items() if not isinstance(v, (list, dict))}
    path.write_text(json.dumps(doc, indent=1) + "\n")
    notes.append(f"{path.relative_to(dest)}: per-peak and per-length series removed, scalar metrics kept")
if notes and (dest / "EVIDENCE.txt").is_file():
    with open(dest / "EVIDENCE.txt", "a") as fh:
        fh.write("Trimmed on import (make_fixture.sh):\n" + "".join(f"- {n}\n" for n in notes))
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
