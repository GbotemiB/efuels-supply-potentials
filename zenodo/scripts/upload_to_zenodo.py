#!/usr/bin/env python3
"""
upload_to_zenodo.py
Upload prepared files to an existing Zenodo deposit via the REST API.
Supports resumable uploads: if a connection drops mid-upload, re-running
the script will automatically continue from the last successful byte.

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
import time
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

CHUNK_SIZE = 8 * 1024 * 1024   # 8 MB per chunk
MAX_RETRIES = 5                 # retries per chunk on connection error
RETRY_BACKOFF = 5               # seconds between retries (doubles each attempt)


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


def get_uploaded_size(bucket_url: str, filename: str, token: str) -> int:
    """Return how many bytes Zenodo already has for this file (0 if none)."""
    r = requests.head(
        f"{bucket_url}/{filename}",
        params={"access_token": token},
        allow_redirects=True,
    )
    if r.status_code == 200:
        return int(r.headers.get("Content-Length", 0))
    return 0


def stream_upload(bucket_url: str, file_path: Path, token: str, dry_run: bool) -> None:
    filename = file_path.name
    total_size = file_path.stat().st_size
    size_mb = total_size / (1024 ** 2)
    url = f"{bucket_url}/{filename}"

    if dry_run:
        print(f"  → {filename}  ({size_mb:.1f} MB)  [DRY RUN — skipped]")
        return

    # Check how much has already been uploaded (resume support)
    offset = get_uploaded_size(bucket_url, filename, token)
    if offset >= total_size:
        print(f"  → {filename}  ({size_mb:.1f} MB)  [already complete — skipped]")
        return

    if offset > 0:
        print(f"  → {filename}  ({size_mb:.1f} MB)  [resuming from {offset / (1024**2):.1f} MB]")
    else:
        print(f"  → {filename}  ({size_mb:.1f} MB)")

    bar = None
    if HAS_TQDM:
        bar = tqdm(
            total=total_size,
            initial=offset,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            leave=True,
        )

    with open(file_path, "rb") as fh:
        fh.seek(offset)
        current_offset = offset

        while current_offset < total_size:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                break

            chunk_end = current_offset + len(chunk) - 1
            headers = {
                "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes {current_offset}-{chunk_end}/{total_size}",
                "Content-Length": str(len(chunk)),
            }

            # Retry loop for each chunk
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    r = requests.put(
                        url,
                        data=chunk,
                        params={"access_token": token},
                        headers=headers,
                        timeout=300,
                    )
                    if r.status_code in (200, 201, 206):
                        break
                    else:
                        print(f"\n  Chunk error {r.status_code} (attempt {attempt}/{MAX_RETRIES}): {r.text[:200]}")
                except requests.exceptions.ConnectionError as e:
                    print(f"\n  Connection error (attempt {attempt}/{MAX_RETRIES}): {e}")

                if attempt == MAX_RETRIES:
                    if bar:
                        bar.close()
                    sys.exit(f"Upload failed after {MAX_RETRIES} retries at byte {current_offset}.")

                wait = RETRY_BACKOFF * (2 ** (attempt - 1))
                print(f"  Retrying in {wait}s...")
                time.sleep(wait)

            if bar:
                bar.update(len(chunk))
            current_offset += len(chunk)

    if bar:
        bar.close()

    print(f"  ✓  {filename}")


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
