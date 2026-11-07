# KPK BTC Carry Fund: Institutional Risk and Strategy Factsheet

**Scale:** $50,000,000 USD Institutional Design Scale  
**Date:** 6 November 2026  
**Author:** Chinmay Gupta  
**Engine:** Agent-Based Monte Carlo Engine (10,000 paths x 90 days hourly)  
**Traceability:** Fully reproducible; logged to `report/runs.jsonl` (grid runs stamped `7b809d8b`, downstream runs stamped `520262df`)

---

## 1. Strategy Overview and Selected Configuration

The **KPK BTC Carry Fund** is an institutional yield strategy designed to harvest lending yield spreads on Bitcoin collateral. While sovereign cash and basis sleeves are insulated, the carry sleeve carries BTC-downside directional exposure during severe price cascades and automated deleveraging events. The fund supplies wrapped Bitcoin (`WBTC`, `cbBTC`) to prime lending venues (`Aave v3 Core`), borrows `USDC` up to a disciplined Loan-to-Value, and deploys the borrowed liquidity into curated lending vaults (`kpk_usdc_yield_vault`, `kpk_usdc_yield_rwa_vault`, `yo_ecosystem_yousd`).

The objective mechanical selection rule (filter $P(\text{liquidation})=0$ and 5th percentile minimum health factor $\ge 1.10$, ranking by carry ES95 loss ascending then median net APY descending) selected **Cell 149**:

| Parameter / Metric | Published Template (Cell 163) | Recommended Setting (Cell 149) | Structural Shift and Rationale |
| :--- | :--- | :--- | :--- |
| **Target LTV** | **70.2%** | **70.2%** | Leverage retained; distance to liquidation is 17.41% |
| **HF Full Unwind Floor** | **1.13** | **1.05** | Prevents false-alarm unwinds during transient market dips |
| **Collateral Split** | 100% WBTC : 0% cbBTC | **70% WBTC : 30% cbBTC** | Diversifies custodian and wrapper bridge exposure |
| **Deployment Split** | None (Uncapped, 1/2 split) | **Three Vaults (Equal Thirds)** | Modelled under the 25% cap parameter; cuts default ruin by 6.7% to 9.6% |
| **Execution Policy** | `kpk_published` | **`kpk_published`** | Standard published policy retained |
| **Carry Sleeve Net BTC APY**| 1.8843% | **1.9373%** | +5.3 bps yield increase from reduced unwind churn |
| **P(Policy Unwind)** | 28.27% | **21.64%** | -6.63 percentage points reduction in execution turnover/fees |
| **Min Health Factor (p5)** | 1.1241 (breaches 1.13) | **1.1240** (holds > 1.05) | Holds comfortably above the 1.05 floor |
| **P(Liquidation)** | 0.00% | **0.00%** | Zero liquidation on standard paths (pre-empted by unwind floor) |
| **Portfolio Net APY** | 3.3948% | **3.4189%** | +2.4 bps portfolio yield increase |

---

## 2. Three-Panel Results in Miniature

Cross-generator results are reported independently across all three simulation panels (unblended):

| Panel (Path Count) | Configuration | Carry Net APY | Carry ES95 Loss % | P(Unwind) | Min-HF (p5) | Portfolio Net APY |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Historical Bootstrap** (10,000 paths) | Template (Cell 163) | 1.8843% | -0.0359% | 28.27% | 1.1241 | 3.3948% |
| | **Recommended (Cell 149)** | **1.9373%** | **-0.0737%** | **21.64%** | **1.1240** | **3.4189%** |
| **Jump Diffusion** (5,000 paths) | Template (Cell 163) | 2.1417% | -0.0602% | 18.00% | 1.1224 | 3.5119% |
| | **Recommended (Cell 149)** | **2.2187%** | **-0.2015%** | **7.80%** | **1.1222** | **3.5469%** |
| **Regime-Switching** (5,000 paths) | Template (Cell 163) | 1.9650% | -0.0335% | 35.74% | 1.1242 | 3.4315% |
| | **Recommended (Cell 149)** | **2.0230%** | **-0.0521%** | **30.44%** | **1.1242** | **3.4579%** |

*Note on standard paths:* The ES95 risk axis is compressed (negative loss indicates net positive returns) because automated health factor unwinds truncate losses before principal impairment occurs. Similarly, $P(\text{liquidation})=0.00\%$ is structural on standard paths because the fund agent unwinds when HF falls below 1.05, well before liquidation at $\text{HF} < 1.00$.

---

## 3. Multi-Strategy Sleeve Allocation and Assumptions Summary

The fund operates a 4-sleeve structure sized to $50,000,000 USD total equity:

```
+-----------------------------------------------------------------------------------+
|                           TOTAL FUND PORTFOLIO ($50M)                             |
+------------------------------------+------------------+-------------+-------------+
|        Lending Carry Sleeve        |   Basis Sleeve   | AMM Sleeve  |   T-Bills   |
|         45.0% ($22.5M)             |  20.0% ($10.0M)  | 15% ($7.5M) | 20% ($10M)  |
|  70:30 WBTC:cbBTC -> Aave v3 Core  |  [GATE ACTIVE]   | Uniswap v3  | FRED DTB3   |
|   70.2% LTV -> Morpho Vaults       | Funding (2.2%) < | WBTC/USDC   | 4.03% Yield |
|     1.94% Net BTC APY (Simulated)  | T-Bill (4.03%)   | Fee Yield   | Liquid Cash |
|     BTC Downside Directional Risk  | -> Parks in Cash | 6.00% APY   | T+1 Buffer  |
+------------------------------------+------------------+-------------+-------------+
```

### Sleeve Assumptions Table
- **T-Bill Reserve (40.0% Active / $20.0M):** 4.03% APY. Source: FRED series `DTB3` (`data/raw/dtb3.csv`), secondary market rate dated 3 Nov 2026. 2023 to 2026 range: 3.52% to 5.36% (mean 4.49%).
- **Basis Sleeve (0.0% Active / Gated to T-Bills):** 2.20% APY. Source: Observed 2026 Ethena/CeFi perpetual funding rate compression, labelled as an empirical proxy. The automated gate redirects capital to 4.03% T-bills when funding spread falls below risk-free rates.
- **AMM Sleeve (15.0% / $7.5M):** 6.00% APY. Assumed parameter; dedicated AMM simulation sub-engine remains future work.
- **Carry Sleeve (45.0% / $22.5M):** 1.9373% net BTC APY. Simulated output from Cell 149 under Historical Block Bootstrap.

---

## 4. Scenario Stress Testing Headlines (SPEC §6.4: S1 to S12)

Evaluated across 1,000 paths with deterministic historical event shocks:

| ID | Scenario | Template ES95 | Rec ES95 | Protection Gain | Computed P(Liq) [T vs R] | Two-Minute Takeaway |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **S1** | Flash crash (-14% intraday) | +3.39% | **+3.39%** | **+0.00%** | 4.0% vs 4.0% | Cascade trough reaches liquidation boundary; 70.2% LTV yields identical 4.0% liquidation rate across both configurations. |
| **S2** | Sustained crash (-45% 72h) | -0.01% | **-0.01%** | **0.00%** | 0.0% vs 0.0% | Orderly multi-day unwind (P(unwind) 100%) preserves capital across both setups with zero liquidations. |
| **S3** | Venue freeze (12h Aave freeze)| -0.03% | **-0.06%** | **+0.03%** | 0.0% vs 0.0% | Zero liquidations; lower 1.05 unwind floor cuts unnecessary unwind churn from 30.0% to 22.4%. |
| **S4** | Held-wrapper depeg (-5% cbBTC)| +4.99% | **+1.49%** | **+3.50%** | 0.0% vs 0.0% | Held-wrapper depeg (-5% cbBTC) causes +4.99% ES95 loss in 100% WBTC book; 70:30 wrapper split caps impairment to +1.49%. |
| **S5** | Vault exploit (44.25% Resolv) | +20.59% | **+13.86%** | **+6.73%** | 0.0% vs 0.0% | Deployment split across three vaults (equal thirds) cuts collateral exploit tail loss from +20.59% to +13.86%. |
| **S6** | Counterparty default (65% xUSD)| +29.72% | **+20.11%** | **+9.62%** | 0.0% vs 0.0% | Equal thirds deployment split insulates against destination insolvency, reducing ES95 tail loss from +29.72% to +20.11%. |
| **S7** | Utilisation crunch (100% util) | +0.17% | **+0.13%** | **+0.04%** | 0.0% vs 0.0% | Trapped vault liquidity and rate spikes absorbed without liquidation; 1.05 floor cuts churn to 22.3%. |
| **S8** | Oracle failure (-5% print 3h) | -0.01% | **-0.06%** | **+0.05%** | 0.0% vs 0.0% | 3-hour false -5% print causes zero liquidations; 1.05 floor reduces false-alarm unwinds from 37.5% to 22.6%. |
| **S9** | Stablecoin depeg (-13% USDC) | -0.01% | **-0.01%** | **0.00%** | 0.0% vs 0.0% | USDC depeg triggers full unwind; debt closed out with deployed USDC without liquidation; zero discounted repayment modelled. |
| **S10** | Negative carry (30-day inversion)| -0.00% | **-0.00%** | **0.00%** | 0.0% vs 0.0% | Negative spread triggers 100% unwind, cutting debt interest accrual and parking capital in T-bills. |
| **S11** | Gas war (25x gas, 35% tx fail) | -0.03% | **-0.06%** | **+0.03%** | 0.0% vs 0.0% | High gas and 35% tx failures cause zero insolvencies; lower unwind floor limits failed rebalance gas costs. |
| **S12** | Correlated shock (S1+S3+S4) | +9.00% | **+5.65%** | **+3.35%** | 65.5% vs 10.8% | Compound shock liquidates 65.5% of template paths; redesigned controls cut liquidations to 10.8% and ES95 loss to +5.65%. |

---

## 5. Construction and Rebalancing Summary

Parameters sized to the 45% carry sleeve of the $50,000,000 USD fund ($22.5M collateral equity):

1. **Collateral Sizing:** Supply 70% WBTC ($15.75M = 157.5 BTC at $100k BTC design price) and 30% cbBTC ($6.75M = 67.5 BTC) to Aave v3 Core Ethereum.
2. **Borrowing Parameters:** Borrow USDC at **70.2% Target LTV** ($15,795,000 USDC borrowed against $22.5M collateral equity), targeting an initial Health Factor of **1.210**.
3. **Deployment Routing:** Allocate the borrowed $15.795M USDC across approved Morpho vaults (`kpk_usdc_yield_vault`, `kpk_usdc_yield_rwa_vault`, `yo_ecosystem_yousd`), enforcing an **equal deployment split across three vaults (equal thirds, $5.265M per vault)**, modelled as the 25% cap parameter setting.
4. **Automated Deleveraging Band:** If HF declines into the band `[1.08, 1.15)`, initiate automated partial repayment via flash unwind to restore HF to 1.21.
5. **Full Unwind Floor Trigger:** If HF breaches **1.05**, execute immediate full clean unwind, repaying outstanding USDC debt and returning BTC collateral to reserve.

---

## 6. Limitations in Brief (SPEC §7)

- **Hourly Simulation Resolution:** Evaluated at 1-hour time-steps versus live agent reactions of approximately 12 seconds. Sub-hourly volatility spikes could introduce execution slippage.
- **Structural Liquidation Absence on Standard Paths:** On unshocked paths, $P(\text{liquidation})=0.00\%$ because the unwind floor pre-empts liquidation. Tail risk differentiation is observable under injected scenario shocks.
- **Scenario Severities:** Scenario table figures represent middle headline severities; multi-severity variants are logged in `report/runs.jsonl`.
- **Partly Assumed Yields:** AMM sleeve yield (6.00%) and basis funding proxy (2.20%) are based on empirical parameters rather than dedicated microstructure simulation sub-engines.
- **Decision Support:** Models fat tails without eliminating tail risk. Outputs serve as quantitative risk guidance rather than an absolute solvency guarantee.
