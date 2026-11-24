"""Unit tests for Feedback Loop 2: Quarterly Earnings Diff & Traffic-Light engine."""

from __future__ import annotations

from coffee_can.config import load_rules
from coffee_can.earnings_diff import evaluate_earnings_diff
from coffee_can.schemas import TrafficLight


def _sample_position(last_accn: str | None = "0001-26-000001", streak: int = 0) -> dict:
    return {
        "ticker": "POWL",
        "status": "IN_CAN",
        "falsification_lines": [
            {
                "id": "FL-1",
                "description": "连续 2 季度营收 YoY 低于 10%",
                "metric": "revenue_yoy_growth",
                "operator": "<",
                "threshold": 0.10,
                "consecutive_quarters": 2,
                "breach_streak": streak,
                "severity": "RED",
            },
            {
                "id": "FL-2",
                "description": "毛利率跌破 25%",
                "metric": "gross_margin",
                "operator": "<",
                "threshold": 0.25,
                "consecutive_quarters": 1,
                "breach_streak": 0,
                "severity": "RED",
            },
        ],
        "earnings_check_history": {
            "last_checked_accession": last_accn,
            "last_checked_period_end": "2026-06-30",
            "last_traffic_light": "GREEN",
            "checks": [
                {
                    "period_end": "2026-06-30",
                    "metrics_snapshot": {"revenue_yoy_growth": 0.28},
                }
            ],
        },
    }


def test_green_on_healthy_new_filing() -> None:
    rules = load_rules()
    pos = _sample_position()
    fundamentals = {
        "ttm_revenue_usd_m": 1350.0,
        "revenue_yoy_growth": 0.18,
        "gross_margin": 0.32,
        "gross_margin_yoy_change": 0.015,
        "ocf_usd_m": 420.0,
        "fcf_usd_m": 400.0,
        "cash_runway_months": None,
        "annual_share_dilution": 0.008,
        "filing_accession": "0001-26-000002",
        "latest_period_end": "2026-09-30",
    }
    check = evaluate_earnings_diff(
        pos,
        fundamentals,
        rules,
        filing_meta={"form": "10-K", "accession": "0001-26-000002", "report_date": "2026-09-30"},
    )
    assert check.is_new_filing is True
    assert check.traffic_light == TrafficLight.GREEN
    assert check.yellow_warnings == []
    assert check.updated_falsification_lines[0]["breach_streak"] == 0


def test_yellow_on_first_of_two_consecutive_breach_then_red_on_second() -> None:
    rules = load_rules()
    pos = _sample_position(streak=0)
    weak_rev = {
        "ttm_revenue_usd_m": 1300.0,
        "revenue_yoy_growth": 0.07,  # Violates FL-1 (< 10%), 1 of 2 quarters
        "gross_margin": 0.30,
        "gross_margin_yoy_change": -0.01,
        "annual_share_dilution": 0.01,
    }
    # Quarter 1: 1/2 streak -> YELLOW early warning (plus deceleration > 15pct!)
    c1 = evaluate_earnings_diff(
        pos,
        weak_rev,
        rules,
        filing_meta={"form": "10-Q", "accession": "0001-26-000002", "report_date": "2026-09-30"},
    )
    assert c1.traffic_light == TrafficLight.YELLOW
    assert c1.updated_falsification_lines[0]["breach_streak"] == 1
    assert any("revenue_growth_deceleration" in w for w in c1.yellow_warnings)

    # Quarter 2: 2/2 consecutive breach -> RED!
    pos["falsification_lines"] = c1.updated_falsification_lines
    pos["earnings_check_history"]["last_checked_accession"] = "0001-26-000002"
    c2 = evaluate_earnings_diff(
        pos,
        weak_rev,
        rules,
        filing_meta={"form": "10-Q", "accession": "0001-26-000003", "report_date": "2026-12-31"},
    )
    assert c2.traffic_light == TrafficLight.RED
    assert c2.updated_falsification_lines[0]["breach_streak"] == 2
    assert c2.line_results[0].breached is True


def test_yellow_on_gross_margin_drop_300bps_and_high_dilution() -> None:
    rules = load_rules()
    pos = _sample_position()
    deteriorating = {
        "ttm_revenue_usd_m": 1400.0,
        "revenue_yoy_growth": 0.20,
        "gross_margin": 0.27,  # Still above FL-2 25% red line
        "gross_margin_yoy_change": -0.035,  # -350 bps -> YELLOW
        "annual_share_dilution": 0.095,  # > 8% -> YELLOW
    }
    check = evaluate_earnings_diff(
        pos,
        deteriorating,
        rules,
        filing_meta={"form": "10-Q", "accession": "0001-26-000002", "report_date": "2026-09-30"},
    )
    assert check.traffic_light == TrafficLight.YELLOW
    assert any("gross_margin_drop_yoy" in w for w in check.yellow_warnings)
    assert any("annual_dilution_above" in w for w in check.yellow_warnings)


def test_same_accession_is_skipped_unless_forced() -> None:
    rules = load_rules()
    pos = _sample_position(last_accn="0001-26-000001")
    check = evaluate_earnings_diff(
        pos,
        {"filing_accession": "0001-26-000001", "gross_margin": 0.10},
        rules,
        filing_meta={"form": "10-Q", "accession": "0001-26-000001", "report_date": "2026-06-30"},
        force=False,
    )
    assert check.is_new_filing is False
    assert check.traffic_light == TrafficLight.GREEN
