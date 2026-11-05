"""
Smoke test for sim/sweep.py per BRIEF Deliverable.
Validates that a 10-path, 2-cell smoke run of the sweep completes
and writes runs.jsonl entries with matching config hashes.
"""

import json
from pathlib import Path

import pytest

from sim.sweep import (
    CellConfig,
    compute_config_hash,
    get_git_commit,
    run_full_sweep,
)


def test_sweep_smoke_run(tmp_path: Path):
    """A 10-path, 2-cell smoke run of the sweep completes and writes runs.jsonl entries

    with matching config hashes and git commit information.
    """
    test_cells = [
        CellConfig(
            cell_id=1,
            target_ltv=0.702,
            hf_unwind_floor=1.13,
            sleeve_split_str="100:0",
            deployment_cap="none",
            policy="kpk_published",
        ),
        CellConfig(
            cell_id=2,
            target_ltv=0.600,
            hf_unwind_floor=1.10,
            sleeve_split_str="50:50",
            deployment_cap="25%",
            policy="kpk_vol_scaled",
        ),
    ]

    # Run smoke sweep in temporary directory
    df, records = run_full_sweep(
        cells=test_cells,
        generators=["HistoricalBlockBootstrap"],
        n_paths_primary=10,
        n_paths_robustness=10,
        n_hours=100,  # Fast horizon for smoke test
        base_seed=123,
        output_dir=tmp_path,
        log_file_mode="w",
    )

    # 1. Assert exactly 2 cells were evaluated
    assert len(df) == 2
    assert len(records) == 2

    runs_file = tmp_path / "runs.jsonl"
    assert runs_file.exists(), "runs.jsonl was not created"

    # Read lines back and verify JSON structure and hashes
    with open(runs_file, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]

    assert len(lines) == 2

    git_commit = get_git_commit()

    for i, line in enumerate(lines):
        cell = test_cells[i]

        # Verify key metadata fields
        assert line["cell_id"] == cell.cell_id
        assert line["target_ltv"] == cell.target_ltv
        assert line["hf_unwind_floor"] == cell.hf_unwind_floor
        assert line["sleeve_split"] == cell.sleeve_split_str
        assert line["deployment_cap"] == cell.deployment_cap
        assert line["policy"] == cell.policy
        assert line["n_paths"] == 10
        assert line["seed"] == 123
        assert line["git_commit"] == git_commit
        assert line["wall_time_sec"] >= 0.0

        # Verify config hash matches canonical computation
        expected_hash = compute_config_hash(cell.to_dict())
        assert line["config_hash"] == expected_hash, (
            f"Config hash mismatch: {line['config_hash']} != {expected_hash}"
        )

        # Verify required performance metrics
        assert "carry_median_net_apy" in line
        assert "carry_es95_loss_pct" in line
        assert "portfolio_median_net_apy" in line
        assert "portfolio_es95_loss_pct" in line
        assert "issuer_hhi" in line

    # Template and Recommended flags
    assert lines[0]["is_template"] is True
    assert lines[0]["is_recommended"] is False
    assert lines[1]["is_template"] is False
    assert lines[1]["is_recommended"] is True
