"""
KPK Carry Sim - Gauntlet-style Monte Carlo risk engine for institutional BTC carry.
Implements Phase 1 core engine and Phase 2 agent populations, event library, and portfolio risk.
"""

from sim.agents import (
    ActionType,
    ArbitrageurPopulation,
    BorrowerPopulation,
    DepositorPopulation,
    FundAgent,
    FundAgentPolicy,
    LiquidatorAgent,
    LiquidatorPopulation,
    PolicyAction,
)
from sim.engine import FundConfig, SimulationResults, run_simulation
from sim.events import (
    ScenarioInjector,
    ScenarioState,
    compose_scenarios,
    load_replay_10oct2025_data,
    load_replay_29oct2026_data,
    load_scenario,
)
from sim.metrics import compute_metrics, format_metrics_table
from sim.portfolio import (
    Counterparty,
    CounterpartyTable,
    PortfolioRiskReport,
    compute_exposure_shares,
    compute_hhi,
    compute_portfolio_metrics,
)
from sim.prices import (
    HistoricalBlockBootstrap,
    JumpDiffusion,
    RegimeSwitching,
    get_generator,
)
from sim.rates import (
    AaveV3IRM,
    borrow_apy,
    diluted_apy,
    hourly_rate_from_apy,
    supply_apy,
)
from sim.venue import AavePoolState, ChainlinkOracle

__all__ = [
    # Agents
    "FundAgent",
    "FundAgentPolicy",
    "PolicyAction",
    "ActionType",
    "LiquidatorAgent",
    "LiquidatorPopulation",
    "BorrowerPopulation",
    "DepositorPopulation",
    "ArbitrageurPopulation",
    # Events & Replay
    "ScenarioState",
    "ScenarioInjector",
    "load_scenario",
    "compose_scenarios",
    "load_replay_29oct2026_data",
    "load_replay_10oct2025_data",
    # Portfolio
    "Counterparty",
    "CounterpartyTable",
    "PortfolioRiskReport",
    "compute_exposure_shares",
    "compute_hhi",
    "compute_portfolio_metrics",
    # Engine & Metrics
    "run_simulation",
    "SimulationResults",
    "FundConfig",
    "compute_metrics",
    "format_metrics_table",
    # Prices & Rates & Venue
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
