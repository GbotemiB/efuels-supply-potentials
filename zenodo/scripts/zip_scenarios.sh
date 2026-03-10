#!/usr/bin/env bash
# zip_scenarios.sh
# Run this ON ZIB to zip each scenario folder into a standalone archive.
#
# Usage:
#   bash zip_scenarios.sh [BASE_DIR] [OUT_DIR]
#
# Defaults:
#   BASE_DIR = notebooks/results      (parent folder containing US_scenario_01 ... US_scenario_10)
#   OUT_DIR  = scenario_zips          (output folder created next to this script)
#
# Example (explicit paths):
#   bash zip_scenarios.sh /home/gino/notebooks/results /home/gino/staging/scenario_zips

set -euo pipefail

BASE_DIR="${1:-notebooks/results}"
OUT_DIR="${2:-scenario_zips}"

mkdir -p "$OUT_DIR"

echo "Zipping scenarios from: $BASE_DIR"
echo "Output directory:       $OUT_DIR"
echo ""

for i in $(seq -w 1 10); do
    SCENARIO="US_scenario_${i}"
    SRC="${BASE_DIR}/${SCENARIO}"
    DEST="${OUT_DIR}/${SCENARIO}.zip"

    if [ ! -d "$SRC" ]; then
        echo "[SKIP] $SRC not found, skipping."
        continue
    fi

    echo "[ZIP]  $SRC  →  $DEST"
    zip -r "$DEST" "$SRC" -x "*.DS_Store" -x "__pycache__/*" -x "*.pyc"
    echo "[DONE] $(du -sh "$DEST" | cut -f1)  $DEST"
    echo ""
done

echo "All scenarios zipped into: $OUT_DIR"
echo "Next step: scp the contents of $OUT_DIR to your laptop's staging directory."
