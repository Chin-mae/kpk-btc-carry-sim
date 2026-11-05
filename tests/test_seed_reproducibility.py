"""
Seed reproducibility tests per SPEC §1, §7, §8 and BRIEF.

Requirements:
  1. Same seed -> identical paths (bit-for-bit np.array_equal) across all three generators.
  2. Same seed -> identical simulation engine results across all output metrics.
  3. Different seed -> different paths and different engine results.
"""

import numpy as np
import pytest

from sim.engine import FundConfig, run_simulation
from sim.prices import (
    HistoricalBlockBootstrap,
    JumpDiffusion,
    RegimeSwitching,
)


@pytest.mark.parametrize(
    "generator_cls",
    [HistoricalBlockBootstrap, JumpDiffusion, RegimeSwitching],
)
def test_price_generators_seed_reproducibility(generator_cls):
    """Verify that same seed yields identical price paths and different seed yields different paths."""
    gen = generator_cls()
    n_paths = 10
    n_hours = 168  # 1 week
    seed_a = 42
    seed_b = 999

    paths_a1 = gen.generate(n_paths=n_paths, n_hours=n_hours, seed=seed_a, s0=100.0)
    paths_a2 = gen.generate(n_paths=n_paths, n_hours=n_hours, seed=seed_a, s0=100.0)
    paths_b = gen.generate(n_paths=n_paths, n_hours=n_hours, seed=seed_b, s0=100.0)

    # Identical seed must match bit-for-bit
    assert paths_a1.shape == (n_paths, n_hours + 1)
    assert np.array_equal(paths_a1, paths_a2), (
        f"{generator_cls.__name__} failed bit-for-bit reproducibility for seed {seed_a}"
    )

    # Different seeds must produce different results
    assert not np.array_equal(paths_a1, paths_b), (
        f"{generator_cls.__name__} produced identical paths for different seeds {seed_a} and {seed_b}"
    )


def test_engine_seed_reproducibility():
    """Verify that identical input paths produce identical engine results and different paths differ."""
    gen = HistoricalBlockBootstrap()
    config = FundConfig.from_yaml()

    paths_seed_10 = gen.generate(n_paths=20, n_hours=720, seed=10, s0=100.0)
    paths_seed_10_dup = gen.generate(n_paths=20, n_hours=720, seed=10, s0=100.0)
    paths_seed_20 = gen.generate(n_paths=20, n_hours=720, seed=20, s0=100.0)

    res_1 = run_simulation(paths_seed_10, config=config)
    res_2 = run_simulation(paths_seed_10_dup, config=config)
    res_3 = run_simulation(paths_seed_20, config=config)

    # Exact bit-for-bit equality on duplicate run
    assert np.array_equal(res_1.net_yield_btc, res_2.net_yield_btc)
    assert np.array_equal(res_1.liquidated, res_2.liquidated)
    assert np.array_equal(res_1.min_hf, res_2.min_hf)
    assert np.array_equal(res_1.unwind_count, res_2.unwind_count)
    assert np.array_equal(res_1.final_btc, res_2.final_btc)

    # Summary metrics exact match
    m1 = res_1.summary()
    m2 = res_2.summary()
    for k in m1:
        assert m1[k] == m2[k], f"Metric {k} mismatch between identical runs"

    # Different seeds must differ in output distribution
    assert not np.array_equal(res_1.net_yield_btc, res_3.net_yield_btc)
    assert not np.array_equal(res_1.min_hf, res_3.min_hf)
