#!/usr/bin/env python3
"""Layer 1: Active Industry Discovery & State Machine Radar (DORMANT <-> ACTIVE).

Responsibilities:
1. Scan free primary signals (USASpending TTM awards & YoY growth, SEC EDGAR EFTS
   S-1/8-K/10-K/10-Q filing clusters, bellwether CapEx acceleration, and optional
   local transcripts under memory/inputs/transcripts/).
2. Compute aggregate TTM revenue & median YoY growth of tracked leader tickers via SEC XBRL.
3. Evaluate DORMANT industries in memory/industries/ against awakening thresholds in
   config/rules.yaml (respecting state_change_cooldown_days).
4. Persist state transitions + discovered candidate_tickers back into memory/industries/
   and optionally emit a Markdown radar report via Notifier.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from coffee_can.clients.sec_edgar import (
    SecEdgarClient,
    extract_capex_growth,
    extract_fundamentals_from_company_facts,
)
from coffee_can.clients.usaspending import UsaSpendingClient
from coffee_can.config import (
    ProjectPaths,
    get_sec_user_agent,
    load_data_sources,
    load_environment,
    load_rules,
    resolve_workspace_root,
)
from coffee_can.notifier import AlertMessage, Notifier
from coffee_can.radar import (
    RadarEvaluation,
    RadarMetrics,
    evaluate_industry_state,
    merge_candidate_tickers,
    scan_local_transcripts,
)
from coffee_can.storage import list_active_yaml_files, read_yaml, write_yaml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Layer 1: Scan external signals and update industry state machines."
    )
    parser.add_argument(
        "--env",
        choices=["prod", "sandbox"],
        default=None,
        help="Target workspace environment (defaults to COFFEE_CAN_ENV or 'prod').",
    )
    parser.add_argument(
        "--industry",
        type=str,
        default=None,
        help="Optional industry slug to scan (default: scan all tracked industries).",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Also scan bellwether mega-cap CapEx acceleration (MSFT/AMZN/GOOGL/META/NVDA/etc.).",
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
        help="Bypass local .cache/ files and fetch fresh SEC & USASpending data.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview scan & state evaluations without mutating memory/industries/ files.",
    )
    return parser


def _scan_industry_metrics(
    record: dict[str, Any],
    sec_client: SecEdgarClient,
    usa_client: UsaSpendingClient,
    data_sources: dict[str, Any],
    paths: ProjectPaths,
    today_str: str,
) -> RadarMetrics:
    slug = str(record.get("slug", "unknown"))
    signals = record.get("signals") or {}
    sec_keywords: list[str] = list(signals.get("sec_keywords") or [])
    usa_keywords: list[str] = list(signals.get("usaspending_keywords") or [])
    naics_codes: list[str] = list(signals.get("naics_codes") or [])
    candidates: list[str] = list(record.get("candidate_tickers") or [])
    leader_tickers: list[str] = list(record.get("leader_tickers") or candidates[:5])
    xbrl_tags = data_sources["sec_edgar"]["xbrl_tag_fallbacks"]
    radar_forms = list(data_sources["sec_edgar"].get("radar_forms", ["S-1", "8-K", "10-K", "10-Q"]))

    # 1. Leader aggregate TTM revenue & median YoY growth via SEC XBRL
    revs: list[float] = []
    growths: list[float] = []
    for ticker in leader_tickers[:10]:
        try:
            cik = sec_client.lookup_cik(ticker)
            facts = sec_client.get_company_facts(cik)
            fund = extract_fundamentals_from_company_facts(facts, xbrl_tags)
            if fund.get("ttm_revenue_usd_m") is not None:
                revs.append(float(fund["ttm_revenue_usd_m"]))
            if fund.get("revenue_yoy_growth") is not None:
                growths.append(float(fund["revenue_yoy_growth"]))
        except Exception:
            continue

    agg_rev = round(sum(revs), 2) if revs else None
    med_yoy = round(statistics.median(growths), 4) if growths else None

    # 2. USASpending TTM contract volume & YoY surge
    usa_ttm_m = 0.0
    usa_yoy: float | None = None
    if data_sources.get("usaspending", {}).get("enabled", True) and (usa_keywords or naics_codes):
        try:
            usa_stats = usa_client.get_trailing_12m_spending(
                keywords=usa_keywords,
                naics_codes=naics_codes or None,
            )
            usa_ttm_m = float(usa_stats.get("ttm_award_usd_m") or 0.0)
            usa_yoy = usa_stats.get("yoy_growth")
        except Exception:
            pass

    # 3. SEC EFTS 180-day filing cluster & candidate ticker discovery
    sec_cluster_count = 0
    discovered_tickers: list[str] = []
    cluster_forms = [f for f in radar_forms if f in {"S-1", "8-K"}] or ["S-1", "8-K"]
    for kw in sec_keywords[:3]:
        try:
            res = sec_client.search_efts(query=kw, forms=cluster_forms, lookback_days=180, limit=25)
            sec_cluster_count += int(res.get("total_hits", 0))
            for t in res.get("discovered_tickers", []):
                if t not in discovered_tickers:
                    discovered_tickers.append(t)
        except Exception:
            continue

    # 4. Local manual transcripts scan
    transcript_mentions = scan_local_transcripts(
        paths.transcripts_dir, sec_keywords + usa_keywords
    )

    return RadarMetrics(
        slug=slug,
        as_of_date=today_str,
        leaders_aggregate_revenue_usd_m=agg_rev,
        leaders_median_yoy_growth=med_yoy,
        leaders_sampled=len(revs),
        usaspending_12m_award_usd_m=usa_ttm_m,
        usaspending_yoy_growth=usa_yoy,
        sec_cluster_filings_180d=sec_cluster_count,
        sec_discovered_tickers=discovered_tickers,
        transcript_mentions=transcript_mentions,
    )


def _scan_bellwether_capex(
    sec_client: SecEdgarClient, data_sources: dict[str, Any]
) -> list[dict[str, Any]]:
    bellwethers = data_sources.get("transcripts", {}).get("bellwether_tickers", [])
    xbrl_tags = data_sources["sec_edgar"]["xbrl_tag_fallbacks"]
    rows: list[dict[str, Any]] = []
    for ticker in bellwethers:
        try:
            cik = sec_client.lookup_cik(ticker)
            facts = sec_client.get_company_facts(cik)
            cap = extract_capex_growth(facts, xbrl_tags)
            rows.append({"ticker": ticker, **cap})
        except Exception as exc:
            rows.append({"ticker": ticker, "error": str(exc)})
    return rows


def _fmt_m(val: float | None) -> str:
    return f"${val:,.0f}M" if val is not None else "N/A"


def _fmt_pct(val: float | None) -> str:
    return f"{val:+.1%}" if val is not None else "N/A"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_environment(ROOT_DIR)

    ws_root, env_name = resolve_workspace_root(args.env, ROOT_DIR)
    paths = ProjectPaths.from_root(ws_root)
    rules = load_rules(ws_root)
    data_sources = load_data_sources(ws_root)

    industries = list_active_yaml_files(paths.industries_dir)
    if args.industry:
        industries = [
            p for p in industries if read_yaml(p).get("slug", p.stem) == args.industry
        ]

    awaken_cfg = rules["layer1_industry_radar"]["state_machine"]["awaken_thresholds"]
    print(f"=== [Layer 1] Topic Radar & Industry State Machine [{env_name.upper()}] ===")
    print(f"Tracked industries     : {len(industries)}")
    print(f"Global awaken revenue  : ${awaken_cfg['min_leaders_aggregate_revenue_usd_m']}M")
    print(f"Dry run                : {args.dry_run}")

    if not industries and not args.discover:
        print(
            "No tracked industry files in memory/industries/. Add a YAML file or pass --discover."
        )
        return 0

    try:
        user_agent = args.sec_user_agent or get_sec_user_agent(data_sources)
    except OSError:
        if args.dry_run:
            # Allow offline/CI dry-run smoke test without live network or SEC_USER_AGENT
            print("SEC_USER_AGENT not set; skipping live network calls in --dry-run preview.")
            for p in industries:
                rec = read_yaml(p)
                cnt = len(rec.get("candidate_tickers", []))
                slug_name = rec.get("slug", p.stem)
                print(f"  - {slug_name} [status={rec.get('status')}, candidates={cnt}]")
            return 0
        raise

    sec_client = SecEdgarClient(
        user_agent=user_agent,
        config=data_sources["sec_edgar"],
        project_root=ROOT_DIR,
        use_cache=not args.no_cache,
    )
    usa_client = UsaSpendingClient(
        config=data_sources["usaspending"],
        project_root=ROOT_DIR,
        use_cache=not args.no_cache,
    )
    bellwether_set = set(data_sources.get("transcripts", {}).get("bellwether_tickers", []))
    today_str = datetime.now(UTC).date().isoformat()

    evaluations: list[RadarEvaluation] = []
    awakened_lines: list[str] = []

    header = (
        f"{'INDUSTRY':<28} {'STATUS':<15} {'AGG_REV':>10} {'MED_YOY':>8} "
        f"{'USA_12M':>10} {'SEC_180D':>8} {'NEW_TKRS'}"
    )
    if industries:
        print("\n" + header)
        print("-" * len(header))

    for path in industries:
        record = read_yaml(path)
        metrics = _scan_industry_metrics(
            record=record,
            sec_client=sec_client,
            usa_client=usa_client,
            data_sources=data_sources,
            paths=paths,
            today_str=today_str,
        )
        evaluation = evaluate_industry_state(record, metrics, rules, today_str)
        evaluations.append(evaluation)

        existing_tkrs = list(record.get("candidate_tickers") or [])
        merged_tkrs = merge_candidate_tickers(
            existing_tkrs,
            metrics.sec_discovered_tickers,
            bellwether_exclude=bellwether_set,
        )
        newly_added = [t for t in merged_tkrs if t not in {x.upper() for x in existing_tkrs}]

        status_col = (
            f"{evaluation.previous_status.value}->{evaluation.new_status.value}"
            if evaluation.changed
            else evaluation.new_status.value
        )
        print(
            f"{metrics.slug:<28} {status_col:<15} "
            f"{_fmt_m(metrics.leaders_aggregate_revenue_usd_m):>10} "
            f"{_fmt_pct(metrics.leaders_median_yoy_growth):>8} "
            f"{_fmt_m(metrics.usaspending_12m_award_usd_m):>10} "
            f"{metrics.sec_cluster_filings_180d:>8d} "
            f"{','.join(newly_added[:5]) or '-'}"
        )

        if evaluation.changed:
            awakened_lines.append(f"- **{metrics.slug}**: {evaluation.rationale}")

        if not args.dry_run:
            if not record.get("leader_tickers") and existing_tkrs:
                record["leader_tickers"] = existing_tkrs
            record["last_scanned_at"] = today_str
            record["latest_radar_snapshot"] = metrics.to_dict()
            record["candidate_tickers"] = merged_tkrs
            if evaluation.changed:
                record["status"] = evaluation.new_status.value
                record["last_state_change_at"] = today_str
                history = list(record.get("history") or [])
                history.append(
                    {
                        "date": today_str,
                        "event": "AWAKENED",
                        "from_status": evaluation.previous_status.value,
                        "to_status": evaluation.new_status.value,
                        "evidence": evaluation.rationale,
                    }
                )
                record["history"] = history
            write_yaml(path, record)

    if args.discover:
        print("\n=== Bellwether CapEx Radar (SEC XBRL TTM) ===")
        capex_rows = _scan_bellwether_capex(sec_client, data_sources)
        for row in capex_rows:
            if "error" in row:
                print(f"  - {row['ticker']:<6} ERROR: {row['error']}")
            else:
                print(
                    f"  - {row['ticker']:<6} CapEx TTM={_fmt_m(row['ttm_capex_usd_m']):>10} "
                    f"YoY={_fmt_pct(row['capex_yoy_growth']):>8} "
                    f"(period={row.get('latest_period_end')})"
                )

    if awakened_lines and not args.dry_run:
        notifier = Notifier(data_sources, paths.reports_dir)
        notifier.dispatch(
            AlertMessage(
                title=f"Layer 1 Radar: {len(awakened_lines)} Industry Awakening(s)",
                severity="TRIGGER",
                body_markdown="\n".join(awakened_lines),
                category="topic_radar",
            ),
            dry_run=args.dry_run,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
