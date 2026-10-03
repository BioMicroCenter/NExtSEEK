#!/usr/bin/env bash
# laya CPU fine-tune (JevLevROUTING SPEC s8). WRITTEN, NOT RUN: it needs torch and the weights, which the
# build phase does not install. Everything stays on this laptop: no Kaggle, Jev, OpenRouter or hosted notebook.
#
#   scripts/laya/train.sh <training-view.jsonl> <checkpoint-dir>
#
# The workspace is outside every git repo. The one network step is the one-time base-weights download; run it
# with LAYA_ALLOW_DOWNLOAD=1 once, then every run is offline.
set -euo pipefail

VIEW=${1:?training view jsonl (from build_dataset.py)}
OUT=${2:?checkpoint dir (written once, never overwritten)}
WORK=${LAYA_WORKSPACE:-$HOME/code/dmac/jevlev-train}
LAYA_VERSION=0.3.25

mkdir -p "$WORK"
if git -C "$WORK" rev-parse --show-toplevel >/dev/null 2>&1; then
  echo "workspace $WORK is inside a git repo: it must be outside every git repo" >&2; exit 2
fi
[ -e "$OUT" ] && { echo "$OUT exists: a checkpoint folder is written once" >&2; exit 2; }

[ -d "$WORK/venv" ] || python3 -m venv "$WORK/venv"
# shellcheck disable=SC1091
. "$WORK/venv/bin/activate"
python -c "import importlib.metadata as m,sys; sys.exit(0 if m.version('laya')=='$LAYA_VERSION' else 1)" >/dev/null 2>&1 \
  || pip install "laya==$LAYA_VERSION" "torch==2.14.1" --extra-index-url https://download.pytorch.org/whl/cpu  # the sidecar lock's torch

if [ "${LAYA_ALLOW_DOWNLOAD:-0}" != "1" ]; then
  export HF_HUB_OFFLINE=1
fi
export HF_HUB_DISABLE_TELEMETRY=1 WANDB_MODE=disabled HF_HOME="$WORK/hf" OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"

# the laya fine-tune entry point is verified at build time (SPEC s11); set it here, e.g.
#   LAYA_TRAIN_CMD="python -m laya.finetune"
: "${LAYA_TRAIN_CMD:?set LAYA_TRAIN_CMD to the laya fine-tune command for $LAYA_VERSION (verified at build)}"
mkdir -p "$OUT"
$LAYA_TRAIN_CMD --data "$VIEW" --output "$OUT" --device cpu
chmod -R a-w "$OUT"
# revision name = <yyyymmdd>-<first 12 hex of the weights file sha256>; fit_calibration.py computes it from --weights
