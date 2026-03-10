#!/usr/bin/env python3
"""
upload_to_zenodo.py
Upload prepared files to an existing Zenodo deposit via the REST API.

Usage:
    python upload_to_zenodo.py --staging-dir /path/to/staging [--deposit-id 18487278] [--dry-run]

Requirements:
    pip install requests tqdm python-dotenv

Environment:
    ZENODO_TOKEN  — your Zenodo personal access token
                    (set in shell or in zenodo/.env, which is gitignored)
"""

import argparse
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ZENODO_API = "https://zenodo.org/api"
DEFAULT_DEPOSIT_ID = 18487278

SCENARIO_NAMES = [f"US_scenario_{i:02d}" for i in range(1, 11)]

# Repo-root files to include (relative to the repo root, located two levels
# above this script: zenodo/scripts/upload_to_zenodo.py → ../../)
REPO_ROOT_FILES = ["README.md", "LICENSE"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_token() -> str:
    # Load from .env file in the zenodo/ directory if present
    dotenv_path = Path(__file__).parent.parent / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path)
    token = os.environ.get("ZENODO_TOKEN", "")
    if not token:
        sys.exit(
            "Error: ZENODO_TOKEN is not set.\n"
            "  Option 1: export ZENODO_TOKEN=your_token\n"
            "  Option 2: copy zenodo/.env.example → zenodo/.env and fill it in."
        )
    return token


def fetch_deposit(deposit_id: int, token: str) -> dict:
    url = f"{ZENODO_API}/deposit/depositions/{deposit_id}"
    r = requests.get(url, params={"access_token": token})
    if r.status_code != 200:
        sys.exit(f"Error fetching deposit {deposit_id}: {r.status_code} {r.text}")
    return r.json()


def stream_upload(bucket_url: str, file_path: Path, token: str, dry_run: bool) -> None:
    filename = file_path.name
    size_mb = file_path.stat().st_size / (1024 ** 2)
    print(f"  → {filename}  ({size_mb:.1f} MB)", end="", flush=True)

    if dry_run:
        print("  [DRY RUN — skipped]")
        return

    url = f"{bucket_url}/{filename}"

    if HAS_TQDM:
        with open(file_path, "rb") as fh:
            with tqdm(
                total=file_path.stat().st_size,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=f"    {filename}",
                leave=False,
            ) as bar:
                def _read_chunks(f, chunk=1024 * 1024):
                    while True:
                        data = f.read(chunk)
                        if not data:
                            break
                        bar.update(len(data))
                        yield data

                r = requests.put(
                    url,
                    data=_read_chunks(fh),
                    params={"access_token": token},
                    headers={"Content-Type": "application/octet-stream"},
                )
    else:
        with open(file_path, "rb") as fh:
            r = requests.put(
                url,
                data=fh,
                params={"access_token": token},
                headers={"Content-Type": "application/octet-stream"},
            )

    if r.status_code in (200, 201):
        print("  ✓")
    else:
        print(f"\n  ERROR {r.status_code}: {r.text}")
        sys.exit(f"Upload failed for {filename}")


def collect_files(staging_dir: Path, repo_root: Path) -> list[tuple[Path, str]]:
    """Return list of (absolute_path, display_label) for all files to upload."""
    files = []

    # Scenario zips
    for name in SCENARIO_NAMES:
        p = staging_dir / f"{name}.zip"
        files.append((p, f"scenario: {name}.zip"))

    # Cutout
    cutout = staging_dir / "cutout_northamerica.zip"
    files.append((cutout, "cutout: cutout_northamerica.zip"))

    # Repo-root files
    for fname in REPO_ROOT_FILES:
        p = repo_root / fname
        files.append((p, f"repo: {fname}"))

    return files


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Upload files to a Zenodo deposit.")
    parser.add_argument(
        "--staging-dir",
        required=True,
        type=Path,
        help="Local directory containing the prepared zip files.",
    )
    parser.add_argument(
        "--deposit-id",
        type=int,
        default=DEFAULT_DEPOSIT_ID,
        help=f"Zenodo deposit ID (default: {DEFAULT_DEPOSIT_ID})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files that would be uploaded without uploading anything.",
    )
    args = parser.parse_args()

    staging_dir: Path = args.staging_dir.expanduser().resolve()
    if not staging_dir.is_dir():
        sys.exit(f"Error: staging directory not found: {staging_dir}")

    # Repo root is 2 levels above this script (zenodo/scripts/ → zenodo/ → root)
    repo_root = Path(__file__).parent.parent.parent.resolve()

    token = get_token()

    # Resolve files
    files = collect_files(staging_dir, repo_root)

    # Check all source files exist before starting
    missing = [str(p) for p, _ in files if not p.exists()]
    if missing:
        print("Warning: the following files are missing and will be skipped:")
        for m in missing:
            print(f"  - {m}")
        print()
        files = [(p, label) for p, label in files if p.exists()]

    if not files:
        sys.exit("No files found to upload. Check your staging directory.")

    print(f"Deposit ID:   {args.deposit_id}")
    print(f"Staging dir:  {staging_dir}")
    print(f"Files to upload: {len(files)}")
    if args.dry_run:
        print("Mode: DRY RUN\n")
    print()

    # Fetch deposit and bucket URL
    if not args.dry_run:
        print("Fetching deposit info...", end="", flush=True)
        deposit = fetch_deposit(args.deposit_id, token)
        bucket_url = deposit["links"]["bucket"]
        print(f" OK  (bucket: {bucket_url})\n")
    else:
        bucket_url = "https://zenodo.org/api/files/DRY-RUN-BUCKET"

    # Upload
    for i, (file_path, label) in enumerate(files, 1):
        print(f"[{i:2d}/{len(files)}] {label}")
        stream_upload(bucket_url, file_path, token, args.dry_run)

    print()
    if args.dry_run:
        print("Dry run complete. Re-run without --dry-run to upload for real.")
    else:
        print(f"All files uploaded successfully.")
        print(f"Review your draft at: https://zenodo.org/uploads/{args.deposit_id}")


if __name__ == "__main__":
    main()
