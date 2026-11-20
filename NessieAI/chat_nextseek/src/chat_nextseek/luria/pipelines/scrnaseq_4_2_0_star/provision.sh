#!/usr/bin/env bash
# Provision a vendored, minimally-patched nf-core/scrnaseq 4.2.0 on Luria that runs STARsolo for
# whitelist-less bead protocols (Drop-seq / Seq-Well). Idempotent: re-running resets the two
# patched files to stock 4.2.0 and re-applies. Two edits vs stock 4.2.0 (see README.md):
#   1) assets/protocols.json : star.dropseq gains extra_args = Seq-Well CB/UMI geometry (12bp/8bp).
#   2) modules/local/star_align/main.nf : with no whitelist staged, skip the whitelist copy and
#      pass `--soloCBwhitelist None`.
# Every other aligner is unaffected: simpleaf, kallisto and cellranger run from the untouched
# remote `-r 4.2.0`, and both edits live only in the `star` protocol section / the STAR_ALIGN module.
set -euo pipefail

TAG=4.2.0
DEST="${1:-/net/bmc-pub10/data1/bmc/pipeline_cd/pipelines/scrnaseq-${TAG}-star-patched}"

if [ ! -d "$DEST/.git" ]; then
    echo "[provision] cloning nf-core/scrnaseq@${TAG} -> $DEST"
    mkdir -p "$(dirname "$DEST")"
    git clone --branch "$TAG" --depth 1 https://github.com/nf-core/scrnaseq "$DEST"
else
    echo "[provision] clone exists; resetting patched files to stock ${TAG}"
    # Luria's git is old (no `git -C`); cd into the clone instead.
    ( cd "$DEST" && git checkout -- assets/protocols.json modules/local/star_align/main.nf )
fi

# The clone must sit on the tag itself: the patches are re-derived against exactly 4.2.0.
head_sha="$(cd "$DEST" && git rev-parse HEAD)"
tag_sha="$(cd "$DEST" && git rev-parse "${TAG}^{}")"
[ "$head_sha" = "$tag_sha" ] || { echo "FAIL clone HEAD $head_sha is not ${TAG} ($tag_sha)"; exit 1; }

python3 - "$DEST" <<'PY'
import json, sys, pathlib

dest = pathlib.Path(sys.argv[1])

# --- edit 1: protocols.json  star.dropseq geometry ---
pj = dest / "assets" / "protocols.json"
data = json.loads(pj.read_text())
assert data["star"]["dropseq"] == {"protocol": "CB_UMI_Simple"}, "4.2.0 drift: star.dropseq changed"
data["star"]["dropseq"]["extra_args"] = "--soloCBstart 1 --soloCBlen 12 --soloUMIstart 13 --soloUMIlen 8 --soloBarcodeReadLength 0"
pj.write_text(json.dumps(data, indent=4) + "\n")
print("[provision] patched", pj)

# --- edit 2: star_align/main.nf  whitelist-None branch ---
sa = dest / "modules" / "local" / "star_align" / "main.nf"
txt = sa.read_text()
if "whitelist_arg" not in txt:
    anchor = "    def (forward, reverse) = reads.collate(2).transpose()\n"
    defblock = (
        "\n    // Whitelist-less bead protocols (Drop-seq/Seq-Well) stage no barcode whitelist;\n"
        "    // STARsolo discovers barcodes when given `--soloCBwhitelist None`. With a whitelist\n"
        "    // staged (10x) keep stock behaviour: decompress it and pass the file.\n"
        "    def whitelist_staged = whitelist ? 'true' : 'false'\n"
        '    def whitelist_arg = whitelist ? "--soloCBwhitelist whitelist.uncompressed.txt" : "--soloCBwhitelist None"\n'
    )
    # Stock always copies the whitelist; with none staged `cp` gets one operand and the task dies.
    copy_orig = (
        "    if [[ $whitelist == *.gz ]]; then\n"
        "        gzip -cdf $whitelist > whitelist.uncompressed.txt\n"
        "    else\n"
        "        cp $whitelist whitelist.uncompressed.txt\n"
        "    fi\n"
    )
    copy_new = (
        "    if ${whitelist_staged}; then\n"
        "        if [[ $whitelist == *.gz ]]; then\n"
        "            gzip -cdf $whitelist > whitelist.uncompressed.txt\n"
        "        else\n"
        "            cp $whitelist whitelist.uncompressed.txt\n"
        "        fi\n"
        "    fi\n"
    )
    cmd_orig = "        --soloCBwhitelist whitelist.uncompressed.txt "
    assert txt.count(anchor) == 1, "4.2.0 drift: anchor line not found"
    assert txt.count(copy_orig) == 1, "4.2.0 drift: whitelist copy block not found"
    assert txt.count(cmd_orig) == 1, "4.2.0 drift: soloCBwhitelist line not found"
    txt = txt.replace(anchor, anchor + defblock, 1)
    txt = txt.replace(copy_orig, copy_new, 1)
    txt = txt.replace(cmd_orig, "        ${whitelist_arg} ", 1)
    sa.write_text(txt)
    print("[provision] patched", sa)
else:
    print("[provision] star_align/main.nf already patched")
PY

echo "[provision] verifying..."
grep -q -- '--soloCBstart 1 --soloCBlen 12 --soloUMIstart 13 --soloUMIlen 8' "$DEST/assets/protocols.json" || { echo "FAIL geometry"; exit 1; }
grep -q -- 'soloCBwhitelist None' "$DEST/modules/local/star_align/main.nf" || { echo "FAIL whitelist-None"; exit 1; }
grep -qF -- 'if ${whitelist_staged}; then' "$DEST/modules/local/star_align/main.nf" || { echo "FAIL whitelist copy guard"; exit 1; }
if grep -qF -- '--soloCBwhitelist whitelist.uncompressed.txt \' "$DEST/modules/local/star_align/main.nf"; then
    echo "FAIL unconditional soloCBwhitelist still in the STAR command"; exit 1
fi
python3 -c "import json; json.load(open('$DEST/assets/protocols.json'))" || { echo "FAIL invalid JSON"; exit 1; }
echo "[provision] OK -> $DEST"
