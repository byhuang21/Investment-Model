#!/usr/bin/env python3
"""Feedback Loop 1: Near-Miss Watchlist Valuation Monitor.

Responsibilities:
1. Read all active watchlist YAML files in memory/near_miss_pool/.
2. Fetch latest market cap & price via yfinance.
3. Apply hysteresis (consecutive_trading_days_below_target) and cooldown (alert_cooldown_days)
   defined in config/rules.yaml before emitting alerts via Notifier.
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
        description="Monitor memory/near_miss_pool/ for target valuation entry triggers."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check prices without updating YAML monitor_state or sending external alerts.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_environment(ROOT_DIR)

    paths = ProjectPaths.from_root(ROOT_DIR)
    rules = load_rules(ROOT_DIR)
    _ = load_data_sources(ROOT_DIR)

    watch_cfg = rules["watchlist_monitor"]
    watchlist_files = list_active_yaml_files(paths.near_miss_dir)

    print("=== [Loop 1] Watchlist Monitor Skeleton ===")
    print(f"Watched NEAR_MISS items: {len(watchlist_files)}")
    print(f"Hysteresis days        : {watch_cfg['consecutive_trading_days_below_target']}")
    print(f"Alert cooldown days    : {watch_cfg['alert_cooldown_days']}")
    print(f"Dry run                : {args.dry_run}")

    for path in watchlist_files:
        item = read_yaml(path)
        target_mcap = item.get("targets", {}).get("target_market_cap_usd_m")
        print(f"  - {item.get('ticker', path.stem)}: target <= ${target_mcap}M")

    print("TODO(Phase 4): Connect MarketDataClient + hysteresis/cooldown state updates + Notifier.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
