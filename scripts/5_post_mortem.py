#!/usr/bin/env python3
"""Feedback Loop 3: Semi-Annual Post-Mortem & Rule Calibration Proposal.

Responsibilities:
1. Backtest memory/rejected_log.json entries across configured lookback windows (180d / 365d)
   against primary/secondary benchmarks (SPY / QQQ).
2. Review DORMANT industries in memory/industries/ for missed breakouts.
3. Compute per-rule false-negative rates (alpha > false_negative_excess_return_threshold).
4. Generate a Markdown calibration proposal in memory/reports/ suggesting config/rules.yaml
   adjustments. NEVER auto-modifies config/rules.yaml without human approval.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from coffee_can.config import ProjectPaths, load_data_sources, load_environment, load_rules
from coffee_can.storage import list_active_yaml_files, read_rejected_log, read_yaml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run semi-annual post-mortem on rejected stocks and DORMANT industries."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print summary to console without writing a report under memory/reports/.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_environment(ROOT_DIR)

    paths = ProjectPaths.from_root(ROOT_DIR)
    rules = load_rules(ROOT_DIR)
    _ = load_data_sources(ROOT_DIR)

    pm_cfg = rules["post_mortem"]
    rejected_log = read_rejected_log(paths.rejected_log_file)
    dormant_industries = [
        read_yaml(p).get("slug", p.stem)
        for p in list_active_yaml_files(paths.industries_dir)
        if read_yaml(p).get("status") == "DORMANT"
    ]

    print("=== [Loop 3] Semi-Annual Post-Mortem Skeleton ===")
    print(f"Rejected log entries   : {len(rejected_log.get('entries', []))}")
    print(f"Dormant industries     : {len(dormant_industries)}")
    primary_bm = pm_cfg["benchmarks"]["primary"]
    secondary_bm = pm_cfg["benchmarks"]["secondary"]
    print(f"Benchmarks             : {primary_bm} / {secondary_bm}")
    print(
        f"False-negative alpha   : > +{pm_cfg['false_negative_excess_return_threshold']:.0%} vs SPY"
    )
    print(f"Dry run                : {args.dry_run}")
    print(
        "TODO(Phase 5): Compute excess returns via yfinance, attribute by failed_rules, "
        "propose YAML diff."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
