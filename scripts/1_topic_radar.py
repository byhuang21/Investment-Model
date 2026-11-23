#!/usr/bin/env python3
"""Layer 1: Active Industry Discovery & State Machine Radar (DORMANT <-> ACTIVE).

Responsibilities:
1. Scan free primary signals (USASpending awards, SEC EDGAR S-1/8-K/10-Q/10-K EFTS,
   bellwether CapEx/keyword shifts, and optional local transcripts under
   memory/inputs/transcripts/).
2. Evaluate DORMANT industries in memory/industries/ against awakening thresholds in
   config/rules.yaml.
3. Transition eligible industries to ACTIVE (respecting state_change_cooldown_days) and refresh
   candidate_tickers for Layer 2 quant screening.
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
from coffee_can.storage import list_active_yaml_files, read_yaml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Layer 1: Scan external signals and update industry state machines."
    )
    parser.add_argument(
        "--industry",
        type=str,
        default=None,
        help="Optional industry slug to scan (default: scan all tracked industries + discovery).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview scan plan without mutating memory/industries/ files.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_environment(ROOT_DIR)

    paths = ProjectPaths.from_root(ROOT_DIR)
    rules = load_rules(ROOT_DIR)
    data_sources = load_data_sources(ROOT_DIR)

    industries = list_active_yaml_files(paths.industries_dir)
    awaken_cfg = rules["layer1_industry_radar"]["state_machine"]["awaken_thresholds"]

    print("=== [Layer 1] Topic Radar Skeleton ===")
    print(f"Tracked industry files : {len(industries)}")
    print(f"Awaken revenue threshold: ${awaken_cfg['min_leaders_aggregate_revenue_usd_m']}M")
    print(f"USASpending enabled    : {data_sources['usaspending']['enabled']}")
    print(f"SEC EDGAR enabled      : {data_sources['sec_edgar']['enabled']}")
    print(f"Dry run                : {args.dry_run}")

    for path in industries:
        record = read_yaml(path)
        if args.industry and record.get("slug") != args.industry:
            continue
        candidates_count = len(record.get("candidate_tickers", []))
        print(
            f"  - {record.get('slug', path.stem)} "
            f"[status={record.get('status')}, candidates={candidates_count}]"
        )

    print(
        "TODO(Phase 3): Connect UsaSpendingClient + SecEdgarClient EFTS + candidate ticker mapper."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
