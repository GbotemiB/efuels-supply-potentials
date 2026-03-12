#!/usr/bin/env python3
"""
upload_to_zenodo.py
Upload prepared files to an existing Zenodo deposit via the REST API.

Resume strategy for large files (S3 multipart):
  Files larger than LARGE_FILE_THRESHOLD are uploaded using the S3 multipart
  protocol exposed by Zenodo's bucket API.
    1. POST  {bucket}/{file}?uploads             → get UploadId
    2. PUT   {bucket}/{file}?partNumber=N&uploadId=ID  → upload each part
    3. POST  {bucket}/{file}?uploadId=ID         → complete (Zenodo assembles)
  Downloaders see a single file — no reassembly needed.
  Progress is saved to .upload_state_{filename}.json in the staging dir.
  On re-run, already-uploaded parts are skipped automatically.

Usage:
    python upload_to_zenodo.py --staging-dir /path/to/staging [--deposit-id 18487278] [--dry-run]

Requirements:
    pip install requests tqdm python-dotenv

Environment:
    ZENODO_TOKEN  — your Zenodo personal access token
                    (set in shell or in zenodo/.env, which is gitignored)
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET

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
# S3 multipart upload (used for large files)
# ---------------------------------------------------------------------------

def _state_path(staging_dir: Path, filename: str) -> Path:
    return staging_dir / f".upload_state_{filename}.json"


def _load_state(staging_dir: Path, filename: str) -> dict:
    p = _state_path(staging_dir, filename)
    if p.exists():
        try:
            with open(p) as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError):
            pass
    return {}


def _save_state(staging_dir: Path, filename: str, state: dict) -> None:
    with open(_state_path(staging_dir, filename), "w") as f:
        json.dump(state, f, indent=2)


def _clear_state(staging_dir: Path, filename: str) -> None:
    p = _state_path(staging_dir, filename)
    if p.exists():
        p.unlink()


def _initiate_multipart(bucket_url: str, filename: str, token: str) -> str:
    """Start a new S3 multipart upload. Returns the UploadId string."""
    r = requests.post(
        f"{bucket_url}/{filename}?uploads",
        params={"access_token": token},
        timeout=30,
    )
    if r.status_code != 200:
        sys.exit(f"Failed to initiate multipart upload: {r.status_code} {r.text}")
    root = ET.fromstring(r.text)
    # Strip any XML namespace before searching
    for el in root.iter():
        el.tag = el.tag.split("}", 1)[-1] if "}" in el.tag else el.tag
    node = root.find(".//UploadId")
    if node is None:
        sys.exit(f"Could not parse UploadId from response: {r.text}")
    return node.text


def _upload_part(bucket_url: str, filename: str, part_number: int,
                 upload_id: str, data: bytes, token: str) -> str:
    """Upload one part of a multipart upload. Returns the ETag."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.put(
                f"{bucket_url}/{filename}",
                params={"partNumber": part_number, "uploadId": upload_id,
                        "access_token": token},
                data=data,
                headers={"Content-Type": "application/octet-stream"},
                timeout=600,
            )
            if r.status_code == 200:
                return r.headers["ETag"]
            print(f"\n  Server error {r.status_code} on part {part_number} "
                  f"(attempt {attempt}): {r.text[:300]}")
        except requests.exceptions.ConnectionError as exc:
            print(f"\n  Connection error on part {part_number} "
                  f"(attempt {attempt}/{MAX_RETRIES}): {exc}")

        if attempt == MAX_RETRIES:
            sys.exit(f"Part {part_number} failed after {MAX_RETRIES} retries.")

        wait = RETRY_BACKOFF * (2 ** (attempt - 1))
        print(f"  Retrying in {wait}s...")
        time.sleep(wait)


def _complete_multipart(bucket_url: str, filename: str,
                        upload_id: str, parts: list, token: str) -> None:
    """Tell Zenodo to assemble all parts into a single file."""
    xml_parts = "\n".join(
        f"  <Part>\n    <PartNumber>{p['PartNumber']}</PartNumber>"
        f"\n    <ETag>{p['ETag']}</ETag>\n  </Part>"
        for p in sorted(parts, key=lambda x: x["PartNumber"])
    )
    body = f"<CompleteMultipartUpload>\n{xml_parts}\n</CompleteMultipartUpload>"
    r = requests.post(
        f"{bucket_url}/{filename}",
        params={"uploadId": upload_id, "access_token": token},
        data=body.encode(),
        headers={"Content-Type": "application/xml"},
        timeout=120,
    )
    if r.status_code not in (200, 201):
        sys.exit(f"Failed to complete multipart upload: {r.status_code} {r.text}")


def _abort_multipart(bucket_url: str, filename: str,
                     upload_id: str, token: str) -> None:
    requests.delete(
        f"{bucket_url}/{filename}",
        params={"uploadId": upload_id, "access_token": token},
        timeout=30,
    )


def upload_multipart(bucket_url: str, file_path: Path, token: str,
                     dry_run: bool, staging_dir: Path) -> None:
    """
    Upload a large file using S3 multipart. Zenodo assembles the parts
    server-side so downloaders see a single file.
    State is saved to staging_dir/.upload_state_{filename}.json after each
    part so the upload resumes from where it left off on re-run.
    """
    filename = file_path.name
    total = file_path.stat().st_size
    n_parts = math.ceil(total / PART_SIZE)

    # If already fully committed, skip
    existing = remote_file_size(bucket_url, filename, token)
    if existing == total:
        print(f"  -> {filename}  ({total / 1024**3:.2f} GB)  [already on Zenodo - skipped]")
        _clear_state(staging_dir, filename)
        return

    print(f"  -> {filename}  ({total / 1024**3:.2f} GB)  "
          f"[S3 multipart: {n_parts} parts x {PART_SIZE // 1024**2} MB]")
    if dry_run:
        return

    # Load or create multipart state
    state = _load_state(staging_dir, filename)
    upload_id = state.get("upload_id", "")
    completed_parts = state.get("completed_parts", [])
    done_part_numbers = {p["PartNumber"] for p in completed_parts}

    if upload_id:
        print(f"  Resuming upload {upload_id} "
              f"({len(completed_parts)}/{n_parts} parts already done)")
    else:
        upload_id = _initiate_multipart(bucket_url, filename, token)
        completed_parts = []
        done_part_numbers = set()
        state = {"upload_id": upload_id, "filename": filename,
                 "total_size": total, "n_parts": n_parts, "completed_parts": []}
        _save_state(staging_dir, filename, state)
        print(f"  Started multipart upload: {upload_id}")

    with open(file_path, "rb") as fh:
        for part_num in range(1, n_parts + 1):
            if part_num in done_part_numbers:
                print(f"  [{part_num:3d}/{n_parts}] already uploaded - skipped")
                continue

            fh.seek((part_num - 1) * PART_SIZE)
            chunk = fh.read(PART_SIZE)

            print(f"  [{part_num:3d}/{n_parts}] {len(chunk) / 1024**2:.0f} MB ...",
                  end="", flush=True)

            if HAS_TQDM:
                bar = tqdm(total=len(chunk), unit="B", unit_scale=True,
                           unit_divisor=1024, leave=False)

            etag = _upload_part(bucket_url, filename, part_num,
                                upload_id, chunk, token)

            if HAS_TQDM:
                bar.update(len(chunk))
                bar.close()

            print(" done")

            # Persist progress immediately so a crash loses at most one part
            completed_parts.append({"PartNumber": part_num, "ETag": etag})
            state["completed_parts"] = completed_parts
            _save_state(staging_dir, filename, state)

    print("  Completing multipart upload (Zenodo assembling)...", end="", flush=True)
    _complete_multipart(bucket_url, filename, upload_id, completed_parts, token)
    print(" done")
    _clear_state(staging_dir, filename)
    print(f"  {filename}  uploaded as a single file")


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
            upload_multipart(bucket_url, file_path, token, args.dry_run, staging_dir)
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
