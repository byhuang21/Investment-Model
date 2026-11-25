"""Layer 3 Objective Evidence Engine (SEC Form 4 XML parser + 3-year capital allocation).

Strictly adheres to AGENT_RULES.md:
- Collects 100% objective, traceable evidence from SEC EDGAR (Form 4 XML, 10-K XBRL,
  and recent 10-K/10-Q/8-K filing index).
- NEVER writes `human_subjective_score`, NEVER draws subjective conclusions about
  management character, and NEVER promotes directly to `APPROVED_FOR_CAN`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Any

from coffee_can.clients.sec_edgar import (
    ANNUAL_MAX_DAYS,
    ANNUAL_MIN_DAYS,
    _collect_facts,
    _duration_days,
)


@dataclass
class Form4Transaction:
    """Single non-derivative transaction parsed from an SEC Form 4 XML document."""

    owner_name: str
    owner_role: str
    is_officer: bool
    is_director: bool
    is_ten_percent_owner: bool
    transaction_date: str
    transaction_code: str  # P=Buy, S=Sell, A=Award, M=Exercise, F=Tax, G=Gift
    acquired_disposed: str  # A | D
    shares: float
    price_per_share: float | None
    total_value_usd: float
    is_10b5_1: bool
    shares_owned_after: float | None
    filing_date: str
    accession: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Form4Summary:
    """Aggregated 24-month insider open-market activity separating 10b5-1 & routine grants."""

    filings_parsed: int
    total_transactions: int
    open_market_buy_count: int
    open_market_buy_usd: float
    unique_buyers: list[str]
    discretionary_sell_count: int
    discretionary_sell_usd: float
    plan_10b5_1_sell_count: int
    plan_10b5_1_sell_usd: float
    unique_sellers: list[str]
    routine_grant_tax_count: int
    net_open_market_usd: float
    notable_transactions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _xml_text(parent: ET.Element | None, path: str) -> str:
    if parent is None:
        return ""
    el = parent.find(path)
    return (el.text or "").strip() if el is not None else ""


def _xml_bool(parent: ET.Element | None, path: str) -> bool:
    return _xml_text(parent, path).lower() in {"1", "true", "yes"}


def _xml_float(parent: ET.Element | None, path: str) -> float | None:
    raw = _xml_text(parent, path)
    if not raw:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def parse_form4_xml(
    xml_text: str,
    accession: str = "",
    filing_date: str = "",
) -> list[Form4Transaction]:
    """Parse SEC Form 4 XML into normalized non-derivative insider transactions."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    owner_el = root.find("reportingOwner")
    owner_name = _xml_text(owner_el, "reportingOwnerId/rptOwnerName") or "Unknown Insider"
    rel_el = owner_el.find("reportingOwnerRelationship") if owner_el is not None else None
    is_director = _xml_bool(rel_el, "isDirector")
    is_officer = _xml_bool(rel_el, "isOfficer")
    is_ten_pct = _xml_bool(rel_el, "isTenPercentOwner")
    officer_title = _xml_text(rel_el, "officerTitle")

    role_parts: list[str] = []
    if officer_title:
        role_parts.append(officer_title)
    elif is_officer:
        role_parts.append("Officer")
    if is_director:
        role_parts.append("Director")
    if is_ten_pct:
        role_parts.append("10%+ Owner")
    owner_role = " / ".join(role_parts) or "Insider"

    is_10b5_1 = _xml_bool(root, "aff10b5One")

    txns: list[Form4Transaction] = []
    for tx_el in root.findall(".//nonDerivativeTable/nonDerivativeTransaction"):
        tx_date = _xml_text(tx_el, "transactionDate/value")
        tx_code = _xml_text(tx_el, "transactionCoding/transactionCode").upper()
        shares = _xml_float(tx_el, "transactionAmounts/transactionShares/value") or 0.0
        price = _xml_float(tx_el, "transactionAmounts/transactionPricePerShare/value")
        acq_disp = _xml_text(
            tx_el, "transactionAmounts/transactionAcquiredDisposedCode/value"
        ).upper()
        owned_after = _xml_float(
            tx_el, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"
        )
        total_val = round(shares * price, 2) if (price is not None and shares > 0) else 0.0

        txns.append(
            Form4Transaction(
                owner_name=owner_name,
                owner_role=owner_role,
                is_officer=is_officer,
                is_director=is_director,
                is_ten_percent_owner=is_ten_pct,
                transaction_date=tx_date,
                transaction_code=tx_code,
                acquired_disposed=acq_disp,
                shares=round(shares, 2),
                price_per_share=round(price, 4) if price is not None else None,
                total_value_usd=total_val,
                is_10b5_1=is_10b5_1,
                shares_owned_after=round(owned_after, 2) if owned_after is not None else None,
                filing_date=filing_date,
                accession=accession,
            )
        )
    return txns


def summarize_form4_activity(
    transactions: list[Form4Transaction],
    filings_parsed: int = 0,
    top_n: int = 10,
) -> Form4Summary:
    """Summarize open-market buys (P), discretionary vs 10b5-1 sales (S), and routine grants."""
    buys = [t for t in transactions if t.transaction_code == "P"]
    sells_disc = [t for t in transactions if t.transaction_code == "S" and not t.is_10b5_1]
    sells_plan = [t for t in transactions if t.transaction_code == "S" and t.is_10b5_1]
    routine = [t for t in transactions if t.transaction_code in {"A", "M", "F", "G"}]

    buy_usd = round(sum(t.total_value_usd for t in buys), 2)
    disc_sell_usd = round(sum(t.total_value_usd for t in sells_disc), 2)
    plan_sell_usd = round(sum(t.total_value_usd for t in sells_plan), 2)
    net_usd = round(buy_usd - disc_sell_usd - plan_sell_usd, 2)

    open_market = buys + sells_disc + sells_plan
    notable = sorted(open_market, key=lambda t: (-t.total_value_usd, t.transaction_date))[:top_n]

    return Form4Summary(
        filings_parsed=filings_parsed,
        total_transactions=len(transactions),
        open_market_buy_count=len(buys),
        open_market_buy_usd=buy_usd,
        unique_buyers=sorted({t.owner_name for t in buys}),
        discretionary_sell_count=len(sells_disc),
        discretionary_sell_usd=disc_sell_usd,
        plan_10b5_1_sell_count=len(sells_plan),
        plan_10b5_1_sell_usd=plan_sell_usd,
        unique_sellers=sorted({t.owner_name for t in (sells_disc + sells_plan)}),
        routine_grant_tax_count=len(routine),
        net_open_market_usd=net_usd,
        notable_transactions=[t.to_dict() for t in notable],
    )


def extract_multiyear_capital_allocation(
    company_facts: dict[str, Any],
    xbrl_tag_fallbacks: dict[str, list[str]],
    years: int = 3,
) -> list[dict[str, Any]]:
    """Extract multi-year 10-K annual capital allocation & unit economics progression (USD M)."""

    def _annual_map(metric_key: str) -> dict[int, tuple[float, str, str]]:
        tags = xbrl_tag_fallbacks.get(metric_key, [])
        if not tags:
            return {}
        facts = _collect_facts(company_facts, tags, unit_key="USD", require_start=True)
        out: dict[int, tuple[float, str, str]] = {}
        for f in facts:
            if f.form not in {"10-K", "10-K/A"}:
                continue
            if not (ANNUAL_MIN_DAYS <= _duration_days(f) <= ANNUAL_MAX_DAYS):
                continue
            yr = f.end.year
            out[yr] = (f.val, f.end.isoformat(), f.accn)
        return out

    rev_map = _annual_map("revenue")
    gp_map = _annual_map("gross_profit")
    cor_map = _annual_map("cost_of_revenue")
    op_map = _annual_map("operating_income")
    ocf_map = _annual_map("operating_cash_flow")
    capex_map = _annual_map("capex")
    rd_map = _annual_map("rd_expense")
    acq_map = _annual_map("acquisitions_cash")
    buyback_map = _annual_map("share_repurchases")
    div_map = _annual_map("dividends_paid")
    sbc_map = _annual_map("stock_based_compensation")

    if not rev_map:
        return []

    target_years = sorted(rev_map.keys())[-years:]
    rows: list[dict[str, Any]] = []
    for yr in target_years:
        rev_val, period_end, accn = rev_map[yr]
        prev_rev = rev_map[yr - 1][0] if (yr - 1) in rev_map else None
        rev_yoy = ((rev_val / prev_rev) - 1.0) if (prev_rev and prev_rev > 0) else None

        gp_val = gp_map[yr][0] if yr in gp_map else None
        if gp_val is None and yr in cor_map:
            gp_val = rev_val - cor_map[yr][0]
        gm = (gp_val / rev_val) if (gp_val is not None and rev_val > 0) else None

        op_val = op_map[yr][0] if yr in op_map else None
        op_margin = (op_val / rev_val) if (op_val is not None and rev_val > 0) else None

        ocf_val = ocf_map[yr][0] if yr in ocf_map else None
        capex_val = abs(capex_map[yr][0]) if yr in capex_map else None
        fcf_val = (
            (ocf_val - capex_val)
            if (ocf_val is not None and capex_val is not None)
            else ocf_val
        )

        rd_val = abs(rd_map[yr][0]) if yr in rd_map else None
        acq_val = abs(acq_map[yr][0]) if yr in acq_map else 0.0
        buyback_val = abs(buyback_map[yr][0]) if yr in buyback_map else 0.0
        div_val = abs(div_map[yr][0]) if yr in div_map else 0.0
        sbc_val = abs(sbc_map[yr][0]) if yr in sbc_map else None

        to_m = lambda v: round(v / 1_000_000.0, 2) if v is not None else None  # noqa: E731
        rows.append(
            {
                "fiscal_year": yr,
                "period_end": period_end,
                "filing_accession": accn,
                "revenue_usd_m": to_m(rev_val),
                "revenue_yoy_growth": round(rev_yoy, 4) if rev_yoy is not None else None,
                "gross_margin": round(gm, 4) if gm is not None else None,
                "operating_margin": round(op_margin, 4) if op_margin is not None else None,
                "ocf_usd_m": to_m(ocf_val),
                "capex_usd_m": to_m(capex_val),
                "fcf_usd_m": to_m(fcf_val),
                "rd_usd_m": to_m(rd_val),
                "rd_pct_of_rev": (
                    round(rd_val / rev_val, 4) if (rd_val is not None and rev_val > 0) else None
                ),
                "acquisitions_cash_usd_m": to_m(acq_val),
                "share_repurchases_usd_m": to_m(buyback_val),
                "dividends_paid_usd_m": to_m(div_val),
                "sbc_usd_m": to_m(sbc_val),
                "sbc_pct_of_rev": (
                    round(sbc_val / rev_val, 4) if (sbc_val is not None and rev_val > 0) else None
                ),
            }
        )
    return rows


def _fmt_m(v: float | None) -> str:
    return f"${v:,.1f}M" if v is not None else "N/A"


def _fmt_usd(v: float) -> str:
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:,.2f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:,.1f}K"
    return f"${v:,.0f}"


def _fmt_pct(v: float | None) -> str:
    return f"{v:.1%}" if v is not None else "N/A"


def render_layer3_evidence_markdown(
    scorecard: dict[str, Any],
    cap_alloc_rows: list[dict[str, Any]],
    form4_summary: Form4Summary,
    narrative_filings: list[dict[str, Any]],
    today_str: str,
) -> str:
    """Render objective Layer 3 dossier Markdown ready for human subjective review."""
    ticker = scorecard.get("ticker", "UNKNOWN")
    company = scorecard.get("company_name", ticker)
    cik = scorecard.get("cik", "N/A")
    industry = scorecard.get("industry_slug", "unassigned")
    mcap = scorecard.get("odds_check", {}).get("current_market_cap_usd_m")
    f4 = scorecard.get("dimensions", {}).get("4_financial_floor", {})

    lines = [
        f"# `{ticker}` — 第三层五维客观投研证据档案",
        "",
        f"- **公司名称**：{company}",
        f"- **CIK**：`{cik}` | **所属赛道**：`{industry}` | "
        f"**二层轨道**：`{scorecard.get('layer2_track_qualified')}`",
        f"- **当前市值**：`{_fmt_m(mcap)}` | **档案状态**：`PENDING_HUMAN_REVIEW` | "
        f"**取证日期**：`{today_str}`",
        "",
        "> [!IMPORTANT]",
        "> 本档案所有数字均提取自 SEC EDGAR 官方一手申报文件（10-K/10-Q XBRL 与 Form 4 XML）。",
        "> 依据 `AGENT_RULES.md`，**维度五（掌舵人）仅呈现客观事实，"
        "主观评分、三句话备忘录与最终入罐决策严格留给人类**。",
        "",
        "---",
        "",
        "## 1–3. 商业模式、Why Now 与竞争格局（待结合一手 10-K/MD&A 深度补全）",
        "- **维度 1（需求满足创新）**：请结合下方最新 10-K Item 1（Business）"
        "核查存量利润池规模、数量级效率提升与旧巨头困境。",
        "- **维度 2（市场正在形成 / Why Now）**：核查渗透率拐点与外部催化。",
        "- **维度 3（竞争格局与护城河雏形）**：核查下表近 3 年毛利率/营业利润率演变趋势，"
        "印证定价权与边际成本递减。",
        "",
        "---",
        "",
        "## 4. 公司财务底线与近 3 年复利轨迹（SEC 10-K XBRL）",
        "",
        f"- 最新 TTM 营收：`{_fmt_m(f4.get('ttm_revenue_usd_m'))}`"
        f"（YoY `{_fmt_pct(f4.get('revenue_yoy_growth'))}`）"
        f" | 毛利率：`{_fmt_pct(f4.get('gross_margin'))}`"
        f" | FCF：`{_fmt_m(f4.get('fcf_usd_m'))}`"
        f" | 短债/现金：`{f4.get('short_term_debt_to_cash')}`"
        f" | 商誉/总资产：`{_fmt_pct(f4.get('goodwill_to_assets'))}`"
        f" | 年化稀释：`{_fmt_pct(f4.get('annual_share_dilution'))}`",
        "",
    ]

    if cap_alloc_rows:
        lines.extend(
            [
                "| 财年 | 期末日 | 营收 | YoY | 毛利率 | 营业利润率 | "
                "OCF | CapEx | FCF | SBC/营收 | Accession |",
                "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for r in cap_alloc_rows:
            lines.append(
                f"| FY{r['fiscal_year']} | {r['period_end']} | {_fmt_m(r['revenue_usd_m'])} "
                f"| {_fmt_pct(r['revenue_yoy_growth'])} | {_fmt_pct(r['gross_margin'])} "
                f"| {_fmt_pct(r['operating_margin'])} | {_fmt_m(r['ocf_usd_m'])} "
                f"| {_fmt_m(r['capex_usd_m'])} | {_fmt_m(r['fcf_usd_m'])} "
                f"| {_fmt_pct(r['sbc_pct_of_rev'])} | `{r['filing_accession']}` |"
            )
    else:
        lines.append("_暂无完整近 3 年 10-K XBRL 年度序列。_")

    lines.extend(
        [
            "",
            "---",
            "",
            "## 5. 掌舵人客观取证（严禁 Agent 打主观分）",
            "",
            "### 5.1 近 3 年资本配置记录（R&D / CapEx / 并购 / 回购 / 分红）",
        ]
    )
    if cap_alloc_rows:
        lines.extend(
            [
                "| 财年 | R&D (占比) | CapEx | 现金并购 (M&A) | 股票回购 | 现金分红 | SBC 费用 |",
                "|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for r in cap_alloc_rows:
            rd_str = (
                f"{_fmt_m(r['rd_usd_m'])} ({_fmt_pct(r['rd_pct_of_rev'])})"
                if r["rd_usd_m"] is not None
                else "未单列"
            )
            acq_s = _fmt_m(r["acquisitions_cash_usd_m"])
            bb_s = _fmt_m(r["share_repurchases_usd_m"])
            lines.append(
                f"| FY{r['fiscal_year']} | {rd_str} | {_fmt_m(r['capex_usd_m'])} "
                f"| {acq_s} | {bb_s} "
                f"| {_fmt_m(r['dividends_paid_usd_m'])} | {_fmt_m(r['sbc_usd_m'])} |"
            )
    else:
        lines.append("_暂无 10-K 资本配置数据。_")

    f4_n = form4_summary.filings_parsed
    lines.extend(
        [
            "",
            f"### 5.2 SEC Form 4 内部人交易统计（近 24 个月，解析 {f4_n} 份申报）",
            f"- **公开市场真金白银买入 (`Code P`)**：`{form4_summary.open_market_buy_count}` 笔，"
            f"合计 **`{_fmt_usd(form4_summary.open_market_buy_usd)}`**"
            f"（买入人：{', '.join(form4_summary.unique_buyers) or '无'}）",
            f"- **计划外自主卖出 (`Code S`, 非 10b5-1)**："
            f"`{form4_summary.discretionary_sell_count}` 笔，"
            f"合计 `{_fmt_usd(form4_summary.discretionary_sell_usd)}`",
            f"- **10b5-1 预设减持计划 (`Code S`, 10b5-1)**："
            f"`{form4_summary.plan_10b5_1_sell_count}` 笔，"
            f"合计 `{_fmt_usd(form4_summary.plan_10b5_1_sell_usd)}`",
            "- **常规股权激励授予/行权/代扣税 (`A/M/F/G`，不计入买卖)**："
            f"`{form4_summary.routine_grant_tax_count}` 笔",
            f"- **公开市场净买/卖额**：`{_fmt_usd(form4_summary.net_open_market_usd)}`",
            "",
        ]
    )

    if form4_summary.notable_transactions:
        lines.extend(
            [
                "#### 重点公开市场内部人交易明细（按金额排序前 10 笔）",
                "| 交易日 | 内部人 | 职务 | 类型 | 10b5-1 | 股数 | 均价 | 金额 | Accession |",
                "|---|---|---|---|---|---:|---:|---:|---|",
            ]
        )
        for tx in form4_summary.notable_transactions:
            code_label = "买入 (P)" if tx["transaction_code"] == "P" else "卖出 (S)"
            plan_label = "是" if tx["is_10b5_1"] else "否"
            px = f"${tx['price_per_share']:.2f}" if tx["price_per_share"] is not None else "N/A"
            lines.append(
                f"| {tx['transaction_date']} | {tx['owner_name']} | {tx['owner_role']} "
                f"| {code_label} | {plan_label} | {tx['shares']:,.0f} | {px} "
                f"| {_fmt_usd(tx['total_value_usd'])} | `{tx['accession']}` |"
            )

    lines.extend(
        [
            "",
            "### 5.3 一手公告索引（供核验过去 2–3 年管理层指引兑现率与 MD&A）",
        ]
    )
    if narrative_filings:
        for f in narrative_filings:
            raw_items = f.get("items")
            if isinstance(raw_items, list):
                items_str = ", ".join(str(x) for x in raw_items if x)
            else:
                items_str = str(raw_items or "").strip()
            items_txt = f" (Items: {items_str})" if items_str else ""
            lines.append(
                f"- `{f['filing_date']}` **{f['form']}** (Period `{f['report_date'] or 'N/A'}`)"
                f"{items_txt} — [{f['accession']}]({f['url']})"
            )
    else:
        lines.append("_暂无近期 10-K/10-Q/8-K 索引。_")

    lines.extend(
        [
            "",
            "---",
            "",
            "## 6. 人类审阅与入罐区（请在 Web 工作台或 `scorecard.yaml` 中亲自完成）",
            "- [ ] 使用 Web 工作台 **10x 赔率沙盒** 压力测试 10 年终局市值（需 `>= 10x`）",
            "- [ ] 审阅上述资本配置与 Form 4 记录，亲手填写 `human_subjective_score (1–10)`",
            "- [ ] 写下三句话买入备忘录与 1–2 条可机读证伪线，决定是否装入 `coffee_can/`",
        ]
    )
    return "\n".join(lines) + "\n"


def update_scorecard_with_evidence(
    existing_scorecard: dict[str, Any],
    cap_alloc_rows: list[dict[str, Any]],
    form4_summary: Form4Summary,
    narrative_filings: list[dict[str, Any]],
    today_str: str,
) -> dict[str, Any]:
    """Attach objective evidence to scorecard.yaml while preserving all human-owned fields."""
    sc = dict(existing_scorecard)
    dims = dict(sc.get("dimensions") or {})
    mgmt = dict(dims.get("5_management") or {})
    obj_ev = dict(mgmt.get("objective_evidence") or {})

    if cap_alloc_rows:
        total_acq = sum(r["acquisitions_cash_usd_m"] or 0.0 for r in cap_alloc_rows)
        total_bb = sum(r["share_repurchases_usd_m"] or 0.0 for r in cap_alloc_rows)
        total_capex = sum(r["capex_usd_m"] or 0.0 for r in cap_alloc_rows)
        total_fcf = sum(r["fcf_usd_m"] or 0.0 for r in cap_alloc_rows)
        obj_ev["capital_allocation_record"] = (
            f"近 {len(cap_alloc_rows)} 财年累计：FCF ${total_fcf:,.1f}M | "
            f"CapEx ${total_capex:,.1f}M | 现金并购 ${total_acq:,.1f}M | "
            f"股票回购 ${total_bb:,.1f}M"
        )
        obj_ev["capital_allocation_3y"] = cap_alloc_rows

    obj_ev["form4_net_insider_activity_24m"] = (
        f"解析 {form4_summary.filings_parsed} 份 Form 4："
        f"公开买入 {form4_summary.open_market_buy_count} 笔 "
        f"({_fmt_usd(form4_summary.open_market_buy_usd)}) | "
        f"自主卖出 {form4_summary.discretionary_sell_count} 笔 "
        f"({_fmt_usd(form4_summary.discretionary_sell_usd)}) | "
        f"10b5-1 计划卖出 {form4_summary.plan_10b5_1_sell_count} 笔 "
        f"({_fmt_usd(form4_summary.plan_10b5_1_sell_usd)})"
    )
    obj_ev["form4_summary"] = {
        "filings_parsed": form4_summary.filings_parsed,
        "open_market_buy_count": form4_summary.open_market_buy_count,
        "open_market_buy_usd": form4_summary.open_market_buy_usd,
        "discretionary_sell_count": form4_summary.discretionary_sell_count,
        "discretionary_sell_usd": form4_summary.discretionary_sell_usd,
        "plan_10b5_1_sell_count": form4_summary.plan_10b5_1_sell_count,
        "plan_10b5_1_sell_usd": form4_summary.plan_10b5_1_sell_usd,
        "net_open_market_usd": form4_summary.net_open_market_usd,
    }
    obj_ev["evidence_refs"] = [f["accession"] for f in narrative_filings[:6] if f.get("accession")]

    # Strictly preserve human-owned fields (AGENT_RULES.md)
    mgmt["objective_evidence"] = obj_ev
    mgmt.setdefault("human_subjective_score", None)
    mgmt.setdefault("human_notes", "")
    mgmt.setdefault("reviewed_by_human", False)
    mgmt.setdefault("reviewed_at", None)
    dims["5_management"] = mgmt
    sc["dimensions"] = dims

    # Only advance DRAFTING_EVIDENCE -> PENDING_HUMAN_REVIEW (never APPROVED_FOR_CAN!)
    if sc.get("status") == "DRAFTING_EVIDENCE":
        sc["status"] = "PENDING_HUMAN_REVIEW"
    sc["updated_at"] = today_str
    return sc
