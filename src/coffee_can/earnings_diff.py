"""Feedback Loop 2: Quarterly 10-Q / 10-K Earnings Diff & Falsification Engine.

Compares fresh SEC XBRL financial snapshots of IN_CAN positions against their
machine-readable falsification_lines and financial deterioration yellow triggers,
producing an auditable GREEN / YELLOW / RED traffic-light report.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from coffee_can.schemas import FalsificationLine, TrafficLight


@dataclass
class LineCheckResult:
    """Audit result for a single machine-readable falsification line."""

    id: str
    description: str
    metric: str
    operator: str
    threshold: float
    actual_value: float | None
    violated_this_quarter: bool
    breach_streak: int
    consecutive_quarters_required: int
    breached: bool
    severity: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EarningsHealthCheck:
    """Traffic-light health verdict for a Coffee Can position on a new 10-Q/10-K."""

    ticker: str
    filing_form: str | None
    filing_accession: str | None
    period_end: str | None
    is_new_filing: bool
    traffic_light: TrafficLight
    line_results: list[LineCheckResult] = field(default_factory=list)
    yellow_warnings: list[str] = field(default_factory=list)
    metrics_snapshot: dict[str, Any] = field(default_factory=dict)
    updated_falsification_lines: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["traffic_light"] = self.traffic_light.value
        return data


def _enrich_metrics(fundamentals: dict[str, Any]) -> dict[str, Any]:
    """Add convenient derived ratios (ocf_margin, fcf_margin) for falsification rules."""
    enriched = dict(fundamentals)
    rev = enriched.get("ttm_revenue_usd_m")
    ocf = enriched.get("ocf_usd_m")
    fcf = enriched.get("fcf_usd_m")
    if rev and rev > 0:
        if ocf is not None:
            enriched["ocf_margin"] = round(float(ocf) / float(rev), 4)
        if fcf is not None:
            enriched["fcf_margin"] = round(float(fcf) / float(rev), 4)
    return enriched


def evaluate_earnings_diff(
    position: dict[str, Any],
    fundamentals: dict[str, Any],
    rules: dict[str, Any],
    filing_meta: dict[str, Any] | None = None,
    force: bool = False,
) -> EarningsHealthCheck:
    """Evaluate a Coffee Can position against latest XBRL fundamentals & falsification lines."""
    ticker = str(position.get("ticker", "UNKNOWN")).upper()
    history = position.get("earnings_check_history") or {}
    last_checked_accn = history.get("last_checked_accession")

    accession = (filing_meta or {}).get("accession") or fundamentals.get("filing_accession")
    form = (filing_meta or {}).get("form")
    period_end = (filing_meta or {}).get("report_date") or fundamentals.get("latest_period_end")
    is_new_filing = bool(accession and accession != last_checked_accn)

    metrics = _enrich_metrics(fundamentals)
    raw_lines: list[dict[str, Any]] = list(position.get("falsification_lines") or [])

    if not is_new_filing and not force:
        prev_light_raw = history.get("last_traffic_light") or "GREEN"
        return EarningsHealthCheck(
            ticker=ticker,
            filing_form=form,
            filing_accession=accession,
            period_end=period_end,
            is_new_filing=False,
            traffic_light=TrafficLight(str(prev_light_raw).upper()),
            metrics_snapshot=metrics,
            updated_falsification_lines=raw_lines,
            summary=f"No new 10-Q/10-K since {last_checked_accn or 'initial check'}.",
        )

    tl_cfg = rules["earnings_diff"]["traffic_light"]
    red_on_any = bool(tl_cfg.get("red_on_any_falsification_breach", True))
    yellow_cfg = tl_cfg.get("yellow_triggers", {})

    # 1. Evaluate each machine-readable falsification line
    line_results: list[LineCheckResult] = []
    updated_lines: list[dict[str, Any]] = []
    has_red_breach = False
    has_yellow_line_warning = False

    for raw in raw_lines:
        prev_streak = int(raw.get("breach_streak") or 0)
        sev_str = str(raw.get("severity", "RED")).upper()
        fl = FalsificationLine(
            id=str(raw.get("id", "FL")),
            description=str(raw.get("description", "")),
            metric=str(raw["metric"]),
            operator=str(raw["operator"]),
            threshold=float(raw["threshold"]),
            consecutive_quarters=int(raw.get("consecutive_quarters", 1)),
            breach_streak=prev_streak,
            severity=TrafficLight(sev_str),
        )
        actual_raw = metrics.get(fl.metric)
        actual_val = float(actual_raw) if actual_raw is not None else None
        violated = fl.evaluate(actual_val)

        # Only advance consecutive-quarter streak when inspecting a genuinely new filing
        if is_new_filing:
            new_streak = (prev_streak + 1) if violated else 0
        else:
            new_streak = max(prev_streak, 1) if violated else 0

        breached = new_streak >= fl.consecutive_quarters
        if breached and fl.severity == TrafficLight.RED:
            has_red_breach = True
        elif breached and fl.severity == TrafficLight.YELLOW:
            has_yellow_line_warning = True
        elif violated and not breached:
            # 1 of N consecutive quarters violated -> early YELLOW warning
            has_yellow_line_warning = True

        line_results.append(
            LineCheckResult(
                id=fl.id,
                description=fl.description,
                metric=fl.metric,
                operator=fl.operator,
                threshold=fl.threshold,
                actual_value=actual_val,
                violated_this_quarter=violated,
                breach_streak=new_streak,
                consecutive_quarters_required=fl.consecutive_quarters,
                breached=breached,
                severity=fl.severity.value,
            )
        )
        updated_lines.append({**raw, "breach_streak": new_streak})

    # 2. Evaluate global financial deterioration YELLOW triggers
    yellow_warnings: list[str] = []

    gm_drop_bps = float(yellow_cfg.get("gross_margin_drop_yoy_bps", 300))
    gm_yoy = metrics.get("gross_margin_yoy_change")
    if gm_yoy is not None and float(gm_yoy) <= -(gm_drop_bps / 10000.0):
        yellow_warnings.append(
            f"gross_margin_drop_yoy ({float(gm_yoy) * 10000:.0f}bps <= -{gm_drop_bps:.0f}bps)"
        )

    decel_th = float(yellow_cfg.get("revenue_growth_deceleration_pct", 0.15))
    prev_checks = list(history.get("checks") or [])
    prev_rev_yoy = (
        prev_checks[-1].get("metrics_snapshot", {}).get("revenue_yoy_growth")
        if prev_checks
        else None
    )
    cur_rev_yoy = metrics.get("revenue_yoy_growth")
    if prev_rev_yoy is not None and cur_rev_yoy is not None:
        decel = float(prev_rev_yoy) - float(cur_rev_yoy)
        if decel >= decel_th:
            yellow_warnings.append(
                f"revenue_growth_deceleration (-{decel:.1%} from "
                f"{float(prev_rev_yoy):.1%} to {float(cur_rev_yoy):.1%})"
            )

    min_runway = float(yellow_cfg.get("cash_runway_below_months", 18))
    runway = metrics.get("cash_runway_months")
    if runway is not None and float(runway) < min_runway:
        yellow_warnings.append(f"cash_runway_below_months ({float(runway):.1f}m < {min_runway:g}m)")

    max_dil = float(yellow_cfg.get("annual_dilution_above", 0.08))
    dilution = metrics.get("annual_share_dilution")
    if dilution is not None and float(dilution) > max_dil:
        yellow_warnings.append(f"annual_dilution_above ({float(dilution):.1%} > {max_dil:.1%})")

    # 3. Traffic light verdict
    if has_red_breach and red_on_any:
        light = TrafficLight.RED
        breached_ids = [r.id for r in line_results if r.breached and r.severity == "RED"]
        summary = (
            f"RED ALERT: Falsification line(s) breached: {', '.join(breached_ids)}. "
            "Trigger immediate Coffee Can sell review!"
        )
    elif has_yellow_line_warning or yellow_warnings:
        light = TrafficLight.YELLOW
        reasons = [
            f"{r.id} ({r.breach_streak}/{r.consecutive_quarters_required})"
            for r in line_results
            if r.violated_this_quarter
        ] + yellow_warnings
        summary = f"YELLOW WARNING: {'; '.join(reasons)}"
    else:
        light = TrafficLight.GREEN
        summary = (
            f"GREEN: All {len(line_results)} falsification line(s) intact on "
            f"{form or 'filing'} ({period_end or 'latest'})."
        )

    return EarningsHealthCheck(
        ticker=ticker,
        filing_form=form,
        filing_accession=accession,
        period_end=period_end,
        is_new_filing=is_new_filing,
        traffic_light=light,
        line_results=line_results,
        yellow_warnings=yellow_warnings,
        metrics_snapshot=metrics,
        updated_falsification_lines=updated_lines,
        summary=summary,
    )
