# KPK BTC Carry Fund — Institutional Risk & Strategy Factsheet
**Scale:** $50M Design Scale (Parameterised from 13.4 WBTC Live Reference to $320M Benchmark)  
**Date:** November 2026 | **Version:** 1.0  
**Engine:** Gauntlet-Style Agent-Based Monte Carlo Engine (10,000 paths × 90 days hourly)  
**Commit Traceability:** Logged bit-for-bit to `report/runs.jsonl`

---

## 1. Executive Summary & Strategy Overview
The **KPK BTC Carry Fund** is an institutional market-neutral yield strategy designed to extract onchain yield on Bitcoin collateral without taking directional market risk. The fund supplies wrapped Bitcoin (`WBTC`, `cbBTC`) to prime lending venues (`Aave v3 Core`, `Aave v4 Bluechip`, `Morpho Markets`), borrows `USDC`, and deploys the borrowed liquidity into curated lending vaults (`KPK USDC Yield Vault`, `KPK USDC Yield RWA Vault`, `Yo USD`).

This factsheet summarizes the results of a comprehensive 360-cell Monte Carlo parameter sweep across 10,000 paths, stress testing the published KPK template against historical cascade replays (10 Oct 2025, 29 Oct 2026 Binance freeze) and institutional risk redesigns.

| Metric | Published KPK Template | Institutional Recommended Setting | Delta / Benefit |
| :--- | :--- | :--- | :--- |
| **Target LTV** | **70.2%** | **60.0%** | **−10.2%** (Equity buffer expands from 17.4% to 29.4%) |
| **Initial Health Factor** | 1.210 | 1.417 | **+17.1%** safety cushion above liquidation |
| **HF Full Unwind Floor** | 1.130 | 1.100 | Prevents premature panic unwinds |
| **Collateral Sleeve Split** | 100% WBTC : 0% cbBTC | **50% WBTC : 50% cbBTC** | Wrapper Issuer HHI drops from 1.000 to **0.500** |
| **Deployment Cap per Vault**| None (Uncapped) | **25% of Vault TVL** | Eliminates single-vault default ruin (>75% loss reduction) |
| **Policy Engine** | Static KPK Published Triggers | **KPK + Volatility-Scaled LTV** | Auto-trims debt when 7d realised vol >65% |
| **Carry Sleeve Net BTC APY**| **1.88%** | **1.70%** | Yield sacrifice of only **18 bps** for massive tail protection |
| **P(Policy Unwind)** | **28.27%** | **21.65%** | **−23.4%** reduction in execution turnover/fees |
| **Min Health Factor (p5)** | **1.124** (breaches 1.13) | **1.140** (comfortably holds) | Zero breach of the 1.10 unwind floor |
| **P(Liquidation)** | 0.00% | 0.00% | Zero liquidations across all standard paths |
| **Portfolio Net APY (Fund)** | **3.39%** | **3.31%** | Negligible 8 bps impact on multi-strategy book |

---

## 2. Multi-Strategy Sleeve Allocation (§11 Prior vs Frontier)
The fund implements a 4-sleeve multi-strategy structure with a binding 20% liquid T-bill floor to support redemptions and margin buffers:

```
+-----------------------------------------------------------------------------------+
|                           TOTAL FUND PORTFOLIO ($50M)                             |
+------------------------------------+------------------+-------------+-------------+
|        Lending Carry Sleeve        |   Basis Sleeve   | AMM Sleeve  |   T-Bills   |
|         45.0% ($22.5M)             |  20.0% ($10.0M)  | 15% ($7.5M) | 20% ($10M)  |
|  50:50 WBTC:cbBTC -> Aave v3 Core  |  [GATE ACTIVE]   | Uniswap v3  | FRED DTB3   |
|     60% LTV -> Morpho Vaults       | Funding (2.2%) < | WBTC/USDC   | 4.03% Yield |
|     1.70% Net BTC APY (Median)     | T-Bill (4.03%)   | Fee Yield   | Liquid Cash |
|                                    | -> Parks in Cash | 6.00% APY   | T+1 Buffer  |
+------------------------------------+------------------+-------------+-------------+
```

- **Basis Sleeve Gate:** In 2026, perpetual funding rates compressed to 2.2% annualised, falling below the 3-month US Treasury Bill rate (4.03% DTB3). When the basis gate is active, capital is redirected into risk-free 4.03% T-bills, expanding effective cash liquidity from 20% to **40%** and preventing yield drag.
- **AMM Sleeve Capacity:** Single-pool depth caps institutional AMM deployment at single-digit millions (~$7.5M / 15%). Larger weights create prohibitive market impact and LVR.

---

## 3. Efficient Frontier Summary (SPEC §6.5)
Across 360 parameter combinations evaluated on 10,000 paths (Historical Block Bootstrap) and 5,000 paths (Jump Diffusion & Regime Switching), the recommended configuration occupies the optimal "kink" on the frontier:

![Efficient Frontier](report/frontier.png)

- **The Convexity Trade-off:** Increasing target LTV from 60.0% to 70.2% increases net carry yield by only +18 bps (1.70% to 1.88%), but shrinks the distance to liquidation by **40.8%** (from 29.4% down to 17.4%).
- **Policy Scaling Advantage:** Volatility-scaled LTV shifts the entire frontier upwards and to the left by preventing liquidations during market turbulence without penalizing calm-regime returns.

---

## 4. Scenario Stress Testing Headlines (SPEC §6.4: S1–S12)
Tail loss evaluated under 1,000 paths with deterministic historical event shocks:

| Scenario | Historical Source | Template ES95 Loss | Rec ES95 Loss | Protection Gain | Core Mechanism & Headline |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **S1 Flash Crash** | 10 Oct 2025 Cascade | 1.84% | **-0.01%** | **+1.85%** | 60% LTV has 29.4% buffer; absorbs -14% crash without liquidations. |
| **S2 Sustained Crash** | Mar 2020 / LUNA 2022 | 8.41% | **3.87%** | **+4.54%** | Vol-scaled LTV trims debt before -45% macro plunge bottoms out. |
| **S3 Venue Freeze** | Binance Hack 29 Oct 2026 | 2.14% | **-0.01%** | **+2.15%** | 12h freeze safely absorbed by 1.417 HF cushion; zero insolvency. |
| **S4 Wrapper Depeg** | Kelp rsETH Pattern | 4.97% | **2.45%** | **+2.52%** | 50:50 WBTC:cbBTC split strictly halves single-wrapper depeg shock. |
| **S5 Vault Exploit** | Resolv (22 Mar 2026) | 15.50% | **3.63%** | **+11.87%** | 25% vault cap prevents single-vault exploit from consuming portfolio. |
| **S6 Opaque Default** | Stream Finance / xUSD | 22.79% | **5.37%** | **+17.42%** | 65% lagged vault default strictly capped at single-vault deposit cap. |
| **S7 Utilisation Crunch** | EURC Crunch (KPK Vault) | 3.62% | **0.77%** | **+2.85%** | 20% liquid T-bill buffer services debt without forced collateral fire-sales. |
| **S8 Oracle Failure** | Generic Tier Criteria | 4.11% | **-0.01%** | **+4.12%** | Conservative HF buffer prevents false -5% prints from triggering unwinds. |
| **S9 Stablecoin Depeg** | USDC SVB (Mar 2023) | -0.03% | **-0.05%** | **+0.02%** | 0.5% peg trigger unwinds cleanly; debt repaid whole at par/discount. |
| **S10 Negative Carry** | 2026 Compression | 0.92% | **-0.05%** | **+0.97%** | Basis gate auto-switches to 4.03% T-bills, eliminating funding bleed. |
| **S11 Gas War** | Historical Crisis Gas | 2.42% | **0.35%** | **+2.07%** | 29.4% cushion tolerates 12h transaction delays & 35% failure rates. |
| **S12 Correlated Shock**| Composite (S1+S3+S4) | **26.39%** | **3.14%** | **+23.25%** | **Template suffers catastrophic liquidation; Rec book survives whole.** |

---

## 5. Construction & Rebalancing Instructions (JD Line 4)
Institutional execution instructions for portfolio managers and automated execution bots:

1. **Collateral Sizing:** Deposit 50% WBTC and 50% cbBTC (250 BTC each at $100k BTC design price = $50M total). Verify custodian multisig thresholds and contract ownership before entry.
2. **Borrowing Parameters:** Borrow USDC on Aave v3 Core Ethereum at **60.0% Target LTV** (HF 1.417). Max initial borrowing is $30.0M USDC.
3. **Deployment Vault Routing:** Allocate borrowed USDC across approved Morpho vaults (`kpk_usdc_yield_vault`, `kpk_usdc_yield_rwa_vault`, `yo_ecosystem_yousd`), enforcing a **strict 25% vault TVL cap** ($12.5M max per $50M vault). Route any overflow liquidity to liquid FRED DTB3 T-bills.
4. **Automated Deleveraging Band:** If HF dips into `[1.10, 1.25)`, execute automated partial deleveraging via Flash Repay to restore HF to 1.417.
5. **Full Unwind Trigger Floor:** If HF breaches **1.10**, execute immediate full clean unwind.
6. **Dynamic Volatility Scaling:** If 7-day annualised realised volatility exceeds **65%**, automatically scale target LTV down towards 50.0% (`scale = min(1.0, 0.65 / vol_7d)`).
7. **Emergency Unwind Triggers:** Execute immediate full unwind upon: (i) BTC -10%/24h; (ii) wrapper peg deviation $\ge 1.0\%$ sustained $\ge 40$ min; (iii) USDC depeg $\ge 0.5\%$; (iv) negative carry for two consecutive 6h windows; (v) venue freeze signal.
