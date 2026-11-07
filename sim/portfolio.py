"""
Portfolio risk module and counterparty concentration metrics per SPEC §13.1.

Computes:
  1. Concentration metrics: exposure shares by venue, asset, wrapper issuer / custodian.
  2. Herfindahl-Hirschman Index (HHI): sum of squared exposure shares (1.0 = full concentration).
  3. Named counterparty table: protocols, deployment vaults, issuers, custodians,
     and loss-given-default (LGD) hooks feeding S5/S6-style scenario defaults.

HARD RULE (SPEC §13.3):
  The BIP-928 wind-down protocol is permanently excluded from active portfolio construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "fund_template.yaml"


def compute_exposure_shares(exposures: dict[str, float]) -> dict[str, float]:
    """Normalize exposure values to fractional portfolio shares summing to 1.0."""
    total = sum(max(0.0, float(v)) for v in exposures.values())
    if total <= 0.0:
        n = len(exposures)
        return {k: (1.0 / n if n > 0 else 0.0) for k in exposures}
    return {k: max(0.0, float(v)) / total for k, v in exposures.items()}


def compute_hhi(shares: dict[str, float] | Sequence[float]) -> float:
    """Compute Herfindahl-Hirschman Index (HHI) for a portfolio distribution.

    Args:
      shares: Dict of shares or sequence of fractional shares summing to 1.0.

    Returns:
      HHI as a decimal in [1/N, 1.0]. Full concentration is 1.0.
    """
    if isinstance(shares, dict):
        vals = list(shares.values())
    else:
        vals = list(shares)

    arr = np.asarray(vals, dtype=float)
    total = np.sum(arr)
    if total <= 0.0:
        return 1.0
    norm_shares = arr / total
    return float(np.sum(norm_shares**2))


@dataclass
class Counterparty:
    """Named counterparty entity with exposure cap and loss-given-default parameters."""

    name: str
    category: str  # "lending_protocol", "curation_protocol", "deployment_vault", "asset_issuer", "custodian", "perp_venue", "treasury"
    current_exposure_usd: float = 0.0
    exposure_cap_usd: float = 25_000_000.0
    loss_given_default: float = 1.00  # Default LGD [0, 1]

    @property
    def utilization(self) -> float:
        """Fraction of exposure cap utilized."""
        if self.exposure_cap_usd <= 0.0:
            return 1.0 if self.current_exposure_usd > 0 else 0.0
        return self.current_exposure_usd / self.exposure_cap_usd


@dataclass
class CounterpartyTable:
    """Table of named institutional counterparties per SPEC §13.1.

    Destinations and issuers sourced from configs/fund_template.yaml:
      - Lending & borrow venues: Aave v3 Core, Morpho, Gearbox
      - Deployment vaults: kpk_usdc_yield_vault, kpk_usdc_yield_rwa_vault, yo_ecosystem_yousd
      - Wrapper issuers / custodians: BitGo (WBTC), Coinbase (cbBTC)
      - Perp venue: Deribit / Binance (basis sleeve)
      - T-bill issuer: US Treasury (DTB3)
    """

    counterparties: dict[str, Counterparty] = field(default_factory=dict)

    @classmethod
    def create_default(
        cls,
        total_fund_usd: float = 50_000_000.0,
        yaml_path: Path | str | None = None,
        basis: str = "selected",
    ) -> CounterpartyTable:
        """Create standard counterparty roster initialized to fund destinations.
        
        Args:
            total_fund_usd: Total fund size in USD ($50M design scale).
            yaml_path: Optional path to config yaml.
            basis: "selected" (default) for selected redesign cell 149 (70:30 split, 70.2% LTV -> $15.795M Morpho, $5.265M per vault),
                   or "template" for KPK published template cell 163 (100:0 split, $10M vault exposure).
        """
        path = Path(yaml_path) if yaml_path is not None else DEFAULT_CONFIG_PATH
        entities: dict[str, Counterparty] = {}

        if basis in ("selected", "rebuilt", "fund_equity"):
            # Selected configuration (Cell 149): 45% carry sleeve ($22.5M), 70:30 WBTC:cbBTC, 70.2% LTV
            # 1. Lending / Borrow Protocols
            entities["aave_v3_core"] = Counterparty(
                name="aave_v3_core",
                category="lending_protocol",
                current_exposure_usd=total_fund_usd * 0.45,  # $22.5M carry collateral / borrow venue
                exposure_cap_usd=35_000_000.0,
                loss_given_default=0.80,  # Assumed model parameter
            )
            entities["morpho"] = Counterparty(
                name="morpho",
                category="curation_protocol",
                current_exposure_usd=total_fund_usd * 0.45 * 0.702,  # $15.795M borrowed USDC across vaults
                exposure_cap_usd=25_000_000.0,
                loss_given_default=0.85,  # Assumed model parameter
            )
            entities["gearbox"] = Counterparty(
                name="gearbox",
                category="lending_protocol",
                current_exposure_usd=0.0,  # secondary venue, capacity constrained at design size
                exposure_cap_usd=10_000_000.0,
                loss_given_default=0.90,  # Assumed model parameter
            )

            # 2. Deployment Vaults: $15.795M borrow split across 3 vaults in equal thirds = $5.265M each
            vault_dep = (total_fund_usd * 0.45 * 0.702) / 3.0
            entities["kpk_usdc_yield_vault"] = Counterparty(
                name="kpk_usdc_yield_vault",
                category="deployment_vault",
                current_exposure_usd=vault_dep,
                exposure_cap_usd=12_500_000.0,
                loss_given_default=0.65,  # Assumed model parameter
            )
            entities["kpk_usdc_yield_rwa_vault"] = Counterparty(
                name="kpk_usdc_yield_rwa_vault",
                category="deployment_vault",
                current_exposure_usd=vault_dep,
                exposure_cap_usd=12_500_000.0,
                loss_given_default=0.65,  # Assumed model parameter
            )
            entities["yo_ecosystem_yousd"] = Counterparty(
                name="yo_ecosystem_yousd",
                category="deployment_vault",
                current_exposure_usd=vault_dep,
                exposure_cap_usd=12_500_000.0,
                loss_given_default=0.65,  # Assumed model parameter
            )

            # 3. Asset Issuers & Custodians (70:30 WBTC:cbBTC on 45% sleeve = $15.75M / $6.75M)
            entities["bitgo"] = Counterparty(
                name="bitgo",
                category="custodian",
                current_exposure_usd=total_fund_usd * 0.45 * 0.70,  # 31.5% fund equity ($15.75M)
                exposure_cap_usd=30_000_000.0,
                loss_given_default=1.00,  # Assumed model parameter
            )
            entities["coinbase"] = Counterparty(
                name="coinbase",
                category="asset_issuer",
                current_exposure_usd=total_fund_usd * 0.45 * 0.30,  # 13.5% fund equity ($6.75M)
                exposure_cap_usd=20_000_000.0,
                loss_given_default=1.00,  # Assumed model parameter
            )

            # 4. Offchain Sleeves & Reserves
            entities["binance_perp"] = Counterparty(
                name="binance_perp",
                category="perp_venue",
                current_exposure_usd=0.0,  # Gated to T-bills under Basis Gate (2.2% funding < 4.03% DTB3)
                exposure_cap_usd=15_000_000.0,
                loss_given_default=1.00,  # Assumed model parameter
            )
            entities["uniswap_v3"] = Counterparty(
                name="uniswap_v3",
                category="amm_protocol",
                current_exposure_usd=total_fund_usd * 0.15,  # 15.0% AMM sleeve ($7.5M)
                exposure_cap_usd=10_000_000.0,
                loss_given_default=0.90,  # Assumed model parameter
            )
            entities["us_treasury"] = Counterparty(
                name="us_treasury",
                category="treasury",
                current_exposure_usd=total_fund_usd * 0.40,  # 40.0% T-bill reserve ($20.0M)
                exposure_cap_usd=50_000_000.0,
                loss_given_default=0.00,  # Sovereign risk-free floor
            )

        elif basis == "template":
            # 1. Lending / Borrow Protocols
            entities["aave_v3_core"] = Counterparty(
                name="aave_v3_core",
                category="lending_protocol",
                current_exposure_usd=total_fund_usd * 0.45,  # 45% carry sleeve anchor
                exposure_cap_usd=35_000_000.0,
                loss_given_default=0.80,  # Assumed model parameter
            )
            entities["morpho"] = Counterparty(
                name="morpho",
                category="curation_protocol",
                current_exposure_usd=total_fund_usd * 0.45 * 0.702,  # $15.795M borrow
                exposure_cap_usd=30_000_000.0,
                loss_given_default=0.85,  # Assumed model parameter
            )
            entities["gearbox"] = Counterparty(
                name="gearbox",
                category="lending_protocol",
                current_exposure_usd=0.0,  # secondary venue, capacity constrained at design size
                exposure_cap_usd=10_000_000.0,
                loss_given_default=0.90,  # Assumed model parameter
            )

            # 2. Deployment Vaults (configs/fund_template.yaml destinations)
            entities["kpk_usdc_yield_vault"] = Counterparty(
                name="kpk_usdc_yield_vault",
                category="deployment_vault",
                current_exposure_usd=total_fund_usd * 0.20,
                exposure_cap_usd=20_000_000.0,
                loss_given_default=0.65,  # Assumed model parameter
            )
            entities["kpk_usdc_yield_rwa_vault"] = Counterparty(
                name="kpk_usdc_yield_rwa_vault",
                category="deployment_vault",
                current_exposure_usd=total_fund_usd * 0.15,
                exposure_cap_usd=15_000_000.0,
                loss_given_default=0.65,  # Assumed model parameter
            )
            entities["yo_ecosystem_yousd"] = Counterparty(
                name="yo_ecosystem_yousd",
                category="deployment_vault",
                current_exposure_usd=0.0,
                exposure_cap_usd=10_000_000.0,
                loss_given_default=0.65,  # Assumed model parameter
            )

            # 3. Asset Issuers & Custodians
            entities["bitgo"] = Counterparty(
                name="bitgo",
                category="custodian",
                current_exposure_usd=total_fund_usd * 0.45,  # 100% WBTC
                exposure_cap_usd=40_000_000.0,
                loss_given_default=1.00,  # Assumed model parameter
            )
            entities["coinbase"] = Counterparty(
                name="coinbase",
                category="asset_issuer",
                current_exposure_usd=0.0,
                exposure_cap_usd=30_000_000.0,
                loss_given_default=1.00,  # Assumed model parameter
            )

            # 4. Offchain Sleeves (Basis & T-Bill)
            entities["binance_perp"] = Counterparty(
                name="binance_perp",
                category="perp_venue",
                current_exposure_usd=total_fund_usd * 0.10,
                exposure_cap_usd=15_000_000.0,
                loss_given_default=1.00,  # Assumed model parameter
            )
            entities["uniswap_v3"] = Counterparty(
                name="uniswap_v3",
                category="amm_protocol",
                current_exposure_usd=0.0,
                exposure_cap_usd=10_000_000.0,
                loss_given_default=0.90,  # Assumed model parameter
            )
            entities["us_treasury"] = Counterparty(
                name="us_treasury",
                category="treasury",
                current_exposure_usd=total_fund_usd * 0.20,  # 20% T-bill liquidity floor
                exposure_cap_usd=50_000_000.0,
                loss_given_default=0.00,  # Sovereign risk-free floor
            )
        else:
            raise ValueError(f"Unknown roster basis: {basis}")

        return cls(counterparties=entities)

    def total_exposure_usd(self) -> float:
        """Total current exposure across all counterparties."""
        return sum(c.current_exposure_usd for c in self.counterparties.values())

    def apply_default(
        self,
        entity_name: str,
        loss_severity: float | None = None,
    ) -> dict[str, Any]:
        """Loss-given-default hook consumed by S5/S6 scenario shocks.

        Calculates portfolio loss when a counterparty defaults or is exploited.

        Args:
          entity_name: Name of defaulting entity.
          loss_severity: Optional severity in [0, 1]. If omitted, uses entity's LGD.

        Returns:
          Dict with loss details.
        """
        entity = self.counterparties.get(entity_name)
        if entity is None:
            raise KeyError(f"Counterparty {entity_name} not found in table.")

        sev = float(loss_severity) if loss_severity is not None else entity.loss_given_default
        sev = float(np.clip(sev, 0.0, 1.0))

        loss_usd = entity.current_exposure_usd * sev
        prior_exposure = entity.current_exposure_usd
        entity.current_exposure_usd = max(0.0, entity.current_exposure_usd - loss_usd)

        return {
            "entity": entity_name,
            "category": entity.category,
            "loss_severity": sev,
            "loss_usd": loss_usd,
            "prior_exposure_usd": prior_exposure,
            "remaining_exposure_usd": entity.current_exposure_usd,
        }


@dataclass
class PortfolioRiskReport:
    """Report detailing portfolio concentration, HHI indices, and counterparty exposures."""

    venue_shares: dict[str, float]
    venue_hhi: float
    asset_shares: dict[str, float]
    asset_hhi: float
    issuer_shares: dict[str, float]
    issuer_hhi: float
    counterparties: dict[str, Counterparty]
    total_fund_usd: float

    def summary(self) -> dict[str, Any]:
        """Headline metrics for reporting."""
        return {
            "total_fund_usd": self.total_fund_usd,
            "venue_hhi": self.venue_hhi,
            "asset_hhi": self.asset_hhi,
            "issuer_hhi": self.issuer_hhi,
            "largest_venue": max(self.venue_shares.items(), key=lambda x: x[1])[0] if self.venue_shares else "",
            "largest_asset": max(self.asset_shares.items(), key=lambda x: x[1])[0] if self.asset_shares else "",
        }


def compute_portfolio_metrics(
    total_fund_usd: float = 50_000_000.0,
    venue_exposures: dict[str, float] | None = None,
    asset_exposures: dict[str, float] | None = None,
    issuer_exposures: dict[str, float] | None = None,
    counterparty_table: CounterpartyTable | None = None,
) -> PortfolioRiskReport:
    """Compute concentration shares and HHI indices for fund portfolio per SPEC §13.1.

    Normalized on ONE unified book definition ($50M multi-strategy fund equity):
      - Carry Sleeve: 45.0% ($22.5M) split 70:30 WBTC:cbBTC on Aave v3 Core
      - AMM Sleeve: 15.0% ($7.5M) on Uniswap v3
      - T-Bill Reserve: 40.0% ($20.0M) US Treasury 3-month bills
    """
    if venue_exposures is None:
        venue_exposures = {
            "aave_v3_core": total_fund_usd * 0.45,  # 45% carry collateral/borrow venue ($22.5M)
            "us_treasury": total_fund_usd * 0.40,   # 40% sovereign cash reserve ($20.0M)
            "uniswap_v3": total_fund_usd * 0.15,    # 15% AMM sleeve ($7.5M)
        }

    if asset_exposures is None:
        asset_exposures = {
            "WBTC": total_fund_usd * 0.45 * 0.70,   # 31.5% of fund equity ($15.75M)
            "cbBTC": total_fund_usd * 0.45 * 0.30,  # 13.5% of fund equity ($6.75M)
            "USD_TBill": total_fund_usd * 0.40,     # 40.0% sovereign cash ($20.0M)
            "USDC_AMM": total_fund_usd * 0.15,      # 15.0% AMM liquidity ($7.5M)
        }

    if issuer_exposures is None:
        issuer_exposures = {
            "bitgo": total_fund_usd * 0.45 * 0.70,      # 31.5% BitGo (WBTC custody, $15.75M)
            "coinbase": total_fund_usd * 0.45 * 0.30,   # 13.5% Coinbase (cbBTC issuer/custody, $6.75M)
            "us_treasury": total_fund_usd * 0.40,       # 40.0% US Sovereign ($20.0M)
            "uniswap_v3": total_fund_usd * 0.15,        # 15.0% Uniswap smart contracts ($7.5M)
        }

    v_shares = compute_exposure_shares(venue_exposures)
    a_shares = compute_exposure_shares(asset_exposures)
    i_shares = compute_exposure_shares(issuer_exposures)

    v_hhi = compute_hhi(v_shares)
    a_hhi = compute_hhi(a_shares)
    i_hhi = compute_hhi(i_shares)

    table = counterparty_table if counterparty_table is not None else CounterpartyTable.create_default(total_fund_usd)

    return PortfolioRiskReport(
        venue_shares=v_shares,
        venue_hhi=v_hhi,
        asset_shares=a_shares,
        asset_hhi=a_hhi,
        issuer_shares=i_shares,
        issuer_hhi=i_hhi,
        counterparties=table.counterparties,
        total_fund_usd=total_fund_usd,
    )


def compute_calm_vs_crisis_correlations(
    carry_yields: np.ndarray,
    btc_price_returns: np.ndarray,
) -> dict[str, float]:
    """Compute empirical correlation between carry sleeve return and BTC returns
    across calm paths vs crisis paths from simulated Monte Carlo paths.
    """
    rets = np.asarray(btc_price_returns, dtype=float).flatten()
    yields = np.asarray(carry_yields, dtype=float).flatten()

    if len(rets) != len(yields) or len(rets) < 2:
        return {"rho_full": 0.0, "rho_calm": 0.0, "rho_crisis": 0.0}

    # Full sample correlation
    if np.std(yields) > 1e-9 and np.std(rets) > 1e-9:
        c_full = float(np.corrcoef(yields, rets)[0, 1])
    else:
        c_full = 0.0

    # Calm paths: BTC price return >= -0.05
    calm_mask = rets >= -0.05
    if np.sum(calm_mask) > 5 and np.std(yields[calm_mask]) > 1e-9 and np.std(rets[calm_mask]) > 1e-9:
        c_calm = float(np.corrcoef(yields[calm_mask], rets[calm_mask])[0, 1])
    else:
        c_calm = 0.0

    # Crisis paths: BTC price return < -0.10
    crisis_mask = rets < -0.10
    if np.sum(crisis_mask) > 5 and np.std(yields[crisis_mask]) > 1e-9 and np.std(rets[crisis_mask]) > 1e-9:
        c_crisis = float(np.corrcoef(yields[crisis_mask], rets[crisis_mask])[0, 1])
    else:
        c_crisis = 0.0

    return {
        "rho_full": round(c_full, 4),
        "rho_calm": round(c_calm, 4),
        "rho_crisis": round(c_crisis, 4),
    }
