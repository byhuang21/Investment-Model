# Coffee Can 自主投研 Agent 系统

一个基于**三层自上而下投研漏斗 + 四大自动化闭环**构建的美股自主投研系统（聚焦 `$300M - $15B` 具备 10 倍赔率潜力的小中盘标的），采用纯免费一手数据源起步（SEC EDGAR XBRL/EFTS、USASpending.gov、yfinance）与 **GitHub Actions 定时巡检 + 本地 Agent 人机协同深挖**的混合架构。

## 核心架构

```text
Layer 1: 赛道主动发现 & 状态机 (DORMANT <-> ACTIVE)
         scripts/1_topic_radar.py  ──▶  memory/industries/*.yaml
                    │ (candidate_tickers)
                    ▼
Layer 2: 双轨量化硬过滤 (SEC XBRL + yfinance)
         scripts/2_quant_screener.py
           ├── PASS      ──▶ memory/research/<TICKER>/ (进入第三层)
           ├── NEAR_MISS ──▶ memory/near_miss_pool/<TICKER>.yaml ──▶ scripts/3_watchlist_monitor.py
           └── REJECTED  ──▶ memory/rejected_log.json            ──▶ scripts/5_post_mortem.py
                    │
                    ▼
Layer 3: 五维深度解剖 (Agent 客观取证 + 人类主观打分与证伪线)
         PENDING_HUMAN_REVIEW ──▶ 人类审批入罐 ──▶ memory/coffee_can/<TICKER>.yaml
                                                        │
                                                        ▼
                                            scripts/4_earnings_diff.py (季度红绿灯体检)
```

> 完整投资哲学、五维模型与 Agent 不可逾越红线见 [AGENT_RULES.md](./AGENT_RULES.md)。

## 目录结构

```text
├── AGENT_RULES.md                 # 永久系统指令与投研宪法
├── config/
│   ├── rules.yaml                 # 行业唤醒、双轨初筛、盯盘防抖、证伪与复盘参数
│   └── data_sources.yaml          # SEC EDGAR / USASpending / yfinance / 通知配置
├── src/coffee_can/                # 公共工程模块（配置、Schema、数据源客户端、存储、通知）
├── scripts/
│   ├── 1_topic_radar.py           # 第一层：赛道雷达与状态机更新
│   ├── 2_quant_screener.py        # 第二层：量化硬过滤与三向分流
│   ├── 3_watchlist_monitor.py     # 闭环：错杀观察池估值盯盘提醒
│   ├── 4_earnings_diff.py         # 闭环：持仓季度财报 Diff 与证伪线红绿灯体检
│   └── 5_post_mortem.py           # 闭环：半年期淘汰池/休眠行业误杀率回测与调参建议
├── memory/
│   ├── industries/                # 赛道状态机档案
│   ├── research/                  # 第三层在研标的（挂起待人类打分）
│   ├── near_miss_pool/            # 错杀观察池（基本面过关、等估值杀跌）
│   ├── coffee_can/                # 正式入罐持仓（含三句话备忘录与可机读证伪线）
│   ├── inputs/transcripts/        # 可选：手动导入的财报电话会实录
│   ├── reports/                   # 自动生成的周报/季报体检/复盘报告
│   └── rejected_log.json          # 淘汰日志（记录当时股价与 SPY/QQQ 基准）
├── .github/workflows/             # GitHub Actions 定时巡检与告警工作流
└── tests/                         # 配置、Schema 与脚本冒烟测试
```

## 快速开始

### 1. 环境准备

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 必填：修改 .env 中的 SEC_USER_AGENT="CoffeeCanResearch your_email@example.com"
```

### 3. 运行基础测试与脚本骨架检查

```bash
pytest
python scripts/1_topic_radar.py --help
python scripts/2_quant_screener.py --help
python scripts/3_watchlist_monitor.py --help
python scripts/4_earnings_diff.py --help
python scripts/5_post_mortem.py --help
```

## 开发路线图

- [x] Phase 0：目录结构、永久指令、配置文件、核心 Schema 与脚本骨架初始化
- [ ] Phase 1：公共数据客户端（`sec_edgar.py`、`market_data.py`、`usaspending.py`）
- [ ] Phase 2：`scripts/2_quant_screener.py` 双轨硬过滤与三向分流实战测试
- [ ] Phase 3：`scripts/1_topic_radar.py` 主动赛道扫描与状态机唤醒
- [ ] Phase 4：`scripts/3_watchlist_monitor.py` 与 `scripts/4_earnings_diff.py` 盯盘/体检闭环
- [ ] Phase 5：`scripts/5_post_mortem.py` 误杀率回测校准 + GitHub Actions 上线
