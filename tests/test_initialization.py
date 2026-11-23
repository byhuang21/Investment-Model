"""Smoke tests verifying project initialization, configs, schemas, and script skeletons."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from coffee_can.config import ProjectPaths, load_data_sources, load_rules
from coffee_can.schemas import FalsificationLine, IndustryStatus, ScreenOutcome, TrafficLight
from coffee_can.storage import list_active_yaml_files, read_rejected_log, read_yaml


def test_project_structure_and_configs_load() -> None:
    paths = ProjectPaths.from_root()
    assert (paths.root / "AGENT_RULES.md").exists()
    assert paths.rules_file.exists()
    assert paths.data_sources_file.exists()
    assert paths.rejected_log_file.exists()

    rules = load_rules()
    assert rules["universe"]["primary_market"] == "US"
    assert rules["layer2_quant_screener"]["market_cap_usd_m"]["min_pass"] == 300
    assert rules["layer2_quant_screener"]["market_cap_usd_m"]["max_pass"] == 15000
    assert rules["layer3_deep_dive"]["min_target_upside_multiple"] == 10.0

    sources = load_data_sources()
    assert sources["sec_edgar"]["enabled"] is True
    assert sources["usaspending"]["enabled"] is True
    assert sources["yfinance"]["enabled"] is True
    assert sources["sec_edgar"]["max_requests_per_second"] <= 10


def test_memory_templates_are_valid_and_ignored_by_active_lister() -> None:
    paths = ProjectPaths.from_root()
    # Templates start with `_` and must never be returned as active items
    for directory in (paths.industries_dir, paths.near_miss_dir, paths.coffee_can_dir):
        active_files = list_active_yaml_files(directory)
        assert all(not p.name.startswith("_") for p in active_files)

    # Templates themselves must parse as valid YAML mappings
    industry_tpl = read_yaml(paths.industries_dir / "_template.industry.yaml")
    assert industry_tpl["status"] == IndustryStatus.DORMANT

    scorecard_tpl = read_yaml(paths.research_dir / "_template_dossier" / "scorecard.yaml")
    assert scorecard_tpl["dimensions"]["5_management"]["human_subjective_score"] is None
    assert scorecard_tpl["dimensions"]["5_management"]["reviewed_by_human"] is False

    position_tpl = read_yaml(paths.coffee_can_dir / "_template.position.yaml")
    assert len(position_tpl["falsification_lines"]) >= 1

    rejected_log = read_rejected_log(paths.rejected_log_file)
    assert isinstance(rejected_log["entries"], list)


def test_falsification_line_evaluation() -> None:
    line = FalsificationLine(
        id="FL-1",
        description="Revenue growth below 15%",
        metric="revenue_yoy_growth",
        operator="<",
        threshold=0.15,
        consecutive_quarters=2,
        severity=TrafficLight.RED,
    )
    assert line.evaluate(0.12) is True
    assert line.evaluate(0.18) is False
    assert line.evaluate(None) is False
    assert ScreenOutcome.PASS.value == "PASS"


def test_all_pipeline_script_skeletons_run_in_dry_run() -> None:
    root = Path(__file__).resolve().parents[1]
    scripts = [
        "scripts/1_topic_radar.py",
        "scripts/2_quant_screener.py",
        "scripts/3_watchlist_monitor.py",
        "scripts/4_earnings_diff.py",
        "scripts/5_post_mortem.py",
    ]
    for script in scripts:
        proc = subprocess.run(
            [sys.executable, str(root / script), "--dry-run"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"{script} failed: {proc.stderr}"
