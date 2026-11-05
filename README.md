# KPK BTC Carry Simulation Engine (Phase 1 Core)

Gauntlet-style agent-based Monte Carlo risk engine for evaluating an institutional-scale ($50M) BTC carry fund across thousands of simulated futures.

Implements the **Phase 1 core engine** per governing specification `docs/SPEC.md` (v0.3).

---

## Strategy & Parameter Provenance

All strategy parameters are loaded from `configs/fund_template.yaml` and were **verified on 5 Nov 2026** against primary `docs.kpk.io` documentation:
- **WBTC Carry Fund**: [https://docs.kpk.io/funds/funds/wbtc-carry](https://docs.kpk.io/funds/funds/wbtc-carry) (thresholds verified as of 1 Nov 2026)
- **cbBTC Carry Fund**: [https://docs.kpk.io/funds/funds/cbbtc-carry](https://docs.kpk.io/funds/funds/cbbtc-carry) (thresholds verified as of 4 Nov 2026)

### Venue Liveness Gate & Venue Exclusion
Per **SPEC §13.3 (Venue-Liveness Gate)**, protocols must be actively operating and solvent to qualify as live venues:
- **Balancer is EXCLUDED** as a live venue throughout code and configurations. Balancer Labs announced wind-down following BIP-928 (>99% governance vote, 29 Sep 2026), pools transitioned to withdrawal-only on 30 Oct 2026, and the v3 Vault pauses 30 Nov 2026.
- Active lending venue: **Aave v3 Core (Ethereum)** with LLTV 0.85 and target LTV 0.702 (WBTC / cbBTC collateral; USDC debt).

---

## Architecture & Repo Layout

Per SPEC §8, the Phase 1 repository structure is organized as follows:

```
kpk-carry-sim/
├── README.md                          # Engine documentation, seed policy, verification guide
├── pyproject.toml                     # Python >= 3.11 build configuration and pinned dependencies
├── configs/
│   └── fund_template.yaml             # Verified KPK published parameters (source-cited)
├── data/
│   ├── pull_data.py                   # Data ingestion: Coinbase BTC/USD hourly candles + FRED DTB3
│   ├── PROVENANCE.md                  # SHA256 checksums, timestamps, row counts, and source URLs
│   └── raw/                           # Cached raw data (idempotent; skipped if present)
│       ├── btc_usd_hourly.csv         # 33,588 hourly candles (Jan 2023 - Oct 2026)
│       └── dtb3.csv                   # Daily 3-month Treasury bill rate series
├── sim/
│   ├── __init__.py                    # Public API exports
│   ├── prices.py                      # Three generators: Block Bootstrap, Jump Diffusion, Regime-Switching (§3.1)
│   ├── rates.py                       # Aave v3 kinked IRM + vault APY own-size dilution model (§3.1)
│   ├── venue.py                       # Aave pool state tracker, liquidation math, Chainlink oracle model (§3.2)
│   ├── agents.py                      # Fund Agent policy class (HF band deleverage, full unwinds) (§3.3)
│   ├── engine.py                      # Vectorised hourly path runner and position simulator (§3.1–§3.3)
│   └── metrics.py                     # Distribution outputs: median, p5/p95, P(loss), P(liq), ES95 (§6)
├── tests/
│   ├── test_liquidation_math.py       # Hand-checkable liquidation arithmetic (LTV 0.702 vs 0.60) (§7)
│   ├── test_no_lookahead.py           # Zero look-ahead verification across truncation horizons (§7)
│   └── test_seed_reproducibility.py   # Bit-for-bit seed reproducibility across generators and engine (§1)
└── notebooks/
    └── 01_calibration.py              # Statistical calibration suite against Jan 2023–Oct 2026 BTC data (§7)
```

---

## Price Path Generators (SPEC §3.1)

Three parallel price generators are implemented with the identical signature:
```python
generate(n_paths: int, n_hours: int, seed: int, s0: float = 100.0, **params) -> np.ndarray
```
returning price arrays of shape `[n_paths, n_hours + 1]` where column 0 is entry price $S_0$:

1. **Historical Block Bootstrap (`HistoricalBlockBootstrap`)**: Resamples 24-hour contiguous blocks of empirical BTC hourly log-returns from Jan 2023–Oct 2026. Preserves empirical volatility clustering, intraday patterns, and heavy tails.
2. **Jump Diffusion (`JumpDiffusion`)**: Continuous GBM with drift 0 and Poisson jump process whose jump sizes are drawn directly from the empirical distribution of BTC's worst 200 hourly drops.
3. **Regime-Switching (`RegimeSwitching`)**: 3-state Markov chain (Calm, Stressed, Crisis) with empirical transition matrix estimated from 24h rolling realised volatility. Generates endogenous multi-day volatility clustering.

---

## Quickstart

### 1. Installation
Requires Python >= 3.11.

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install numpy pandas scipy pyarrow matplotlib pyyaml pytest requests
```

### 2. Data Pull (Idempotent)
Pulls hourly BTC/USD candles (Jan 2023–Oct 2026) from the Coinbase Exchange API and 3-month T-bill rates from FRED, computing SHA256 checksums in `data/PROVENANCE.md`:

```bash
python data/pull_data.py
```
*Note: Subsequent runs detect existing files and skip re-downloading.*

### 3. Run Test Suite
Runs the full test suite including liquidation math hand-checks, zero look-ahead bias validation, and seed reproducibility:

```bash
pytest tests/ -v
```

### 4. Run Calibration Suite
Runs the statistical calibration suite comparing simulated return distributions against realised historical data:

```bash
python notebooks/01_calibration.py
```

---

## Seed Policy & Reproducibility

Every random generation step uses NumPy's modern `np.random.default_rng(seed)` generator:
- **Bit-for-Bit Determinism**: Given the same seed and input parameters, the output arrays of all generators and the simulation engine match bit-for-bit (`np.array_equal` evaluates to `True`).
- **Seed Differentiation**: Different seeds produce distinct stochastic trajectories.
- All benchmark runs, scenarios, and tests record their explicit seeds.

---

## Liquidation Math & Invariants (SPEC §7 Hand-Checks)

The engine enforces exact onchain DeFi arithmetic:

$$\text{HF} = \frac{\text{Collateral Value} \times \text{LLTV}}{\text{Debt Value}} = \frac{C \times P_{\text{oracle}} \times \text{LLTV}}{D}$$

- **Target Entry HF**: At active venue parameters ($\text{LLTV} = 0.85$, $\text{Target LTV} = 0.702$):
  $$\text{HF}_{\text{entry}} = \frac{0.85}{0.702} = 1.210826...$$
- **Liquidation Price Threshold**:
  $$P_{\text{liq}} = P_0 \times \frac{\text{Target LTV}}{\text{LLTV}} = P_0 \times \frac{0.702}{0.85} = 0.825882 \times P_0$$
  The required price drop to trigger liquidation is:
  $$\Delta P_{\text{liq}} = 1 - \frac{0.702}{0.85} = 17.4118\% \approx 17.4\%$$
- **Conservative LTV Invariant**: At $\text{LTV} = 0.60$, the same $17.41\%$ price drop yields $\text{HF} = 1.170 > 1.0$, preventing liquidation.
