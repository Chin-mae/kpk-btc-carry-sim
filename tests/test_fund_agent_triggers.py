"""
Automated unit tests for extended verified Fund Agent triggers.
Tests verified trigger conditions from configs/fund_template.yaml:
  1. Wrapper peg off 1% sustained 40 minutes -> full unwind.
  2. USDC 0.5% off peg -> full unwind.
  3. Exploit detection on venue or oracle -> full unwind.
  4. Venue freezes reserve or sets LTV to 0 -> full unwind.
  5. Venue changes price source -> full unwind.
  6. Custodian WBTC multisig threshold set to 1 -> full unwind.
  7. Custodian cbBTC owner/proxy/pauser changed -> full unwind.
  8. VAULT-EXIT triggers: vault curator changes -> VAULT_EXIT (partial action, not full unwind).
  9. VAULT-EXIT triggers: vault share price drops > 1bp/min -> VAULT_EXIT (partial action, not full unwind).
"""

import numpy as np
import pytest

from sim.agents import ActionType, FundAgent, FundAgentPolicy


def test_wrapper_peg_trigger_sustained():
    """Wrapper peg off >= 1% sustained >= 40 minutes must trigger full unwind."""
    agent = FundAgent()
    prices = np.full(10, 100_000.0)

    # Peg ratio 0.985 (1.5% off peg) sustained for 45 minutes
    peg_hist = np.full(10, 0.985)
    action = agent.evaluate(
        t=5,
        price_history=prices,
        current_hf=1.21,
        wrapper_peg_history=peg_hist,
        wrapper_peg_duration_min_observed=45,
    )
    assert action.action_type == ActionType.FULL_UNWIND
    assert action.reason == "wrapper_peg_off_sustained"

    # Same depeg but only sustained for 15 minutes (< 40 min) -> HOLD
    action_brief = agent.evaluate(
        t=5,
        price_history=prices,
        current_hf=1.21,
        wrapper_peg_history=peg_hist,
        wrapper_peg_duration_min_observed=15,
    )
    assert action_brief.action_type == ActionType.HOLD


def test_usdc_peg_trigger():
    """USDC 0.5% off peg must trigger full unwind immediately."""
    agent = FundAgent()
    prices = np.full(5, 100_000.0)

    # USDC trades at $0.994 (0.6% off peg)
    usdc_hist = np.array([1.0, 1.0, 1.0, 0.994, 0.994])
    action = agent.evaluate(
        t=3,
        price_history=prices,
        current_hf=1.21,
        usdc_peg_history=usdc_hist,
    )
    assert action.action_type == ActionType.FULL_UNWIND
    assert action.reason == "usdc_peg_off_pct"

    # USDC at $0.998 (0.2% off peg < 0.5%) -> HOLD
    usdc_normal = np.full(5, 0.998)
    action_normal = agent.evaluate(
        t=3,
        price_history=prices,
        current_hf=1.21,
        usdc_peg_history=usdc_normal,
    )
    assert action_normal.action_type == ActionType.HOLD


def test_security_and_venue_triggers():
    """Exploit detection, venue freeze, and oracle change triggers."""
    agent = FundAgent()
    prices = np.full(5, 100_000.0)

    # Exploit detected
    act1 = agent.evaluate(t=1, price_history=prices, current_hf=1.21, exploit_detected=True)
    assert act1.action_type == ActionType.FULL_UNWIND
    assert act1.reason == "exploit_detected_on_venue_or_oracle"

    # Venue freeze / zero LTV
    act2 = agent.evaluate(t=1, price_history=prices, current_hf=1.21, venue_frozen_or_zero_ltv=True)
    assert act2.action_type == ActionType.FULL_UNWIND
    assert act2.reason == "venue_freezes_reserve_or_zeroes_ltv"

    # Venue price source change
    act3 = agent.evaluate(t=1, price_history=prices, current_hf=1.21, venue_changed_price_source=True)
    assert act3.action_type == ActionType.FULL_UNWIND
    assert act3.reason == "venue_changes_price_source"


def test_custodian_contract_triggers():
    """Custodian triggers: WBTC multisig threshold to 1, cbBTC proxy/pauser change."""
    agent = FundAgent()
    prices = np.full(5, 100_000.0)

    # WBTC fund: multisig threshold set to 1
    act_wbtc = agent.evaluate(
        t=1,
        price_history=prices,
        current_hf=1.21,
        custodian_multisig_threshold_to_1=True,
        asset_type="WBTC",
    )
    assert act_wbtc.action_type == ActionType.FULL_UNWIND
    assert act_wbtc.reason == "wbtc_custodian_multisig_threshold_set_to_1"

    # cbBTC fund: owner/proxy changed
    act_cbbtc = agent.evaluate(
        t=1,
        price_history=prices,
        current_hf=1.21,
        custodian_owner_or_proxy_changed=True,
        asset_type="cbBTC",
    )
    assert act_cbbtc.action_type == ActionType.FULL_UNWIND
    assert act_cbbtc.reason == "cbbtc_owner_proxy_pauser_change_or_upgrade"


def test_vault_exit_partial_triggers():
    """VAULT-EXIT triggers: partial action exiting specific vault, NOT a full unwind."""
    agent = FundAgent()
    prices = np.full(5, 100_000.0)

    # 1. Curator changed on kpk_usdc_yield_vault
    act_curator = agent.evaluate(
        t=1,
        price_history=prices,
        current_hf=1.21,
        vault_curator_changed={"kpk_usdc_yield_vault": True},
    )
    assert act_curator.action_type == ActionType.VAULT_EXIT
    assert act_curator.vault_name == "kpk_usdc_yield_vault"
    assert act_curator.reason == "vault_owner_or_curator_changes"
    # Must NOT be a full unwind
    assert act_curator.repay_fraction == 0.0

    # 2. Vault share price drop > 1 bp in a minute
    act_drop = agent.evaluate(
        t=1,
        price_history=prices,
        current_hf=1.21,
        vault_share_price_drop_bp={"kpk_usdc_yield_rwa_vault": 1.8},
    )
    assert act_drop.action_type == ActionType.VAULT_EXIT
    assert act_drop.vault_name == "kpk_usdc_yield_rwa_vault"
    assert act_drop.reason == "vault_share_price_drop_bp_per_min"
    assert act_drop.trigger_value == 1.8
    assert act_drop.repay_fraction == 0.0
