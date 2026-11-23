"""Layer 2 dual-track quant hard screening engine (PASS / NEAR_MISS / REJECTED)."""

from __future__ import annotations

from typing import Any

from coffee_can.schemas import FinancialSnapshot, ScreenDecision, ScreenOutcome


def evaluate_snapshot(
    snapshot: FinancialSnapshot,
    rules: dict[str, Any],
    sector: str | None = None,
) -> ScreenDecision:
    """Evaluate a normalized FinancialSnapshot against config/rules.yaml Layer 2 gates.

    All violated floor/track rules are collected (without early exit) so rejected_log.json
    retains complete attribution for semi-annual Post-Mortem calibration.
    """
    excluded_sectors = set(rules.get("universe", {}).get("exclude_sectors", []))
    l2 = rules["layer2_quant_screener"]
    mcap_cfg = l2["market_cap_usd_m"]
    floor_cfg = l2["common_floor"]
    tracks_cfg = l2["tracks"]
    val_cfg = l2["valuation_gate"]

    failed_rules: list[str] = []

    # 1. Sector exclusion
    if sector and sector in excluded_sectors:
        failed_rules.append(f"excluded_sector:{sector}")

    # 2. Common financial floor
    min_rev = float(floor_cfg["min_ttm_revenue_usd_m"])
    if snapshot.ttm_revenue_usd_m is None:
        failed_rules.append("missing_ttm_revenue")
    elif snapshot.ttm_revenue_usd_m < min_rev:
        failed_rules.append(
            f"common_floor.min_ttm_revenue (< ${min_rev:g}M, actual ${snapshot.ttm_revenue_usd_m}M)"
        )

    min_gm = float(floor_cfg["min_gross_margin"])
    allow_improving_gm = bool(floor_cfg.get("allow_improving_gross_margin", True))
    min_gm_imp = float(floor_cfg.get("min_gross_margin_improvement_yoy", 0.05))
    if snapshot.gross_margin is None:
        failed_rules.append("missing_gross_margin")
    elif snapshot.gross_margin < min_gm:
        improving_exempt = (
            allow_improving_gm
            and snapshot.gross_margin > 0
            and snapshot.gross_margin_yoy_change is not None
            and snapshot.gross_margin_yoy_change >= min_gm_imp
        )
        if not improving_exempt:
            failed_rules.append(
                "common_floor.min_gross_margin "
                f"(< {min_gm:.0%}, actual {snapshot.gross_margin:.1%})"
            )

    min_runway = float(floor_cfg["min_cash_runway_months"])
    burning_cash = snapshot.fcf_usd_m is not None and snapshot.fcf_usd_m < 0
    if burning_cash:
        if snapshot.cash_runway_months is None:
            failed_rules.append("missing_cash_runway")
        elif snapshot.cash_runway_months < min_runway:
            failed_rules.append(
                "common_floor.min_cash_runway_months "
                f"(< {min_runway:g}m, actual {snapshot.cash_runway_months:.1f}m)"
            )

    max_st_debt_cash = float(floor_cfg["max_short_term_debt_to_cash"])
    if (
        snapshot.short_term_debt_to_cash is not None
        and snapshot.short_term_debt_to_cash > max_st_debt_cash
    ):
        failed_rules.append(
            "common_floor.max_short_term_debt_to_cash "
            f"(> {max_st_debt_cash:.2f}x, actual {snapshot.short_term_debt_to_cash:.2f}x)"
        )

    max_goodwill = float(floor_cfg["max_goodwill_to_total_assets"])
    if (
        snapshot.goodwill_to_total_assets is not None
        and snapshot.goodwill_to_total_assets > max_goodwill
    ):
        failed_rules.append(
            "common_floor.max_goodwill_to_total_assets "
            f"(> {max_goodwill:.0%}, actual {snapshot.goodwill_to_total_assets:.1%})"
        )

    max_dilution = float(floor_cfg["max_annual_share_dilution"])
    if (
        snapshot.annual_share_dilution is not None
        and snapshot.annual_share_dilution > max_dilution
    ):
        failed_rules.append(
            "common_floor.max_annual_share_dilution "
            f"(> {max_dilution:.1%}, actual {snapshot.annual_share_dilution:.1%})"
        )

    # 3. Dual-track qualification (Track A1/A2: Profitable Compounder | Track B: High-Growth)
    track_a = tracks_cfg["profitable_compounder"]
    track_b = tracks_cfg["high_growth_reinvestment"]

    has_pos_cf = (snapshot.ocf_usd_m is not None and snapshot.ocf_usd_m > 0) or (
        snapshot.fcf_usd_m is not None and snapshot.fcf_usd_m > 0
    )
    track_a1_ok = (
        (has_pos_cf or not track_a.get("require_positive_ocf_or_fcf", True))
        and snapshot.revenue_yoy_growth is not None
        and snapshot.revenue_yoy_growth >= float(track_a["min_revenue_yoy_growth"])
        and snapshot.gross_margin is not None
        and snapshot.gross_margin >= float(track_a["min_gross_margin"])
    )

    op_lev_cfg = track_a.get("operating_leverage_subtrack", {})
    ocf_margin: float | None = None
    if (
        snapshot.ocf_usd_m is not None
        and snapshot.ttm_revenue_usd_m is not None
        and snapshot.ttm_revenue_usd_m > 0
    ):
        ocf_margin = snapshot.ocf_usd_m / snapshot.ttm_revenue_usd_m

    track_a2_ok = (
        bool(op_lev_cfg.get("enabled", False))
        and snapshot.ocf_usd_m is not None
        and snapshot.ocf_usd_m > 0
        and snapshot.gross_margin is not None
        and snapshot.gross_margin >= float(op_lev_cfg.get("min_gross_margin", 0.20))
        and snapshot.revenue_yoy_growth is not None
        and snapshot.revenue_yoy_growth >= float(op_lev_cfg.get("min_revenue_yoy_growth", 0.20))
        and ocf_margin is not None
        and ocf_margin >= float(op_lev_cfg.get("min_ocf_margin", 0.08))
    )

    track_b_runway_ok = (not burning_cash) or (
        snapshot.cash_runway_months is not None
        and snapshot.cash_runway_months >= float(track_b["min_cash_runway_months"])
    )
    track_b_ok = (
        snapshot.revenue_yoy_growth is not None
        and snapshot.revenue_yoy_growth >= float(track_b["min_revenue_yoy_growth"])
        and snapshot.gross_margin is not None
        and snapshot.gross_margin >= float(track_b["min_gross_margin"])
        and track_b_runway_ok
    )

    track_qualified: str | None = None
    if track_a1_ok:
        track_qualified = "profitable_compounder"
    elif track_a2_ok:
        track_qualified = "operating_leverage_compounder"
    elif track_b_ok:
        track_qualified = "high_growth_reinvestment"
    else:
        rev_yoy_str = (
            f"{snapshot.revenue_yoy_growth:.1%}"
            if snapshot.revenue_yoy_growth is not None
            else "N/A"
        )
        gm_str = f"{snapshot.gross_margin:.1%}" if snapshot.gross_margin is not None else "N/A"
        ocf_m_str = f"{ocf_margin:.1%}" if ocf_margin is not None else "N/A"
        failed_rules.append(
            f"tracks.failed_both (rev_yoy={rev_yoy_str}, gm={gm_str}, ocf_margin={ocf_m_str})"
        )

    # Reject immediately if any fundamental / financial floor check failed
    if failed_rules:
        return ScreenDecision(
            ticker=snapshot.ticker,
            outcome=ScreenOutcome.REJECTED,
            track_qualified=track_qualified,
            failed_rules=failed_rules,
            notes="Failed fundamental or financial floor hard filters.",
            snapshot=snapshot,
        )

    # 4. Market cap & valuation gate (splits PASS vs NEAR_MISS vs REJECTED)
    min_pass = float(mcap_cfg["min_pass"])
    max_pass = float(mcap_cfg["max_pass"])
    max_near_miss = float(mcap_cfg["max_near_miss"])
    max_ev_sales = float(val_cfg["max_ev_to_sales_pass"])

    mcap = snapshot.market_cap_usd_m
    if mcap is None:
        return ScreenDecision(
            ticker=snapshot.ticker,
            outcome=ScreenOutcome.REJECTED,
            track_qualified=track_qualified,
            failed_rules=["missing_market_cap"],
            notes="Unable to determine current market cap.",
            snapshot=snapshot,
        )

    if mcap < min_pass:
        return ScreenDecision(
            ticker=snapshot.ticker,
            outcome=ScreenOutcome.REJECTED,
            track_qualified=track_qualified,
            failed_rules=[f"market_cap_below_min_pass (< ${min_pass:g}M, actual ${mcap}M)"],
            notes="Below minimum liquidity/size floor.",
            snapshot=snapshot,
        )

    if mcap > max_near_miss:
        return ScreenDecision(
            ticker=snapshot.ticker,
            outcome=ScreenOutcome.REJECTED,
            track_qualified=track_qualified,
            failed_rules=[
                f"market_cap_above_max_near_miss (> ${max_near_miss:g}M, actual ${mcap}M)"
            ],
            notes="Too large for 10x Coffee Can odds universe.",
            snapshot=snapshot,
        )

    if mcap > max_pass:
        return ScreenDecision(
            ticker=snapshot.ticker,
            outcome=ScreenOutcome.NEAR_MISS,
            track_qualified=track_qualified,
            failed_rules=["MARKET_CAP_ABOVE_PASS_RANGE"],
            notes=(
                f"Fundamentals passed ({track_qualified}), but market cap ${mcap:,.0f}M > "
                f"${max_pass:,.0f}M pass ceiling. Routed to near_miss_pool."
            ),
            snapshot=snapshot,
        )

    if snapshot.ev_to_sales is not None and snapshot.ev_to_sales > max_ev_sales:
        return ScreenDecision(
            ticker=snapshot.ticker,
            outcome=ScreenOutcome.NEAR_MISS,
            track_qualified=track_qualified,
            failed_rules=["EV_TO_SALES_TOO_HIGH"],
            notes=(
                f"Fundamentals passed ({track_qualified}), but EV/Sales "
                f"{snapshot.ev_to_sales:.1f}x > {max_ev_sales:.1f}x gate. "
                "Routed to near_miss_pool."
            ),
            snapshot=snapshot,
        )

    return ScreenDecision(
        ticker=snapshot.ticker,
        outcome=ScreenOutcome.PASS,
        track_qualified=track_qualified,
        failed_rules=[],
        notes=f"All hard filters passed via {track_qualified}. Ready for Layer 3 deep dive.",
        snapshot=snapshot,
    )


def compute_near_miss_target_mcap(
    snapshot: FinancialSnapshot,
    rules: dict[str, Any],
    reason: str,
) -> float | None:
    """Calculate initial target market cap trigger for a NEAR_MISS watchlist entry."""
    if snapshot.market_cap_usd_m is None:
        return None
    l2 = rules["layer2_quant_screener"]
    max_pass = float(l2["market_cap_usd_m"]["max_pass"])
    discount_pct = float(l2["valuation_gate"].get("near_miss_discount_target_pct", 0.25))
    discounted = snapshot.market_cap_usd_m * (1.0 - discount_pct)
    if reason == "MARKET_CAP_ABOVE_PASS_RANGE":
        return round(min(max_pass, discounted), 2)
    return round(discounted, 2)
