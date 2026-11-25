"""Unit tests for Layer 1 industry state machine, cooldowns, and candidate ticker merging."""

from __future__ import annotations

from coffee_can.config import load_rules
from coffee_can.radar import (
    RadarMetrics,
    evaluate_industry_state,
    merge_candidate_tickers,
    scan_local_transcripts,
)
from coffee_can.schemas import IndustryStatus


def test_dormant_industry_awakens_on_scale_and_growth() -> None:
    rules = load_rules()
    record = {
        "slug": "organ_perfusion_medtech",
        "status": "DORMANT",
        "last_state_change_at": "2026-09-01",
        "awaken_conditions": {
            "min_leaders_aggregate_revenue_usd_m": 600,
            "min_sector_yoy_revenue_growth": 0.25,
        },
    }
    metrics = RadarMetrics(
        slug="organ_perfusion_medtech",
        as_of_date="2026-11-24",
        leaders_aggregate_revenue_usd_m=728.0,
        leaders_median_yoy_growth=0.285,
        leaders_sampled=1,
    )
    ev = evaluate_industry_state(record, metrics, rules, "2026-11-24")
    assert ev.changed is True
    assert ev.new_status == IndustryStatus.ACTIVE
    assert ev.cooldown_blocked is False


def test_dormant_awakening_blocked_during_cooldown() -> None:
    rules = load_rules()
    record = {
        "slug": "cooling_test",
        "status": "DORMANT",
        "last_state_change_at": "2026-11-10",  # Only 14 days ago (< 30d cooldown)
    }
    metrics = RadarMetrics(
        slug="cooling_test",
        as_of_date="2026-11-24",
        leaders_aggregate_revenue_usd_m=1500.0,
        leaders_median_yoy_growth=0.35,
    )
    ev = evaluate_industry_state(record, metrics, rules, "2026-11-24")
    assert ev.changed is False
    assert ev.new_status == IndustryStatus.DORMANT
    assert ev.cooldown_blocked is True


def test_dormant_stays_dormant_when_revenue_too_small() -> None:
    rules = load_rules()
    record = {
        "slug": "industrial_smr_nuclear",
        "status": "DORMANT",
        "last_state_change_at": "2026-09-01",
    }
    metrics = RadarMetrics(
        slug="industrial_smr_nuclear",
        as_of_date="2026-11-24",
        leaders_aggregate_revenue_usd_m=45.0,
        leaders_median_yoy_growth=0.10,
        usaspending_12m_award_usd_m=80.0,
        sec_cluster_filings_180d=12,
    )
    ev = evaluate_industry_state(record, metrics, rules, "2026-11-24")
    assert ev.changed is False
    assert ev.new_status == IndustryStatus.DORMANT


def test_merge_candidate_tickers_excludes_bellwethers_and_deduplicates() -> None:
    merged = merge_candidate_tickers(
        existing=["POWL", "IESC"],
        discovered=["MSFT", "POWL", "VRT", "NVDA", "MOD"],
        bellwether_exclude={"MSFT", "NVDA"},
        max_total=4,
    )
    assert merged == ["POWL", "IESC", "VRT", "MOD"]


def test_scan_local_transcripts_matches_keywords_and_ignores_readme(tmp_path) -> None:
    (tmp_path / "README.md").write_text("liquid cooling readme should be ignored", encoding="utf-8")
    (tmp_path / "MSFT_2026Q3_transcript.txt").write_text(
        "We are ramping liquid cooling capacity across AI clusters.", encoding="utf-8"
    )
    (tmp_path / "OTHER_2026Q3_transcript.md").write_text(
        "Unrelated retail update.", encoding="utf-8"
    )
    assert scan_local_transcripts(tmp_path, ["liquid cooling"]) == 1


def test_cluster_s1_hits_deduplicates_amendments_and_excludes_spacs() -> None:
    from coffee_can.clients.sec_edgar import cluster_s1_hits_by_sic

    raw_hits = [
        # Same CIK filing S-1 and S-1/A -> should count as 1 unique filer
        {
            "_source": {
                "ciks": ["0001111111"],
                "sics": ["3674"],
                "display_names": ["Alpha Semi Inc."],
                "file_date": "2026-10-01",
            }
        },
        {
            "_source": {
                "ciks": ["0001111111"],
                "sics": ["3674"],
                "display_names": ["Alpha Semi Inc."],
                "file_date": "2026-10-15",
            }
        },
        # Second unique company in SIC 3674
        {
            "_source": {
                "ciks": ["0002222222"],
                "sics": ["3674"],
                "display_names": ["Beta Photonics Corp."],
                "file_date": "2026-11-01",
            }
        },
        # SPACs (6770) must be excluded even with many filers
        {
            "_source": {
                "ciks": ["0003333333"],
                "sics": ["6770"],
                "display_names": ["Blank Check I"],
                "file_date": "2026-11-05",
            }
        },
        {
            "_source": {
                "ciks": ["0004444444"],
                "sics": ["6770"],
                "display_names": ["Blank Check II"],
                "file_date": "2026-11-06",
            }
        },
    ]

    clusters = cluster_s1_hits_by_sic(
        raw_hits=raw_hits,
        ticker_resolver=lambda cik: "ALPH" if cik == "0001111111" else None,
        min_unique_filers=2,
    )
    assert len(clusters) == 1
    assert clusters[0]["sic_code"] == "3674"
    assert clusters[0]["unique_filers"] == 2
    assert clusters[0]["discovered_tickers"] == ["ALPH"]


def test_merge_discovered_signals_preserves_dismissed_and_tracked_state() -> None:
    from coffee_can.radar import DiscoveredThemeSignal, merge_discovered_signals

    existing = [
        {
            "slug": "naics_334511",
            "name": "Nav Instruments",
            "source": "USASPENDING_NAICS",
            "status": "DISMISSED",
            "discovered_at": "2026-11-01",
            "last_seen_at": "2026-11-01",
        }
    ]
    fresh = [
        DiscoveredThemeSignal(
            slug="naics_334511",
            name="Nav Instruments",
            source="USASPENDING_NAICS",
            headline_metric="+40.0% YoY",
            rationale="Surge",
            discovered_at="2026-11-24",
            last_seen_at="2026-11-24",
            naics_codes=["334511"],
        ),
        DiscoveredThemeSignal(
            slug="sic_3612",
            name="Transformers",
            source="SEC_S1_CLUSTER",
            headline_metric="3 filers",
            rationale="Cluster",
            discovered_at="2026-11-24",
            last_seen_at="2026-11-24",
            sic_codes=["3612"],  # Already tracked!
        ),
        DiscoveredThemeSignal(
            slug="humanoid_industrial_robotics",
            name="Robotics",
            source="CROSS_SECTOR_PROBE",
            headline_metric="20 filings",
            rationale="Emerging",
            discovered_at="2026-11-24",
            last_seen_at="2026-11-24",
        ),
    ]

    merged = merge_discovered_signals(
        existing_items=existing,
        fresh_items=fresh,
        tracked_slugs=set(),
        tracked_naics=set(),
        tracked_sics={"3612"},
        today_str="2026-11-24",
    )
    by_slug = {item["slug"]: item for item in merged}
    assert by_slug["naics_334511"]["status"] == "DISMISSED"
    assert by_slug["naics_334511"]["discovered_at"] == "2026-11-01"
    assert by_slug["sic_3612"]["status"] == "PROMOTED"
    assert by_slug["humanoid_industrial_robotics"]["status"] == "NEW"


def test_usaspending_get_surging_naics_filters_and_ranks(monkeypatch) -> None:
    from coffee_can.clients.usaspending import UsaSpendingClient

    client = UsaSpendingClient(config={}, use_cache=False)

    def fake_fetch(start_date, end_date, limit=100, pages=2):
        if end_date.year == 2026:
            return {
                "236220": {
                    "naics_code": "236220",
                    "naics_name": "Building Construction",
                    "amount_usd": 500_000_000,
                },
                "334511": {
                    "naics_code": "334511",
                    "naics_name": "Navigation & Guidance Instruments",
                    "amount_usd": 360_000_000,
                },
                "336414": {
                    "naics_code": "336414",
                    "naics_name": "Space Vehicles",
                    "amount_usd": 200_000_000,
                },
            }
        return {
            "236220": {
                "naics_code": "236220",
                "naics_name": "Building Construction",
                "amount_usd": 200_000_000,
            },
            "334511": {
                "naics_code": "334511",
                "naics_name": "Navigation & Guidance Instruments",
                "amount_usd": 200_000_000,
            },
            "336414": {
                "naics_code": "336414",
                "naics_name": "Space Vehicles",
                "amount_usd": 190_000_000,
            },
        }

    from datetime import date

    monkeypatch.setattr(client, "_fetch_naics_category_window", fake_fetch)
    res = client.get_surging_naics_categories(
        min_ttm_usd_m=100.0,
        min_prior_usd_m=25.0,
        min_yoy_growth=0.25,
        as_of=date(2026, 11, 24),
    )
    assert len(res) == 1
    assert res[0]["naics_code"] == "334511"
    assert res[0]["yoy_growth"] == 0.8

