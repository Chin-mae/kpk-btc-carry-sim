"""
Verification test asserting that all scenario runs exercise the sim/events.py layer.
Per BRIEF Deliverable:
"add a test that fails if any scenario run's losses were produced without the
events layer being exercised — e.g. assert event state variables changed during scenario runs".
"""

import numpy as np
import pytest

from sim.engine import FundConfig, run_simulation
from sim.events import ScenarioInjector, ScenarioState, load_scenario


@pytest.fixture
def mock_paths():
    """Deterministic price path: 10 paths, 72 hours, constant $100,000 BTC."""
    np.random.seed(42)
    base = 100000.0
    # Add tiny noise to avoid 0 volatility issues
    noise = np.random.normal(0, 0.001, (10, 72))
    return base * np.exp(np.cumsum(noise, axis=1))


@pytest.fixture
def template_config():
    """Template cell configuration (cell-163)."""
    return FundConfig(
        target_ltv=0.702,
        hf_full_unwind_below=1.13,
        sleeve_split={"WBTC": 1.0, "cbBTC": 0.0},
        deployment_cap="none",
        deployment_cap_pct=None,
        policy_type="kpk_published",
    )


def test_baseline_run_has_no_scenario_events(mock_paths, template_config):
    """An unshocked baseline run must NOT have scenario events exercised."""
    res = run_simulation(mock_paths, template_config, scenario_injector=None)
    assert res.scenario_events_exercised is None


@pytest.mark.parametrize("scenario_idx", range(1, 13))
def test_all_scenarios_exercise_events_layer(mock_paths, template_config, scenario_idx):
    """Each scenario S1-S12 must exercise the events layer, mutating declared state variables."""
    s_id = f"S{scenario_idx}"
    injector = load_scenario(s_id)

    res = run_simulation(
        mock_paths,
        template_config,
        scenario_injector=injector,
        scenario_start_hour=10,
    )

    # 1. Traceability dictionary must exist
    assert res.scenario_events_exercised is not None, (
        f"Scenario {s_id} did not produce scenario_events_exercised metadata."
    )

    # 2. Shock must have been recorded as applied
    assert res.scenario_events_exercised["shock_applied"] is True

    # 3. Scenario ID must match
    assert res.scenario_events_exercised["scenario_id"] == s_id

    # 4. State variables must have actually changed
    changed_vars = set(res.scenario_events_exercised["changed_variables"])
    affected_declared = set(res.scenario_events_exercised["affected_state"])

    assert len(changed_vars) > 0, (
        f"Scenario {s_id} had zero state variables changed! Events layer was not exercised."
    )
    assert changed_vars.issubset(affected_declared), (
        f"Scenario {s_id} changed variables {changed_vars} not in declared affected_state {affected_declared}."
    )


def test_scenario_run_fails_if_events_layer_not_exercised():
    """Assert that a scenario evaluation gate rejects results if events were unexercised."""
    class DummyFakeInjector(ScenarioInjector):
        scenario_id = "FAKE"
        description = "Fake injector that mutates nothing"
        affected_state = []

        def apply(self, state: ScenarioState, t: int) -> ScenarioState:
            # Does not mutate state at all
            return state

    paths = np.full((5, 48), 100000.0)
    cfg = FundConfig(target_ltv=0.702)
    fake_inj = DummyFakeInjector()

    res = run_simulation(paths, cfg, scenario_injector=fake_inj)

    # Validation check that sweep / evaluation pipelines must enforce:
    # If shock_applied is not True or changed_variables is empty, reject run.
    events_meta = res.scenario_events_exercised
    assert events_meta is not None
    # Changed variables is empty because state was untouched
    assert len(events_meta["changed_variables"]) == 0

    def assert_valid_scenario_run(results):
        meta = results.scenario_events_exercised
        if not meta or not meta.get("shock_applied") or len(meta.get("changed_variables", [])) == 0:
            raise RuntimeError("CRITICAL: Scenario run fabricated! Events layer was not exercised.")

    with pytest.raises(RuntimeError, match="CRITICAL: Scenario run fabricated"):
        assert_valid_scenario_run(res)
