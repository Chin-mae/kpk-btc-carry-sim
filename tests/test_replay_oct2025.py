"""
Automated Replay Validations per SPEC §7 and BRIEF.

Validation 1 (29 Oct 2026):
  Run recorded BTC hourly path for 28–30 Oct 2026 using Aave V4 venue row
  (LLTV 84.5%, target LTV 69.8%). With a freeze applied to a DIFFERENT venue
  (the real event: Binance hack freeze), the fund agent must achieve a full
  clean unwind with zero liquidation loss, matching KPK's real outcome
  (repaid 277,337 USDC, 4.925 WBTC returned — position closed whole).

Validation 2 (10 Oct 2025 liquidation arithmetic):
  At target LTV 70.2% on the recorded 10 Oct 2025 path with S1 cascade mechanics,
  the position's min HF must approach 1.0 within one hourly step of the theoretical
  point (17.4% fall). At target LTV 60% it must strictly survive.
"""

import numpy as np
import pytest

from sim.agents import (
    ActionType,
    ArbitrageurPopulation,
    BorrowerPopulation,
    DepositorPopulation,
    FundAgent,
    FundAgentPolicy,
    LiquidatorPopulation,
)
from sim.events import (
    load_replay_10oct2025_data,
    load_replay_29oct2026_data,
    load_scenario,
    ScenarioState,
)
from sim.venue import AavePoolState


# =====================================================================
# REPLAY VALIDATION 1: 29 OCT 2026 CLEAN UNWIND (SPEC §7)
# =====================================================================

def test_replay_1_29oct2026_binance_hack_clean_unwind():
    """Replay 1 (29 Oct 2026):

    Recorded prices for 28-30 Oct 2026.
    Aave V4 Bluechip row: LLTV 84.5%, Target LTV 69.8%.
    Position: 4.925 WBTC, 277,337 USDC debt.
    External freeze hits a DIFFERENT venue (Binance hack).
    Fund agent must achieve full clean unwind with zero liquidation loss,
    repaying 277,337 USDC and returning 4.925 WBTC whole.
    """
    prices, df = load_replay_29oct2026_data()
    assert len(prices) >= 72, f"Expected 72 hours of data, got {len(prices)}"

    # Initial position matching KPK published account:
    # 4.925 WBTC collateral borrowing on Aave V4 (LLTV 84.5%, target LTV 69.8%)
    initial_wbtc = 4.925
    initial_debt_usdc = 277_337.0
    deployed_usdc = 277_337.0
    lltv_v4 = 0.845
    target_ltv_v4 = 0.698

    p0 = prices[0]  # 80,633.87
    collat_val_0 = initial_wbtc * p0
    expected_debt_target = collat_val_0 * target_ltv_v4

    # Assert recorded debt 277,337 is within 0.1% of target LTV 69.8%
    assert abs(initial_debt_usdc - expected_debt_target) / expected_debt_target < 0.001

    pool = AavePoolState(
        collateral_btc=initial_wbtc,
        debt_usdc=initial_debt_usdc,
        lltv=lltv_v4,
        liquidation_bonus=0.05,
    )

    # Initial HF must equal ~1.21 (LLTV / target LTV = 0.845 / 0.698 = 1.2106...)
    initial_hf = pool.health_factor(p0)
    assert abs(initial_hf - 1.210) < 0.005, f"Initial HF {initial_hf} not ~1.21"

    policy = FundAgentPolicy.from_yaml()
    agent = FundAgent(policy)

    # Simulate hour-by-hour until 29 Oct 2026 event (hour 24)
    event_hour = 24  # 29 Oct 2026 ~00:00 UTC
    position_unwound = False
    repaid_usdc = 0.0
    returned_wbtc = 0.0
    liquidation_loss_btc = 0.0

    for t in range(len(prices)):
        p_t = prices[t]
        hf_t = pool.health_factor(p_t)

        # Trigger event: On 29 Oct 2026, an external venue freeze occurs (Binance hack)
        # Note: Freeze hits a DIFFERENT venue, NOT our Aave V4 borrowing venue!
        external_venue_frozen = (t >= event_hour)

        action = agent.evaluate(
            t=t,
            price_history=prices[: t + 1],
            current_hf=hf_t,
            venue_frozen_or_zero_ltv=external_venue_frozen,
        )

        if action.action_type == ActionType.FULL_UNWIND:
            # Fund agent executes clean unwind:
            # 1. Deployed USDC is retrieved (no loss)
            # 2. Debt of 277,337 USDC is repaid in full
            # 3. 4.925 WBTC collateral is returned whole
            repaid_usdc = pool.repay_debt(deployed_usdc)
            returned_wbtc = pool.collateral_btc
            liquidation_loss_btc = initial_wbtc - returned_wbtc
            position_unwound = True
            break

    # SPEC §7 and BRIEF Validations:
    assert position_unwound is True, "Fund agent failed to trigger full unwind on venue freeze"
    assert abs(repaid_usdc - 277_337.0) < 1e-4, f"Repaid debt {repaid_usdc} != 277,337 USDC"
    assert abs(returned_wbtc - 4.925) < 1e-6, f"Returned WBTC {returned_wbtc} != 4.925 WBTC"
    assert liquidation_loss_btc == 0.0, f"Liquidation loss {liquidation_loss_btc} > 0"
    assert pool.debt_usdc == 0.0, "Debt was not fully repaid"


# =====================================================================
# REPLAY VALIDATION 2: 10 OCT 2025 LIQUIDATION ARITHMETIC (SPEC §7)
# =====================================================================

def test_replay_2_liquidation_arithmetic_10oct2025_target_ltv_702():
    """Replay 2 (liquidation arithmetic on 10 Oct 2025 recorded path):

    At target LTV 70.2% and LLTV 85%, the theoretical liquidation point is:
      P_liq / P0 = target_ltv / lltv = 0.702 / 0.85 = 0.825882... (17.41% fall).
    On the recorded 10 Oct 2025 path with S1 cascade mechanics, min HF must
    approach 1.0 within one hourly step of the theoretical point (17.4% fall).
    """
    prices, df, t_theoretical = load_replay_10oct2025_data(include_cascade=True)
    p0 = prices[0]  # 121,814.96
    target_ltv = 0.702
    lltv = 0.85

    pool_702 = AavePoolState.from_entry(
        collateral_btc=10.0,
        entry_price=p0,
        target_ltv=target_ltv,
        lltv=lltv,
    )

    # Initial HF = 0.85 / 0.702 = 1.2108...
    assert abs(pool_702.health_factor(p0) - (lltv / target_ltv)) < 1e-6

    # Theoretical fall to liquidate
    theoretical_fall = 1.0 - (target_ltv / lltv)  # 0.1741176... (17.41%)
    assert abs(theoretical_fall - 0.174) < 0.005

    # Health factor series across the 48h path
    hf_series = np.array([pool_702.health_factor(p) for p in prices])

    # 1. At the theoretical point step (t_theoretical = hour 22), HF must approach 1.0 exactly
    hf_at_theoretical_point = hf_series[t_theoretical]
    assert abs(hf_at_theoretical_point - 1.000) < 0.005, (
        f"At theoretical point, HF {hf_at_theoretical_point} did not equal ~1.0"
    )

    # 2. Within one hourly step of the theoretical point:
    # Check t_theoretical - 1, t_theoretical, t_theoretical + 1
    hf_step_before = hf_series[t_theoretical - 1]
    hf_step_after = hf_series[t_theoretical + 1]

    assert abs(hf_step_before - 1.0) < 0.06, (
        f"One step before theoretical point, HF {hf_step_before} not within 0.06 of 1.0"
    )
    assert abs(hf_step_after - 1.0) < 0.06, (
        f"One step after theoretical point, HF {hf_step_after} not within 0.06 of 1.0"
    )

    # Minimum HF on the path must reach ~1.000
    min_hf = float(np.min(hf_series))
    assert abs(min_hf - 1.000) < 0.01


def test_replay_2_liquidation_arithmetic_10oct2025_target_ltv_600_survives():
    """Replay 2: At target LTV 60% on the exact same 10 Oct 2025 path, the position MUST survive."""
    prices, df, t_theoretical = load_replay_10oct2025_data(include_cascade=True)
    p0 = prices[0]
    conservative_ltv = 0.600
    lltv = 0.85

    pool_600 = AavePoolState.from_entry(
        collateral_btc=10.0,
        entry_price=p0,
        target_ltv=conservative_ltv,
        lltv=lltv,
    )

    # Initial HF at 60% LTV is 0.85 / 0.60 = 1.41667...
    assert abs(pool_600.health_factor(p0) - (0.85 / 0.60)) < 1e-6

    # Theoretical fall to liquidate 60% LTV would require 1 - 0.60/0.85 = 29.41%
    theoretical_fall_600 = 1.0 - (conservative_ltv / lltv)
    assert abs(theoretical_fall_600 - 0.2941) < 0.005

    # Health factor series across all hours
    hf_series = np.array([pool_600.health_factor(p) for p in prices])
    min_hf = float(np.min(hf_series))

    # At the exact step where 70.2% LTV approached 1.0, 60% LTV position has HF ~1.17
    hf_at_theoretical_step = hf_series[t_theoretical]
    expected_hf_at_17_4_fall = (1.0 - (1.0 - 0.702 / 0.85)) * (lltv / conservative_ltv)
    assert abs(hf_at_theoretical_step - expected_hf_at_17_4_fall) < 1e-5
    assert abs(hf_at_theoretical_step - 1.170) < 0.005

    # Position strictly survives: min HF remains well above 1.0 (at ~1.17)
    assert min_hf > 1.0, f"Conservative position unexpectedly liquidated with min HF {min_hf}"
    assert min_hf >= 1.15, f"Min HF {min_hf} dropped below conservative threshold"

    # Confirm is_liquidatable is False at every single step
    for t, p in enumerate(prices):
        assert not pool_600.is_liquidatable(p), f"Liquidatable at t={t}, p={p}"


# =====================================================================
# AGENT POPULATION INTERACTION TESTS (SPEC §3.3)
# =====================================================================

def test_liquidator_population_close_factor_and_profitability():
    """Verify liquidator population executes 50% close factor only when profitable."""
    pop = LiquidatorPopulation.create_default()
    entry_p = 100_000.0

    # Healthy position (HF ~1.21) -> None (no liquidation)
    healthy_pool = AavePoolState.from_entry(
        collateral_btc=1.0, entry_price=entry_p, target_ltv=0.702, lltv=0.85
    )
    assert pop.liquidate_position(
        position_collateral_btc=healthy_pool.collateral_btc,
        position_debt_usdc=healthy_pool.debt_usdc,
        market_price=entry_p,
        lltv=healthy_pool.lltv,
    ) is None

    # Unhealthy position (price falls 25% -> HF ~0.908 < 1.0)
    stressed_p = entry_p * 0.75
    res = pop.liquidate_position(
        position_collateral_btc=healthy_pool.collateral_btc,
        position_debt_usdc=healthy_pool.debt_usdc,
        market_price=stressed_p,
        lltv=healthy_pool.lltv,
        liquidation_bonus=0.05,
        current_gas_price_gwei=30.0,
    )
    assert res is not None
    assert res["executed"] is True
    # Close factor 50%: repaid debt must equal 50% of outstanding debt
    assert abs(res["repaid_debt_usdc"] - (healthy_pool.debt_usdc * 0.50)) < 1e-4
    assert res["net_profit_usd"] > 0.0

    # Under extreme gas war (1000 gwei), small position liquidation is skipped if unprofitable
    small_debt = 200.0  # $200 debt
    small_collat = small_debt / (stressed_p * 0.70)
    res_gas_war = pop.liquidate_position(
        position_collateral_btc=small_collat,
        position_debt_usdc=small_debt,
        market_price=stressed_p,
        lltv=0.85,
        current_gas_price_gwei=1000.0,  # Huge gas war
    )
    assert res_gas_war is None, "Liquidator should have skipped unprofitable small position during gas war"


def test_depositor_population_stress_utilization_crunch():
    """Verify depositor withdrawals drive vault utilisation to 100% and block withdrawals (EURC mechanism)."""
    depositors = DepositorPopulation(vault_tvl=100_000_000.0, vault_borrows=80_000_000.0)
    assert abs(depositors.utilisation - 0.80) < 1e-4
    assert depositors.is_withdrawal_blocked is False

    # Under severe stress regime (S7 EURC crunch), depositors withdraw available liquidity
    step_res = depositors.step(is_stress_regime=True, withdrawal_run_pct=1.0)
    assert abs(depositors.utilisation - 1.00) < 1e-4
    assert depositors.is_withdrawal_blocked is True
    assert step_res["is_withdrawal_blocked"] is True


def test_arbitrageur_population_halved_capital_under_hack():
    """Verify arbitrageur peg restoration speed halves under hack events (redemption halts)."""
    arbs = ArbitrageurPopulation(base_capital_usd=20_000_000.0, reversion_speed=0.20)
    depegged_ratio = 0.90  # 10% depeg

    # Normal recovery
    normal_ratio = arbs.step(current_peg_ratio=depegged_ratio, is_hack_event=False)
    normal_gain = normal_ratio - depegged_ratio

    # Hack event recovery (capital halves)
    hack_ratio = arbs.step(current_peg_ratio=depegged_ratio, is_hack_event=True)
    hack_gain = hack_ratio - depegged_ratio

    # Hack gain must be exactly 50% of normal gain
    assert abs(hack_gain - (normal_gain * 0.50)) < 1e-6
