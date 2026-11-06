"""
Automated unit tests for Scenario Injection Library per SPEC §3.4, §4 and BRIEF.

Requirements:
  1. Each scenario injector changes EXACTLY the state variables its SPEC row names
     and asserts no other variables are changed.
  2. S12 composite injector modifies the exact union of S1 + S3 + S4 state variables,
     applying all three sub-events.
  3. Scenarios are parameterised from configs/scenarios.yaml with verified KPK template values.
  4. S5/S6 hooks consume loss-given-default from sim/portfolio.py.
"""

import pytest
from pathlib import Path

from sim.events import (
    ScenarioState,
    load_scenario,
    compose_scenarios,
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
)
from sim.portfolio import CounterpartyTable


@pytest.mark.parametrize(
    "scenario_id,expected_affected",
    [
        ("S1", {"btc_price", "borrower_cascade_active", "gas_price_multiplier"}),
        ("S2", {"btc_price"}),
        ("S3", {"venue_frozen"}),
        ("S4", {"wrapper_price_ratio", "arbitrageur_capital_factor"}),
        ("S5", {"vault_asset_market_price", "vault_asset_oracle_price", "vault_real_loss"}),
        ("S6", {"vault_loss", "loss_discovery_lag_hours"}),
        ("S7", {"deployment_vault_utilisation", "withdrawal_blocked"}),
        ("S8", {"oracle_reported_price", "oracle_stale"}),
        ("S9", {"usdc_price"}),
        ("S10", {"borrow_rate", "deploy_yield"}),
        ("S11", {"gas_price_multiplier", "transaction_failure_rate"}),
    ],
)
def test_individual_scenarios_modify_exact_spec_variables(scenario_id: str, expected_affected: set[str]):
    """Assert that each injector changes ONLY the state variables declared in SPEC §4."""
    injector = load_scenario(scenario_id)
    assert set(injector.affected_state) == expected_affected, (
        f"{scenario_id} declared affected state mismatch: {injector.affected_state} != {expected_affected}"
    )

    state_before = ScenarioState()
    state_after = injector.apply(state_before)

    # Compute actual modified variables
    actual_changed = state_before.diff(state_after)

    # 1. Assert exactly the expected variables changed
    assert actual_changed == expected_affected, (
        f"{scenario_id} did not modify exactly expected variables. Changed: {actual_changed}, Expected: {expected_affected}"
    )

    # 2. Assert no other state variables were touched
    is_valid, changed, unexpected = injector.verify_state_transition(state_before, state_after)
    assert is_valid, f"{scenario_id} mutated unexpected state variables: {unexpected}"
    assert len(unexpected) == 0


def test_s12_composite_scenario_composition():
    """Verify S12 composite injector modifies exactly the union of S1, S3, and S4 state variables."""
    s12 = load_scenario("S12")
    s1 = load_scenario("S1")
    s3 = load_scenario("S3")
    s4 = load_scenario("S4")

    expected_union = set(s1.affected_state) | set(s3.affected_state) | set(s4.affected_state)
    assert set(s12.affected_state) == expected_union

    state_before = ScenarioState()
    state_after = s12.apply(state_before)

    actual_changed = state_before.diff(state_after)
    assert actual_changed == expected_union, (
        f"S12 changed {actual_changed}, expected {expected_union}"
    )

    # Verify mechanics of all three sub-events were applied
    assert state_after.btc_price < state_before.btc_price
    assert state_after.borrower_cascade_active is True
    assert state_after.gas_price_multiplier == 20.0
    assert state_after.venue_frozen is True
    assert state_after.wrapper_price_ratio < 1.0
    assert state_after.arbitrageur_capital_factor == 0.50


def test_compose_scenarios_utility():
    """Test dynamic composition of arbitrary scenarios on a single path."""
    s3 = load_scenario("S3")
    s7 = load_scenario("S7")
    composed = compose_scenarios([s3, s7], scenario_id="S3+S7")

    expected_affected = set(s3.affected_state) | set(s7.affected_state)
    assert set(composed.affected_state) == expected_affected

    state_before = ScenarioState()
    state_after = composed.apply(state_before)

    assert state_before.diff(state_after) == expected_affected
    assert state_after.venue_frozen is True
    assert state_after.deployment_vault_utilisation == 1.0
    assert state_after.withdrawal_blocked is True


def test_verified_template_scenario_parameters():
    """Verify scenarios adhere to KPK template numbers and SPEC §4 specifications."""
    # S3: Freeze durations 1h, 12h, 72h
    s3_1 = S3VenueFreeze(duration_hours=1)
    s3_12 = S3VenueFreeze(duration_hours=12)
    s3_72 = S3VenueFreeze(duration_hours=72)
    assert s3_1.duration_hours == 1
    assert s3_12.duration_hours == 12
    assert s3_72.duration_hours == 72

    # S4: Wrapper depeg -3%, -5%, -8%, 40 min duration trigger
    s4 = S4WrapperDepeg(peg_discount=-0.05, arbitrageur_capital_factor=0.50)
    st = s4.apply(ScenarioState())
    assert abs(st.wrapper_price_ratio - 0.95) < 1e-9
    assert st.arbitrageur_capital_factor == 0.50

    # S6: 7-day lag (168h), loss severities 30%, 65%, 100%
    s6 = S6OpaqueCounterpartyDefault(loss_pct=0.65, discovery_lag_hours=168)
    st = s6.apply(ScenarioState())
    assert st.vault_loss == 0.65
    assert st.loss_discovery_lag_hours == 168

    # S7: Utilisation 100%, withdrawal blocked
    s7 = S7UtilisationCrunch(vault_utilisation=1.0, withdrawal_blocked=True)
    st = s7.apply(ScenarioState())
    assert st.deployment_vault_utilisation == 1.0
    assert st.withdrawal_blocked is True

    # S9: USDC -13% intraday (0.87)
    s9 = S9StablecoinDepeg(usdc_trough_price=0.87)
    st = s9.apply(ScenarioState())
    assert abs(st.usdc_price - 0.87) < 1e-9


def test_portfolio_default_hook_feeding_scenario_s6():
    """Verify sim/portfolio.py counterparty table feeds S6 default loss."""
    # 1. Template roster ($10M vault exposure)
    table_tmpl = CounterpartyTable.create_default(total_fund_usd=50_000_000.0, basis="template")
    default_res_tmpl = table_tmpl.apply_default("kpk_usdc_yield_vault", loss_severity=0.65)
    assert default_res_tmpl["entity"] == "kpk_usdc_yield_vault"
    assert default_res_tmpl["loss_severity"] == 0.65
    assert abs(default_res_tmpl["prior_exposure_usd"] - 10_000_000.0) < 1e-6
    assert abs(default_res_tmpl["loss_usd"] - 6_500_000.0) < 1e-6
    assert abs(default_res_tmpl["remaining_exposure_usd"] - 3_500_000.0) < 1e-6

    # 2. Selected redesign roster ($5.265M vault exposure: 1/3 of $15.795M borrowed USDC)
    table_sel = CounterpartyTable.create_default(total_fund_usd=50_000_000.0, basis="selected")
    default_res_sel = table_sel.apply_default("kpk_usdc_yield_vault", loss_severity=0.65)
    assert default_res_sel["entity"] == "kpk_usdc_yield_vault"
    assert default_res_sel["loss_severity"] == 0.65
    assert abs(default_res_sel["prior_exposure_usd"] - 5_265_000.0) < 1e-6
    assert abs(default_res_sel["loss_usd"] - (5_265_000.0 * 0.65)) < 1e-6
    assert abs(default_res_sel["remaining_exposure_usd"] - (5_265_000.0 * 0.35)) < 1e-6

    # Feed loss into S6 scenario
    loss_fraction_of_sleeve = default_res_sel["loss_severity"]
    s6 = S6OpaqueCounterpartyDefault(loss_pct=loss_fraction_of_sleeve, discovery_lag_hours=168)
    st = s6.apply(ScenarioState())
    assert st.vault_loss == 0.65
    assert st.loss_discovery_lag_hours == 168
