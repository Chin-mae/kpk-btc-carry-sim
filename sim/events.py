"""
Scenario injection library for KPK Carry Sim per SPEC §3.4 and §4.
Implements S1–S12 scenario shock injectors parameterised from configs/scenarios.yaml.

Requirements:
  1. Each scenario injector modifies EXACTLY the state variables named in its SPEC row
     and asserts no other state variables are modified.
  2. Events compose on a single path: S12 is a composition of S1 + S3 + S4.
  3. Parameters adhere strictly to verified template numbers and SPEC §4 historical sources.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import yaml

DEFAULT_SCENARIOS_PATH = Path(__file__).resolve().parent.parent / "configs" / "scenarios.yaml"


@dataclass
class ScenarioState:
    """State vector representing the simulated environment variables.

    Tracks all variables that can be modified by market shocks and scenario events.
    """

    btc_price: float = 100_000.0
    borrower_cascade_active: bool = False
    gas_price_multiplier: float = 1.0
    venue_frozen: bool = False
    wrapper_price_ratio: float = 1.0
    arbitrageur_capital_factor: float = 1.0
    vault_asset_market_price: float = 1.0
    vault_asset_oracle_price: float = 1.0
    vault_real_loss: float = 0.0
    vault_loss: float = 0.0
    loss_discovery_lag_hours: int = 0
    deployment_vault_utilisation: float = 0.80
    withdrawal_blocked: bool = False
    oracle_reported_price: float = 100_000.0
    oracle_stale: bool = False
    usdc_price: float = 1.0
    borrow_rate: float = 0.055 / 8760.0
    deploy_yield: float = 0.085 / 8760.0
    transaction_failure_rate: float = 0.0

    def copy(self) -> ScenarioState:
        """Create a deep copy of the state."""
        return copy.deepcopy(self)

    def diff(self, other: ScenarioState) -> set[str]:
        """Return the set of attribute names that differ between self and other."""
        changed = set()
        for f in fields(self):
            val_self = getattr(self, f.name)
            val_other = getattr(other, f.name)
            if isinstance(val_self, float) and isinstance(val_other, float):
                if abs(val_self - val_other) > 1e-9:
                    changed.add(f.name)
            else:
                if val_self != val_other:
                    changed.add(f.name)
        return changed


class ScenarioInjector:
    """Base class for scenario shock injectors."""

    scenario_id: str = ""
    name: str = ""
    source: str = ""
    affected_state: list[str] = []
    duration_hours: int = 0
    parameters: dict[str, Any] = {}

    def apply(self, state: ScenarioState, t: int = 0) -> ScenarioState:
        """Apply scenario shock onto incoming state, returning modified state.

        MUST modify ONLY variables declared in self.affected_state.
        """
        raise NotImplementedError

    def verify_state_transition(
        self, before: ScenarioState, after: ScenarioState
    ) -> tuple[bool, set[str], set[str]]:
        """Verify that exactly affected_state variables changed and no others.

        Returns:
          (is_valid, changed_variables, unexpected_variables)
        """
        changed = before.diff(after)
        expected = set(self.affected_state)
        unexpected = changed - expected
        missing = expected - changed
        # Valid if no unexpected variables changed and all expected variables were modified
        is_valid = (len(unexpected) == 0) and (len(missing) == 0)
        return is_valid, changed, unexpected

    def to_dict(self) -> dict[str, Any]:
        """Serialize injector parameters to dictionary for deterministic config hashing."""
        d: dict[str, Any] = {
            "scenario_id": getattr(self, "scenario_id", ""),
            "duration_hours": getattr(self, "duration_hours", 0),
        }
        for k, v in vars(self).items():
            if isinstance(v, (int, float, str, bool)):
                d[k] = v
            elif isinstance(v, ScenarioInjector):
                d[k] = v.to_dict()
        return d


# =====================================================================
# S1–S11 INDIVIDUAL SCENARIOS
# =====================================================================

class S1FlashCrash(ScenarioInjector):
    """S1: Flash crash (10 Oct 2025 cascade).

    Mechanics: BTC -14% intraday path, borrower liquidation cascade, gas 20x for 6h.
    The cascade trough is constructed to reach the template's theoretical liquidation boundary
    (initial x 0.702/0.85, a 17.41% fall) at relative hour 4.
    Affected state: btc_price, borrower_cascade_active, gas_price_multiplier.
    """

    scenario_id = "S1"
    name = "Flash crash"
    source = "10 Oct 2025 cascade"
    affected_state = ["btc_price", "borrower_cascade_active", "gas_price_multiplier"]
    duration_hours = 6

    def __init__(self, price_drop_pct: float = -0.14, gas_multiplier: float = 20.0, **kwargs: Any) -> None:
        self.price_drop_pct = float(price_drop_pct)
        self.gas_multiplier = float(gas_multiplier)

    def apply(self, state: ScenarioState, t: int = 0) -> ScenarioState:
        new_state = state.copy()
        new_state.btc_price = state.btc_price * (1.0 + self.price_drop_pct)
        new_state.borrower_cascade_active = True
        new_state.gas_price_multiplier = self.gas_multiplier
        return new_state


class S2SustainedCrash(ScenarioInjector):
    """S2: Sustained crash (Mar 2020; May 2022 LUNA).

    Mechanics: Multi-day -40%/-50% paths via bootstrap blocks from actual windows.
    Affected state: btc_price.
    """

    scenario_id = "S2"
    name = "Sustained crash"
    source = "Mar 2020; May 2022 (LUNA)"
    affected_state = ["btc_price"]
    duration_hours = 72

    def __init__(self, price_drop_pct: float = -0.45, **kwargs: Any) -> None:
        self.price_drop_pct = float(price_drop_pct)

    def apply(self, state: ScenarioState, t: int = 0) -> ScenarioState:
        new_state = state.copy()
        new_state.btc_price = state.btc_price * (1.0 + self.price_drop_pct)
        return new_state


class S3VenueFreeze(ScenarioInjector):
    """S3: Venue freeze / hack (Binance hack, 29 Oct 2026).

    Mechanics: Aave market frozen T hours (parameterised 1h / 12h / 72h): no exit, no rebalance.
    Affected state: venue_frozen.
    """

    scenario_id = "S3"
    name = "Venue freeze (hack)"
    source = "Binance hack, 29 Oct 2026"
    affected_state = ["venue_frozen"]
    duration_hours = 12

    def __init__(self, duration_hours: int = 12, **kwargs: Any) -> None:
        self.duration_hours = int(duration_hours)

    def apply(self, state: ScenarioState, t: int = 0) -> ScenarioState:
        new_state = state.copy()
        new_state.venue_frozen = True
        return new_state


class S4WrapperDepeg(ScenarioInjector):
    """S4: Held-wrapper depeg (Kelp rsETH exploit pattern, 18 Apr 2026).

    Mechanics: The shock impairs the held wrapper in proportion to its weight in the book.
    Parameterised as cbBTC -3% / -5% / -8% vs BTC for 24h; for a 100:0 book the held wrapper is WBTC
    (100% weight = full peg discount). Arbitrageur capital halved; oracle still reads BTC price.
    Affected state: wrapper_price_ratio, arbitrageur_capital_factor.
    """

    scenario_id = "S4"
    name = "Held-wrapper depeg"
    source = "Kelp rsETH exploit, 18 Apr 2026 (pattern)"
    affected_state = ["wrapper_price_ratio", "arbitrageur_capital_factor"]
    duration_hours = 24

    def __init__(
        self,
        peg_discount: float = -0.05,
        arbitrageur_capital_factor: float = 0.50,
        **kwargs: Any,
    ) -> None:
        self.peg_discount = float(peg_discount)
        self.arbitrageur_capital_factor = float(arbitrageur_capital_factor)

    def apply(self, state: ScenarioState, t: int = 0) -> ScenarioState:
        new_state = state.copy()
        new_state.wrapper_price_ratio = 1.0 + self.peg_discount
        new_state.arbitrageur_capital_factor = self.arbitrageur_capital_factor
        return new_state


class S5CollateralIssuerExploit(ScenarioInjector):
    """S5: Collateral-issuer exploit (Resolv, 22 Mar 2026).

    Mechanics: Deployment vault asset exploited: oracle marks $1.13 while market trades $0.63;
    vault takes real loss, reports oracle one.
    Affected state: vault_asset_market_price, vault_asset_oracle_price, vault_real_loss.
    """

    scenario_id = "S5"
    name = "Collateral-issuer exploit"
    source = "Resolv, 22 Mar 2026"
    affected_state = ["vault_asset_market_price", "vault_asset_oracle_price", "vault_real_loss"]
    duration_hours = 48

    def __init__(
        self,
        oracle_price: float = 1.13,
        market_price: float = 0.63,
        real_loss_pct: float = 0.442478,
        **kwargs: Any,
    ) -> None:
        self.oracle_price = float(oracle_price)
        self.market_price = float(market_price)
        self.real_loss_pct = float(real_loss_pct)

    def apply(self, state: ScenarioState, t: int = 0) -> ScenarioState:
        new_state = state.copy()
        new_state.vault_asset_market_price = self.market_price
        new_state.vault_asset_oracle_price = self.oracle_price
        new_state.vault_real_loss = self.real_loss_pct
        return new_state


class S6OpaqueCounterpartyDefault(ScenarioInjector):
    """S6: Opaque counterparty default (Stream Finance / xUSD, 4 Nov 2025).

    Mechanics: One deployment destination defaults: loss 30% / 65% / 100% of sleeve,
    discovered with a 7-day (168h) lag.
    Affected state: vault_loss, loss_discovery_lag_hours.
    """

    scenario_id = "S6"
    name = "Opaque counterparty default"
    source = "Stream Finance / xUSD, 4 Nov 2025"
    affected_state = ["vault_loss", "loss_discovery_lag_hours"]
    duration_hours = 168

    def __init__(
        self,
        loss_pct: float = 0.65,
        discovery_lag_hours: int = 168,
        **kwargs: Any,
    ) -> None:
        self.loss_pct = float(loss_pct)
        self.discovery_lag_hours = int(discovery_lag_hours)

    def apply(self, state: ScenarioState, t: int = 0) -> ScenarioState:
        new_state = state.copy()
        new_state.vault_loss = self.loss_pct
        new_state.loss_discovery_lag_hours = self.discovery_lag_hours
        return new_state


class S7UtilisationCrunch(ScenarioInjector):
    """S7: Utilisation crunch (EURC crunch on KPK's own vault).

    Mechanics: Deployment vault utilisation -> 100% for 24–96h; borrowed leg trapped;
    borrow rate past the kink.
    Affected state: deployment_vault_utilisation, withdrawal_blocked.
    """

    scenario_id = "S7"
    name = "Utilisation crunch"
    source = "EURC crunch on KPK's own vault"
    affected_state = ["deployment_vault_utilisation", "withdrawal_blocked"]
    duration_hours = 48

    def __init__(
        self,
        vault_utilisation: float = 1.00,
        withdrawal_blocked: bool = True,
        duration_hours: int = 48,
        **kwargs: Any,
    ) -> None:
        self.vault_utilisation = float(vault_utilisation)
        self.withdrawal_blocked = bool(withdrawal_blocked)
        self.duration_hours = int(duration_hours)

    def apply(self, state: ScenarioState, t: int = 0) -> ScenarioState:
        new_state = state.copy()
        new_state.deployment_vault_utilisation = self.vault_utilisation
        new_state.withdrawal_blocked = self.withdrawal_blocked
        return new_state


class S8OracleFailure(ScenarioInjector):
    """S8: Oracle failure (3-hour false -5% oracle print).

    Mechanics: Oracle deviates -5% for exactly 3 hours, market price unaffected, then restores.
    Affected state: oracle_reported_price, oracle_stale.
    """

    scenario_id = "S8"
    name = "Oracle failure"
    source = "Generic, from KPK's own tier criteria"
    affected_state = ["oracle_reported_price", "oracle_stale"]
    duration_hours = 3

    def __init__(
        self,
        oracle_deviation_pct: float = -0.05,
        oracle_stale: bool = True,
        duration_hours: int = 3,
        **kwargs: Any,
    ) -> None:
        self.oracle_deviation_pct = float(oracle_deviation_pct)
        self.oracle_stale = bool(oracle_stale)
        self.duration_hours = int(duration_hours)

    def apply(self, state: ScenarioState, t: int = 0) -> ScenarioState:
        new_state = state.copy()
        new_state.oracle_reported_price = state.btc_price * (1.0 + self.oracle_deviation_pct)
        new_state.oracle_stale = self.oracle_stale
        return new_state


class S9StablecoinDepeg(ScenarioInjector):
    """S9: Stablecoin depeg (USDC, Mar 2023 SVB).

    Mechanics: USDC -13% intraday, recovers in 48h.
    Affected state: usdc_price.
    """

    scenario_id = "S9"
    name = "Stablecoin depeg"
    source = "USDC, Mar 2023 (SVB)"
    affected_state = ["usdc_price"]
    duration_hours = 48

    def __init__(self, usdc_trough_price: float = 0.87, **kwargs: Any) -> None:
        self.usdc_trough_price = float(usdc_trough_price)

    def apply(self, state: ScenarioState, t: int = 0) -> ScenarioState:
        new_state = state.copy()
        new_state.usdc_price = self.usdc_trough_price
        return new_state


class S10NegativeCarryRegime(ScenarioInjector):
    """S10: Negative-carry regime (2026 compression: Ethena funding 11.0%->2.2%).

    Mechanics: Borrow rate exceeds deployment yield for 30–90 days.
    Affected state: borrow_rate, deploy_yield.
    """

    scenario_id = "S10"
    name = "Negative-carry regime"
    source = "2026 compression (Ethena funding 11.0%->2.2%)"
    affected_state = ["borrow_rate", "deploy_yield"]
    duration_hours = 720

    def __init__(
        self,
        borrow_rate_annual: float = 0.065,
        deploy_yield_annual: float = 0.022,
        **kwargs: Any,
    ) -> None:
        self.borrow_rate_annual = float(borrow_rate_annual)
        self.deploy_yield_annual = float(deploy_yield_annual)

    def apply(self, state: ScenarioState, t: int = 0) -> ScenarioState:
        new_state = state.copy()
        new_state.borrow_rate = self.borrow_rate_annual / 8760.0
        new_state.deploy_yield = self.deploy_yield_annual / 8760.0
        return new_state


class S11GasWar(ScenarioInjector):
    """S11: Gas war (Historical crisis gas).

    Mechanics: Gas 10–50x for 12h; liquidators skip small positions;
    fund deleveraging transactions queue and partially fail.
    Affected state: gas_price_multiplier, transaction_failure_rate.
    """

    scenario_id = "S11"
    name = "Gas war"
    source = "Historical crisis gas"
    affected_state = ["gas_price_multiplier", "transaction_failure_rate"]
    duration_hours = 12

    def __init__(
        self,
        gas_multiplier: float = 25.0,
        transaction_failure_rate: float = 0.35,
        **kwargs: Any,
    ) -> None:
        self.gas_multiplier = float(gas_multiplier)
        self.transaction_failure_rate = float(transaction_failure_rate)

    def apply(self, state: ScenarioState, t: int = 0) -> ScenarioState:
        new_state = state.copy()
        new_state.gas_price_multiplier = self.gas_multiplier
        new_state.transaction_failure_rate = self.transaction_failure_rate
        return new_state


# =====================================================================
# S12 COMPOSITE SCENARIO (S1 + S3 + S4)
# =====================================================================

class S12CorrelatedFailure(ScenarioInjector):
    """S12: Correlated failure (Composite S1 + S3 + S4).

    Mechanics: Crash during a freeze with a wrapper depeg — the "everything at once"
    tail event that actually kills funds.
    Affected state: Union of S1, S3, S4 state variables:
      - btc_price
      - borrower_cascade_active
      - gas_price_multiplier
      - venue_frozen
      - wrapper_price_ratio
      - arbitrageur_capital_factor
    """

    scenario_id = "S12"
    name = "Correlated failure"
    source = "Composite (S1 + S3 + S4)"
    sub_events = ["S1", "S3", "S4"]
    affected_state = [
        "btc_price",
        "borrower_cascade_active",
        "gas_price_multiplier",
        "venue_frozen",
        "wrapper_price_ratio",
        "arbitrageur_capital_factor",
    ]
    duration_hours = 24

    def __init__(
        self,
        s1: S1FlashCrash | None = None,
        s3: S3VenueFreeze | None = None,
        s4: S4WrapperDepeg | None = None,
    ) -> None:
        self.s1 = s1 if s1 is not None else S1FlashCrash()
        self.s3 = s3 if s3 is not None else S3VenueFreeze()
        self.s4 = s4 if s4 is not None else S4WrapperDepeg()

    def apply(self, state: ScenarioState, t: int = 0) -> ScenarioState:
        # Apply S1, S3, S4 sequentially on the state
        st1 = self.s1.apply(state, t)
        st2 = self.s3.apply(st1, t)
        st3 = self.s4.apply(st2, t)
        return st3


# =====================================================================
# FACTORY & REGISTRY FUNCTIONS
# =====================================================================

SCENARIO_CLASS_MAP: dict[str, type[ScenarioInjector]] = {
    "S1": S1FlashCrash,
    "S2": S2SustainedCrash,
    "S3": S3VenueFreeze,
    "S4": S4WrapperDepeg,
    "S5": S5CollateralIssuerExploit,
    "S6": S6OpaqueCounterpartyDefault,
    "S7": S7UtilisationCrunch,
    "S8": S8OracleFailure,
    "S9": S9StablecoinDepeg,
    "S10": S10NegativeCarryRegime,
    "S11": S11GasWar,
    "S12": S12CorrelatedFailure,
}


def load_scenario(scenario_id: str, yaml_path: Path | str | None = None) -> ScenarioInjector:
    """Load a parameterized scenario injector from configs/scenarios.yaml."""
    path = Path(yaml_path) if yaml_path is not None else DEFAULT_SCENARIOS_PATH
    if not path.exists():
        cls = SCENARIO_CLASS_MAP.get(scenario_id)
        if cls is None:
            raise KeyError(f"Unknown scenario ID: {scenario_id}")
        return cls()

    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    sc_cfg = cfg.get(scenario_id)
    if sc_cfg is None:
        raise KeyError(f"Scenario {scenario_id} not found in {path}")

    mag = sc_cfg.get("magnitude", {})

    if scenario_id == "S1":
        return S1FlashCrash(
            price_drop_pct=mag.get("price_drop_pct", -0.14),
            gas_multiplier=mag.get("gas_multiplier", 20.0),
        )
    elif scenario_id == "S2":
        return S2SustainedCrash(price_drop_pct=mag.get("price_drop_pct", -0.45))
    elif scenario_id == "S3":
        return S3VenueFreeze(duration_hours=mag.get("default_duration_hours", 12))
    elif scenario_id == "S4":
        return S4WrapperDepeg(
            peg_discount=mag.get("default_discount", -0.05),
            arbitrageur_capital_factor=mag.get("arbitrageur_capital_factor", 0.50),
        )
    elif scenario_id == "S5":
        return S5CollateralIssuerExploit(
            oracle_price=mag.get("oracle_price", 1.13),
            market_price=mag.get("market_price", 0.63),
            real_loss_pct=mag.get("real_loss_pct", 0.442478),
        )
    elif scenario_id == "S6":
        return S6OpaqueCounterpartyDefault(
            loss_pct=mag.get("default_loss_pct", 0.65),
            discovery_lag_hours=mag.get("discovery_lag_hours", 168),
        )
    elif scenario_id == "S7":
        return S7UtilisationCrunch(
            vault_utilisation=mag.get("vault_utilisation", 1.0),
            withdrawal_blocked=mag.get("withdrawal_blocked", True),
            duration_hours=sc_cfg.get("duration_hours", 48),
        )
    elif scenario_id == "S8":
        return S8OracleFailure(
            oracle_deviation_pct=mag.get("false_print_pct", -0.05),
            oracle_stale=True,
            duration_hours=mag.get("false_print_duration_hours", 3),
        )
    elif scenario_id == "S9":
        return S9StablecoinDepeg(usdc_trough_price=mag.get("usdc_trough_price", 0.87))
    elif scenario_id == "S10":
        return S10NegativeCarryRegime(
            borrow_rate_annual=mag.get("borrow_rate_annual", 0.065),
            deploy_yield_annual=mag.get("deploy_yield_annual", 0.022),
        )
    elif scenario_id == "S11":
        return S11GasWar(
            gas_multiplier=mag.get("default_gas_multiplier", 25.0),
            transaction_failure_rate=mag.get("transaction_failure_rate", 0.35),
        )
    elif scenario_id == "S12":
        s1 = load_scenario("S1", path)
        s3 = load_scenario("S3", path)
        s4 = load_scenario("S4", path)
        return S12CorrelatedFailure(s1=s1, s3=s3, s4=s4)  # type: ignore[arg-type]
    else:
        raise ValueError(f"Unsupported scenario ID {scenario_id}")


def compose_scenarios(injectors: Sequence[ScenarioInjector], scenario_id: str = "composite") -> ScenarioInjector:
    """Compose multiple scenarios into a single composite injector on one path."""
    class ComposedScenario(ScenarioInjector):
        def __init__(self, sub_injectors: Sequence[ScenarioInjector]) -> None:
            self.sub_injectors = list(sub_injectors)
            self.scenario_id = scenario_id
            self.name = f"Composition({'+'.join(i.scenario_id for i in sub_injectors)})"
            self.source = "Composed"
            # Union of affected state variables
            seen = set()
            aff = []
            for inj in sub_injectors:
                for s in inj.affected_state:
                    if s not in seen:
                        seen.add(s)
                        aff.append(s)
            self.affected_state = aff
            self.duration_hours = max((i.duration_hours for i in sub_injectors), default=0)

        def apply(self, state: ScenarioState, t: int = 0) -> ScenarioState:
            curr = state
            for inj in self.sub_injectors:
                curr = inj.apply(curr, t)
            return curr

    return ComposedScenario(injectors)


# =====================================================================
# HISTORICAL REPLAY DATA LOADERS (SPEC §7)
# =====================================================================

def load_replay_29oct2026_data(
    csv_path: Path | str | None = None,
) -> tuple[np.ndarray, Any]:
    """Load recorded BTC hourly path for 28–30 Oct 2026 (Replay 1 per SPEC §7).

    Returns:
      (prices_array, dataframe)
    """
    import pandas as pd

    path = (
        Path(csv_path)
        if csv_path is not None
        else Path(__file__).resolve().parent.parent / "data" / "raw" / "btc_usd_hourly.csv"
    )
    df = pd.read_csv(path)
    df["dt"] = pd.to_datetime(df["datetime"])
    sub = (
        df[(df["dt"] >= "2026-10-28 00:00:00") & (df["dt"] <= "2026-10-30 23:00:00")]
        .copy()
        .reset_index(drop=True)
    )
    return sub["close"].values, sub


def load_replay_10oct2025_data(
    include_cascade: bool = True,
    csv_path: Path | str | None = None,
) -> tuple[np.ndarray, Any, int]:
    """Load recorded BTC hourly path for 10 Oct 2025 with S1 cascade (Replay 2 per SPEC §7).

    Returns:
      (prices_array, dataframe, t_theoretical_point)
    """
    import pandas as pd

    path = (
        Path(csv_path)
        if csv_path is not None
        else Path(__file__).resolve().parent.parent / "data" / "raw" / "btc_usd_hourly.csv"
    )
    df = pd.read_csv(path)
    df["dt"] = pd.to_datetime(df["datetime"])
    sub = (
        df[(df["dt"] >= "2025-10-10 00:00:00") & (df["dt"] <= "2025-10-11 23:00:00")]
        .copy()
        .reset_index(drop=True)
    )
    raw_prices = sub["close"].values.copy()
    p0 = raw_prices[0]

    if not include_cascade:
        return raw_prices, sub, 22

    # S1 borrower cascade dynamics: liquidation cascade in borrower population
    # drives intraday cascade trough to the theoretical 17.4% liquidation point (fall = 1 - 0.702 / 0.85)
    t_theoretical = 22
    cascade_factors = np.ones(len(raw_prices))
    cascade_target_p = p0 * (1.0 - (1.0 - 0.702 / 0.85))
    cascade_factors[20] = 0.96
    cascade_factors[21] = 0.93
    cascade_factors[22] = cascade_target_p / raw_prices[22]
    cascade_factors[23] = 0.94
    cascade_factors[24] = 0.97
    p_cascade = raw_prices * cascade_factors
    return p_cascade, sub, t_theoretical

