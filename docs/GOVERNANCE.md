# 项目治理与环境隔离规范 (GOVERNANCE)

本仓库同时承载**投研代码引擎**与**真实长期投资记忆（`memory/`）**。为支持高频“来回迭代”同时杜绝测试数据污染正式投资决策，本项目采用四层轻量化隔离治理体系。

---

## 1. 四层目录与环境边界

| 区域 | 路径 | Git 追踪 | 用途与变更规则 |
|---|---|---|---|
| **生产环境 (`prod`)** | 根目录 `config/` + `memory/` | 是（核心真相源） | 真实规则、真实赛道状态机、在研标的、错杀观察池、咖啡罐持仓与淘汰回测日志。 |
| **实验沙盒 (`sandbox`)** | `sandbox/` | **否**（`.gitignore` 排除） | 临时实验区。用于测试新参数、批量试跑脚本、在 Web 界面演练“入罐”流程，用完即弃。 |
| **隔离暂存区 (`quarantine`)** | `memory/quarantine/` | 是（待审队列） | 物理隔离三类未核验产出：`anomalies/`（异常数据）、`proposals/`（调参提案）、`signals/`（未验证信号）。 |
| **本地缓存 (`.cache`)** | `.cache/` | **否**（`.gitignore` 排除） | SEC EDGAR / USASpending / yfinance 原始响应缓存，可随时清空重建。 |

---

## 2. 沙盒工作流（零风险来回试错）

凡是涉及**修改 `rules.yaml` 做敏感性测试**、**大批量试跑新股票**或**演练 Web 审批入罐**，统一在沙盒执行：

```bash
# 1. 从当前 prod 克隆一份干净沙盒
python scripts/sandbox_tool.py init   # 或 reset 刷新

# 2. 在沙盒中运行任意脚本或启动沙盒版 Web 工作台（完全不碰正式 memory/）
python scripts/2_quant_screener.py --env sandbox --tickers NVDA AMD
python scripts/run_workbench.py --env sandbox --port 8001

# 3. 对比沙盒改动（如 rules.yaml 调参差异与新增档案）
python scripts/sandbox_tool.py diff

# 4. 确认满意后再合回 prod，并清空沙盒
python scripts/sandbox_tool.py clean
```

也可通过环境变量全局切换：`export COFFEE_CAN_ENV=sandbox`。

---

## 3. Git 分支与自动化防冲突约定

1. **`main` 分支**：永远保持可运行、通过 CI 门禁（`ruff` + `pytest` + `doctor.py`），且仅存放真实投研记忆。
2. **代码/规则迭代分支**：
   - 功能开发：`feat/<short-name>`
   - 策略与参数实验：`exp/<short-name>`（先在 `sandbox/` 验证，确认有效后开 PR 合入 `main` 并记录在 [DECISIONS.md](./DECISIONS.md)）
   - 缺陷修复：`fix/<short-name>`
3. **GitHub Actions 定时任务**：
   - 每日/每周巡检若需要写回 `memory/` 更新，建议通过独立分支 + Pull Request（如 `bot/weekly-sync`）提交，避免与本地人工研究提交产生 `main` 分支冲突。

---

## 4. 质量门禁与体检 (`scripts/doctor.py` & `ci.yml`)

每次 Push / PR 都会由 `.github/workflows/ci.yml` 自动执行三道检查：
1. `ruff check src scripts tests` — 静态代码规范检查
2. `pytest -v` — 单元、XBRL 回归、状态机、红绿灯与 FastAPI 门控测试
3. `python scripts/doctor.py` — 校验所有 YAML/JSON Schema 及 [AGENT_RULES.md](../AGENT_RULES.md) 永久红线（如 `coffee_can/` 严禁缺少掌舵人主观分、严禁赔率 `< 10x`、严禁缺少可机读证伪线）
