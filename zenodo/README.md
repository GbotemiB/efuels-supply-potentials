# Zenodo Upload Guide

This directory contains everything needed to archive the **e-fuels supply potentials** project results on Zenodo.

**Existing deposit:** https://zenodo.org/uploads/18487278

---

## Prerequisites

Install the required Python packages (once):
```bash
pip install requests tqdm python-dotenv
```

Set your Zenodo API token:
```bash
# Option A — export in shell
export ZENODO_TOKEN=your_token_here

# Option B — create a local .env file (already gitignored)
cp zenodo/.env.example zenodo/.env
# then edit zenodo/.env and fill in your token
```
Get a token at: https://zenodo.org/account/settings/applications/ → "New token" with `deposit:write` scope.

---

## Step-by-Step Workflow

### Step 1 — Zip scenario results on zib

SSH into zib and run:
```bash
bash zenodo/scripts/zip_scenarios.sh notebooks/results scenario_zips
```

This creates `scenario_zips/US_scenario_01.zip` … `US_scenario_10.zip`.

**Edit `zip_scenarios.sh` if the path is different on zib.**

### Step 2 — Transfer zips to your laptop

```bash
# From your laptop
scp -r zib:/path/to/scenario_zips ~/staging/
```

Replace `/path/to/scenario_zips` with the actual output path from Step 1.

### Step 3 — Zip the ERA5 cutout (run locally)

Find your `.nc` cutout file (e.g., `cutout-2013-era5.nc`) and run:
```bash
bash zenodo/scripts/zip_cutout.sh ~/path/to/cutout-2013-era5.nc ~/staging
```

This creates `~/staging/cutout_northamerica.zip`.

### Step 4 — Dry run (verify file list)

```bash
python zenodo/scripts/upload_to_zenodo.py \
    --staging-dir ~/staging \
    --dry-run
```

Check that all 12 files (10 scenarios + cutout + README + LICENSE) are listed with no missing-file warnings.

### Step 5 — Upload to Zenodo

```bash
python zenodo/scripts/upload_to_zenodo.py \
    --staging-dir ~/staging
```

The script will:
1. Fetch the bucket URL from deposit `18487278`
2. Stream-upload each file (safe for large files like the 20 GB cutout)
3. Print progress per file

When done, visit https://zenodo.org/uploads/18487278 to review the draft and publish.

---

## File Reference

| File | Description |
|---|---|
| `scripts/zip_scenarios.sh` | Bash script to zip scenarios on zib |
| `scripts/zip_cutout.sh` | Bash script to zip the ERA5 cutout locally |
| `scripts/upload_to_zenodo.py` | Python upload script (Zenodo REST API) |
| `.zenodo.json` | Deposit metadata (title, creators, license) |
| `CITATION.cff` | Machine-readable citation record |
| `.env.example` | Token template — copy to `.env` and fill in |

---

## Notes

- **Never commit `zenodo/.env`** — it is listed in `.gitignore`.
- The deposit will remain a **draft** until you click "Publish" in the browser. You can upload files incrementally before publishing.
- If the total zipped size exceeds 50 GB, email `info@zenodo.org` to request a quota increase before publishing.
- To target a different deposit, pass `--deposit-id <id>` to the upload script.
