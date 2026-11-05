# KPK BTC Carry Simulation Engine (Phase 1 Core + Phase 2 Agents & Events)

Gauntlet-style agent-based Monte Carlo risk engine for evaluating an institutional-scale ($50M) BTC carry fund across thousands of simulated futures.

Implements **Phase 1 (Core Engine)** and **Phase 2 (Agent Populations, S1–S12 Event Library, Historical Replays, and Portfolio Risk)** per governing specification `docs/SPEC.md` (v0.3).

---

## Strategy & Parameter Provenance

All strategy parameters are loaded from `configs/fund_template.yaml` and were **verified on 5 Nov 2026** against primary `docs.kpk.io` documentation:
- **WBTC Carry Fund**: [https://docs.kpk.io/funds/funds/wbtc-carry](https://docs.kpk.io/funds/funds/wbtc-carry) (thresholds verified as of 1 Nov 2026)
- **cbBTC Carry Fund**: [https://docs.kpk.io/funds/funds/cbbtc-carry](https://docs.kpk.io/funds/funds/cbbtc-carry) (thresholds verified as of 4 Nov 2026)

### Venue Liveness Gate & Exclusion Rule
Per **SPEC §13.3 (Venue-Liveness Gate)**, protocols must be actively operating and solvent to qualify as live venues:
- Sunset/wind-down venues (e.g. BIP-928) are **permanently excluded** from live portfolio construction.
- Active lending venue: **Aave v3 Core (Ethereum)** with LLTV 0.85 and target LTV 0.702 (WBTC / cbBTC collateral; USDC debt).
- Replay venue: **Aave V4 Bluechip** with LLTV 0.845 and target LTV 0.698 (venue active during 29 Oct 2026 freeze).

---

## Phase 2 Components

### 1. Agent Populations (`sim/agents.py`)
Per SPEC §3.3:
- **Fund Agent**: Executes the full verified trigger list:
  - Full unwind: HF < 1.13; BTC 24h drop <= -10%; two consecutive 6h negative-carry windows; wrapper peg off 1% sustained 40 min; USDC off peg 0.5%; exploit detection on venue/oracle; venue freezes reserve or sets LTV to 0; venue changes price source; custodian multisig threshold set to 1 (WBTC); cbBTC owner/proxy/pauser change.
  - Partial action (`VAULT_EXIT`): deployment vault curator changes, or vault share price drops > 1 bp/min (exits that vault, redeploys or holds USDC; NOT a full unwind).
  - Deleveraging: HF restored to target 1.21 when entering [1.13, 1.15).
- **Liquidator Agents (`LiquidatorPopulation`)**: Heterogeneous bot population across capital, gas bids, and profit thresholds. Liquidates positions with HF < 1.0 using standard 50% close factor only when net profit exceeds threshold after gas and slippage. Skips small positions during gas wars.
- **Background Borrowers (`BorrowerPopulation`)**: Drives Aave USDC pool utilisation; panic-deleveraging and margin seizures generate liquidation cascades during market stress.
- **Depositors (`DepositorPopulation`)**: Simulates vault depositor runs in stress regimes, surging utilisation to 100% and blocking withdrawals (the EURC trapped liquidity mechanism).
- **Arbitrageurs (`ArbitrageurPopulation`)**: Restores wrapper pegs at speed proportional to discount and capital. Under hack scenarios, capital halves due to redemption halts.

### 2. Scenario Shock Library (`sim/events.py` & `configs/scenarios.yaml`)
Injects S1–S12 scenarios per SPEC §4:
- **S1 (Flash crash)**: BTC -14% intraday path, borrower liquidation cascade, gas 20x for 6h.
- **S2 (Sustained crash)**: Multi-day -40%/-50% paths via bootstrap blocks.
- **S3 (Venue freeze)**: Aave market frozen T hours (1h, 12h, 72h).
- **S4 (Wrapper depeg)**: cbBTC or WBTC trades -3% / -5% / -8% for 6–48h; arbitrageur capital halved.
- **S5 (Collateral-issuer exploit)**: Resolv-style exploit: oracle marks $1.13 while market trades $0.63.
- **S6 (Opaque counterparty default)**: Stream Finance / xUSD-style default: 30% / 65% / 100% loss discovered with 7-day lag.
- **S7 (Utilisation crunch)**: EURC-style crunch: deployment vault utilisation -> 100% for 24–96h; withdrawals blocked.
- **S8 (Oracle failure)**: Oracle stale for 24h then -12% catch-up jump, or prints -5% wrong for 3h.
- **S9 (Stablecoin depeg)**: USDC -13% intraday, recovers in 48h.
- **S10 (Negative carry)**: Borrow rate exceeds deployment yield for 30–90 days.
- **S11 (Gas war)**: Gas 10–50x for 12h; liquidators skip small positions; transactions queue.
- **S12 (Correlated failure)**: Composite S1 + S3 + S4 (crash during freeze with wrapper depeg).

*Contract Invariant*: Each scenario injector modifies **exactly** its declared state variables and no others.

### 3. Portfolio Risk Module (`sim/portfolio.py`)
Per SPEC §13.1:
- Exposure shares by venue, asset, and issuer / custodian.
- Herfindahl-Hirschman Index (HHI) concentration calculations.
- Named counterparty table (Aave Core, Morpho, Gearbox, KPK yield vaults, Coinbase, BitGo, Deribit, US Treasury).
- Loss-given-default hooks consumed directly by S5 and S6 scenario injectors.

### 4. Replay Validations (`tests/test_replay_oct2025.py`)
Per SPEC §7:
- **Replay 1 (29 Oct 2026 Binance Hack)**: Position on Aave V4 (LLTV 84.5%, target LTV 69.8%, 4.925 WBTC, 277,337 USDC debt). With a freeze applied to a DIFFERENT venue, fund agent executes clean full unwind with zero liquidation loss (repaid 277,337 USDC, returned 4.925 WBTC whole), reproducing KPK's real-world outcome.
- **Replay 2 (10 Oct 2025 Liquidation Arithmetic)**: On recorded path with S1 cascade, at target LTV 70.2% min HF approaches 1.0 within one hourly step of the theoretical liquidation point (17.41% fall); at 60% LTV the position strictly survives (min HF ~1.170 > 1.0, zero liquidations).

---

## Repo Layout

```
kpk-carry-sim/
├── README.md                          # Engine documentation & instructions
├── pyproject.toml                     # Python >= 3.11 build configuration & deps
├── configs/
│   ├── fund_template.yaml             # Verified KPK published parameters (source-cited)
│   └── scenarios.yaml                 # S1–S12 scenario parameter blocks
├── data/
│   ├── pull_data.py                   # Data ingestion: Coinbase BTC/USD candles + FRED DTB3
│   ├── PROVENANCE.md                  # SHA256 checksums, timestamps, row counts, source URLs
│   └── raw/                           # Cached raw data (idempotent)
│       ├── btc_usd_hourly.csv         # 33,588 hourly candles (Jan 2023 - Oct 2026)
│       └── dtb3.csv                   # Daily 3-month Treasury bill rate series
├── sim/
│   ├── __init__.py                    # Public API exports
│   ├── prices.py                      # Three generators: Block Bootstrap, Jump Diffusion, Regime-Switching (§3.1)
│   ├── rates.py                       # Aave v3 kinked IRM + vault APY own-size dilution model (§3.1)
│   ├── venue.py                       # Aave pool state tracker, liquidation math, Chainlink oracle model (§3.2)
│   ├── agents.py                      # Fund Agent + Liquidators, Borrowers, Depositors, Arbitrageurs (§3.3)
│   ├── events.py                      # S1–S12 scenario shock injectors + historical replay loaders (§3.4, §4)
│   ├── portfolio.py                   # Portfolio risk metrics, HHI concentration, counterparty default hooks (§13.1)
│   ├── engine.py                      # Vectorised hourly path runner and position simulator (§3.1–§3.3)
│   └── metrics.py                     # Distribution outputs: median, p5/p95, P(loss), P(liq), ES95 (§6)
├── tests/
│   ├── test_liquidation_math.py       # Hand-checkable liquidation arithmetic (LTV 0.702 vs 0.60) (§7)
│   ├── test_no_lookahead.py           # Zero look-ahead verification across truncation horizons (§7)
│   ├── test_seed_reproducibility.py   # Bit-for-bit seed reproducibility across generators and engine (§1)
│   ├── test_events.py                 # S1–S12 state mutation invariants & composition tests (§4)
│   ├── test_replay_oct2025.py         # Replay 1 (29 Oct 2026) & Replay 2 (10 Oct 2025) validations (§7)
│   ├── test_portfolio.py              # Portfolio concentration shares, HHI, and counterparty default hooks (§13.1)
│   └── test_fund_agent_triggers.py    # Extended verified triggers & VAULT-EXIT partial action checks
└── notebooks/
    └── 01_calibration.py              # Statistical calibration suite against Jan 2023–Oct 2026 BTC data (§7)
```

---

## Instructions for Running Tests

Ensure virtual environment is activated:
```bash
source .venv/bin/activate
```

### 1. Run Complete Test Suite
Runs all 41 unit and validation tests:
```bash
pytest tests/ -v
```

### 2. Run Historical Replays (SPEC §7)
Runs Replay 1 (29 Oct 2026 clean unwind) and Replay 2 (10 Oct 2025 liquidation arithmetic):
```bash
pytest tests/test_replay_oct2025.py -v
```

### 3. Run Event Library Invariant Tests (SPEC §4)
Verifies that S1–S11 each modify exactly the specified state variables and no others, and that S12 composes S1 + S3 + S4:
```bash
pytest tests/test_events.py -v
```

### 4. Run Portfolio Risk & Counterparty Tests (SPEC §13.1)
Verifies concentration shares, HHI indices, and counterparty default hooks:
```bash
pytest tests/test_portfolio.py -v
```

### 5. Run Fund Agent Full Verified Triggers
Verifies wrapper peg, USDC peg, exploit detection, venue freeze, and VAULT-EXIT triggers:
```bash
pytest tests/test_fund_agent_triggers.py -v
```

### 6. Run Generator Calibration
Validates moment matching, tail heaviness, and volatility clustering:
```bash
python notebooks/01_calibration.py
```
