#!/usr/bin/env bash
# Copy only what reingest needs from one nf-core results folder on the cluster
# into <into>/<name>. Run it yourself, from your Mac; set REINGEST_SSH_HOST to
# the cluster's ssh host alias first:
#
#   scripts/reingest/fetch_run.sh <name> <remote-outdir> <remote-samplesheet> [<remote-contrasts>] [--into <dir>]
#
# --into <dir>: the parent folder the run is copied into, as <dir>/<name>
# (default: .claude/reingest-local-test in this checkout).
#
# <remote-contrasts>: differentialabundance's contrasts file, for runs that keep
# it in the launch folder. It is copied in under its own name, at the top level,
# where the harvest looks for the file params.contrasts names.
#
# It mirrors what the real harvest does on the cluster:
#   - copied:    the small text files the harvest reads (GENERIC_GLOBS plus every
#                map's harvest_globs), each up to the harvest's 4 MB per-file cap
#   - name only: the files the harvest only lists (CORE_INVENTORY_GLOBS plus each
#                map's inventory_globs; older refs used INVENTORY_GLOBS: bam, h5ad,
#                rds, mtx, vcf, merged counts, quant.sf, MultiQC html) become
#                empty files with the same name; nothing is downloaded
#   - skipped:   everything else (plots, reports, DE tables, FastQC, ...)
# The patterns are read from the reingest code at $REINGEST_REF
# (default feat/nfcore-reingest-integration), so they follow the branch.
# The samplesheet is saved as <name>/samplesheet.csv. Safe to rerun.
set -euo pipefail
usage() { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
into=""; args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --into) [ $# -ge 2 ] || usage; into="$2"; shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
set -- ${args[@]+"${args[@]}"}
[ $# -eq 3 ] || [ $# -eq 4 ] || usage
: "${REINGEST_SSH_HOST:?set REINGEST_SSH_HOST to the cluster ssh host alias}"
name=$1 remote=$2 sheet=$3 contrasts=${4:-}
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(git -C "$here" rev-parse --show-toplevel)"
ref=${REINGEST_REF:-feat/nfcore-reingest-integration}
out="${into:-$repo/.claude/reingest-local-test}/$name"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT

# 1. Read the staging and inventory patterns out of the branch's own code.
git -C "$repo" show "$ref:NessieAI/ns/reingest/harvest.py" > "$work/harvest.py"
mkdir "$work/maps"
for f in $(git -C "$repo" ls-tree --name-only "$ref" NessieAI/ns/reingest_maps/ | grep '\.outputs\.json$'); do
  git -C "$repo" show "$ref:$f" > "$work/maps/$(basename "$f")"
done
python3 - "$work" <<'PY'
import ast, json, pathlib, re, sys
work = pathlib.Path(sys.argv[1])
consts = {}
for node in ast.parse((work / "harvest.py").read_text()).body:
    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
        continue
    target = node.targets[0] if isinstance(node, ast.Assign) else node.target
    if not isinstance(target, ast.Name):
        continue
    if target.id == "GENERIC_GLOBS":
        consts[target.id] = [consts[e.id] if isinstance(e, ast.Name) else ast.literal_eval(e)
                             for e in node.value.elts]
    else:
        try:
            consts[target.id] = ast.literal_eval(node.value)
        except Exception:
            pass
# Older refs name the constant INVENTORY_GLOBS; newer ones split it into
# CORE_INVENTORY_GLOBS plus each map's own inventory_globs.
inventory = list(consts.get("INVENTORY_GLOBS") or consts["CORE_INVENTORY_GLOBS"])
inventory += [p for m in sorted((work / "maps").glob("*.json"))
              for p in json.loads(m.read_text()).get("inventory_globs", [])]
extra = [p for m in sorted((work / "maps").glob("*.json"))
         for p in json.loads(m.read_text()).get("harvest_globs", [])]
inventory_names = {p.rsplit("/", 1)[-1] for p in inventory}   # same rule as harvest.staged_globs
staged = list(dict.fromkeys(consts["GENERIC_GLOBS"]
                            + [p for p in extra if p.rsplit("/", 1)[-1] not in inventory_names]))
# MAX_FILE_BYTES = int(os.environ.get("NEXTSEEK_HARVEST_MAX_FILE_BYTES", 4_000_000)): take the default
cap = re.search(r"^MAX_FILE_BYTES\s*=.*?,\s*([\d_]+)\s*\)\s*\)", (work / "harvest.py").read_text(), re.M)
json.dump({"staged": staged, "inventory": inventory, "max_file_bytes": int(cap.group(1).replace("_", ""))},
          open(work / "patterns.json", "w"))
PY

# 2. On the cluster: list which files match. Only names and sizes come back.
cat > "$work/match.py" <<'PY'
import json, pathlib, re, sys
root = pathlib.Path(sys.argv[1])
spec = json.loads(sys.stdin.read())
def expand(p):
    m = re.search(r"\{([^}]*)\}", p)
    if not m:
        return [p]
    return [q for alt in m.group(1).split(",") for q in expand(p[:m.start()] + alt + p[m.end():])]
def match(patterns):
    found = {}
    for p in patterns:
        for q in expand(p):
            for f in root.glob(q):
                if f.is_file():
                    found[f.relative_to(root).as_posix()] = f.stat().st_size
    return found
staged = match(spec["staged"])
inventory = {k: v for k, v in match(spec["inventory"]).items() if k not in staged}
for rel, size in sorted(staged.items()):
    print("STAGE\t%d\t%s" % (size, rel))
for rel, size in sorted(inventory.items()):
    print("NAME\t%d\t%s" % (size, rel))
# Nextflow's verdict from the newest execution report: only the one sentence
# comes back, as in the harvest's own stage script, never the report.
reports = sorted(root.glob("pipeline_info/execution_report*.html"))
if reports:
    verdict = ""
    with open(reports[-1], "rb") as fh:
        for line in fh:
            m = re.search(rb"Workflow execution completed (?:un)?successfully[^<]*", line)
            if m:
                verdict = " ".join(m.group(0).decode("utf-8", "replace").split())
                break
    print("REPORT\t%s\t%s" % (reports[-1].relative_to(root).as_posix(), verdict))
PY
echo "== $name"
{ cat "$work/match.py"; echo; echo "__SPEC__"; cat "$work/patterns.json"; } > "$work/remote_input"
# The remote dir goes through the remote shell: quote it, and hand it to python as
# its own argument (sys.argv[1]) rather than splicing it into the python source.
remote_q="$(printf '%q' "$remote")"
ssh "$REINGEST_SSH_HOST" "python3 -c \"import sys; src, spec = sys.stdin.read().split('__SPEC__', 1); sys.argv = ['m', sys.argv[1]]; sys.stdin = __import__('io').StringIO(spec); exec(src)\" $remote_q" \
  < "$work/remote_input" > "$work/matches.tsv"

max=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["max_file_bytes"])' "$work/patterns.json")
awk -F'\t' -v max="$max" '$1=="STAGE" && $2<=max {print $3}' "$work/matches.tsv" > "$work/stage.txt"
awk -F'\t' -v max="$max" '$1=="STAGE" && $2>max {print "   over the " max " B cap, skipped as the harvest would: " $3}' "$work/matches.tsv"
awk -F'\t' '$1=="NAME" {print $3}' "$work/matches.tsv" > "$work/names.txt"

# 3. Copy the staged files; make empty stand-ins for the name-only ones.
mkdir -p "$out"
if [ -s "$work/stage.txt" ]; then
  rsync -a -L --files-from="$work/stage.txt" "$REINGEST_SSH_HOST:$remote/" "$out/"
fi
while IFS= read -r f; do
  mkdir -p "$out/$(dirname "$f")"; [ -e "$out/$f" ] || : > "$out/$f"
done < "$work/names.txt"
# A one-line stand-in for the newest report, holding only Nextflow's verdict,
# so the stage script finds it locally just as it would on the cluster.
report=$(awk -F'\t' '$1=="REPORT" {print $2}' "$work/matches.tsv")
if [ -n "$report" ]; then
  mkdir -p "$out/$(dirname "$report")"
  awk -F'\t' '$1=="REPORT" {print "<div>" $3 "</div>"}' "$work/matches.tsv" > "$out/$report"
  echo "   newest report: $report -> $(awk -F'\t' '$1=="REPORT" {print ($3 == "" ? "(no verdict)" : $3)}' "$work/matches.tsv")"
fi
scp -q "$REINGEST_SSH_HOST:$sheet" "$out/samplesheet.csv"
if [ -n "$contrasts" ]; then
  scp -q "$REINGEST_SSH_HOST:$contrasts" "$out/$(basename "$contrasts")"
  echo "   contrasts file: $(basename "$contrasts")"
fi

echo "   copied $(wc -l < "$work/stage.txt" | tr -d ' ') files, $(wc -l < "$work/names.txt" | tr -d ' ') name-only stand-ins, plus samplesheet.csv"
echo "done: $out ($(du -sh "$out" | cut -f1))"
