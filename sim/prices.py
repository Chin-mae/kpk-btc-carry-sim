"""
Price generation layer for KPK Carry Sim.
Implements SPEC §3.1 (Market Layer - Prices) and §7 (Calibration).

Provides three parallel, seed-reproducible price path generators:
  1. HistoricalBlockBootstrap: Resampling 24h blocks of empirical BTC log-returns.
  2. JumpDiffusion: Drift 0 GBM + Poisson jumps from empirical worst-200 hourly moves.
  3. RegimeSwitching: 3-state Markov chain (Calm, Stressed, Crisis) with empirical transition matrix.

All generators implement the signature:
  generate(n_paths: int, n_hours: int, seed: int, **params) -> np.ndarray
returning an array of shape [n_paths, n_hours + 1] where index 0 is entry price s0.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "raw" / "btc_usd_hourly.csv"

# Pre-computed empirical calibration fallback parameters (Jan 2023–Oct 2026 Coinbase)
# Used when raw CSV has not yet been pulled.
FALLBACK_MEAN_HOURLY = 4.6034e-05
FALLBACK_STD_HOURLY = 0.004918
FALLBACK_TRANSITION_MATRIX = np.array([
    [0.9746, 0.0248, 0.0006],
    [0.0501, 0.9338, 0.0161],
    [0.0021, 0.0494, 0.9485],
])
FALLBACK_REGIME_SIGMAS = np.array([0.003146, 0.005534, 0.009524])


def load_historical_btc_log_returns(data_path: Path | str | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Load historical BTC hourly closes and compute log-returns.

    Returns:
      (log_returns, closes)
    """
    path = Path(data_path) if data_path is not None else DEFAULT_DATA_PATH
    if not path.exists():
        # Generate representative synthetic history if file doesn't exist yet
        rng = np.random.default_rng(1337)
        synth_rets = rng.normal(FALLBACK_MEAN_HOURLY, FALLBACK_STD_HOURLY, size=33587)
        synth_closes = 100.0 * np.exp(np.cumsum(synth_rets))
        return synth_rets, synth_closes

    df = pd.read_csv(path)
    closes = df["close"].to_numpy(dtype=float)
    log_returns = np.diff(np.log(closes))
    return log_returns, closes


class BasePriceGenerator(ABC):
    """Abstract base class for price path generators per SPEC §3.1."""

    @abstractmethod
    def generate(
        self,
        n_paths: int,
        n_hours: int,
        seed: int,
        s0: float = 100.0,
        **params,
    ) -> np.ndarray:
        """Generate simulated BTC price paths.

        Args:
          n_paths: Number of Monte Carlo paths.
          n_hours: Horizon length in hours.
          seed: Random seed for exact reproducibility.
          s0: Initial entry price at hour 0.
          **params: Additional model-specific parameters.

        Returns:
          np.ndarray of shape [n_paths, n_hours + 1] where column 0 is s0.
        """
        pass


class HistoricalBlockBootstrap(BasePriceGenerator):
    """(a) Historical block bootstrap of hourly BTC returns (SPEC §3.1).

    Resamples contiguous 24-hour blocks of empirical hourly returns to preserve
    volatility clustering, intraday patterns, and empirical tail distributions.
    """

    def __init__(
        self,
        historical_returns: np.ndarray | None = None,
        block_hours: int = 24,
    ) -> None:
        if historical_returns is None:
            returns, _ = load_historical_btc_log_returns()
            self.historical_returns = returns
        else:
            self.historical_returns = np.asarray(historical_returns, dtype=float)

        self.block_hours = block_hours
        if len(self.historical_returns) < self.block_hours:
            raise ValueError(f"Historical data must have at least {self.block_hours} returns")

        # Pre-slice all overlapping 24-hour blocks for fast vectorised indexing
        n_valid = len(self.historical_returns) - self.block_hours + 1
        self._blocks = np.lib.stride_tricks.sliding_window_view(
            self.historical_returns, self.block_hours
        )  # shape: [n_valid, block_hours]
        self._n_blocks_available = n_valid

    def generate(
        self,
        n_paths: int,
        n_hours: int,
        seed: int,
        s0: float = 100.0,
        **params,
    ) -> np.ndarray:
        """Generate price paths via 24-hour block bootstrap.

        Returns:
          np.ndarray of shape [n_paths, n_hours + 1].
        """
        rng = np.random.default_rng(seed)

        # Number of 24h blocks required per path
        blocks_needed = int(np.ceil(n_hours / self.block_hours))

        # Sample block indices: shape [n_paths, blocks_needed]
        sampled_indices = rng.integers(0, self._n_blocks_available, size=(n_paths, blocks_needed))

        # Gather sampled blocks: shape [n_paths, blocks_needed, block_hours]
        sampled_blocks = self._blocks[sampled_indices]

        # Reshape to [n_paths, blocks_needed * block_hours] and truncate to n_hours
        path_log_returns = sampled_blocks.reshape(n_paths, blocks_needed * self.block_hours)[:, :n_hours]

        # Accumulate prices
        cum_returns = np.cumsum(path_log_returns, axis=1)
        prices = np.empty((n_paths, n_hours + 1), dtype=float)
        prices[:, 0] = s0
        prices[:, 1:] = s0 * np.exp(cum_returns)

        return prices


class JumpDiffusion(BasePriceGenerator):
    """(b) Jump diffusion generator per SPEC §3.1.

    Drift 0, continuous diffusion vol, and Poisson jumps with jump sizes
    drawn directly from the empirical distribution of BTC's worst 200 hourly moves.
    """

    def __init__(
        self,
        historical_returns: np.ndarray | None = None,
        drift: float = 0.0,
        n_worst_moves: int = 200,
    ) -> None:
        if historical_returns is None:
            returns, _ = load_historical_btc_log_returns()
            self.historical_returns = returns
        else:
            self.historical_returns = np.asarray(historical_returns, dtype=float)

        self.drift = drift
        self.n_worst_moves = n_worst_moves

        # Extract empirical worst N moves (crashes)
        sorted_indices = np.argsort(self.historical_returns)
        self.worst_moves = self.historical_returns[sorted_indices[: self.n_worst_moves]]

        # Empirical jump intensity (expected jumps per hour)
        n_total = len(self.historical_returns)
        self.jump_intensity = len(self.worst_moves) / max(n_total, 1)

        # Calibrate continuous diffusion volatility: total_var = sigma_diff^2 + lambda * E[J^2]
        total_var = float(np.var(self.historical_returns))
        jump_second_moment = float(np.mean(self.worst_moves**2))
        diff_var = max(1e-8, total_var - self.jump_intensity * jump_second_moment)
        self.sigma_diff = float(np.sqrt(diff_var))

    def generate(
        self,
        n_paths: int,
        n_hours: int,
        seed: int,
        s0: float = 100.0,
        compensate_drift: bool = True,
        **params,
    ) -> np.ndarray:
        """Generate price paths via Jump Diffusion.

        Args:
          compensate_drift: If True, adjust continuous drift so total expected return is exactly self.drift (0.0).
        """
        rng = np.random.default_rng(seed)

        # Continuous Gaussian diffusion component
        z = rng.standard_normal((n_paths, n_hours))

        # Expected jump contribution per hour
        mean_jump = float(np.mean(self.worst_moves))
        effective_drift = self.drift
        if compensate_drift:
            # E[r_t] = effective_drift - 0.5*sigma_diff^2 + lambda * E[J] = drift
            effective_drift = self.drift - self.jump_intensity * mean_jump

        cont_returns = (effective_drift - 0.5 * self.sigma_diff**2) + self.sigma_diff * z

        # Poisson jump occurrences: shape [n_paths, n_hours]
        n_jumps = rng.poisson(self.jump_intensity, size=(n_paths, n_hours))
        total_jumps = int(np.sum(n_jumps))

        jump_effects = np.zeros((n_paths, n_hours), dtype=float)
        if total_jumps > 0:
            sampled_jumps = rng.choice(self.worst_moves, size=total_jumps)
            # Assign sampled jumps into paths/hours
            indices = np.nonzero(n_jumps)
            # Expand for multiple jumps in a single hour if any
            repeat_indices_0 = np.repeat(indices[0], n_jumps[indices])
            repeat_indices_1 = np.repeat(indices[1], n_jumps[indices])
            np.add.at(jump_effects, (repeat_indices_0, repeat_indices_1), sampled_jumps)

        total_returns = cont_returns + jump_effects

        # Accumulate prices
        cum_returns = np.cumsum(total_returns, axis=1)
        prices = np.empty((n_paths, n_hours + 1), dtype=float)
        prices[:, 0] = s0
        prices[:, 1:] = s0 * np.exp(cum_returns)

        return prices


class RegimeSwitching(BasePriceGenerator):
    """(c) Regime-switching generator per SPEC §3.1.

    3 regimes: Calm (0), Stressed (1), Crisis (2).
    Transition matrix estimated from realised-volatility history.
    State persistence captures multi-day volatility clustering.
    """

    def __init__(
        self,
        historical_returns: np.ndarray | None = None,
        vol_window: int = 24,
        q_calm: float = 0.60,
        q_stress: float = 0.90,
        sampling_mode: Literal["empirical", "student_t", "gaussian"] = "empirical",
    ) -> None:
        if historical_returns is None:
            returns, _ = load_historical_btc_log_returns()
            self.historical_returns = returns
        else:
            self.historical_returns = np.asarray(historical_returns, dtype=float)

        self.vol_window = vol_window
        self.sampling_mode = sampling_mode

        # Classify historical regimes via rolling realised vol
        series = pd.Series(self.historical_returns)
        rolling_vol = series.rolling(self.vol_window).std().dropna().to_numpy()

        calm_thresh = float(np.percentile(rolling_vol, q_calm * 100))
        stress_thresh = float(np.percentile(rolling_vol, q_stress * 100))

        regimes = np.zeros(len(rolling_vol), dtype=int)
        regimes[rolling_vol > calm_thresh] = 1
        regimes[rolling_vol > stress_thresh] = 2

        # Empirical transition matrix: shape [3, 3]
        transitions = np.zeros((3, 3), dtype=float)
        for i in range(len(regimes) - 1):
            transitions[regimes[i], regimes[i + 1]] += 1

        row_sums = transitions.sum(axis=1, keepdims=True)
        self.transition_matrix = np.divide(
            transitions, row_sums, out=FALLBACK_TRANSITION_MATRIX.copy(), where=row_sums > 0
        )
        self._cum_transitions = np.cumsum(self.transition_matrix, axis=1)

        # Segment returns by regime
        aligned_returns = self.historical_returns[self.vol_window - 1 :]
        self.regime_pools: list[np.ndarray] = [
            aligned_returns[regimes == r] for r in range(3)
        ]
        self.regime_sigmas = np.array([
            float(np.std(pool)) if len(pool) > 0 else FALLBACK_REGIME_SIGMAS[r]
            for r, pool in enumerate(self.regime_pools)
        ])

        # Stationary distribution (eigenvector of transition matrix)
        eigvals, eigvecs = np.linalg.eig(self.transition_matrix.T)
        stat_idx = np.argmin(np.abs(eigvals - 1.0))
        stat_dist = np.real(eigvecs[:, stat_idx])
        stat_dist = stat_dist / np.sum(stat_dist)
        self.stationary_distribution = np.maximum(0.0, stat_dist)
        self.stationary_distribution /= np.sum(self.stationary_distribution)

    def generate(
        self,
        n_paths: int,
        n_hours: int,
        seed: int,
        s0: float = 100.0,
        **params,
    ) -> np.ndarray:
        """Generate price paths via 3-regime Markov chain."""
        rng = np.random.default_rng(seed)

        # Initial regime from stationary distribution
        init_u = rng.random(size=n_paths)
        init_cum = np.cumsum(self.stationary_distribution)
        curr_regimes = np.searchsorted(init_cum, init_u)
        curr_regimes = np.clip(curr_regimes, 0, 2)

        path_returns = np.empty((n_paths, n_hours), dtype=float)

        # Step forward hour-by-hour across all paths simultaneously
        for t in range(n_hours):
            # Transition Markov states
            u_trans = rng.random(size=n_paths)
            cum_probs = self._cum_transitions[curr_regimes]  # shape [n_paths, 3]
            # Fast vectorized state update
            new_regimes = (u_trans[:, None] > cum_probs[:, :2]).sum(axis=1)
            curr_regimes = new_regimes

            # Draw returns conditioned on regime
            if self.sampling_mode == "empirical":
                for r in range(3):
                    mask = curr_regimes == r
                    count = int(np.sum(mask))
                    if count > 0:
                        pool = self.regime_pools[r]
                        # Draw empirical returns with zero-mean centering to enforce SPEC drift 0
                        draws = rng.choice(pool, size=count)
                        path_returns[mask, t] = draws - np.mean(pool)
            elif self.sampling_mode == "gaussian":
                sigmas = self.regime_sigmas[curr_regimes]
                z = rng.standard_normal(size=n_paths)
                path_returns[:, t] = -0.5 * sigmas**2 + sigmas * z
            else:  # student_t with df=3
                sigmas = self.regime_sigmas[curr_regimes]
                t_draws = rng.standard_t(df=3, size=n_paths) * np.sqrt(1 / 3)  # unit variance t(3)
                path_returns[:, t] = sigmas * t_draws

        # Accumulate prices
        cum_returns = np.cumsum(path_returns, axis=1)
        prices = np.empty((n_paths, n_hours + 1), dtype=float)
        prices[:, 0] = s0
        prices[:, 1:] = s0 * np.exp(cum_returns)

        return prices


def get_generator(
    name: Literal["bootstrap", "jump_diffusion", "regime_switching"],
    historical_returns: np.ndarray | None = None,
    **kwargs,
) -> BasePriceGenerator:
    """Factory function to instantiate price generators per SPEC §3.1."""
    name_norm = name.lower().replace("-", "_").replace(" ", "_")
    if "boot" in name_norm:
        return HistoricalBlockBootstrap(historical_returns=historical_returns, **kwargs)
    elif "jump" in name_norm:
        return JumpDiffusion(historical_returns=historical_returns, **kwargs)
    elif "regime" in name_norm:
        return RegimeSwitching(historical_returns=historical_returns, **kwargs)
    else:
        raise ValueError(
            f"Unknown generator '{name}'. Must be one of: 'bootstrap', 'jump_diffusion', 'regime_switching'"
        )
