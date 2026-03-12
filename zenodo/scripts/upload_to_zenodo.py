#!/usr/bin/env python3
"""
upload_to_zenodo.py
Upload prepared files to an existing Zenodo deposit via the REST API.

Resume strategy for large files:
  Zenodo does not support server-side Content-Range reassembly — each PUT
  replaces the whole file. Files larger than LARGE_FILE_THRESHOLD are split
  into PART_SIZE pieces and uploaded as separate Zenodo files
  (e.g. cutout_northamerica.zip.part001). On re-run, parts that already
  exist on Zenodo with the correct byte size are skipped automatically.
  A reassembly command is printed at the end.

Usage:
    python upload_to_zenodo.py --staging-dir /path/to/staging [--deposit-id 18487278] [--dry-run]

Requirements:
    pip install requests tqdm python-dotenv

Environment:
    ZENODO_TOKEN  — your Zenodo personal access token
                    (set in shell or in zenodo/.env, which is gitignored)
"""

import argparse
import math
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

# Files larger than this threshold are split into parts before uploading
LARGE_FILE_THRESHOLD = 2 * 1024 ** 3    # 2 GB
PART_SIZE            = 500 * 1024 ** 2  # 500 MB per part

MAX_RETRIES   = 5
RETRY_BACKOFF = 10  # seconds for first retry (doubles each time)


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


def remote_file_size(bucket_url: str, filename: str, token: str) -> int:
    """
    Return the committed size of a file on Zenodo, or 0 if absent.
    Zenodo only returns Content-Length for fully committed files — a failed
    or partial upload leaves no trace, so 0 means 'not yet uploaded'.
    """
    r = requests.head(
        f"{bucket_url}/{filename}",
        params={"access_token": token},
        allow_redirects=True,
        timeout=30,
    )
    if r.status_code == 200:
        return int(r.headers.get("Content-Length", 0))
    return 0


def _put_bytes(url: str, data: bytes, token: str, label: str) -> None:
    """PUT a complete byte buffer, retrying with exponential backoff on errors."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.put(
                url,
                data=data,
                params={"access_token": token},
                headers={"Content-Type": "application/octet-stream"},
                timeout=600,
            )
            if r.status_code in (200, 201):
                return
            print(f"\n  Server error {r.status_code} on '{label}' (attempt {attempt}): {r.text[:300]}")
        except requests.exceptions.ConnectionError as exc:
            print(f"\n  Connection error on '{label}' (attempt {attempt}/{MAX_RETRIES}): {exc}")

        if attempt == MAX_RETRIES:
            sys.exit(f"Upload failed after {MAX_RETRIES} retries: {label}")

        wait = RETRY_BACKOFF * (2 ** (attempt - 1))
        print(f"  Retrying in {wait}s...")
        time.sleep(wait)


def upload_whole_file(bucket_url: str, file_path: Path, token: str, dry_run: bool) -> None:
    """Upload a single file as one atomic PUT. Skips if Zenodo already has the exact size."""
    filename = file_path.name
    total = file_path.stat().st_size

    existing = remote_file_size(bucket_url, filename, token)
    if existing == total:
        print(f"  -> {filename}  ({total / 1024**2:.1f} MB)  [already on Zenodo - skipped]")
        return

    print(f"  -> {filename}  ({total / 1024**2:.1f} MB)")
    if dry_run:
        return

    with open(file_path, "rb") as fh:
        data = fh.read()

    if HAS_TQDM:
        bar = tqdm(total=total, unit="B", unit_scale=True, unit_divisor=1024, leave=True)

    _put_bytes(f"{bucket_url}/{filename}", data, token, filename)

    if HAS_TQDM:
        bar.update(total)
        bar.close()

    print("  done")


def upload_in_parts(bucket_url: str, file_path: Path, token: str, dry_run: bool) -> list:
    """
    Split file into PART_SIZE slices, upload each as its own Zenodo file.
    Parts matching their expected size on Zenodo are skipped (true resume).
    Returns list of part filenames for reassembly.
    """
    total = file_path.stat().st_size
    n_parts = math.ceil(total / PART_SIZE)
    stem = file_path.name
    part_names = [f"{stem}.part{i + 1:03d}" for i in range(n_parts)]

    print(
        f"  -> {stem}  ({total / 1024**3:.2f} GB)  "
        f"splitting into {n_parts} parts x {PART_SIZE // 1024**2} MB"
    )

    if dry_run:
        for p in part_names:
            print(f"     [DRY RUN] {p}")
        return part_names

    with open(file_path, "rb") as fh:
        for i, part_name in enumerate(part_names):
            fh.seek(i * PART_SIZE)
            chunk = fh.read(PART_SIZE)
            expected = len(chunk)

            existing = remote_file_size(bucket_url, part_name, token)
            if existing == expected:
                print(f"  [{i + 1:3d}/{n_parts}] {part_name}  ({expected / 1024**2:.1f} MB)  [skipped]")
                continue

            print(f"  [{i + 1:3d}/{n_parts}] {part_name}  ({expected / 1024**2:.1f} MB) ...", end="", flush=True)

            if HAS_TQDM:
                bar = tqdm(total=expected, unit="B", unit_scale=True, unit_divisor=1024, leave=False)

            _put_bytes(f"{bucket_url}/{part_name}", chunk, token, part_name)

            if HAS_TQDM:
                bar.update(expected)
                bar.close()

            print("  done")

    return part_names


def collect_files(staging_dir: Path, repo_root: Path) -> list:
    """Return list of (absolute_path, display_label) for all files to upload."""
    files = []
    for name in SCENARIO_NAMES:
        files.append((staging_dir / f"{name}.zip", f"scenario: {name}.zip"))
    files.append((staging_dir / "cutout_northamerica.zip", "cutout: cutout_northamerica.zip"))
    for fname in REPO_ROOT_FILES:
        files.append((repo_root / fname, f"repo: {fname}"))
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

    reassemble_parts = {}

    for i, (file_path, label) in enumerate(files, 1):
        print(f"[{i:2d}/{len(files)}] {label}")
        if file_path.stat().st_size > LARGE_FILE_THRESHOLD:
            parts = upload_in_parts(bucket_url, file_path, token, args.dry_run)
            reassemble_parts[file_path.name] = parts
        else:
            upload_whole_file(bucket_url, file_path, token, args.dry_run)
        print()

    if args.dry_run:
        print("Dry run complete. Re-run without --dry-run to upload.")
    else:
        print("All files uploaded successfully.")
        print(f"Review your draft: https://zenodo.org/uploads/{args.deposit_id}")

    if reassemble_parts:
        print("\n--- Reassembly commands (run after downloading from Zenodo) ---")
        for original, parts in reassemble_parts.items():
            print(f"\n# {original}")
            print(f"cat {' '.join(parts)} > {original}")
            print(f"# verify: md5sum {original}")


if __name__ == "__main__":
    main()
