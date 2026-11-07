"""
Agent populations and Fund Agent policy implementation for KPK Carry Sim.
Implements SPEC §3.3 (Agent Layer) and verified triggers from configs/fund_template.yaml.

Agent Populations:
  1. FundAgent: Executes full verified KPK BTC Carry policy triggers:
     - HF floor breach (< 1.13) -> full unwind.
     - BTC 24h drop <= -10% -> full unwind.
     - Consecutive negative carry windows -> full unwind.
     - Wrapper peg off >= 1% sustained >= 40 min -> full unwind.
     - USDC off peg >= 0.5% -> full unwind.
     - Exploit detection on venue or oracle feed -> full unwind.
     - Venue freezes reserve or sets LTV to 0 -> full unwind.
     - Venue changes price source -> full unwind.
     - WBTC multisig threshold set to 1 -> full unwind.
     - cbBTC owner / proxy / pauser changed -> full unwind.
     - Vault curator change -> VAULT_EXIT (partial exit of that vault).
     - Vault share price drop > 1 bp in one minute -> VAULT_EXIT (partial exit of that vault).
     - HF band restoration ([1.13, 1.15)) -> deleverage to target HF 1.21.
  2. LiquidatorPopulation: Heterogeneous capital, gas bids, profit thresholds;
     liquidates positions with HF < 1.0 only when profitable after gas and slippage (close factor 50%).
  3. BorrowerPopulation: Background borrowers driving pool utilisation; deleveraging
     en masse in crisis regimes (liquidation cascade generator).
  4. DepositorPopulation: Vault depositors making stress-regime withdrawals pushing
     utilisation toward 100% (EURC trapped liquidity mechanism).
  5. ArbitrageurPopulation: Restores wrapper pegs at speed proportional to discount
     and capital; capital parameter halves under hack events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "fund_template.yaml"


class ActionType(str, Enum):
    """Possible actions executable by the Fund Agent."""

    HOLD = "hold"
    DELEVERAGE = "deleverage"
    FULL_UNWIND = "full_unwind"
    VAULT_EXIT = "vault_exit"


@dataclass(frozen=True)
class PolicyAction:
    """Action returned by the Fund Agent policy."""

    action_type: ActionType
    repay_fraction: float = 0.0  # Fraction of outstanding debt to repay when deleveraging [0, 1]
    reason: str = ""
    trigger_value: float = 0.0
    vault_name: str = ""         # Specific vault targeted in VAULT_EXIT actions

    @classmethod
    def hold(cls, reason: str = "normal") -> PolicyAction:
        return cls(action_type=ActionType.HOLD, reason=reason)

    @classmethod
    def deleverage(
        cls,
        repay_fraction: float,
        reason: str = "hf_band_deleveraging",
        trigger_value: float = 0.0,
    ) -> PolicyAction:
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

    @classmethod
    def vault_exit(cls, vault_name: str, reason: str, trigger_value: float = 0.0) -> PolicyAction:
        """Partial exit from a specific deployment vault without unwinding whole fund."""
        return cls(
            action_type=ActionType.VAULT_EXIT,
            repay_fraction=0.0,
            reason=reason,
            trigger_value=float(trigger_value),
            vault_name=vault_name,
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
    # Verified full unwind trigger parameters:
    wrapper_peg_off_pct: float = 0.01          # 1% off peg sustained 40 min
    wrapper_peg_duration_min: int = 40
    usdc_peg_off_pct: float = 0.005            # USDC 0.5% off peg
    exploit_detection_enabled: bool = True
    venue_freeze_trigger_enabled: bool = True
    venue_change_price_source_enabled: bool = True
    wbtc_multisig_threshold_trigger: bool = True
    cbbtc_owner_change_trigger: bool = True
    # Verified vault exit trigger parameters:
    vault_owner_or_curator_change_trigger: bool = True
    vault_share_price_drop_bp_per_min: float = 1.0

    @classmethod
    def from_yaml(cls, yaml_path: Path | str | None = None) -> FundAgentPolicy:
        """Load policy configuration from configs/fund_template.yaml."""
        path = Path(yaml_path) if yaml_path is not None else DEFAULT_CONFIG_PATH
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        fund_cfg = cfg.get("fund", {})
        triggers_full = fund_cfg.get("triggers_full_unwind", {})
        triggers_vault = fund_cfg.get("triggers_vault_exit", {})
        band = fund_cfg.get("hf_band", [1.15, 1.25])

        return cls(
            target_ltv=float(fund_cfg.get("target_ltv", 0.702)),
            hf_target=float(fund_cfg.get("hf_target", 1.21)),
            hf_band_lower=float(band[0]),
            hf_band_upper=float(band[1]),
            hf_full_unwind_below=float(triggers_full.get("hf_below", 1.13)),
            btc_24h_drop_threshold=float(triggers_full.get("btc_24h_drop_pct", -0.10)),
            negative_carry_window_hours=int(triggers_full.get("usdc_borrow_above_vault_yield_window_hours", 6)),
            negative_carry_consecutive_windows=int(triggers_full.get("usdc_borrow_above_vault_yield_windows", 2)),
            wrapper_peg_off_pct=float(triggers_full.get("wrapper_peg_off_pct", 0.01)),
            wrapper_peg_duration_min=int(triggers_full.get("wrapper_peg_duration_min", 40)),
            usdc_peg_off_pct=float(triggers_full.get("usdc_peg_off_pct", 0.005)),
            exploit_detection_enabled=bool(triggers_full.get("exploit_detection_on_venue_or_oracle", True)),
            venue_freeze_trigger_enabled=bool(triggers_full.get("venue_freezes_reserve_or_zeroes_ltv", True)),
            venue_change_price_source_enabled=bool(triggers_full.get("venue_changes_price_source", True)),
            wbtc_multisig_threshold_trigger=bool(triggers_full.get("wbtc_custodian_multisig_threshold_set_to_1", True)),
            cbbtc_owner_change_trigger=bool(triggers_full.get("cbbtc_owner_proxy_pauser_change_or_upgrade", True)),
            vault_owner_or_curator_change_trigger=bool(triggers_vault.get("vault_owner_or_curator_changes", True)),
            vault_share_price_drop_bp_per_min=float(triggers_vault.get("vault_share_price_drop_bp_per_min", 1.0)),
        )


class FundAgent:
    """Fund Agent executing the verified KPK BTC Carry policy triggers per SPEC §3.3."""

    def __init__(self, policy: FundAgentPolicy | None = None) -> None:
        self.policy = policy if policy is not None else FundAgentPolicy.from_yaml()

    def evaluate(
        self,
        t: int,
        price_history: np.ndarray,
        current_hf: float,
        borrow_rate_history: np.ndarray | None = None,
        deploy_rate_history: np.ndarray | None = None,
        *,
        wrapper_peg_history: np.ndarray | None = None,
        wrapper_peg_duration_min_observed: int = 0,
        usdc_peg_history: np.ndarray | None = None,
        exploit_detected: bool = False,
        venue_frozen_or_zero_ltv: bool = False,
        venue_changed_price_source: bool = False,
        custodian_multisig_threshold_to_1: bool = False,
        custodian_owner_or_proxy_changed: bool = False,
        vault_curator_changed: dict[str, bool] | None = None,
        vault_share_price_drop_bp: dict[str, float] | None = None,
        asset_type: str = "WBTC",
        minutes_per_step: int = 60,
    ) -> PolicyAction:
        """Evaluate policy triggers at hour t using strictly data up to t.

        Args:
          t: Current time index (>= 0).
          price_history: 1D array of BTC prices from index 0 to t inclusive.
          current_hf: Current position health factor at index t.
          borrow_rate_history: 1D array of borrow rates from index 0 to t.
          deploy_rate_history: 1D array of deployment yields from index 0 to t.
          wrapper_peg_history: 1D array of wrapper/BTC price ratios up to t (1.0 = par).
          wrapper_peg_duration_min_observed: Explicit duration in minutes wrapper depeg sustained.
          usdc_peg_history: 1D array of USDC/USD price up to t (1.0 = par).
          exploit_detected: Signal indicating exploit on venue or oracle feeds.
          venue_frozen_or_zero_ltv: Signal indicating venue froze reserve or set LTV to 0.
          venue_changed_price_source: Signal indicating venue changed price feed source.
          custodian_multisig_threshold_to_1: Custodian multisig threshold set to 1 (WBTC).
          custodian_owner_or_proxy_changed: Owner/proxy/pauser changed or upgraded (cbBTC).
          vault_curator_changed: Dict mapping vault names to curator change flags.
          vault_share_price_drop_bp: Dict mapping vault names to share price drop in bp/min.
          asset_type: Underlying wrapper asset under management ("WBTC" or "cbBTC").
          minutes_per_step: Granularity of simulation steps in minutes (default 60 for hourly).

        Returns:
          PolicyAction (HOLD, DELEVERAGE, VAULT_EXIT, or FULL_UNWIND).
        """
        # Strictly slice arrays up to t to prevent any future information leakage
        prices = np.asarray(price_history[: t + 1], dtype=float)

        # -------------------------------------------------------------
        # 1. Full Unwind: Exploit / Security Triggers
        # -------------------------------------------------------------
        if self.policy.exploit_detection_enabled and exploit_detected:
            return PolicyAction.full_unwind(
                reason="exploit_detected_on_venue_or_oracle",
                trigger_value=1.0,
            )

        if self.policy.venue_freeze_trigger_enabled and venue_frozen_or_zero_ltv:
            return PolicyAction.full_unwind(
                reason="venue_freezes_reserve_or_zeroes_ltv",
                trigger_value=1.0,
            )

        if self.policy.venue_change_price_source_enabled and venue_changed_price_source:
            return PolicyAction.full_unwind(
                reason="venue_changes_price_source",
                trigger_value=1.0,
            )

        # Custodian / Contract Triggers per asset
        if asset_type == "WBTC" and self.policy.wbtc_multisig_threshold_trigger and custodian_multisig_threshold_to_1:
            return PolicyAction.full_unwind(
                reason="wbtc_custodian_multisig_threshold_set_to_1",
                trigger_value=1.0,
            )

        if asset_type == "cbBTC" and self.policy.cbbtc_owner_change_trigger and custodian_owner_or_proxy_changed:
            return PolicyAction.full_unwind(
                reason="cbbtc_owner_proxy_pauser_change_or_upgrade",
                trigger_value=1.0,
            )

        # -------------------------------------------------------------
        # 2. Full Unwind: Stablecoin (USDC) Peg Breach (0.5% off peg)
        # -------------------------------------------------------------
        if usdc_peg_history is not None:
            u_hist = np.asarray(usdc_peg_history[: t + 1], dtype=float)
            usdc_p = u_hist[t]
            usdc_depeg = abs(1.0 - usdc_p)
            if usdc_depeg >= self.policy.usdc_peg_off_pct:
                return PolicyAction.full_unwind(
                    reason="usdc_peg_off_pct",
                    trigger_value=float(usdc_depeg),
                )

        # -------------------------------------------------------------
        # 3. Full Unwind: Wrapper Peg Breach (1% off sustained 40 min)
        # -------------------------------------------------------------
        if wrapper_peg_history is not None:
            w_hist = np.asarray(wrapper_peg_history[: t + 1], dtype=float)
            current_wrapper_ratio = w_hist[t]
            wrapper_depeg = abs(1.0 - current_wrapper_ratio)

            # Check sustained depeg duration
            sustained_minutes = wrapper_peg_duration_min_observed
            if sustained_minutes == 0 and wrapper_depeg >= self.policy.wrapper_peg_off_pct:
                # Count backward from t to find contiguous steps above threshold
                depeg_steps = 0
                for idx in range(t, -1, -1):
                    if abs(1.0 - w_hist[idx]) >= self.policy.wrapper_peg_off_pct:
                        depeg_steps += 1
                    else:
                        break
                sustained_minutes = depeg_steps * minutes_per_step

            if wrapper_depeg >= self.policy.wrapper_peg_off_pct and sustained_minutes >= self.policy.wrapper_peg_duration_min:
                return PolicyAction.full_unwind(
                    reason="wrapper_peg_off_sustained",
                    trigger_value=float(wrapper_depeg),
                )

        # -------------------------------------------------------------
        # 4. Partial Action: VAULT-EXIT Triggers
        #    (Exit that specific vault, hold/redeploy USDC; NOT a full unwind)
        # -------------------------------------------------------------
        if self.policy.vault_owner_or_curator_change_trigger and vault_curator_changed:
            for v_name, changed in vault_curator_changed.items():
                if changed:
                    return PolicyAction.vault_exit(
                        vault_name=v_name,
                        reason="vault_owner_or_curator_changes",
                        trigger_value=1.0,
                    )

        if vault_share_price_drop_bp:
            for v_name, drop_bp in vault_share_price_drop_bp.items():
                if drop_bp > self.policy.vault_share_price_drop_bp_per_min:
                    return PolicyAction.vault_exit(
                        vault_name=v_name,
                        reason="vault_share_price_drop_bp_per_min",
                        trigger_value=float(drop_bp),
                    )

        # -------------------------------------------------------------
        # 5. Full Unwind: HF Floor Breach (< 1.13)
        # -------------------------------------------------------------
        if current_hf < self.policy.hf_full_unwind_below:
            return PolicyAction.full_unwind(
                reason="hf_below_floor",
                trigger_value=current_hf,
            )

        # -------------------------------------------------------------
        # 6. Full Unwind: BTC 24-Hour Drop <= -10%
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
        # 7. Full Unwind: Consecutive Negative Carry Windows
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
            net_carry = d_hist - b_hist

            all_negative = True
            for w in range(n_windows):
                w_end = t - (w * w_size)
                w_start = w_end - w_size
                w_carry = net_carry[w_start:w_end]
                if np.mean(w_carry) >= 0.0:
                    all_negative = False
                    break

            if all_negative:
                return PolicyAction.full_unwind(
                    reason="negative_carry_consecutive_windows",
                    trigger_value=float(np.mean(net_carry[t - required_hours : t])),
                )

        # -------------------------------------------------------------
        # 8. Deleveraging Trigger: HF in [1.13, 1.15)
        # -------------------------------------------------------------
        if current_hf < self.policy.hf_band_lower:
            repay_frac = 1.0 - (current_hf / self.policy.hf_target)
            return PolicyAction.deleverage(
                repay_fraction=repay_frac,
                reason="hf_band_deleveraging",
                trigger_value=current_hf,
            )

        # -------------------------------------------------------------
        # 9. Normal: HOLD
        # -------------------------------------------------------------
        return PolicyAction.hold(reason="within_normal_parameters")


# =====================================================================
# AGENT POPULATION 1: Liquidator Agents (SPEC §3.3)
# =====================================================================

@dataclass
class LiquidatorAgent:
    """Individual liquidator bot with distinct capital, gas bid, and profit threshold.

    Executes liquidations when HF < 1.0, but ONLY when net profit after gas
    and market slippage exceeds its profit threshold.
    """

    agent_id: str
    capital_usd: float
    gas_bid_gwei: float
    profit_threshold_usd: float
    slippage_factor: float = 1e-7  # Slippage impact per dollar liquidated

    def evaluate_liquidation(
        self,
        debt_to_repay: float,
        seized_usd: float,
        gas_price_gwei: float,
        eth_usd_price: float = 3000.0,
        gas_used: int = 300_000,
    ) -> tuple[bool, float, float, float]:
        """Evaluate if liquidating this position is profitable.

        Returns:
          (is_profitable, net_profit_usd, slippage_usd, gas_cost_usd)
        """
        if debt_to_repay > self.capital_usd:
            return False, 0.0, 0.0, 0.0

        # Slippage on selling seized collateral: slippage_pct = slippage_factor * seized_usd
        slippage_pct = min(0.50, self.slippage_factor * seized_usd)
        slippage_usd = seized_usd * slippage_pct
        effective_proceeds = seized_usd - slippage_usd

        # Gas cost in USD: gas_used * gas_price * 1e-9 * eth_price
        gas_cost_usd = gas_used * (gas_price_gwei * 1e-9) * eth_usd_price

        # Net profit after debt repayment, slippage, and gas
        net_profit_usd = effective_proceeds - debt_to_repay - gas_cost_usd

        is_profitable = net_profit_usd >= self.profit_threshold_usd
        return is_profitable, net_profit_usd, slippage_usd, gas_cost_usd


@dataclass
class LiquidatorPopulation:
    """Heterogeneous liquidator population competing for liquidations.

    Liquidates positions with HF < 1.0 using standard close factor (50%).
    Under high gas (e.g. gas war), liquidators skip small or marginally
    profitable positions.
    """

    liquidators: list[LiquidatorAgent] = field(default_factory=list)
    close_factor: float = 0.50  # Liquidate up to 50% of outstanding loan

    @classmethod
    def create_default(cls, n_liquidators: int = 5, seed: int = 42) -> LiquidatorPopulation:
        """Create a standard heterogeneous population spanning bot sizes."""
        rng = np.random.default_rng(seed)
        bots = []
        # Tiered population: small, medium, large, institutional whales
        capitals = [50_000.0, 250_000.0, 1_000_000.0, 5_000_000.0, 20_000_000.0]
        gas_bids = [30.0, 50.0, 100.0, 200.0, 500.0]
        profit_thresholds = [15.0, 50.0, 150.0, 300.0, 1000.0]

        for i in range(min(n_liquidators, len(capitals))):
            bots.append(
                LiquidatorAgent(
                    agent_id=f"liq_bot_{i+1}",
                    capital_usd=capitals[i],
                    gas_bid_gwei=gas_bids[i],
                    profit_threshold_usd=profit_thresholds[i],
                    slippage_factor=1e-7 / (i + 1),
                )
            )
        return cls(liquidators=bots, close_factor=0.50)

    def liquidate_position(
        self,
        position_collateral_btc: float,
        position_debt_usdc: float,
        market_price: float,
        lltv: float,
        liquidation_bonus: float = 0.05,
        current_gas_price_gwei: float = 30.0,
        eth_usd_price: float = 3000.0,
    ) -> dict[str, Any] | None:
        """Attempt to liquidate an unhealthy position (HF < 1.0).

        Returns:
          Dict with liquidation details if executed, or None if HF >= 1.0 or unprofitable.
        """
        if position_debt_usdc <= 0.0:
            return None

        # Check health factor
        collat_val = position_collateral_btc * market_price
        hf = (collat_val * lltv) / position_debt_usdc
        if hf >= 1.0:
            return None  # Position is healthy

        # Close factor 50%
        debt_to_repay = position_debt_usdc * self.close_factor
        seized_usd = debt_to_repay * (1.0 + liquidation_bonus)
        seized_btc = min(position_collateral_btc, seized_usd / market_price)

        # Compete among liquidators: highest gas bidder / profitable bot executes
        best_bot: LiquidatorAgent | None = None
        best_profit = -float("inf")
        best_gas_cost = 0.0
        best_slippage = 0.0

        for bot in self.liquidators:
            # Bot can only bid if willing to pay at least current network gas
            eff_gas = max(bot.gas_bid_gwei, current_gas_price_gwei)
            profitable, profit, slip, gas_cost = bot.evaluate_liquidation(
                debt_to_repay=debt_to_repay,
                seized_usd=seized_usd,
                gas_price_gwei=eff_gas,
                eth_usd_price=eth_usd_price,
            )
            if profitable and profit > best_profit:
                best_bot = bot
                best_profit = profit
                best_gas_cost = gas_cost
                best_slippage = slip

        if best_bot is None:
            # Unliquidated due to insufficient profitability / gas costs / capital
            return None

        return {
            "executed": True,
            "liquidator_id": best_bot.agent_id,
            "repaid_debt_usdc": debt_to_repay,
            "seized_collateral_btc": seized_btc,
            "seized_collateral_usd": seized_usd,
            "net_profit_usd": best_profit,
            "gas_cost_usd": best_gas_cost,
            "slippage_usd": best_slippage,
            "remaining_collateral_btc": position_collateral_btc - seized_btc,
            "remaining_debt_usdc": position_debt_usdc - debt_to_repay,
        }


# =====================================================================
# AGENT POPULATION 2: Background Borrowers (SPEC §3.3)
# =====================================================================

@dataclass
class BorrowerPopulation:
    """Background borrower population driving Aave USDC pool utilisation.

    In crisis regimes (e.g. sharp market sell-offs), underwater borrowers get
    liquidated and sell collateral en masse, causing a liquidation cascade.
    Solvent borrowers also panic-deleverage to avoid liquidation.
    """

    total_collateral_btc: float = 20_000.0
    total_debt_usdc: float = 600_000_000.0
    pool_total_liquidity_usdc: float = 750_000_000.0
    crisis_deleverage_pct: float = 0.15     # 15% deleveraging in crisis
    cascade_liquidation_pct: float = 0.10   # 10% collateral liquidated into market

    @property
    def utilisation(self) -> float:
        """Current Aave pool utilisation ratio."""
        if self.pool_total_liquidity_usdc <= 0.0:
            return 1.0
        return float(np.clip(self.total_debt_usdc / self.pool_total_liquidity_usdc, 0.0, 1.0))

    def step(
        self,
        current_btc_price: float,
        price_drop_24h: float = 0.0,
        is_crisis: bool = False,
    ) -> dict[str, float]:
        """Advance borrower population by 1 step, generating cascade effects if in crisis."""
        is_stress = is_crisis or (price_drop_24h <= -0.10)
        collateral_sold_btc = 0.0
        debt_repaid_usdc = 0.0

        if is_stress:
            # 1. Cascade liquidations: seized collateral dumped on market
            liq_collateral = self.total_collateral_btc * self.cascade_liquidation_pct
            liq_debt = (liq_collateral * current_btc_price * 0.70)
            self.total_collateral_btc -= liq_collateral
            self.total_debt_usdc = max(0.0, self.total_debt_usdc - liq_debt)
            collateral_sold_btc += liq_collateral

            # 2. Panic deleveraging: borrowers repay to protect HF
            panic_repay = self.total_debt_usdc * self.crisis_deleverage_pct
            self.total_debt_usdc = max(0.0, self.total_debt_usdc - panic_repay)
            debt_repaid_usdc += liq_debt + panic_repay

        return {
            "new_utilisation": self.utilisation,
            "collateral_sold_btc": collateral_sold_btc,
            "debt_repaid_usdc": debt_repaid_usdc,
            "cascade_active": is_stress,
        }


# =====================================================================
# AGENT POPULATION 3: Depositors (SPEC §3.3)
# =====================================================================

@dataclass
class DepositorPopulation:
    """Deployment vault depositors.

    Under calm regimes, vault deposits remain stable.
    Under stress regimes (e.g. S7 EURC crunch or collateral exploits),
    depositors withdraw capital en masse, driving utilisation to 100%
    and trapping the fund's borrowed-USDC leg (withdrawal lock).
    """

    vault_tvl: float = 100_000_000.0
    vault_borrows: float = 80_000_000.0
    normal_utilisation: float = 0.80

    @property
    def utilisation(self) -> float:
        """Current deployment vault utilisation."""
        if self.vault_tvl <= 0.0:
            return 1.0
        return float(np.clip(self.vault_borrows / self.vault_tvl, 0.0, 1.0))

    @property
    def is_withdrawal_blocked(self) -> bool:
        """Withdrawals are blocked when utilisation reaches 100%."""
        return self.utilisation >= 0.9999

    def step(
        self,
        is_stress_regime: bool = False,
        withdrawal_run_pct: float = 0.20,
    ) -> dict[str, Any]:
        """Advance 1 step. Under stress, withdrawals shrink TVL toward borrowed amount."""
        withdrawn_usd = 0.0
        if is_stress_regime:
            available_liquidity = max(0.0, self.vault_tvl - self.vault_borrows)
            # Withdraw fraction of available liquidity
            withdraw_request = available_liquidity * withdrawal_run_pct
            withdrawn_usd = min(available_liquidity, withdraw_request)
            self.vault_tvl -= withdrawn_usd

            # If stress is severe, TVL is driven exactly to borrows (100% utilisation)
            if withdrawal_run_pct >= 0.99:
                self.vault_tvl = self.vault_borrows

        return {
            "vault_tvl": self.vault_tvl,
            "vault_borrows": self.vault_borrows,
            "utilisation": self.utilisation,
            "is_withdrawal_blocked": self.is_withdrawal_blocked,
            "withdrawn_usd": withdrawn_usd,
        }


# =====================================================================
# AGENT POPULATION 4: Arbitrageurs (SPEC §3.3)
# =====================================================================

@dataclass
class ArbitrageurPopulation:
    """Arbitrageur population restoring wrapper pegs (WBTC/cbBTC vs BTC).

    Restores wrapper pegs at a speed proportional to the discount and available capital.
    Under hack/exploit scenarios (redemption halts), available capital halves,
    causing depegs to persist.
    """

    base_capital_usd: float = 20_000_000.0
    reversion_speed: float = 0.25  # Speed of peg restoration per hour

    def step(
        self,
        current_peg_ratio: float,
        is_hack_event: bool = False,
    ) -> float:
        """Advance arbitrageur restoration by 1 step.

        Args:
          current_peg_ratio: Ratio of wrapper price to BTC price (e.g. 0.95 = -5% depeg).
          is_hack_event: If True, arbitrageur capital halves due to redemption halts.

        Returns:
          new_peg_ratio: Restored peg ratio.
        """
        # Under hack events (bridges/redemptions halted), arbitrageur capital halves
        capital_factor = 0.50 if is_hack_event else 1.0
        effective_capital = self.base_capital_usd * capital_factor

        discount = 1.0 - current_peg_ratio
        if discount <= 0.0:
            return 1.0  # Already on or above par

        # Peg restoration speed is proportional to discount and capital
        restoration = self.reversion_speed * (effective_capital / self.base_capital_usd) * discount
        new_ratio = float(min(1.0, current_peg_ratio + restoration))
        return new_ratio
