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

---

## ADR-006：第一层三通道主动盲扫发现引擎
- **日期**：2026-11-25
- **决定**：在 `scripts/1_topic_radar.py --discover` 中加入三通道主动发现引擎（USASpending 全门类 6 位 NAICS 同比暴增盲扫 + SEC EDGAR 近 180 天 S-1 IPO 剔除 SPAC 后按 4 位 SIC 聚类 + 跨行业新兴商业赛道探测网），发现结果统一落盘至 `memory/quarantine/signals/discovered_themes.yaml`，经人类在工作台确认后再晋升至 `memory/industries/`。

---

## ADR-007：`rules.yaml v0.3.0` 第三层估值门控升级为「1–2 年前瞻估值 + 长期空间不设限」
- **日期**：2026-11-25
- **背景**：在首只正式入罐标的 `TMDX` 的人类决策复核中，用户指出 10 年后的精确市值无法可靠预测，强行填写“10 年目标市值 `>= 10x`”容易演变为虚假的精确；实战中真正具备可见度的是 1–2 年的前瞻估值与成长匹配度，以及长期天花板是否尚未封死。
- **决定（经人类明确批准）**：将 `config/rules.yaml` 升级至 `v0.3.0`，设置 `layer3_deep_dive.valuation_gate_mode: "forward_1_2y"` 与 `require_10y_exact_multiple_veto: false`。入罐时强制记录 **掌舵人主观评分 + 1–2 年前瞻估值评估 + 长期空间未封死确认 + 三句话备忘录 + 可机读证伪红线**；原 10x 赔率沙盒保留为可选参考推演工具。

---

## ADR-008：全自动无人值守研究链路（L1 自动建档/唤醒 → L2 增量硬筛 → L3 自动取证 → 体检直推刷新 Pages）
- **日期**：2026-11-25
- **背景**：盲扫发现的新赛道停留在隔离区等人手动收编，且云端定时扫描通过 PR 等待手动合并，导致公开工作台无法自动反映最新发现的新行业与新 `PASS` 标的。
- **决定（经人类明确批准）**：
  1. 在 `config/data_sources.yaml` 新增 `blind_discovery.auto_enroll` 高置信度硬门槛，满足条件的新线索由 `1_topic_radar.py` 自动建档为 `DORMANT` 并立即评估状态机（达标自动唤醒为 `ACTIVE`）；弱噪声继续留守隔离区。
  2. 为 `2_quant_screener.py` 增加 `--only-new --max-tickers --allow-partial-errors` 增量筛选能力，在每周工作流中串联 `L1 -> L2 -> L3 (6_deep_dive_evidence.py --all-drafting) -> Loop 2 (4_earnings_diff.py)`。
  3. 经 `doctor.py` 全量体检通过后，云端机器人直推同步 `memory/` 与 `web/data/snapshot.json` 并自动触发 `deploy_pages.yml` 刷新网页；同时在工作流中硬编码拦截：严禁机器人修改 `config/`、`src/`、`scripts/` 或新建 `memory/coffee_can/` 持仓。


