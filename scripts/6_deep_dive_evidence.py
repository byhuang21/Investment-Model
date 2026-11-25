#!/usr/bin/env python3
"""Layer 3: Automated Objective Evidence Collector (Form 4 + 3-Year Capital Allocation).

Responsibilities:
1. For PASS dossiers in memory/research/<TICKER>/, fetch:
   - Past 24 months SEC Form 4 XML filings (open-market P buys vs discretionary S vs 10b5-1 S)
   - Past 3 fiscal years 10-K XBRL capital allocation (R&D, CapEx, M&A, Buybacks, Dividends, SBC)
   - Recent 10-K / 10-Q / 8-K primary filing index with direct SEC EDGAR URLs
2. Populate memory/research/<TICKER>/evidence.md and scorecard.yaml objective_evidence.
3. Advance status from DRAFTING_EVIDENCE -> PENDING_HUMAN_REVIEW.
   STRICTLY obeys AGENT_RULES.md: NEVER touches human_subjective_score or promotes to coffee_can/.
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

from coffee_can.clients.sec_edgar import SecEdgarClient
from coffee_can.config import (
    ProjectPaths,
    get_sec_user_agent,
    load_data_sources,
    load_environment,
    resolve_workspace_root,
)
from coffee_can.deep_dive import (
    Form4Transaction,
    extract_multiyear_capital_allocation,
    parse_form4_xml,
    render_layer3_evidence_markdown,
    summarize_form4_activity,
    update_scorecard_with_evidence,
)
from coffee_can.snapshot import export_static_snapshot
from coffee_can.storage import read_yaml, write_yaml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Layer 3: Collect objective SEC Form 4 & 3Y capital allocation evidence."
    )
    parser.add_argument(
        "--env",
        choices=["prod", "sandbox"],
        default=None,
        help="Target workspace environment (defaults to COFFEE_CAN_ENV or 'prod').",
    )
    parser.add_argument(
        "--tickers",
        nargs="*",
        default=None,
        help="Specific tickers in memory/research/ to enrich (e.g. --tickers POWL TMDX).",
    )
    parser.add_argument(
        "--all-drafting",
        action="store_true",
        help="Enrich all dossiers currently in DRAFTING_EVIDENCE status.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=730,
        help="Form 4 lookback window in days (default: 730 / ~24 months).",
    )
    parser.add_argument(
        "--max-form4",
        type=int,
        default=40,
        help="Maximum Form 4 XML filings to parse per ticker (default: 40).",
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
        help="Bypass local .cache/sec_edgar/ and fetch fresh SEC filings.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print objective evidence summary without writing scorecard.yaml or evidence.md.",
    )
    return parser


def _resolve_target_tickers(
    paths: ProjectPaths,
    explicit: list[str] | None,
    all_drafting: bool,
) -> list[str]:
    if explicit:
        return sorted({t.strip().upper() for t in explicit if t.strip()})
    if not paths.research_dir.exists():
        return []
    found: list[str] = []
    for entry in sorted(paths.research_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        sc_path = entry / "scorecard.yaml"
        if not sc_path.exists():
            continue
        sc = read_yaml(sc_path)
        if all_drafting and sc.get("status") != "DRAFTING_EVIDENCE":
            continue
        if sc.get("status") == "APPROVED_FOR_CAN":
            continue
        found.append(str(sc.get("ticker", entry.name)).upper())
    return found


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_environment(ROOT_DIR)

    ws_root, env_name = resolve_workspace_root(args.env, ROOT_DIR)
    paths = ProjectPaths.from_root(ws_root)
    data_sources = load_data_sources(ws_root)

    tickers = _resolve_target_tickers(paths, args.tickers, args.all_drafting)
    print(f"=== [Layer 3] Objective Evidence Collector [{env_name.upper()}] ===")
    print(f"Target dossiers ({len(tickers)}): {tickers or '(none)'}")
    print(f"Form 4 window     : {args.lookback_days}d (max {args.max_form4} filings/ticker)")
    print(f"Dry run           : {args.dry_run}")

    if not tickers:
        print("No matching research dossiers found under memory/research/.")
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

    errors: list[tuple[str, str]] = []
    header = (
        f"{'TICKER':<8} {'F4_PARSED':>9} {'P_BUYS':>11} {'DISC_SELL':>11} "
        f"{'10B5_SELL':>11} {'3Y_FCF':>10} {'STATUS'}"
    )
    print("\n" + header)
    print("-" * len(header))

    for ticker in tickers:
        dossier_dir = paths.research_dir / ticker
        sc_path = dossier_dir / "scorecard.yaml"
        if not sc_path.exists():
            errors.append((ticker, f"Missing {sc_path} (run 2_quant_screener.py first)"))
            continue
        try:
            sc = read_yaml(sc_path)
            cik = str(sc.get("cik") or sec_client.lookup_cik(ticker))

            # 1. Multi-year 10-K capital allocation
            facts = sec_client.get_company_facts(cik)
            cap_rows = extract_multiyear_capital_allocation(facts, xbrl_tags, years=3)

            # 2. Past 24-month Form 4 XML parsing
            f4_meta = sec_client.get_recent_form4_filings(
                cik, lookback_days=args.lookback_days, max_filings=args.max_form4
            )
            all_txns: list[Form4Transaction] = []
            parsed_count = 0
            for meta in f4_meta:
                if not meta.get("primary_document"):
                    continue
                try:
                    xml_text = sec_client.get_form4_xml(
                        cik, meta["accession"], meta["primary_document"]
                    )
                    txns = parse_form4_xml(
                        xml_text, accession=meta["accession"], filing_date=meta["filing_date"]
                    )
                    all_txns.extend(txns)
                    parsed_count += 1
                except Exception:
                    continue
            f4_summary = summarize_form4_activity(all_txns, filings_parsed=parsed_count)

            # 3. Recent 10-K / 10-Q / 8-K narrative filing index
            narrative = sec_client.get_recent_narrative_filings(cik, limit=12)

            updated_sc = update_scorecard_with_evidence(
                existing_scorecard=sc,
                cap_alloc_rows=cap_rows,
                form4_summary=f4_summary,
                narrative_filings=narrative,
                today_str=today_str,
            )
            evidence_md = render_layer3_evidence_markdown(
                scorecard=updated_sc,
                cap_alloc_rows=cap_rows,
                form4_summary=f4_summary,
                narrative_filings=narrative,
                today_str=today_str,
            )

            fcf_3y = sum((r["fcf_usd_m"] or 0.0) for r in cap_rows)
            print(
                f"{ticker:<8} {parsed_count:>9d} "
                f"${f4_summary.open_market_buy_usd / 1e6:>9.2f}M "
                f"${f4_summary.discretionary_sell_usd / 1e6:>9.2f}M "
                f"${f4_summary.plan_10b5_1_sell_usd / 1e6:>9.2f}M "
                f"${fcf_3y:>8,.0f}M {updated_sc['status']}"
            )

            if not args.dry_run:
                write_yaml(sc_path, updated_sc)
                (dossier_dir / "evidence.md").write_text(evidence_md, encoding="utf-8")
        except Exception as exc:
            errors.append((ticker, str(exc)))

    if not args.dry_run and env_name == "prod" and not errors:
        export_static_snapshot(ROOT_DIR)

    if errors:
        print("\nErrors:")
        for ticker, msg in errors:
            print(f"  - {ticker}: {msg}")

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
