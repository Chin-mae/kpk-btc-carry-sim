"""
Interest rate models and yield dilution functions for KPK Carry Sim.
Implements SPEC §3.1 (Market Layer - Rates).

Components:
  1. Aave v3 kinked interest-rate model (IRM) parameterised from configs/fund_template.yaml.
  2. Vault APY own-size impact function: diluted_apy(base_apy, vault_tvl, deposit).
  3. Compounding converters between annual APY and hourly interest rate factors.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "fund_template.yaml"

HOURS_PER_YEAR = 8760.0


@dataclass(frozen=True)
class AaveIRMParams:
    """Parameters for the Aave v3 Kinked Interest Rate Model."""

    optimal_utilisation: float = 0.90
    base_rate: float = 0.0
    slope1: float = 0.055
    slope2: float = 0.60
    reserve_factor: float = 0.10

    @classmethod
    def from_yaml(cls, yaml_path: Path | str | None = None) -> AaveIRMParams:
        """Load IRM parameters from configs/fund_template.yaml."""
        path = Path(yaml_path) if yaml_path is not None else DEFAULT_CONFIG_PATH
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        irm_cfg = cfg.get("aave_usdc_irm", {})
        return cls(
            optimal_utilisation=float(irm_cfg.get("optimal_utilisation", 0.90)),
            base_rate=float(irm_cfg.get("base_rate", 0.0)),
            slope1=float(irm_cfg.get("slope1", 0.055)),
            slope2=float(irm_cfg.get("slope2", 0.60)),
            reserve_factor=float(irm_cfg.get("reserve_factor", 0.10)),
        )


class AaveV3IRM:
    """Aave v3 Kinked Interest Rate Model per SPEC §3.1.

    Borrow rate curve:
      If U <= U_optimal:
        R_borrow(U) = R_base + (U / U_optimal) * R_slope1
      If U > U_optimal:
        R_borrow(U) = R_base + R_slope1 + ((U - U_optimal) / (1 - U_optimal)) * R_slope2

    Supply rate curve:
      R_supply(U) = U * R_borrow(U) * (1 - reserve_factor)
    """

    def __init__(self, params: AaveIRMParams | None = None) -> None:
        self.params = params if params is not None else AaveIRMParams.from_yaml()

    def borrow_apy(self, utilisation: float | np.ndarray) -> float | np.ndarray:
        """Calculate variable borrow APY for a given utilisation ratio U in [0, 1]."""
        u = np.asarray(utilisation, dtype=float)
        u_clamped = np.clip(u, 0.0, 1.0)

        u_opt = self.params.optimal_utilisation
        r_base = self.params.base_rate
        slope1 = self.params.slope1
        slope2 = self.params.slope2

        # Piecewise calculation
        below_kink = r_base + (u_clamped / u_opt) * slope1
        above_kink = r_base + slope1 + ((u_clamped - u_opt) / (1.0 - u_opt)) * slope2

        rate = np.where(u_clamped <= u_opt, below_kink, above_kink)
        return float(rate) if np.ndim(utilisation) == 0 else rate

    def supply_apy(self, utilisation: float | np.ndarray) -> float | np.ndarray:
        """Calculate supply APY for a given utilisation ratio U in [0, 1]."""
        u = np.asarray(utilisation, dtype=float)
        u_clamped = np.clip(u, 0.0, 1.0)
        borrow_rate = self.borrow_apy(u_clamped)
        supply_rate = u_clamped * borrow_rate * (1.0 - self.params.reserve_factor)
        return float(supply_rate) if np.ndim(utilisation) == 0 else supply_rate

    def hourly_borrow_rate(
        self, utilisation: float | np.ndarray, compounding: bool = True
    ) -> float | np.ndarray:
        """Compute the 1-hour borrow rate from the annualised APY."""
        apy = self.borrow_apy(utilisation)
        return hourly_rate_from_apy(apy, compounding=compounding)

    def hourly_supply_rate(
        self, utilisation: float | np.ndarray, compounding: bool = True
    ) -> float | np.ndarray:
        """Compute the 1-hour supply rate from the annualised APY."""
        apy = self.supply_apy(utilisation)
        return hourly_rate_from_apy(apy, compounding=compounding)


def borrow_apy(
    utilisation: float | np.ndarray, params: AaveIRMParams | None = None
) -> float | np.ndarray:
    """Convenience function for Aave v3 borrow APY."""
    return AaveV3IRM(params).borrow_apy(utilisation)


def supply_apy(
    utilisation: float | np.ndarray, params: AaveIRMParams | None = None
) -> float | np.ndarray:
    """Convenience function for Aave v3 supply APY."""
    return AaveV3IRM(params).supply_apy(utilisation)


def hourly_rate_from_apy(
    apy: float | np.ndarray, compounding: bool = True
) -> float | np.ndarray:
    """Convert annualised APY to 1-hour interest rate factor.

    If compounding is True: (1 + APY)**(1/8760) - 1
    If compounding is False: APY / 8760
    """
    arr = np.asarray(apy, dtype=float)
    if compounding:
        rate = np.power(1.0 + np.maximum(-0.9999, arr), 1.0 / HOURS_PER_YEAR) - 1.0
    else:
        rate = arr / HOURS_PER_YEAR
    return float(rate) if np.ndim(apy) == 0 else rate


def diluted_apy(
    base_apy: float | np.ndarray,
    vault_tvl: float | np.ndarray,
    deposit: float | np.ndarray,
    marginal_ratio: float = 0.0,
) -> float | np.ndarray:
    """Vault APY own-size impact function per SPEC §3.1.

    Models the dilution of deployment yield as deposit share grows:
      depositing $X into a vault of size $Y dilutes its APY by a fitted curve;
      this is the mechanism that makes $50M categorically different from $500K.

    Formula:
      diluted_apy = base_apy * (vault_tvl + marginal_ratio * deposit) / (vault_tvl + deposit)

    Args:
      base_apy: Un-diluted vault APY before new deposit.
      vault_tvl: Existing total value locked (TVL) in the vault ($).
      deposit: New fund deposit amount ($).
      marginal_ratio: Marginal earning efficiency of incremental capital [0, 1].
                      Default 0.0 models fixed strategy revenue capacity.

    Returns:
      Effective diluted APY.
    """
    v = np.asarray(vault_tvl, dtype=float)
    d = np.asarray(deposit, dtype=float)
    b = np.asarray(base_apy, dtype=float)

    denom = v + d
    # If total TVL is zero or negative, return base_apy unchanged
    with np.errstate(divide="ignore", invalid="ignore"):
        factor = np.where(denom > 0, (v + marginal_ratio * d) / denom, 1.0)
    diluted = b * factor

    if np.ndim(base_apy) == 0 and np.ndim(vault_tvl) == 0 and np.ndim(deposit) == 0:
        return float(diluted)
    return diluted
