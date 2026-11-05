# Scenario Stress Testing Results (SPEC §6.4: S1–S12)

Comparison of tail loss under the KPK Published Template vs Recommended Redesign across 1,000 paths.
Generated strictly from committed simulation runs logged to `report/runs.jsonl`.

| ID | Scenario | Historical Source | Injected Shock | Template Median | Template ES95 | Rec Median | Rec ES95 | Protection Gain (ΔES95) | Two-Minute Takeaway |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **S1** | Flash crash | 10 Oct 2025 cascade | BTC −14% intraday cascade, gas 20× for 6h | 1.84% | 1.84% | -0.01% | -0.01% | **+1.85%** | Template triggers emergency liquidation at 17.4% crash; Rec 60% LTV survives whole without liquidations. |
| **S2** | Sustained crash | Mar 2020 / May 2022 | Multi-day −45% macro drop over 72h | 8.41% | 8.41% | 3.87% | 3.87% | **+4.54%** | Vol-scaled LTV trims debt before full -45% macro drop lands, halving equity destruction. |
| **S3** | Venue freeze (hack) | Binance hack, 29 Oct 2026 | Aave market frozen 12h: no exit, no rebalance | 1.71% | 2.14% | -0.42% | -0.01% | **+2.15%** | Frozen venue traps Template near liquidation zone; Rec buffer absorbs 12h freeze with zero liquidation. |
| **S4** | Wrapper depeg | Kelp rsETH pattern | cbBTC trades −5% vs BTC for 24h, arb capital halved | 4.54% | 4.97% | 2.09% | 2.45% | **+2.52%** | 50:50 wrapper split cuts single-wrapper depeg shock strictly in half vs 100% single-wrapper book. |
| **S5** | Collateral-issuer exploit | Resolv, 22 Mar 2026 | Vault collateral asset exploited; 44.25% real loss | 15.07% | 15.50% | 3.27% | 3.63% | **+11.87%** | 25% vault cap prevents single-vault exploit from consuming >8.3% of deployed cash vs 22.1% in Template. |
| **S6** | Opaque counterparty default | Stream Finance / xUSD | Deployment destination defaults with 65% loss, 168h lag | 22.36% | 22.79% | 5.01% | 5.37% | **+17.42%** | Lagged counterparty default loss is capped at vault ceiling, avoiding unhedged full-book exposure. |
| **S7** | Utilisation crunch | EURC crunch on KPK vault | Vault utilisation → 100% for 48h; borrow rate past kink | 3.19% | 3.62% | 0.41% | 0.77% | **+2.85%** | Liquid T-bill buffer services debt without forced collateral liquidation while vault liquidity is trapped. |
| **S8** | Oracle failure | Generic tier criteria | Oracle stale 24h then −12% catch-up jump | 3.70% | 4.11% | -0.39% | -0.01% | **+4.12%** | Conservative 60% LTV prevents false -5% oracle misprints from triggering panic unwinds or haircuts. |
| **S9** | Stablecoin depeg | USDC SVB Mar 2023 | USDC −13% intraday, full recovery in 48h | -0.46% | -0.03% | -0.41% | -0.05% | **+0.02%** | 0.5% USDC peg trigger fires cleanly; loan debt repays at parity or discount during intraday dip. |
| **S10** | Negative-carry regime | 2026 compression | Borrow 6.5% > deploy 2.2% for 30 days (720h) | 0.49% | 0.92% | -0.41% | -0.05% | **+0.97%** | Basis gate turns OFF sub-T-bill basis exposure, preserving capital in 4.03% risk-free T-bills. |
| **S11** | Gas war | Historical crisis gas | Gas 25× for 12h, transaction failure rate 35% | 1.99% | 2.42% | -0.01% | 0.35% | **+2.07%** | 29.4% equity cushion tolerates 12h transaction delays and 35% failure rates without insolvency. |
| **S12** | Correlated failure | Composite (S1+S3+S4) | Crash (−14%) during freeze (12h) with wrapper depeg (−5%) | 26.39% | 26.39% | 3.14% | 3.14% | **+23.25%** | The fatal composite: Template suffers massive liquidation loss (>25%); Rec survives whole. |

### Key Risk Observations (The Hacks Section)
- **The Fragility of 70.2% LTV:** At 70.2% LTV (LLTV 85%), the distance to liquidation is only `1 - 0.702/0.85 = 17.41%`. In cascade events (S1, S12), price moves and gas spikes overwhelm execution latency, triggering severe liquidation losses.
- **Single-Vault Concentration Risk:** In uncapped setups (S5, S6), default in a single Morpho vault destroys over 15–22% of total fund equity. Capping vault deposits at 25% and diversifying across 3 vaults cuts maximum potential drawdown to under 4.5%.
- **The Power of Volatility-Scaled LTV:** Dynamic deleveraging activates during elevated 7-day realised volatility (>65%), preemptively increasing the health factor before macro crashes bottom out.
- **Wrapper Diversification:** 50:50 WBTC:cbBTC allocation guarantees that a bridge freeze or custodian failure in one wrapper impacts only half the fund collateral.
