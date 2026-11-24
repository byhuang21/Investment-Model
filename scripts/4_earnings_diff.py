#!/usr/bin/env python3
"""Feedback Loop 2: Quarterly 10-Q / 10-K Earnings Diff & Falsification Health Check.

Responsibilities:
1. Scan all IN_CAN positions in memory/coffee_can/.
2. Check SEC EDGAR submissions for new 10-Q or 10-K filings since last_checked_accession.
3. Extract latest XBRL metrics and evaluate each position's machine-readable falsification_lines
   plus financial deterioration warnings from config/rules.yaml.
4. Output a GREEN / YELLOW / RED traffic-light report, update position history, and dispatch
   alerts via Notifier on YELLOW/RED (or any new filing).
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

from coffee_can.clients.sec_edgar import (
    SecEdgarClient,
    extract_fundamentals_from_company_facts,
)
from coffee_can.config import (
    ProjectPaths,
    get_sec_user_agent,
    load_data_sources,
    load_environment,
    load_rules,
    resolve_workspace_root,
)
from coffee_can.earnings_diff import EarningsHealthCheck, evaluate_earnings_diff
from coffee_can.notifier import AlertMessage, Notifier
from coffee_can.schemas import TrafficLight
from coffee_can.storage import list_active_yaml_files, read_yaml, write_yaml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run quarterly 10-Q/10-K diff against memory/coffee_can/ falsification lines."
    )
    parser.add_argument(
        "--env",
        choices=["prod", "sandbox"],
        default=None,
        help="Target workspace environment (defaults to COFFEE_CAN_ENV or 'prod').",
    )
    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help="Optional single ticker in memory/coffee_can/ to inspect.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Evaluate even if the latest SEC filing accession was already checked.",
    )
    parser.add_argument(
        "--sec-user-agent",
        type=str,
        default=None,
        help="Optional SEC EDGAR User-Agent override (defaults to SEC_USER_AGENT env var).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass local .cache/sec_edgar/ files and fetch fresh SEC filings.",
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

    ws_root, env_name = resolve_workspace_root(args.env, ROOT_DIR)
    paths = ProjectPaths.from_root(ws_root)
    rules = load_rules(ws_root)
    data_sources = load_data_sources(ws_root)

    positions = list_active_yaml_files(paths.coffee_can_dir)
    if args.ticker:
        sym = args.ticker.strip().upper()
        positions = [
            p for p in positions if str(read_yaml(p).get("ticker", p.stem)).upper() == sym
        ]

    yellow_cfg = rules["earnings_diff"]["traffic_light"]["yellow_triggers"]
    print(f"=== [Loop 2] Quarterly Earnings Diff & Falsification Check [{env_name.upper()}] ===")
    print(f"Positions in Coffee Can: {len(positions)}")
    print(f"Yellow GM drop trigger : >= {yellow_cfg['gross_margin_drop_yoy_bps']} bps YoY")
    print(f"Dry run                : {args.dry_run}")

    if not positions:
        print("No IN_CAN positions in memory/coffee_can/ yet (awaiting Layer 3 human approval).")
        return 0

    try:
        user_agent = args.sec_user_agent or get_sec_user_agent(data_sources)
    except OSError:
        if args.dry_run:
            print("SEC_USER_AGENT not set; skipping live SEC calls in --dry-run preview.")
            return 0
        raise

    sec_cfg = data_sources["sec_edgar"]
    sec_client = SecEdgarClient(
        user_agent=user_agent,
        config=sec_cfg,
        project_root=ROOT_DIR,
        use_cache=not args.no_cache,
    )
    xbrl_tags = sec_cfg["xbrl_tag_fallbacks"]
    today_str = datetime.now(UTC).date().isoformat()

    results: list[EarningsHealthCheck] = []
    report_lines: list[str] = []
    worst_light = TrafficLight.GREEN
    errors: list[tuple[str, str]] = []

    header = f"{'TICKER':<8} {'FILING':<12} {'PERIOD':<12} {'NEW?':<5} {'LIGHT':<8} SUMMARY"
    print("\n" + header)
    print("-" * len(header))

    for path in positions:
        pos = read_yaml(path)
        ticker = str(pos.get("ticker", path.stem)).upper()
        if str(pos.get("status", "IN_CAN")).upper() == "EXITED":
            continue
        try:
            cik = str(pos.get("cik") or sec_client.lookup_cik(ticker))
            filing_meta = sec_client.get_latest_periodic_filing(cik)
            facts = sec_client.get_company_facts(cik)
            fundamentals = extract_fundamentals_from_company_facts(facts, xbrl_tags)

            check = evaluate_earnings_diff(
                position=pos,
                fundamentals=fundamentals,
                rules=rules,
                filing_meta=filing_meta,
                force=args.force,
            )
            results.append(check)

            print(
                f"{check.ticker:<8} {(check.filing_form or 'N/A'):<12} "
                f"{(check.period_end or 'N/A'):<12} {('YES' if check.is_new_filing else 'no'):<5} "
                f"{check.traffic_light.value:<8} {check.summary}"
            )

            if check.is_new_filing or args.force:
                report_lines.append(
                    f"- **{check.ticker}** [{check.traffic_light.value}] "
                    f"({check.filing_form or 'Filing'} {check.period_end or ''} / "
                    f"`{check.filing_accession}`): {check.summary}"
                )
                if check.traffic_light == TrafficLight.RED:
                    worst_light = TrafficLight.RED
                elif (
                    check.traffic_light == TrafficLight.YELLOW
                    and worst_light != TrafficLight.RED
                ):
                    worst_light = TrafficLight.YELLOW

            if not args.dry_run and (check.is_new_filing or args.force):
                pos["falsification_lines"] = check.updated_falsification_lines
                hist = dict(pos.get("earnings_check_history") or {})
                hist["last_checked_accession"] = check.filing_accession
                hist["last_checked_period_end"] = check.period_end
                hist["last_traffic_light"] = check.traffic_light.value
                checks_list = list(hist.get("checks") or [])
                checks_list.append(
                    {
                        "checked_at": today_str,
                        "filing_form": check.filing_form,
                        "filing_accession": check.filing_accession,
                        "period_end": check.period_end,
                        "traffic_light": check.traffic_light.value,
                        "summary": check.summary,
                        "metrics_snapshot": check.metrics_snapshot,
                        "line_results": [lr.to_dict() for lr in check.line_results],
                        "yellow_warnings": check.yellow_warnings,
                    }
                )
                hist["checks"] = checks_list
                pos["earnings_check_history"] = hist
                if check.traffic_light == TrafficLight.RED:
                    pos["status"] = "UNDER_REVIEW"
                write_yaml(path, pos)
        except Exception as exc:
            errors.append((ticker, str(exc)))

    if report_lines:
        notifier = Notifier(data_sources, paths.reports_dir)
        notifier.dispatch(
            AlertMessage(
                title=f"Quarterly Earnings Diff ({len(report_lines)} holding(s))",
                severity=worst_light.value,
                body_markdown="\n".join(report_lines),
                category="earnings_diff",
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
