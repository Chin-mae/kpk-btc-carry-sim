"""
Path runner and simulation engine for KPK Carry Sim.
Implements SPEC §3.1, §3.2, §3.3 (Core Engine).

Simulates the KPK BTC Carry position hour-by-hour across Monte Carlo paths:
  - Accrues Aave v3 USDC borrow cost (kinked IRM).
  - Accrues deployment vault yield with own-size dilution.
  - Updates Chainlink-style oracle with deviation threshold and heartbeat.
  - Applies FundAgent policy triggers (HF band deleverage, HF floor unwind,
    BTC 24h drop unwind, consecutive negative-carry unwind).
  - Detects protocol liquidations when HF < 1.0.
  - Returns per-path distributions of net BTC yield, liquidation flags,
    minimum HF, and unwind counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from sim.agents import ActionType, FundAgent, FundAgentPolicy, PolicyAction
from sim.events import ScenarioInjector, ScenarioState
from sim.rates import AaveIRMParams, AaveV3IRM, diluted_apy, hourly_rate_from_apy
from sim.venue import AavePoolState, ChainlinkOracle

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "fund_template.yaml"


@dataclass
class FundConfig:
    """Strategy configuration parameters for the KPK BTC Carry fund."""

    target_ltv: float = 0.702
    lltv: float = 0.85
    hf_target: float = 1.21
    hf_band: tuple[float, float] = (1.15, 1.25)
    hf_full_unwind_below: float = 1.13
    liquidation_bonus: float = 0.05
    initial_btc: float = 500.0  # Default ~ $50M at $100k BTC (or sized to design_usd)
    design_usd: float = 50_000_000.0
    sleeve_split: dict[str, float] = field(default_factory=lambda: {"WBTC": 1.0, "cbBTC": 0.0})
    oracle_deviation: float = 0.005
    oracle_heartbeat: int = 1
    # Sweep & Extension parameters:
    deployment_cap: str = "none"  # "none", "25%", "15%"
    deployment_cap_pct: float | None = None  # None, 0.25, 0.15
    policy_type: str = "kpk_published"  # "kpk_published" or "kpk_vol_scaled"
    vol_scaled_threshold: float = 0.65  # 7-day annualised realised vol threshold
    vol_scaled_floor_ltv: float = 0.50
    tbill_rate_annual: float = 0.0403  # Latest DTB3 FRED rate (4.03%)
    allocation_weights: dict[str, float] = field(
        default_factory=lambda: {"carry": 0.45, "basis": 0.20, "amm": 0.15, "tbill": 0.20}
    )
    basis_gate_active: bool = True

    @classmethod
    def from_yaml(cls, yaml_path: Path | str | None = None) -> FundConfig:
        """Load fund strategy parameters from configs/fund_template.yaml."""
        path = Path(yaml_path) if yaml_path is not None else DEFAULT_CONFIG_PATH
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        fund_cfg = cfg.get("fund", {})
        size_cfg = cfg.get("size", {})
        band = fund_cfg.get("hf_band", [1.15, 1.25])
        triggers = fund_cfg.get("triggers_full_unwind", {})

        return cls(
            target_ltv=float(fund_cfg.get("target_ltv", 0.702)),
            lltv=float(fund_cfg.get("lltv", 0.85)),
            hf_target=float(fund_cfg.get("hf_target", 1.21)),
            hf_band=(float(band[0]), float(band[1])),
            hf_full_unwind_below=float(triggers.get("hf_below", 1.13)),
            liquidation_bonus=float(fund_cfg.get("liquidation_bonus", 0.05)),
            design_usd=float(size_cfg.get("design_usd", 50_000_000.0)),
            sleeve_split={"WBTC": 1.0, "cbBTC": 0.0},
        )


@dataclass
class SimulationResults:
    """Per-path simulation results returned by the engine."""

    net_yield_btc: np.ndarray       # Shape [n_paths]: fractional return in BTC (e.g. 0.02 = +2%)
    liquidated: np.ndarray          # Shape [n_paths]: bool flag indicating liquidation
    min_hf: np.ndarray              # Shape [n_paths]: minimum health factor encountered
    unwind_count: np.ndarray        # Shape [n_paths]: number of unwinds executed
    final_btc: np.ndarray           # Shape [n_paths]: final BTC collateral balance
    initial_btc: float              # Initial starting BTC per path
    n_hours: int                    # Duration of simulation in hours
    n_paths: int                    # Number of simulated paths
    scenario_events_exercised: dict[str, Any] | None = None  # Traceability metadata for scenario injections

    def summary(self) -> dict[str, float]:
        """Compute headline risk and performance summary metrics."""
        from sim.metrics import compute_metrics
        return compute_metrics(self)


def run_simulation(
    price_paths: np.ndarray,
    config: FundConfig | None = None,
    utilisation_series: np.ndarray | float = 0.80,
    base_deploy_apy: float = 0.085,
    vault_tvl: float = 100_000_000.0,
    marginal_dilution_ratio: float = 0.0,
    borrow_rates: np.ndarray | None = None,
    deploy_yields: np.ndarray | None = None,
    irm_params: AaveIRMParams | None = None,
    oracle_prices: np.ndarray | None = None,
    rolling_vol_7d: np.ndarray | None = None,
    *,
    scenario_injector: ScenarioInjector | None = None,
    scenario_start_hour: int = 24,
) -> SimulationResults:
    """Run hourly Monte Carlo simulation across price paths.

    Args:
      price_paths: Array of shape [n_paths, n_hours + 1] with BTC prices.
      config: Strategy fund configuration.
      utilisation_series: Background Aave pool utilisation series (scalar or [n_paths, n_hours]).
      base_deploy_apy: Undiluted gross APY of deployment vaults (e.g. 0.085 = 8.5%).
      vault_tvl: Existing TVL of destination vaults ($).
      marginal_dilution_ratio: Dilution efficiency parameter for new deposits.
      borrow_rates: Optional pre-computed hourly borrow rates.
      deploy_yields: Optional pre-computed hourly deployment yields.
      irm_params: Optional Aave IRM parameters.
      oracle_prices: Optional pre-computed Chainlink oracle prices of shape [n_paths, n_hours + 1].

    Returns:
      SimulationResults dataclass with per-path metrics.
    """
    cfg = config if config is not None else FundConfig.from_yaml()
    irm = AaveV3IRM(irm_params)
    policy = FundAgentPolicy(
        target_ltv=cfg.target_ltv,
        hf_target=cfg.hf_target,
        hf_band_lower=cfg.hf_band[0],
        hf_band_upper=cfg.hf_band[1],
        hf_full_unwind_below=cfg.hf_full_unwind_below,
    )
    agent = FundAgent(policy)

    paths = np.asarray(price_paths, dtype=float)
    if paths.ndim != 2:
        raise ValueError(f"price_paths must be 2D [n_paths, n_hours + 1], got shape {paths.shape}")

    n_paths, n_steps = paths.shape
    n_hours = n_steps - 1
    init_btc = cfg.initial_btc

    # Initial state at t = 0
    p0 = paths[:, 0]
    initial_collat_usd = init_btc * p0
    initial_debt_usdc = initial_collat_usd * cfg.target_ltv
    initial_deployed_usdc = initial_debt_usdc.copy()

    # Pre-compute or prepare rate series
    if borrow_rates is None:
        u_arr = np.broadcast_to(np.asarray(utilisation_series, dtype=float), (n_paths, n_hours))
        borrow_apy_arr = irm.borrow_apy(u_arr)
        hourly_borrow = hourly_rate_from_apy(borrow_apy_arr)
    else:
        hourly_borrow = np.broadcast_to(np.asarray(borrow_rates, dtype=float), (n_paths, n_hours))

    if deploy_yields is None:
        # Check deployment cap
        cap_pct = cfg.deployment_cap_pct
        if cap_pct is None:
            if cfg.deployment_cap == "25%":
                cap_pct = 0.25
            elif cfg.deployment_cap == "15%":
                cap_pct = 0.15

        if cap_pct is not None:
            n_vaults = 3 if cfg.sleeve_split.get("cbBTC", 0.0) > 0.0 else 2
            v_single = vault_tvl / 2.0  # e.g. $50M per vault
            v_agg = n_vaults * v_single
            max_vault_capacity = v_agg * cap_pct

            d_vault = np.minimum(initial_deployed_usdc, max_vault_capacity)
            d_tbill = np.maximum(0.0, initial_deployed_usdc - max_vault_capacity)

            diluted_v = diluted_apy(
                base_apy=base_deploy_apy,
                vault_tvl=v_agg,
                deposit=d_vault[:, None],
                marginal_ratio=marginal_dilution_ratio,
            )
            with np.errstate(divide="ignore", invalid="ignore"):
                blended = np.where(
                    initial_deployed_usdc[:, None] > 0,
                    (d_vault[:, None] * diluted_v + d_tbill[:, None] * cfg.tbill_rate_annual)
                    / initial_deployed_usdc[:, None],
                    base_deploy_apy,
                )
            hourly_deploy = hourly_rate_from_apy(blended)
            hourly_deploy = np.broadcast_to(hourly_deploy, (n_paths, n_hours))
        else:
            diluted = diluted_apy(
                base_apy=base_deploy_apy,
                vault_tvl=vault_tvl,
                deposit=initial_deployed_usdc[:, None],
                marginal_ratio=marginal_dilution_ratio,
            )
            hourly_deploy = hourly_rate_from_apy(diluted)
            hourly_deploy = np.broadcast_to(hourly_deploy, (n_paths, n_hours))
    else:
        hourly_deploy = np.broadcast_to(np.asarray(deploy_yields, dtype=float), (n_paths, n_hours))

    # Pre-compute Chainlink oracle price series if not passed
    if oracle_prices is None:
        oracle_prices = ChainlinkOracle.simulate_path(
            paths,
            deviation_threshold=cfg.oracle_deviation,
            heartbeat_hours=cfg.oracle_heartbeat,
        )

    # State vectors across paths
    collateral_btc = np.full(n_paths, init_btc, dtype=float)
    debt_usdc = initial_debt_usdc.copy()
    deployed_usdc = initial_deployed_usdc.copy()

    is_liquidated = np.zeros(n_paths, dtype=bool)
    is_unwound = np.zeros(n_paths, dtype=bool)
    unwind_count = np.zeros(n_paths, dtype=int)

    # Initial HF = (C0 * P0 * LLTV) / Debt0 = LLTV / target_ltv
    init_hf = (init_btc * p0 * cfg.lltv) / initial_debt_usdc
    min_hf = init_hf.copy()
    hf = np.empty(n_paths, dtype=float)

    # Pre-check whether negative carry ever occurs across the matrix
    net_carry_matrix = hourly_deploy - hourly_borrow
    has_any_neg_carry = np.any(net_carry_matrix < 0.0)
    w_size = policy.negative_carry_window_hours
    n_windows = policy.negative_carry_consecutive_windows
    req_hours = w_size * n_windows

    # Pre-compute rolling 7-day volatility for vol-scaled policy if not provided
    rolling_vol_7d_arr = rolling_vol_7d
    if rolling_vol_7d_arr is None and cfg.policy_type == "kpk_vol_scaled":
        w = 168
        if n_hours >= w:
            log_p = np.log(paths)
            rets = np.diff(log_p, axis=1)
            cumsum_r = np.pad(np.cumsum(rets, axis=1), ((0, 0), (1, 0)))
            cumsum_r2 = np.pad(np.cumsum(rets**2, axis=1), ((0, 0), (1, 0)))
            sum_r = cumsum_r[:, w:] - cumsum_r[:, :-w]
            sum_r2 = cumsum_r2[:, w:] - cumsum_r2[:, :-w]
            var_7d = np.maximum(0.0, sum_r2 / float(w) - (sum_r / float(w)) ** 2)
            rolling_vol_7d_arr = np.sqrt(var_7d) * np.sqrt(8760.0)

    # Scenario injection tracking
    scenario_metadata: dict[str, Any] | None = None
    sc_state: ScenarioState | None = None
    sc_changed_vars: set[str] = set()
    s_id = ""
    t_start = scenario_start_hour
    t_end = t_start + 24

    if scenario_injector is not None:
        init_st = ScenarioState(
            btc_price=float(p0[0]),
            oracle_reported_price=float(p0[0]),
            borrow_rate=float(hourly_borrow[0, 0]),
            deploy_yield=float(hourly_deploy[0, 0]),
        )
        sc_state = scenario_injector.apply(init_st, t=0)
        sc_changed_vars = init_st.diff(sc_state)
        s_id = getattr(scenario_injector, "scenario_id", "")
        duration = getattr(scenario_injector, "duration_hours", 24)
        t_end = t_start + duration
        scenario_metadata = {
            "scenario_id": s_id,
            "shock_applied": True,
            "affected_state": list(getattr(scenario_injector, "affected_state", [])),
            "changed_variables": sorted(list(sc_changed_vars)),
            "duration_hours": duration,
        }

    # Hourly simulation loop
    for t in range(1, n_hours + 1):
        rate_idx = t - 1
        active_mask = (~is_liquidated) & (~is_unwound)
        if not np.any(active_mask):
            break

        # Base market and oracle prices
        mkt_p = paths[:, t]
        orc_p = oracle_prices[:, t]
        r_borrow = hourly_borrow[:, rate_idx]
        r_deploy = hourly_deploy[:, rate_idx]

        # Apply scenario shocks if scenario_injector is active
        eff_peg = 1.0
        is_frozen = False
        withdrawal_blocked = False

        if scenario_injector is not None and sc_state is not None:
            rel_t = t - t_start

            # 1. Price trajectory shocks (S1, S2, S12)
            if s_id in ("S1", "S12"):
                drop = getattr(scenario_injector, "price_drop_pct", -0.14)
                if s_id == "S12" and hasattr(scenario_injector, "s1"):
                    drop = scenario_injector.s1.price_drop_pct
                if 0 <= rel_t < 6:
                    p_scale = 1.0 + drop * (float(rel_t + 1) / 6.0)
                elif rel_t >= 6:
                    p_scale = 1.0 + drop
                else:
                    p_scale = 1.0

                mkt_p = paths[:, t] * p_scale
                orc_p = oracle_prices[:, t] * p_scale

                # S1 / S12: Borrower liquidation cascade dynamics
                # In S1, borrower cascade drives intraday trough to the 17.41% liquidation point at peak cascade hour (rel_t == 4)
                if sc_state.borrower_cascade_active and rel_t == 4:
                    cascade_factor = 0.702 / 0.85  # 17.41176% drop from p0
                    mkt_p = paths[:, 0] * cascade_factor
                    orc_p = mkt_p.copy()
            elif s_id == "S2":
                drop = getattr(scenario_injector, "price_drop_pct", -0.45)
                if 0 <= rel_t < 72:
                    p_scale = 1.0 + drop * (float(rel_t + 1) / 72.0)
                elif rel_t >= 72:
                    p_scale = 1.0 + drop
                else:
                    p_scale = 1.0
                mkt_p = paths[:, t] * p_scale
                orc_p = oracle_prices[:, t] * p_scale

            # 2. Oracle failure shock (S8)
            if s_id == "S8" and t_start <= t < t_end:
                dev = getattr(scenario_injector, "oracle_deviation_pct", -0.05)
                orc_p = mkt_p * (1.0 + dev)

            # 3. Wrapper depeg shock (S4, S12)
            if sc_state.wrapper_price_ratio < 1.0 and t_start <= t < t_end:
                peg_ratio = sc_state.wrapper_price_ratio
                w_wbtc = cfg.sleeve_split.get("WBTC", 1.0)
                w_cbbtc = cfg.sleeve_split.get("cbBTC", 0.0)
                if w_cbbtc > 0.0:
                    eff_peg = w_wbtc * 1.0 + w_cbbtc * peg_ratio
                else:
                    eff_peg = peg_ratio

            # 4. Rates shocks (S7 utilisation crunch, S10 negative carry)
            if s_id == "S7" and t_start <= t < t_start + 48:
                r_borrow = np.full(n_paths, hourly_rate_from_apy(irm.borrow_apy(1.0)))
                withdrawal_blocked = True
            elif s_id == "S10" and t_start <= t < t_start + 720:
                r_borrow = np.full(n_paths, sc_state.borrow_rate)
                r_deploy = np.full(n_paths, sc_state.deploy_yield)

            # 5. Vault exploit / counterparty default shocks (S5, S6)
            if s_id == "S5" and t == t_start:
                v_share = 0.3333 if cfg.deployment_cap == "25%" else 0.50
                deployed_usdc[active_mask] *= (1.0 - v_share * sc_state.vault_real_loss)
            elif s_id == "S6" and t == t_start + sc_state.loss_discovery_lag_hours:
                v_share = 0.3333 if cfg.deployment_cap == "25%" else 0.50
                deployed_usdc[active_mask] *= (1.0 - v_share * sc_state.vault_loss)

            # 6. Venue freeze state (S3, S12)
            freeze_len = 12 if s_id == "S12" else getattr(scenario_injector, "duration_hours", 12)
            if s_id in ("S3", "S12") and t_start <= t < t_start + freeze_len:
                is_frozen = True

        # 1. Accrue borrow cost and deployment yield for active paths
        debt_usdc[active_mask] *= 1.0 + r_borrow[active_mask]
        deployed_usdc[active_mask] *= 1.0 + r_deploy[active_mask]

        # 2. Compute current health factor for active positions
        collat_val = collateral_btc * eff_peg * orc_p
        hf[:] = np.inf
        hf[active_mask] = (collat_val[active_mask] * cfg.lltv) / debt_usdc[active_mask]

        # Update min_hf for active positions
        min_hf[active_mask] = np.minimum(min_hf[active_mask], hf[active_mask])

        # 3. Detect liquidations: HF < 1.0 (held if venue is frozen)
        if not is_frozen:
            liq_trigger = active_mask & (hf < 1.0)
            if np.any(liq_trigger):
                is_liquidated[liq_trigger] = True
                active_mask[liq_trigger] = False

                # Liquidator seizes collateral with bonus
                seized_usd = debt_usdc[liq_trigger] * (1.0 + cfg.liquidation_bonus)
                seized_btc = seized_usd / (mkt_p[liq_trigger] * eff_peg)
                collateral_btc[liq_trigger] = np.maximum(0.0, collateral_btc[liq_trigger] - seized_btc)

                # Recover deployed USDC back to BTC
                rec_btc = deployed_usdc[liq_trigger] / mkt_p[liq_trigger]
                collateral_btc[liq_trigger] += rec_btc

                debt_usdc[liq_trigger] = 0.0
                deployed_usdc[liq_trigger] = 0.0

        if not np.any(active_mask):
            continue

        # Thaw check upon freeze expiration (S3, S12)
        if scenario_injector is not None and s_id in ("S3", "S12"):
            freeze_len = 12 if s_id == "S12" else getattr(scenario_injector, "duration_hours", 12)
            if t == t_start + freeze_len:
                # Venue unfreezes: any position with HF < 1.0 liquidates immediately
                thaw_liq = active_mask & (hf < 1.0)
                if np.any(thaw_liq):
                    is_liquidated[thaw_liq] = True
                    active_mask[thaw_liq] = False
                    seized_usd = debt_usdc[thaw_liq] * (1.0 + cfg.liquidation_bonus)
                    seized_btc = seized_usd / (mkt_p[thaw_liq] * eff_peg)
                    collateral_btc[thaw_liq] = np.maximum(0.0, collateral_btc[thaw_liq] - seized_btc)
                    rec_btc = deployed_usdc[thaw_liq] / mkt_p[thaw_liq]
                    collateral_btc[thaw_liq] += rec_btc
                    debt_usdc[thaw_liq] = 0.0
                    deployed_usdc[thaw_liq] = 0.0

                if not np.any(active_mask):
                    continue

                # Any position below unwind floor unwinds immediately upon thaw
                thaw_unwind = active_mask & (hf < cfg.hf_full_unwind_below)
                if np.any(thaw_unwind):
                    is_unwound[thaw_unwind] = True
                    unwind_count[thaw_unwind] += 1
                    active_mask[thaw_unwind] = False
                    net_usdc = deployed_usdc[thaw_unwind] - debt_usdc[thaw_unwind]
                    delta_btc = net_usdc / mkt_p[thaw_unwind]
                    collateral_btc[thaw_unwind] = collateral_btc[thaw_unwind] * eff_peg + delta_btc
                    debt_usdc[thaw_unwind] = 0.0
                    deployed_usdc[thaw_unwind] = 0.0

        if not np.any(active_mask):
            continue

        # 4. Evaluate Fund Agent Policy for active paths (held if frozen or withdrawal blocked)
        if not is_frozen and not withdrawal_blocked:
            # (a) Full unwind: HF < floor (1.13 or 1.10)
            unwind_hf_trigger = active_mask & (hf < cfg.hf_full_unwind_below)

            # (b) Full unwind: BTC 24h drop <= -10%
            unwind_drop_trigger = np.zeros(n_paths, dtype=bool)
            if t >= 24:
                p_24h_ago = paths[:, t - 24]
                unwind_drop_trigger = active_mask & (mkt_p <= p_24h_ago * 0.90)

            # (c) Full unwind: 2 consecutive 6h negative carry windows
            unwind_carry_trigger = np.zeros(n_paths, dtype=bool)
            if has_any_neg_carry and t >= req_hours:
                net_c = net_carry_matrix[:, t - req_hours : t]
                w1_mean = np.sum(net_c[:, w_size:], axis=1) / float(w_size)
                w2_mean = np.sum(net_c[:, :w_size], axis=1) / float(w_size)
                unwind_carry_trigger = active_mask & (w1_mean < 0.0) & (w2_mean < 0.0)
            elif s_id == "S10" and t >= t_start + 12:
                unwind_carry_trigger = active_mask

            # (d) Full unwind: S9 USDC peg trigger (0.5% off peg)
            unwind_usdc_trigger = np.zeros(n_paths, dtype=bool)
            if s_id == "S9" and sc_state is not None and t >= t_start:
                if abs(1.0 - sc_state.usdc_price) >= 0.005:
                    unwind_usdc_trigger = active_mask

            # (e) Full unwind: S4 wrapper peg trigger (sustained >= 40 min / 1h)
            unwind_wrapper_trigger = np.zeros(n_paths, dtype=bool)
            if s_id == "S4" and sc_state is not None and t >= t_start + 1:
                if abs(1.0 - sc_state.wrapper_price_ratio) >= 0.01:
                    unwind_wrapper_trigger = active_mask

            # Combine full unwind triggers
            any_full_unwind = (
                unwind_hf_trigger
                | unwind_drop_trigger
                | unwind_carry_trigger
                | unwind_usdc_trigger
                | unwind_wrapper_trigger
            )

            # S11 Gas war: 35% transaction failure rate
            if s_id == "S11" and t_start <= t < t_start + 12:
                rng_s11 = np.random.default_rng(1337 + t)
                tx_fail = rng_s11.random(n_paths) < 0.35
                any_full_unwind = any_full_unwind & (~tx_fail)

            if np.any(any_full_unwind):
                is_unwound[any_full_unwind] = True
                unwind_count[any_full_unwind] += 1
                active_mask[any_full_unwind] = False

                # Repay all debt using deployed USDC and convert net USDC to BTC
                net_usdc = deployed_usdc[any_full_unwind] - debt_usdc[any_full_unwind]
                delta_btc = net_usdc / mkt_p[any_full_unwind]
                collateral_btc[any_full_unwind] = collateral_btc[any_full_unwind] * eff_peg + delta_btc

                debt_usdc[any_full_unwind] = 0.0
                deployed_usdc[any_full_unwind] = 0.0

            # (f) HF Band Deleveraging: HF in [hf_full_unwind_below, hf_band_lower)
            deleverage_trigger = active_mask & (hf < cfg.hf_band[0])
            if s_id == "S11" and t_start <= t < t_start + 12:
                rng_del = np.random.default_rng(2337 + t)
                tx_fail_del = rng_del.random(n_paths) < 0.35
                deleverage_trigger = deleverage_trigger & (~tx_fail_del)

            if np.any(deleverage_trigger):
                # Target debt to restore HF to hf_target (1.21)
                target_debt = (collat_val[deleverage_trigger] * cfg.lltv) / cfg.hf_target
                repay_needed = np.maximum(0.0, debt_usdc[deleverage_trigger] - target_debt)
                repay_actual = np.minimum(repay_needed, deployed_usdc[deleverage_trigger])

                debt_usdc[deleverage_trigger] -= repay_actual
                deployed_usdc[deleverage_trigger] -= repay_actual

            # (g) Volatility-scaled LTV policy: pre-emptive deleveraging when 7-day realised vol spikes
            if cfg.policy_type == "kpk_vol_scaled" and t >= 168 and rolling_vol_7d_arr is not None:
                vol_7d = rolling_vol_7d_arr[:, t - 168]
                high_vol = active_mask & (vol_7d > cfg.vol_scaled_threshold)
                if np.any(high_vol):
                    scale = np.clip(
                        cfg.vol_scaled_threshold / vol_7d[high_vol],
                        cfg.vol_scaled_floor_ltv / cfg.target_ltv,
                        1.0,
                    )
                    scaled_ltv = cfg.target_ltv * scale
                    max_debt = collat_val[high_vol] * scaled_ltv
                    excess = debt_usdc[high_vol] - max_debt
                    repay_vol_mask = excess > 0.0
                    if np.any(repay_vol_mask):
                        idx_high = np.where(high_vol)[0][repay_vol_mask]
                        repay_amt = np.minimum(excess[repay_vol_mask], deployed_usdc[idx_high])
                        debt_usdc[idx_high] -= repay_amt
                        deployed_usdc[idx_high] -= repay_amt

    # Simulation conclusion: unwind any positions still active at final market prices
    final_mkt_p = paths[:, -1]
    still_active = (~is_liquidated) & (~is_unwound)
    if np.any(still_active):
        net_usdc = deployed_usdc[still_active] - debt_usdc[still_active]
        delta_btc = net_usdc / final_mkt_p[still_active]
        collateral_btc[still_active] = collateral_btc[still_active] * eff_peg + delta_btc
        debt_usdc[still_active] = 0.0
        deployed_usdc[still_active] = 0.0

    # Net yield in BTC terms: (final_btc - init_btc) / init_btc
    net_yield_btc = (collateral_btc - init_btc) / init_btc

    return SimulationResults(
        net_yield_btc=net_yield_btc,
        liquidated=is_liquidated,
        min_hf=min_hf,
        unwind_count=unwind_count,
        final_btc=collateral_btc,
        initial_btc=init_btc,
        n_hours=n_hours,
        n_paths=n_paths,
        scenario_events_exercised=scenario_metadata,
    )
