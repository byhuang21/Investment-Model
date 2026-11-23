"""Core domain schemas and enums shared across all Coffee Can pipeline scripts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class IndustryStatus(StrEnum):
    DORMANT = "DORMANT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ScreenOutcome(StrEnum):
    PASS = "PASS"
    NEAR_MISS = "NEAR_MISS"
    REJECTED = "REJECTED"


class ResearchStatus(StrEnum):
    DRAFTING_EVIDENCE = "DRAFTING_EVIDENCE"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    APPROVED_FOR_CAN = "APPROVED_FOR_CAN"
    MOVED_TO_NEAR_MISS = "MOVED_TO_NEAR_MISS"
    REJECTED = "REJECTED"


class TrafficLight(StrEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


@dataclass
class FinancialSnapshot:
    """Normalized fundamental & market snapshot for Layer 2 screening and logging."""

    ticker: str
    as_of_date: str
    cik: str | None = None
    company_name: str | None = None
    price_usd: float | None = None
    market_cap_usd_m: float | None = None
    ev_to_sales: float | None = None
    ttm_revenue_usd_m: float | None = None
    revenue_yoy_growth: float | None = None
    gross_margin: float | None = None
    gross_margin_yoy_change: float | None = None
    ocf_usd_m: float | None = None
    fcf_usd_m: float | None = None
    cash_and_st_inv_usd_m: float | None = None
    cash_runway_months: float | None = None
    short_term_debt_to_cash: float | None = None
    goodwill_to_total_assets: float | None = None
    annual_share_dilution: float | None = None
    filing_accession: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScreenDecision:
    """Result of Layer 2 dual-track quant screening."""

    ticker: str
    outcome: ScreenOutcome
    track_qualified: str | None = None
    failed_rules: list[str] = field(default_factory=list)
    notes: str = ""
    snapshot: FinancialSnapshot | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RejectedEntry:
    """Append-only rejection record stored in memory/rejected_log.json."""

    ticker: str
    rejected_at: str
    stage: str  # "LAYER2_QUANT" | "LAYER3_HUMAN"
    industry_slug: str | None
    failed_rules: list[str]
    price_at_rejection_usd: float | None
    market_cap_at_rejection_usd_m: float | None
    spy_price_at_rejection: float | None
    qqq_price_at_rejection: float | None
    metrics_snapshot: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FalsificationLine:
    """Machine-readable thesis falsification rule checked by 4_earnings_diff.py."""

    id: str
    description: str
    metric: str
    operator: str  # "<" | "<=" | ">" | ">="
    threshold: float
    consecutive_quarters: int = 1
    breach_streak: int = 0
    severity: TrafficLight = TrafficLight.RED

    def evaluate(self, value: float | None) -> bool:
        """Return True if the current metric value violates this falsification line."""
        if value is None:
            return False
        ops = {
            "<": value < self.threshold,
            "<=": value <= self.threshold,
            ">": value > self.threshold,
            ">=": value >= self.threshold,
        }
        if self.operator not in ops:
            raise ValueError(f"Unsupported falsification operator: {self.operator}")
        return ops[self.operator]
