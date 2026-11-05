"""
Protocol venue state and oracle model for KPK Carry Sim.
Implements SPEC §3.2 (Venue Layer - Protocol State).

Components:
  1. ChainlinkOracle: Updates on deviation-threshold or heartbeat breaches.
  2. AavePoolState: Tracks supplied BTC collateral, USDC debt, utilisation,
     health factor (HF), and liquidation mechanics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "fund_template.yaml"


@dataclass
class ChainlinkOracle:
    """Chainlink-style price feed oracle per SPEC §3.2.

    Updates to market price when:
      1. Absolute price change from last update exceeds deviation_threshold, OR
      2. Elapsed time since last update reaches or exceeds heartbeat_hours.
    """

    deviation_threshold: float = 0.005  # 0.5% standard threshold
    heartbeat_hours: int = 1            # 1 hour standard heartbeat for high-activity feeds
    last_reported_price: float = field(default=0.0, init=False)
    last_update_hour: int = field(default=0, init=False)
    is_initialised: bool = field(default=False, init=False)

    def init_price(self, price: float, hour: int = 0) -> None:
        """Initialise oracle price at simulation start."""
        self.last_reported_price = float(price)
        self.last_update_hour = hour
        self.is_initialised = True

    def update(self, market_price: float, current_hour: int) -> float:
        """Process incoming market price at current_hour and return effective oracle price."""
        if not self.is_initialised:
            self.init_price(market_price, current_hour)
            return self.last_reported_price

        elapsed = current_hour - self.last_update_hour
        dev = abs(market_price - self.last_reported_price) / max(self.last_reported_price, 1e-12)

        if dev >= self.deviation_threshold or elapsed >= self.heartbeat_hours:
            self.last_reported_price = float(market_price)
            self.last_update_hour = current_hour

        return self.last_reported_price

    @classmethod
    def simulate_path(
        cls,
        market_prices: np.ndarray,
        deviation_threshold: float = 0.005,
        heartbeat_hours: int = 1,
    ) -> np.ndarray:
        """Vectorised / batch computation of oracle price series for an array of market prices.

        Args:
          market_prices: 1D or 2D array of prices [n_hours + 1] or [n_paths, n_hours + 1].

        Returns:
          np.ndarray of same shape containing the effective oracle prices.
        """
        prices = np.asarray(market_prices, dtype=float)
        if prices.ndim == 1:
            oracle_prices = np.empty_like(prices)
            oracle_prices[0] = prices[0]
            last_p = prices[0]
            last_t = 0
            for t in range(1, len(prices)):
                p = prices[t]
                dev = abs(p - last_p) / max(last_p, 1e-12)
                if dev >= deviation_threshold or (t - last_t) >= heartbeat_hours:
                    last_p = p
                    last_t = t
                oracle_prices[t] = last_p
            return oracle_prices
        else:
            # 2D array [n_paths, n_steps]
            n_paths, n_steps = prices.shape
            oracle_prices = np.empty_like(prices)
            oracle_prices[:, 0] = prices[:, 0]
            last_p = prices[:, 0].copy()
            last_t = np.zeros(n_paths, dtype=int)

            for t in range(1, n_steps):
                p = prices[:, t]
                dev = np.abs(p - last_p) / np.maximum(last_p, 1e-12)
                trigger = (dev >= deviation_threshold) | ((t - last_t) >= heartbeat_hours)
                last_p = np.where(trigger, p, last_p)
                last_t = np.where(trigger, t, last_t)
                oracle_prices[:, t] = last_p

            return oracle_prices


@dataclass
class AavePoolState:
    """Aave v3 pool position state tracker per SPEC §3.2.

    State variables:
      collateral_btc: Total collateral supplied (WBTC/cbBTC) in BTC units.
      debt_usdc: Total borrowed USDC debt.
      lltv: Liquidation Loan-To-Value threshold (0.85 per configs/fund_template.yaml).
      liquidation_bonus: Penalty fee seized by liquidators (0.05 / 5% per configs/fund_template.yaml).
      utilisation: Background pool utilisation ratio in [0, 1].
    """

    collateral_btc: float
    debt_usdc: float
    lltv: float = 0.85
    liquidation_bonus: float = 0.05
    utilisation: float = 0.80

    @classmethod
    def from_entry(
        cls,
        collateral_btc: float,
        entry_price: float,
        target_ltv: float = 0.702,
        lltv: float = 0.85,
        liquidation_bonus: float = 0.05,
        utilisation: float = 0.80,
    ) -> AavePoolState:
        """Create a new Aave position at target LTV."""
        collateral_val = collateral_btc * entry_price
        debt = collateral_val * target_ltv
        return cls(
            collateral_btc=float(collateral_btc),
            debt_usdc=float(debt),
            lltv=float(lltv),
            liquidation_bonus=float(liquidation_bonus),
            utilisation=float(utilisation),
        )

    def collateral_value(self, price: float) -> float:
        """USD value of supplied collateral at given price."""
        return self.collateral_btc * price

    def current_ltv(self, price: float) -> float:
        """Current Loan-to-Value ratio: debt_value / collateral_value."""
        val = self.collateral_value(price)
        if val <= 0.0:
            return float("inf") if self.debt_usdc > 0 else 0.0
        return self.debt_usdc / val

    def health_factor(self, price: float) -> float:
        """Health factor (HF) per SPEC §3.2:

        HF = collateral_value * LLTV / debt_value
           = (collateral_btc * price * lltv) / debt_usdc
        """
        if self.debt_usdc <= 0.0:
            return float("inf")
        collat_val = self.collateral_value(price)
        return (collat_val * self.lltv) / self.debt_usdc

    def is_liquidatable(self, price: float) -> bool:
        """Liquidation occurs when HF < 1.0."""
        return self.health_factor(price) < 1.0

    def liquidation_price(self) -> float:
        """Solve for the price at which HF == 1.0:

        HF = 1.0 => (collateral_btc * P_liq * lltv) / debt_usdc = 1.0
        P_liq = debt_usdc / (collateral_btc * lltv)
        """
        denom = self.collateral_btc * self.lltv
        if denom <= 0.0:
            return 0.0
        return self.debt_usdc / denom

    def liquidation_drop_pct(self, entry_price: float) -> float:
        """Percentage fall from entry_price to liquidation_price:

        drop = 1.0 - (P_liq / P_entry)
        """
        p_liq = self.liquidation_price()
        return 1.0 - (p_liq / entry_price)

    def accrue_interest(self, hourly_borrow_rate: float) -> None:
        """Accrue 1 hour of borrowing cost onto outstanding USDC debt."""
        self.debt_usdc *= 1.0 + hourly_borrow_rate

    def repay_debt(self, amount: float) -> float:
        """Repay outstanding USDC debt up to total debt. Returns actual repaid amount."""
        repay = min(self.debt_usdc, max(0.0, float(amount)))
        self.debt_usdc -= repay
        return repay

    def borrow_more(self, amount: float) -> None:
        """Increase debt by borrowing additional USDC."""
        self.debt_usdc += max(0.0, float(amount))

    def execute_liquidation(self, market_price: float) -> dict[str, float]:
        """Execute protocol liquidation when HF < 1.0.

        Liquidator repays all debt and seizes collateral valued at:
          debt_usdc * (1 + liquidation_bonus)
        Returns remaining collateral and liquidation metrics.
        """
        if market_price <= 0.0:
            raise ValueError(f"Invalid market price {market_price}")

        debt_to_cover = self.debt_usdc
        seized_usd = debt_to_cover * (1.0 + self.liquidation_bonus)
        seized_btc = seized_usd / market_price

        btc_lost = min(self.collateral_btc, seized_btc)
        self.collateral_btc -= btc_lost
        self.debt_usdc = 0.0

        return {
            "seized_btc": btc_lost,
            "seized_usd": seized_usd,
            "remaining_collateral_btc": self.collateral_btc,
            "shortfall_usd": max(0.0, seized_usd - (btc_lost * market_price)),
        }
