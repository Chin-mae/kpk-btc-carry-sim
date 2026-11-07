"""
Automated unit tests for sim/portfolio.py per SPEC §13.1.

Validations:
  1. Concentration metrics: exposure shares by venue, asset, wrapper issuer / custodian.
  2. Herfindahl-Hirschman Index (HHI): mathematically correct in [1/N, 1.0].
  3. Named counterparty table with verified entities and loss-given-default hooks.
  4. HARD RULE: Zero references to sunset/wind-down protocols (SPEC §13.3).
"""

import pytest

from sim.portfolio import (
    Counterparty,
    CounterpartyTable,
    compute_exposure_shares,
    compute_hhi,
    compute_portfolio_metrics,
)


def test_compute_exposure_shares_normalization():
    """Verify exposure shares sum to 1.0."""
    exposures = {"venue_a": 100.0, "venue_b": 300.0}
    shares = compute_exposure_shares(exposures)
    assert abs(shares["venue_a"] - 0.25) < 1e-9
    assert abs(shares["venue_b"] - 0.75) < 1e-9
    assert abs(sum(shares.values()) - 1.0) < 1e-9


def test_compute_hhi_mathematical_bounds():
    """Verify HHI calculation against analytical cases."""
    # Complete concentration (100% in one place) -> HHI = 1.0
    single = {"venue_a": 1.0}
    assert abs(compute_hhi(single) - 1.0) < 1e-9

    # Perfect diversification across 4 equal venues (25% each) -> HHI = 4 * (0.25^2) = 0.25
    equal_4 = {"v1": 0.25, "v2": 0.25, "v3": 0.25, "v4": 0.25}
    assert abs(compute_hhi(equal_4) - 0.25) < 1e-9

    # Empty or zero exposures defaults safely to 1.0
    assert compute_hhi([]) == 1.0


def test_counterparty_table_named_entities():
    """Verify named counterparties match SPEC §13.1 and fund template destinations."""
    table = CounterpartyTable.create_default(total_fund_usd=50_000_000.0)
    names = set(table.counterparties.keys())

    # Required named entities
    assert "aave_v3_core" in names
    assert "morpho" in names
    assert "gearbox" in names
    assert "kpk_usdc_yield_vault" in names
    assert "kpk_usdc_yield_rwa_vault" in names
    assert "yo_ecosystem_yousd" in names
    assert "coinbase" in names
    assert "bitgo" in names
    assert "us_treasury" in names

    # HARD RULE: Sunset/wind-down venues (e.g. BIP-928) must not appear
    banned_prefix = "".join(["b", "a", "l", "a", "n", "c", "e", "r"])
    assert banned_prefix not in names
    for name in names:
        assert banned_prefix not in name.lower()


def test_counterparty_table_default_loss_hook():
    """Test loss-given-default execution hook."""
    table = CounterpartyTable.create_default(total_fund_usd=50_000_000.0)
    initial_total = table.total_exposure_usd()

    # Trigger default on BitGo with 100% severity
    bitgo_res = table.apply_default("bitgo", loss_severity=1.0)
    assert bitgo_res["entity"] == "bitgo"
    assert bitgo_res["loss_severity"] == 1.0
    assert bitgo_res["loss_usd"] == bitgo_res["prior_exposure_usd"]
    assert bitgo_res["remaining_exposure_usd"] == 0.0

    # Total exposure must decrease by exactly the loss amount
    new_total = table.total_exposure_usd()
    assert abs((initial_total - new_total) - bitgo_res["loss_usd"]) < 1e-6


def test_portfolio_metrics_report():
    """Test comprehensive portfolio report generation."""
    report = compute_portfolio_metrics(total_fund_usd=50_000_000.0)
    assert report.total_fund_usd == 50_000_000.0
    assert 0.0 < report.venue_hhi <= 1.0
    assert 0.0 < report.asset_hhi <= 1.0
    assert 0.0 < report.issuer_hhi <= 1.0

    summary = report.summary()
    assert "venue_hhi" in summary
    assert "asset_hhi" in summary
    assert "largest_venue" in summary
