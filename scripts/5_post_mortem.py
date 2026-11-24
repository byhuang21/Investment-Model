#!/usr/bin/env python3
"""Feedback Loop 3: Semi-Annual Post-Mortem & Rule Calibration Proposal.

Responsibilities:
1. Backtest memory/rejected_log.json entries across configured lookback windows (180d / 365d)
   against primary/secondary benchmarks (SPY / QQQ).
2. Review DORMANT industries in memory/industries/ for missed 180d stock breakouts.
3. Compute per-rule false-negative rates (alpha >= false_negative_excess_return_threshold).
4. Write Markdown calibration proposal + latest_post_mortem.json snapshot in memory/reports/.
   NEVER auto-modifies config/rules.yaml without human approval.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from coffee_can.clients.market_data import MarketDataClient
from coffee_can.config import ProjectPaths, load_data_sources, load_environment, load_rules
from coffee_can.notifier import AlertMessage, Notifier
from coffee_can.post_mortem import (
    DormantIndustryCheck,
    render_post_mortem_markdown,
    run_post_mortem_analysis,
)
from coffee_can.storage import list_active_yaml_files, read_rejected_log, read_yaml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run semi-annual post-mortem on rejected stocks and DORMANT industries."
    )
    parser.add_argument(
        "--include-immature",
        action="store_true",
        help="Include entries younger than 180 days for mark-to-market preview.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass local .cache/yfinance/ when fetching benchmark & stock prices.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print summary to console without writing reports under memory/reports/.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_environment(ROOT_DIR)

    paths = ProjectPaths.from_root(ROOT_DIR)
    rules = load_rules(ROOT_DIR)
    data_sources = load_data_sources(ROOT_DIR)

    pm_cfg = rules["post_mortem"]
    primary_bm = str(pm_cfg["benchmarks"]["primary"])
    secondary_bm = str(pm_cfg["benchmarks"]["secondary"])
    rejected_log = read_rejected_log(paths.rejected_log_file)
    entries = list(rejected_log.get("entries") or [])

    dormant_records = [
        read_yaml(p)
        for p in list_active_yaml_files(paths.industries_dir)
        if str(read_yaml(p).get("status", "")).upper() == "DORMANT"
    ]

    print("=== [Loop 3] Semi-Annual Post-Mortem Calibration ===")
    print(f"Rejected log entries   : {len(entries)}")
    print(f"Dormant industries     : {len(dormant_records)}")
    print(f"Benchmarks             : {primary_bm} / {secondary_bm}")
    print(
        f"False-negative alpha   : >= +{pm_cfg['false_negative_excess_return_threshold']:.0%} "
        f"vs {primary_bm}"
    )
    print(f"Dry run                : {args.dry_run}")

    if not entries and not dormant_records:
        print("No rejected entries or dormant industries to review yet.")
        return 0

    market_client = MarketDataClient(
        config=data_sources["yfinance"],
        project_root=ROOT_DIR,
        use_cache=not args.no_cache,
    )
    today_dt = datetime.now(UTC).date()
    today_str = today_dt.isoformat()

    cur_spy = market_client.get_latest_price(primary_bm)
    cur_qqq = market_client.get_latest_price(secondary_bm)

    unique_tickers = sorted({str(e.get("ticker", "")).upper() for e in entries if e.get("ticker")})
    current_prices: dict[str, float | None] = {}
    for sym in unique_tickers:
        try:
            current_prices[sym] = market_client.get_latest_price(sym)
        except Exception:
            current_prices[sym] = None

    # Check DORMANT industries for 180-day stock breakout vs SPY
    lookback_180_str = (today_dt - timedelta(days=180)).isoformat()
    spy_180_ago = market_client.get_price_on_or_after(primary_bm, lookback_180_str)
    spy_180_ret = (
        ((cur_spy / spy_180_ago) - 1.0) if (cur_spy and spy_180_ago and spy_180_ago > 0) else None
    )

    dormant_checks: list[DormantIndustryCheck] = []
    for rec in dormant_records:
        slug = str(rec.get("slug", "unknown"))
        name = str(rec.get("name", slug))
        leaders = list(rec.get("leader_tickers") or rec.get("candidate_tickers") or [])[:5]
        excesses: list[float] = []
        for sym in leaders:
            try:
                p_now = market_client.get_latest_price(sym)
                p_old = market_client.get_price_on_or_after(sym, lookback_180_str)
                if p_now and p_old and p_old > 0 and spy_180_ret is not None:
                    excesses.append(((p_now / p_old) - 1.0) - spy_180_ret)
            except Exception:
                continue
        avg_ex = round(sum(excesses) / len(excesses), 4) if excesses else None
        breakout = bool(avg_ex is not None and avg_ex >= 0.30)
        dormant_checks.append(
            DormantIndustryCheck(
                slug=slug,
                name=name,
                candidates_checked=len(excesses),
                avg_180d_excess_vs_spy=avg_ex,
                breakout_alert=breakout,
                note=(
                    "休眠赛道股价已大幅跑赢大盘，建议人工检查是否存在营收滞后拐点。"
                    if breakout
                    else ""
                ),
            )
        )

    report = run_post_mortem_analysis(
        rejected_entries=entries,
        current_prices=current_prices,
        current_spy=cur_spy,
        current_qqq=cur_qqq,
        rules=rules,
        today_str=today_str,
        dormant_checks=dormant_checks,
        include_immature=args.include_immature,
    )
    markdown_body = render_post_mortem_markdown(report)

    severity = "YELLOW" if report.calibration_proposals else "INFO"
    notifier = Notifier(data_sources, paths.reports_dir)
    notifier.dispatch(
        AlertMessage(
            title=f"Semi-Annual Post-Mortem ({today_str})",
            severity=severity,
            body_markdown=markdown_body,
            category="post_mortem",
        ),
        dry_run=args.dry_run,
    )

    if not args.dry_run:
        paths.reports_dir.mkdir(parents=True, exist_ok=True)
        latest_json = paths.reports_dir / "latest_post_mortem.json"
        tmp = latest_json.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(report.to_dict(), fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        tmp.replace(latest_json)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
