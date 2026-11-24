"""Unit tests for Feedback Loop 3: Semi-Annual Post-Mortem calibration engine."""

from __future__ import annotations

from coffee_can.config import load_rules
from coffee_can.post_mortem import (
    DormantIndustryCheck,
    normalize_rule_key,
    render_post_mortem_markdown,
    run_post_mortem_analysis,
)


def test_normalize_rule_key_strips_parenthetical_values() -> None:
    assert (
        normalize_rule_key("common_floor.max_annual_share_dilution (> 6.0%, actual 10.7%)")
        == "common_floor.max_annual_share_dilution"
    )
    assert normalize_rule_key("excluded_sector:Utilities") == "excluded_sector:Utilities"


def test_post_mortem_triggers_calibration_proposal_when_false_negative_rate_high() -> None:
    rules = load_rules()
    # Create 10 mature rejected entries (200 days ago) under the same rule,
    # 3 of which beat SPY by +50% (30% false-negative rate >= 25% threshold, sample >= 10)
    entries = []
    prices = {}
    for i in range(10):
        sym = f"STK{i}"
        entries.append(
            {
                "ticker": sym,
                "rejected_at": "2026-04-01",
                "price_at_rejection_usd": 100.0,
                "spy_price_at_rejection": 500.0,
                "qqq_price_at_rejection": 400.0,
                "failed_rules": [
                    "common_floor.max_annual_share_dilution (> 6.0%, actual 7.2%)"
                ],
            }
        )
        # SPY +10% (500->550); first 3 stocks +60% (+50% alpha -> false negative)
        prices[sym] = 160.0 if i < 3 else 105.0

    report = run_post_mortem_analysis(
        rejected_entries=entries,
        current_prices=prices,
        current_spy=550.0,
        current_qqq=440.0,
        rules=rules,
        today_str="2026-11-24",
        dormant_checks=[
            DormantIndustryCheck(
                slug="industrial_smr_nuclear",
                name="SMR",
                candidates_checked=3,
                avg_180d_excess_vs_spy=0.42,
                breakout_alert=True,
                note="Breakout check",
            )
        ],
    )
    assert report.mature_sample_count == 10
    assert report.false_negative_count == 3
    assert report.overall_false_negative_rate == 0.3
    assert len(report.rule_attributions) == 1
    assert report.rule_attributions[0].needs_calibration is True
    assert len(report.calibration_proposals) == 1

    md = render_post_mortem_markdown(report)
    assert "common_floor.max_annual_share_dilution" in md
    assert "建议校准" in md
    assert "industrial_smr_nuclear" in md


def test_post_mortem_excludes_immature_entries_by_default() -> None:
    rules = load_rules()
    entries = [
        {
            "ticker": "RECENT",
            "rejected_at": "2026-11-23",
            "price_at_rejection_usd": 100.0,
            "spy_price_at_rejection": 680.0,
            "qqq_price_at_rejection": 590.0,
            "failed_rules": ["tracks.failed_both (rev_yoy=7.0%)"],
        }
    ]
    report = run_post_mortem_analysis(
        rejected_entries=entries,
        current_prices={"RECENT": 180.0},
        current_spy=690.0,
        current_qqq=600.0,
        rules=rules,
        today_str="2026-11-24",
        include_immature=False,
    )
    assert report.mature_sample_count == 0
    assert report.immature_sample_count == 1
    assert report.overall_false_negative_rate is None
    assert report.calibration_proposals == []
