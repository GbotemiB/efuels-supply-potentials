#!/usr/bin/env bash
# upload_zenodo_slurm.sh
# Submit this with:
#   sbatch zenodo/scripts/upload_zenodo_slurm.sh
#
# What it does (in order):
#   1. Zip all 10 scenario folders  →  $STAGING_DIR/US_scenario_XX.zip
#   2. Upload all zips + README + LICENSE to Zenodo deposit 18487278
#
# The cutout (.nc file) is large and expected to be uploaded separately
# from a local machine using the same upload_to_zenodo.py script.
# To include it here set UPLOAD_CUTOUT=1 and point CUTOUT_PATH at the .nc file.
#
# REQUIREMENTS
#   - ZENODO_TOKEN must be set, either:
#       export ZENODO_TOKEN=xxx  before sbatch, OR
#       add it to ~/.bashrc / ~/.profile on zib
#   - The conda env (or module) with requests/tqdm/python-dotenv must be named
#     below in CONDA_ENV.  Adjust to match your setup.
#   - zib nodes that run this job must have outbound HTTPS access to zenodo.org.
#     Check with your admin if unsure; typically a "login" or "transfer" partition
#     is needed rather than compute nodes.

# ---------------------------------------------------------------------------
# SLURM directives  — adjust to your cluster's partition/QOS names
# ---------------------------------------------------------------------------
#SBATCH --job-name=zenodo-upload
#SBATCH --output=zenodo/logs/upload_%j.out
#SBATCH --error=zenodo/logs/upload_%j.err
#SBATCH --time=12:00:00          # 12 h should be more than enough
#SBATCH --cpus-per-task=4        # zip benefits from parallelism (-@ flag not used here, safe default)
#SBATCH --mem=8G
#SBATCH --partition=medium       # ← CHANGE to your internet-connected partition
# #SBATCH --qos=normal           # uncomment / adjust if your cluster uses QOS

# ---------------------------------------------------------------------------
# User-configurable paths  — edit these before submitting
# ---------------------------------------------------------------------------

# Root of the cloned repo on zib
REPO_ROOT="${HOME}/efuels-supply-potentials"

# Where scenario zips (and optional cutout zip) will be written
STAGING_DIR="${HOME}/staging/zenodo"

# Parent directory containing US_scenario_01 … US_scenario_10
RESULTS_DIR="${REPO_ROOT}/notebooks/results"

# Conda environment that has requests, tqdm, python-dotenv
CONDA_ENV="pypsa-earth"          # ← CHANGE to your env name

# Set to 1 to also upload the cutout from zib; set path below
UPLOAD_CUTOUT=0
CUTOUT_PATH="${HOME}/data/cutout-2013-era5.nc"   # only used if UPLOAD_CUTOUT=1

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
set -euo pipefail

mkdir -p "${STAGING_DIR}"
mkdir -p "${REPO_ROOT}/zenodo/logs"

echo "============================================================"
echo "Job ID:       ${SLURM_JOB_ID:-local}"
echo "Node:         $(hostname)"
echo "Start:        $(date)"
echo "Repo root:    ${REPO_ROOT}"
echo "Staging dir:  ${STAGING_DIR}"
echo "Results dir:  ${RESULTS_DIR}"
echo "============================================================"
echo ""

# Activate conda env
# shellcheck source=/dev/null
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "${CONDA_ENV}"

python --version
echo ""

# Verify token is present before doing anything slow
if [ -z "${ZENODO_TOKEN:-}" ]; then
    # Try loading from .env file as fallback
    ENV_FILE="${REPO_ROOT}/zenodo/.env"
    if [ -f "${ENV_FILE}" ]; then
        # shellcheck source=/dev/null
        set -o allexport
        source "${ENV_FILE}"
        set +o allexport
    fi
fi

if [ -z "${ZENODO_TOKEN:-}" ]; then
    echo "ERROR: ZENODO_TOKEN is not set."
    echo "  Set it before submitting:  export ZENODO_TOKEN=your_token && sbatch ..."
    echo "  Or create ${REPO_ROOT}/zenodo/.env with ZENODO_TOKEN=your_token"
    exit 1
fi

echo "ZENODO_TOKEN found (length: ${#ZENODO_TOKEN})"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Zip scenarios
# ---------------------------------------------------------------------------
echo "------------------------------------------------------------"
echo "Step 1: Zipping scenario folders"
echo "------------------------------------------------------------"

bash "${REPO_ROOT}/zenodo/scripts/zip_scenarios.sh" \
    "${RESULTS_DIR}" \
    "${STAGING_DIR}"

echo ""
echo "Scenario zips written to ${STAGING_DIR}:"
ls -lh "${STAGING_DIR}"/US_scenario_*.zip 2>/dev/null || echo "  (none found)"
echo ""

# ---------------------------------------------------------------------------
# Step 2: (Optional) Zip cutout
# ---------------------------------------------------------------------------
if [ "${UPLOAD_CUTOUT}" = "1" ]; then
    echo "------------------------------------------------------------"
    echo "Step 2: Zipping cutout"
    echo "------------------------------------------------------------"
    bash "${REPO_ROOT}/zenodo/scripts/zip_cutout.sh" \
        "${CUTOUT_PATH}" \
        "${STAGING_DIR}"
    echo ""
fi

# ---------------------------------------------------------------------------
# Step 3: Upload to Zenodo
# ---------------------------------------------------------------------------
echo "------------------------------------------------------------"
echo "Step 3: Uploading to Zenodo"
echo "------------------------------------------------------------"

python "${REPO_ROOT}/zenodo/scripts/upload_to_zenodo.py" \
    --staging-dir "${STAGING_DIR}"

echo ""
echo "============================================================"
echo "Done: $(date)"
echo "Review deposit: https://zenodo.org/uploads/18487278"
echo "============================================================"
