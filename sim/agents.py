"""
Fund Agent policy implementation for KPK Carry Sim.
Implements SPEC §3.3 (Agent Layer - Fund Agent) for Phase 1.

The Fund Agent executes the published KPK BTC Carry template policy triggers:
  1. Full unwind if HF falls below floor (1.13).
  2. Full unwind if BTC drops >= 10% over the trailing 24 hours.
  3. Full unwind after 2 consecutive 6-hour negative-carry windows (borrow APY > deploy APY).
  4. Partial deleveraging toward target LTV (HF 1.21) when HF drops below the target band (1.15).
  5. Hold otherwise.

Strictly no look-ahead: decisions at hour t evaluate only historical data up to t.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "fund_template.yaml"


class ActionType(str, Enum):
    """Possible actions executable by the Fund Agent."""

    HOLD = "hold"
    DELEVERAGE = "deleverage"
    FULL_UNWIND = "full_unwind"


@dataclass(frozen=True)
class PolicyAction:
    """Action returned by the Fund Agent policy."""

    action_type: ActionType
    repay_fraction: float = 0.0  # Fraction of outstanding debt to repay when deleveraging [0, 1]
    reason: str = ""
    trigger_value: float = 0.0

    @classmethod
    def hold(cls, reason: str = "normal") -> PolicyAction:
        return cls(action_type=ActionType.HOLD, reason=reason)

    @classmethod
    def deleverage(cls, repay_fraction: float, reason: str = "hf_band_deleveraging", trigger_value: float = 0.0) -> PolicyAction:
        return cls(
            action_type=ActionType.DELEVERAGE,
            repay_fraction=float(np.clip(repay_fraction, 0.0, 1.0)),
            reason=reason,
            trigger_value=float(trigger_value),
        )

    @classmethod
    def full_unwind(cls, reason: str, trigger_value: float = 0.0) -> PolicyAction:
        return cls(
            action_type=ActionType.FULL_UNWIND,
            repay_fraction=1.0,
            reason=reason,
            trigger_value=float(trigger_value),
        )


@dataclass
class FundAgentPolicy:
    """KPK Fund Agent policy rules parameterised from configs/fund_template.yaml."""

    target_ltv: float = 0.702
    hf_target: float = 1.21
    hf_band_lower: float = 1.15
    hf_band_upper: float = 1.25
    hf_full_unwind_below: float = 1.13
    btc_24h_drop_threshold: float = -0.10
    negative_carry_window_hours: int = 6
    negative_carry_consecutive_windows: int = 2

    @classmethod
    def from_yaml(cls, yaml_path: Path | str | None = None) -> FundAgentPolicy:
        """Load policy configuration from configs/fund_template.yaml."""
        path = Path(yaml_path) if yaml_path is not None else DEFAULT_CONFIG_PATH
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        fund_cfg = cfg.get("fund", {})
        triggers = fund_cfg.get("triggers_full_unwind", {})
        band = fund_cfg.get("hf_band", [1.15, 1.25])

        return cls(
            target_ltv=float(fund_cfg.get("target_ltv", 0.702)),
            hf_target=float(fund_cfg.get("hf_target", 1.21)),
            hf_band_lower=float(band[0]),
            hf_band_upper=float(band[1]),
            hf_full_unwind_below=float(triggers.get("hf_below", 1.13)),
            btc_24h_drop_threshold=float(triggers.get("btc_24h_drop_pct", -0.10)),
            negative_carry_window_hours=int(triggers.get("usdc_borrow_above_vault_yield_window_hours", 6)),
            negative_carry_consecutive_windows=int(triggers.get("usdc_borrow_above_vault_yield_windows", 2)),
        )


class FundAgent:
    """Fund Agent executing the KPK BTC Carry template policy per SPEC §3.3."""

    def __init__(self, policy: FundAgentPolicy | None = None) -> None:
        self.policy = policy if policy is not None else FundAgentPolicy.from_yaml()

    def evaluate(
        self,
        t: int,
        price_history: np.ndarray,
        current_hf: float,
        borrow_rate_history: np.ndarray | None = None,
        deploy_rate_history: np.ndarray | None = None,
    ) -> PolicyAction:
        """Evaluate policy triggers at hour t using strictly data up to t.

        Args:
          t: Current hour index (>= 0).
          price_history: 1D array of BTC prices from hour 0 to t inclusive (len >= t + 1).
          current_hf: Current position health factor at hour t.
          borrow_rate_history: 1D array of hourly borrow rates from hour 0 to t (or None).
          deploy_rate_history: 1D array of hourly deployment yields from hour 0 to t (or None).

        Returns:
          PolicyAction (HOLD, DELEVERAGE, or FULL_UNWIND).
        """
        # Guard: slice strictly up to t to ensure no future data leakage
        prices = np.asarray(price_history[: t + 1], dtype=float)

        # -------------------------------------------------------------
        # 1. Full Unwind Trigger: HF floor breach (< 1.13)
        # -------------------------------------------------------------
        if current_hf < self.policy.hf_full_unwind_below:
            return PolicyAction.full_unwind(
                reason="hf_below_floor",
                trigger_value=current_hf,
            )

        # -------------------------------------------------------------
        # 2. Full Unwind Trigger: BTC 24-hour drop <= -10%
        # -------------------------------------------------------------
        if t >= 24:
            p_now = prices[t]
            p_24h_ago = prices[t - 24]
            btc_24h_change = (p_now - p_24h_ago) / p_24h_ago
            if btc_24h_change <= self.policy.btc_24h_drop_threshold:
                return PolicyAction.full_unwind(
                    reason="btc_24h_drop",
                    trigger_value=btc_24h_change,
                )

        # -------------------------------------------------------------
        # 3. Full Unwind Trigger: 2 consecutive 6-hour negative carry windows
        # -------------------------------------------------------------
        w_size = self.policy.negative_carry_window_hours
        n_windows = self.policy.negative_carry_consecutive_windows
        required_hours = w_size * n_windows

        if (
            t >= required_hours
            and borrow_rate_history is not None
            and deploy_rate_history is not None
        ):
            b_hist = np.asarray(borrow_rate_history[: t + 1], dtype=float)
            d_hist = np.asarray(deploy_rate_history[: t + 1], dtype=float)

            # Net carry series: deployment yield minus borrow cost
            net_carry = d_hist - b_hist

            # Check consecutive windows
            all_negative = True
            for w in range(n_windows):
                w_end = t - (w * w_size)
                w_start = w_end - w_size
                w_carry = net_carry[w_start:w_end]
                # If mean net carry in window >= 0, condition fails
                if np.mean(w_carry) >= 0.0:
                    all_negative = False
                    break

            if all_negative:
                return PolicyAction.full_unwind(
                    reason="negative_carry_consecutive_windows",
                    trigger_value=float(np.mean(net_carry[t - required_hours : t])),
                )

        # -------------------------------------------------------------
        # 4. HF Band Deleveraging Trigger: HF in [1.13, 1.15)
        # -------------------------------------------------------------
        if current_hf < self.policy.hf_band_lower:
            # Restore HF to hf_target (1.21).
            # HF = Collateral_Value * LLTV / Debt
            # Target Debt = Collateral_Value * LLTV / HF_target
            # Debt_Target / Debt = current_hf / HF_target
            # Repay fraction = 1 - (current_hf / HF_target)
            repay_frac = 1.0 - (current_hf / self.policy.hf_target)
            return PolicyAction.deleverage(
                repay_fraction=repay_frac,
                reason="hf_band_deleveraging",
                trigger_value=current_hf,
            )

        # -------------------------------------------------------------
        # 5. Normal operation: HOLD
        # -------------------------------------------------------------
        return PolicyAction.hold(reason="within_normal_parameters")
