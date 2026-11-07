"""
Verification test asserting that all downstream records in report/runs.jsonl
(scenario evaluations, sensitivity runs, and allocation extension runs)
match the configuration of the mechanically selected recommended cell.
"""

import json
from pathlib import Path
import pandas as pd
import pytest

from sim.sweep import build_parameter_grid, select_recommended_cell


def test_downstream_records_match_selected_cell():
    runs_path = Path("report/runs.jsonl")
    assert runs_path.exists(), "report/runs.jsonl does not exist"

    with open(runs_path, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

    # Find grid records and select recommended cell
    grid_records = [r for r in records if r.get("run_id", "").startswith("cell_")]
    assert len(grid_records) == 1080, f"Expected 1080 grid records, got {len(grid_records)}"

    df_grid = pd.DataFrame(grid_records)
    df_boot = df_grid[df_grid["generator"] == "HistoricalBlockBootstrap"]
    selected_id, _ = select_recommended_cell(df_boot)

    grid = build_parameter_grid()
    grid_by_id = {c.cell_id: c for c in grid}
    selected_cell = grid_by_id[selected_id]

    expected_config = {
        "cell_id": selected_cell.cell_id,
        "target_ltv": selected_cell.target_ltv,
        "hf_unwind_floor": selected_cell.hf_unwind_floor,
        "sleeve_split": selected_cell.sleeve_split_str,
        "deployment_cap": selected_cell.deployment_cap,
        "policy": selected_cell.policy,
    }

    # 1. Check all recommended scenario records (both headline and sensitivities)
    rec_scenarios = [r for r in records if r.get("setting") == "recommended"]
    assert len(rec_scenarios) > 0, "No recommended scenario records found"

    for r in rec_scenarios:
        for field, expected_val in expected_config.items():
            actual_val = r.get(field)
            assert actual_val == expected_val, (
                f"Mismatch in recommended scenario record {r.get('run_id')}: "
                f"field '{field}' expected {expected_val!r}, got {actual_val!r}"
            )

    # 2. Check all allocation extension records
    alloc_records = [r for r in records if r.get("allocation_run") is True]
    assert len(alloc_records) == 3, f"Expected 3 allocation records, got {len(alloc_records)}"

    for r in alloc_records:
        for field, expected_val in expected_config.items():
            actual_val = r.get(field)
            assert actual_val == expected_val, (
                f"Mismatch in allocation record {r.get('run_id')}: "
                f"field '{field}' expected {expected_val!r}, got {actual_val!r}"
            )

    # 3. Check portfolio correlations record if present
    port_records = [r for r in records if r.get("portfolio_run") is True]
    for r in port_records:
        for field, expected_val in expected_config.items():
            actual_val = r.get(field)
            assert actual_val == expected_val, (
                f"Mismatch in portfolio record {r.get('run_id')}: "
                f"field '{field}' expected {expected_val!r}, got {actual_val!r}"
            )
