# 架构与投研规则决策日志 (ADR — Architecture Decision Records)

> 本系统各模块相互关联，迭代过程中会不断来回校准。本文件记录每一项关键架构与规则变更的**背景、实证依据与最终取舍**，防止遗忘历史经验。

---

## ADR-001：美股小中盘赔率宇宙与纯免费一手数据源栈
- **日期**：2026-11-22
- **决定**：聚焦美股为主、目标市值 `\$300M - \$15B`（`NEAR_MISS` 上限 `\$35B`）；财务真相源采用免费官方 **SEC EDGAR CompanyFacts (XBRL)**，政府合同采用 **USASpending.gov**，行情与基准收益采用 **yfinance**；电话会实录采用 `10-Q/10-K MD&A + memory/inputs/transcripts/` 本地手动投喂。
- **依据**：`yfinance` 财报字段存在缺失与口径不一致，SEC XBRL 是可追溯到具体 `Accession Number` 的官方一手真相源，且零订阅成本即可满足长期低换手 Coffee Can 投研需求。

---

## ADR-002：SEC XBRL 实战防污染三项加固
- **日期**：2026-11-22
- **实证来源**：对 `POWL / AXON / CAVA / FIX` 等真实美股财报实测中发现 3 类真实 XBRL 陷阱：
  1. **拆股伪稀释（`POWL` 3 拆 1）**：封面页 `dei:EntityCommonStockSharesOutstanding` 不追溯拆股，导致跨年比较出现 `+202%` 虚假稀释。
  2. **亏损季反稀释跳变（`AXON`）**：GAAP 亏损季稀释股本被迫等于基本股本，盈利季恢复期权计入会制造虚假高稀释。
  3. **过期毛利标签（`CAVA`）与 `10-Q` 误标全年跨度（`FIX`）**：公司切换披露口径后旧标签残留，以及个别 `10-Q` 误标 364 天覆盖 `10-K` 年报事实。
- **决定**：
  - 股本稀释优先读取**同一份财报内可比期**的 `WeightedAverageNumberOfSharesOutstandingBasic`（ASC 260 强制拆股追溯且免疫反稀释跳变）；
  - 资产负债表与毛利标签加入 45 天时效校验；
  - 禁止 `10-Q` 写入/覆盖 330–385 天全年跨度事实。

---

## ADR-003：`rules.yaml v0.2.0` 增设轨道 A2（经营杠杆复利子轨）
- **日期**：2026-11-22
- **实证来源**：跨行业 21 只美股实证研究显示，一刀切 `毛利率 >= 30%` 误杀了工业电气化（`IESC` 26.6%）、直营连锁餐饮（`CAVA` 24.4%）与支付混合 SaaS（`TOST` 27.0%）三类高现金流、低稀释的强复利商业模式。
- **决定**：保留公共底线 `毛利率 >= 20%`，在轨道 A 增设 **A2 经营杠杆复利子轨**（`毛利率 >= 20%` 且 `营收 YoY >= 20%` 且 `OCF 利润率 >= 8%`），同时严格保留 `稀释率 <= 6%`、`商誉/总资产 <= 35%` 与 `增速 >= 12%` 三道铁律。

---

## ADR-004：零构建双模式 Web 投研工作台（FastAPI + 静态导出）
- **日期**：2026-11-24
- **决定**：采用 `FastAPI + 原生 HTML/CSS/ES6`（零 `node_modules` 构建负担），支持两种运行形态无缝切换：本地读写模式（直接写回 `memory/*.yaml` 并由服务端强制执行 `AGENT_RULES.md` 人类审批红线）与静态快照模式（`web/data/snapshot.json`，可零成本部署至 GitHub Pages / Vercel）。

---

## ADR-005：四层隔离治理体系（Sandbox + Quarantine + CI + Doctor）
- **日期**：2026-11-24
- **决定**：引入 `sandbox/`（`.gitignore` 隔离的本地实验区 + `sandbox_tool.py`）、`memory/quarantine/`（异常数据/调参提案/未验证信号暂存区）、`.github/workflows/ci.yml` 与 `scripts/doctor.py`（自动校验 Schema 与 `AGENT_RULES` 永久红线）。
