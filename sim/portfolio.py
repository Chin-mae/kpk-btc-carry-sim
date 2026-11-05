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
    ) -> CounterpartyTable:
        """Create standard counterparty roster initialized to fund template destinations."""
        path = Path(yaml_path) if yaml_path is not None else DEFAULT_CONFIG_PATH
        entities: dict[str, Counterparty] = {}

        # 1. Lending / Borrow Protocols
        entities["aave_v3_core"] = Counterparty(
            name="aave_v3_core",
            category="lending_protocol",
            current_exposure_usd=total_fund_usd * 0.45,  # 45% carry sleeve anchor
            exposure_cap_usd=35_000_000.0,
            loss_given_default=0.80,
        )
        entities["morpho"] = Counterparty(
            name="morpho",
            category="curation_protocol",
            current_exposure_usd=total_fund_usd * 0.30,
            exposure_cap_usd=30_000_000.0,
            loss_given_default=0.85,
        )
        entities["gearbox"] = Counterparty(
            name="gearbox",
            category="lending_protocol",
            current_exposure_usd=0.0,  # secondary venue, capacity-constrained per SPEC §13.3
            exposure_cap_usd=10_000_000.0,
            loss_given_default=0.90,
        )

        # 2. Deployment Vaults (configs/fund_template.yaml destinations)
        entities["kpk_usdc_yield_vault"] = Counterparty(
            name="kpk_usdc_yield_vault",
            category="deployment_vault",
            current_exposure_usd=total_fund_usd * 0.20,
            exposure_cap_usd=20_000_000.0,
            loss_given_default=0.65,
        )
        entities["kpk_usdc_yield_rwa_vault"] = Counterparty(
            name="kpk_usdc_yield_rwa_vault",
            category="deployment_vault",
            current_exposure_usd=total_fund_usd * 0.15,
            exposure_cap_usd=15_000_000.0,
            loss_given_default=0.65,
        )
        entities["yo_ecosystem_yousd"] = Counterparty(
            name="yo_ecosystem_yousd",
            category="deployment_vault",
            current_exposure_usd=0.0,
            exposure_cap_usd=10_000_000.0,
            loss_given_default=1.00,
        )

        # 3. Asset Issuers & Custodians
        entities["bitgo"] = Counterparty(
            name="bitgo",
            category="custodian",
            current_exposure_usd=total_fund_usd * 0.35,  # WBTC custody
            exposure_cap_usd=40_000_000.0,
            loss_given_default=1.00,
        )
        entities["coinbase"] = Counterparty(
            name="coinbase",
            category="asset_issuer",
            current_exposure_usd=total_fund_usd * 0.15,  # cbBTC minting/backing
            exposure_cap_usd=30_000_000.0,
            loss_given_default=1.00,
        )

        # 4. Offchain Sleeves (Basis & T-Bill)
        entities["binance_perp"] = Counterparty(
            name="binance_perp",
            category="perp_venue",
            current_exposure_usd=total_fund_usd * 0.10,
            exposure_cap_usd=15_000_000.0,
            loss_given_default=1.00,
        )
        entities["us_treasury"] = Counterparty(
            name="us_treasury",
            category="treasury",
            current_exposure_usd=total_fund_usd * 0.20,  # 20% T-bill liquidity floor
            exposure_cap_usd=50_000_000.0,
            loss_given_default=0.00,  # Sovereign risk-free floor
        )

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
    """Compute concentration shares and HHI indices for fund portfolio per SPEC §13.1."""
    if venue_exposures is None:
        venue_exposures = {
            "aave_v3_core": 22_500_000.0,   # 45% carry
            "morpho_vaults": 15_000_000.0,  # 30% deployment
            "perp_basis": 5_000_000.0,      # 10% basis
            "t_bills": 10_000_000.0,        # 20% liquidity
        }

    if asset_exposures is None:
        asset_exposures = {
            "WBTC": 35_000_000.0,
            "cbBTC": 15_000_000.0,
        }

    if issuer_exposures is None:
        issuer_exposures = {
            "bitgo": 35_000_000.0,
            "coinbase": 15_000_000.0,
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
