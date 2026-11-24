#!/usr/bin/env python3
"""Layer 2: Dual-Track Quant Hard Filter (PASS / NEAR_MISS / REJECTED).

Responsibilities:
1. Collect candidate tickers from ACTIVE industries in memory/industries/ or CLI --tickers.
2. Fetch financial fundamentals from SEC EDGAR CompanyFacts (XBRL) and market cap/price
   from yfinance.
3. Apply common floor + Track A (Profitable Compounder) / Track B (High-Growth Reinvestment) rules.
4. Route outcomes:
   - PASS      -> initialize memory/research/<TICKER>/ dossier skeleton
   - NEAR_MISS -> write memory/near_miss_pool/<TICKER>.yaml with target market cap
   - REJECTED  -> append to memory/rejected_log.json with price + SPY/QQQ benchmark close
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from coffee_can.clients.market_data import MarketDataClient
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
)
from coffee_can.schemas import FinancialSnapshot, RejectedEntry, ScreenDecision, ScreenOutcome
from coffee_can.screener import compute_near_miss_target_mcap, evaluate_snapshot
from coffee_can.storage import (
    append_rejected_entry,
    list_active_yaml_files,
    read_yaml,
    write_yaml,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Layer 2: Run dual-track quant hard screening and 3-way routing."
    )
    parser.add_argument(
        "--tickers",
        nargs="*",
        default=None,
        help="Explicit ticker list (e.g. --tickers POWL AVAV VRT). Defaults to ACTIVE industries.",
    )
    parser.add_argument(
        "--industry",
        type=str,
        default=None,
        help="Optional industry slug to attribute screened tickers to.",
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
        help="Bypass local .cache/ files and fetch fresh SEC & yfinance data.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate without writing to memory/research/, near_miss_pool/, or rejected_log.json.",
    )
    return parser


def collect_active_industry_tickers(
    industries_dir: Path, slug_filter: str | None
) -> dict[str, str | None]:
    """Return mapping `{TICKER: industry_slug}` from ACTIVE industries."""
    ticker_to_industry: dict[str, str | None] = {}
    for path in list_active_yaml_files(industries_dir):
        record = read_yaml(path)
        slug = str(record.get("slug", path.stem))
        if slug_filter and slug != slug_filter:
            continue
        if record.get("status") == "ACTIVE":
            for raw_ticker in record.get("candidate_tickers", []):
                ticker_to_industry[str(raw_ticker).strip().upper()] = slug
    return dict(sorted(ticker_to_industry.items()))


def build_financial_snapshot(
    ticker: str,
    cik: str,
    sec_title: str,
    fundamentals: dict[str, Any],
    quote: dict[str, Any],
    as_of_date: str,
) -> FinancialSnapshot:
    """Merge SEC XBRL fundamentals and yfinance quote snapshot into a FinancialSnapshot."""
    mcap_m = quote.get("market_cap_usd_m")
    ev_m = quote.get("enterprise_value_usd_m")
    rev_m = fundamentals.get("ttm_revenue_usd_m")
    cash_m = fundamentals.get("cash_and_st_inv_usd_m")
    debt_m = fundamentals.get("total_debt_usd_m") or 0.0

    if ev_m is None and mcap_m is not None:
        ev_m = mcap_m + debt_m - (cash_m or 0.0)

    ev_to_sales: float | None = None
    if ev_m is not None and rev_m is not None and rev_m > 0:
        ev_to_sales = round(ev_m / rev_m, 2)

    return FinancialSnapshot(
        ticker=ticker.upper(),
        as_of_date=as_of_date,
        cik=cik,
        company_name=quote.get("company_name") or sec_title or ticker.upper(),
        price_usd=quote.get("price_usd"),
        market_cap_usd_m=mcap_m,
        ev_to_sales=ev_to_sales,
        ttm_revenue_usd_m=rev_m,
        revenue_yoy_growth=fundamentals.get("revenue_yoy_growth"),
        gross_margin=fundamentals.get("gross_margin"),
        gross_margin_yoy_change=fundamentals.get("gross_margin_yoy_change"),
        ocf_usd_m=fundamentals.get("ocf_usd_m"),
        fcf_usd_m=fundamentals.get("fcf_usd_m"),
        cash_and_st_inv_usd_m=cash_m,
        cash_runway_months=fundamentals.get("cash_runway_months"),
        short_term_debt_to_cash=fundamentals.get("short_term_debt_to_cash"),
        goodwill_to_total_assets=fundamentals.get("goodwill_to_total_assets"),
        annual_share_dilution=fundamentals.get("annual_share_dilution"),
        filing_accession=fundamentals.get("filing_accession"),
    )


def route_pass(
    decision: ScreenDecision,
    paths: ProjectPaths,
    industry_slug: str | None,
    today_str: str,
) -> Path:
    """Initialize memory/research/<TICKER>/ dossier without clobbering human-reviewed files."""
    snap = decision.snapshot
    assert snap is not None
    dossier_dir = paths.research_dir / snap.ticker
    scorecard_path = dossier_dir / "scorecard.yaml"
    evidence_path = dossier_dir / "evidence.md"

    if scorecard_path.exists():
        existing = read_yaml(scorecard_path)
        if existing.get("dimensions", {}).get("5_management", {}).get("reviewed_by_human"):
            return scorecard_path

    dossier_dir.mkdir(parents=True, exist_ok=True)
    scorecard = {
        "ticker": snap.ticker,
        "company_name": snap.company_name,
        "cik": snap.cik,
        "industry_slug": industry_slug,
        "status": "DRAFTING_EVIDENCE",
        "created_at": today_str,
        "updated_at": today_str,
        "layer2_track_qualified": decision.track_qualified,
        "odds_check": {
            "current_market_cap_usd_m": snap.market_cap_usd_m,
            "conservative_10y_target_market_cap_usd_m": None,
            "implied_upside_multiple": None,
            "passed_10x_gate": None,
            "assumptions_summary": "",
        },
        "dimensions": {
            "1_demand_fulfilling_innovation": {
                "summary": "",
                "incumbent_dilemma": "",
                "evidence_refs": [],
            },
            "2_market_formation": {
                "why_now_inflection": "",
                "estimated_penetration_pct": None,
                "evidence_refs": [],
            },
            "3_competitive_landscape": {
                "emerging_moat_types": [],
                "moat_evidence": "",
                "evidence_refs": [],
            },
            "4_financial_floor": {
                "veto_passed": True,
                "unit_economics_note": "",
                "ttm_revenue_usd_m": snap.ttm_revenue_usd_m,
                "revenue_yoy_growth": snap.revenue_yoy_growth,
                "gross_margin": snap.gross_margin,
                "ocf_usd_m": snap.ocf_usd_m,
                "fcf_usd_m": snap.fcf_usd_m,
                "cash_runway_months": snap.cash_runway_months,
                "short_term_debt_to_cash": snap.short_term_debt_to_cash,
                "goodwill_to_assets": snap.goodwill_to_total_assets,
                "annual_share_dilution": snap.annual_share_dilution,
                "filing_refs": [snap.filing_accession] if snap.filing_accession else [],
            },
            "5_management": {
                "objective_evidence": {
                    "guidance_delivery_rate_note": "",
                    "capital_allocation_record": "",
                    "form4_net_insider_activity_24m": "",
                    "founder_led": None,
                    "insider_ownership_pct": None,
                    "evidence_refs": [],
                },
                "human_subjective_score": None,
                "human_notes": "",
                "reviewed_by_human": False,
                "reviewed_at": None,
            },
        },
        "decision": {
            "three_sentence_memo": ["", "", ""],
            "falsification_lines": [],
        },
    }
    write_yaml(scorecard_path, scorecard)

    if not evidence_path.exists():
        evidence_md = (
            f"# `{snap.ticker}` — 五维深度投研证据档案\n\n"
            f"- **公司名称**：{snap.company_name}\n"
            f"- **CIK**：`{snap.cik}`\n"
            f"- **所属赛道**：`{industry_slug or 'unassigned'}`\n"
            f"- **二层命中轨道**：`{decision.track_qualified}`\n"
            f"- **当前状态**：`DRAFTING_EVIDENCE`\n"
            f"- **初筛日期**：`{today_str}`\n\n"
            f"## 财务底线初筛快照（SEC Accession: `{snap.filing_accession or 'N/A'}`）\n"
            f"- 市值：`${snap.market_cap_usd_m}M` | EV/Sales：`{snap.ev_to_sales}x`\n"
            f"- TTM 营收：`${snap.ttm_revenue_usd_m}M` | "
            f"YoY：`{_fmt_pct(snap.revenue_yoy_growth)}`\n"
            f"- 毛利率：`{_fmt_pct(snap.gross_margin)}` | OCF：`${snap.ocf_usd_m}M` | "
            f"FCF：`${snap.fcf_usd_m}M`\n"
            f"- 现金跑道：`{snap.cash_runway_months or 'N/A (FCF+)'}` | "
            f"短债/现金：`{snap.short_term_debt_to_cash}` | "
            f"商誉/总资产：`{_fmt_pct(snap.goodwill_to_total_assets)}` | "
            f"年化稀释：`{_fmt_pct(snap.annual_share_dilution)}`\n\n"
            "## 后续动作\n"
            "请运行第三层 Agent 深挖补齐五维证据，并将 `scorecard.yaml` 状态更新为 "
            "`PENDING_HUMAN_REVIEW` 等待人类对掌舵人打分。\n"
        )
        evidence_path.write_text(evidence_md, encoding="utf-8")

    return scorecard_path


def route_near_miss(
    decision: ScreenDecision,
    paths: ProjectPaths,
    rules: dict[str, Any],
    industry_slug: str | None,
    today_str: str,
) -> Path:
    """Write or refresh memory/near_miss_pool/<TICKER>.yaml while preserving monitor_state."""
    snap = decision.snapshot
    assert snap is not None
    target_path = paths.near_miss_dir / f"{snap.ticker}.yaml"
    existing = read_yaml(target_path) if target_path.exists() else {}
    reason = decision.failed_rules[0] if decision.failed_rules else "VALUATION_GATE"
    target_mcap = compute_near_miss_target_mcap(snap, rules, reason)

    record = {
        "ticker": snap.ticker,
        "company_name": snap.company_name,
        "cik": snap.cik,
        "industry_slug": industry_slug or existing.get("industry_slug"),
        "added_at": existing.get("added_at", today_str),
        "updated_at": today_str,
        "near_miss_reason": reason,
        "market_cap_at_entry_usd_m": existing.get(
            "market_cap_at_entry_usd_m", snap.market_cap_usd_m
        ),
        "price_at_entry_usd": existing.get("price_at_entry_usd", snap.price_usd),
        "ev_to_sales_at_entry": existing.get("ev_to_sales_at_entry", snap.ev_to_sales),
        "targets": {
            "target_market_cap_usd_m": existing.get("targets", {}).get(
                "target_market_cap_usd_m", target_mcap
            ),
            "target_ev_to_sales": existing.get("targets", {}).get(
                "target_ev_to_sales",
                rules["layer2_quant_screener"]["valuation_gate"]["max_ev_to_sales_pass"],
            ),
        },
        "monitor_state": existing.get(
            "monitor_state",
            {
                "status": "WATCHING",
                "consecutive_days_below_target": 0,
                "last_checked_at": today_str,
                "last_market_cap_usd_m": snap.market_cap_usd_m,
                "last_price_usd": snap.price_usd,
                "last_alert_at": None,
                "last_alert_market_cap_usd_m": None,
            },
        ),
        "fundamentals_snapshot": {
            "ttm_revenue_usd_m": snap.ttm_revenue_usd_m,
            "revenue_yoy_growth": snap.revenue_yoy_growth,
            "gross_margin": snap.gross_margin,
            "cash_runway_months": snap.cash_runway_months,
            "annual_share_dilution": snap.annual_share_dilution,
            "track_qualified": decision.track_qualified,
            "filing_accession": snap.filing_accession,
        },
    }
    write_yaml(target_path, record)
    return target_path


def route_rejected(
    decision: ScreenDecision,
    paths: ProjectPaths,
    industry_slug: str | None,
    today_str: str,
    spy_price: float | None,
    qqq_price: float | None,
) -> Path:
    """Append rejected ticker with benchmark closes to memory/rejected_log.json."""
    snap = decision.snapshot
    assert snap is not None
    entry = RejectedEntry(
        ticker=snap.ticker,
        rejected_at=today_str,
        stage="LAYER2_QUANT",
        industry_slug=industry_slug,
        failed_rules=decision.failed_rules,
        price_at_rejection_usd=snap.price_usd,
        market_cap_at_rejection_usd_m=snap.market_cap_usd_m,
        spy_price_at_rejection=spy_price,
        qqq_price_at_rejection=qqq_price,
        metrics_snapshot=snap.to_dict(),
        notes=decision.notes,
    )
    append_rejected_entry(paths.rejected_log_file, entry)
    return paths.rejected_log_file


def _fmt_pct(val: float | None) -> str:
    return f"{val:.1%}" if val is not None else "N/A"


def _fmt_m(val: float | None) -> str:
    return f"${val:,.0f}M" if val is not None else "N/A"


def print_summary_table(decisions: list[ScreenDecision]) -> None:
    if not decisions:
        return
    header = (
        f"{'TICKER':<8} {'MCAP':>10} {'REV_TTM':>10} {'REV_YOY':>8} "
        f"{'GM':>7} {'FCF':>9} {'DILUT':>7} {'OUTCOME':<10} DETAIL"
    )
    print("\n" + header)
    print("-" * len(header))
    for dec in decisions:
        s = dec.snapshot
        assert s is not None
        detail = (
            dec.track_qualified
            if dec.outcome == ScreenOutcome.PASS
            else "; ".join(dec.failed_rules[:2])
        )
        print(
            f"{s.ticker:<8} {_fmt_m(s.market_cap_usd_m):>10} {_fmt_m(s.ttm_revenue_usd_m):>10} "
            f"{_fmt_pct(s.revenue_yoy_growth):>8} {_fmt_pct(s.gross_margin):>7} "
            f"{_fmt_m(s.fcf_usd_m):>9} {_fmt_pct(s.annual_share_dilution):>7} "
            f"{dec.outcome.value:<10} {detail}"
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_environment(ROOT_DIR)

    paths = ProjectPaths.from_root(ROOT_DIR)
    rules = load_rules(ROOT_DIR)
    data_sources = load_data_sources(ROOT_DIR)

    l2_cfg = rules["layer2_quant_screener"]
    mcap_cfg = l2_cfg["market_cap_usd_m"]

    if args.tickers:
        ticker_map: dict[str, str | None] = {
            t.strip().upper(): args.industry for t in args.tickers if t.strip()
        }
    else:
        ticker_map = collect_active_industry_tickers(paths.industries_dir, args.industry)

    print("=== [Layer 2] Dual-Track Quant Screener ===")
    print(f"PASS market cap range  : ${mcap_cfg['min_pass']}M - ${mcap_cfg['max_pass']}M")
    print(f"NEAR_MISS ceiling      : <= ${mcap_cfg['max_near_miss']}M")
    print(f"Max annual dilution    : {l2_cfg['common_floor']['max_annual_share_dilution']:.1%}")
    print(f"Candidate tickers ({len(ticker_map):d}) : {list(ticker_map.keys()) or '(none)'}")
    print(f"Dry run                : {args.dry_run}")

    if not ticker_map:
        print("No candidate tickers to screen. Pass --tickers or activate an industry in memory/.")
        return 0

    try:
        user_agent = args.sec_user_agent or get_sec_user_agent(data_sources)
    except OSError:
        if args.dry_run:
            print("SEC_USER_AGENT not set; skipping live network calls in --dry-run preview.")
            return 0
        raise
    sec_cfg = data_sources["sec_edgar"]
    yf_cfg = data_sources["yfinance"]

    sec_client = SecEdgarClient(
        user_agent=user_agent,
        config=sec_cfg,
        project_root=ROOT_DIR,
        use_cache=not args.no_cache,
    )
    market_client = MarketDataClient(
        config=yf_cfg,
        project_root=ROOT_DIR,
        use_cache=not args.no_cache,
    )

    today_str = datetime.now(UTC).date().isoformat()
    spy_price = market_client.get_latest_price("SPY")
    qqq_price = market_client.get_latest_price("QQQ")
    xbrl_tags = sec_cfg["xbrl_tag_fallbacks"]

    decisions: list[ScreenDecision] = []
    errors: list[tuple[str, str]] = []

    for ticker, industry_slug in ticker_map.items():
        try:
            cik, sec_title = sec_client.lookup_company(ticker)
            facts = sec_client.get_company_facts(cik)
            fundamentals = extract_fundamentals_from_company_facts(facts, xbrl_tags)
            quote = market_client.get_quote_snapshot(ticker)
            snapshot = build_financial_snapshot(
                ticker=ticker,
                cik=cik,
                sec_title=sec_title,
                fundamentals=fundamentals,
                quote=quote,
                as_of_date=today_str,
            )
            decision = evaluate_snapshot(snapshot, rules, sector=quote.get("sector"))
            decisions.append(decision)

            if not args.dry_run:
                if decision.outcome == ScreenOutcome.PASS:
                    route_pass(decision, paths, industry_slug, today_str)
                elif decision.outcome == ScreenOutcome.NEAR_MISS:
                    route_near_miss(decision, paths, rules, industry_slug, today_str)
                else:
                    route_rejected(decision, paths, industry_slug, today_str, spy_price, qqq_price)
        except Exception as exc:
            errors.append((ticker, str(exc)))

    print_summary_table(decisions)

    counts = {o: sum(1 for d in decisions if d.outcome == o) for o in ScreenOutcome}
    print(
        f"\nSummary: PASS={counts[ScreenOutcome.PASS]} | "
        f"NEAR_MISS={counts[ScreenOutcome.NEAR_MISS]} | "
        f"REJECTED={counts[ScreenOutcome.REJECTED]} | ERRORS={len(errors)}"
    )
    if errors:
        print("\nErrors:")
        for ticker, msg in errors:
            print(f"  - {ticker}: {msg}")

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
