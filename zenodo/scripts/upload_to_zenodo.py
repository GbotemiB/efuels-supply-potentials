#!/usr/bin/env python3
"""
upload_to_zenodo.py
Upload prepared files to an existing Zenodo deposit via the REST API.

Resume strategy for large files (file splitting):
  Files larger than LARGE_FILE_THRESHOLD are split into PART_SIZE chunks and
  each part is uploaded as an independent file:
      cutout_northamerica.zip.part_001
      cutout_northamerica.zip.part_002  …
  A companion  cutout_northamerica.zip_REASSEMBLE.txt  is also uploaded with
  a self-contained Python reassembly script for downloaders:
      python cutout_northamerica.zip_REASSEMBLE.py
  Resume: before uploading each part the script checks whether Zenodo already
  holds a file of the correct size and skips it if so.

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


# ---------------------------------------------------------------------------
# File-splitting upload (used for large files)
# ---------------------------------------------------------------------------

def _make_reassemble_script(filename: str) -> bytes:
    """Return the source of a standalone Python reassembly script for downloaders."""
    return f'''\
#!/usr/bin/env python3
"""
Reassemble {filename} from its downloaded parts.

Steps:
  1. Download ALL files named  {filename}.part_*  and this script
     into the same folder.
  2. Open a terminal / command prompt in that folder.
  3. Run:   python {filename}_REASSEMBLE.py

No extra software needed — only Python (already installed on most computers).
Download Python from https://www.python.org/downloads/ if needed.
"""

import glob
import os
import sys

FILENAME = "{filename}"
PARTS_PATTERN = FILENAME + ".part_*"


def main():
    parts = sorted(glob.glob(PARTS_PATTERN))
    if not parts:
        sys.exit(
            f"No part files found matching \\'{{PARTS_PATTERN}}\\'.\\n"
            "Make sure you have downloaded all .part_* files into this folder."
        )

    total_size = sum(os.path.getsize(p) for p in parts)
    print(f"Found {{len(parts)}} parts  ({{total_size / 1024**3:.2f}} GB total)")
    print(f"Writing \\'{{FILENAME}}\\'...")

    written = 0
    with open(FILENAME, "wb") as out:
        for i, part in enumerate(parts, 1):
            size = os.path.getsize(part)
            print(f"  [{{i:3d}}/{{len(parts)}}] {{part}}  ({{size / 1024**2:.0f}} MB)",
                  end="", flush=True)
            with open(part, "rb") as fh:
                out.write(fh.read())
            written += size
            print(f"  {{written / total_size * 100:.0f}}% complete")

    print(f"\\nDone!  \\'{{FILENAME}}\\' written  ({{written / 1024**3:.2f}} GB).")
    print("You can now delete the .part_* files and this script.")


if __name__ == "__main__":
    main()
'''.encode()


def upload_in_parts(bucket_url: str, file_path: Path, token: str,
                    dry_run: bool) -> None:
    """
    Split a large file into PART_SIZE chunks and upload each as an independent
    Zenodo file.  A companion Python script *_REASSEMBLE.py is also uploaded —
    downloaders just run  python cutout_northamerica.zip_REASSEMBLE.py  in the
    folder where they saved the parts; no shell knowledge required.

    Resume: before uploading each part, the script checks whether Zenodo
    already holds a file of the correct size and skips it if so.
    """
    filename = file_path.name
    total = file_path.stat().st_size
    n_parts = math.ceil(total / PART_SIZE)

    print(f"  -> {filename}  ({total / 1024**3:.2f} GB)  "
          f"[splitting: {n_parts} parts x {PART_SIZE // 1024**2} MB]")
    if dry_run:
        print(f"     Parts will be named {filename}.part_001 … .part_{n_parts:03d}")
        print(f"     Reassembly script   → {filename}_REASSEMBLE.py")
        return

    # Upload the reassembly script first so it's present even if we abort mid-run
    script_name = f"{filename}_REASSEMBLE.py"
    if remote_file_size(bucket_url, script_name, token) == 0:
        print(f"  Uploading reassembly script → {script_name}")
        _put_bytes(f"{bucket_url}/{script_name}",
                   _make_reassemble_script(filename), token, script_name)

    with open(file_path, "rb") as fh:
        for part_num in range(1, n_parts + 1):
            part_name = f"{filename}.part_{part_num:03d}"
            fh.seek((part_num - 1) * PART_SIZE)
            chunk = fh.read(PART_SIZE)

            existing = remote_file_size(bucket_url, part_name, token)
            if existing == len(chunk):
                print(f"  [{part_num:3d}/{n_parts}] {part_name}  [already on Zenodo - skipped]")
                continue

            print(f"  [{part_num:3d}/{n_parts}] {part_name}  "
                  f"({len(chunk) / 1024**2:.0f} MB) ...", end="", flush=True)
            _put_bytes(f"{bucket_url}/{part_name}", chunk, token, part_name)
            print(" done")

    print(f"  All {n_parts} parts uploaded.")
    print(f"  Downloaders run: python {script_name}")


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

    for i, (file_path, label) in enumerate(files, 1):
        print(f"[{i:2d}/{len(files)}] {label}")
        if file_path.stat().st_size > LARGE_FILE_THRESHOLD:
            upload_in_parts(bucket_url, file_path, token, args.dry_run)
        else:
            upload_whole_file(bucket_url, file_path, token, args.dry_run)
        print()

    if args.dry_run:
        print("Dry run complete. Re-run without --dry-run to upload.")
    else:
        print("All files uploaded successfully.")
        print(f"Review your draft: https://zenodo.org/uploads/{args.deposit_id}")


if __name__ == "__main__":
    main()
