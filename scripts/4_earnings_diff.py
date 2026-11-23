#!/usr/bin/env python3
"""Feedback Loop 2: Quarterly 10-Q / 10-K Earnings Diff & Falsification Health Check.

Responsibilities:
1. Scan all IN_CAN positions in memory/coffee_can/.
2. Check SEC EDGAR submissions for new 10-Q or 10-K filings since last_checked_accession.
3. Extract latest XBRL metrics and evaluate each position's machine-readable falsification_lines
   plus financial deterioration warnings from config/rules.yaml.
4. Output a GREEN / YELLOW / RED traffic-light report and dispatch alerts on YELLOW/RED.
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
        description="Run quarterly 10-Q/10-K diff against memory/coffee_can/ falsification lines."
    )
    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help="Optional single ticker in memory/coffee_can/ to inspect.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate without updating earnings_check_history or emitting external alerts.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_environment(ROOT_DIR)

    paths = ProjectPaths.from_root(ROOT_DIR)
    rules = load_rules(ROOT_DIR)
    _ = load_data_sources(ROOT_DIR)

    positions = list_active_yaml_files(paths.coffee_can_dir)
    yellow_cfg = rules["earnings_diff"]["traffic_light"]["yellow_triggers"]

    print("=== [Loop 2] Quarterly Earnings Diff Skeleton ===")
    print(f"Positions in Coffee Can: {len(positions)}")
    print(f"Yellow GM drop trigger : >= {yellow_cfg['gross_margin_drop_yoy_bps']} bps YoY")
    print(f"Dry run                : {args.dry_run}")

    for path in positions:
        pos = read_yaml(path)
        ticker = pos.get("ticker", path.stem)
        if args.ticker and ticker.upper() != args.ticker.upper():
            continue
        lines_count = len(pos.get("falsification_lines", []))
        print(f"  - {ticker} [status={pos.get('status')}, falsification_lines={lines_count}]")

    print(
        "TODO(Phase 4): Poll SEC submissions/XBRL, evaluate FalsificationLine rules, "
        "emit R/Y/G report."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
