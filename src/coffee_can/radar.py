"""Layer 1 Industry Radar: state machine (DORMANT <-> ACTIVE) & signal aggregation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from coffee_can.schemas import IndustryStatus


@dataclass
class RadarMetrics:
    """Quantified signals collected for a single industry during a Layer 1 radar scan."""

    slug: str
    as_of_date: str
    leaders_aggregate_revenue_usd_m: float | None = None
    leaders_median_yoy_growth: float | None = None
    leaders_sampled: int = 0
    usaspending_12m_award_usd_m: float = 0.0
    usaspending_yoy_growth: float | None = None
    sec_cluster_filings_180d: int = 0
    sec_discovered_tickers: list[str] = field(default_factory=list)
    transcript_mentions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RadarEvaluation:
    """State-machine decision produced for an industry."""

    slug: str
    previous_status: IndustryStatus
    new_status: IndustryStatus
    changed: bool
    cooldown_blocked: bool
    triggered_signals: list[str] = field(default_factory=list)
    metrics: RadarMetrics | None = None
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_date(val: str | None) -> date | None:
    if not val:
        return None
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def scan_local_transcripts(transcripts_dir: Path, keywords: list[str]) -> int:
    """Count local manually dropped transcript files mentioning any industry keyword."""
    if not transcripts_dir.exists() or not keywords:
        return 0
    lowered = [k.strip().lower() for k in keywords if k and k.strip()]
    if not lowered:
        return 0
    matches = 0
    for path in sorted(transcripts_dir.iterdir()):
        if not path.is_file() or path.name.lower() == "readme.md":
            continue
        if path.suffix.lower() not in {".txt", ".md"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if any(kw in text for kw in lowered):
            matches += 1
    return matches


def merge_candidate_tickers(
    existing: list[str],
    discovered: list[str],
    bellwether_exclude: set[str] | None = None,
    max_total: int = 20,
) -> list[str]:
    """Merge curated seed tickers with newly discovered SEC EFTS tickers (preserving order)."""
    exclude = {t.upper() for t in (bellwether_exclude or set())}
    merged: list[str] = []
    seen: set[str] = set()
    for raw in existing:
        sym = str(raw).strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            merged.append(sym)
    for raw in discovered:
        sym = str(raw).strip().upper()
        if sym and sym not in seen and sym not in exclude:
            seen.add(sym)
            merged.append(sym)
            if len(merged) >= max_total:
                break
    return merged


def evaluate_industry_state(
    industry_record: dict[str, Any],
    metrics: RadarMetrics,
    rules: dict[str, Any],
    today_str: str,
) -> RadarEvaluation:
    """Evaluate DORMANT <-> ACTIVE state machine transitions with cooldown hysteresis."""
    sm_cfg = rules["layer1_industry_radar"]["state_machine"]
    global_th = sm_cfg["awaken_thresholds"]
    local_th = industry_record.get("awaken_conditions") or {}
    cooldown_days = int(sm_cfg.get("state_change_cooldown_days", 30))

    min_rev = float(
        local_th.get(
            "min_leaders_aggregate_revenue_usd_m",
            global_th["min_leaders_aggregate_revenue_usd_m"],
        )
    )
    min_growth = float(
        local_th.get(
            "min_sector_yoy_revenue_growth",
            global_th["min_sector_yoy_revenue_growth"],
        )
    )
    min_usa_m = float(
        local_th.get(
            "usaspending_12m_award_usd_m",
            global_th["usaspending_12m_award_usd_m"],
        )
    )
    min_usa_yoy = float(global_th.get("usaspending_yoy_growth", 0.40))
    min_sec_cluster = int(
        local_th.get(
            "min_sec_cluster_filings_180d",
            global_th["min_sec_cluster_filings_180d"],
        )
    )

    prev_status = IndustryStatus(str(industry_record.get("status", "DORMANT")).upper())
    if prev_status == IndustryStatus.ARCHIVED:
        return RadarEvaluation(
            slug=metrics.slug,
            previous_status=prev_status,
            new_status=IndustryStatus.ARCHIVED,
            changed=False,
            cooldown_blocked=False,
            metrics=metrics,
            rationale="Archived industry; skipped by automated awakening.",
        )

    triggered: list[str] = []
    rev_ok = (
        metrics.leaders_aggregate_revenue_usd_m is not None
        and metrics.leaders_aggregate_revenue_usd_m >= min_rev
    )
    growth_ok = (
        metrics.leaders_median_yoy_growth is not None
        and metrics.leaders_median_yoy_growth >= min_growth
    )
    usa_scale_ok = metrics.usaspending_12m_award_usd_m >= min_usa_m
    usa_surge_ok = (
        metrics.usaspending_12m_award_usd_m >= (min_usa_m * 0.4)
        and metrics.usaspending_yoy_growth is not None
        and metrics.usaspending_yoy_growth >= min_usa_yoy
    )
    sec_cluster_ok = metrics.sec_cluster_filings_180d >= min_sec_cluster

    if rev_ok:
        agg_rev_val = metrics.leaders_aggregate_revenue_usd_m
        triggered.append(f"leaders_agg_rev (${agg_rev_val:,.0f}M >= ${min_rev:,.0f}M)")
    if growth_ok:
        triggered.append(
            f"sector_yoy ({metrics.leaders_median_yoy_growth:.1%} >= {min_growth:.0%})"
        )
    if usa_scale_ok:
        triggered.append(
            f"usaspending_12m (${metrics.usaspending_12m_award_usd_m:,.0f}M >= ${min_usa_m:,.0f}M)"
        )
    elif usa_surge_ok and metrics.usaspending_yoy_growth is not None:
        triggered.append(
            f"usaspending_surge (+{metrics.usaspending_yoy_growth:.0%} YoY, "
            f"${metrics.usaspending_12m_award_usd_m:,.0f}M)"
        )
    if sec_cluster_ok:
        triggered.append(
            f"sec_cluster_180d ({metrics.sec_cluster_filings_180d} >= {min_sec_cluster})"
        )

    # Check cooldown before allowing any state change
    last_change_d = _parse_date(industry_record.get("last_state_change_at"))
    today_d = _parse_date(today_str)
    in_cooldown = (
        last_change_d is not None
        and today_d is not None
        and (today_d - last_change_d).days < cooldown_days
    )

    # Awakening criterion for DORMANT -> ACTIVE:
    # Either (scale + growth both met) OR (scale met + external cluster/contract signal)
    # OR (both federal spending surge AND SEC filing cluster confirm emerging breakout).
    should_awaken = (
        (rev_ok and growth_ok)
        or (rev_ok and (usa_scale_ok or sec_cluster_ok))
        or ((usa_scale_ok or usa_surge_ok) and sec_cluster_ok and (rev_ok or growth_ok))
    )

    if prev_status == IndustryStatus.DORMANT and should_awaken:
        if in_cooldown:
            return RadarEvaluation(
                slug=metrics.slug,
                previous_status=prev_status,
                new_status=prev_status,
                changed=False,
                cooldown_blocked=True,
                triggered_signals=triggered,
                metrics=metrics,
                rationale=(
                    f"Awaken thresholds met ({'; '.join(triggered)}), "
                    f"but blocked by {cooldown_days}d state_change_cooldown."
                ),
            )
        return RadarEvaluation(
            slug=metrics.slug,
            previous_status=prev_status,
            new_status=IndustryStatus.ACTIVE,
            changed=True,
            cooldown_blocked=False,
            triggered_signals=triggered,
            metrics=metrics,
            rationale=f"AWAKENED DORMANT -> ACTIVE via: {'; '.join(triggered)}",
        )

    return RadarEvaluation(
        slug=metrics.slug,
        previous_status=prev_status,
        new_status=prev_status,
        changed=False,
        cooldown_blocked=False,
        triggered_signals=triggered,
        metrics=metrics,
        rationale=(
            f"Remains {prev_status.value} ({len(triggered)} signal(s) active)."
            if triggered
            else f"Remains {prev_status.value} (below awakening thresholds)."
        ),
    )
