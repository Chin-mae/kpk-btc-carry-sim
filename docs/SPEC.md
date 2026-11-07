# SPEC — Gauntlet-style Monte Carlo risk engine for a multi-strategy BTC fund at institutional scale
Version 0.3, 5 Nov 2026 (v0.2 adds §11–§14: allocation provenance, JD coverage matrix, portfolio risk metrics, protocol coverage, AMM sub-engine; v0.3 removes Balancer as a live venue throughout — DAO-approved wind-down BIP-928, pools withdrawal-only from 30 Oct 2026, v3 Vault pauses 30 Nov 2026 — and adds the §13.3 venue-liveness gate). Status: awaiting Chinmay's go for build.
Internal document. Design target ($50M BTC) comes from a private briefing; nothing in the public deliverables may reference a client or mandate. Public framing: "a BTC carry fund at institutional scale".

## 0. The idea in one paragraph
MAVERIK asked a yes/no question of the past: "if May 2022 happened again, does this book break?" This engine asks a probability question of many possible futures: "across 10,000 simulated futures, including crashes, hacks, freezes and depegs, how often does this fund design break, how badly, and which safety settings make breaking rarest per unit of yield given up?" That is the Gauntlet method (agent-based Monte Carlo simulation), applied to a KPK Fund instead of a Morpho vault. It is the most quant-native format this role can be answered in, it is buildable in Python in days, and every number it produces is reproducible from a seed.

## 1. Jargon used in this spec (each term defined once, here)
- **Monte Carlo simulation** — instead of forecasting one future, generate thousands of plausible futures from a statistical model, run the strategy inside each, and read the distribution of outcomes (median, bad percentiles, worst cases) rather than a single answer.
- **Agent** — a simulated actor with its own rules: our fund's exit agent, liquidators, arbitrageurs, other borrowers, panicking depositors. Agents react to the simulated world and to each other; the interesting failures emerge from their interaction, which a price chart alone cannot show.
- **Agent-based simulation** — a Monte Carlo simulation whose world contains agents. Gauntlet's signature method for DeFi risk.
- **Path** — one simulated future, hour by hour (e.g. 90 days = 2,160 steps). 10,000 paths = 10,000 futures.
- **GBM (Geometric Brownian Motion)** — the standard random-walk model for prices: each step's percentage change is drawn from a bell curve. Known flaw: real crypto returns have **fat tails** (extreme moves happen far more often than a bell curve predicts), so GBM alone is banned in this spec (see §3.1).
- **Jump diffusion** — GBM plus randomly timed, randomly sized sudden jumps (crashes and spikes). Captures the fact that BTC often moves 15% in an hour, not smoothly.
- **Block bootstrap** — building simulated futures by resampling real historical chunks (e.g. random 24-hour blocks of actual BTC history), preserving real patterns like volatility clustering (calm days follow calm days, violent days follow violent days) that formulas miss.
- **Volatility clustering** — the empirical fact that big moves bunch together. Any price model that ignores it understates multi-day crash risk.
- **Regime** — a market mood (calm, stressed, crisis) with its own volatility and rate behaviour; a **regime-switching model** flips between moods with realistic persistence.
- **Calibration** — fitting the model's knobs to real historical data so simulated futures statistically resemble real ones. An uncalibrated simulation is fiction with charts.
- **Kinked interest-rate model** — Aave's rate formula: borrow rates rise gently as the pool fills, then sharply past an optimal utilisation point (the "kink", 90% on Aave v3 Core USDC). Past the kink, borrowing costs explode by design, to push borrowers to repay.
- **Utilisation** — share of a pool currently lent out (defined in the glossary note). Drives rates via the kinked model.
- **Liquidator** — an independent bot/actor who repays part of an unhealthy loan and seizes its collateral at a discount (the **liquidation bonus**, ~5% on the relevant Aave markets). Liquidators are profit-driven agents: they act only when the trade pays after gas, which is why small or congested-market positions can sit unliquidated.
- **Gas** — the fee paid to execute a transaction on Ethereum. In crises gas can rise 10–50×, delaying or pricing out exits and liquidations.
- **Oracle** — the price feed the protocol liquidates against (Chainlink for Aave). Oracles update on deviation thresholds and **heartbeats** (a maximum time between updates, e.g. 24h). A stale or wrong oracle means the protocol acts on a fictional price.
- **Depeg** — a wrapped/staked/stable token trading away from its reference price (WBTC vs BTC, cbBTC vs BTC, USDC vs $1).
- **CVaR / Expected Shortfall (ES95)** — the average loss across the worst 5% of futures. The honest risk number: not "the worst case" (one freak path) and not the median (a typical day), but "when it is bad, how bad on average".
- **Efficient frontier (of safety settings)** — for each candidate safety setting, plot yield earned vs risk taken; the frontier is the set of settings where you cannot get more yield without more risk. Recommending a point on it, with reasons, is the deliverable's core argument.
- **Look-ahead bias** — accidentally letting the simulation use information that was not available at that simulated moment. Invalidates results; tests must guard against it.
- **Seed** — the fixed starting number for the random generator. Same seed + same code = identical results, bit for bit. Every reported number carries its seed.

## 2. The strategy under test (the "fund")
KPK's published BTC Carry template, scaled, with our candidate redesign as the variable under test:
- Collateral: BTC in two sleeves, WBTC and cbBTC (the wrapper split is itself a risk variable: a wrapper depeg hits only its sleeve).
- Venue: supply on Aave v3 Core (LLTV 85%); borrow USDC.
- Deployment: borrowed USDC into KPK USDC Yield and USDC Yield RWA vaults (modelled as rate-paying destinations with their own capacity and failure modes).
- Fund agent policy (KPK's published trigger list, implemented as code): maintain HF in target band by partial deleveraging; full unwind below HF floor; unwind on BTC −10%/24h; unwind on negative carry for 2× 6h windows; unwind on wrapper depeg beyond threshold; unwind on venue freeze signal.
- Design size: $50M BTC (internal); the engine is size-parameterised so 13 WBTC (KPK's live fund today) and $320M (Sentora scale) are also runnable for comparison.

## 3. Model architecture (four layers)
### 3.1 Market layer (prices and rates)
Three price generators run in parallel and results are reported per generator, never blended invisibly:
  (a) **Historical block bootstrap** of hourly BTC returns, Jan 2023–Oct 2026 (blocks of 24h, preserving volatility clustering).
  (b) **Jump diffusion**: drift 0, annualised vol calibrated per regime, jumps from a Poisson process (a random-event clock) with jump sizes drawn from the empirical distribution of BTC's worst 200 hourly moves.
  (c) **Regime-switching**: calm / stressed / crisis regimes with a transition matrix estimated from realised-vol history.
Rates:
  - Aave USDC borrow rate from the actual kinked model, fed by simulated utilisation (§3.2), not sampled independently — this coupling is where naive models lie: in a crash, utilisation spikes and the borrow rate can flip the carry negative exactly when exit is hardest.
  - Deployment yield: KPK vault APYs modelled with an own-size impact function — depositing $X into a vault of size $Y dilutes its APY by a fitted curve; this is the mechanism that makes $50M categorically different from $500K.
  - T-bill rate as the floor/opportunity cost series (FRED).
### 3.2 Venue layer (protocol state)
- Aave v3 Core WBTC/cbBTC and USDC pool state: total supply, total borrows, utilisation, reserve factors, rate outputs per the onchain formula.
- Deployment vaults: TVL, utilisation, withdrawal queue behaviour at 100% utilisation (withdrawals blocked, modelled explicitly).
- Oracle layer: Chainlink-style updates with deviation threshold + heartbeat; oracle price and market price tracked as separate series (the Resolv lesson: they diverge in exactly the scenarios that matter).
### 3.3 Agent layer (Gauntlet-style)
- **Fund agent** — executes the §2 policy; reaction latency parameterised (KPK's agents act in ~1 block under normal conditions; latency degrades under gas spikes/freezes in event scenarios).
- **Liquidator agents** — a population with heterogeneous capital, gas bids and profit thresholds; they liquidate any position (ours, others') whose HF < 1 when profitable after gas and slippage (**slippage**: the price worsening caused by your own trade's size).
- **Other borrowers** — a background population whose borrowing/repaying drives utilisation; in crisis regimes they deleverage en masse (a **liquidation cascade**: falling prices trigger seizures, seized collateral is sold, prices fall further).
- **Depositors** — in stress regimes, withdraw from deployment vaults, pushing utilisation toward 100% and trapping our borrowed-USDC leg (the EURC mechanism).
- **Arbitrageurs** — restore wrapper pegs by buying the depegged wrapper and redeeming it, at a speed proportional to the discount and their capital; in hack scenarios their capital partially vanishes (bridges/redemptions halted), so depegs persist.
### 3.4 Event layer (scenario injections)
Events are parameterised shocks injected at a random or fixed time into a path: each has trigger, magnitude, duration, the state variables it hits, and its historical source. Library in §4. Events compose: a crash + a freeze can hit the same path, because in reality they did (29 Oct 2026).

## 4. Scenario library (deterministic replays + stochastic overlays)
Deterministic replays run the fund through recorded history tick-by-tick; stochastic overlays inject the event's mechanics into Monte Carlo paths at random times.
| # | Scenario | Source event | Mechanics injected |
|---|---|---|---|
| S1 | Flash crash | 10 Oct 2025 cascade | BTC −14% intraday path, liquidation cascade in borrower population, gas 20× for 6h |
| S2 | Sustained crash | Mar 2020; May 2022 (LUNA) | Multi-day −40/−50% paths via bootstrap blocks from the actual windows |
| S3 | Venue freeze (hack) | Binance hack, 29 Oct 2026 | Aave market frozen T hours (parameterised 1h / 12h / 72h): no exit, no rebalance; oracle heartbeat continues; test whether the fund agent's published 12-second unwind survives T>0 — i.e. what if the freeze had hit KPK's market, not someone else's |
| S4 | Wrapper depeg | Kelp rsETH exploit, 18 Apr 2026 (pattern) | cbBTC or WBTC trades −3% / −5% / −8% vs BTC for 6–48h; arbitrageur capital halved; oracle still reads BTC price (gap between oracle value and realisable value is the loss) |
| S5 | Collateral-issuer exploit | Resolv, 22 Mar 2026 | A deployment-vault asset is exploited; oracle marks it at $1.13 while market trades $0.63 (actual figures); vault takes the real loss, reports the oracle one |
| S6 | Opaque counterparty default | Stream Finance / xUSD, 4 Nov 2025 | One deployment destination defaults: loss 30% / 65% / 100% of that sleeve, discovered with a 7-day lag (the rehypothecation lesson: you learn late) |
| S7 | Utilisation crunch | EURC crunch on KPK's own vault | Deployment vault utilisation → 100% for 24–96h; borrowed leg trapped; borrow rate past the kink; fund must either pay crisis rates or partially unwind into a falling market |
| S8 | Oracle failure | Generic, from KPK's own tier criteria | Oracle stale for 24h (heartbeat breach) then updates with a −12% catch-up jump; or oracle prints −5% wrong for 3h, triggering false liquidations by liquidator agents |
| S9 | Stablecoin depeg | USDC, Mar 2023 (SVB) | USDC −13% intraday, recovers in 48h; loan denominated in USDC shrinks in real terms while collateral pricing oracles lag — cuts both ways, model both |
| S10 | Negative-carry regime | 2026 compression (Ethena funding 11.0%→2.2%) | Borrow rate exceeds deployment yield for 30–90 days; tests the policy's switch-off discipline and the opportunity cost vs simply holding BTC / T-bills |
| S11 | Gas war | Historical crisis gas | Gas 10–50× for 12h; liquidators skip small positions; fund agent's deleveraging transactions queue and partially fail |
| S12 | Correlated failure | Composite | S1 + S3 + S4 in one path (crash during a freeze with a wrapper depeg): the "everything at once" percentile that actually kills funds |

## 5. The experiment (parameter sweep → frontier)
Grid (each cell = full Monte Carlo run, 10,000 paths × 90 days, hourly):
- Target LTV: 50% / 55% / 60% / 65% / 70.2% (KPK template)
- HF unwind floor: 1.05 / 1.10 / 1.13 (KPK template) / 1.20
- Sleeve split WBTC:cbBTC: 100:0 / 70:30 / 50:50
- Deployment cap per vault: none / 25% / 15% of vault TVL
- Policy: KPK trigger list as published vs trigger list + our additions (volatility-scaled LTV: automatically lower target LTV when 7-day realised vol exceeds a threshold)
≈ 180 cells × 3 price generators. Compute: vectorised numpy (all paths of a cell simulated as one array operation per step); estimated hours, not days, on a laptop-class VM. Every cell stores its seed and config hash.

## 6. Outputs (what the engine reports, per cell and per scenario)
1. Distribution of net yield in BTC terms (the fund's denomination): median, 5th/95th percentile, P(net yield < 0).
2. P(at least one liquidation event), expected liquidation loss in BTC, and ES95 (average loss in the worst 5% of paths).
3. P(HF breaches the unwind floor), average time spent below target band, number of policy unwinds per path (each unwind has a cost; a policy that unwinds weekly is uninvestable even if safe).
4. Scenario table: for each S1–S12, loss at template settings vs at recommended settings — the "hacks section" a hiring manager can read in two minutes.
5. The frontier chart: yield vs ES95 across the grid, KPK's template marked, our recommended point marked, and the one-line argument for the recommendation.
6. Benchmarks on identical paths: hold BTC (yield 0, risk 0), Sentora-style single-cycle at conservative LTV (the competitor as a parameter setting, not a strawman), KPK template as published.

## 7. Calibration and validation (what makes it science rather than fiction)
- Calibration targets: simulated BTC hourly return distribution, vol clustering and worst-1% moves must statistically match Jan 2023–Oct 2026 reality (reported side by side; mismatch > tolerance fails the build).
- Replay validation 1 (the strong one): run S3 mechanics against 29 Oct 2026's actual recorded prices — the engine's fund agent must reproduce KPK's real outcome (full unwind, position closed whole) under the real event's parameters. If our model cannot reproduce the one real-world test the fund has passed, the model is wrong, and we say so.
- Replay validation 2: at 70.2% LTV, S1's recorded path must come within one hourly step of the theoretical liquidation point (17.4% fall); at 60% it must survive. Arithmetic the reader can check by hand.
- Historical backtest layer: the deterministic replays of §4 run the strategy as if live through 2023–2026, with only information available at each timestamp (look-ahead tests in the test suite).
- Declared limitations (printed in the deliverable, not buried): fat tails are modelled, not eliminated; agent behaviour parameters are estimated, not observed; oracle behaviour in a true venue exploit is partly unobservable; results are decision support, not a guarantee. Honest limits are a Gauntlet house style, and KPK will recognise it.

## 8. Python implementation (repo layout)
```
kpk-carry-sim/
  README.md                # how to reproduce every number (seed table)
  pyproject.toml           # pinned deps: numpy, pandas, scipy, pyarrow, matplotlib, pyyaml, pytest
  configs/
    fund_template.yaml     # KPK published parameters (source-cited per line)
    scenarios.yaml         # S1–S12 parameter blocks
    sweep.yaml             # §5 grid
  data/                    # pulled series + provenance file (source, timestamp, hash per file)
    pull_data.py           # BTC hourly (exchange API), Aave rates/utilisation (Aave API/subgraph),
                           # KPK vault sizes/APYs (KPK app/DefiLlama), T-bill (FRED)
  sim/
    prices.py              # bootstrap / jump-diffusion / regime generators
    rates.py               # kinked IRM, vault APY impact curves
    venue.py               # pool state, oracle model, freeze/depeg state machines
    agents.py              # fund agent (policy), liquidators, borrowers, depositors, arbitrageurs
    events.py              # S1–S12 injectors
    engine.py              # path runner (vectorised), seed control
    metrics.py             # §6 outputs, frontier computation
  tests/
    test_no_lookahead.py   # future data must not change past decisions
    test_liquidation_math.py  # HF/LTV arithmetic vs hand-checkable cases
    test_replay_oct2025.py    # §7 validations as automated tests
  notebooks/
    01_calibration.ipynb   # model vs reality, side by side
    02_sweep_results.ipynb # frontier + scenario table
  report/
    memo.md                # KPK docs-style fund risk memo (the deliverable)
    factsheet.md           # 2-page summary with the frontier chart
```
Rules: no number in the memo that is not generated by a committed run; every run logged (config hash, seed, git commit); tests run before results are quoted anywhere.

## 9. Build plan
- **Phase 0 — Template verification + data (half day).** Re-verify KPK BTC Carry parameters on docs.kpk.io (standing caveat: second deep dive could not relocate primary pages; template lines in fund_template.yaml need a live source each). Pull and hash all data series.
- **Phase 1 — Core engine (1–2 days).** Prices + rates + venue + fund agent; single-strategy runs; calibration notebook; liquidation-math tests.
- **Phase 2 — Agents + events (1–2 days).** Liquidators, background populations, S1–S12; replay validations as tests.
- **Phase 3 — Sweep + deliverables (1 day).** Full grid, frontier, scenario table, memo + factsheet in KPK house format; adversarial review pass; MAVERIK attached as the vault-side appendix so the submission shows both layers.
Total ≈ 4–6 focused days. Phases gated: no Phase 3 numbers quoted until Phase 2 validation tests pass.

## 10. Why this beats the alternatives (for the record)
- vs a prose strategy memo: anyone can write one; this is executed, seeded, tested work product — the quant role's native currency.
- vs a simple historical backtest: a backtest shows one past; the Monte Carlo shows the distribution of futures, and the agent layer is what generates the failure modes (cascades, trapped withdrawals, skipped liquidations) that actually kill funds and that KPK's own framework (150+ checks, worst-dimension rating) is built to catch.
- vs MAVERIK alone: MAVERIK is deterministic stress on vault positions (someone else's book). This is stochastic simulation of a fund strategy (our design) — it demonstrates the funds-side job: design the strategy, set its parameters from simulation, and write the risk case for it.

## 11. Where the 45 / 20 / 15 / 20 allocation comes from (provenance, added v0.2)
Status of the number: a **prior** — a reasoned starting allocation the simulation starts from and then replaces with sweep output. It is NOT a result and must never be quoted as one. Its ancestry and logic:
- It descends from the 4 Nov project brief's sleeve ranges (lending core 40–50%, basis 30–40%, spread 10–20%, calls 0–10%), re-cut for the Monte Carlo sleeve set (carry / basis / AMM / T-bill).
- Carry 45%: the anchor because it has the deepest evidence base (KPK's live template, Sentora's $320M proof) and the only sleeve with no capacity question at $50M once split across venues.
- Basis 20%: cut from the brief's 30–40% because the 2026 evidence (funding 2.2%, CME basis ≈3% < T-bills) says the sleeve is gated off most of the time; sizing it at 35% would leave a third of the fund permanently parked and misstate the fund's intent.
- AMM 15%: a capacity cap, not a preference. Pool depth (largest relevant pools are single-digit $M) means a larger weight cannot be deployed without becoming the pool; 15% of $50M ≈ $7.5M is already aggressive and the memo must show the capacity arithmetic.
- T-bill 20%: the original brief's liquidity rule (≥20% T+1 liquidity), retained. It is a floor derived from obligations, not a return choice: redemptions + futures margin top-ups must be meetable without forced unwinds; Phase 1 includes a buffer-adequacy simulation that can raise this floor with evidence.
The §5 sweep extends to allocation weights (carry 30–60%, basis 0–30%, AMM 0–20%, T-bill floor binding) so the final memo recommends weights from the frontier, with the prior shown for contrast.

## 12. JD coverage matrix (the posting's Key Responsibilities, line by line)
| JD responsibility | Covered where in this PoW | Artefact that proves it |
|---|---|---|
| Build simulation frameworks to stress test onchain strategies under adverse conditions: depegs, oracle failures, liquidity crunches, cascading liquidations | Engine (§3) + event layer S4/S5 (depegs), S8 (oracle failure), S7 (liquidity crunch), S1/S12 + liquidator/borrower agents (cascading liquidations) | sim/events.py, sim/agents.py; scenario results table (§6.4) |
| Model concentration risk, counterparty exposure, and correlation dynamics across the portfolio | Portfolio risk module (§13): per-venue/per-asset/per-counterparty exposure, HHI concentration index, sleeve correlation matrices in calm vs crisis regimes (correlations are regime-dependent in the model, because in reality they spike toward 1 in crashes) | sim/portfolio.py outputs in memo §"Portfolio risk" |
| Analyse historical onchain data to validate risk assumptions and calibrate model parameters | Phase 0 data pulls are onchain (Aave/Morpho/Gearbox reserve and position data, Uniswap pool history; Balancer history only through Oct 2026 and labelled historical, see §13.3), not price-only feeds; calibration targets + replay validations (§7) | data/ provenance file; notebooks/01_calibration.ipynb; tests/test_replay_oct2025.py |
| Produce quantitative risk reports that inform vault construction and rebalancing decisions | Memo written as a parameter-recommendation report: every finding lands as a construction/rebalancing instruction (target LTV, HF band, caps per vault/venue, sleeve weights, gate thresholds), in KPK's own docs format; MAVERIK appendix covers the vault side explicitly | report/memo.md recommendation table |
| Model and backtest a basket of AMM positions: LP construction, fee optimisation, impermanent loss dynamics, rebalancing strategies across concentrated and full-range liquidity | AMM sub-engine (§14): a basket (multiple pools, not one), historical backtest AND Monte Carlo; LP construction variants, fee-tier and range-width optimisation, IL/LVR computed per path, rebalancing rules compared including full-range as a first-class variant | sim/amm.py; notebooks/03_amm_basket.ipynb |
| Work with live protocols (Morpho, Gearbox, Aave, Uniswap, Balancer) and real capital, not academic datasets | Protocol coverage (§13.2) for the four live protocols, modelled from live state and used as actual venues/sleeves. Balancer is handled explicitly in §13.3: the JD (posted 27 Oct 2026) predates Balancer's pools going withdrawal-only (30 Oct 2026, BIP-928 wind-down), so Balancer is excluded as a live venue and the memo says why — flagging a stale JD line with evidence is itself part of the deliverable's diligence | configs per protocol with live-source citations + §13.3 liveness log |

## 13. Portfolio risk module + protocol coverage (added v0.2)
### 13.1 Portfolio risk metrics (computed per path, reported as distributions)
- **Concentration:** exposure share by venue, by asset, by wrapper issuer; **HHI** (Herfindahl-Hirschman Index: sum of squared exposure shares — 1.0 = everything in one place, lower = more spread) reported for the fund and tracked against a stated ceiling.
- **Counterparty exposure:** a named-entity table — Aave protocol, Morpho protocol, Gearbox protocol, each deployment vault, Coinbase (cbBTC issuer), BitGo (WBTC custodian), the perp venue, the T-bill issuer — with current exposure, cap, and loss-given-default per entity feeding scenario S6-style defaults at portfolio level.
- **Correlation dynamics:** sleeve return correlations estimated separately in calm and crisis regimes from simulated paths; the memo reports the diversification benefit honestly, including the paths where correlations converge to 1 and diversification fails (that failure is an output, not an embarrassment).
### 13.2 Protocol coverage (the JD's five protocols: four live with a real job each, Balancer excluded per §13.3)
- **Aave v3 Core** — carry sleeve's lending/borrowing venue (full kinked rate model).
- **Morpho** — the deployment vaults' home protocol (KPK USDC Yield vaults are Morpho vaults); also the MAVERIK appendix's subject, and a carry-venue alternative in the venue-comparison run.
- **Gearbox** — KPK's first public curator venue (Oct 2025); modelled as the carry sleeve's second venue: Gearbox Credit Accounts allow leveraged positions inside one account, so the venue comparison (Aave vs Morpho vs Gearbox for the same carry) is a genuine construction decision the memo decides with numbers.
- **Uniswap v3/v4** — AMM basket's concentrated-liquidity pools.
- **Balancer** — EXCLUDED as a live venue (wind-down, §13.3). Its two legitimate remaining uses: historical pool data through Oct 2026 in the AMM backtest, labelled historical; and the worked example of venue mortality in the counterparty module (a top-five AMM can vote to shut down — venue sunset is a modelled risk, scenario class added to the counterparty table).

### 13.3 Venue-liveness gate (added v0.3 — the Balancer rule)
No protocol may appear in this PoW as a usable venue on the strength of a JD, a doc page, or TVL history alone. At Phase 0, and again before the memo is finalised, every named venue passes a liveness check, logged in data/provenance: (1) is it operating today (not paused, not withdrawal-only, not in a voted wind-down); (2) governance/news sweep in the last 90 days for exploits, shutdown votes, team dissolution; (3) TVL and volume trend (a venue in terminal decline is flagged even if technically open). A venue failing any check is excluded from live roles and may appear only as clearly-labelled history or as a risk case study.
The originating failure, recorded honestly: Balancer. Balancer Labs announced closure in March 2026 after the Nov 2025 v2 exploit (~$128M); BAL holders passed BIP-928 (29 Sep 2026, >99%) for an orderly shutdown; pausable pools went withdrawal-only on 30 Oct 2026 and the v3 Vault pauses 30 Nov 2026. The JD listing Balancer was posted 27 Oct 2026 — three days before withdrawal-only took effect — so the JD itself is stale on this line. Spec v0.2 repeated the JD's list without checking; v0.3 corrects it. The memo will carry one short paragraph making this catch visible to KPK: venues verified live at build date, Balancer excluded, reason cited. A candidate who catches the employer's stale JD line, politely and with evidence, demonstrates exactly the diligence the role exists for.
Liveness log, first pass 5 Nov 2026 (to be re-run at Phase 0 and pre-submission): Aave — live; note a live proposal to close six minor-chain markets (Sonic, Scroll, zkSync, Metis, Soneium, Aptos) — our venue is Ethereum Core, unaffected, but the check must be per-market, not per-brand. Morpho — live, expanding (Robinhood collateral markets, Aave V4 curation integrations). Uniswap — live, actively launching. Pendle — live (comparator in the brief, not a fund venue). Gearbox — live but small: TVL ≈ $24M (DefiLlama, early Nov 2026), down from far larger; no shutdown found, but at $50M fund scale Gearbox is capacity-constrained to a minor venue role, and the venue-comparison run must use its real current size, not its 2024 reputation. Balancer — DEAD for new positions (above).

## 14. AMM sub-engine detail (added v0.2)
- **Basket, not a pool:** WBTC/USDC and WBTC/ETH on Uniswap v3 (multiple fee tiers: 0.05% / 0.30%), one full-range (v2-style, Uniswap) position as the concentrated-vs-full-range control, plus Balancer weighted-pool history through Oct 2026 as a historical backtest leg only (labelled as such; no simulated live Balancer position). Same capital, same futures, compared head to head.
- **LP construction variants:** range widths ±5% / ±10% / ±25% / full-range; single-sided vs balanced entry; hedge on/off (unhedged runs exist to price the hedge, not as candidates).
- **Fee optimisation, two meanings, both modelled:** (i) fee-tier selection — higher tiers pay more per trade but attract less volume; the optimiser chooses per pool from historical volume/fee data; (ii) range-width selection — narrower ranges earn more per dollar in-range and spend more time out-of-range earning zero; the sweep finds each pool's efficient width.
- **IL/LVR dynamics:** computed exactly from the v3 position formulas per path (no approximation), decomposed in reporting into fee income, IL/LVR, hedge P&L and hedge costs, so a reader can see which term dominates in which regime.
- **Rebalancing strategies compared:** recentre on range exit vs on a fixed cadence (4h / 24h) vs on delta drift (>5%); hedge cadence tied to the same triggers; each strategy pays its real gas + swap costs in the model. The deliverable recommends one per pool with the comparison table behind it.
- **Backtest layer:** the same basket run historically (2023–2026 hourly pool data) before it enters the Monte Carlo, so JD's "model AND backtest" is satisfied literally, twice over.
