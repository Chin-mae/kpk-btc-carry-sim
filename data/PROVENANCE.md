# Data Provenance

Generated per SPEC §8 and §9 on `2026-11-05T10:11:17.665068+00:00`.

All data series are cached locally in `data/raw/` with SHA256 integrity hashes.
Data collection is idempotent: existing files with matching hashes are not re-downloaded.

## Dataset Manifest

| Asset / Series | Source URL | Rows | File Path | SHA256 Checksum |
| :--- | :--- | :--- | :--- | :--- |
| **BTC/USD Hourly Candles** (Jan 2023–Oct 2026) | `https://api.exchange.coinbase.com/products/BTC-USD/candles` | 33,588 | `data/raw/btc_usd_hourly.csv` | `1c842b533f9ca7785444bcb05c462c95c16de02a6679b57e6776775b0e51107f` |
| **3-Month Treasury Bill Rate (DTB3)** | `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3` | 19,002 | `data/raw/dtb3.csv` | `e5fa1cb672371e23f5eb5fa45c23dc61e122b1a97cd9b3ebc641bcdaf68f45ac` |

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
