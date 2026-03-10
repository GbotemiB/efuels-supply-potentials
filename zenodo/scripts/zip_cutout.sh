#!/usr/bin/env bash
# zip_cutout.sh
# Run this LOCALLY to zip the ERA5 cutout .nc file before uploading to Zenodo.
#
# Usage:
#   bash zip_cutout.sh <path/to/cutout.nc> [output_dir]
#
# Example:
#   bash zip_cutout.sh ~/data/cutout-2013-era5.nc ~/staging
#
# The output will always be named: cutout_northamerica.zip

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <path/to/cutout.nc> [output_dir]"
    echo "Example: $0 ~/data/cutout-2013-era5.nc ~/staging"
    exit 1
fi

CUTOUT_FILE="$1"
OUT_DIR="${2:-.}"

if [ ! -f "$CUTOUT_FILE" ]; then
    echo "Error: cutout file not found at: $CUTOUT_FILE"
    exit 1
fi

mkdir -p "$OUT_DIR"
DEST="${OUT_DIR}/cutout_northamerica.zip"

echo "Zipping: $CUTOUT_FILE"
echo "Output:  $DEST"
echo ""

zip -j "$DEST" "$CUTOUT_FILE" -x "*.DS_Store"

echo ""
echo "[DONE] $(du -sh "$DEST" | cut -f1)  $DEST"
echo "Next step: make sure this file is in your staging directory before running upload_to_zenodo.py"
