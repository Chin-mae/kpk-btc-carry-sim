"""
Risk and distribution metrics layer for KPK Carry Sim.
Implements SPEC §6 (Outputs).

Calculates standard distribution summaries:
  - Median net yield (in BTC terms)
  - 5th and 95th percentiles (p5 / p95)
  - Probability of negative yield P(yield < 0)
  - Probability of liquidation P(liquidation)
  - Expected Shortfall at 95% confidence (ES95 / CVaR) in BTC terms
  - Unwind frequency and minimum health factor distributions
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from sim.engine import SimulationResults


def compute_metrics(
    results: SimulationResults | dict[str, Any],
) -> dict[str, float]:
    """Compute distribution summary metrics per SPEC §6.

    Args:
      results: SimulationResults object or dict with 'net_yield_btc', 'liquidated',
               'min_hf', 'unwind_count', and 'initial_btc'.

    Returns:
      Dictionary of risk and performance statistics.
    """
    if hasattr(results, "net_yield_btc"):
        yields = np.asarray(results.net_yield_btc, dtype=float)
        liquidated = np.asarray(results.liquidated, dtype=bool)
        min_hf = np.asarray(results.min_hf, dtype=float)
        unwind_count = np.asarray(results.unwind_count, dtype=int)
        init_btc = float(results.initial_btc)
    else:
        yields = np.asarray(results["net_yield_btc"], dtype=float)
        liquidated = np.asarray(results["liquidated"], dtype=bool)
        min_hf = np.asarray(results["min_hf"], dtype=float)
        unwind_count = np.asarray(results["unwind_count"], dtype=int)
        init_btc = float(results.get("initial_btc", 500.0))

    n_paths = len(yields)
    if n_paths == 0:
        return {}

    median_yield = float(np.median(yields))
    p5_yield = float(np.percentile(yields, 5))
    p95_yield = float(np.percentile(yields, 95))
    p_negative_yield = float(np.mean(yields < 0.0))
    p_liquidation = float(np.mean(liquidated))

    # Expected Shortfall (ES95 / CVaR) on loss distribution
    # Loss L = -yield (positive number indicates loss)
    losses = -yields
    loss_threshold_95 = float(np.percentile(losses, 95))
    tail_losses = losses[losses >= loss_threshold_95]
    es95_loss_pct = float(np.mean(tail_losses)) if len(tail_losses) > 0 else 0.0
    es95_loss_btc = es95_loss_pct * init_btc

    mean_unwinds = float(np.mean(unwind_count))
    p_unwind = float(np.mean(unwind_count > 0))
    min_hf_median = float(np.median(min_hf))
    min_hf_p5 = float(np.percentile(min_hf, 5))

    return {
        "median_net_yield": median_yield,
        "p5_net_yield": p5_yield,
        "p95_net_yield": p95_yield,
        "p_negative_yield": p_negative_yield,
        "p_liquidation": p_liquidation,
        "es95_loss_pct": es95_loss_pct,
        "es95_loss_btc": es95_loss_btc,
        "mean_unwind_count": mean_unwinds,
        "p_unwind": p_unwind,
        "min_hf_median": min_hf_median,
        "min_hf_p5": min_hf_p5,
        "n_paths": float(n_paths),
    }


def format_metrics_table(metrics: dict[str, float]) -> str:
    """Format simulation metrics into a clean markdown table."""
    rows = [
        ("Median Net Yield (BTC terms)", f"{metrics.get('median_net_yield', 0.0):.4%}"),
        ("5th Percentile Net Yield (p5)", f"{metrics.get('p5_net_yield', 0.0):.4%}"),
        ("95th Percentile Net Yield (p95)", f"{metrics.get('p95_net_yield', 0.0):.4%}"),
        ("P(Net Yield < 0)", f"{metrics.get('p_negative_yield', 0.0):.2%}"),
        ("P(Liquidation)", f"{metrics.get('p_liquidation', 0.0):.2%}"),
        ("ES95 (Worst 5% Loss, %)", f"{metrics.get('es95_loss_pct', 0.0):.4%}"),
        ("ES95 (Worst 5% Loss, BTC)", f"{metrics.get('es95_loss_btc', 0.0):.4f} BTC"),
        ("P(Policy Unwind)", f"{metrics.get('p_unwind', 0.0):.2%}"),
        ("Mean Unwinds per Path", f"{metrics.get('mean_unwind_count', 0.0):.2f}"),
        ("Min HF (Median)", f"{metrics.get('min_hf_median', 0.0):.4f}"),
        ("Min HF (5th Percentile)", f"{metrics.get('min_hf_p5', 0.0):.4f}"),
        ("Total Simulated Paths", f"{int(metrics.get('n_paths', 0)):,}"),
    ]

    header = "| Metric | Value |\n| :--- | :--- |\n"
    body = "\n".join(f"| {label} | {val} |" for label, val in rows)
    return header + body
