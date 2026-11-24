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
