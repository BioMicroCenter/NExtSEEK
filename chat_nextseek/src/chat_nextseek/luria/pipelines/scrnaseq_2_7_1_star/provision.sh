#!/usr/bin/env bash
# Provision a vendored, minimally-patched nf-core/scrnaseq 2.7.1 on Luria that runs STARsolo for
# whitelist-less bead protocols (Drop-seq / Seq-Well). Idempotent: re-running resets to stock 2.7.1
# and re-applies. Two edits vs stock 2.7.1 (see README.md):
#   1) assets/protocols.json : star.dropseq gains extra_args = Seq-Well CB/UMI geometry (12bp/8bp).
#   2) modules/local/star_align.nf : --soloCBwhitelist becomes `None` when no whitelist is staged.
# Alevin is unaffected: it runs from the untouched remote `-r 2.7.1`, and both edits live only in
# the `star` protocol section / the STAR_ALIGN module.
set -euo pipefail

TAG=2.7.1
DEST="${1:-/net/bmc-pub10/data1/bmc/pipeline_cd/pipelines/scrnaseq-${TAG}-star-patched}"

if [ ! -d "$DEST/.git" ]; then
    echo "[provision] cloning nf-core/scrnaseq@${TAG} -> $DEST"
    mkdir -p "$(dirname "$DEST")"
    git clone --branch "$TAG" --depth 1 https://github.com/nf-core/scrnaseq "$DEST"
else
    echo "[provision] clone exists; resetting patched files to stock ${TAG}"
    # Luria's git is old (no `git -C`); cd into the clone instead.
    ( cd "$DEST" && git checkout -- assets/protocols.json modules/local/star_align.nf )
fi

python3 - "$DEST" <<'PY'
import json, sys, pathlib

dest = pathlib.Path(sys.argv[1])

# --- edit 1: protocols.json  star.dropseq geometry ---
pj = dest / "assets" / "protocols.json"
data = json.loads(pj.read_text())
assert data["star"]["dropseq"]["protocol"] == "CB_UMI_Simple", "2.7.1 drift: star.dropseq changed"
data["star"]["dropseq"]["extra_args"] = "--soloCBstart 1 --soloCBlen 12 --soloUMIstart 13 --soloUMIlen 8 --soloBarcodeReadLength 0"
pj.write_text(json.dumps(data, indent=4) + "\n")
print("[provision] patched", pj)

# --- edit 2: star_align.nf  whitelist-None branch ---
sa = dest / "modules" / "local" / "star_align.nf"
txt = sa.read_text()
if "whitelist_arg" not in txt:
    anchor = "    def (forward, reverse) = reads.collate(2).transpose()\n"
    defblock = (
        "\n    // Whitelist-less bead protocols (Drop-seq/Seq-Well) provide no barcode whitelist;\n"
        "    // STARsolo discovers barcodes when given `--soloCBwhitelist None`. With a whitelist\n"
        "    // staged (10x) keep the original gzip-decompressed process substitution.\n"
        '    def whitelist_arg = whitelist ? "--soloCBwhitelist <(gzip -cdf ${whitelist})" : "--soloCBwhitelist None"\n'
    )
    cmd_orig = "--soloCBwhitelist <(gzip -cdf $whitelist)"
    assert anchor in txt, "2.7.1 drift: anchor line not found"
    assert cmd_orig in txt, "2.7.1 drift: soloCBwhitelist line not found"
    txt = txt.replace(anchor, anchor + defblock, 1)
    txt = txt.replace(cmd_orig, "${whitelist_arg}")
    sa.write_text(txt)
    print("[provision] patched", sa)
else:
    print("[provision] star_align.nf already patched")
PY

echo "[provision] verifying..."
grep -q -- '--soloCBstart 1 --soloCBlen 12 --soloUMIstart 13 --soloUMIlen 8' "$DEST/assets/protocols.json" || { echo "FAIL geometry"; exit 1; }
grep -q -- 'soloCBwhitelist None' "$DEST/modules/local/star_align.nf" || { echo "FAIL whitelist-None"; exit 1; }
python3 -c "import json; json.load(open('$DEST/assets/protocols.json'))" || { echo "FAIL invalid JSON"; exit 1; }
echo "[provision] OK -> $DEST"
