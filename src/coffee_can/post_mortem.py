"""Feedback Loop 3: Semi-Annual Post-Mortem & Rule Calibration Proposal Engine.

Backtests memory/rejected_log.json entries and DORMANT industries against SPY/QQQ
benchmarks, attributes false negatives by canonical rule key, and produces human-reviewed
config/rules.yaml calibration proposals (NEVER auto-mutates config/rules.yaml).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass
class BacktestSample:
    """Performance evaluation of a single rejected_log entry vs SPY/QQQ."""

    ticker: str
    rejected_at: str
    days_elapsed: int
    mature: bool
    failed_rule_keys: list[str]
    price_at_rejection: float | None
    current_price: float | None
    stock_return: float | None
    spy_return: float | None
    qqq_return: float | None
    excess_vs_spy: float | None
    excess_vs_qqq: float | None
    is_false_negative: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuleAttribution:
    """False-negative statistics aggregated for one canonical screening rule."""

    rule_key: str
    sample_count: int
    false_negative_count: int
    false_negative_rate: float
    avg_excess_vs_spy: float | None
    missed_tickers: list[str] = field(default_factory=list)
    needs_calibration: bool = False
    proposal: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DormantIndustryCheck:
    """Breakout check on a DORMANT industry's tracked tickers."""

    slug: str
    name: str
    candidates_checked: int
    avg_180d_excess_vs_spy: float | None
    breakout_alert: bool
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PostMortemReport:
    """Complete semi-annual post-mortem calibration report."""

    as_of_date: str
    min_window_days: int
    total_rejected_entries: int
    mature_sample_count: int
    immature_sample_count: int
    false_negative_count: int
    overall_false_negative_rate: float | None
    samples: list[BacktestSample] = field(default_factory=list)
    rule_attributions: list[RuleAttribution] = field(default_factory=list)
    dormant_checks: list[DormantIndustryCheck] = field(default_factory=list)
    calibration_proposals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_rule_key(raw_rule: str) -> str:
    """Extract canonical rule identifier from a formatted failed_rules message."""
    text = str(raw_rule).strip()
    if " (" in text:
        return text.split(" (", 1)[0].strip()
    return text


def _parse_date(val: str | None) -> date | None:
    if not val:
        return None
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


RULE_PROPOSAL_HINTS: dict[str, str] = {
    "common_floor.max_annual_share_dilution": (
        "考虑在 config/rules.yaml 中将 max_annual_share_dilution 从 6% 微调至 7.5%，"
        "或针对高毛利(>60%)+高增速(>30%)软件股增加 1 年 SBC 过渡豁免。"
    ),
    "common_floor.max_goodwill_to_total_assets": (
        "考虑区分「商誉高但经营现金流持续改善的平台整合」与「商誉减值风险」，"
        "可测试将 max_goodwill_to_total_assets 放宽至 40% 并叠加 FCF>0 约束。"
    ),
    "tracks.failed_both": (
        "双轨门槛可能对高资本开支爬坡期公司过严，建议复核 A2 经营杠杆子轨的 "
        "min_ocf_margin (当前 8%) 或轨道 B 毛利率门槛 (当前 40%)。"
    ),
    "market_cap_above_max_near_miss": (
        "大市值龙头超额显著，若组合允许 3x–5x 稳健复利仓位，可评估将 max_near_miss 适度上调。"
    ),
}


def run_post_mortem_analysis(
    rejected_entries: list[dict[str, Any]],
    current_prices: dict[str, float | None],
    current_spy: float | None,
    current_qqq: float | None,
    rules: dict[str, Any],
    today_str: str,
    dormant_checks: list[DormantIndustryCheck] | None = None,
    include_immature: bool = False,
) -> PostMortemReport:
    """Evaluate rejected_log entries vs SPY/QQQ and generate rule calibration proposals."""
    pm_cfg = rules["post_mortem"]
    windows = sorted(int(w) for w in pm_cfg.get("lookback_windows_days", [180, 365]))
    min_window_days = windows[0] if windows else 180
    fn_alpha_th = float(pm_cfg.get("false_negative_excess_return_threshold", 0.30))
    trigger_fn_rate = float(pm_cfg.get("rule_review_trigger_false_negative_rate", 0.25))
    min_sample_size = int(pm_cfg.get("min_sample_size_for_calibration", 10))

    today_d = _parse_date(today_str)
    samples: list[BacktestSample] = []

    for entry in rejected_entries:
        ticker = str(entry.get("ticker", "")).upper()
        rej_date_str = str(entry.get("rejected_at", ""))
        rej_d = _parse_date(rej_date_str)
        days_elapsed = (today_d - rej_d).days if (today_d and rej_d) else 0
        mature = days_elapsed >= min_window_days

        p0 = entry.get("price_at_rejection_usd")
        p1 = current_prices.get(ticker)
        spy0 = entry.get("spy_price_at_rejection")
        qqq0 = entry.get("qqq_price_at_rejection")

        stock_ret = (
            ((float(p1) / float(p0)) - 1.0)
            if (p0 and p1 and float(p0) > 0)
            else None
        )
        spy_ret = (
            ((float(current_spy) / float(spy0)) - 1.0)
            if (spy0 and current_spy and float(spy0) > 0)
            else None
        )
        qqq_ret = (
            ((float(current_qqq) / float(qqq0)) - 1.0)
            if (qqq0 and current_qqq and float(qqq0) > 0)
            else None
        )

        excess_spy = (
            round(stock_ret - spy_ret, 4)
            if (stock_ret is not None and spy_ret is not None)
            else None
        )
        excess_qqq = (
            round(stock_ret - qqq_ret, 4)
            if (stock_ret is not None and qqq_ret is not None)
            else None
        )
        is_fn = bool(excess_spy is not None and excess_spy >= fn_alpha_th)

        rule_keys = sorted(
            {normalize_rule_key(r) for r in (entry.get("failed_rules") or []) if r}
        )
        samples.append(
            BacktestSample(
                ticker=ticker,
                rejected_at=rej_date_str,
                days_elapsed=days_elapsed,
                mature=mature,
                failed_rule_keys=rule_keys,
                price_at_rejection=float(p0) if p0 is not None else None,
                current_price=float(p1) if p1 is not None else None,
                stock_return=round(stock_ret, 4) if stock_ret is not None else None,
                spy_return=round(spy_ret, 4) if spy_ret is not None else None,
                qqq_return=round(qqq_ret, 4) if qqq_ret is not None else None,
                excess_vs_spy=excess_spy,
                excess_vs_qqq=excess_qqq,
                is_false_negative=is_fn,
            )
        )

    evaluated_pool = [
        s for s in samples if (s.mature or include_immature) and s.excess_vs_spy is not None
    ]
    mature_count = sum(1 for s in samples if s.mature)
    immature_count = len(samples) - mature_count
    fn_total = sum(1 for s in evaluated_pool if s.is_false_negative)
    overall_fn_rate = (
        round(fn_total / len(evaluated_pool), 4) if evaluated_pool else None
    )

    # Attribute false negatives by canonical rule key
    by_rule: dict[str, list[BacktestSample]] = {}
    for s in evaluated_pool:
        for key in s.failed_rule_keys:
            by_rule.setdefault(key, []).append(s)

    attributions: list[RuleAttribution] = []
    proposals: list[str] = []
    for key, rule_samples in sorted(by_rule.items(), key=lambda kv: -len(kv[1])):
        count = len(rule_samples)
        fns = [s for s in rule_samples if s.is_false_negative]
        fn_rate = round(len(fns) / count, 4) if count else 0.0
        excesses = [s.excess_vs_spy for s in rule_samples if s.excess_vs_spy is not None]
        avg_ex = round(sum(excesses) / len(excesses), 4) if excesses else None
        needs_cal = count >= min_sample_size and fn_rate >= trigger_fn_rate
        proposal = ""
        if needs_cal:
            hint = RULE_PROPOSAL_HINTS.get(
                key,
                f"规则 `{key}` 误杀率达 {fn_rate:.1%}（样本 {count}），建议人工复核阈值。",
            )
            proposal = hint
            proposals.append(f"- **`{key}`** (误杀 {len(fns)}/{count} = {fn_rate:.1%}): {hint}")

        attributions.append(
            RuleAttribution(
                rule_key=key,
                sample_count=count,
                false_negative_count=len(fns),
                false_negative_rate=fn_rate,
                avg_excess_vs_spy=avg_ex,
                missed_tickers=sorted({s.ticker for s in fns}),
                needs_calibration=needs_cal,
                proposal=proposal,
            )
        )

    return PostMortemReport(
        as_of_date=today_str,
        min_window_days=min_window_days,
        total_rejected_entries=len(rejected_entries),
        mature_sample_count=mature_count,
        immature_sample_count=immature_count,
        false_negative_count=fn_total,
        overall_false_negative_rate=overall_fn_rate,
        samples=samples,
        rule_attributions=attributions,
        dormant_checks=list(dormant_checks or []),
        calibration_proposals=proposals,
    )


def render_post_mortem_markdown(report: PostMortemReport) -> str:
    """Format a human-readable Markdown report for memory/reports/ & Notifier."""
    fn_rate_str = (
        f"{report.overall_false_negative_rate:.1%}"
        if report.overall_false_negative_rate is not None
        else "N/A（尚无满期样本）"
    )
    lines = [
        f"- **复盘日期**：`{report.as_of_date}`",
        f"- **淘汰样本总数**：`{report.total_rejected_entries}`"
        f"（满 {report.min_window_days} 天：`{report.mature_sample_count}`，"
        f"未满期：`{report.immature_sample_count}`）",
        f"- **潜在误杀率（相对 SPY 超额 >= +30%）**：`{fn_rate_str}`",
        "",
        "## 按淘汰规则归因",
    ]
    if report.rule_attributions:
        lines.append("| 规则键 | 样本数 | 误杀数 | 误杀率 | 平均超额 (vs SPY) | 状态 |")
        lines.append("|---|---:|---:|---:|---:|---|")
        for attr in report.rule_attributions:
            avg_s = (
                f"{attr.avg_excess_vs_spy:+.1%}" if attr.avg_excess_vs_spy is not None else "N/A"
            )
            verdict = "⚠️ 建议校准" if attr.needs_calibration else "保持严控"
            lines.append(
                f"| `{attr.rule_key}` | {attr.sample_count} | {attr.false_negative_count} "
                f"| {attr.false_negative_rate:.1%} | {avg_s} | {verdict} |"
            )
    else:
        lines.append("_暂无可统计的超额归因样本（等待淘汰记录满 180 天后自动触发正式校准）。_")

    if report.dormant_checks:
        lines.extend(["", "## 休眠行业 (DORMANT) 股价异动复查"])
        for dc in report.dormant_checks:
            ex_s = (
                f"{dc.avg_180d_excess_vs_spy:+.1%}"
                if dc.avg_180d_excess_vs_spy is not None
                else "N/A"
            )
            flag = "🔥 跑赢大盘预警" if dc.breakout_alert else "正常休眠"
            lines.append(
                f"- **{dc.slug}**（{dc.name}）：180 天均值超额 `{ex_s}` — {flag} {dc.note}"
            )

    lines.extend(["", "## `config/rules.yaml` 调参提案（严禁自动修改，须人类审批）"])
    if report.calibration_proposals:
        lines.extend(report.calibration_proposals)
    else:
        lines.append("- 当前无触发校准红线的规则，保持现有 `config/rules.yaml` 参数不变。")

    return "\n".join(lines)
