# BTC Carry Fund: Agent-Based Monte Carlo Risk Simulation

A Gauntlet-style, agent-based Monte Carlo engine that stress-tests the design of a Bitcoin carry fund at institutional scale, built as a quantitative research proof of work. The fund under test follows KPK's published BTC Carry template: BTC collateral posted on Aave, USDC borrowed against it at a target loan-to-value, and the borrowed USDC deployed into curated yield vaults, with the spread converted back to BTC.

The engine sweeps 1,080 parameter combinations across three price generators, drives the fund through a 12-scenario event library built from real incidents (exchange hacks, wrapper depegs, oracle failures, counterparty defaults, liquidation cascades), and models liquidators, depositors and arbitrageurs as agents alongside the fund's own exit agent, which implements KPK's published unwind triggers. Every reported number traces to a logged simulation run in `report/runs.jsonl`; nothing in the reports is asserted without a run behind it.

Author: Chinmay Gupta. Companion analysis of the vault-curation side (MAVERIK, a position-level stress engine run on KPK's live Morpho Prime vaults) is available separately.

## Start here

| Document | What it is |
| :--- | :--- |
| [report/memo.md](report/memo.md) | The full risk memo: fund design, method, replay validations, frontier, scenario results, portfolio risk, recommendations and stated limitations |
| [report/factsheet.md](report/factsheet.md) | Short summary of the design and results |
| [report/scenario_table.md](report/scenario_table.md) | The generated S1 to S12 scenario table (template vs recommended, 1,000 paths each) |
| [report/calibration.md](report/calibration.md) | Generator calibration against Jan 2023 to Oct 2026 BTC history, with the misses disclosed |
| [report/frontier_data.csv](report/frontier_data.csv) | The full frontier dataset behind the chart (the chart itself regenerates with the downstream run below) |
| [docs/SPEC.md](docs/SPEC.md) | The governing build specification (v0.3), including the venue-liveness gate in §13.3 |

## Headline results

Parameters were selected by a disclosed mechanical rule (filter, then rank), not by hand. On the primary block-bootstrap panel (10,000 paths per cell):

| Metric | KPK published template (Cell 163) | Recommended (Cell 149) |
| :--- | :--- | :--- |
| Target LTV | 70.2% | 70.2% |
| Health factor unwind floor | 1.13 | 1.05 |
| Collateral mix | 100% WBTC | 70:30 WBTC to cbBTC |
| Median carry APY | 1.8843% | 1.9373% |
| Unwind probability, standard paths | 28.27% | 21.64% |
| Portfolio APY (45% carry allocation) | 3.3948% | 3.4189% |

Scenario results that carry the argument (ES95 tail loss / liquidation probability, recommended vs template):

- **S12 composite (crash + venue freeze + wrapper depeg):** +5.65% / 10.8% vs +9.00% / 65.5%. The redesign earns its keep here.
- **S4 held-wrapper depeg:** +1.49% vs +4.99%. The 70:30 collateral split works as intended.
- **S1 flash crash:** +3.39% / 4.0% for both settings. At identical leverage the redesign offers no protection in a pure crash, and the memo says so.

Validation: the engine replays KPK's documented 29 October 2026 venue-freeze unwind and reproduces the outcome exactly (277,337 USDC repaid, 4.925 WBTC returned, zero liquidation loss). That is a mechanics-consistency validation, not an independent forecast, and the memo labels it as such. Calibration verdict is partial: the bootstrap generator passes all checks, the jump-diffusion and regime-switching generators miss the strict volatility-clustering tolerance. Both limitations are documented in the memo rather than smoothed over, as is the withdrawal of an earlier draft of this work that failed internal adversarial review.

## Repository layout

- `sim/`: the engine: price generators (`prices.py`), Aave kinked rates (`rates.py`), venue and oracle model (`venue.py`), agent populations (`agents.py`), the S1 to S12 event injectors (`events.py`), portfolio risk (`portfolio.py`), metrics (`metrics.py`) and the sweep and reporting runner (`sweep.py`)
- `configs/`: the verified fund template (`fund_template.yaml`, checked against docs.kpk.io on 5 Nov 2026) and scenario definitions (`scenarios.yaml`)
- `data/`: data pull script, provenance note, and the raw series used (Coinbase BTC/USD hourly, Jan 2023 to Oct 2026; DTB3 Treasury bill rates)
- `notebooks/`: the calibration notebook
- `report/`: memo, factsheet, scenario table, calibration note, frontier data, and `runs.jsonl`, the full run log (1,126 records)
- `tests/`: 58 tests, including liquidation math, no-lookahead, seed reproducibility, replay checks, and a guard that fails if any downstream record's configuration differs from the selected cell

## Reproduce

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                        # 58 tests
python -m sim.sweep --downstream-only   # regenerate scenarios, benchmarks, allocation, chart and tables from the logged grid
python -m sim.sweep --full              # full 1,080-cell sweep across all three generators
```

## Traceability note

Each record in `report/runs.jsonl` is stamped with the git commit of the code that generated it in the local development history (grid runs `7b809d8b`; downstream runs `520262df`), and scenario records carry a configuration hash binding the fund configuration, injector parameters, generator and seed. This public repository is the publication snapshot of that worktree at its final state; the stamps are preserved in the records so the generating code state for every number can be identified.

## Venue note

Balancer appears in this repository only as history. Balancer Labs announced its shutdown in 2026 and its pools moved to withdrawal-only, so per the venue-liveness gate (SPEC §13.3) it is excluded as a live venue everywhere in the simulation and retained solely as a venue-mortality case study.
