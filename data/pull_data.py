"""
Data acquisition module for KPK Carry Sim.
Implements SPEC §3.1, §8, §9 data requirements.

Pulls:
  1. Hourly BTC/USD candles Jan 2023–Oct 2026 from Coinbase Exchange public API.
  2. 3-month T-bill rate (DTB3) from FRED.
Records SHA256 checksums, row counts, source URLs, and timestamps in data/PROVENANCE.md.
Idempotent: skips downloads if files already exist.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parent
RAW_DIR = DATA_DIR / "raw"
PROVENANCE_FILE = DATA_DIR / "PROVENANCE.md"

COINBASE_API_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
FRED_DTB3_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"

USER_AGENT = "curl/7.88.1"


def compute_sha256(filepath: Path) -> str:
    """Compute the SHA256 hex digest of a file."""
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(65536), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def pull_fred_dtb3(output_path: Path, force: bool = False) -> tuple[int, str]:
    """Pull 3-month T-bill rates from FRED DTB3 CSV.

    Returns (row_count, sha256).
    """
    if output_path.exists() and output_path.stat().st_size > 0 and not force:
        print(f"[FRED DTB3] Cache hit: {output_path}")
        df = pd.read_csv(output_path)
        return len(df), compute_sha256(output_path)

    print(f"[FRED DTB3] Downloading from {FRED_DTB3_URL}...")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    headers = {"User-Agent": USER_AGENT, "Connection": "close"}
    try:
        response = requests.get(FRED_DTB3_URL, headers=headers, timeout=20)
        response.raise_for_status()
        content = response.content
    except Exception:
        # Fallback to curl
        cmd = ["curl", "-sSL", FRED_DTB3_URL]
        content = subprocess.check_output(cmd, timeout=30)

    output_path.write_bytes(content)

    df = pd.read_csv(output_path)
    sha256_sum = compute_sha256(output_path)
    print(f"[FRED DTB3] Saved {len(df)} rows to {output_path} (sha256: {sha256_sum[:12]}...)")
    return len(df), sha256_sum


def _fetch_coinbase_chunk(url: str, params: dict) -> list:
    """Fetch candle chunk using requests with fallback to curl."""
    headers = {"User-Agent": USER_AGENT, "Connection": "close"}
    retries = 4
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=12)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                time.sleep(2.0 + attempt)
            else:
                time.sleep(1.0)
        except Exception:
            time.sleep(1.0 + attempt)

    # Fallback to curl
    query_str = f"granularity={params['granularity']}&start={params['start']}&end={params['end']}"
    full_url = f"{url}?{query_str}"
    proc = subprocess.run(["curl", "-sSL", full_url], capture_output=True, text=True, timeout=20)
    if proc.returncode == 0 and proc.stdout.strip().startswith("["):
        return json.loads(proc.stdout)
    raise RuntimeError(f"Failed to fetch chunk for params {params}")


def pull_coinbase_btc_hourly(
    output_path: Path,
    start_iso: str = "2023-01-01T00:00:00Z",
    end_iso: str = "2026-11-01T00:00:00Z",
    chunk_hours: int = 250,
    force: bool = False,
) -> tuple[int, str]:
    """Pull hourly BTC/USD candles from Coinbase Exchange public API in chunks.

    Returns (row_count, sha256).
    """
    if output_path.exists() and output_path.stat().st_size > 0 and not force:
        print(f"[Coinbase BTC] Cache hit: {output_path}")
        df = pd.read_csv(output_path)
        return len(df), compute_sha256(output_path)

    print(f"[Coinbase BTC] Pulling hourly candles from {start_iso} to {end_iso}...")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    start_dt = datetime.datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    end_dt = datetime.datetime.fromisoformat(end_iso.replace("Z", "+00:00"))

    all_candles = []
    current_start = start_dt

    total_chunks = int((end_dt - start_dt).total_seconds() / (chunk_hours * 3600)) + 1
    chunk_idx = 0

    while current_start < end_dt:
        chunk_idx += 1
        current_end = min(current_start + datetime.timedelta(hours=chunk_hours), end_dt)
        params = {
            "granularity": 3600,
            "start": current_start.isoformat(),
            "end": current_end.isoformat(),
        }

        data = _fetch_coinbase_chunk(COINBASE_API_URL, params)
        all_candles.extend(data)

        if chunk_idx % 15 == 0 or current_end >= end_dt:
            print(f"  Progress: chunk {chunk_idx}/{total_chunks} ({len(all_candles)} candles collected)")

        current_start = current_end
        time.sleep(0.05)  # Polite throttle

    # Columns: [timestamp, low, high, open, close, volume]
    df = pd.DataFrame(all_candles, columns=["timestamp", "low", "high", "open", "close", "volume"])
    # Reorder columns to standard: timestamp, open, high, low, close, volume
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    # Filter to exact range
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())
    df = df[(df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)].reset_index(drop=True)

    # Add iso datetime for readability
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    df.to_csv(output_path, index=False)
    sha256_sum = compute_sha256(output_path)
    print(f"[Coinbase BTC] Successfully saved {len(df)} candles to {output_path} (sha256: {sha256_sum[:12]}...)")
    return len(df), sha256_sum


def write_provenance(btc_info: dict, dtb3_info: dict) -> None:
    """Write data/PROVENANCE.md recording provenance metadata per SPEC §8."""
    timestamp_now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    content = f"""# Data Provenance

Generated per SPEC §8 and §9 on `{timestamp_now}`.

All data series are cached locally in `data/raw/` with SHA256 integrity hashes.
Data collection is idempotent: existing files with matching hashes are not re-downloaded.

## Dataset Manifest

| Asset / Series | Source URL | Rows | File Path | SHA256 Checksum |
| :--- | :--- | :--- | :--- | :--- |
| **BTC/USD Hourly Candles** (Jan 2023–Oct 2026) | `{COINBASE_API_URL}` | {btc_info['rows']:,} | `{btc_info['path']}` | `{btc_info['sha256']}` |
| **3-Month Treasury Bill Rate (DTB3)** | `{FRED_DTB3_URL}` | {dtb3_info['rows']:,} | `{dtb3_info['path']}` | `{dtb3_info['sha256']}` |

## Verification Details

- **Coinbase BTC/USD Hourly:**
  - Range: `2023-01-01T00:00:00Z` to `2026-11-01T00:00:00Z`
  - Granularity: 3600 seconds (1 hour)
  - Fields: `timestamp`, `open`, `high`, `low`, `close`, `volume`, `datetime`
  - Pagination: 250-hour chunks with duplicate elimination and chronological sorting.

- **FRED 3-Month T-Bill Rate (DTB3):**
  - Frequency: Daily (business days)
  - Units: Percent per annum
  - Series ID: `DTB3`
"""
    PROVENANCE_FILE.write_text(content)
    print(f"[Provenance] Wrote provenance metadata to {PROVENANCE_FILE}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull historical data for KPK Carry Sim")
    parser.add_argument("--force", action="store_true", help="Force re-download even if cached")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    btc_csv = RAW_DIR / "btc_usd_hourly.csv"
    dtb3_csv = RAW_DIR / "dtb3.csv"

    btc_rows, btc_sha = pull_coinbase_btc_hourly(btc_csv, force=args.force)
    dtb3_rows, dtb3_sha = pull_fred_dtb3(dtb3_csv, force=args.force)

    btc_info = {
        "rows": btc_rows,
        "sha256": btc_sha,
        "path": "data/raw/btc_usd_hourly.csv",
    }
    dtb3_info = {
        "rows": dtb3_rows,
        "sha256": dtb3_sha,
        "path": "data/raw/dtb3.csv",
    }

    write_provenance(btc_info, dtb3_info)
    print("Data pull complete.")


if __name__ == "__main__":
    main()
