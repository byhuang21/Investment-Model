// Coffee Can OS — Interactive Web Workbench Frontend (Live FastAPI + Static Fallback)

const appState = {
  mode: "loading", // "live-rw" | "static-ro"
  snapshot: null,
  activeTab: "overview",
  loopsSubTab: "can",
  radarFilter: "ALL",
  signalFilter: "NEW",
  screenerFilter: "ALL",
  screenerQuery: "",
  expandedScreenerTicker: null,
  selectedResearchTicker: null,
  // Local interactive draft state for Layer 3 Human Gate
  drafts: {},
};

const PAGE_META = {
  overview: [
    "漏斗总览",
    "实时读取 Git memory/ 真相源与 SEC EDGAR 财报档案",
  ],
  radar: [
    "第一层 · 赛道雷达 & 状态机",
    "聚合 USASpending 联邦合同、SEC EFTS 全文集群与龙头 XBRL 营收增速",
  ],
  screener: [
    "第二层 · 量化硬筛分流台",
    "双轨硬过滤（A1 高毛利复利 / A2 经营杠杆复利 / B 高毛利高成长）三向分流审计",
  ],
  deepdive: [
    "第三层 · 五维深挖 & 人类决策台",
    "10x 赔率交互压力测试 + 掌舵人主观评分（Agent 严禁越权填写）与一键入罐",
  ],
  loops: [
    "咖啡罐持仓 · 观察池 · 半年校准",
    "四大自动化闭环：持仓季报证伪红绿灯、观察池估值防抖盯盘与误杀率复盘",
  ],
};

// ============================================================================
// Formatting & Utilities
// ============================================================================
function esc(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function fmtM(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "N/A";
  const n = Number(v);
  if (Math.abs(n) >= 1000) return `$${n.toLocaleString("en-US", { maximumFractionDigits: 0 })}M`;
  return `$${n.toFixed(0)}M`;
}

function fmtPct(v, signed = false) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "N/A";
  const pct = Number(v) * 100;
  const sign = signed && pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(1)}%`;
}

function showToast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.add("is-visible");
  clearTimeout(window._ccToastTimer);
  window._ccToastTimer = setTimeout(() => el.classList.remove("is-visible"), 3200);
}

// ============================================================================
// Data Loading & API Mutations
// ============================================================================
async function loadSnapshot() {
  try {
    const resp = await fetch("/api/snapshot", { cache: "no-store" });
    if (resp.ok) {
      appState.snapshot = await resp.json();
      appState.mode = "live-rw";
    } else {
      throw new Error("API unavailable");
    }
  } catch {
    const staticResp = await fetch("./data/snapshot.json", { cache: "no-store" });
    if (!staticResp.ok) {
      document.getElementById("view-root").innerHTML =
        `<div class="empty-state">无法加载数据快照。请运行 <code>python scripts/run_workbench.py</code> 或 <code>python scripts/export_web_snapshot.py</code>。</div>`;
      return;
    }
    appState.snapshot = await staticResp.json();
    appState.mode = "static-ro";
  }

  // Ensure a default Layer 3 research ticker is selected
  const researchList = appState.snapshot.research || [];
  if (
    researchList.length &&
    (!appState.selectedResearchTicker ||
      !researchList.some((r) => r.ticker === appState.selectedResearchTicker))
  ) {
    appState.selectedResearchTicker = researchList[0].ticker;
  }

  syncHeaderAndBadges();
  renderCurrentView();
}

function syncHeaderAndBadges() {
  const s = appState.snapshot;
  if (!s) return;

  const activeInd = (s.industries || []).filter((i) => i.status === "ACTIVE").length;
  const screenerTotal =
    (s.research || []).length + (s.near_miss || []).length + (s.rejected_log || []).length;
  const pendingResearch = (s.research || []).filter(
    (r) => r.scorecard?.status !== "APPROVED_FOR_CAN"
  ).length;

  document.getElementById("badge-active-industries").textContent = String(activeInd);
  document.getElementById("badge-screener-total").textContent = String(screenerTotal);
  document.getElementById("badge-research-count").textContent = `${pendingResearch} 待审`;
  document.getElementById("badge-in-can").textContent = String((s.coffee_can || []).length);

  document.getElementById("rules-version-text").textContent = `rules v${s.rules_version || "0.2.0"}`;
  const modePill = document.getElementById("runtime-mode-pill");
  if (appState.mode === "live-rw") {
    if (s.env === "sandbox") {
      modePill.textContent = "SANDBOX 沙盒读写";
      modePill.classList.add("is-static");
    } else {
      modePill.textContent = "PROD 本地读写";
      modePill.classList.remove("is-static");
    }
  } else {
    modePill.textContent = "静态只读快照";
    modePill.classList.add("is-static");
  }

  const ts = s.generated_at ? new Date(s.generated_at).toLocaleString("zh-CN") : "已同步";
  document.getElementById("snapshot-time").textContent = `同步：${ts}`;
}

function switchTab(tab, subTab = null) {
  appState.activeTab = tab;
  if (subTab) appState.loopsSubTab = subTab;
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.tab === tab);
  });
  const [title, subtitle] = PAGE_META[tab] || PAGE_META.overview;
  document.getElementById("page-title").textContent = title;
  document.getElementById("page-subtitle").textContent = subtitle;
  renderCurrentView();
}

function buildUnifiedScreenerRows() {
  const s = appState.snapshot;
  const rows = [];

  for (const item of s.research || []) {
    const sc = item.scorecard || {};
    const f4 = sc.dimensions?.["4_financial_floor"] || {};
    rows.push({
      ticker: sc.ticker || item.ticker,
      name: sc.company_name || item.ticker,
      industry: sc.industry_slug || "—",
      mcapM: sc.odds_check?.current_market_cap_usd_m,
      ttmRevM: f4.ttm_revenue_usd_m,
      revYoY: f4.revenue_yoy_growth,
      gm: f4.gross_margin,
      ocfM: f4.ocf_usd_m,
      fcfM: f4.fcf_usd_m,
      runwayMo: f4.cash_runway_months,
      goodwill: f4.goodwill_to_assets,
      stDebtCash: f4.short_term_debt_to_cash,
      dilution: f4.annual_share_dilution,
      track: sc.layer2_track_qualified || "profitable_compounder",
      outcome: "PASS",
      accession: (f4.filing_refs || [])[0] || "—",
      detail: `通过二层硬筛 (${sc.layer2_track_qualified || "Track A"})，当前三层状态：${sc.status}`,
    });
  }

  for (const nm of s.near_miss || []) {
    const f = nm.fundamentals_snapshot || {};
    rows.push({
      ticker: nm.ticker,
      name: nm.company_name || nm.ticker,
      industry: nm.industry_slug || "—",
      mcapM: nm.monitor_state?.last_market_cap_usd_m ?? nm.market_cap_at_entry_usd_m,
      ttmRevM: f.ttm_revenue_usd_m,
      revYoY: f.revenue_yoy_growth,
      gm: f.gross_margin,
      ocfM: null,
      fcfM: null,
      runwayMo: f.cash_runway_months,
      goodwill: null,
      stDebtCash: null,
      dilution: f.annual_share_dilution,
      track: f.track_qualified || "—",
      outcome: "NEAR_MISS",
      accession: f.filing_accession || "—",
      detail: `${nm.near_miss_reason} · 目标触发市值 <= ${fmtM(nm.targets?.target_market_cap_usd_m)}`,
    });
  }

  for (const rej of s.rejected_log || []) {
    const m = rej.metrics_snapshot || {};
    rows.push({
      ticker: rej.ticker,
      name: m.company_name || rej.ticker,
      industry: rej.industry_slug || "—",
      mcapM: rej.market_cap_at_rejection_usd_m ?? m.market_cap_usd_m,
      ttmRevM: m.ttm_revenue_usd_m,
      revYoY: m.revenue_yoy_growth,
      gm: m.gross_margin,
      ocfM: m.ocf_usd_m,
      fcfM: m.fcf_usd_m,
      runwayMo: m.cash_runway_months,
      goodwill: m.goodwill_to_total_assets,
      stDebtCash: m.short_term_debt_to_cash,
      dilution: m.annual_share_dilution,
      track: "—",
      outcome: "REJECTED",
      accession: m.filing_accession || "—",
      detail: (rej.failed_rules || []).join("；"),
    });
  }

  const order = { PASS: 0, NEAR_MISS: 1, REJECTED: 2 };
  return rows.sort((a, b) => order[a.outcome] - order[b.outcome] || a.ticker.localeCompare(b.ticker));
}

// ============================================================================
// View 1: Overview (HQ)
// ============================================================================
function renderOverview() {
  const s = appState.snapshot;
  const activeInd = (s.industries || []).filter((i) => i.status === "ACTIVE").length;
  const dormantInd = (s.industries || []).filter((i) => i.status === "DORMANT").length;
  const pendingResearch = (s.research || []).filter(
    (r) => r.scorecard?.status !== "APPROVED_FOR_CAN"
  );
  const inCan = s.coffee_can || [];
  const nearMiss = s.near_miss || [];
  const rejected = s.rejected_log || [];
  const newSignals = (s.discovered_signals || []).filter(
    (sig) => String(sig.status || "NEW").toUpperCase() === "NEW"
  );

  const actionItems = [];
  if (pendingResearch.length) {
    actionItems.push(`
      <div class="action-row">
        <div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            <span class="badge badge-warning mono">HUMAN GATE</span>
            <strong>${pendingResearch.length} 只 PASS 标的正在 memory/research/ 等待第三层深挖与掌舵人打分</strong>
          </div>
          <div class="card-desc">标的：${pendingResearch.map((r) => esc(r.ticker)).join(" · ")}</div>
        </div>
        <button type="button" class="btn btn-primary btn-sm" data-goto-tab="deepdive">进入审阅台 →</button>
      </div>
    `);
  }
  if (newSignals.length) {
    actionItems.push(`
      <div class="action-row">
        <div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            <span class="badge badge-success mono">BLIND RADAR</span>
            <strong>机器盲扫新发现 ${newSignals.length} 条潜在赛道线索在隔离区待审阅</strong>
          </div>
          <div class="card-desc">示例：${newSignals.slice(0, 4).map((sig) => esc(sig.name)).join(" · ")}</div>
        </div>
        <button type="button" class="btn btn-secondary btn-sm" data-goto-tab="radar">去 L1 审阅新线索 →</button>
      </div>
    `);
  }
  if (nearMiss.length) {
    actionItems.push(`
      <div class="action-row">
        <div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            <span class="badge badge-primary mono">WATCHLIST</span>
            <strong>错杀观察池正在监控 ${nearMiss.length} 只高质但偏贵标的</strong>
          </div>
          <div class="card-desc">${nearMiss.map((n) => `${esc(n.ticker)} (目标 <= ${fmtM(n.targets?.target_market_cap_usd_m)})`).join(" · ")}</div>
        </div>
        <button type="button" class="btn btn-secondary btn-sm" data-goto-tab="loops" data-goto-sub="watchlist">查看盯盘状态</button>
      </div>
    `);
  }
  if (s.post_mortem) {
    const dormantSummary = (s.post_mortem.dormant_checks || [])
      .map((d) => `${esc(d.slug)} 180天超额 ${fmtPct(d.avg_180d_excess_vs_spy, true)}`)
      .join("；");
    actionItems.push(`
      <div class="action-row">
        <div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            <span class="badge badge-muted mono">POST-MORTEM</span>
            <strong>最新复盘已归档（${esc(s.post_mortem.as_of_date)}）：追踪 ${s.post_mortem.total_rejected_entries} 条淘汰样本</strong>
          </div>
          <div class="card-desc">${dormantSummary || "所有规则当前处于严控状态"}</div>
        </div>
        <button type="button" class="btn btn-secondary btn-sm" data-goto-tab="loops" data-goto-sub="postmortem">查看归因报告</button>
      </div>
    `);
  }

  return `
    <section class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label">赛道雷达状态机</div>
        <div class="kpi-value-row">
          <span class="kpi-value mono">${activeInd}</span>
          <span class="badge badge-success mono">ACTIVE</span>
          <span class="muted mono" style="font-size:12px;">/ ${dormantInd} DORMANT</span>
        </div>
        <div class="kpi-foot">隔离区另发现 ${newSignals.length} 条盲扫新赛道线索</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">第三层待审队列</div>
        <div class="kpi-value-row">
          <span class="kpi-value mono">${pendingResearch.length}</span>
          <span class="badge badge-warning mono">PASS</span>
        </div>
        <div class="kpi-foot">已通过 SEC XBRL 财务底线硬过滤</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">错杀观察池盯盘</div>
        <div class="kpi-value-row">
          <span class="kpi-value mono">${nearMiss.length}</span>
          <span class="badge badge-primary mono">WATCHING</span>
        </div>
        <div class="kpi-foot">连续 3 交易日跌破目标市值触发提醒</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">咖啡罐正式持仓</div>
        <div class="kpi-value-row">
          <span class="kpi-value mono">${inCan.length}</span>
          <span class="badge badge-success mono">IN CAN</span>
        </div>
        <div class="kpi-foot">淘汰池已锚定 ${rejected.length} 条回测样本</div>
      </div>
    </section>

    <section class="card">
      <div class="card-header">
        <div>
          <h2 class="card-title">三层投研漏斗实时态势</h2>
          <p class="card-desc">点击任意模块直接切入对应真实记忆库数据</p>
        </div>
        <span class="mono muted" style="font-size:12px;">Top-Down · >= 10x Odds · Low Turnover</span>
      </div>
      <div class="grid-4">
        <button type="button" class="funnel-step" data-goto-tab="radar">
          <div class="funnel-step-head">
            <span class="mono strong" style="color:var(--accent);">LAYER 1</span>
            <span class="muted">主动发现</span>
          </div>
          <div class="funnel-step-title">选市场与行业</div>
          <div class="funnel-step-desc">${activeInd} 活跃 / ${dormantInd} 休眠 + ${newSignals.length} 条盲扫新线索</div>
        </button>

        <button type="button" class="funnel-step" data-goto-tab="screener">
          <div class="funnel-step-head">
            <span class="mono strong" style="color:var(--accent);">LAYER 2</span>
            <span class="muted">硬过滤</span>
          </div>
          <div class="funnel-step-title">双轨量化分流</div>
          <div class="funnel-step-desc">${(s.research || []).length} PASS · ${nearMiss.length} NEAR_MISS · ${rejected.length} REJECTED</div>
        </button>

        <button type="button" class="funnel-step" data-goto-tab="deepdive">
          <div class="funnel-step-head">
            <span class="mono strong" style="color:var(--warning);">LAYER 3</span>
            <span class="muted">人机协同</span>
          </div>
          <div class="funnel-step-title">五维深度解剖</div>
          <div class="funnel-step-desc">10x 赔率沙盒 + 掌舵人主观打分与证伪线确认</div>
        </button>

        <button type="button" class="funnel-step" data-goto-tab="loops" data-goto-sub="can">
          <div class="funnel-step-head">
            <span class="mono strong" style="color:var(--success);">LOOPS</span>
            <span class="muted">自动闭环</span>
          </div>
          <div class="funnel-step-title">持仓 · 观察池 · 校准</div>
          <div class="funnel-step-desc">${inCan.length} 只持仓季报红绿灯体检 & 半年误杀回测</div>
        </button>
      </div>
    </section>

    <section class="card">
      <div class="card-header">
        <div>
          <h2 class="card-title">当前待办与信号队列</h2>
          <p class="card-desc">基于本地 Git 仓库最新扫描状态自动汇总</p>
        </div>
      </div>
      <div class="action-list">${actionItems.join("") || '<div class="empty-state">暂无待办事项</div>'}</div>
    </section>
  `;
}

// ============================================================================
// View 2: Layer 1 Topic Radar
// ============================================================================
function renderRadar() {
  const industries = (appState.snapshot.industries || []).filter(
    (i) => appState.radarFilter === "ALL" || i.status === appState.radarFilter
  );

  const filters = ["ALL", "ACTIVE", "DORMANT", "ARCHIVED"]
    .map(
      (f) =>
        `<button type="button" class="pill-btn ${appState.radarFilter === f ? "is-active" : ""}" data-radar-filter="${f}">${f === "ALL" ? "全部赛道" : f}</button>`
    )
    .join("");

  const cards = industries
    .map((ind) => {
      const snap = ind.latest_radar_snapshot || {};
      const cond = ind.awaken_conditions || {};
      const revTarget = Number(cond.min_leaders_aggregate_revenue_usd_m || 1000);
      const revActual = snap.leaders_aggregate_revenue_usd_m;
      const revPct = revActual != null ? Math.min(100, Math.round((revActual / revTarget) * 100)) : 0;

      const usaTarget = Number(cond.usaspending_12m_award_usd_m || 250);
      const usaActual = Number(snap.usaspending_12m_award_usd_m || 0);
      const usaPct = Math.min(100, Math.round((usaActual / usaTarget) * 100));

      const leadersSet = new Set((ind.leader_tickers || []).map((t) => String(t).toUpperCase()));
      const allCandidates = ind.candidate_tickers || [];
      const nextStatus = ind.status === "ACTIVE" ? "DORMANT" : "ACTIVE";
      const badgeCls =
        ind.status === "ACTIVE"
          ? "badge-success"
          : ind.status === "DORMANT"
            ? "badge-muted"
            : "badge-warning";

      return `
        <article class="card" style="display:flex;flex-direction:column;justify-content:space-between;gap:16px;">
          <div style="display:flex;flex-direction:column;gap:12px;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;">
              <div>
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                  <span class="badge ${badgeCls} mono">${esc(ind.status)}</span>
                  <span class="mono muted" style="font-size:12px;">${esc(ind.slug)}</span>
                </div>
                <h3 style="margin:8px 0 0;font-size:16px;font-weight:600;">${esc(ind.name)}</h3>
              </div>
              <span class="meta-chip mono">${fmtPct(snap.leaders_median_yoy_growth, true)} YoY</span>
            </div>

            <div style="font-size:12.5px;color:var(--text-secondary);display:flex;flex-direction:column;gap:5px;">
              <div><strong style="color:var(--text-primary);">存量利润池：</strong>${esc(ind.thesis_snapshot?.profit_pool_description || "—")}</div>
              <div><strong style="color:var(--text-primary);">需求创新：</strong>${esc(ind.thesis_snapshot?.demand_innovation_hypothesis || "—")}</div>
            </div>

            <div style="padding:12px;border-radius:8px;background:var(--bg-subtle);border:1px solid var(--border-subtle);display:flex;flex-direction:column;gap:10px;">
              <div class="progress-block">
                <div class="progress-meta">
                  <span class="muted">核心龙头合计 TTM 营收（唤醒线 ${fmtM(revTarget)}）</span>
                  <span class="mono strong">${fmtM(revActual)} (${revPct}%)</span>
                </div>
                <div class="progress-track">
                  <div class="progress-fill ${revPct >= 100 ? "is-done" : ""}" style="width:${revPct}%;"></div>
                </div>
              </div>

              <div class="progress-block">
                <div class="progress-meta">
                  <span class="muted">USASpending 12M 联邦合同（唤醒线 ${fmtM(usaTarget)}）</span>
                  <span class="mono strong">${fmtM(usaActual)} · SEC 180天集群 ${snap.sec_cluster_filings_180d ?? 0} 条</span>
                </div>
                <div class="progress-track">
                  <div class="progress-fill ${usaPct >= 100 ? "is-done" : ""}" style="width:${usaPct}%;"></div>
                </div>
              </div>
            </div>
          </div>

          <div style="padding-top:12px;border-top:1px solid var(--border-subtle);display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;">
            <div class="ticker-tags">
              ${allCandidates
                .slice(0, 10)
                .map(
                  (t) =>
                    `<span class="ticker-tag ${leadersSet.has(String(t).toUpperCase()) ? "is-leader" : ""}" title="${leadersSet.has(String(t).toUpperCase()) ? "核心龙头代表" : "EFTS 发现候选"}">${esc(t)}</span>`
                )
                .join("")}
              ${allCandidates.length > 10 ? `<span class="ticker-tag">+${allCandidates.length - 10}</span>` : ""}
            </div>
            <button type="button" class="btn btn-secondary btn-sm" data-toggle-industry="${esc(ind.slug)}" data-next-status="${nextStatus}" ${appState.mode !== "live-rw" ? "disabled" : ""}>
              切换为 ${nextStatus}
            </button>
          </div>
        </article>
      `;
    })
    .join("");

  const allSignals = appState.snapshot.discovered_signals || [];
  const sigMeta = appState.snapshot.discovered_signals_meta || {};
  const newSignalsCount = allSignals.filter(
    (s) => String(s.status || "NEW").toUpperCase() === "NEW"
  ).length;

  const filteredSignals = allSignals.filter((sig) => {
    const st = String(sig.status || "NEW").toUpperCase();
    const src = String(sig.source || "").toUpperCase();
    if (appState.signalFilter === "NEW") return st === "NEW";
    if (appState.signalFilter === "HANDLED") return st !== "NEW";
    if (appState.signalFilter === "ALL") return true;
    return src === appState.signalFilter && st === "NEW";
  });

  const signalFilters = [
    ["NEW", `待审新线索 (${newSignalsCount})`],
    ["USASPENDING_NAICS", "联邦采购暴增 (NAICS)"],
    ["SEC_S1_CLUSTER", "IPO 扎堆聚类 (S-1)"],
    ["CROSS_SECTOR_PROBE", "跨行业探测网"],
    ["HANDLED", "已晋升 / 已忽略"],
  ]
    .map(
      ([val, label]) =>
        `<button type="button" class="pill-btn ${appState.signalFilter === val ? "is-active" : ""}" data-signal-filter="${val}">${label}</button>`
    )
    .join("");

  const sourceLabelMap = {
    USASPENDING_NAICS: ["USASpending NAICS", "badge-primary"],
    SEC_S1_CLUSTER: ["SEC S-1 聚类", "badge-warning"],
    CROSS_SECTOR_PROBE: ["跨行业探测器", "badge-success"],
  };

  const signalCards = filteredSignals
    .map((sig) => {
      const st = String(sig.status || "NEW").toUpperCase();
      const [srcLabel, srcCls] = sourceLabelMap[sig.source] || [sig.source || "RADAR", "badge-muted"];
      const tickers = sig.discovered_tickers || [];
      const codes = [...(sig.naics_codes || []).map((c) => `NAICS ${c}`), ...(sig.sic_codes || []).map((c) => `SIC ${c}`)];
      const disabledAttr = appState.mode !== "live-rw" ? "disabled" : "";
      const actionBar =
        st === "NEW"
          ? `
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
              <button type="button" class="btn btn-primary btn-sm" data-promote-signal="${esc(sig.slug)}" data-promote-status="DORMANT" ${disabledAttr}>收编为休眠赛道</button>
              <button type="button" class="btn btn-secondary btn-sm" data-promote-signal="${esc(sig.slug)}" data-promote-status="ACTIVE" ${disabledAttr}>直接激活</button>
              <button type="button" class="btn btn-secondary btn-sm" data-dismiss-signal="${esc(sig.slug)}" ${disabledAttr}>忽略</button>
            </div>
          `
          : `<span class="badge ${st === "PROMOTED" ? "badge-success" : "badge-muted"} mono">${esc(st)}</span>`;

      return `
        <article class="card" style="display:flex;flex-direction:column;justify-content:space-between;gap:12px;">
          <div style="display:flex;flex-direction:column;gap:10px;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;">
              <div>
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                  <span class="badge ${srcCls} mono">${esc(srcLabel)}</span>
                  <span class="mono muted" style="font-size:12px;">${esc(sig.slug)}</span>
                </div>
                <h3 style="margin:6px 0 0;font-size:15px;font-weight:600;">${esc(sig.name)}</h3>
              </div>
              <span class="meta-chip mono">${esc(sig.headline_metric || "—")}</span>
            </div>

            <div style="font-size:12.5px;color:var(--text-secondary);line-height:1.55;">
              ${esc(sig.rationale || "—")}
            </div>

            ${
              codes.length
                ? `<div class="mono muted" style="font-size:11.5px;">行业编码：${esc(codes.join(" · "))}</div>`
                : ""
            }
          </div>

          <div style="padding-top:10px;border-top:1px solid var(--border-subtle);display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;">
            <div class="ticker-tags">
              ${
                tickers.length
                  ? tickers
                      .slice(0, 8)
                      .map((t) => `<span class="ticker-tag">${esc(t)}</span>`)
                      .join("")
                  : '<span class="muted" style="font-size:12px;">待二层或人工补充龙头股</span>'
              }
            </div>
            ${actionBar}
          </div>
        </article>
      `;
    })
    .join("");

  return `
    <div class="filter-bar">
      <div class="pill-group">${filters}</div>
      <span class="muted" style="font-size:12px;">已正式建档赛道（带高亮边框 Ticker 为核心龙头）</span>
    </div>
    <div class="grid-2">${cards || '<div class="empty-state">当前筛选条件下暂无赛道</div>'}</div>

    <section class="card" style="margin-top:24px;">
      <div class="card-header">
        <div>
          <h2 class="card-title">机器盲扫新发现赛道线索（隔离暂存区）</h2>
          <p class="card-desc">
            每周日自动从 USASpending 全门类 NAICS 增速、SEC S-1 IPO 扎堆聚类与跨行业探测网扫描新机会，先落盘至 <code>memory/quarantine/signals/</code>，经你确认后再收编进正式赛道库
          </p>
        </div>
        <span class="meta-chip mono">最近盲扫：${esc(sigMeta.last_scanned_at || "未扫描")} · 每周日定时刷新</span>
      </div>

      <div class="filter-bar" style="margin-top:8px;">
        <div class="pill-group">${signalFilters}</div>
        ${
          appState.mode !== "live-rw"
            ? '<span class="muted" style="font-size:12px;">当前为 GitHub Pages 只读预览，如需点击“收编/忽略”请连接本地工作台</span>'
            : '<span class="muted" style="font-size:12px;">点击“收编”会自动生成 memory/industries/&lt;slug&gt;.yaml</span>'
        }
      </div>

      <div class="grid-2" style="margin-top:12px;">
        ${signalCards || '<div class="empty-state">当前筛选下暂无盲扫线索。运行 <code>python scripts/1_topic_radar.py --discover</code> 可立即刷新。</div>'}
      </div>
    </section>
  `;
}

// ============================================================================
// View 3: Layer 2 Quant Screener Triage
// ============================================================================
function renderScreener() {
  const allRows = buildUnifiedScreenerRows();
  const q = appState.screenerQuery.trim().toLowerCase();
  const rows = allRows.filter((r) => {
    const matchOutcome = appState.screenerFilter === "ALL" || r.outcome === appState.screenerFilter;
    const matchQuery =
      !q || r.ticker.toLowerCase().includes(q) || String(r.name).toLowerCase().includes(q);
    return matchOutcome && matchQuery;
  });

  const filters = ["ALL", "PASS", "NEAR_MISS", "REJECTED"]
    .map((f) => {
      const count = f === "ALL" ? allRows.length : allRows.filter((r) => r.outcome === f).length;
      return `<button type="button" class="pill-btn ${appState.screenerFilter === f ? "is-active" : ""}" data-screener-filter="${f}">${f === "ALL" ? "全部" : f} (${count})</button>`;
    })
    .join("");

  const badgeFor = (o) =>
    o === "PASS" ? "badge-success" : o === "NEAR_MISS" ? "badge-primary" : "badge-danger";

  const tbody = rows
    .map((r) => {
      const isOpen = appState.expandedScreenerTicker === r.ticker;
      const dilWarn = r.dilution != null && r.dilution > 0.06 ? 'style="color:var(--danger);font-weight:600;"' : "";
      return `
        <tr class="clickable-row" data-expand-ticker="${esc(r.ticker)}">
          <td>
            <div class="mono strong">${esc(r.ticker)}</div>
            <div class="muted" style="font-size:12px;">${esc(r.name)}</div>
          </td>
          <td class="num-col mono">${fmtM(r.mcapM)}</td>
          <td class="num-col mono">
            <div>${fmtM(r.ttmRevM)}</div>
            <div class="muted" style="font-size:11.5px;">${fmtPct(r.revYoY, true)}</div>
          </td>
          <td class="num-col mono">${fmtPct(r.gm)}</td>
          <td class="num-col mono">${r.fcfM != null ? fmtM(r.fcfM) : r.runwayMo ? `${r.runwayMo}m` : "—"}</td>
          <td class="num-col mono" ${dilWarn}>${fmtPct(r.dilution)}</td>
          <td class="secondary" style="font-size:12px;">${esc(r.track)}</td>
          <td class="num-col"><span class="badge ${badgeFor(r.outcome)} mono">${esc(r.outcome)}</span></td>
        </tr>
        ${
          isOpen
            ? `
          <tr class="detail-row">
            <td colspan="8">
              <div style="display:flex;flex-wrap:wrap;justify-content:space-between;gap:10px;margin-bottom:8px;">
                <div><strong>分流审计依据：</strong>${esc(r.detail)}</div>
                <span class="mono muted" style="font-size:12px;">SEC Accession: ${esc(r.accession)}</span>
              </div>
              <div style="display:flex;flex-wrap:wrap;gap:16px;align-items:center;font-size:12px;" class="muted mono">
                <span>OCF: ${fmtM(r.ocfM)}</span>
                <span>商誉/总资产: ${fmtPct(r.goodwill)}</span>
                <span>短债/现金: ${r.stDebtCash ?? "—"}</span>
                <span>赛道: ${esc(r.industry)}</span>
                ${
                  r.outcome === "PASS"
                    ? `<button type="button" class="btn btn-primary btn-sm" data-open-research="${esc(r.ticker)}">前往第三层审阅 →</button>`
                    : ""
                }
              </div>
            </td>
          </tr>
        `
            : ""
        }
      `;
    })
    .join("");

  return `
    <div class="filter-bar">
      <div class="pill-group">${filters}</div>
      <input type="search" id="screener-search" class="input search-input" placeholder="搜索 Ticker 或公司名..." value="${esc(appState.screenerQuery)}" />
    </div>
    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>标的</th>
            <th class="num-col">市值</th>
            <th class="num-col">TTM 营收 / YoY</th>
            <th class="num-col">毛利率</th>
            <th class="num-col">FCF / 跑道</th>
            <th class="num-col">年化稀释</th>
            <th>命中轨道</th>
            <th class="num-col">分流结果</th>
          </tr>
        </thead>
        <tbody>${tbody || '<tr><td colspan="8" class="empty-state">无匹配标的</td></tr>'}</tbody>
      </table>
    </div>
  `;
}

// ============================================================================
// View 4: Layer 3 Deep Dive & Human Gate (Read/Write YAML)
// ============================================================================
function getOrInitDraft(researchItem) {
  const ticker = researchItem.ticker;
  if (!appState.drafts[ticker]) {
    const sc = researchItem.scorecard || {};
    const mcapM = Number(sc.odds_check?.current_market_cap_usd_m || 5000);
    const revM = Number(sc.dimensions?.["4_financial_floor"]?.ttm_revenue_usd_m || 1000);
    const defaultTamB = Math.max(20, Math.round((revM * 15) / 1000));
    const savedMemo = sc.decision?.three_sentence_memo || ["", "", ""];
    const savedLines =
      sc.decision?.falsification_lines && sc.decision.falsification_lines.length
        ? sc.decision.falsification_lines.map((l) => ({ ...l }))
        : [
            {
              id: "FL-1",
              description: "连续 2 季度营收同比增速跌破 12%",
              metric: "revenue_yoy_growth",
              operator: "<",
              threshold: 0.12,
              consecutive_quarters: 2,
              severity: "RED",
            },
            {
              id: "FL-2",
              description: "毛利率跌破 25%（护城河受损或价格战）",
              metric: "gross_margin",
              operator: "<",
              threshold: 0.25,
              consecutive_quarters: 1,
              severity: "RED",
            },
          ];

    const savedOdds = sc.odds_check || {};
    appState.drafts[ticker] = {
      currentMcapM: mcapM,
      tamB: defaultTamB,
      sharePct: 12,
      netMarginPct: 18,
      exitPe: 25,
      forwardValuationNote: savedOdds.forward_1_2y_valuation_note || "",
      longTermRunwayIntact: savedOdds.long_term_runway_intact !== false,
      humanScore: sc.dimensions?.["5_management"]?.human_subjective_score ?? null,
      humanNotes: sc.dimensions?.["5_management"]?.human_notes || "",
      memo: [savedMemo[0] || "", savedMemo[1] || "", savedMemo[2] || ""],
      falsificationLines: savedLines,
    };
  }
  return appState.drafts[ticker];
}

function calcDraftOdds(draft) {
  const targetRevM = draft.tamB * 1000 * (draft.sharePct / 100);
  const targetNetM = targetRevM * (draft.netMarginPct / 100);
  const targetMcapM = targetNetM * draft.exitPe;
  const multiple = draft.currentMcapM > 0 ? targetMcapM / draft.currentMcapM : 0;
  return { targetRevM, targetNetM, targetMcapM, multiple };
}

function renderDeepDive() {
  const list = appState.snapshot.research || [];
  if (!list.length) {
    return `<div class="card empty-state">memory/research/ 下暂无通过二层硬筛的标的。</div>`;
  }

  const current = list.find((r) => r.ticker === appState.selectedResearchTicker) || list[0];
  const sc = current.scorecard || {};
  const f4 = sc.dimensions?.["4_financial_floor"] || {};
  const draft = getOrInitDraft(current);
  const odds = calcDraftOdds(draft);
  const l3Rules = appState.snapshot.rules?.layer3_deep_dive || {};
  const minMultiple = Number(l3Rules.min_target_upside_multiple || 10);
  const require10yVeto = Boolean(l3Rules.require_10y_exact_multiple_veto);
  const oddsPassed = odds.multiple >= minMultiple;
  const forwardGatePassed =
    Boolean(String(draft.forwardValuationNote || "").trim()) && draft.longTermRunwayIntact;
  const valuationPassed = require10yVeto ? oddsPassed : forwardGatePassed || oddsPassed;
  const alreadyInCan = sc.status === "APPROVED_FOR_CAN";
  const isLive = appState.mode === "live-rw";

  const ribbon = list
    .map((item) => {
      const st = item.scorecard?.status;
      const dot = st === "APPROVED_FOR_CAN" ? "✓ " : "";
      return `<button type="button" class="pill-btn ${item.ticker === current.ticker ? "is-active" : ""}" data-select-research="${esc(item.ticker)}">${dot}${esc(item.ticker)}</button>`;
    })
    .join("");

  const flRows = draft.falsificationLines
    .map(
      (fl, idx) => `
      <div class="fl-item">
        <div>
          <div class="strong">${esc(fl.id)} · ${esc(fl.description)}</div>
          <div class="mono muted" style="font-size:11.5px;margin-top:2px;">
            ${esc(fl.metric)} ${esc(fl.operator)} ${esc(fl.threshold)} · 连续 ${esc(fl.consecutive_quarters)} 季
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:6px;">
          <span class="badge badge-danger mono">${esc(fl.severity || "RED")}</span>
          <button type="button" class="btn btn-ghost btn-sm" data-remove-fl="${idx}" title="移除">×</button>
        </div>
      </div>
    `
    )
    .join("");

  return `
    <div class="ticker-ribbon">${ribbon}</div>

    <div class="deepdive-layout">
      <!-- Left: Objective Evidence & Valuation Sandbox -->
      <div style="display:flex;flex-direction:column;gap:18px;">
        <section class="card">
          <div class="card-header">
            <div>
              <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                <span class="mono strong" style="font-size:18px;">${esc(sc.ticker)}</span>
                <span class="secondary">${esc(sc.company_name)}</span>
                <span class="badge ${alreadyInCan ? "badge-success" : "badge-warning"} mono">${esc(sc.status)}</span>
              </div>
              <p class="card-desc mono">CIK ${esc(sc.cik)} · 轨道：${esc(sc.layer2_track_qualified)} · Accession ${(f4.filing_refs || ["—"])[0]}</p>
            </div>
            <div style="text-align:right;">
              <div class="muted" style="font-size:11.5px;">当前市值</div>
              <div class="mono strong" style="font-size:18px;">${fmtM(draft.currentMcapM)}</div>
            </div>
          </div>

          <div class="metrics-mini-grid">
            <div class="metric-cell">
              <div class="metric-cell-label">TTM 营收 / YoY</div>
              <div class="metric-cell-value mono">${fmtM(f4.ttm_revenue_usd_m)} · ${fmtPct(f4.revenue_yoy_growth, true)}</div>
            </div>
            <div class="metric-cell">
              <div class="metric-cell-label">毛利率</div>
              <div class="metric-cell-value mono">${fmtPct(f4.gross_margin)}</div>
            </div>
            <div class="metric-cell">
              <div class="metric-cell-label">OCF / FCF</div>
              <div class="metric-cell-value mono">${fmtM(f4.ocf_usd_m)} / ${fmtM(f4.fcf_usd_m)}</div>
            </div>
            <div class="metric-cell">
              <div class="metric-cell-label">短债 / 现金</div>
              <div class="metric-cell-value mono">${f4.short_term_debt_to_cash ?? "0.0"}x</div>
            </div>
            <div class="metric-cell">
              <div class="metric-cell-label">商誉 / 总资产</div>
              <div class="metric-cell-value mono">${fmtPct(f4.goodwill_to_assets)}</div>
            </div>
            <div class="metric-cell">
              <div class="metric-cell-label">年化股本稀释</div>
              <div class="metric-cell-value mono">${fmtPct(f4.annual_share_dilution)}</div>
            </div>
          </div>
        </section>

        <!-- Optional Long-Term Ceiling Reference Sandbox -->
        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">终局天花板参考沙盒（可选推演 · 不再强制填 10 年精确估值）</h2>
              <p class="card-desc">v0.3.0 规则以右侧「1–2 年前瞻估值 + 长期空间不设限」为准，此处仅供感性检验天花板是否够大</p>
            </div>
            <div style="text-align:right;">
              <div class="mono strong" style="font-size:22px;color:${oddsPassed ? "var(--success)" : "var(--text-secondary)"};">${odds.multiple.toFixed(1)}x</div>
              <div style="font-size:12px;" class="muted">理论天花板参考倍数</div>
            </div>
          </div>

          <div class="slider-grid">
            <label class="slider-field">
              <div style="display:flex;justify-content:space-between;">
                <span class="muted">终局可触达市场 TAM</span>
                <span class="mono strong">$${draft.tamB}B</span>
              </div>
              <input type="range" min="10" max="300" step="5" value="${draft.tamB}" data-odds-slider="tamB" />
            </label>

            <label class="slider-field">
              <div style="display:flex;justify-content:space-between;">
                <span class="muted">终局保守市占率</span>
                <span class="mono strong">${draft.sharePct}%</span>
              </div>
              <input type="range" min="2" max="35" step="1" value="${draft.sharePct}" data-odds-slider="sharePct" />
            </label>

            <label class="slider-field">
              <div style="display:flex;justify-content:space-between;">
                <span class="muted">稳态净利率</span>
                <span class="mono strong">${draft.netMarginPct}%</span>
              </div>
              <input type="range" min="5" max="40" step="1" value="${draft.netMarginPct}" data-odds-slider="netMarginPct" />
            </label>

            <label class="slider-field">
              <div style="display:flex;justify-content:space-between;">
                <span class="muted">稳态合理 PE</span>
                <span class="mono strong">${draft.exitPe}x</span>
              </div>
              <input type="range" min="12" max="40" step="1" value="${draft.exitPe}" data-odds-slider="exitPe" />
            </label>
          </div>

          <div style="margin-top:14px;padding:10px 12px;border-radius:8px;background:var(--bg-subtle);border:1px solid var(--border-subtle);display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;font-size:12px;" class="mono">
            <span>终局营收：$${(odds.targetRevM / 1000).toFixed(1)}B</span>
            <span>终局净利：$${(odds.targetNetM / 1000).toFixed(2)}B</span>
            <span>隐含终局市值：$${(odds.targetMcapM / 1000).toFixed(1)}B</span>
          </div>
        </section>

        <!-- Research Evidence Markdown -->
        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">投研证据文档（memory/research/${esc(sc.ticker)}/evidence.md）</h2>
              <p class="card-desc">由 Agent 客观取证并持续追加，所有数据均附 SEC 一手来源</p>
            </div>
          </div>
          <pre class="evidence-pre">${esc(current.evidence_markdown || "尚未生成证据文本")}</pre>
        </section>
      </div>

      <!-- Right: Human-in-the-Loop Gate -->
      <aside class="card sticky-panel" style="display:flex;flex-direction:column;gap:16px;">
        <div style="padding-bottom:12px;border-bottom:1px solid var(--border-subtle);display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
          <div>
            <h2 class="card-title">人类专属决策台 (Human Gate)</h2>
            <p class="card-desc">依据 AGENT_RULES.md，掌舵人主观分与入罐权严格保留给人类</p>
          </div>
          <span class="badge badge-warning mono">${isLive ? "可写回 YAML" : "只读模式"}</span>
        </div>

        <!-- 1-2Y Forward Valuation & Long-Term Runway -->
        <div style="display:flex;flex-direction:column;gap:8px;">
          <div style="display:flex;justify-content:space-between;align-items:center;font-size:12.5px;">
            <span class="strong">1–2 年前瞻估值评估（v0.3.0）</span>
            <span class="badge ${valuationPassed ? "badge-success" : "badge-warning"} mono">${valuationPassed ? "已确认" : "待填写"}</span>
          </div>
          <textarea rows="2" class="textarea" data-draft-field="forwardValuationNote" placeholder="记录当前 1–2 年前瞻估值（如 EV/S、Forward P/E 与增速匹配度），不强行预测 10 年精确市值...">${esc(draft.forwardValuationNote)}</textarea>
          <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-secondary);cursor:pointer;">
            <input type="checkbox" id="chk-runway-intact" ${draft.longTermRunwayIntact ? "checked" : ""} />
            <span>确认行业长期天花板未封死（具备长坡厚雪空间）</span>
          </label>
        </div>

        <!-- Subjective Management Score -->
        <div style="display:flex;flex-direction:column;gap:8px;">
          <div style="display:flex;justify-content:space-between;font-size:12.5px;">
            <span class="strong">维度五 · 掌舵人主观评分（1–10）</span>
            <span class="mono strong" style="color:${draft.humanScore >= 7 ? "var(--success)" : "var(--warning)"};">
              ${draft.humanScore ? `${draft.humanScore} / 10` : "待打分"}
            </span>
          </div>
          <div class="score-grid">
            ${[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
              .map(
                (n) =>
                  `<button type="button" class="score-btn ${draft.humanScore === n ? "is-selected" : ""}" data-human-score="${n}">${n}</button>`
              )
              .join("")}
          </div>
          <textarea rows="2" class="textarea" data-draft-field="humanNotes" placeholder="掌舵人主观备注（诚信、资本配置克制度、长期愿景）...">${esc(draft.humanNotes)}</textarea>
        </div>

        <!-- 3-Sentence Buy Memo -->
        <div style="display:flex;flex-direction:column;gap:6px;">
          <div class="strong" style="font-size:12.5px;">三句话买入备忘录</div>
          <input type="text" class="input" data-memo-idx="0" placeholder="第 1 句：瞄准的存量利润池与数量级需求创新" value="${esc(draft.memo[0])}" />
          <input type="text" class="input" data-memo-idx="1" placeholder="第 2 句：Why Now 拐点与护城河雏形" value="${esc(draft.memo[1])}" />
          <input type="text" class="input" data-memo-idx="2" placeholder="第 3 句：1–2 年估值匹配度、长期空间与掌舵人理由" value="${esc(draft.memo[2])}" />
        </div>

        <!-- Falsification Lines -->
        <div style="display:flex;flex-direction:column;gap:8px;">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span class="strong" style="font-size:12.5px;">可机读逻辑证伪线（供 4_earnings_diff.py）</span>
            <span class="mono muted" style="font-size:11px;">${draft.falsificationLines.length} 条</span>
          </div>
          ${flRows}
          <div class="fl-add-row">
            <select id="new-fl-metric" class="select">
              <option value="revenue_yoy_growth">营收 YoY 增速</option>
              <option value="gross_margin">毛利率</option>
              <option value="ocf_margin">OCF 利润率</option>
              <option value="annual_share_dilution">年化稀释率</option>
              <option value="cash_runway_months">现金跑道(月)</option>
            </select>
            <select id="new-fl-op" class="select mono">
              <option value="<">&lt;</option>
              <option value=">">&gt;</option>
            </select>
            <input id="new-fl-threshold" type="number" step="0.01" value="0.15" class="input mono" />
            <input id="new-fl-q" type="number" min="1" max="4" value="2" class="input mono" title="连续季数" />
            <button type="button" class="btn btn-secondary btn-sm" id="btn-add-fl">+ 添加</button>
          </div>
        </div>

        <!-- Actions -->
        <div style="display:flex;flex-direction:column;gap:8px;padding-top:6px;">
          <button type="button" class="btn btn-secondary" id="btn-save-review" ${!isLive ? "disabled" : ""}>
            保存审阅草稿至 scorecard.yaml
          </button>
          <button type="button" class="btn btn-primary" id="btn-promote-can" ${!isLive || alreadyInCan || !valuationPassed || !draft.humanScore ? "disabled" : ""}>
            ${alreadyInCan ? "已正式装入咖啡罐" : "保存并批准装入咖啡罐 →"}
          </button>
        </div>
      </aside>
    </div>
  `;
}

// ============================================================================
// View 5: Coffee Can Holdings / Watchlist / Post-Mortem
// ============================================================================
function renderLoops() {
  const s = appState.snapshot;
  const subTabs = [
    ["can", `咖啡罐持仓 (${(s.coffee_can || []).length})`],
    ["watchlist", `错杀观察池 (${(s.near_miss || []).length})`],
    ["postmortem", "半年校准 & 报告"],
  ]
    .map(
      ([id, label]) =>
        `<button type="button" class="pill-btn ${appState.loopsSubTab === id ? "is-active" : ""}" data-loops-sub="${id}">${label}</button>`
    )
    .join("");

  let body = "";
  if (appState.loopsSubTab === "can") {
    const holdings = s.coffee_can || [];
    if (!holdings.length) {
      body = `
        <div class="card empty-state">
          <p style="margin:0 0 12px;">咖啡罐当前为空（严格遵守 AGENT_RULES.md，所有标的须经你本人在第三层填完掌舵人主观分与证伪线后方可入罐）。</p>
          <button type="button" class="btn btn-primary btn-sm" data-goto-tab="deepdive">前往第三层审阅 PASS 标的 →</button>
        </div>
      `;
    } else {
      body = holdings
        .map((pos) => {
          const light = pos.earnings_check_history?.last_traffic_light || "GREEN";
          const lightCls =
            light === "GREEN" ? "badge-success" : light === "YELLOW" ? "badge-warning" : "badge-danger";
          const ent = pos.entry || {};
          const valBadge =
            ent.valuation_basis === "1-2y_forward"
              ? `1–2Y 前瞻估值${ent.ev_to_sales_at_entry ? ` (EV/S ${ent.ev_to_sales_at_entry}x` : ""}${ent.forward_pe_at_entry ? `, Fwd PE ${ent.forward_pe_at_entry}x)` : ent.ev_to_sales_at_entry ? ")" : ""}`
              : `预期赔率 ${ent.target_upside_multiple ?? "—"}x`;
          const priceStr = ent.entry_price_usd ? ` · 入罐价 $${Number(ent.entry_price_usd).toFixed(2)}` : "";
          const checkedStr = pos.earnings_check_history?.last_checked_period_end
            ? ` · 最近季报核验 ${pos.earnings_check_history.last_checked_period_end}`
            : "";
          return `
            <article class="card" style="display:flex;flex-direction:column;gap:14px;">
              <div style="display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px;">
                <div>
                  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                    <span class="mono strong" style="font-size:17px;">${esc(pos.ticker)}</span>
                    <span class="secondary">${esc(pos.company_name)}</span>
                    <span class="badge ${lightCls} mono">季报红绿灯：${esc(light)}</span>
                  </div>
                  <div class="card-desc mono">
                    入罐日 ${esc(ent.entered_at)}${priceStr} · 入罐市值 ${fmtM(ent.entry_market_cap_usd_m)} · ${esc(valBadge)} · 掌舵人分 ${ent.management_human_score ?? "—"}/10${esc(checkedStr)}
                  </div>
                </div>
              </div>
              ${
                ent.forward_1_2y_valuation_note
                  ? `<div style="padding:10px 12px;border-radius:8px;background:var(--bg-subtle);border:1px solid var(--border-subtle);font-size:12.5px;color:var(--text-secondary);"><strong style="color:var(--text-primary);">1–2 年前瞻估值与空间记录：</strong>${esc(ent.forward_1_2y_valuation_note)}</div>`
                  : ""
              }
              <div class="grid-2">
                <div style="padding:12px;border-radius:8px;background:var(--bg-subtle);border:1px solid var(--border-subtle);font-size:12.5px;">
                  <div class="strong" style="margin-bottom:6px;">三句话买入备忘录</div>
                  <ol style="margin:0;padding-left:18px;color:var(--text-secondary);display:flex;flex-direction:column;gap:4px;">
                    ${(pos.three_sentence_memo || []).map((m) => `<li>${esc(m)}</li>`).join("")}
                  </ol>
                </div>
                <div style="padding:12px;border-radius:8px;background:var(--bg-subtle);border:1px solid var(--border-subtle);font-size:12.5px;">
                  <div class="strong" style="margin-bottom:6px;">逻辑证伪线（4_earnings_diff.py 自动盯防）</div>
                  <div style="display:flex;flex-direction:column;gap:6px;">
                    ${(pos.falsification_lines || [])
                      .map(
                        (fl) => `
                        <div style="display:flex;justify-content:space-between;gap:8px;">
                          <span class="secondary">${esc(fl.id)} · ${esc(fl.description)}</span>
                          <span class="mono muted">${esc(fl.metric)} ${esc(fl.operator)} ${esc(fl.threshold)}</span>
                        </div>
                      `
                      )
                      .join("")}
                  </div>
                </div>
              </div>
            </article>
          `;
        })
        .join("");
    }
  } else if (appState.loopsSubTab === "watchlist") {
    const items = s.near_miss || [];
    body = `
      <div class="grid-2">
        ${items
          .map((nm) => {
            const st = nm.monitor_state || {};
            const curM = Number(st.last_market_cap_usd_m ?? nm.market_cap_at_entry_usd_m ?? 0);
            const tgtM = Number(nm.targets?.target_market_cap_usd_m ?? 1);
            const gap = ((curM - tgtM) / tgtM) * 100;
            const prog = Math.min(100, Math.round((tgtM / Math.max(curM, 1)) * 100));
            return `
              <article class="card" style="display:flex;flex-direction:column;gap:14px;">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                  <div>
                    <div style="display:flex;align-items:center;gap:8px;">
                      <span class="mono strong" style="font-size:16px;">${esc(nm.ticker)}</span>
                      <span class="secondary" style="font-size:13px;">${esc(nm.company_name)}</span>
                    </div>
                    <div class="card-desc">${esc(nm.near_miss_reason)} · 最近盯盘 ${esc(st.last_checked_at || nm.added_at)}</div>
                  </div>
                  <span class="badge ${st.status === "TRIGGERED" ? "badge-success" : "badge-primary"} mono">${esc(st.status || "WATCHING")}</span>
                </div>

                <div style="padding:12px;border-radius:8px;background:var(--bg-subtle);border:1px solid var(--border-subtle);display:flex;flex-direction:column;gap:8px;">
                  <div class="progress-meta mono">
                    <span>当前市值 ${fmtM(curM)}</span>
                    <span>目标 <= ${fmtM(tgtM)}</span>
                  </div>
                  <div class="progress-track">
                    <div class="progress-fill ${curM <= tgtM ? "is-done" : ""}" style="width:${prog}%;"></div>
                  </div>
                  <div class="progress-meta muted" style="font-size:12px;">
                    <span>距目标差值：${gap > 0 ? "+" : ""}${gap.toFixed(1)}%</span>
                    <span class="mono">防抖连续天数：${st.consecutive_days_below_target || 0}/3</span>
                  </div>
                </div>

                <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
                  <span class="muted" style="font-size:12px;">调整目标触发市值 (USD M)：</span>
                  <div style="display:flex;gap:6px;">
                    <input type="number" class="input mono" style="width:110px;" value="${tgtM}" data-target-input="${esc(nm.ticker)}" ${appState.mode !== "live-rw" ? "disabled" : ""} />
                    <button type="button" class="btn btn-secondary btn-sm" data-save-target="${esc(nm.ticker)}" ${appState.mode !== "live-rw" ? "disabled" : ""}>更新</button>
                  </div>
                </div>
              </article>
            `;
          })
          .join("") || '<div class="card empty-state">错杀观察池为空</div>'}
      </div>
    `;
  } else {
    const pm = s.post_mortem;
    if (!pm) {
      body = `<div class="card empty-state">尚未运行 <code>scripts/5_post_mortem.py</code> 生成复盘快照。</div>`;
    } else {
      const ruleRows = (pm.rule_attributions || [])
        .map(
          (r) => `
          <tr>
            <td class="mono">${esc(r.rule_key)}</td>
            <td class="num-col mono">${r.sample_count}</td>
            <td class="num-col mono">${r.false_negative_count}</td>
            <td class="num-col mono">${fmtPct(r.false_negative_rate)}</td>
            <td class="num-col mono">${fmtPct(r.avg_excess_vs_spy, true)}</td>
            <td class="num-col"><span class="badge ${r.needs_calibration ? "badge-warning" : "badge-success"}">${r.needs_calibration ? "建议校准" : "保持严控"}</span></td>
          </tr>
        `
        )
        .join("");

      const dormantHtml = (pm.dormant_checks || [])
        .map(
          (d) => `
          <div class="action-row">
            <div>
              <strong>${esc(d.slug)}</strong> <span class="muted">（${esc(d.name)}）</span>
              <div class="card-desc">${esc(d.note || "当前跑输大盘，休眠状态机成功过滤概念回撤")}</div>
            </div>
            <span class="badge ${d.breakout_alert ? "badge-warning" : "badge-muted"} mono">180天超额 ${fmtPct(d.avg_180d_excess_vs_spy, true)}</span>
          </div>
        `
        )
        .join("");

      body = `
        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">淘汰规则误杀率归因（相对 SPY / QQQ）</h2>
              <p class="card-desc">复盘日 ${esc(pm.as_of_date)} · 淘汰总数 ${pm.total_rejected_entries}（满 ${pm.min_window_days} 天 ${pm.mature_sample_count} / 未满期 ${pm.immature_sample_count}）</p>
            </div>
            <span class="badge badge-muted mono">严禁自动覆盖 rules.yaml</span>
          </div>
          <div class="table-wrap" style="box-shadow:none;">
            <table class="data-table" style="min-width:640px;">
              <thead>
                <tr>
                  <th>规则键</th>
                  <th class="num-col">样本数</th>
                  <th class="num-col">误杀数</th>
                  <th class="num-col">误杀率</th>
                  <th class="num-col">平均超额 (vs SPY)</th>
                  <th class="num-col">校准判定</th>
                </tr>
              </thead>
              <tbody>${ruleRows || '<tr><td colspan="6" class="empty-state">暂无可统计样本</td></tr>'}</tbody>
            </table>
          </div>
        </section>

        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">休眠行业 (DORMANT) 180 天股价表现复查</h2>
              <p class="card-desc">防止营收滞后导致错过已经启动的新赛道</p>
            </div>
          </div>
          <div class="action-list">${dormantHtml || '<div class="empty-state">无休眠行业</div>'}</div>
        </section>
      `;
    }
  }

  return `
    <div class="pill-group">${subTabs}</div>
    ${body}
  `;
}

// ============================================================================
// Render & Event Delegation
// ============================================================================
function renderCurrentView() {
  const root = document.getElementById("view-root");
  if (!appState.snapshot) return;
  if (appState.activeTab === "overview") root.innerHTML = renderOverview();
  else if (appState.activeTab === "radar") root.innerHTML = renderRadar();
  else if (appState.activeTab === "screener") root.innerHTML = renderScreener();
  else if (appState.activeTab === "deepdive") root.innerHTML = renderDeepDive();
  else if (appState.activeTab === "loops") root.innerHTML = renderLoops();
}

async function handleSaveReview(promoteAfter = false) {
  const ticker = appState.selectedResearchTicker;
  const draft = appState.drafts[ticker];
  if (!ticker || !draft) return;
  const odds = calcDraftOdds(draft);

  const payload = {
    valuation_horizon: "1-2y_forward",
    forward_1_2y_valuation_note: draft.forwardValuationNote,
    long_term_runway_intact: draft.longTermRunwayIntact,
    conservative_10y_target_market_cap_usd_m: odds.targetMcapM,
    implied_upside_multiple: odds.multiple,
    assumptions_summary: `TAM $${draft.tamB}B × ${draft.sharePct}% share × ${draft.netMarginPct}% net margin × ${draft.exitPe}x PE`,
    human_subjective_score: draft.humanScore,
    human_notes: draft.humanNotes,
    three_sentence_memo: draft.memo,
    falsification_lines: draft.falsificationLines,
  };

  const saveResp = await fetch(`/api/research/${encodeURIComponent(ticker)}/human-review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!saveResp.ok) {
    const err = await saveResp.json().catch(() => ({}));
    showToast(`保存失败：${err.detail || saveResp.statusText}`);
    return;
  }

  if (!promoteAfter) {
    showToast(`已保存 ${ticker} 审阅草稿至 memory/research/${ticker}/scorecard.yaml`);
    await loadSnapshot();
    return;
  }

  const promResp = await fetch(`/api/research/${encodeURIComponent(ticker)}/promote`, {
    method: "POST",
  });
  if (!promResp.ok) {
    const err = await promResp.json().catch(() => ({}));
    showToast(`入罐被拦下：${err.detail || promResp.statusText}`);
    return;
  }
  showToast(`已正式装入咖啡罐！memory/coffee_can/${ticker}.yaml 已生成`);
  await loadSnapshot();
  switchTab("loops", "can");
}

function bindGlobalEvents() {
  // Sidebar navigation
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  document.getElementById("btn-refresh").addEventListener("click", async () => {
    await loadSnapshot();
    showToast("已重新加载 memory/ 最新数据");
  });

  // Theme toggle
  const themeBtn = document.getElementById("btn-theme");
  const applyTheme = (theme) => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("cc-theme", theme);
    themeBtn.textContent = theme === "dark" ? "浅色模式" : "深色模式";
  };
  applyTheme(localStorage.getItem("cc-theme") || "light");
  themeBtn.addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
  });

  // Delegated clicks inside #view-root
  const root = document.getElementById("view-root");
  root.addEventListener("click", async (e) => {
    const btn = e.target.closest("button, tr[data-expand-ticker]");
    if (!btn) return;

    if (btn.dataset.gotoTab) {
      switchTab(btn.dataset.gotoTab, btn.dataset.gotoSub || null);
      return;
    }
    if (btn.dataset.radarFilter) {
      appState.radarFilter = btn.dataset.radarFilter;
      renderCurrentView();
      return;
    }
    if (btn.dataset.signalFilter) {
      appState.signalFilter = btn.dataset.signalFilter;
      renderCurrentView();
      return;
    }
    if (btn.dataset.screenerFilter) {
      appState.screenerFilter = btn.dataset.screenerFilter;
      renderCurrentView();
      return;
    }
    if (btn.dataset.loopsSub) {
      appState.loopsSubTab = btn.dataset.loopsSub;
      renderCurrentView();
      return;
    }
    if (btn.dataset.openResearch) {
      e.stopPropagation();
      appState.selectedResearchTicker = btn.dataset.openResearch;
      switchTab("deepdive");
      return;
    }
    if (btn.dataset.expandTicker) {
      const t = btn.dataset.expandTicker;
      appState.expandedScreenerTicker = appState.expandedScreenerTicker === t ? null : t;
      renderCurrentView();
      return;
    }
    if (btn.dataset.selectResearch) {
      appState.selectedResearchTicker = btn.dataset.selectResearch;
      renderCurrentView();
      return;
    }
    if (btn.dataset.humanScore) {
      const draft = appState.drafts[appState.selectedResearchTicker];
      if (draft) {
        draft.humanScore = Number(btn.dataset.humanScore);
        renderCurrentView();
      }
      return;
    }
    if (btn.dataset.removeFl !== undefined) {
      const draft = appState.drafts[appState.selectedResearchTicker];
      if (draft) {
        draft.falsificationLines.splice(Number(btn.dataset.removeFl), 1);
        renderCurrentView();
      }
      return;
    }
    if (btn.id === "btn-add-fl") {
      const draft = appState.drafts[appState.selectedResearchTicker];
      if (!draft) return;
      const metricEl = document.getElementById("new-fl-metric");
      const metric = metricEl.value;
      const metricLabel = metricEl.options[metricEl.selectedIndex].text;
      const op = document.getElementById("new-fl-op").value;
      const th = Number(document.getElementById("new-fl-threshold").value);
      const q = Number(document.getElementById("new-fl-q").value) || 1;
      draft.falsificationLines.push({
        id: `FL-${draft.falsificationLines.length + 1}`,
        description: `连续 ${q} 季度 ${metricLabel} ${op} ${th}`,
        metric,
        operator: op,
        threshold: th,
        consecutive_quarters: q,
        severity: "RED",
      });
      renderCurrentView();
      return;
    }
    if (btn.id === "btn-save-review") {
      await handleSaveReview(false);
      return;
    }
    if (btn.id === "btn-promote-can") {
      await handleSaveReview(true);
      return;
    }
    if (btn.dataset.toggleIndustry) {
      const slug = btn.dataset.toggleIndustry;
      const status = btn.dataset.nextStatus;
      const resp = await fetch(`/api/industries/${encodeURIComponent(slug)}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      if (resp.ok) {
        showToast(`赛道 ${slug} 已切换为 ${status}`);
        await loadSnapshot();
      } else {
        showToast("切换状态失败");
      }
      return;
    }
    if (btn.dataset.promoteSignal) {
      const slug = btn.dataset.promoteSignal;
      const status = btn.dataset.promoteStatus || "DORMANT";
      const resp = await fetch(`/api/quarantine/signals/${encodeURIComponent(slug)}/promote`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      if (resp.ok) {
        showToast(`已收编新赛道 ${slug} (${status}) -> memory/industries/${slug}.yaml`);
        await loadSnapshot();
      } else {
        const err = await resp.json().catch(() => ({}));
        showToast(`收编失败：${err.detail || resp.status}`);
      }
      return;
    }
    if (btn.dataset.dismissSignal) {
      const slug = btn.dataset.dismissSignal;
      const resp = await fetch(`/api/quarantine/signals/${encodeURIComponent(slug)}/dismiss`, {
        method: "POST",
      });
      if (resp.ok) {
        showToast(`已忽略盲扫线索 ${slug}（后续周扫不再重复打扰）`);
        await loadSnapshot();
      } else {
        showToast("忽略失败");
      }
      return;
    }
    if (btn.dataset.saveTarget) {
      const sym = btn.dataset.saveTarget;
      const inp = root.querySelector(`input[data-target-input="${sym}"]`);
      const val = Number(inp?.value);
      if (!val || val <= 0) return;
      const resp = await fetch(`/api/near-miss/${encodeURIComponent(sym)}/target`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_market_cap_usd_m: val }),
      });
      if (resp.ok) {
        showToast(`已更新 ${sym} 目标触发市值为 $${val.toLocaleString()}M`);
        await loadSnapshot();
      }
    }
  });

  // Delegated inputs inside #view-root
  root.addEventListener("input", (e) => {
    const el = e.target;
    if (el.id === "screener-search") {
      appState.screenerQuery = el.value;
      renderCurrentView();
      const ref = document.getElementById("screener-search");
      if (ref) {
        ref.focus();
        ref.setSelectionRange(ref.value.length, ref.value.length);
      }
      return;
    }
    const draft = appState.drafts[appState.selectedResearchTicker];
    if (!draft) return;
    if (el.dataset.oddsSlider) {
      draft[el.dataset.oddsSlider] = Number(el.value);
      renderCurrentView();
      return;
    }
    if (el.dataset.draftField) {
      draft[el.dataset.draftField] = el.value;
      if (el.dataset.draftField === "forwardValuationNote") {
        const promBtn = document.getElementById("btn-promote-can");
        if (promBtn && appState.mode === "live-rw" && draft.humanScore) {
          promBtn.disabled = !(String(el.value || "").trim() && draft.longTermRunwayIntact);
        }
      }
      return;
    }
    if (el.dataset.memoIdx !== undefined) {
      draft.memo[Number(el.dataset.memoIdx)] = el.value;
    }
  });

  root.addEventListener("change", (e) => {
    const el = e.target;
    if (el.id === "chk-runway-intact") {
      const draft = appState.drafts[appState.selectedResearchTicker];
      if (draft) {
        draft.longTermRunwayIntact = Boolean(el.checked);
        renderCurrentView();
      }
    }
  });
}

bindGlobalEvents();
loadSnapshot();
