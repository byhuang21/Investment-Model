# 隔离暂存区 (`memory/quarantine/`)

本目录用于物理隔离**尚未经过人工核验或存在异常的外部/自动产出**，防止脏数据、低置信度信号或未经审批的参数修改污染正式投研记忆库（`memory/industries/`、`memory/research/`、`memory/coffee_can/`、`config/rules.yaml`）。

## 三条隔离通道

| 子目录 | 写入方 | 用途与晋升规则 |
|---|---|---|
| `anomalies/` | `2_quant_screener.py` / `4_earnings_diff.py` | 财报抓取失败、SEC XBRL 关键字段缺失或口径异常的诊断记录。人工排查修复后手动删除。 |
| `proposals/` | `5_post_mortem.py` | 半年期误杀回测触发的 `config/rules.yaml` 调参提案。严禁自动覆盖配置，须经人类确认后手动合入 `config/rules.yaml` 并归档提案。 |
| `signals/` | 外部信号适配器 / AI 发现 | 尚未核实的外部新赛道/新闻/电话会线索。验证具备“大存量利润池 + 需求创新 + 赢家通吃”三要素后，晋升至 `memory/industries/<slug>.yaml`。 |

## 体检监控
运行 `python scripts/doctor.py` 会自动统计本目录待处理条目数并给出提醒。
