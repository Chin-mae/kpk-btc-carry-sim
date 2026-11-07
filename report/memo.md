# KPK BTC Carry Fund: Institutional Risk Architecture and Parameter Recommendation Memo

**Document Status:** Research Memo (Phase 3E Parameter Sweep and Scenario Verification)  
**Date:** 6 November 2026  
**Author:** Chinmay Gupta  
**Scope:** Institutional Bitcoin Carry Fund at $50,000,000 USD Design Scale  
**Framework:** Agent-Based Monte Carlo Simulation Engine (SPEC v0.3)  
**Traceability:** Fully reproducible; all metrics traceable directly to `report/runs.jsonl` (grid runs stamped git commit `7b809d8b`; downstream runs stamped `520262df`).

---

## 1. Executive Summary and Scope

### 1.1 The Fund Design and the Mechanical Recommendation
This study evaluates the risk architecture and operational parameters of an institutional Bitcoin Carry Fund modeled on the parameters published by KPK (`https://docs.kpk.io/funds/funds/wbtc-carry`, verified 5 November 2026). The fund executes an automated, collateralized carry strategy:
1. Supplies wrapped Bitcoin collateral (`WBTC`, `cbBTC`) to prime onchain lending markets.
2. Borrows `USDC` against supplied Bitcoin up to a designated target Loan-to-Value (`target_ltv`).
3. Deploys borrowed `USDC` into curated yield lending vaults (`kpk_usdc_yield_vault`, `kpk_usdc_yield_rwa_vault`, `yo_ecosystem_yousd`).
4. Executes automated risk policies to maintain health factors within target bands, unwinding cleanly during market stress.

The mechanical selection rule defined in `sim/sweep.py` (`select_recommended_cell`: filter paths with $P(\text{liquidation}) = 0$ and 5th percentile minimum health factor $\ge 1.10$, ranking by carry ES95 loss ascending then median net APY descending) selected **Cell 149**:
- **Target LTV:** 70.2% (retained from KPK published template)
- **Health Factor Unwind Floor:** 1.05 (lowered from template 1.13)
- **Collateral Wrapper Split:** 70:30 WBTC:cbBTC (diversified from 100:0 WBTC)
- **Deployment Split:** Three vaults (equal thirds, loss share 1/3 vs 1/2 uncapped, modelled under the 25% cap parameter setting)
- **Execution Policy:** `kpk_published` (retained from published policy)

On standard simulation paths, the empirical data does not indict KPK's 70.2% leverage. Rather, the evidence indicts:
1. Waiting until Health Factor (HF) drops below 1.13 to initiate full unwind, which triggers excessive false-alarm churn during transient market dips.
2. Single-wrapper collateral concentration (100% WBTC), which leaves the entire fund exposed to bridge freezes or custodian actions.
3. Uncapped vault deployments, which expose fund equity to single-destination counterparty defaults.

### 1.2 Methodological Candour: Withdrawal of Preliminary Draft
An earlier internal draft of this deliverable recommended a 60.0% target LTV with volatility-scaled deleveraging (`cell_269`). That draft failed internal adversarial review: the 60.0% configuration was designated prior to the completion of the sweep, scenario losses were tabulated with static additives rather than simulated injectors, and allocation extensions had not been executed. In accordance with institutional research standards, that preliminary draft is formally withdrawn. The results presented in this memo are generated strictly from the committed codebase (grid runs stamped `7b809d8b`, downstream runs stamped `520262df`), executing real event shock injectors through the simulation engine across all 1,126 logged runs.

### 1.3 Design Scale
All quantitative evaluations are sized to the institutional design scale of **$50,000,000 USD**. Sizing parameterisation accounts for non-linear scale effects: vault dilution curves, protocol pool utilisation displacement, and liquidator execution constraints during liquidity cascades. Sizing is evaluated as an engineering target for risk modeling.

---

## 2. Multi-Strategy Sleeve Allocation Architecture

### 2.1 The Baseline Prior and Allocation Extension
The fund operates a multi-strategy structure comprising four sleeves:
- **Carry Sleeve (Anchor):** Supplies BTC collateral, borrows USDC, deploys into curated Morpho lending vaults.
- **Basis Sleeve (Delta-Neutral Funding Arbitrage):** Captures perpetual funding spreads across centralized and decentralized venues.
- **AMM Liquidity Sleeve (Fee Capture):** Concentrated LP fee generation in high-volume pairs.
- **Liquid T-Bill Reserve (Margin and Redemption Buffer):** Sovereign cash floor providing liquidity without forced liquidation.

Per SPEC §11, the starting baseline prior allocation was set at **45% Carry / 20% Basis / 15% AMM / 20% T-Bill**. To assess allocation sensitivity, three extension runs were executed at 30%, 45%, and 60% carry sleeve weightings:

| Sleeve | Baseline Prior | Active Allocation (Gated) | Extension (30% Carry) | Extension (60% Carry) | Primary Role and Governance Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Carry Sleeve** | **45.0%** ($22.5M) | **45.0%** ($22.5M) | **30.0%** ($15.0M) | **60.0%** ($30.0M) | **Core Anchor:** Generates 1.94% net BTC APY; long BTC collateral with downside directional risk. |
| **Basis Sleeve** | **20.0%** ($10.0M) | **0.0% (Gated to T-Bills)** | **0.0% (Gated)** | **0.0% (Gated)** | **Delta-Neutral Funding:** Gated to T-bills when funding spread falls below risk-free yield. |
| **AMM Sleeve** | **15.0%** ($7.5M) | **15.0%** ($7.5M) | **20.0%** ($10.0M) | **10.0%** ($5.0M) | **Fee Capture:** Maintained at capacity ceiling; full dynamic hedging scoped as future work. |
| **T-Bill Reserve** | **20.0%** ($10.0M) | **40.0%** ($20.0M) | **50.0%** ($25.0M) | **30.0%** ($15.0M) | **Liquid Buffer:** Sovereign margin and redemption reserve; expands when basis gate activates. |

### 2.2 Assumptions Table
Yields for non-carry sleeves are documented transparently below:

| Sleeve / Asset | Model APY | Parameter Type | Data Source and Provenance |
| :--- | :--- | :--- | :--- |
| **US Treasury 3-Month Bills** | 4.03% | Observed Data Series | FRED series `DTB3` (`data/raw/dtb3.csv`), secondary market rate dated 3 Nov 2026. 2023 to 2026 range: 3.52% to 5.36% (mean 4.49%). |
| **Basis Funding Rate** | 2.20% | Market Proxy | Observed 2026 Ethena / CeFi perpetual funding compression; labelled as an empirical proxy for BTC perpetual basis. |
| **AMM Liquidity Yield** | 6.00% | Assumed Parameter | Assumed baseline pool fee net APY; AMM dynamic simulation sub-engine remains future work. |
| **Carry Sleeve Yield** | 1.9373% | Simulated Output | Simulated net BTC yield from selected Cell 149 under Historical Block Bootstrap (grid stamped `7b809d8b`). |

### 2.3 Automated Basis Gate Mechanics
Throughout 2026, perpetual funding rates compressed toward 2.20% annualised, falling well below short-term US Treasury bill yields (4.03%). Sizing an un-gated basis sleeve at 20% would create yield drag. The fund enforces an automated gate rule:
$$\text{If } R_{\text{funding}} < R_{\text{DTB3}} \implies \text{Gate Active: } w_{\text{basis}} \to 0\%, \quad w_{\text{tbill, eff}} = w_{\text{tbill}} + w_{\text{basis}}$$
When active, the basis allocation redirects into risk-free 3-month T-bills. This expands total sovereign cash liquidity to **40.0% ($20.0M)**, capturing an incremental +1.83% annual spread over perpetual funding while eliminating exchange counterparty risk.

### 2.4 Allocation Extension Results
Evaluating the portfolio across carry sleeve weightings reveals the following performance trade-off:
- **30% Carry / 20% AMM / 50% T-Bill:** Portfolio Net APY **3.8442%**, Portfolio ES95 **-0.8143%**.
- **45% Carry / 15% AMM / 40% T-Bill:** Portfolio Net APY **3.4189%**, Portfolio ES95 **-0.6521%** (matches grid cell 149 exactly).
- **60% Carry / 10% AMM / 30% T-Bill:** Portfolio Net APY **2.9949%**, Portfolio ES95 **-0.4899%**.

Higher T-bill allocations generate higher portfolio APY because risk-free sovereign cash (4.03%) yields more than the net carry spread (~1.94%) in compressed rate environments. The 45% carry weighting is maintained as the baseline balance between Bitcoin carry generation and cash liquidity.

---

## 3. Venue Layer and Governance Liveness Gate (§13.2 and §13.3)

### 3.1 Primary Lending Venue Parameters
Venue parameters are taken from the verified fund template (`https://docs.kpk.io/funds/funds/wbtc-carry`, archived in `configs/fund_template.yaml`):

| Venue | Protocol Structure | Liquidation LLTV | Target LTV (HF 1.21) | Protocol Status and Key Risk Factor |
| :--- | :--- | :--- | :--- | :--- |
| **Aave v3 Core (Ethereum)** | Pooled Reserve | **0.850** | **0.702** | **Active Primary Borrow Venue:** Deepest market liquidity; kinked interest rate model (optimal utilisation 90%, slope1 5.5%, slope2 60%). |
| **Aave v4 Bluechip** | Isolated Market | **0.845** | **0.698** | **Approved Alternative:** Verified venue row utilized during the 29 October 2026 Binance incident replay. |
| **Morpho Markets** | Isolated Lending Pairs | **0.860** | **0.711** | **Approved Alternative:** Isolated peer-to-peer risk; highest LLTV. Serves as home layer for deployment vaults. |
| **Spark Protocol** | Maker/Sky Allocator | **0.780** (WBTC) / **0.820** (cbBTC) | **0.645** / **0.678** | **Approved Alternative:** Conservative LLTV on WBTC; cbBTC favoured. Governed by Maker/Sky risk models. |
| **Compound v3** | Single-Borrow Market | **0.860** | **0.711** | **Approved Alternative:** cbBTC approved; WBTC under review per template configuration. |

### 3.2 Gearbox Protocol Status
Gearbox Protocol was noted in preliminary literature but is absent from the approved primary venue list in `configs/fund_template.yaml`. Deploying a $22.5M carry sleeve into Gearbox would encounter severe capacity constraints at the fund's $50M design scale, creating potential liquidity exit bottlenecks. Gearbox is therefore excluded as a primary borrowing venue on qualitative capacity grounds.

### 3.3 Balancer Protocol Wind-Down Disqualification
The hiring job description referenced Balancer among potential venues. Diligence confirms this is a stale reference:
- Following the November 2025 v2 exploit, Balancer Labs announced organizational wind-down.
- BAL tokenholders passed governance proposal **BIP-928** on 29 September 2026 (>99% approval) for an orderly protocol sunset.
- On **30 October 2026**, all pausable Balancer pools entered **withdrawal-only** mode.
- The Balancer v3 Vault permanently pauses on **30 November 2026**.

**Governance Decision:** Balancer is permanently disqualified as an active venue. It is retained only in historical AMM backtest archives.

---

## 4. Quantitative Methodology

### 4.1 Simulation Architecture
The Monte Carlo simulation engine evaluates positions across **10,000 paths** over a **90-day horizon** (2,160 hours per path) at **1-hour resolution**. The simulation incorporates four coupled layers:
1. **Price Generator:** Simulates hourly BTC price trajectories across three statistical generators.
2. **Venue Layer:** Models Aave v3 Core kinked interest rate dynamics and utilization spikes.
3. **Agent Layer:** Models the fund execution agent, heterogeneous liquidator bots, background borrowers, and vault depositors.
4. **Event Layer:** Injects deterministic historical shocks (S1 to S12) directly into simulated paths.

### 4.2 Three Independent Price Path Generators
To evaluate robustness without statistical blending, results are produced across three distinct panels:
1. **Historical Block Bootstrap (Primary Panel, 10,000 paths):** Resamples contiguous 24-hour blocks from 33,588 historical hourly candles (Jan 2023 to Oct 2026). Preserves empirical volatility clustering, fat tails, and drawdown morphology without parametric assumptions.
2. **Jump Diffusion (Robustness Panel 1, 5,000 paths):** Zero-drift continuous Gaussian diffusion coupled to a Poisson jump process ($\lambda = 0.00595$ jumps/hour, approximately 1 jump per 168 hours). Jump magnitudes are sampled from the empirical distribution of Bitcoin's worst 200 historical hourly moves.
3. **Regime-Switching (Robustness Panel 2, 5,000 paths):** 3-state Markov chain calibrated to historical hourly volatility:
   - Calm Regime: $\sigma = 0.31\%$ hourly (29.4% annualised)
   - Stressed Regime: $\sigma = 0.55\%$ hourly (51.8% annualised)
   - Crisis Regime: $\sigma = 0.95\%$ hourly (89.2% annualised)

### 4.3 Delta and Risk Profile
The basis sleeve is delta-neutral. In contrast, the carry sleeve maintains **BTC-downside directional exposure**. Although the borrowed USDC leg is deployed into dollar lending vaults, the collateral backing the loan is Bitcoin. When Bitcoin price declines, the dollar value of collateral contracts while the dollar debt accrues interest, driving the health factor downward toward liquidation thresholds.

### 4.4 Equal-Thirds Slicing Dilution Example
The modeled risk mitigation slices borrowed USDC equally across three destination vaults (loss share 1/3, modelled under the 25% cap parameter). Sizing $15.795M USDC equally across three vaults places $5.265M in each vault. As an illustrative example, depositing $5.265M into a $21.06M destination vault represents 20% of post-deposit TVL:
$$\text{APY}_{\text{diluted}} = \text{APY}_{\text{base}} \times \frac{V_{\text{pre}}}{V_{\text{pre}} + D_{\text{fund}}} = 8.50\% \times \frac{21.06}{26.325} = 6.80\%$$
This arithmetic is labelled an illustrative example; no claim of live vault TVLs is made, as live TVLs fluctuate over time.

---

## 5. Historical Replay Validations

### 5.1 Replay 1: 29 October 2026 Binance Venue Freeze
On 29 October 2026, an external security incident froze Binance cross-chain bridges. The fund agent was evaluated using KPK's documented position on the Aave v4 Bluechip market:
- **Parameters:** LLTV 84.5%, Target LTV 69.8%, Initial HF 1.2106.
- **Position:** 4.925 WBTC collateral borrowing 277,337 USDC.
- **Outcome:** The fund agent identified the external venue freeze signal at hour 24 and initiated an automated full unwind. The loan was repaid in full ($277,337 USDC) and 4.925 WBTC was reclaimed intact. Liquidation loss was 0.0000 BTC.
- **Methodological Caveat:** This test serves as a mechanics-consistency validation, confirming that the simulation engine accurately reproduces documented agent behaviour and protocol parameters. It does not constitute an independent, out-of-sample statistical proof of future performance.

### 5.2 Replay 2: 10 October 2025 Cascade Arithmetic (Cascade-Adjusted)
On 10 October 2025, BTC experienced an intraday drop. Historical hourly candle data (`data/raw/btc_usd_hourly.csv`) records:
- First close: $121,814.96
- Lowest hourly close in 24h: $112,980.28 (-7.2525%)
- Intraday low: $107,000.00 (-12.1619%)

To stress the protocol's distance-to-liquidation arithmetic under extreme conditions, the historical replay uses a cascade-adjusted path where the trough is forced to the template's theoretical liquidation boundary.
- **Distance to Liquidation Arithmetic:** At 70.2% Target LTV and 85.0% LLTV, the theoretical price drop required to breach $\text{HF} = 1.0$ is:
  $$1.0 - \frac{\text{Target LTV}}{\text{LLTV}} = 1.0 - \frac{0.702}{0.850} = 17.4118\% \quad (\text{a fall from } \$121,814.96 \text{ to } \$100,604.83)$$
- **Outcome:** At relative hour 4 of the cascade-adjusted path, price reaches the theoretical liquidation threshold. The position health factor touches 1.0000. Under an unmanaged position, this point initiates liquidation. This replay validates the theoretical distance-to-liquidation arithmetic. It is an arithmetic verification; no claim of an executed onchain liquidation during historical trading is made.

---

## 6. Efficient Frontier and Parameter Recommendations (§6.2)

### 6.1 Mechanical Selection Outcome
Across the 1,080 evaluated parameter combinations (360 per generator panel), the mechanical selection rule (`select_recommended_cell`: filter $P(\text{liquidation})=0$ and min-HF $p_5 \ge 1.10$, ranking by carry ES95 loss ascending then median net APY descending) operates with the following explicit mathematical properties:
1. **Admissibility:** On the primary Historical Block Bootstrap panel, all 360 cells satisfy both filter criteria ($P(\text{liquidation})=0.00\%$ and min-HF $p_5 \ge 1.10$ hold across all combinations due to automated unwinds).
2. **Exact Ties:** Four cells tie exactly on both ranking metrics: Cell 149, Cell 150, Cell 152, and Cell 153 all produce carry ES95 of -0.0737% and carry median net APY of 1.9373%.
3. **Selection Tie-Breaker:** The selection among exact ties is determined by stable order in the evaluated grid (Cell 149 is evaluated first). All four tied cells share the identical core risk architecture: 70.2% LTV, 1.05 unwind floor, and `kpk_published` policy.
4. **Signed ES95 Ranking Interpretation:** Because standard surviving paths experience positive carry return even in the 5% tail, the signed ES95 ranking is tail-return optimisation among configurations whose worst-5% standard-path outcomes are still gains, stated in those words.

The selected configuration is **Cell 149**:
- **Target LTV:** 70.2%
- **HF Unwind Floor:** 1.05
- **Sleeve Split:** 70:30 WBTC:cbBTC
- **Deployment Split:** Three vaults (equal thirds, loss share 1/3 vs 1/2 uncapped, modelled under the 25% cap parameter setting)
- **Execution Policy:** `kpk_published`

### 6.2 Three-Panel Performance Comparison: Recommended (Cell 149) vs Template (Cell 163)
The three generator panels are reported side by side below, unblended:

| Panel / Metric | KPK Template (`cell_163`) | Recommended Redesign (`cell_149`) | Structural Advantage / Difference |
| :--- | :--- | :--- | :--- |
| **Core Parameters** | | | |
| Target LTV | 70.2% | **70.2%** | Leverage retained; distance to liquidation is 17.41% |
| HF Unwind Floor | 1.13 | **1.05** | Lowers floor; prevents false-alarm unwinds during transient noise |
| Collateral Split | 100% WBTC : 0% cbBTC | **70% WBTC : 30% cbBTC** | Diversifies custodian and wrapper bridge exposure |
| Vault Deployment Split | None (Uncapped, 1/2 split) | **Three Vaults (Equal Thirds)** | Modelled under 25% cap parameter; cuts single-vault default loss share from 1/2 to 1/3 |
| Execution Policy | `kpk_published` | **`kpk_published`** | Standard published policy retained |
| **Historical Block Bootstrap (10,000 paths)** | | | |
| Carry Median Net APY | 1.8843% | **1.9373%** | +5.3 bps improvement from reduced unwind churn |
| Carry ES95 Loss % | -0.0359% | **-0.0737%** | Standard paths compressed; worst 5% outcomes remain net gains |
| P(Unwind) | 28.27% | **21.64%** | -6.63 percentage points reduction in policy turnover |
| Min-HF (5th Percentile) | 1.1241 | **1.1240** | Holds comfortably above the 1.05 floor |
| P(Liquidation) | 0.00% | **0.00%** | Zero liquidation on standard paths (pre-empted by unwind floor) |
| Portfolio Median Net APY | 3.3948% | **3.4189%** | +2.4 bps portfolio yield increase |
| **Jump Diffusion (5,000 paths)** | | | |
| Carry Median Net APY | 2.1417% | **2.2187%** | +7.7 bps yield improvement |
| Carry ES95 Loss % | -0.0602% | **-0.2015%** | Tail outcome improvement under discrete jumps |
| P(Unwind) | 18.00% | **7.80%** | -10.20 percentage points reduction in unnecessary unwinds |
| Min-HF (5th Percentile) | 1.1224 | **1.1222** | Maintained above 1.05 floor |
| P(Liquidation) | 0.00% | **0.00%** | Zero liquidation across jump diffusion paths |
| Portfolio Median Net APY | 3.5119% | **3.5469%** | +3.5 bps portfolio yield increase |
| **Regime-Switching (5,000 paths)** | | | |
| Carry Median Net APY | 1.9650% | **2.0230%** | +5.8 bps yield improvement |
| Carry ES95 Loss % | -0.0335% | **-0.0521%** | Tail protection maintained across persistent stress regimes |
| P(Unwind) | 35.74% | **30.44%** | -5.30 percentage points reduction in stress unwind churn |
| Min-HF (5th Percentile) | 1.1242 | **1.1242** | Stable buffer across Markov regimes |
| P(Liquidation) | 0.00% | **0.00%** | Zero liquidation across regime switching paths |
| Portfolio Median Net APY | 3.4315% | **3.4579%** | +2.6 bps portfolio yield increase |

### 6.3 Standard-Path Risk Axis Compression and Structural Explanation
On standard Monte Carlo paths, the ES95 risk axis is compressed: the 95th percentile Expected Shortfall is negative (-0.0737% for Cell 149 and -0.0359% for Cell 163), indicating that even in the worst 5% of standard surviving paths, the net return is positive. This occurs because automated health factor unwinds truncate losses before principal impairment can occur.

Furthermore, $P(\text{liquidation}) = 0.00\%$ is structural on standard paths. The fund agent unwinds positions when HF breaches the floor (1.05 or 1.13), well before reaching protocol liquidation at $\text{HF} < 1.00$. Consequently, the recommendation of Cell 149 rests on:
1. The mechanical selection rule ranking.
2. Empirical scenario stress testing evidence (S1 to S12), where liquidation does occur under extreme shocks if parameters are unhedged.
3. Health-factor buffer arithmetic and churn reduction (cutting unwind frequency from 28.27% to 21.64%), rather than an artificial frontier "optimum" claim.

---

## 7. Scenario Stress Testing Results (SPEC §6.4: S1 to S12)

### 7.1 Scenario Table
The performance of the KPK Published Template (Cell 163) versus the Recommended Redesign (Cell 149) across 1,000 paths under simulated shocks is tabulated below (generated from `report/runs.jsonl`):

| ID | Scenario | Historical Source | Injected Shock | Template Median | Template ES95 | Template P(Liq) | Template P(Unwind) | Rec Median | Rec ES95 | Rec P(Liq) | Rec P(Unwind) | Protection Gain (ΔES95) | Two-Minute Takeaway |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **S1** | Flash crash | 10 Oct 2025 cascade | BTC -14% intraday cascade, borrower liquidation cascade, gas 20x for 6h; cascade trough constructed to reach the template's theoretical liquidation boundary | -0.01% | +3.39% | 4.0% | 96.0% | -0.01% | +3.39% | 4.0% | 96.0% | **+0.00%** | Cascade trough forced to liquidation boundary; template incurs 4.0% liquidation rate (+3.39% ES95); redesigned parameters suffer identical 4.0% liquidation rate (+3.39% ES95) as 70.2% LTV matches template initial leverage. |
| **S2** | Sustained crash | Mar 2020 / May 2022 | Multi-day -45% macro drop over 72h | -0.01% | -0.01% | 0.0% | 100.0% | -0.01% | -0.01% | 0.0% | 100.0% | **+0.00%** | Orderly multi-day unwind (P(unwind) 100.0%) preserves capital across both setups; zero liquidations observed over 72h duration. |
| **S3** | Venue freeze (hack) | Binance hack, 29 Oct 2026 | Aave market frozen 12h: no exit, no rebalance (1h/12h/72h sensitivities logged) | -0.46% | -0.03% | 0.0% | 30.0% | -0.48% | -0.06% | 0.0% | 22.4% | **+0.03%** | 12h market freeze causes zero liquidations; lower 1.05 unwind floor reduces unnecessary unwind churn (22.4% vs 30.0%). |
| **S4** | Held-wrapper depeg | Kelp rsETH pattern | Held wrapper trades -5% vs BTC for 24h (cbBTC -5% parameterisation; WBTC for 100:0), arb capital halved (-3%/-5%/-8% sensitivities logged) | +4.99% | +4.99% | 0.0% | 100.0% | +1.49% | +1.49% | 0.0% | 100.0% | **+3.50%** | Held wrapper trades -5% vs BTC for 24h; template (100% WBTC) incurs +4.99% ES95 loss, while 70:30 wrapper diversification limits held-wrapper impairment to +1.49%. |
| **S5** | Collateral-issuer exploit | Resolv, 22 Mar 2026 | Vault collateral asset exploited; 44.25% real loss on destination | +14.53% | +20.59% | 0.0% | 30.0% | +9.44% | +13.86% | 0.0% | 22.6% | **+6.73%** | Deployment split across three vaults (equal thirds) insulates capital, cutting collateral exploit ES95 loss from +20.59% to +13.86% (+6.73% protection gain). |
| **S6** | Opaque counterparty default | Stream Finance / xUSD | Destination defaults with 65% loss, 168h lag (30%/65%/100% sensitivities logged) | +20.52% | +29.72% | 0.0% | 30.0% | +13.52% | +20.11% | 0.0% | 22.6% | **+9.62%** | Deployment split across three vaults (equal thirds) prevents single counterparty insolvency from wiping out deployment, cutting ES95 tail loss from +29.72% to +20.11%. |
| **S7** | Utilisation crunch | EURC crunch on KPK vault | Vault utilisation -> 100% for 48h; withdrawal blocked; borrow past kink | -0.31% | +0.17% | 0.0% | 30.6% | -0.32% | +0.13% | 0.0% | 22.3% | **+0.04%** | Trapped vault liquidity and rate spikes absorbed without liquidation; 1.05 floor reduces unwind churn from 30.6% to 22.3%. |
| **S8** | Oracle failure | Generic tier criteria | Oracle false print -5% for 3h, market price unaffected, then restores | -0.42% | -0.01% | 0.0% | 37.5% | -0.47% | -0.06% | 0.0% | 22.6% | **+0.05%** | 3-hour false -5% oracle print causes zero liquidations; 1.05 floor reduces false-alarm unwinds from 37.5% to 22.6% before oracle restores. |
| **S9** | Stablecoin depeg | USDC SVB Mar 2023 | USDC -13% intraday, full clean recovery in 48h | -0.01% | -0.01% | 0.0% | 100.0% | -0.01% | -0.01% | 0.0% | 100.0% | **+0.00%** | USDC depegs to $0.87, breaching 0.5% threshold and triggering full unwind; debt closed out with deployed USDC without liquidation; zero discounted repayment modelled. |
| **S10** | Negative-carry regime | 2026 compression | Borrow 6.5% > deploy 2.2% for 30 days (720h) | -0.00% | -0.00% | 0.0% | 100.0% | -0.00% | -0.00% | 0.0% | 100.0% | **+0.00%** | Negative carry triggers 100% unwind, cutting debt interest accrual and safely parking capital in T-bills. |
| **S11** | Gas war | Historical crisis gas | Gas 25x for 12h, transaction failure rate 35% (10x/25x/50x logged) | -0.46% | -0.03% | 0.0% | 30.0% | -0.48% | -0.06% | 0.0% | 22.5% | **+0.03%** | 25x gas spike and 35% tx failures cause zero insolvencies; lower unwind floor limits failed rebalance transaction costs. |
| **S12** | Correlated failure | Composite (S1+S3+S4) | Intraday crash (-14%) during freeze (12h) with held-wrapper depeg (-5%) | +8.56% | +9.00% | 65.5% | 34.5% | +1.49% | +5.65% | 10.8% | 89.2% | **+3.35%** | Simultaneous crash, freeze, and wrapper depeg causes 65.5% liquidations in template; redesigned controls reduce liquidations to 10.8% and cut ES95 loss to +5.65%. |

### 7.2 Key Risk Observations
1. **Unwind Floor Discipline (1.13 vs 1.05):** Template cell 163 triggers full unwind at HF 1.13, causing high false-alarm churn (30% to 37% in S3, S7, S8, S11). Lowering the floor to 1.05 cuts churn to ~22% while keeping P(liquidation)=0 on standard paths.
2. **Destination Slicing (Equal Thirds):** In uncapped setups (S5, S6), default in a single Morpho vault exposes 50% of deployed capital. Slicing deposits equally across three destination vaults (loss share 1/3) cuts exploit and default tail loss by 6.7% to 9.6%.
3. **Wrapper Diversification:** Allocating collateral across wrappers (70% WBTC / 30% cbBTC) ensures a single custodian freeze or depeg does not impair the entire collateral base (S4 held-wrapper depeg tail loss cut from +4.99% to +1.49%).
4. **Execution During Severe Cascades (S1, S12):** In S1 (where cascade trough reaches the 17.41% liquidation boundary), both setups experience a 4.0% liquidation rate due to identical 70.2% initial LTV. Under composite compound stress (S12: crash + freeze + depeg), redesigned controls reduce liquidations from 65.5% to 10.8% and reduce ES95 tail loss from +9.00% to +5.65%.

---

## 8. Portfolio Risk Architecture and Counterparty Exposure (SPEC §13.1)

### 8.1 Concentration Metrics and Herfindahl-Hirschman Index (HHI)
All exposures are normalized on **ONE unified book definition** ($50M multi-strategy fund equity: 45% Carry [$22.5M, 70:30 WBTC:cbBTC], 15% AMM [$7.5M], 40% T-Bill [$20.0M]):

```
+---------------------------------------------------------------------------------------+
|                             PORTFOLIO DIVERSIFICATION METRICS                         |
+--------------------------+---------------------+-------------------+------------------+
| Dimension                | Template (Cell 163) | Redesign (Cell 149)| Structural Shift |
+--------------------------+---------------------+-------------------+------------------+
| Carry Collateral HHI     | 1.000 (100% WBTC)   | 0.580 (70:30 WBTC)| -42.0% Risk Cut  |
| Fund Equity Asset HHI    | 0.385               | 0.300             | Diversified Coll |
| Fund Equity Issuer HHI   | 0.385               | 0.300             | Multi-Custodian  |
| Fund Equity Venue HHI    | 0.385               | 0.385             | Aave/UST/Uniswap |
+--------------------------+---------------------+-------------------+------------------+
```

Exposures on Total Fund Equity ($50,000,000 USD):
- **Asset Shares:** WBTC 31.5% ($15.75M), cbBTC 13.5% ($6.75M), USD T-Bills 40.0% ($20.0M), USDC AMM 15.0% ($7.5M). Sum = 100.0%.
  $$\text{Asset HHI} = 0.315^2 + 0.135^2 + 0.40^2 + 0.15^2 = 0.099225 + 0.018225 + 0.1600 + 0.0225 = 0.29995 \approx 0.300$$
- **Issuer Shares:** BitGo 31.5% ($15.75M), Coinbase 13.5% ($6.75M), US Sovereign 40.0% ($20.0M), Uniswap Smart Contracts 15.0% ($7.5M). Sum = 100.0%.
  $$\text{Issuer HHI} = 0.29995 \approx 0.300$$
- **Venue Shares:** Aave v3 Core 45.0% ($22.5M), US Treasury 40.0% ($20.0M), Uniswap v3 15.0% ($7.5M). Sum = 100.0%.
  $$\text{Venue HHI} = 0.45^2 + 0.40^2 + 0.15^2 = 0.2025 + 0.1600 + 0.0225 = 0.3850$$

### 8.2 Named Counterparty Exposure and Loss-Given-Default Roster
Evaluated at the $50,000,000 USD design scale. Loss-Given-Default (LGD) values are explicitly labelled as assumed model parameters:

| Counterparty | Category | Exposure ($) | Share (%) | Exposure Cap ($) | Assumed LGD | Stress Vulnerability |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Aave v3 Core** | Lending Protocol | $22,500,000 | 45.0% | $35,000,000 | 0.80 | Primary borrow liquidity; IRM slope2 utilisation spike. |
| **Morpho Curated Vaults**| Lending Vaults | $15,795,000 | 31.6% | $25,000,000 | 0.85 | Deployed borrowed USDC; depositor runs drive util to 100%. |
| **US Treasury (DTB3)** | Sovereign Cash | $20,000,000 | 40.0% | $50,000,000 | 0.00 | Sovereign risk-free assumption; redemption margin buffer. |
| **BitGo (WBTC)** | Custodian | $15,750,000 | 31.5% | $30,000,000 | 1.00 | Custodial bridge freeze or legal entity insolvency. |
| **Coinbase (cbBTC)** | Asset Issuer | $6,750,000 | 13.5% | $20,000,000 | 1.00 | Exchange blacklisting or centralized redemption freeze. |
| **Uniswap v3 Pools** | AMM Protocol | $7,500,000 | 15.0% | $10,000,000 | 0.90 | LVR and market impact during cascade volatility. |
| **Gearbox Protocol** | Credit Accounts | $0 | 0.0% | $10,000,000 | 0.90 | Capacity constrained. Excluded as primary. |
| **Balancer** | Wind-Down Protocol | **EXCLUDED** | **0.0%** | **$0** | 1.00 | **BIP-928 wind-down; withdrawal-only mode.** |

*Note on exposure aggregation:* The roster rows are overlapping views of the same capital and must not be summed (they reflect the same assets across collateral custodians, lending venues, deployment destinations, and cash reserves).

### 8.3 Empirically Computed Correlations
Correlations between carry sleeve net BTC yield and Bitcoin price returns are logged from the `portfolio_correlations_cell_149` record (seed 42, input_vectors_hash `84dfefe484ed9617`, evaluated across 10,000 Historical Block Bootstrap paths for Cell 149):
- **Full Sample Correlation (`rho_full`):** -0.3788
- **Calm Paths (`rho_calm`, BTC path return $\ge -5\%$):** -0.4092
- **Crisis Paths (`rho_crisis`, BTC path return $< -10\%$):** -0.0207

In calm regimes (calm: BTC path return $\ge -5\%$), carry net yield in BTC terms displays mild negative correlation to BTC price (-0.4092) because dollar-denominated interest payments represent a smaller fraction of collateral when Bitcoin price appreciates. In crisis regimes (crisis: BTC path return $< -10\%$), the correlation drops to near zero (-0.0207) as automated health factor unwinds execute, closing out debt and truncating exposure.

---

## 9. Benchmarks (§6.6)

Two external benchmarks were evaluated under identical Historical Block Bootstrap paths (10,000 paths, 2,160 hours):

| Strategy / Benchmark | Target LTV | Median Net APY | ES95 Loss % | P(Unwind) | P(Liquidation) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Hold-BTC Benchmark** | 0.0% | 0.0000% | 0.0000% | 0.00% | 0.00% |
| **Sentora-Style Single-Cycle Carry** | 50.0% | 1.4487% | -0.0534% | 21.86% | 0.00% |
| **KPK Template (`cell_163`)** | 70.2% | 1.8843% | -0.0359% | 28.27% | 0.00% |
| **Recommended Redesign (`cell_149`)** | 70.2% | **1.9373%** | **-0.0737%** | **21.64%** | **0.00%** |

- **Hold-BTC:** Definitional zero under BTC-denominated accounting (yield 0, risk 0), not a simulation, serving as the passive reference line.
- **Sentora Single-Cycle:** Operates at conservative 50% LTV without multi-cycle looping. This benchmark is a conservative 50%-LTV proxy run in the same carry machinery, not a model of Sentora's supervised-loan structure. While safe (zero liquidation), it produces 1.45% carry APY, trailing the Recommended Redesign by 49 bps.
- **Recommended Redesign:** Produces 1.94% carry APY and lowers unwind churn to 21.64% (vs 21.86% for the Sentora proxy and 28.27% for the template).

---

## 10. Declared Limitations (SPEC §7)

1. **Simulation Horizon and Reaction Latency:** Monte Carlo paths evaluate dynamics at 1-hour resolution. While KPK's live agent monitors health factors at approximately 12-second block intervals, extreme sub-hourly cascades can produce brief execution slippage.
2. **Structural Liquidation Absence on Standard Paths:** On standard unshocked paths, $P(\text{liquidation}) = 0.00\%$ because the unwind floor pre-empts liquidation. Tail risk differentiation is observable primarily under injected scenario stress (S1 to S12).
3. **Scenario Headline Severities:** Scenario table figures reflect middle headline severities (e.g. S3 at 12h freeze; S4 at -5% depeg; S6 at 65% default). Multi-severity sensitivity variants (1h/72h for S3; -3%/-8% for S4; 30%/100% for S6; 10x/50x for S11) are logged separately in `report/runs.jsonl`.
4. **Oracle Model Limits:** Oracle failure scenarios model fixed basis misprints and staleness. Extreme multi-block oracle manipulation during protocol-level exploits is partially unobservable on historical data.
5. **Non-Simulated AMM Engine:** The AMM sleeve yield (6.00%) and funding rate proxy (2.20%) are parameterised based on empirical data rather than simulated through dedicated microstructure sub-engines.
6. **Fat Tails and Model Scope:** Fat tails are modeled, not eliminated. Results represent quantitative decision support, not an absolute solvency guarantee.

---

## 11. Construction and Rebalancing Instructions (JD Line 4)

Instructions for portfolio execution sized to the 45% carry sleeve of the $50,000,000 USD fund ($22.5M total collateral equity):

1. **Collateral Sizing:** Supply 70% WBTC ($15.75M = 157.5 BTC at $100k BTC design price) and 30% cbBTC ($6.75M = 67.5 BTC) to Aave v3 Core Ethereum.
2. **Borrowing Parameters:** Borrow USDC at **70.2% Target LTV** ($15,795,000 USDC borrowed against $22.5M collateral equity), targeting an initial Health Factor of **1.210**.
3. **Deployment Routing:** Allocate the borrowed $15.795M USDC split equally across the three named destination vaults (`kpk_usdc_yield_vault`, `kpk_usdc_yield_rwa_vault`, `yo_ecosystem_yousd`), $5.265M each at design scale, modelled as the 25% cap parameter setting; no live vault TVLs are used.
4. **Automated Deleveraging Band:** If HF declines into the band `[1.08, 1.15)`, initiate automated partial repayment via flash unwind to restore HF to 1.21.
5. **Full Unwind Floor Trigger:** If HF breaches **1.05**, execute immediate full clean unwind, repaying outstanding USDC debt and returning BTC collateral to reserve.
6. **Emergency Circuit Breakers:** Trigger full emergency unwind upon:
   - BTC 24h price drop $\ge 10\%$.
   - Wrapper peg deviation $\ge 1.0\%$ sustained for $\ge 40$ minutes.
   - USDC peg deviation $\ge 0.5\%$.
   - Negative carry spread for two consecutive 6-hour windows.
   - Venue reserve freeze or borrow suspension signal.
