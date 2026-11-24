"""Unit tests for Feedback Loop 1: Near-Miss Watchlist hysteresis & cooldown engine."""

from __future__ import annotations

from coffee_can.config import load_rules
from coffee_can.monitor import evaluate_watchlist_item


def _base_record(**state_overrides: object) -> dict:
    monitor_state = {
        "status": "WATCHING",
        "consecutive_days_below_target": 0,
        "last_checked_at": None,
        "last_market_cap_usd_m": 20000.0,
        "last_price_usd": 250.0,
        "last_alert_at": None,
        "last_alert_market_cap_usd_m": None,
    }
    monitor_state.update(state_overrides)
    return {
        "ticker": "AXON",
        "targets": {"target_market_cap_usd_m": 15000.0},
        "monitor_state": monitor_state,
    }


def test_hysteresis_requires_3_consecutive_days_and_is_same_day_idempotent() -> None:
    rules = load_rules()
    rec = _base_record()

    # Day 1 below target -> streak 1/3, APPROACHING, no alert
    ev1 = evaluate_watchlist_item(rec, 14500.0, 180.0, rules, "2026-11-24")
    assert ev1.consecutive_days_below == 1
    assert ev1.new_status == "APPROACHING"
    assert ev1.should_alert is False

    # Same-day rerun MUST NOT increment streak again
    rec["monitor_state"] = ev1.updated_monitor_state
    ev1_dup = evaluate_watchlist_item(rec, 14400.0, 179.0, rules, "2026-11-24")
    assert ev1_dup.consecutive_days_below == 1
    assert ev1_dup.should_alert is False

    # Day 2 below target -> streak 2/3
    rec["monitor_state"] = ev1_dup.updated_monitor_state
    ev2 = evaluate_watchlist_item(rec, 14200.0, 177.0, rules, "2026-11-25")
    assert ev2.consecutive_days_below == 2
    assert ev2.should_alert is False

    # Day 3 below target -> TRIGGERED + alert!
    rec["monitor_state"] = ev2.updated_monitor_state
    ev3 = evaluate_watchlist_item(rec, 14000.0, 175.0, rules, "2026-11-26")
    assert ev3.consecutive_days_below == 3
    assert ev3.new_status == "TRIGGERED"
    assert ev3.should_alert is True
    assert ev3.updated_monitor_state["last_alert_at"] == "2026-11-26"
    assert ev3.updated_monitor_state["last_alert_market_cap_usd_m"] == 14000.0


def test_cooldown_suppresses_repeat_alert_until_extra_drop_override() -> None:
    rules = load_rules()
    rec = _base_record(
        status="TRIGGERED",
        consecutive_days_below_target=3,
        last_checked_at="2026-11-26",
        last_alert_at="2026-11-26",
        last_alert_market_cap_usd_m=14000.0,
    )

    # 5 days later, minor further dip (-5%) -> suppressed by 30d cooldown
    ev_cool = evaluate_watchlist_item(rec, 13300.0, 166.0, rules, "2026-12-01")
    assert ev_cool.should_alert is False
    assert ev_cool.cooldown_suppressed is True

    # 7 days later, severe extra crash (-18% below $14,000M = $11,480M) -> overrides cooldown!
    rec["monitor_state"] = ev_cool.updated_monitor_state
    ev_crash = evaluate_watchlist_item(rec, 11480.0, 143.0, rules, "2026-12-03")
    assert ev_crash.should_alert is True
    assert ev_crash.extra_drop_override is True


def test_bounce_above_target_resets_streak_and_detects_approaching_zone() -> None:
    rules = load_rules()
    rec = _base_record(consecutive_days_below_target=2, last_checked_at="2026-11-24")

    # +5% above $15,000M ($15,750M) -> within 10% approaching zone, streak resets to 0
    ev = evaluate_watchlist_item(rec, 15750.0, 195.0, rules, "2026-11-25")
    assert ev.consecutive_days_below == 0
    assert ev.new_status == "APPROACHING"
    assert ev.should_alert is False

    # +25% above target -> WATCHING
    ev2 = evaluate_watchlist_item(rec, 18750.0, 230.0, rules, "2026-11-26")
    assert ev2.new_status == "WATCHING"
