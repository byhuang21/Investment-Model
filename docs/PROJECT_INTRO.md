# Coffee Can 自主投研 Agent 系统 — 项目简介与使用说明

## 一、这是什么项目？

**Coffee Can OS** 是一套面向美股小中盘（`\$300M – \$15B`）、追求 10 倍以上长期复利赔率的**自主投研与决策辅助系统**。它把经典的“咖啡罐（Coffee Can）”投资哲学——**选对赛道、硬控财务底线、重赔率、人机协同看掌舵人、低换手长期持有、只在核心逻辑被证伪时卖出**——工程化为一套零订阅成本、数据可追溯、可自我校准的自动化投研流水线与交互式 Web 工作台。

---

## 二、核心设计：三层漏斗 + 四大闭环

1. **第一层：选市场与行业（`scripts/1_topic_radar.py`）**
   - 自动扫描 **USASpending.gov** 联邦合同、**SEC EDGAR EFTS** 招股书/重大公告集群、巨头 CapEx 增速及本地电话会目录。
   - 内置 `DORMANT ↔ ACTIVE` 行业状态机与 30 天冷却防抖：规模未到拐点的概念赛道先休眠，一旦营收/增速/订单触发唤醒线自动激活，并映射候选股票池。

2. **第二层：量化硬筛分流（`scripts/2_quant_screener.py`）**
   - 以 **SEC EDGAR CompanyFacts (XBRL)** 为财务真相源（已内置防拆股伪稀释、防反稀释跳变、防过期标签与 `10-Q` 脏数据加固），`yfinance` 补充实时市值与行情。
   - 按 [config/rules.yaml](../config/rules.yaml)（当前 `v0.2.0`）执行公共底线（真实营收、毛利率、现金跑道、短债/现金、商誉占比、年化稀释率 `<= 6%`）+ 双轨筛选（**A1 高毛利复利 / A2 经营杠杆复利 / B 高毛利高成长**），完成三向分流：
     - `PASS` → 进入 `memory/research/<TICKER>/`
     - `NEAR_MISS` → 基本面过关但偏贵，进入 `memory/near_miss_pool/` 等杀估值
     - `REJECTED` → 写入 `memory/rejected_log.json`（锚定当日 `SPY/QQQ` 收盘价供事后回测）

3. **第三层：五维深挖与人类审批门控（`scripts/6_deep_dive_evidence.py` + Web 工作台）**
   - 自动解析近 24 个月 **SEC Form 4 XML**（精准区分公开市场真金白银买入 `Code P`、计划外卖出与 `10b5-1` 预设减持，剔除常规税扣噪音）及近 3 财年 `10-K` 资本配置记录（R&D / CapEx / 并购 / 回购 / 分红 / SBC）。
   - **严格人机边界（[AGENT_RULES.md](../AGENT_RULES.md)）**：Agent 只做客观取证并挂起至 `PENDING_HUMAN_REVIEW`，**掌舵人主观打分（1–10）、10x 赔率拍板、三句话备忘录与可机读证伪线必须由人类亲自完成**后方可装入 `memory/coffee_can/`。

4. **四大自动化反馈闭环**
   - **赛道唤醒**：`1_topic_radar.py`（每周）
   - **观察池盯盘**：`3_watchlist_monitor.py`（每日，连续 3 交易日跌破目标市值触发 + 30 天冷却 + 额外跌 15% 破例提醒）
   - **季度财报红绿灯体检**：`4_earnings_diff.py`（每周，检测新 `10-Q/10-K` 逐条比对持仓证伪线输出 `GREEN / YELLOW / RED`）
   - **半年规则校准复盘**：`5_post_mortem.py`（每半年，相对 `SPY/QQQ` 统计各规则误杀率，调参提案写入 `memory/quarantine/proposals/` 等待人工审批）

---

## 三、交互形态与安全治理

- **双模式 Web 工作台（`web/` + FastAPI）**：
  - 本地运行 `python scripts/run_workbench.py`：实时读写 `memory/` YAML/JSON，支持 10x 赔率滑块沙盒、打分与一键入罐；
  - 云端零成本只读看板：通过 `.github/workflows/deploy_pages.yml` 自动导出静态快照部署到 GitHub Pages，随时随地看红绿灯与观察池。
- **四层隔离治理（[GOVERNANCE.md](./GOVERNANCE.md)）**：
  - `sandbox/`：一键克隆隔离沙盒（`python scripts/sandbox_tool.py init` + `--env sandbox`），零风险测试新策略与演练入罐；
  - `memory/quarantine/`：物理隔离异常数据、调参提案与未核验线索；
  - `ci.yml` + `scripts/doctor.py`：每次 Push / PR 自动校验 `ruff`、全量单测与 `AGENT_RULES.md` 红线；
  - 定时巡检通过独立 `bot/*` Pull Request 写回 `memory/`，避免与本地人工研究产生冲突。

---

## 四、日常常用命令速查

```bash
# 1. 初始化环境（首次）
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # 填入 SEC_USER_AGENT="CoffeeCanResearch 你的邮箱"

# 2. 启动本地交互式 Web 工作台（读写模式）
python scripts/run_workbench.py

# 3. 零风险沙盒试错
python scripts/sandbox_tool.py init
python scripts/run_workbench.py --env sandbox --port 8001
python scripts/sandbox_tool.py clean

# 4. 手动运行各层投研流水线
python scripts/1_topic_radar.py --discover
python scripts/2_quant_screener.py --tickers POWL TMDX IESC
python scripts/6_deep_dive_evidence.py --all-drafting
python scripts/3_watchlist_monitor.py
python scripts/4_earnings_diff.py
python scripts/5_post_mortem.py --include-immature

# 5. 一键全仓体检与测试
python scripts/doctor.py && pytest -v
```

## 五、云端无人值守上线（GitHub 仓库 2 步配置）
1. 在 GitHub 仓库 `Settings → Secrets and variables → Actions` 新增 `SEC_USER_AGENT`（必填，格式：`CoffeeCanResearch 你的邮箱`），按需配置 `ALERT_WEBHOOK_URL` 或 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`；
2. 在 `Settings → Pages → Build and deployment → Source` 选择 **GitHub Actions**，推送 `main` 后即自动发布只读 Web 看板。
