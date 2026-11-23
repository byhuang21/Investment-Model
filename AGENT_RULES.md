# Coffee Can 自主投研 Agent 系统 — 永久系统指令 (AGENT_RULES)

> 本文件是本项目的**最高宪法与永久行为约束**。任何在此工作区运行的 AI Agent 或自动化流程都必须严格遵守本文件定义的投资哲学、三层漏斗、状态机流转、人机协作边界与数据合规准则。

---

## 0. 核心定位与不可逾越的红线

本系统服务于**美股为主、聚焦小中盘（初始目标市值区间 `$300M - $15B`）、追求 10 倍以上长期复利赔率**的“咖啡罐（Coffee Can）”投资哲学：买入极少数高确定性长期复利标的，装入罐中长期持有，只在**预设的量化逻辑证伪线被击穿**时卖出。

### 绝对红线（Hard Guardrails）
1. **严禁编造数据**：所有财务数字、合同金额、内部人交易、管理层过往指引必须来自可追溯的一手来源（SEC EDGAR Filing Accession Number / 报告期、USASpending Award ID、或 `memory/inputs/` 下的人类导入文本）。缺失数据必须显式标记为 `MISSING` 或 `DATA_UNAVAILABLE`，严禁估算后伪装成事实。
2. **掌舵人维度严禁越权打分**：在第三层“维度五：掌舵人”中，Agent **只负责客观取证**（过去 2–3 年指引兑现率、资本配置历史、Form 4 内部人增减持），**绝对不得**替人类填写 `human_subjective_score` 或跳过人类审批直接将标的写入 `memory/coffee_can/`。
3. **规则参数修改必须经人类确认**：`5_post_mortem.py` 只能输出 `config/rules.yaml` 的校准建议报告（Diff Proposal），**不得自动覆盖** `config/rules.yaml`。
4. **合规访问数据源**：调用 SEC EDGAR API 必须携带合法 `User-Agent`（通过环境变量 `SEC_USER_AGENT` 注入），且严格遵守限速（默认 `<= 8 req/s`）。

---

## 1. 自上而下三层漏斗架构

### 第一层：选市场与行业（主动发现 + 状态机）
- **目标**：不依赖人类手动输入热门概念，通过定期扫描一手信号源，发掘同时具备以下三特征的新兴赛道：
  1. **大存量利润池**（瞄准庞大低效的既有市场）
  2. **正在发生需求满足创新**（数量级效率提升或成本下降）
  3. **赢家通吃基因**（边际成本递减、网络效应、高转换成本或生态锁定）
- **免费一手信号源**：
  - `USASpending.gov`：政府/国防/能源/基建大额合同与新晋中标方异动
  - `SEC EDGAR (S-1 / 8-K / 10-Q / 10-K EFTS 全文检索)`：新股招股书、重大合同公告、巨头 CapEx 与业务关键词词频突增
  - `memory/inputs/transcripts/`：人类可选导入的巨头财报电话会实录
- **行业状态机**（持久化于 `memory/industries/<industry_slug>.yaml`）：
  - `DORMANT`（休眠）：商业逻辑成立但当前市场规模过小或拐点未至。必须绑定**可量化唤醒条件**（如头部公司合计 TTM 营收突破阈值、连续 2 季度行业增速超阈值、政府订单额突破阈值）。
  - `ACTIVE`（激活）：满足唤醒条件或初始即达到规模，允许进入赛道拆解并维护 `candidate_tickers`（通过 SEC 全文检索、SIC/NAICS、招股书竞争对手列表及主题 ETF 成分股映射生成）。
  - `ARCHIVED`（归档）：赛道被证伪或彻底商品化，保留历史记录供复盘。
- **防抖机制**：行业状态切换需记录触发证据与时间戳，避免因单一季度的异常波动频繁在 `DORMANT` 与 `ACTIVE` 间抖动。

---

### 第二层：量化初筛股票（Python 脚本硬过滤）
- **数据分工**：
  - 财务真相源：**SEC EDGAR CompanyFacts API（XBRL）**
  - 行情与市值补充：**yfinance**（实时/最新市值、价格与基准指数 SPY/QQQ）
- **双轨硬过滤逻辑**（参数见 `config/rules.yaml`）：
  - **公共底线**：真实营收门槛、毛利率为正或呈改善趋势、现金跑道 `>= 24` 个月（或自由现金流/经营现金流为正）、无致命短期偿债危机、商誉占总资产比例可控、**年化股本稀释率（SBC/稀释股本增速）可控**。
  - **轨道 A（稳健复利轨）**：已实现正向 OCF/FCF 且保持健康营收增长。
  - **轨道 B（高毛利高成长扩张轨）**：允许短期 FCF 为负，但必须同时满足高毛利率、高营收 YoY 增速以及充足现金跑道（防止误杀早期 10 倍潜力股）。
- **三向分流**：
  1. `PASS`：基本面、财务底线与市值赔率区间全部过关 → 自动在 `memory/research/<TICKER>/` 初始化第三层研究包骨架。
  2. `NEAR_MISS`（错杀观察池）：基本面与财务硬指标全部过关，**仅因当前市值偏贵/赔率不足被挡下** → 写入 `memory/near_miss_pool/<TICKER>.yaml`，记录目标触发市值与估值锚点，由盯盘脚本持续跟踪。
  3. `REJECTED`：未通过财务底线或核心硬指标 → 追加记录到 `memory/rejected_log.json`，强制记录：淘汰日期、具体触犯规则、核心财务快照、**当时股价及当日 SPY/QQQ 收盘价**（用于半年期相对收益回测误杀率）。

---

### 第三层：五维投资模型深度解剖（Human-in-the-Loop）
仅对 `PASS` 标的启动。Agent 需按以下五维生成 `memory/research/<TICKER>/evidence.md` 与 `scorecard.yaml`：

1. **维度一：需求满足创新（决定上限）**
   - 瞄准的存量利润池规模与痛点
   - 产品/技术是否带来数量级（3x–10x）的效率提升或成本下降
   - 旧巨头为何因“既得利益/创新者窘境”不敢或不能跟进
2. **维度二：市场正在形成（决定时机 / Why Now）**
   - 促使拐点不可逆的外部催化（法规、成本曲线拐点、基础设施成熟、供应链重构）
   - 行业渗透率所处阶段（优先寻找 5%–20% 加速拐点）
3. **维度三：竞争格局正在形成（护城河雏形）**
   - 边际成本递减、网络效应、高转换成本、数据飞轮或独占牌照迹象
   - 份额集中度变化与毛利率/留存率（NRR/GRR）佐证
4. **维度四：公司财务底线（一票否决项）**
   - 单位经济模型（Unit Economics）拆解
   - OCF / FCF 烧钱速度与净现金跑道压力测试
   - 债务到期结构（短债/可转债）与并购商誉/无形资产减值风险
   - SBC 股权激励占营收比重与过去 3 年稀释股本复合增速
5. **维度五：掌舵人（客观取证，主观留给人类）**
   - Agent 仅整理客观证据表：
     - 过去 2–3 年管理层业绩指引/公开承诺 vs 实际兑现率
     - 资本配置记录（研发投入产出、并购成败、回购/增发时机）
     - SEC Form 4 内部人真金白银增减持与创始人持股比例
   - **挂起机制**：完成后将 `scorecard.yaml` 状态设为 `PENDING_HUMAN_REVIEW`，明确提示人类审核并填写：
     - `management.human_subjective_score`（主观评分与备注）
     - `decision.three_sentence_memo`（三句话买入备忘录）
     - `decision.falsification_lines`（1–2 条**可量化、可机读**的逻辑证伪线）
- **附加进退纪律**：
  - **市值赔率（一票否决）**：必须基于保守终局市占率与净利率测算，具备 **10 倍以上（>= 10x）** 长期空间。
  - **正式入罐**：人类审阅并批准后，由研究包晋升为 `memory/coffee_can/<TICKER>.yaml`（状态 `IN_CAN`）。

---

## 2. 四大自动化闭环

| 闭环 | 脚本 | 频率建议 | 核心动作 |
|---|---|---|---|
| 1. 赛道发现与唤醒 | `scripts/1_topic_radar.py` | 每周 | 扫描 USASpending / SEC EDGAR 信号，更新 `memory/industries/` 状态机与候选股池 |
| 2. 错杀观察池盯盘 | `scripts/3_watchlist_monitor.py` | 每个交易日 | 检查 `memory/near_miss_pool/` 实时市值，满足防抖（连续 N 日达标）且不在冷却期内时触发推送提醒 |
| 3. 季度财报体检 Diff | `scripts/4_earnings_diff.py` | 每周 / 财报季每日 | 检测 `memory/coffee_can/` 持仓最新 10-Q/10-K，自动拉取 XBRL 指标逐条比对 `falsification_lines`，输出 `GREEN / YELLOW / RED` 红绿灯报告 |
| 4. 半年规则校准复盘 | `scripts/5_post_mortem.py` | 每半年 | 回测 `rejected_log.json` 与 `DORMANT` 行业相对 SPY/QQQ 的超额表现，统计规则误杀率，生成 `config/rules.yaml` 调参建议报告 |

---

## 3. 文件与状态流转约定

- 所有持久化状态以 Git 友好的 **YAML / JSON / Markdown** 存放在 `memory/` 下，确保每次决策可回溯、可审计、可 diff。
- 命名规范：
  - 行业文件：`memory/industries/<lowercase_snake_slug>.yaml`
  - 在研目录：`memory/research/<UPPER_TICKER>/`
  - 错杀观察池：`memory/near_miss_pool/<UPPER_TICKER>.yaml`
  - 咖啡罐持仓：`memory/coffee_can/<UPPER_TICKER>.yaml`
  - 淘汰日志：`memory/rejected_log.json`（append-only 历史记录）
- 模板文件以下划线 `_template` 开头，所有扫描与监控脚本必须自动忽略以 `_` 开头的模板文件。
