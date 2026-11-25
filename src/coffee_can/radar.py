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


@dataclass
class DiscoveredThemeSignal:
    """Candidate emerging industry surfaced by the Layer 1 Blind Discovery Engine."""

    slug: str
    name: str
    source: str  # "USASPENDING_NAICS" | "SEC_S1_CLUSTER" | "CROSS_SECTOR_PROBE"
    headline_metric: str
    rationale: str
    discovered_at: str
    last_seen_at: str
    status: str = "NEW"  # "NEW" | "PROMOTED" | "DISMISSED"
    metrics: dict[str, Any] = field(default_factory=dict)
    sec_keywords: list[str] = field(default_factory=list)
    usaspending_keywords: list[str] = field(default_factory=list)
    naics_codes: list[str] = field(default_factory=list)
    sic_codes: list[str] = field(default_factory=list)
    discovered_tickers: list[str] = field(default_factory=list)
    profit_pool_description: str = ""
    demand_innovation_hypothesis: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def slugify_theme(raw: str, prefix: str = "") -> str:
    """Convert an industry title or code into a safe lowercase snake_case slug."""
    cleaned = []
    prev_us = False
    for ch in raw.lower():
        if ch.isalnum():
            cleaned.append(ch)
            prev_us = False
        elif not prev_us:
            cleaned.append("_")
            prev_us = True
    body = "".join(cleaned).strip("_")[:48]
    if prefix:
        return f"{prefix}_{body}" if body else prefix
    return body or "discovered_theme"


def merge_discovered_signals(
    existing_items: list[dict[str, Any]],
    fresh_items: list[DiscoveredThemeSignal],
    tracked_slugs: set[str],
    tracked_naics: set[str],
    tracked_sics: set[str],
    today_str: str,
) -> list[dict[str, Any]]:
    """Idempotently merge fresh blind-discovery signals with prior quarantine history.

    Preserves human `DISMISSED` and `PROMOTED` decisions so ignored themes do not re-appear
    as `NEW` on subsequent weekly scans, and automatically marks already-tracked industries.
    """
    existing_by_slug: dict[str, dict[str, Any]] = {}
    for row in existing_items:
        if isinstance(row, dict) and row.get("slug"):
            existing_by_slug[str(row["slug"])] = dict(row)

    merged: dict[str, dict[str, Any]] = {}

    # Carry forward previously acted-upon items (DISMISSED / PROMOTED)
    for slug, prev in existing_by_slug.items():
        prev_status = str(prev.get("status", "NEW")).upper()
        if slug in tracked_slugs:
            prev["status"] = "PROMOTED"
            merged[slug] = prev
        elif prev_status in {"DISMISSED", "PROMOTED"}:
            merged[slug] = prev

    # Apply fresh discoveries
    for sig in fresh_items:
        slug = sig.slug
        already_tracked = (
            slug in tracked_slugs
            or bool(set(sig.naics_codes) & tracked_naics)
            or bool(set(sig.sic_codes) & tracked_sics)
        )
        prev = existing_by_slug.get(slug)
        prev_status = str(prev.get("status", "NEW")).upper() if prev else "NEW"

        entry = sig.to_dict()
        if prev and prev.get("discovered_at"):
            entry["discovered_at"] = prev["discovered_at"]
        entry["last_seen_at"] = today_str

        if already_tracked:
            entry["status"] = "PROMOTED"
        elif prev_status in {"DISMISSED", "PROMOTED"}:
            entry["status"] = prev_status
        else:
            entry["status"] = "NEW"

        merged[slug] = entry

    status_rank = {"NEW": 0, "PROMOTED": 1, "DISMISSED": 2}
    source_rank = {
        "USASPENDING_NAICS": 0,
        "SEC_S1_CLUSTER": 1,
        "CROSS_SECTOR_PROBE": 2,
    }
    return sorted(
        merged.values(),
        key=lambda r: (
            status_rank.get(str(r.get("status", "NEW")).upper(), 9),
            source_rank.get(str(r.get("source", "")).upper(), 9),
            str(r.get("slug", "")),
        ),
    )


def build_industry_record_from_signal(
    signal: dict[str, Any],
    initial_status: IndustryStatus,
    today_str: str,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """Construct a schema-valid memory/industries/<slug>.yaml record from a quarantine signal."""
    slug = str(signal["slug"])
    name = str(signal.get("name") or slug)
    awaken_defaults = rules["layer1_industry_radar"]["state_machine"]["awaken_thresholds"]
    tickers = [str(t).upper() for t in (signal.get("discovered_tickers") or []) if str(t).strip()]
    profit_pool = str(signal.get("profit_pool_description") or "").strip() or (
        f"Discovered via {signal.get('source', 'RADAR')}: {signal.get('rationale', '')}"
    )
    demand_hyp = str(signal.get("demand_innovation_hypothesis") or "").strip() or (
        f"Quantified signal ({signal.get('headline_metric', 'N/A')}) flagged by Layer 1 radar."
    )
    return {
        "slug": slug,
        "name": name,
        "status": initial_status.value,
        "created_at": today_str,
        "last_scanned_at": today_str,
        "last_state_change_at": today_str,
        "thesis_snapshot": {
            "profit_pool_description": profit_pool,
            "demand_innovation_hypothesis": demand_hyp,
            "winner_take_all_dynamics": "Pending Layer 1/3 human thesis refinement.",
        },
        "signals": {
            "sec_keywords": list(signal.get("sec_keywords") or []),
            "sic_codes": [str(c) for c in (signal.get("sic_codes") or [])],
            "naics_codes": [str(c) for c in (signal.get("naics_codes") or [])],
            "usaspending_keywords": list(signal.get("usaspending_keywords") or []),
            "bellwether_mentions": [],
        },
        "awaken_conditions": {
            "min_leaders_aggregate_revenue_usd_m": awaken_defaults[
                "min_leaders_aggregate_revenue_usd_m"
            ],
            "consecutive_high_growth_quarters": awaken_defaults[
                "consecutive_high_growth_quarters"
            ],
            "min_sector_yoy_revenue_growth": awaken_defaults["min_sector_yoy_revenue_growth"],
            "usaspending_12m_award_usd_m": awaken_defaults["usaspending_12m_award_usd_m"],
            "min_sec_cluster_filings_180d": awaken_defaults["min_sec_cluster_filings_180d"],
        },
        "leader_tickers": tickers[:5],
        "candidate_tickers": tickers[:20],
        "history": [
            {
                "date": today_str,
                "event": "PROMOTED_FROM_QUARANTINE",
                "from_status": None,
                "to_status": initial_status.value,
                "evidence": (
                    f"Human promoted blind-discovery signal ({signal.get('source')}: "
                    f"{signal.get('headline_metric')})"
                ),
            }
        ],
    }

