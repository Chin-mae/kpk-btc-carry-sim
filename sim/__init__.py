"""
KPK Carry Sim - Gauntlet-style Monte Carlo risk engine for institutional BTC carry.
Implements Phase 1 core engine per SPEC §3.1, §3.2, §3.3, §6, §7, §8, §9.
"""

from sim.agents import FundAgent, PolicyAction, ActionType
from sim.engine import run_simulation, SimulationResults, FundConfig
from sim.metrics import compute_metrics, format_metrics_table
from sim.prices import (
    HistoricalBlockBootstrap,
    JumpDiffusion,
    RegimeSwitching,
    get_generator,
)
from sim.rates import (
    AaveV3IRM,
    borrow_apy,
    supply_apy,
    diluted_apy,
    hourly_rate_from_apy,
)
from sim.venue import AavePoolState, ChainlinkOracle

__all__ = [
    "FundAgent",
    "PolicyAction",
    "ActionType",
    "run_simulation",
    "SimulationResults",
    "FundConfig",
    "compute_metrics",
    "format_metrics_table",
    "HistoricalBlockBootstrap",
    "JumpDiffusion",
    "RegimeSwitching",
    "get_generator",
    "AaveV3IRM",
    "borrow_apy",
    "supply_apy",
    "diluted_apy",
    "hourly_rate_from_apy",
    "AavePoolState",
    "ChainlinkOracle",
]
