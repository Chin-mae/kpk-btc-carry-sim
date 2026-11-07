"""
Look-ahead bias verification tests per SPEC §1, §7, §8 and BRIEF.

Requirement:
  Truncating the price and rate series after hour T must NOT change
  any Fund Agent decision at or before hour T.
"""

import numpy as np
import pytest

from sim.agents import ActionType, FundAgent, FundAgentPolicy
from sim.prices import HistoricalBlockBootstrap, JumpDiffusion


def test_agent_decision_truncation_invariance():
    """Verify that truncating input series at T produces identical decisions for all t <= T."""
    gen = JumpDiffusion()
    # Generate 300 hours of price data
    paths = gen.generate(n_paths=5, n_hours=300, seed=42, s0=60000.0)
    full_prices = paths[0]  # Single path of 301 price points

    # Synthesize borrow and deploy rates
    borrow_rates = np.full(301, 0.055 / 8760.0)
    deploy_rates = np.full(301, 0.085 / 8760.0)

    # Induce negative carry after hour 120
    borrow_rates[120:] = 0.20 / 8760.0

    policy = FundAgentPolicy.from_yaml()
    agent = FundAgent(policy)

    # Test multiple truncation horizons T
    test_horizons = [10, 24, 50, 100, 150, 200]

    for T in test_horizons:
        # Series truncated at T (length T + 1)
        truncated_prices = full_prices[: T + 1]
        truncated_borrow = borrow_rates[: T + 1]
        truncated_deploy = deploy_rates[: T + 1]

        for t in range(T + 1):
            # Compute mock HF at hour t based on price change from t=0
            p_t = full_prices[t]
            p0 = full_prices[0]
            current_hf = (0.85 / 0.702) * (p_t / p0)

            # Evaluation with full series (evaluated strictly at hour t)
            action_full = agent.evaluate(
                t=t,
                price_history=full_prices,
                current_hf=current_hf,
                borrow_rate_history=borrow_rates,
                deploy_rate_history=deploy_rates,
            )

            # Evaluation with truncated series
            action_trunc = agent.evaluate(
                t=t,
                price_history=truncated_prices,
                current_hf=current_hf,
                borrow_rate_history=truncated_borrow,
                deploy_rate_history=truncated_deploy,
            )

            # Assert complete equality
            assert action_full.action_type == action_trunc.action_type, (
                f"Mismatch at t={t} for T={T}: {action_full.action_type} != {action_trunc.action_type}"
            )
            assert abs(action_full.repay_fraction - action_trunc.repay_fraction) < 1e-9, (
                f"Repay fraction mismatch at t={t} for T={T}"
            )
            assert action_full.reason == action_trunc.reason, (
                f"Reason mismatch at t={t} for T={T}"
            )


def test_future_crash_cannot_influence_prior_decision():
    """A catastrophic flash crash at T+1 must have zero effect on agent action at T."""
    policy = FundAgentPolicy.from_yaml()
    agent = FundAgent(policy)

    base_prices = np.full(50, 100000.0)
    current_hf = 1.21

    # Normal state up to hour 30
    action_before_crash = agent.evaluate(
        t=30,
        price_history=base_prices[:31],
        current_hf=current_hf,
    )
    assert action_before_crash.action_type == ActionType.HOLD

    # Inject 50% crash at hour 31 in extended series
    crashed_prices = base_prices.copy()
    crashed_prices[31:] = 50000.0

    # Decision at hour 30 with future crash present in array
    action_with_future_crash = agent.evaluate(
        t=30,
        price_history=crashed_prices,
        current_hf=current_hf,
    )

    assert action_with_future_crash.action_type == ActionType.HOLD
    assert action_with_future_crash.action_type == action_before_crash.action_type
    assert action_with_future_crash.reason == action_before_crash.reason

    # But at hour 31, the crash DOES trigger full unwind
    action_at_crash = agent.evaluate(
        t=31,
        price_history=crashed_prices,
        current_hf=0.60,  # Stressed HF
    )
    assert action_at_crash.action_type == ActionType.FULL_UNWIND
    assert action_at_crash.reason in ["hf_below_floor", "btc_24h_drop"]


def test_future_negative_carry_cannot_influence_prior_decision():
    """Future negative carry at T+1 must not trigger negative carry unwind at T."""
    policy = FundAgentPolicy.from_yaml()
    agent = FundAgent(policy)

    prices = np.full(50, 100000.0)
    # 20 hours of positive carry
    borrow_rates = np.full(50, 0.05 / 8760.0)
    deploy_rates = np.full(50, 0.08 / 8760.0)

    # Hour 20 decision
    action_before = agent.evaluate(
        t=20,
        price_history=prices,
        current_hf=1.21,
        borrow_rate_history=borrow_rates[:21],
        deploy_rate_history=deploy_rates[:21],
    )
    assert action_before.action_type == ActionType.HOLD

    # Inject massive negative carry from hour 21 onwards
    borrow_rates_future_spike = borrow_rates.copy()
    borrow_rates_future_spike[21:] = 0.50 / 8760.0  # 50% borrow APY

    action_with_future_spike = agent.evaluate(
        t=20,
        price_history=prices,
        current_hf=1.21,
        borrow_rate_history=borrow_rates_future_spike,
        deploy_rate_history=deploy_rates,
    )

    assert action_with_future_spike.action_type == ActionType.HOLD
    assert action_with_future_spike.reason == action_before.reason
