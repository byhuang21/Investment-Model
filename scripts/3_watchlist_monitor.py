#!/usr/bin/env python3
"""Feedback Loop 1: Near-Miss Watchlist Valuation Monitor.

Responsibilities:
1. Read all active watchlist YAML files in memory/near_miss_pool/.
2. Fetch latest market cap & price via yfinance.
3. Apply hysteresis (consecutive_trading_days_below_target), cooldown (alert_cooldown_days),
   and extra-drop override defined in config/rules.yaml.
4. Persist updated monitor_state back to YAML and dispatch alerts via Notifier.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from coffee_can.clients.market_data import MarketDataClient
from coffee_can.config import ProjectPaths, load_data_sources, load_environment, load_rules
from coffee_can.monitor import WatchlistEvaluation, evaluate_watchlist_item
from coffee_can.notifier import AlertMessage, Notifier
from coffee_can.storage import list_active_yaml_files, read_yaml, write_yaml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitor memory/near_miss_pool/ for target valuation entry triggers."
    )
    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help="Optional single ticker in memory/near_miss_pool/ to check.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass local .cache/yfinance/ and fetch fresh live quotes.",
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
    data_sources = load_data_sources(ROOT_DIR)

    watch_cfg = rules["watchlist_monitor"]
    watchlist_files = list_active_yaml_files(paths.near_miss_dir)
    if args.ticker:
        target_sym = args.ticker.strip().upper()
        watchlist_files = [
            p
            for p in watchlist_files
            if str(read_yaml(p).get("ticker", p.stem)).upper() == target_sym
        ]

    print("=== [Loop 1] Near-Miss Watchlist Monitor ===")
    print(f"Watched NEAR_MISS items: {len(watchlist_files)}")
    print(f"Hysteresis days        : {watch_cfg['consecutive_trading_days_below_target']}")
    print(f"Alert cooldown days    : {watch_cfg['alert_cooldown_days']}")
    print(f"Dry run                : {args.dry_run}")

    if not watchlist_files:
        print("No active watchlist items to monitor.")
        return 0

    market_client = MarketDataClient(
        config=data_sources["yfinance"],
        project_root=ROOT_DIR,
        use_cache=not args.no_cache,
    )
    today_str = datetime.now(UTC).date().isoformat()

    evaluations: list[WatchlistEvaluation] = []
    alerts_md: list[str] = []
    errors: list[tuple[str, str]] = []

    header = (
        f"{'TICKER':<8} {'CUR_MCAP':>10} {'TGT_MCAP':>10} {'GAP':>8} "
        f"{'STREAK':>7} {'STATUS':<12} SUMMARY"
    )
    print("\n" + header)
    print("-" * len(header))

    for path in watchlist_files:
        record = read_yaml(path)
        ticker = str(record.get("ticker", path.stem)).upper()
        try:
            quote = market_client.get_quote_snapshot(ticker)
            cur_mcap = quote.get("market_cap_usd_m")
            if cur_mcap is None:
                raise RuntimeError(f"Market cap unavailable from yfinance for {ticker}")
            ev = evaluate_watchlist_item(
                record=record,
                current_market_cap_usd_m=float(cur_mcap),
                current_price_usd=quote.get("price_usd"),
                rules=rules,
                today_str=today_str,
            )
            evaluations.append(ev)

            streak_str = f"{ev.consecutive_days_below}/{ev.required_days}"
            print(
                f"{ev.ticker:<8} ${ev.current_market_cap_usd_m:>8,.0f}M "
                f"${ev.target_market_cap_usd_m:>8,.0f}M {ev.gap_pct:>+7.1%} "
                f"{streak_str:>7} {ev.new_status:<12} {ev.summary}"
            )

            if ev.should_alert:
                alerts_md.append(
                    f"- **{ev.ticker}** (Price `${ev.current_price_usd}` | "
                    f"MCap `${ev.current_market_cap_usd_m:,.0f}M` <= "
                    f"Target `${ev.target_market_cap_usd_m:,.0f}M`): {ev.summary}"
                )

            if not args.dry_run:
                record["monitor_state"] = ev.updated_monitor_state
                write_yaml(path, record)
        except Exception as exc:
            errors.append((ticker, str(exc)))

    if alerts_md:
        notifier = Notifier(data_sources, paths.reports_dir)
        notifier.dispatch(
            AlertMessage(
                title=f"Near-Miss Watchlist Triggered ({len(alerts_md)} ticker(s))",
                severity="TRIGGER",
                body_markdown="\n".join(alerts_md),
                category="watchlist",
            ),
            dry_run=args.dry_run,
        )

    if errors:
        print("\nErrors:")
        for ticker, msg in errors:
            print(f"  - {ticker}: {msg}")

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
