# 子系统上游管理指南

> 最后更新: 2026-06-26

---

## 三子系统上游仓库

| 子系统 | 本地代码位置 | 上游 fork | 原作者 |
|:-------|:------------|:----------|:-------|
| **ML** (MindLynx-Aistock) | `systems/MindLynx-Aistock/` | `github.com/Mindlx/MindLynx-Aistock.git` | `ZhuLinsen/daily_stock_analysis` |
| **LY** (lynx_vnpy) | `systems/lynx_vnpy/` | `github.com/Mindlx/lynx_vnpy.git` | 独立项目 |
| **AT** (mind_TradingAgent) | `systems/mind_TradingAgent/` | `github.com/Mindlx/mind_TradingAgents.git` | 独立项目 |

fork 源目录：`/home/bluekuma/workspace/{子系统目录}/`

---

## 本地定制代码记录（ML 子系统）

### 高风险文件（每次 sync 后需重点检查）

这些文件包含重要的本地业务逻辑改动，sync 后最可能被覆盖：

| 文件 | 改动内容 | 最后 commit |
|:-----|:---------|:------------|
| `src/market_analyzer.py` | TTL 缓存 `_MARKET_CACHE` + `_cached_call`；`_load_hourly_analysis()` 整点分析注入；`label`→`temperature_label` bug 修复；`get_cached_sector_rankings()` 公开 API；`stock_data` prompt 注入 | 6ca68ce |
| `src/core/market_review.py` | `run_market_review()` 的 `hourly_analysis_text` 参数；treemap 改用 `get_cached_sector_rankings()` 消除二次拉取；`_load_hourly_analysis()` 读取当日整点分析；stock_data 参数 + prompt 自选股操盘建议章节 | 6ca68ce + f7a3d3e |
| `src/config.py` | `setup_env()` 补充加载项目根 `.env` 作为后备 | f623a87 |
| `main.py` | 整点分析 4 次→2 次（移除 10:00/15:00） | 6f69187 |
| `src/notification_sender/wechat_sender.py` | `_send_wechat_message()` 3 次指数退避重试 + 滑动窗口速率限制（18次/60s） | f623a87 |
| `.env` | 删除了独立的 `WECHAT_WEBHOOK_URL`（统一到根 `.env`） | f623a87 |
| `scripts/generate_rating_report.py` | PDF 紧凑格式优化（每只股票从~50 行→2 行），新增市场概况/机构参与度/综合得分，读取共享缓存减少 API 调用 | 51ce101 |
| `scripts/fetch_eastmoney_rating.py` | 盘中数据抓取脚本（新增） | fc30544 |
| `src/core/prompt_shared.py` | 自选股操盘建议 prompt 章节（"八、自选股操盘建议"） | f7a3d3e |
| `src/notification.py` | `save_report_to_file()` 文件名改为 `report_YYYYMMDD_HHMM.md` 防止覆盖 | 6d2d5be |

### 中风险文件

| 文件 | 改动内容 |
|:-----|:---------|
| `src/core/pipeline_data.py` | 数据管道调整 |
| `data_provider/tushare_fetcher.py` | Tushare 优先级调整 |

### 低风险文件（配置/文档类）

| 文件 | 改动内容 |
|:-----|:---------|
| `strategies/*.yaml` | 15 个策略 YAML 文件（上游未改则无需恢复） |
| `requirements.txt` | 依赖调整 |
| `AGENTS.md` / `CLAUDE.md` | 项目 AI 协作规则 |

---

## 同步到 fork 的操作步骤

### 完整流程（首次同步或批量同步）

```bash
# 1. 先看差异
SRC="systems/MindLynx-Aistock"  # 项目中的 ML 目录
DST="/home/bluekuma/workspace/MindLynx-Aistock"  # fork 源目录

# 列出有差异的文件
for f in "src/market_analyzer.py" "src/core/market_review.py" \
         "src/config.py" "src/notification_sender/wechat_sender.py" \
         "src/notification.py" ".env"; do
    diff_lines=$(diff -u "$DST/$f" "$SRC/$f" 2>/dev/null | wc -l)
    echo "$f: $diff_lines 行差异"
done

# 2. 拷贝文件
cp "$SRC/src/market_analyzer.py" "$DST/src/market_analyzer.py"
cp "$SRC/src/core/market_review.py" "$DST/src/core/market_review.py"
# ... 拷贝所有差异文件

# 3. 提交并推送到 fork
cd "$DST"
git add <修改的文件>
git commit -m "sync: 说明同步内容" --no-verify
git push origin main

# 4. 注意：.env 文件被 gitignore 忽略，不提交
```

### 单文件快速同步

```bash
cd /home/bluekuma/workspace/MindLynx-Aistock
git add src/market_analyzer.py
git commit -m "sync: market_analyzer.py — TTL缓存+整点分析注入"
git push origin main
```

### 注意事项

1. fork 仓库的 `pre-commit` hook 依赖 venv，未激活时会失败。始终用 `--no-verify` 跳过
2. `.env` 文件在 fork 仓库中被 gitignore，不要 force-add
3. LY 和 AT 子系统目前无定制代码差异，无需同步
4. 上游 sync（`sync_systems.sh`）后，需按高/中风险文件清单逐项检查并恢复
