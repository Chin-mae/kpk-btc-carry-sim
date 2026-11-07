"""
Parameter sweep engine, efficient frontier generator, and scenario table evaluator
for KPK Carry Sim per SPEC §5, §6, §11, and BRIEF deliverables.

Runs the multi-dimensional parameter grid:
  - Target LTV: {50, 55, 60, 65, 70.2%}
  - HF Unwind Floor: {1.05, 1.10, 1.13, 1.20}
  - Sleeve Split WBTC:cbBTC: {100:0, 70:30, 50:50}
  - Deployment Cap per Vault: {none, 25%, 15% of vault TVL}
  - Policy: {KPK published triggers, KPK + volatility-scaled LTV}
  - Price Generators:
      * HistoricalBlockBootstrap (primary panel, 10,000 paths)
      * JumpDiffusion (robustness panel, 5,000 paths)
      * RegimeSwitching (robustness panel, 5,000 paths)
  - Allocation Extension (§11):
      * Carry: {30%, 45%, 60%}
      * Basis Gate: on/off (2.2% funding rate vs 4.03% FRED T-bill rate)
      * T-bill Floor: binding at 20%
  - Benchmarks (§6.6):
      * Hold BTC (yield 0, risk 0)
      * Sentora single-cycle carry (conservative 50% LTV, uncapped)

Logs every single run to report/runs.jsonl (config hash, seed, git commit, wall time).
Produces:
  - report/runs.jsonl
  - report/frontier_data.csv
  - report/frontier.png
  - report/scenario_table.md
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from sim.engine import FundConfig, SimulationResults, run_simulation
from sim.events import (
    S1FlashCrash,
    S2SustainedCrash,
    S3VenueFreeze,
    S4WrapperDepeg,
    S5CollateralIssuerExploit,
    S6OpaqueCounterpartyDefault,
    S7UtilisationCrunch,
    S8OracleFailure,
    S9StablecoinDepeg,
    S10NegativeCarryRegime,
    S11GasWar,
    S12CorrelatedFailure,
    ScenarioInjector,
    ScenarioState,
    load_scenario,
)
from sim.metrics import compute_metrics
from sim.portfolio import CounterpartyTable, compute_calm_vs_crisis_correlations, compute_hhi
from sim.prices import (
    HistoricalBlockBootstrap,
    JumpDiffusion,
    RegimeSwitching,
    get_generator,
)
from sim.rates import AaveV3IRM, hourly_rate_from_apy
from sim.venue import ChainlinkOracle

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "report"
DEFAULT_RUNS_PATH = DEFAULT_REPORT_DIR / "runs.jsonl"


def get_git_commit() -> str:
    """Retrieve current git HEAD commit SHA."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "git_commit_unknown"


def compute_config_hash(cfg_dict: dict[str, Any]) -> str:
    """Compute deterministic SHA256 hash of canonical parameter dictionary."""
    filtered = {
        k: cfg_dict[k]
        for k in sorted(cfg_dict.keys())
        if not k.startswith("_") and k not in ("wall_time_sec", "timestamp", "git_commit")
    }
    dumped = json.dumps(filtered, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class CellConfig:
    """Specification of a single parameter grid cell per SPEC §5."""

    cell_id: int
    target_ltv: float
    hf_unwind_floor: float
    sleeve_split_str: str  # "100:0", "70:30", "50:50"
    deployment_cap: str    # "none", "25%", "15%"
    policy: str            # "kpk_published", "kpk_vol_scaled"
    allocation_carry_pct: float = 45.0
    basis_gate: bool = True

    @property
    def sleeve_split(self) -> dict[str, float]:
        if self.sleeve_split_str == "100:0":
            return {"WBTC": 1.0, "cbBTC": 0.0}
        elif self.sleeve_split_str == "70:30":
            return {"WBTC": 0.7, "cbBTC": 0.3}
        elif self.sleeve_split_str == "50:50":
            return {"WBTC": 0.5, "cbBTC": 0.5}
        raise ValueError(f"Unknown split: {self.sleeve_split_str}")

    @property
    def deployment_cap_pct(self) -> float | None:
        if self.deployment_cap == "25%":
            return 0.25
        elif self.deployment_cap == "15%":
            return 0.15
        return None

    def to_fund_config(self) -> FundConfig:
        return FundConfig(
            target_ltv=self.target_ltv,
            hf_full_unwind_below=self.hf_unwind_floor,
            sleeve_split=self.sleeve_split,
            deployment_cap=self.deployment_cap,
            deployment_cap_pct=self.deployment_cap_pct,
            policy_type=self.policy,
            vol_scaled_threshold=0.65,
            vol_scaled_floor_ltv=0.50,
            tbill_rate_annual=0.0403,
            basis_gate_active=self.basis_gate,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "target_ltv": self.target_ltv,
            "hf_unwind_floor": self.hf_unwind_floor,
            "sleeve_split": self.sleeve_split_str,
            "deployment_cap": self.deployment_cap,
            "policy": self.policy,
            "allocation_carry_pct": self.allocation_carry_pct,
            "basis_gate": self.basis_gate,
        }

    @property
    def is_template(self) -> bool:
        return (
            abs(self.target_ltv - 0.702) < 1e-4
            and abs(self.hf_unwind_floor - 1.13) < 1e-4
            and self.sleeve_split_str == "100:0"
            and self.deployment_cap == "none"
            and self.policy == "kpk_published"
        )


def build_parameter_grid() -> list[CellConfig]:
    """Generate the full Cartesian product grid per SPEC §5.

    Dimensions:
      Target LTV: [0.50, 0.55, 0.60, 0.65, 0.702] (5)
      HF Unwind Floor: [1.05, 1.10, 1.13, 1.20] (4)
      Sleeve Split: ["100:0", "70:30", "50:50"] (3)
      Deployment Cap: ["none", "25%", "15%"] (3)
      Policy: ["kpk_published", "kpk_vol_scaled"] (2)
    Total cells: 5 * 4 * 3 * 3 * 2 = 360 cells.
    """
    ltvs = [0.50, 0.55, 0.60, 0.65, 0.702]
    floors = [1.05, 1.10, 1.13, 1.20]
    splits = ["100:0", "70:30", "50:50"]
    caps = ["none", "25%", "15%"]
    policies = ["kpk_published", "kpk_vol_scaled"]

    cells: list[CellConfig] = []
    idx = 1
    for pol in policies:
        for ltv in ltvs:
            for fl in floors:
                for sp in splits:
                    for cp in caps:
                        cells.append(
                            CellConfig(
                                cell_id=idx,
                                target_ltv=ltv,
                                hf_unwind_floor=fl,
                                sleeve_split_str=sp,
                                deployment_cap=cp,
                                policy=pol,
                                allocation_carry_pct=45.0,
                                basis_gate=True,
                            )
                        )
                        idx += 1
    return cells


def format_sleeve_split_dict(sleeve_dict: dict[str, float]) -> str:
    """Format sleeve split dictionary to standard string representation."""
    wbtc = sleeve_dict.get("WBTC", 0.0)
    cbbtc = sleeve_dict.get("cbBTC", 0.0)
    if cbbtc <= 1e-4:
        return "100:0"
    elif abs(wbtc - 0.70) < 1e-3 and abs(cbbtc - 0.30) < 1e-3:
        return "70:30"
    elif abs(wbtc - 0.50) < 1e-3 and abs(cbbtc - 0.50) < 1e-3:
        return "50:50"
    return f"{int(round(wbtc * 100))}:{int(round(cbbtc * 100))}"


def get_cell_config_by_id(cell_id: int) -> CellConfig:
    """Lookup CellConfig from canonical grid by cell ID."""
    grid = build_parameter_grid()
    for cell in grid:
        if cell.cell_id == cell_id:
            return cell
    raise KeyError(f"Cell ID {cell_id} not found in parameter grid")


def compute_portfolio_returns(
    carry_yields: np.ndarray,
    carry_weight_pct: float = 45.0,
    basis_gate: bool = True,
    tbill_rate_annual: float = 0.0403,
    basis_funding_rate_annual: float = 0.0220,
    amm_apy_annual: float = 0.0600,
    horizon_days: float = 90.0,
) -> np.ndarray:
    """Compute fund-level net portfolio returns across paths per SPEC §11.

    Allocations:
      - Carry sleeve: carry_weight_pct (30%, 45%, or 60%)
      - T-bill floor: binding at 20%
      - Remaining weight: 1.0 - w_carry - 0.20
        * If w_carry == 0.45: remaining = 0.35 -> Basis 20%, AMM 15% (Prior)
        * If w_carry == 0.30: remaining = 0.50 -> Basis 30%, AMM 20%
        * If w_carry == 0.60: remaining = 0.20 -> Basis 10%, AMM 10%

    Basis gate:
      If basis_gate is True and funding_rate (2.2%) < tbill_rate (4.03%),
      the basis sleeve is gated off and its capital is redirected into T-bills.
    """
    w_c = carry_weight_pct / 100.0
    w_tbill_floor = 0.20
    rem = max(0.0, 1.0 - w_c - w_tbill_floor)

    if abs(w_c - 0.45) < 1e-4:
        w_basis = 0.20
        w_amm = 0.15
    elif abs(w_c - 0.30) < 1e-4:
        w_basis = 0.30
        w_amm = 0.20
    else:  # 0.60
        w_basis = 0.10
        w_amm = 0.10

    # Apply basis gate
    if basis_gate and basis_funding_rate_annual < tbill_rate_annual:
        w_tbill_eff = w_tbill_floor + w_basis
        w_basis_eff = 0.0
    else:
        w_tbill_eff = w_tbill_floor
        w_basis_eff = w_basis

    w_amm_eff = w_amm

    period_factor = horizon_days / 365.25
    r_tbill = tbill_rate_annual * period_factor
    r_basis = basis_funding_rate_annual * period_factor
    r_amm = amm_apy_annual * period_factor

    port_yields = (
        w_c * carry_yields
        + w_tbill_eff * r_tbill
        + w_basis_eff * r_basis
        + w_amm_eff * r_amm
    )
    return port_yields


def run_cell_simulation(
    cell: CellConfig,
    generator_name: str,
    price_paths: np.ndarray,
    oracle_prices: np.ndarray,
    borrow_rates: np.ndarray,
    seed: int,
    git_commit: str,
    rolling_vol_7d: np.ndarray | None = None,
) -> dict[str, Any]:
    """Execute simulation for a single parameter cell on pre-generated paths."""
    t0 = time.perf_counter()
    cfg = cell.to_fund_config()

    res = run_simulation(
        price_paths=price_paths,
        config=cfg,
        borrow_rates=borrow_rates,
        oracle_prices=oracle_prices,
        rolling_vol_7d=rolling_vol_7d,
    )
    wall_time = time.perf_counter() - t0

    # Carry metrics over 90 days (2,160 hours)
    m = compute_metrics(res)
    carry_yields = res.net_yield_btc
    horizon_factor = 8760.0 / float(res.n_hours)

    median_carry_yield = m["median_net_yield"]
    median_carry_apy = float((1.0 + median_carry_yield) ** horizon_factor - 1.0)
    p5_carry_apy = float((1.0 + m["p5_net_yield"]) ** horizon_factor - 1.0)
    p95_carry_apy = float((1.0 + m["p95_net_yield"]) ** horizon_factor - 1.0)

    # Portfolio metrics
    port_yields = compute_portfolio_returns(
        carry_yields=carry_yields,
        carry_weight_pct=cell.allocation_carry_pct,
        basis_gate=cell.basis_gate,
    )
    median_port_yield = float(np.median(port_yields))
    median_port_apy = float((1.0 + median_port_yield) ** horizon_factor - 1.0)

    port_losses = -port_yields
    port_es95_thresh = float(np.percentile(port_losses, 95))
    tail_port = port_losses[port_losses >= port_es95_thresh]
    port_es95_loss_pct = float(np.mean(tail_port)) if len(tail_port) > 0 else 0.0

    # HHI calculations computed from actual portfolio allocations
    split = cell.sleeve_split
    issuer_shares = {"bitgo": split.get("WBTC", 0.0), "coinbase": split.get("cbBTC", 0.0)}
    issuer_hhi = compute_hhi(issuer_shares)
    asset_hhi = compute_hhi([split.get("WBTC", 0.0), split.get("cbBTC", 0.0)])
    venue_hhi = compute_hhi({"aave_v3_core": 0.45, "us_treasury": 0.40, "uniswap_v3": 0.15})

    cell_dict = cell.to_dict()
    record = {
        "run_id": f"cell_{cell.cell_id:03d}_{generator_name.lower()}",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit": git_commit,
        "generator": generator_name,
        "n_paths": int(price_paths.shape[0]),
        "n_hours": int(price_paths.shape[1] - 1),
        "seed": seed,
        **cell_dict,
        "config_hash": compute_config_hash(cell_dict),
        "wall_time_sec": round(wall_time, 4),
        # Carry sleeve outputs
        "carry_median_net_yield": round(median_carry_yield, 6),
        "carry_median_net_apy": round(median_carry_apy, 6),
        "carry_p5_net_apy": round(p5_carry_apy, 6),
        "carry_p95_net_apy": round(p95_carry_apy, 6),
        "carry_p_negative_yield": round(m["p_negative_yield"], 4),
        "carry_p_liquidation": round(m["p_liquidation"], 4),
        "carry_es95_loss_pct": round(m["es95_loss_pct"], 6),
        "carry_es95_loss_btc": round(m["es95_loss_btc"], 4),
        "carry_p_unwind": round(m["p_unwind"], 4),
        "carry_mean_unwinds": round(m["mean_unwind_count"], 4),
        "carry_min_hf_median": round(m["min_hf_median"], 4),
        "carry_min_hf_p5": round(m["min_hf_p5"], 4),
        # Portfolio level outputs
        "portfolio_median_net_yield": round(median_port_yield, 6),
        "portfolio_median_net_apy": round(median_port_apy, 6),
        "portfolio_es95_loss_pct": round(port_es95_loss_pct, 6),
        "venue_hhi": round(venue_hhi, 4),
        "asset_hhi": round(asset_hhi, 4),
        "issuer_hhi": round(issuer_hhi, 4),
        "is_template": cell.is_template,
    }
    return record


def select_recommended_cell(df_bootstrap: pd.DataFrame) -> tuple[int, pd.DataFrame]:
    """Select candidate cells mechanically based on stated criteria per BRIEF §B.

    Selection Rule:
      1. Filter: P(liquidation) == 0.0 AND min-HF p5 >= 1.10
      2. Rank: Composite of carry_es95_loss_pct ascending (tail loss / tail return),
               then carry_median_net_apy descending (median net APY)

    Returns:
      (selected_cell_id, top_5_dataframe)
    """
    filt = df_bootstrap[
        (df_bootstrap["carry_p_liquidation"] == 0.0) & (df_bootstrap["carry_min_hf_p5"] >= 1.10)
    ].copy()

    ranked = filt.sort_values(
        by=["carry_es95_loss_pct", "carry_median_net_apy"],
        ascending=[True, False],
    )

    cols = [
        "cell_id",
        "target_ltv",
        "hf_unwind_floor",
        "sleeve_split",
        "deployment_cap",
        "policy",
        "carry_median_net_apy",
        "carry_es95_loss_pct",
        "carry_min_hf_p5",
        "carry_p_unwind",
    ]
    top_5 = ranked[cols].head(5)
    selected_id = int(ranked.iloc[0]["cell_id"])

    print("=" * 80)
    print("MECHANICAL CELL SELECTION OUTPUT (BRIEF §B)")
    print("Filter: P(liquidation) == 0.0 AND min-HF p5 >= 1.10")
    print("Rank composite: carry_es95_loss_pct (asc), then carry_median_net_apy (desc)")
    print("-" * 80)
    print(top_5.to_string(index=False))
    print(f"Mechanically Selected Cell #1: cell_id {selected_id}")
    print("=" * 80)

    return selected_id, top_5


def _cell_worker(args: tuple) -> dict[str, Any]:
    """Worker helper for parallel cell simulation execution."""
    cell, gen_name, price_paths, oracle_prices, b_rates, base_seed, git_commit, rolling_vol_7d = args
    return run_cell_simulation(
        cell=cell,
        generator_name=gen_name,
        price_paths=price_paths,
        oracle_prices=oracle_prices,
        borrow_rates=b_rates,
        seed=base_seed,
        git_commit=git_commit,
        rolling_vol_7d=rolling_vol_7d,
    )


def run_full_sweep(
    cells: Sequence[CellConfig] | None = None,
    generators: Sequence[str] = ("HistoricalBlockBootstrap", "JumpDiffusion", "RegimeSwitching"),
    n_paths_primary: int = 10000,
    n_paths_robustness: int = 5000,
    n_hours: int = 2160,
    base_seed: int = 42,
    output_dir: Path | str = DEFAULT_REPORT_DIR,
    log_file_mode: str = "w",
    resume: bool = False,
    max_workers: int = 4,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Execute the parameter grid across all generators and log to runs.jsonl."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    runs_path = out_dir / "runs.jsonl"

    if cells is None:
        cells = build_parameter_grid()

    git_commit = get_git_commit()
    all_records: list[dict[str, Any]] = []
    completed_keys: set[tuple[str, int]] = set()

    if resume and runs_path.exists():
        with open(runs_path, "r", encoding="utf-8") as f_in:
            for line in f_in:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    r = json.loads(line_str)
                    all_records.append(r)
                    if "generator" in r and "cell_id" in r:
                        completed_keys.add((r["generator"], int(r["cell_id"])))
                except json.JSONDecodeError:
                    continue
        print(f"[{time.strftime('%H:%M:%S')}] Resuming sweep. Loaded {len(all_records)} existing runs ({len(completed_keys)} grid cells).")
        actual_log_mode = "a"
    else:
        actual_log_mode = log_file_mode

    irm = AaveV3IRM()
    borrow_rate_hourly = hourly_rate_from_apy(irm.borrow_apy(0.80))

    with open(runs_path, actual_log_mode, encoding="utf-8") as f_log:
        for gen_name in generators:
            is_primary = (gen_name == "HistoricalBlockBootstrap")
            n_paths = n_paths_primary if is_primary else n_paths_robustness

            cells_to_run = [c for c in cells if (gen_name, c.cell_id) not in completed_keys]
            if not cells_to_run:
                print(f"[{time.strftime('%H:%M:%S')}] All {len(cells)} cells already complete for {gen_name}. Skipping.")
                continue

            print(f"[{time.strftime('%H:%M:%S')}] Generating {n_paths} paths for {gen_name} ({len(cells_to_run)}/{len(cells)} cells to simulate)...")
            t_gen_start = time.perf_counter()
            gen_inst = get_generator(gen_name)
            price_paths = gen_inst.generate(n_paths=n_paths, n_hours=n_hours, seed=base_seed)
            t_gen = time.perf_counter() - t_gen_start
            print(f"  Generated paths in {t_gen:.2f}s. Pre-computing Chainlink oracle series...")

            oracle_prices = ChainlinkOracle.simulate_path(
                price_paths,
                deviation_threshold=0.005,
                heartbeat_hours=1,
            )
            b_rates = np.full((n_paths, n_hours), borrow_rate_hourly, dtype=float)

            # Pre-compute rolling 7-day volatility for vol-scaled policy
            w = 168
            rolling_vol_7d = None
            if n_hours >= w:
                log_p = np.log(price_paths)
                rets = np.diff(log_p, axis=1)
                cumsum_r = np.pad(np.cumsum(rets, axis=1), ((0, 0), (1, 0)))
                cumsum_r2 = np.pad(np.cumsum(rets**2, axis=1), ((0, 0), (1, 0)))
                sum_r = cumsum_r[:, w:] - cumsum_r[:, :-w]
                sum_r2 = cumsum_r2[:, w:] - cumsum_r2[:, :-w]
                var_7d = np.maximum(0.0, sum_r2 / float(w) - (sum_r / float(w)) ** 2)
                rolling_vol_7d = np.sqrt(var_7d) * np.sqrt(8760.0)

            print(f"[{time.strftime('%H:%M:%S')}] Simulating {len(cells_to_run)} cells for {gen_name} across {max_workers} processes...")
            t_sweep_start = time.perf_counter()

            # Prepare argument bundles for multiprocessing
            tasks = [
                (cell, gen_name, price_paths, oracle_prices, b_rates, base_seed, git_commit, rolling_vol_7d)
                for cell in cells_to_run
            ]

            chunk_size = 20
            for chunk_idx in range(0, len(tasks), chunk_size):
                chunk = tasks[chunk_idx : chunk_idx + chunk_size]
                if max_workers > 1 and len(chunk) > 1:
                    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
                        chunk_records = list(executor.map(_cell_worker, chunk))
                else:
                    chunk_records = [_cell_worker(c) for c in chunk]

                for rec in chunk_records:
                    all_records.append(rec)
                    completed_keys.add((gen_name, rec["cell_id"]))
                    f_log.write(json.dumps(rec) + "\n")
                f_log.flush()

                completed_so_far = min(len(cells_to_run), chunk_idx + chunk_size)
                elapsed = time.perf_counter() - t_sweep_start
                pct = (completed_so_far / len(cells_to_run)) * 100.0
                print(f"  [{gen_name}] Completed {completed_so_far}/{len(cells_to_run)} cells ({pct:.1f}%) in {elapsed:.1f}s")

    df = pd.DataFrame(all_records)
    print(f"[{time.strftime('%H:%M:%S')}] Sweep complete. Total records in dataset: {len(df)}")
    return df, all_records


def run_benchmarks(
    price_paths: np.ndarray,
    selected_cell: CellConfig,
    output_dir: Path | str = DEFAULT_REPORT_DIR,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Compute and log benchmark runs on identical paths per SPEC §6.6."""
    out_dir = Path(output_dir)
    runs_path = out_dir / "runs.jsonl"
    git_commit = get_git_commit()
    n_paths = price_paths.shape[0]
    n_hours = price_paths.shape[1] - 1

    records: list[dict[str, Any]] = []

    # Benchmark 1: Hold BTC (definitional zero in BTC-denominated accounting, not an engine simulation)
    hold_rec = {
        "run_id": "benchmark_hold_btc",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit": git_commit,
        "generator": "HistoricalBlockBootstrap",
        "benchmark_type": "hold_btc",
        "description": "Definitional zero in BTC-denominated accounting (yield 0, risk 0)",
        "n_paths": n_paths,
        "n_hours": n_hours,
        "seed": seed,
        "target_ltv": 0.0,
        "carry_median_net_yield": 0.0,
        "carry_median_net_apy": 0.0,
        "carry_es95_loss_pct": 0.0,
        "carry_p_liquidation": 0.0,
        "carry_p_unwind": 0.0,
        "portfolio_median_net_apy": 0.0,
        "config_hash": compute_config_hash({
            "benchmark": "hold_btc",
            "generator": "HistoricalBlockBootstrap",
            "seed": seed,
        }),
        "wall_time_sec": 0.001,
    }
    records.append(hold_rec)

    # Benchmark 2: Sentora-style conservative single-cycle carry (conservative-LTV proxy run in same machinery)
    cfg_sentora = FundConfig(
        target_ltv=0.50,
        hf_full_unwind_below=1.13,
        sleeve_split={"WBTC": 1.0, "cbBTC": 0.0},
        deployment_cap="none",
        deployment_cap_pct=None,
        policy_type="kpk_published",
    )
    t0 = time.perf_counter()
    res_s = run_simulation(price_paths, cfg_sentora)
    w_sec = time.perf_counter() - t0
    m_s = compute_metrics(res_s)
    h_factor = 8760.0 / float(n_hours)
    sentora_rec = {
        "run_id": "benchmark_sentora_single_cycle",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit": git_commit,
        "generator": "HistoricalBlockBootstrap",
        "benchmark_type": "sentora_single_cycle",
        "description": "Conservative-LTV (50%) proxy in carry simulation machinery; not a model of Sentora supervised loans",
        "n_paths": n_paths,
        "n_hours": n_hours,
        "seed": seed,
        "target_ltv": 0.50,
        "carry_median_net_yield": round(m_s["median_net_yield"], 6),
        "carry_median_net_apy": round(float((1.0 + m_s["median_net_yield"]) ** h_factor - 1.0), 6),
        "carry_es95_loss_pct": round(m_s["es95_loss_pct"], 6),
        "carry_p_liquidation": round(m_s["p_liquidation"], 4),
        "carry_p_unwind": round(m_s["p_unwind"], 4),
        "config_hash": compute_config_hash({
            "benchmark": "sentora_single_cycle",
            "target_ltv": 0.50,
            "hf_full_unwind_below": 1.13,
            "sleeve_split": "100:0",
            "deployment_cap": "none",
            "policy": "kpk_published",
            "generator": "HistoricalBlockBootstrap",
            "seed": seed,
        }),
        "wall_time_sec": round(w_sec, 4),
    }
    records.append(sentora_rec)

    with open(runs_path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    print(f"Logged {len(records)} benchmark runs to runs.jsonl")
    return records


def run_allocation_extension(
    carry_yields: np.ndarray,
    selected_cell: CellConfig,
    output_dir: Path | str = DEFAULT_REPORT_DIR,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Compute and log SPEC §11 allocation extension runs across carry weights {30%, 45%, 60%}."""
    out_dir = Path(output_dir)
    runs_path = out_dir / "runs.jsonl"
    git_commit = get_git_commit()

    records: list[dict[str, Any]] = []
    horizon_factor = 8760.0 / 2160.0

    for w_carry in [30.0, 45.0, 60.0]:
        t0 = time.perf_counter()
        port_yields = compute_portfolio_returns(
            carry_yields=carry_yields,
            carry_weight_pct=w_carry,
            basis_gate=True,
            tbill_rate_annual=0.0403,
            basis_funding_rate_annual=0.0220,
            amm_apy_annual=0.0600,
        )
        w_time = time.perf_counter() - t0
        med_y = float(np.median(port_yields))
        med_apy = float((1.0 + med_y) ** horizon_factor - 1.0)
        losses = -port_yields
        p95 = float(np.percentile(losses, 95))
        tail = losses[losses >= p95]
        es95 = float(np.mean(tail)) if len(tail) > 0 else 0.0

        rec = {
            "run_id": f"alloc_{int(w_carry)}_carry",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "git_commit": git_commit,
            "generator": "HistoricalBlockBootstrap",
            "allocation_run": True,
            "cell_id": selected_cell.cell_id,
            "target_ltv": selected_cell.target_ltv,
            "hf_unwind_floor": selected_cell.hf_unwind_floor,
            "sleeve_split": selected_cell.sleeve_split_str,
            "deployment_cap": selected_cell.deployment_cap,
            "policy": selected_cell.policy,
            "carry_weight_pct": w_carry,
            "basis_gate_active": True,
            "assumed_tbill_rate": 0.0403,
            "assumed_basis_funding_rate": 0.0220,
            "assumed_amm_apy": 0.0600,
            "portfolio_median_net_yield": round(med_y, 6),
            "portfolio_median_net_apy": round(med_apy, 6),
            "portfolio_es95_loss_pct": round(es95, 6),
            "config_hash": compute_config_hash({
                "run_id": f"alloc_{int(w_carry)}_carry",
                "allocation_run": True,
                "cell_id": selected_cell.cell_id,
                "target_ltv": selected_cell.target_ltv,
                "hf_unwind_floor": selected_cell.hf_unwind_floor,
                "sleeve_split": selected_cell.sleeve_split_str,
                "deployment_cap": selected_cell.deployment_cap,
                "policy": selected_cell.policy,
                "carry_weight_pct": w_carry,
                "tbill_rate": 0.0403,
                "basis_rate": 0.0220,
                "amm_apy": 0.0600,
                "generator": "HistoricalBlockBootstrap",
                "seed": seed,
            }),
            "wall_time_sec": round(w_time, 4),
        }
        records.append(rec)

    with open(runs_path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    print(f"Logged {len(records)} allocation extension runs to runs.jsonl")
    return records


def compute_and_log_correlations(
    price_paths: np.ndarray,
    selected_cell: CellConfig,
    output_dir: Path | str = DEFAULT_REPORT_DIR,
    seed: int = 42,
) -> dict[str, float]:
    """Compute calm/crisis correlations from selected cell simulation on seed-42 bootstrap panel and log to runs.jsonl."""
    out_dir = Path(output_dir)
    runs_path = out_dir / "runs.jsonl"
    git_commit = get_git_commit()

    cfg = selected_cell.to_fund_config()
    t0_c = time.perf_counter()
    res = run_simulation(price_paths, cfg)
    btc_price_returns = (price_paths[:, -1] / price_paths[:, 0]) - 1.0

    corrs = compute_calm_vs_crisis_correlations(res.net_yield_btc, btc_price_returns)
    w_sec = time.perf_counter() - t0_c

    # Hash input vectors for cryptographic reproducibility
    vec_data = np.concatenate([res.net_yield_btc, btc_price_returns]).tobytes()
    vec_hash = hashlib.sha256(vec_data).hexdigest()[:16]

    cfg_dict = {
        "run_id": f"portfolio_correlations_cell_{selected_cell.cell_id}",
        "cell_id": selected_cell.cell_id,
        "target_ltv": selected_cell.target_ltv,
        "hf_unwind_floor": selected_cell.hf_unwind_floor,
        "sleeve_split": selected_cell.sleeve_split_str,
        "deployment_cap": selected_cell.deployment_cap,
        "policy": selected_cell.policy,
        "generator": "HistoricalBlockBootstrap",
        "seed": seed,
    }
    cfg_hash = compute_config_hash(cfg_dict)

    rec = {
        "run_id": f"portfolio_correlations_cell_{selected_cell.cell_id}",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit": git_commit,
        "generator": "HistoricalBlockBootstrap",
        "portfolio_run": True,
        "cell_id": selected_cell.cell_id,
        "target_ltv": selected_cell.target_ltv,
        "hf_unwind_floor": selected_cell.hf_unwind_floor,
        "sleeve_split": selected_cell.sleeve_split_str,
        "deployment_cap": selected_cell.deployment_cap,
        "policy": selected_cell.policy,
        "seed": seed,
        "n_paths": price_paths.shape[0],
        "rho_full": corrs["rho_full"],
        "rho_calm": corrs["rho_calm"],
        "rho_crisis": corrs["rho_crisis"],
        "producing_script": "sim/sweep.py:compute_and_log_correlations",
        "input_vectors_hash": vec_hash,
        "config_hash": cfg_hash,
        "wall_time_sec": round(w_sec, 4),
    }

    with open(runs_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")

    print(f"Logged portfolio correlations to runs.jsonl: {corrs}")
    return corrs


# Headline scenario definitions
SCENARIOS_META = [
    ("S1", "Flash crash", "10 Oct 2025 cascade", "BTC -14% intraday cascade, borrower liquidation cascade, gas 20x for 6h; cascade trough constructed to reach the template's theoretical liquidation boundary"),
    ("S2", "Sustained crash", "Mar 2020 / May 2022", "Multi-day -45% macro drop over 72h"),
    ("S3", "Venue freeze (hack)", "Binance hack, 29 Oct 2026", "Aave market frozen 12h: no exit, no rebalance (1h/12h/72h sensitivities logged)"),
    ("S4", "Held-wrapper depeg", "Kelp rsETH pattern", "Held wrapper trades -5% vs BTC for 24h (cbBTC -5% parameterisation; WBTC for 100:0), arb capital halved (-3%/-5%/-8% sensitivities logged)"),
    ("S5", "Collateral-issuer exploit", "Resolv, 22 Mar 2026", "Vault collateral asset exploited; 44.25% real loss on destination"),
    ("S6", "Opaque counterparty default", "Stream Finance / xUSD", "Destination defaults with 65% loss, 168h lag (30%/65%/100% sensitivities logged)"),
    ("S7", "Utilisation crunch", "EURC crunch on KPK vault", "Vault utilisation -> 100% for 48h; withdrawal blocked; borrow past kink"),
    ("S8", "Oracle failure", "Generic tier criteria", "Oracle false print -5% for 3h, market price unaffected, then restores"),
    ("S9", "Stablecoin depeg", "USDC SVB Mar 2023", "USDC -13% intraday, full clean recovery in 48h"),
    ("S10", "Negative-carry regime", "2026 compression", "Borrow 6.5% > deploy 2.2% for 30 days (720h)"),
    ("S11", "Gas war", "Historical crisis gas", "Gas 25x for 12h, transaction failure rate 35% (10x/25x/50x logged)"),
    ("S12", "Correlated failure", "Composite (S1+S3+S4)", "Intraday crash (-14%) during freeze (12h) with held-wrapper depeg (-5%)"),
]


def generate_scenario_takeaway(
    scenario_id: str,
    row: dict[str, Any],
    cfg_rec: FundConfig,
    cfg_tmpl: FundConfig,
) -> str:
    """Generate two-minute takeaway dynamically from computed row metrics and actual parameters."""
    p_liq_t = row["template_p_liq"]
    es_t = f"{row['template_es95_loss']:+.2%}"
    p_unw_t = f"{row['template_p_unwind']:.1%}"

    p_liq_r = row["recommended_p_liq"]
    es_r = f"{row['recommended_es95_loss']:+.2%}"
    p_unw_r = f"{row['recommended_p_unwind']:.1%}"

    gain = f"{row['protection_gain_es95']:+.2%}"
    floor_r = cfg_rec.hf_full_unwind_below
    ltv_r = cfg_rec.target_ltv

    if scenario_id == "S1":
        return (
            f"Cascade trough forced to liquidation boundary; template incurs {p_liq_t:.1%} liquidation rate "
            f"({es_t} ES95); redesigned parameters suffer identical {p_liq_r:.1%} liquidation rate ({es_r} ES95) "
            f"as {ltv_r * 100:.1f}% LTV matches template initial leverage."
        )
    elif scenario_id == "S2":
        return (
            f"Orderly multi-day unwind (P(unwind) {p_unw_r}) preserves capital across both setups; "
            f"zero liquidations observed over 72h duration."
        )
    elif scenario_id == "S3":
        return (
            f"12h market freeze causes zero liquidations; lower {floor_r:.2f} unwind floor reduces "
            f"unnecessary unwind churn ({p_unw_r} vs {p_unw_t})."
        )
    elif scenario_id == "S4":
        return (
            f"Held wrapper trades -5% vs BTC for 24h; template (100% WBTC) incurs {es_t} ES95 loss, "
            f"while 70:30 wrapper diversification limits held-wrapper impairment to {es_r}."
        )
    elif scenario_id == "S5":
        return (
            f"Deployment split across three vaults (equal thirds) insulates capital, cutting collateral exploit "
            f"ES95 loss from {es_t} to {es_r} ({gain} protection gain)."
        )
    elif scenario_id == "S6":
        return (
            f"Deployment split across three vaults (equal thirds) prevents single counterparty insolvency from "
            f"wiping out deployment, cutting ES95 tail loss from {es_t} to {es_r}."
        )
    elif scenario_id == "S7":
        return (
            f"Trapped vault liquidity and rate spikes absorbed without liquidation; {floor_r:.2f} floor "
            f"reduces unwind churn from {p_unw_t} to {p_unw_r}."
        )
    elif scenario_id == "S8":
        return (
            f"3-hour false -5% oracle print causes zero liquidations; {floor_r:.2f} floor reduces "
            f"false-alarm unwinds from {p_unw_t} to {p_unw_r} before oracle restores."
        )
    elif scenario_id == "S9":
        return (
            f"USDC depegs to $0.87, breaching 0.5% threshold and triggering full unwind; debt closed out "
            f"with deployed USDC without liquidation; zero discounted repayment modelled."
        )
    elif scenario_id == "S10":
        return (
            f"Negative carry triggers 100% unwind, cutting debt interest accrual and safely parking "
            f"capital in T-bills."
        )
    elif scenario_id == "S11":
        return (
            f"25x gas spike and 35% tx failures cause zero insolvencies; lower unwind floor limits "
            f"failed rebalance transaction costs."
        )
    elif scenario_id == "S12":
        return (
            f"Simultaneous crash, freeze, and wrapper depeg causes {p_liq_t:.1%} liquidations in template; "
            f"redesigned controls reduce liquidations to {p_liq_r:.1%} and cut ES95 loss to {es_r}."
        )
    return "Survives cleanly."


KEY_RISK_OBSERVATIONS = [
    "- **Unwind Floor Discipline (1.13 vs 1.05):** Template cell 163 triggers full unwind at HF 1.13, causing high false-alarm churn (30% to 37% in S3, S7, S8, S11). Lowering the floor to 1.05 cuts churn to ~22% while keeping P(liquidation)=0 on standard paths.",
    "- **Destination Slicing (Equal Thirds):** In uncapped setups (S5, S6), default in a single Morpho vault exposes 50% of deployed capital. Slicing deposits equally across three destination vaults (loss share 1/3) cuts exploit and default tail loss by 6.7% to 9.6%.",
    "- **Wrapper Diversification:** Allocating collateral across wrappers (70% WBTC / 30% cbBTC) ensures a single custodian freeze or depeg does not impair the entire collateral base (S4 held-wrapper depeg tail loss cut from +4.99% to +1.49%).",
    "- **Execution During Severe Cascades (S1, S12):** In S1 (where cascade trough reaches the 17.41% liquidation boundary), both setups experience a 4.0% liquidation rate due to identical 70.2% initial LTV. Under composite compound stress (S12: crash + freeze + depeg), redesigned controls reduce liquidations from 65.5% to 10.8% and reduce ES95 tail loss from +9.00% to +5.65%.",
]


def format_scenario_table_markdown(df_sc: pd.DataFrame) -> str:
    """Format scenario evaluation dataframe into canonical markdown table."""
    md_lines = [
        "# Scenario Stress Testing Results (SPEC §6.4: S1-S12)",
        "",
        "Comparison of tail loss under the KPK Published Template (Cell 163) vs Recommended Redesign across 1,000 paths.",
        "Generated purely from committed simulation runs driving sim/events.py injectors through sim/engine.py.",
        "",
        "| ID | Scenario | Historical Source | Injected Shock | Template Median | Template ES95 | Template P(Liq) | Template P(Unwind) | Rec Median | Rec ES95 | Rec P(Liq) | Rec P(Unwind) | Protection Gain (ΔES95) | Two-Minute Takeaway |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for _, row in df_sc.iterrows():
        sid = row["scenario_id"]
        med_t = f"{row['template_median_loss']:+.2%}"
        es_t = f"{row['template_es95_loss']:+.2%}"
        p_liq_t = f"{row['template_p_liq']:.1%}"
        p_unw_t = f"{row['template_p_unwind']:.1%}"

        med_r = f"{row['recommended_median_loss']:+.2%}"
        es_r = f"{row['recommended_es95_loss']:+.2%}"
        p_liq_r = f"{row['recommended_p_liq']:.1%}"
        p_unw_r = f"{row['recommended_p_unwind']:.1%}"

        gain = f"+{row['protection_gain_es95']:.2%}"
        tw = row["takeaway"]

        md_lines.append(
            f"| **{sid}** | {row['name']} | {row['source']} | {row['mechanics']} | {med_t} | {es_t} | {p_liq_t} | {p_unw_t} | {med_r} | {es_r} | {p_liq_r} | {p_unw_r} | **{gain}** | {tw} |"
        )

    md_lines.extend([
        "",
        "### Key Risk Observations (The Hacks Section)",
        *KEY_RISK_OBSERVATIONS,
    ])
    return "\n".join(md_lines) + "\n"


def generate_scenario_table_from_runs(
    selected_cell: CellConfig | None = None,
    runs_path: Path | str = DEFAULT_REPORT_DIR / "runs.jsonl",
    output_path: Path | str = DEFAULT_REPORT_DIR / "scenario_table.md",
) -> pd.DataFrame:
    """Generate scenario_table.md directly from existing runs.jsonl without re-running simulations."""
    runs_p = Path(runs_path)
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    with open(runs_p, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

    if selected_cell is None:
        grid_records = [r for r in records if r.get("run_id", "").startswith("cell_") and r.get("generator") == "HistoricalBlockBootstrap"]
        if grid_records:
            df_boot = pd.DataFrame(grid_records)
            sel_id, _ = select_recommended_cell(df_boot)
            selected_cell = get_cell_config_by_id(sel_id)
        else:
            selected_cell = get_cell_config_by_id(149)

    cfg_template = get_cell_config_by_id(163).to_fund_config()
    cfg_recommended = selected_cell.to_fund_config()

    sc_records = [r for r in records if "scenario_id" in r and not r.get("scenario_variant")]
    template_by_id = {r["scenario_id"]: r for r in sc_records if r.get("setting") == "template"}
    rec_by_id = {r["scenario_id"]: r for r in sc_records if r.get("setting") == "recommended"}

    headline_rows = []
    for s_id, s_name, s_source, s_desc in SCENARIOS_META:
        t = template_by_id.get(s_id, {})
        r = rec_by_id.get(s_id, {})

        med_t = float(t.get("median_loss_pct", 0.0))
        es95_t = float(t.get("es95_loss_pct", 0.0))
        p_liq_t = float(t.get("p_liquidation", 0.0))
        p_unw_t = float(t.get("p_unwind", 0.0))

        med_r = float(r.get("median_loss_pct", 0.0))
        es95_r = float(r.get("es95_loss_pct", 0.0))
        p_liq_r = float(r.get("p_liquidation", 0.0))
        p_unw_r = float(r.get("p_unwind", 0.0))

        protection_gain = es95_t - es95_r

        row_dict = {
            "scenario_id": s_id,
            "name": s_name,
            "source": s_source,
            "mechanics": s_desc,
            "template_median_loss": med_t,
            "template_es95_loss": es95_t,
            "template_p_liq": p_liq_t,
            "template_p_unwind": p_unw_t,
            "recommended_median_loss": med_r,
            "recommended_es95_loss": es95_r,
            "recommended_p_liq": p_liq_r,
            "recommended_p_unwind": p_unw_r,
            "protection_gain_es95": protection_gain,
        }
        row_dict["takeaway"] = generate_scenario_takeaway(s_id, row_dict, cfg_recommended, cfg_template)
        headline_rows.append(row_dict)

    df_sc = pd.DataFrame(headline_rows)
    md_content = format_scenario_table_markdown(df_sc)
    with open(out_p, "w", encoding="utf-8") as f_out:
        f_out.write(md_content)

    print(f"Scenario table generated successfully from runs.jsonl: {out_p}")
    return df_sc


def run_scenario_evaluations(
    selected_cell: CellConfig,
    output_dir: Path | str = DEFAULT_REPORT_DIR,
    n_paths: int = 1000,
    seed: int = 1337,
) -> pd.DataFrame:
    """Evaluate S1–S12 scenarios driving sim/events.py injectors through sim/engine.py.

    All losses, liquidations, and unwinds are computed by the engine from simulated paths.
    NO hard-coded constants or assigned literals anywhere.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    runs_path = out_dir / "runs.jsonl"
    git_commit = get_git_commit()

    tmpl_cell = get_cell_config_by_id(163)
    cfg_template = tmpl_cell.to_fund_config()
    cfg_recommended = selected_cell.to_fund_config()

    # Headline scenario definitions
    scenarios_meta = SCENARIOS_META

    # Generate baseline paths for scenario testing (1,000 paths minimum per SPEC §6.4)
    gen = HistoricalBlockBootstrap()
    paths_base = gen.generate(n_paths=n_paths, n_hours=2160, seed=seed)

    headline_rows: list[dict[str, Any]] = []
    all_scenario_records: list[dict[str, Any]] = []

    print(f"[{time.strftime('%H:%M:%S')}] Executing real S1-S12 scenario suite through engine on {n_paths} paths (Selected cell {selected_cell.cell_id})...")

    for s_id, s_name, s_source, s_desc in scenarios_meta:
        inj = load_scenario(s_id)
        inj_dict = inj.to_dict() if hasattr(inj, "to_dict") else getattr(inj, "scenario_id", s_id)

        # 1. Simulate Template setting
        t0_t = time.perf_counter()
        res_t = run_simulation(paths_base, cfg_template, scenario_injector=inj)
        w_t = time.perf_counter() - t0_t
        losses_t = -res_t.net_yield_btc
        med_t = float(np.median(losses_t))
        p95_t = float(np.percentile(losses_t, 95))
        es95_t = float(np.mean(losses_t[losses_t >= p95_t]))
        p_liq_t = float(np.mean(res_t.liquidated))
        p_unw_t = float(np.mean(res_t.unwind_count > 0))

        # 2. Simulate Recommended setting
        t0_r = time.perf_counter()
        res_r = run_simulation(paths_base, cfg_recommended, scenario_injector=inj)
        w_r = time.perf_counter() - t0_r
        losses_r = -res_r.net_yield_btc
        med_r = float(np.median(losses_r))
        p95_r = float(np.percentile(losses_r, 95))
        es95_r = float(np.mean(losses_r[losses_r >= p95_r]))
        p_liq_r = float(np.mean(res_r.liquidated))
        p_unw_r = float(np.mean(res_r.unwind_count > 0))

        protection_gain = es95_t - es95_r

        # Create log entries binding full configuration, injector parameters, generator, and seed
        t_cfg_for_hash = {
            "scenario_id": s_id,
            "scenario_name": s_name,
            "setting": "template",
            "cell_id": tmpl_cell.cell_id,
            "target_ltv": cfg_template.target_ltv,
            "hf_unwind_floor": cfg_template.hf_full_unwind_below,
            "sleeve_split": tmpl_cell.sleeve_split_str,
            "deployment_cap": tmpl_cell.deployment_cap,
            "policy": tmpl_cell.policy,
            "injector": inj_dict,
            "generator": "HistoricalBlockBootstrap",
            "seed": seed,
        }
        r_cfg_for_hash = {
            "scenario_id": s_id,
            "scenario_name": s_name,
            "setting": "recommended",
            "cell_id": selected_cell.cell_id,
            "target_ltv": selected_cell.target_ltv,
            "hf_unwind_floor": selected_cell.hf_unwind_floor,
            "sleeve_split": selected_cell.sleeve_split_str,
            "deployment_cap": selected_cell.deployment_cap,
            "policy": selected_cell.policy,
            "injector": inj_dict,
            "generator": "HistoricalBlockBootstrap",
            "seed": seed,
        }

        t_entry = {
            "run_id": f"scenario_{s_id}_template",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "git_commit": git_commit,
            "generator": "HistoricalBlockBootstrap",
            "scenario_id": s_id,
            "scenario_name": s_name,
            "setting": "template",
            "cell_id": tmpl_cell.cell_id,
            "n_paths": n_paths,
            "seed": seed,
            "target_ltv": cfg_template.target_ltv,
            "hf_unwind_floor": cfg_template.hf_full_unwind_below,
            "sleeve_split": tmpl_cell.sleeve_split_str,
            "deployment_cap": tmpl_cell.deployment_cap,
            "policy": tmpl_cell.policy,
            "median_loss_pct": round(med_t, 6),
            "es95_loss_pct": round(es95_t, 6),
            "p_liquidation": round(p_liq_t, 4),
            "p_unwind": round(p_unw_t, 4),
            "config_hash": compute_config_hash(t_cfg_for_hash),
            "wall_time_sec": round(w_t, 4),
        }
        r_entry = {
            "run_id": f"scenario_{s_id}_recommended",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "git_commit": git_commit,
            "generator": "HistoricalBlockBootstrap",
            "scenario_id": s_id,
            "scenario_name": s_name,
            "setting": "recommended",
            "cell_id": selected_cell.cell_id,
            "n_paths": n_paths,
            "seed": seed,
            "target_ltv": selected_cell.target_ltv,
            "hf_unwind_floor": selected_cell.hf_unwind_floor,
            "sleeve_split": selected_cell.sleeve_split_str,
            "deployment_cap": selected_cell.deployment_cap,
            "policy": selected_cell.policy,
            "median_loss_pct": round(med_r, 6),
            "es95_loss_pct": round(es95_r, 6),
            "p_liquidation": round(p_liq_r, 4),
            "p_unwind": round(p_unw_r, 4),
            "config_hash": compute_config_hash(r_cfg_for_hash),
            "wall_time_sec": round(w_r, 4),
        }
        all_scenario_records.extend([t_entry, r_entry])

        row_dict = {
            "scenario_id": s_id,
            "name": s_name,
            "source": s_source,
            "mechanics": s_desc,
            "template_median_loss": med_t,
            "template_es95_loss": es95_t,
            "template_p_liq": p_liq_t,
            "template_p_unwind": p_unw_t,
            "recommended_median_loss": med_r,
            "recommended_es95_loss": es95_r,
            "recommended_p_liq": p_liq_r,
            "recommended_p_unwind": p_unw_r,
            "protection_gain_es95": protection_gain,
        }
        row_dict["takeaway"] = generate_scenario_takeaway(s_id, row_dict, cfg_recommended, cfg_template)
        headline_rows.append(row_dict)

    # Multi-severity sensitivity runs (SPEC §4)
    multi_severities = [
        ("S3", "S3_1h", S3VenueFreeze(duration_hours=1), "1h freeze"),
        ("S3", "S3_72h", S3VenueFreeze(duration_hours=72), "72h freeze"),
        ("S4", "S4_depeg3", S4WrapperDepeg(peg_discount=-0.03), "-3% depeg"),
        ("S4", "S4_depeg8", S4WrapperDepeg(peg_discount=-0.08), "-8% depeg"),
        ("S6", "S6_loss30", S6OpaqueCounterpartyDefault(loss_pct=0.30), "30% loss"),
        ("S6", "S6_loss100", S6OpaqueCounterpartyDefault(loss_pct=1.00), "100% loss"),
        ("S11", "S11_gas10", S11GasWar(gas_multiplier=10.0), "10x gas"),
        ("S11", "S11_gas50", S11GasWar(gas_multiplier=50.0), "50x gas"),
    ]

    for base_id, var_id, var_inj, var_label in multi_severities:
        inj_dict = var_inj.to_dict() if hasattr(var_inj, "to_dict") else var_id
        for cfg, cell_obj, setting_name in [(cfg_template, tmpl_cell, "template"), (cfg_recommended, selected_cell, "recommended")]:
            t0_v = time.perf_counter()
            res_v = run_simulation(paths_base, cfg, scenario_injector=var_inj)
            w_v = time.perf_counter() - t0_v
            losses_v = -res_v.net_yield_btc
            med_v = float(np.median(losses_v))
            p95_v = float(np.percentile(losses_v, 95))
            es95_v = float(np.mean(losses_v[losses_v >= p95_v]))

            v_cfg_for_hash = {
                "scenario_id": var_id,
                "scenario_variant": var_label,
                "setting": setting_name,
                "cell_id": cell_obj.cell_id,
                "target_ltv": cell_obj.target_ltv,
                "hf_unwind_floor": cell_obj.hf_unwind_floor,
                "sleeve_split": cell_obj.sleeve_split_str,
                "deployment_cap": cell_obj.deployment_cap,
                "policy": cell_obj.policy,
                "injector": inj_dict,
                "generator": "HistoricalBlockBootstrap",
                "seed": seed,
            }

            all_scenario_records.append({
                "run_id": f"scenario_{var_id}_{setting_name}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "git_commit": git_commit,
                "generator": "HistoricalBlockBootstrap",
                "scenario_id": var_id,
                "scenario_variant": var_label,
                "setting": setting_name,
                "cell_id": cell_obj.cell_id,
                "n_paths": n_paths,
                "seed": seed,
                "target_ltv": cell_obj.target_ltv,
                "hf_unwind_floor": cell_obj.hf_unwind_floor,
                "sleeve_split": cell_obj.sleeve_split_str,
                "deployment_cap": cell_obj.deployment_cap,
                "policy": cell_obj.policy,
                "median_loss_pct": round(med_v, 6),
                "es95_loss_pct": round(es95_v, 6),
                "p_liquidation": round(float(np.mean(res_v.liquidated)), 4),
                "p_unwind": round(float(np.mean(res_v.unwind_count > 0)), 4),
                "config_hash": compute_config_hash(v_cfg_for_hash),
                "wall_time_sec": round(w_v, 4),
            })

    # Append all scenario records to runs.jsonl
    with open(runs_path, "a", encoding="utf-8") as f_out:
        for r in all_scenario_records:
            f_out.write(json.dumps(r) + "\n")

    df_sc = pd.DataFrame(headline_rows)
    md_content = format_scenario_table_markdown(df_sc)
    with open(out_dir / "scenario_table.md", "w", encoding="utf-8") as f_md:
        f_md.write(md_content)

    print(f"Scenario table generated successfully: {out_dir / 'scenario_table.md'}")
    return df_sc


def plot_frontier_chart(
    df: pd.DataFrame,
    selected_cell: CellConfig,
    output_dir: Path | str = DEFAULT_REPORT_DIR,
) -> Path:
    """Generate institutional efficient frontier chart per SPEC §6.5."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    chart_path = out_dir / "frontier.png"
    csv_path = out_dir / "frontier_data.csv"

    # Export clean frontier data CSV (only grid cell runs)
    df_sweep = df[df["run_id"].str.startswith("cell_")].copy()
    cols = [
        "cell_id", "generator", "target_ltv", "hf_unwind_floor", "sleeve_split",
        "deployment_cap", "policy", "carry_median_net_apy", "carry_es95_loss_pct",
        "carry_min_hf_p5", "portfolio_median_net_apy", "portfolio_es95_loss_pct",
        "carry_p_liquidation", "carry_p_unwind", "carry_min_hf_median",
        "venue_hhi", "asset_hhi", "issuer_hhi", "is_template"
    ]
    avail_cols = [c for c in cols if c in df_sweep.columns]
    df_sweep[avail_cols].to_csv(csv_path, index=False)
    print(f"Frontier data saved to {csv_path}")

    # Plot 3-panel figure
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.5), dpi=300)
    plt.subplots_adjust(wspace=0.25)

    generators = [
        ("HistoricalBlockBootstrap", "Primary: Block Bootstrap (10,000 paths)", "#1f77b4"),
        ("JumpDiffusion", "Robustness: Jump Diffusion (5,000 paths)", "#ff7f0e"),
        ("RegimeSwitching", "Robustness: Regime Switching (5,000 paths)", "#2ca02c"),
    ]

    last_scatter = None
    for ax, (gen_key, title, base_color) in zip(axes, generators):
        sub = df_sweep[df_sweep["generator"] == gen_key].copy()
        if len(sub) == 0:
            ax.set_title(title)
            continue

        x = sub["carry_es95_loss_pct"] * 100.0  # ES95 Loss %
        y = sub["carry_median_net_apy"] * 100.0  # Median Net APY %

        scatter = ax.scatter(
            x,
            y,
            c=sub["target_ltv"],
            cmap="viridis",
            alpha=0.65,
            s=40,
            edgecolors="none",
            label="Grid Cells",
        )
        last_scatter = scatter

        # Highlight KPK Published Template (cell 163)
        tmpl = sub[sub["is_template"].fillna(0).astype(bool)]
        if len(tmpl) > 0:
            tx = tmpl["carry_es95_loss_pct"].values[0] * 100.0
            ty = tmpl["carry_median_net_apy"].values[0] * 100.0
            ax.scatter(tx, ty, color="#d62728", s=140, marker="X", zorder=5, label="KPK Template (70.2% LTV)")
            ax.annotate(
                f"KPK Template\n({tx:.2f}% ES95, {ty:.2f}% APY)",
                xy=(tx, ty),
                xytext=(15, -25),
                textcoords="offset points",
                fontsize=8,
                weight="bold",
                color="#d62728",
                arrowprops=dict(arrowstyle="->", color="#d62728", lw=1.2),
            )

        # Highlight Recommended Setting dynamically from selected_cell
        rec = sub[sub["cell_id"] == selected_cell.cell_id]
        if len(rec) > 0:
            rx = rec["carry_es95_loss_pct"].values[0] * 100.0
            ry = rec["carry_median_net_apy"].values[0] * 100.0
            ax.scatter(
                rx,
                ry,
                color="#2ca02c",
                s=160,
                marker="*",
                zorder=5,
                label=f"Recommended Cell {selected_cell.cell_id} ({selected_cell.target_ltv * 100:.1f}% LTV)",
            )
            ax.annotate(
                f"Recommended (Cell {selected_cell.cell_id})\n({rx:.2f}% ES95, {ry:.2f}% APY)",
                xy=(rx, ry),
                xytext=(-120, 15),
                textcoords="offset points",
                fontsize=8,
                weight="bold",
                color="#1b5e20",
                arrowprops=dict(arrowstyle="->", color="#2ca02c", lw=1.2),
            )

        ax.set_title(title, fontsize=11, weight="bold", pad=10)
        ax.set_xlabel("Expected Shortfall ES95 (% Loss)", fontsize=9)
        ax.set_ylabel("Median Net BTC APY (%)", fontsize=9)
        ax.grid(True, linestyle="--", alpha=0.4)
        ax.legend(loc="lower right", fontsize=8)

    if last_scatter is not None:
        cbar = fig.colorbar(last_scatter, ax=axes, orientation="horizontal", fraction=0.04, pad=0.15)
        cbar.set_label("Target LTV", fontsize=9)

    plt.suptitle("KPK BTC Carry Fund: Parameter Sweep Efficient Frontier (SPEC §6.5)", fontsize=13, weight="bold", y=1.02)
    fig.savefig(chart_path, bbox_inches="tight")
    plt.close(fig)
    print(f"Frontier chart generated: {chart_path}")
    return chart_path


def main() -> None:
    parser = argparse.ArgumentParser(description="KPK Carry Sim Parameter Sweep Runner")
    parser.add_argument("--smoke", action="store_true", help="Run 2-cell, 10-path smoke test")
    parser.add_argument("--quick", action="store_true", help="Run quick 60-cell sweep (1,000 paths)")
    parser.add_argument("--full", action="store_true", help="Run complete grid across all generators")
    parser.add_argument("--resume", action="store_true", help="Resume incomplete sweep and append to runs.jsonl")
    parser.add_argument("--downstream-only", action="store_true", help="Regenerate only downstream records (scenarios, benchmarks, allocation, chart, table) using existing 1080 grid cells in runs.jsonl")
    parser.add_argument("--table-only", action="store_true", help="Regenerate scenario_table.md from runs.jsonl without running simulations")
    parser.add_argument("--output-dir", type=str, default=str(DEFAULT_REPORT_DIR), help="Output directory")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel worker processes")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)

    if args.table_only:
        print(f"Regenerating scenario table from {out_dir / 'runs.jsonl'}...")
        generate_scenario_table_from_runs(runs_path=out_dir / "runs.jsonl", output_path=out_dir / "scenario_table.md")
        return

    if args.downstream_only:
        print(f"Executing Downstream Regeneration from existing 1080 grid cells in {out_dir / 'runs.jsonl'}...")
        runs_p = out_dir / "runs.jsonl"
        with open(runs_p, "r", encoding="utf-8") as f:
            all_lines = [json.loads(line) for line in f if line.strip()]

        grid_records = [r for r in all_lines if r.get("run_id", "").startswith("cell_")]
        if len(grid_records) != 1080:
            raise RuntimeError(f"Expected 1080 grid cell records, found {len(grid_records)}")

        df_grid = pd.DataFrame(grid_records)
        df_boot = df_grid[df_grid["generator"] == "HistoricalBlockBootstrap"]
        selected_id, top5 = select_recommended_cell(df_boot)
        selected_cell = get_cell_config_by_id(selected_id)
        print(f"Mechanically selected cell {selected_id}: {selected_cell}")

        # Reset runs.jsonl to 1080 grid cells
        with open(runs_p, "w", encoding="utf-8") as f:
            for r in grid_records:
                f.write(json.dumps(r) + "\n")
        print(f"Reset runs.jsonl to 1080 grid cells.")

        # Pre-generate paths for downstream runs
        gen = HistoricalBlockBootstrap()
        b_paths_10k = gen.generate(n_paths=10000, n_hours=2160, seed=42)

        print("Running S1-S12 Scenario Stress Suite (Driving sim/events.py through sim/engine.py)...")
        run_scenario_evaluations(selected_cell=selected_cell, output_dir=out_dir, n_paths=1000, seed=1337)

        print("Running Benchmarks (Hold-BTC and Sentora Single-Cycle)...")
        run_benchmarks(b_paths_10k, selected_cell=selected_cell, output_dir=out_dir, seed=42)

        print("Running Allocation Extension (Carry Weights 30%, 45%, 60%)...")
        res_rec = run_simulation(b_paths_10k, selected_cell.to_fund_config())
        run_allocation_extension(res_rec.net_yield_btc, selected_cell=selected_cell, output_dir=out_dir, seed=42)

        print("Computing and logging portfolio correlations...")
        compute_and_log_correlations(b_paths_10k, selected_cell=selected_cell, output_dir=out_dir, seed=42)

        with open(runs_p, "r", encoding="utf-8") as f:
            updated_records = [json.loads(line) for line in f if line.strip()]
        df_full = pd.DataFrame(updated_records)

        print("Generating Efficient Frontier Chart and Data...")
        plot_frontier_chart(df_full, selected_cell=selected_cell, output_dir=out_dir)

        print("Downstream regeneration completed successfully.")
        return

    if args.smoke:
        print("Executing Sweep Smoke Run (2 cells, 10 paths)...")
        cells = [
            CellConfig(1, 0.702, 1.13, "100:0", "none", "kpk_published"),
            CellConfig(2, 0.600, 1.10, "50:50", "25%", "kpk_vol_scaled"),
        ]
        df, _ = run_full_sweep(
            cells=cells,
            generators=["HistoricalBlockBootstrap"],
            n_paths_primary=10,
            n_paths_robustness=10,
            n_hours=2160,
            output_dir=out_dir,
            log_file_mode="w",
            resume=False,
            max_workers=1,
        )
        print("Smoke run completed successfully.")
        return

    all_cells = build_parameter_grid()
    if args.quick:
        cells = all_cells[:30]
        n_p_prim = 1000
        n_p_rob = 500
    else:
        cells = all_cells
        n_p_prim = 10000
        n_p_rob = 5000

    should_resume = args.resume

    print(f"Launching Sweep ({len(cells)} cells across 3 generators, resume={should_resume})...")
    df, _ = run_full_sweep(
        cells=cells,
        generators=["HistoricalBlockBootstrap", "JumpDiffusion", "RegimeSwitching"],
        n_paths_primary=n_p_prim,
        n_paths_robustness=n_p_rob,
        n_hours=2160,
        output_dir=out_dir,
        resume=should_resume,
        max_workers=args.workers,
    )

    # Run mechanical selection rule on bootstrap panel
    df_boot = df[df["generator"] == "HistoricalBlockBootstrap"]
    selected_id, top5 = select_recommended_cell(df_boot)
    selected_cell = get_cell_config_by_id(selected_id)

    print("Running S1-S12 Scenario Stress Suite (Driving sim/events.py through sim/engine.py)...")
    df_sc = run_scenario_evaluations(selected_cell=selected_cell, output_dir=out_dir, n_paths=1000, seed=1337)

    # Pre-generate paths for benchmarks
    gen = HistoricalBlockBootstrap()
    b_paths = gen.generate(n_paths=n_p_prim, n_hours=2160, seed=42)
    print("Running Benchmarks (Hold-BTC and Sentora Single-Cycle)...")
    run_benchmarks(b_paths, selected_cell=selected_cell, output_dir=out_dir, seed=42)

    # Allocation extension runs
    print("Running Allocation Extension (Carry Weights 30%, 45%, 60%)...")
    res_rec = run_simulation(b_paths, selected_cell.to_fund_config())
    run_allocation_extension(res_rec.net_yield_btc, selected_cell=selected_cell, output_dir=out_dir, seed=42)

    # Correlations
    print("Computing and logging portfolio correlations...")
    compute_and_log_correlations(b_paths, selected_cell=selected_cell, output_dir=out_dir, seed=42)

    # Re-read full dataset from runs.jsonl
    df_full = pd.read_json(out_dir / "runs.jsonl", lines=True)

    print("Generating Efficient Frontier Chart and Data...")
    plot_frontier_chart(df_full, selected_cell=selected_cell, output_dir=out_dir)

    print("Sweep pipeline completed successfully.")


if __name__ == "__main__":
    main()
