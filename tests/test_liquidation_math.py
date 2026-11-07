"""
Hand-checkable liquidation arithmetic tests per SPEC §7 and BRIEF.

Test cases:
  1. At LTV 0.702 and LLTV 0.85, HF must equal 0.85 / 0.702 = 1.2108...
  2. Liquidation price fall from entry must be ~17.4% (assert within 0.5pp).
  3. At LTV 0.60 the same fall must NOT liquidate.
"""

import pytest
from sim.venue import AavePoolState


def test_hf_calculation_hand_check():
    """Case 1: At LTV 0.702 and LLTV 0.85, HF must equal 0.85 / 0.702 = 1.2108..."""
    target_ltv = 0.702
    lltv = 0.85
    entry_price = 60000.0
    collateral_btc = 10.0

    pool = AavePoolState.from_entry(
        collateral_btc=collateral_btc,
        entry_price=entry_price,
        target_ltv=target_ltv,
        lltv=lltv,
    )

    expected_hf = lltv / target_ltv  # 0.85 / 0.702 = 1.2108262...
    actual_hf = pool.health_factor(entry_price)

    assert abs(actual_hf - expected_hf) < 1e-7, f"Expected {expected_hf}, got {actual_hf}"
    assert abs(actual_hf - 1.2108) < 0.0001, f"HF should equal ~1.2108, got {actual_hf}"


def test_liquidation_price_fall_hand_check():
    """Case 2: Liquidation price fall from entry must be ~17.4% (assert within 0.5pp)."""
    target_ltv = 0.702
    lltv = 0.85
    entry_price = 100000.0
    collateral_btc = 5.0

    pool = AavePoolState.from_entry(
        collateral_btc=collateral_btc,
        entry_price=entry_price,
        target_ltv=target_ltv,
        lltv=lltv,
    )

    p_liq = pool.liquidation_price()
    expected_p_liq = entry_price * (target_ltv / lltv)  # 100000 * (0.702 / 0.85) = 82588.235...
    assert abs(p_liq - expected_p_liq) < 1e-4

    fall_pct = pool.liquidation_drop_pct(entry_price)
    expected_fall = 1.0 - (target_ltv / lltv)  # ~0.174118 (17.41%)

    # Assert within 0.5 percentage points (0.005) of 17.4% (0.174)
    assert abs(fall_pct - 0.174) <= 0.005, f"Fall pct {fall_pct:.4f} not within 0.5pp of 17.4%"
    assert abs(fall_pct - expected_fall) < 1e-6


def test_ltv_60_survives_same_fall():
    """Case 3: At LTV 0.60 the same fall must NOT liquidate."""
    entry_price = 100000.0
    collateral_btc = 10.0
    lltv = 0.85
    conservative_ltv = 0.60

    pool_60 = AavePoolState.from_entry(
        collateral_btc=collateral_btc,
        entry_price=entry_price,
        target_ltv=conservative_ltv,
        lltv=lltv,
    )

    # Initial HF at 60% LTV is 0.85 / 0.60 = 1.4167
    assert abs(pool_60.health_factor(entry_price) - (0.85 / 0.60)) < 1e-6

    # Apply the exact fall from Case 2 (~17.41%)
    fall_from_case_2 = 1.0 - (0.702 / 0.85)  # 0.1741176...
    stressed_price = entry_price * (1.0 - fall_from_case_2)  # ~82,588.24

    # At this stressed price, HF must remain strictly above 1.0
    hf_stressed = pool_60.health_factor(stressed_price)
    expected_hf_stressed = lltv / (conservative_ltv / (1.0 - fall_from_case_2))
    assert abs(hf_stressed - expected_hf_stressed) < 1e-6
    assert hf_stressed > 1.0, f"Position liquidated unexpectedly with HF {hf_stressed}"
    assert abs(hf_stressed - 1.170) < 0.005, f"Expected HF ~1.17, got {hf_stressed}"
    assert not pool_60.is_liquidatable(stressed_price)

    # Verify that a larger fall (e.g. 30%) DOES liquidate LTV 0.60
    # Liquidation threshold for 0.60 LTV is fall of 1 - 0.60/0.85 = 29.41%
    liquidating_price = entry_price * (1.0 - 0.30)
    assert pool_60.is_liquidatable(liquidating_price)
    assert pool_60.health_factor(liquidating_price) < 1.0
