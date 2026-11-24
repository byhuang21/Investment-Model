"""Feedback Loop 1: Near-Miss Watchlist Valuation Monitor Engine.

Implements hysteresis (consecutive trading days below target), alert cooldown,
extra-drop cooldown override, and same-day idempotency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass
class WatchlistEvaluation:
    """Result of checking a single NEAR_MISS watchlist item against live market cap."""

    ticker: str
    previous_status: str
    new_status: str
    current_market_cap_usd_m: float
    current_price_usd: float | None
    target_market_cap_usd_m: float
    gap_pct: float
    consecutive_days_below: int
    required_days: int
    should_alert: bool
    cooldown_suppressed: bool
    extra_drop_override: bool
    updated_monitor_state: dict[str, Any]
    summary: str


def _parse_date(val: str | None) -> date | None:
    if not val:
        return None
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def evaluate_watchlist_item(
    record: dict[str, Any],
    current_market_cap_usd_m: float,
    current_price_usd: float | None,
    rules: dict[str, Any],
    today_str: str,
) -> WatchlistEvaluation:
    """Evaluate a near_miss_pool YAML record against live market cap with hysteresis & cooldown."""
    ticker = str(record.get("ticker", "UNKNOWN")).upper()
    cfg = rules["watchlist_monitor"]
    required_days = int(cfg.get("consecutive_trading_days_below_target", 3))
    cooldown_days = int(cfg.get("alert_cooldown_days", 30))
    extra_drop_pct = float(cfg.get("extra_drop_override_pct", 0.15))
    approaching_pct = float(cfg.get("approaching_zone_pct", 0.10))

    target_mcap = float(record.get("targets", {}).get("target_market_cap_usd_m") or 0.0)
    if target_mcap <= 0:
        raise ValueError(f"{ticker}: invalid target_market_cap_usd_m ({target_mcap})")

    prev_state = dict(record.get("monitor_state") or {})
    prev_status = str(prev_state.get("status", "WATCHING")).upper()
    prev_streak = int(prev_state.get("consecutive_days_below_target") or 0)
    last_checked = prev_state.get("last_checked_at")
    last_alert_at = prev_state.get("last_alert_at")
    last_alert_mcap = prev_state.get("last_alert_market_cap_usd_m")

    gap_pct = (current_market_cap_usd_m - target_mcap) / target_mcap

    # Preserve terminal states (PROMOTED / EXPIRED)
    if prev_status in {"PROMOTED", "EXPIRED"}:
        return WatchlistEvaluation(
            ticker=ticker,
            previous_status=prev_status,
            new_status=prev_status,
            current_market_cap_usd_m=current_market_cap_usd_m,
            current_price_usd=current_price_usd,
            target_market_cap_usd_m=target_mcap,
            gap_pct=round(gap_pct, 4),
            consecutive_days_below=prev_streak,
            required_days=required_days,
            should_alert=False,
            cooldown_suppressed=False,
            extra_drop_override=False,
            updated_monitor_state=prev_state,
            summary=f"Skipped ({prev_status}).",
        )

    same_day_rerun = str(last_checked or "")[:10] == today_str[:10]
    should_alert = False
    cooldown_suppressed = False
    extra_drop_override = False

    if current_market_cap_usd_m <= target_mcap:
        new_streak = prev_streak if same_day_rerun else (prev_streak + 1)
        if new_streak >= required_days:
            new_status = "TRIGGERED"
            today_d = _parse_date(today_str)
            last_alert_d = _parse_date(last_alert_at)
            in_cooldown = (
                today_d is not None
                and last_alert_d is not None
                and (today_d - last_alert_d).days < cooldown_days
            )
            if in_cooldown:
                if (
                    last_alert_mcap is not None
                    and current_market_cap_usd_m <= float(last_alert_mcap) * (1.0 - extra_drop_pct)
                ):
                    should_alert = True
                    extra_drop_override = True
                    prev_alert_m = float(last_alert_mcap)
                    summary = (
                        f"EXTRA-DROP OVERRIDE: ${current_market_cap_usd_m:,.0f}M dropped "
                        f">= {extra_drop_pct:.0%} below prior alert (${prev_alert_m:,.0f}M)"
                    )
                else:
                    cooldown_suppressed = True
                    summary = (
                        f"Below target (${current_market_cap_usd_m:,.0f}M <= "
                        f"${target_mcap:,.0f}M), alert suppressed by {cooldown_days}d cooldown."
                    )
            else:
                should_alert = True
                summary = (
                    f"TRIGGERED: ${current_market_cap_usd_m:,.0f}M <= target ${target_mcap:,.0f}M "
                    f"for {new_streak}/{required_days} consecutive days!"
                )
        else:
            new_status = "APPROACHING"
            summary = (
                f"Below target (${current_market_cap_usd_m:,.0f}M <= ${target_mcap:,.0f}M), "
                f"hysteresis streak {new_streak}/{required_days}."
            )
    else:
        new_streak = 0
        if gap_pct <= approaching_pct:
            new_status = "APPROACHING"
            summary = (
                f"Approaching target (+{gap_pct:.1%} above ${target_mcap:,.0f}M; "
                f"current ${current_market_cap_usd_m:,.0f}M)."
            )
        else:
            new_status = "WATCHING"
            summary = (
                f"Watching (+{gap_pct:.1%} above target ${target_mcap:,.0f}M; "
                f"current ${current_market_cap_usd_m:,.0f}M)."
            )

    updated_state = {
        **prev_state,
        "status": new_status,
        "consecutive_days_below_target": new_streak,
        "last_checked_at": today_str,
        "last_market_cap_usd_m": round(current_market_cap_usd_m, 2),
        "last_price_usd": round(current_price_usd, 4) if current_price_usd is not None else None,
        "last_alert_at": today_str if should_alert else last_alert_at,
        "last_alert_market_cap_usd_m": (
            round(current_market_cap_usd_m, 2) if should_alert else last_alert_mcap
        ),
    }

    return WatchlistEvaluation(
        ticker=ticker,
        previous_status=prev_status,
        new_status=new_status,
        current_market_cap_usd_m=round(current_market_cap_usd_m, 2),
        current_price_usd=current_price_usd,
        target_market_cap_usd_m=target_mcap,
        gap_pct=round(gap_pct, 4),
        consecutive_days_below=new_streak,
        required_days=required_days,
        should_alert=should_alert,
        cooldown_suppressed=cooldown_suppressed,
        extra_drop_override=extra_drop_override,
        updated_monitor_state=updated_state,
        summary=summary,
    )
