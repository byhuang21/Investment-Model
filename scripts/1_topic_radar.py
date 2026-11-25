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
    DiscoveredThemeSignal,
    RadarEvaluation,
    RadarMetrics,
    build_industry_record_from_signal,
    evaluate_industry_state,
    merge_candidate_tickers,
    merge_discovered_signals,
    qualifies_for_auto_enroll,
    scan_local_transcripts,
)
from coffee_can.schemas import IndustryStatus
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
        help=(
            "Also run Bellwether CapEx radar + Zero-Keyword Blind Discovery Engine "
            "(writes candidate themes to memory/quarantine/signals/discovered_themes.yaml)."
        ),
    )
    parser.add_argument(
        "--discover-only",
        action="store_true",
        help="Run only Bellwether CapEx + Blind Discovery Engine without rescanning industries.",
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


def _run_blind_discovery(
    sec_client: SecEdgarClient,
    usa_client: UsaSpendingClient,
    data_sources: dict[str, Any],
    bellwether_set: set[str],
    tracked_slugs: set[str],
    today_str: str,
) -> list[DiscoveredThemeSignal]:
    """Run the 3-channel Blind Discovery Engine (NAICS surge, SEC S-1 clusters, cross-sector)."""
    cfg = data_sources.get("blind_discovery") or {}
    if not cfg.get("enabled", True):
        return []

    fresh: list[DiscoveredThemeSignal] = []

    # Channel A: Zero-keyword USASpending 6-digit NAICS YoY surge
    naics_cfg = cfg.get("naics_surge") or {}
    if naics_cfg.get("enabled", True) and data_sources.get("usaspending", {}).get("enabled", True):
        try:
            surging = usa_client.get_surging_naics_categories(
                min_ttm_usd_m=float(naics_cfg.get("min_ttm_usd_m", 100.0)),
                min_prior_usd_m=float(naics_cfg.get("min_prior_usd_m", 25.0)),
                min_yoy_growth=float(naics_cfg.get("min_yoy_growth", 0.25)),
                pages=int(naics_cfg.get("pages", 2)),
                top_n=int(naics_cfg.get("top_n", 6)),
                exclude_naics_prefixes=tuple(naics_cfg.get("exclude_naics_prefixes") or ()),
            )
            for row in surging:
                code = str(row["naics_code"])
                name = str(row["naics_name"])
                ttm_m = float(row["ttm_usd_m"])
                prior_m = float(row["prior_ttm_usd_m"])
                yoy = float(row["yoy_growth"])
                fresh.append(
                    DiscoveredThemeSignal(
                        slug=f"naics_{code}",
                        name=f"{name} (NAICS {code})",
                        source="USASPENDING_NAICS",
                        headline_metric=f"{yoy:+.1%} YoY (${ttm_m:,.0f}M TTM)",
                        rationale=(
                            f"联邦政府采购门类 NAICS {code} 近 12 个月合同额由 "
                            f"${prior_m:,.0f}M 跃升至 ${ttm_m:,.0f}M（同比 {yoy:+.1%}）。"
                        ),
                        discovered_at=today_str,
                        last_seen_at=today_str,
                        metrics={
                            "ttm_award_usd_m": ttm_m,
                            "prior_ttm_award_usd_m": prior_m,
                            "yoy_growth": yoy,
                        },
                        naics_codes=[code],
                        profit_pool_description=(
                            f"美国联邦采购门类 NAICS {code}（{name}）的存量财政与基础设施预算池"
                        ),
                        demand_innovation_hypothesis=(
                            f"近 12 个月联邦订单同比放量 {yoy:+.1%}，"
                            "需核查是否源于技术代际切换或政策刚需"
                        ),
                    )
                )
        except Exception as exc:
            print(f"  [WARN] USASpending NAICS blind scan skipped: {exc}")

    # Channel B: Zero-keyword SEC EDGAR S-1 IPO SIC cluster sweep
    s1_cfg = cfg.get("sec_s1_clusters") or {}
    if s1_cfg.get("enabled", True):
        try:
            clusters = sec_client.discover_s1_clusters(
                lookback_days=int(s1_cfg.get("lookback_days", 180)),
                max_hits=int(s1_cfg.get("max_hits", 200)),
                min_unique_filers=int(s1_cfg.get("min_unique_filers", 2)),
                top_n=int(s1_cfg.get("top_n", 6)),
                exclude_sic_prefixes=tuple(s1_cfg.get("exclude_sic_prefixes") or ()),
            )
            for cl in clusters:
                sic = str(cl["sic_code"])
                sic_name = str(cl["sic_name"])
                cnt = int(cl["unique_filers"])
                filers = list(cl.get("filers") or [])
                tickers = [
                    t
                    for t in (cl.get("discovered_tickers") or [])
                    if str(t).upper() not in bellwether_set
                ]
                filer_preview = "、".join(filers[:3])
                fresh.append(
                    DiscoveredThemeSignal(
                        slug=f"sic_{sic}",
                        name=f"{sic_name} (SIC {sic})",
                        source="SEC_S1_CLUSTER",
                        headline_metric=f"{cnt} 家公司 180天递交 S-1",
                        rationale=(
                            f"近 {int(s1_cfg.get('lookback_days', 180))} 天共有 {cnt} 家独立企业"
                            f"在 SIC {sic} 下密集递交 S-1 IPO 招股书"
                            + (f"（含 {filer_preview}）。" if filer_preview else "。")
                        ),
                        discovered_at=today_str,
                        last_seen_at=today_str,
                        metrics={
                            "unique_s1_filers_180d": cnt,
                            "sample_filers": filers[:5],
                        },
                        sic_codes=[sic],
                        discovered_tickers=tickers[:10],
                        profit_pool_description=(
                            f"一级市场资本密集涌入的 SIC {sic}（{sic_name}）细分行业集群"
                        ),
                        demand_innovation_hypothesis=(
                            f"180 天内出现 {cnt} 家独立公司集中 IPO，往往映射细分赛道商业化拐点"
                        ),
                    )
                )
        except Exception as exc:
            print(f"  [WARN] SEC S-1 SIC blind scan skipped: {exc}")

    # Channel C: Cross-sector emerging theme probes (non-B2G commercial/tech/consumer/medtech)
    probe_cfg = cfg.get("sector_probes") or {}
    if probe_cfg.get("enabled", True):
        lookback = int(probe_cfg.get("lookback_days", 180))
        min_filings = int(probe_cfg.get("min_sec_filings_180d", 5))
        max_themes = int(probe_cfg.get("max_themes", 8))
        probe_hits: list[tuple[int, DiscoveredThemeSignal]] = []
        for probe in probe_cfg.get("probes") or []:
            slug = str(probe.get("slug") or "").strip()
            if not slug or slug in tracked_slugs:
                continue
            sec_kws = [str(k).strip() for k in (probe.get("sec_keywords") or []) if str(k).strip()]
            usa_kws = [
                str(k).strip()
                for k in (probe.get("usaspending_keywords") or [])
                if str(k).strip()
            ]
            if not sec_kws:
                continue
            total_filings = 0
            found_tickers: list[str] = []
            for kw in sec_kws[:2]:
                try:
                    res = sec_client.search_efts(
                        query=kw,
                        forms=["S-1", "8-K"],
                        lookback_days=lookback,
                        limit=20,
                    )
                    total_filings += int(res.get("total_hits", 0))
                    for t in res.get("discovered_tickers", []):
                        tu = str(t).upper()
                        if tu not in bellwether_set and tu not in found_tickers:
                            found_tickers.append(tu)
                except Exception:
                    continue
            if total_filings < min_filings:
                continue
            sig = DiscoveredThemeSignal(
                slug=slug,
                name=str(probe.get("name") or slug),
                source="CROSS_SECTOR_PROBE",
                headline_metric=f"{total_filings} 条 SEC 180天公告",
                rationale=(
                    f"跨行业探测关键词（{', '.join(sec_kws[:2])}）在近 {lookback} 天命中 "
                    f"{total_filings} 条 SEC 8-K/S-1 公告，"
                    f"识别出 {len(found_tickers)} 只美股关联标的。"
                ),
                discovered_at=today_str,
                last_seen_at=today_str,
                metrics={"sec_filings_180d": total_filings},
                sec_keywords=sec_kws,
                usaspending_keywords=usa_kws,
                discovered_tickers=found_tickers[:12],
                profit_pool_description=str(probe.get("profit_pool_description") or ""),
                demand_innovation_hypothesis=str(probe.get("demand_innovation_hypothesis") or ""),
            )
            probe_hits.append((total_filings, sig))

        probe_hits.sort(key=lambda pair: (-pair[0], pair[1].slug))
        fresh.extend(sig for _, sig in probe_hits[:max_themes])

    return fresh


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

    all_industry_files = list_active_yaml_files(paths.industries_dir)
    tracked_slugs: set[str] = set()
    tracked_naics: set[str] = set()
    tracked_sics: set[str] = set()
    for p in all_industry_files:
        rec = read_yaml(p)
        tracked_slugs.add(str(rec.get("slug", p.stem)))
        sigs = rec.get("signals") or {}
        tracked_naics.update(str(c) for c in (sigs.get("naics_codes") or []))
        tracked_sics.update(str(c) for c in (sigs.get("sic_codes") or []))

    run_discover = bool(args.discover or args.discover_only)
    industries = [] if args.discover_only else list(all_industry_files)
    if args.industry and not args.discover_only:
        industries = [
            p for p in industries if read_yaml(p).get("slug", p.stem) == args.industry
        ]

    awaken_cfg = rules["layer1_industry_radar"]["state_machine"]["awaken_thresholds"]
    print(f"=== [Layer 1] Topic Radar & Industry State Machine [{env_name.upper()}] ===")
    print(f"Tracked industries     : {len(industries)}")
    print(f"Global awaken revenue  : ${awaken_cfg['min_leaders_aggregate_revenue_usd_m']}M")
    print(f"Blind discovery        : {run_discover}")
    print(f"Dry run                : {args.dry_run}")

    if not industries and not run_discover:
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

    if run_discover:
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

        print("\n=== Zero-Keyword Blind Discovery Engine (Quarantine Signals) ===")
        fresh_signals = _run_blind_discovery(
            sec_client=sec_client,
            usa_client=usa_client,
            data_sources=data_sources,
            bellwether_set=bellwether_set,
            tracked_slugs=tracked_slugs,
            today_str=today_str,
        )
        signals_path = paths.quarantine_signals_dir / "discovered_themes.yaml"
        existing_doc = read_yaml(signals_path) if signals_path.exists() else {}
        existing_items = list(existing_doc.get("signals") or [])
        merged_signals = merge_discovered_signals(
            existing_items=existing_items,
            fresh_items=fresh_signals,
            tracked_slugs=tracked_slugs,
            tracked_naics=tracked_naics,
            tracked_sics=tracked_sics,
            today_str=today_str,
        )
        new_count = sum(1 for item in merged_signals if item.get("status") == "NEW")
        print(
            f"Discovered {len(fresh_signals)} raw signal(s) -> "
            f"{new_count} NEW waiting in quarantine/signals/discovered_themes.yaml"
        )
        for item in merged_signals:
            if item.get("status") != "NEW":
                continue
            tkrs = ",".join(item.get("discovered_tickers", [])[:5]) or "-"
            print(
                f"  - [{item['source']:<18}] {item['slug']:<30} "
                f"{item['headline_metric']:<26} tickers={tkrs}"
            )

        auto_cfg = (data_sources.get("blind_discovery") or {}).get("auto_enroll") or {}
        if auto_cfg.get("enabled", False):
            max_per_run = int(auto_cfg.get("max_new_industries_per_run", 3))
            max_total = int(auto_cfg.get("max_total_tracked_industries", 20))
            slots = max(0, min(max_per_run, max_total - len(tracked_slugs)))
            # Prioritize high-confidence themes that already discovered listed US tickers
            candidates_for_enroll: list[tuple[int, int, dict[str, Any], str]] = []
            for item in merged_signals:
                ok, reason = qualifies_for_auto_enroll(item, auto_cfg)
                if not ok:
                    continue
                slug = str(item.get("slug", ""))
                if not slug or slug in tracked_slugs:
                    continue
                tkrs_cnt = len(item.get("discovered_tickers") or [])
                has_tkrs = 0 if tkrs_cnt > 0 else 1
                candidates_for_enroll.append((has_tkrs, -tkrs_cnt, item, reason))
            candidates_for_enroll.sort(key=lambda x: (x[0], x[1], str(x[2].get("slug", ""))))

            enrolled_count = 0
            for _, _, item, reason in candidates_for_enroll[:slots]:
                slug = str(item["slug"])
                ind_path = paths.industries_dir / f"{slug}.yaml"
                if ind_path.exists():
                    continue
                new_rec = build_industry_record_from_signal(
                    signal=item,
                    initial_status=IndustryStatus.DORMANT,
                    today_str=today_str,
                    rules=rules,
                    event_name="AUTO_ENROLLED_DORMANT",
                    allow_immediate_awaken=True,
                )
                metrics = _scan_industry_metrics(
                    record=new_rec,
                    sec_client=sec_client,
                    usa_client=usa_client,
                    data_sources=data_sources,
                    paths=paths,
                    today_str=today_str,
                )
                ev = evaluate_industry_state(new_rec, metrics, rules, today_str)
                merged_tkrs = merge_candidate_tickers(
                    list(new_rec.get("candidate_tickers") or []),
                    metrics.sec_discovered_tickers,
                    bellwether_exclude=bellwether_set,
                )
                new_rec["last_scanned_at"] = today_str
                new_rec["latest_radar_snapshot"] = metrics.to_dict()
                new_rec["candidate_tickers"] = merged_tkrs
                if not new_rec.get("leader_tickers") and merged_tkrs:
                    new_rec["leader_tickers"] = merged_tkrs[:5]
                if ev.changed:
                    new_rec["status"] = ev.new_status.value
                    new_rec["last_state_change_at"] = today_str
                    new_rec["history"].append(
                        {
                            "date": today_str,
                            "event": "AUTO_AWAKENED",
                            "from_status": ev.previous_status.value,
                            "to_status": ev.new_status.value,
                            "evidence": ev.rationale,
                        }
                    )
                    awakened_lines.append(
                        f"- **{slug}** (自动发现并激活): {ev.rationale}"
                    )
                else:
                    new_rec["last_state_change_at"] = today_str
                    awakened_lines.append(
                        f"- **{slug}** (自动建档为 DORMANT 休眠跟踪): {reason}"
                    )

                print(
                    f"  [AUTO-ENROLL] {slug:<30} -> {new_rec['status']:<8} "
                    f"(rev={_fmt_m(metrics.leaders_aggregate_revenue_usd_m)}, "
                    f"yoy={_fmt_pct(metrics.leaders_median_yoy_growth)}, reason={reason})"
                )

                if not args.dry_run:
                    write_yaml(ind_path, new_rec)
                    item["status"] = "PROMOTED"
                    item["auto_enrolled"] = True
                    item["promoted_at"] = today_str
                tracked_slugs.add(slug)
                enrolled_count += 1

            if enrolled_count:
                new_count = sum(1 for item in merged_signals if item.get("status") == "NEW")
                print(
                    f"Auto-enrolled {enrolled_count} high-confidence industry/industries "
                    f"into memory/industries/ ({new_count} remaining in quarantine)."
                )

        if not args.dry_run:
            payload = {
                "last_scanned_at": today_str,
                "total_signals": len(merged_signals),
                "new_signals_count": new_count,
                "signals": merged_signals,
            }
            write_yaml(signals_path, payload)

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
