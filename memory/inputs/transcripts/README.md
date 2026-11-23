# 财报电话会实录手动导入目录

由于高质量的财报电话会（Earnings Call Transcript）问答实录没有稳定免费的官方 API，本系统在免费起步阶段采用：
1. 自动扫描 SEC EDGAR 的 `10-Q / 10-K` MD&A 与 `8-K` 财报新闻稿；
2. 若你有获取到的巨头或目标公司电话会文字稿，可直接放入本目录，供 `1_topic_radar.py` 与第三层研究读取。

## 建议文件命名规范
```text
<TICKER>_<YYYY>Q<1-4>_transcript.txt
# 或 .md，例如：
NVDA_2026Q3_transcript.md
MSFT_2026Q2_transcript.txt
```
