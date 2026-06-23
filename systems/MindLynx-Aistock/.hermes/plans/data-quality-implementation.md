# 数据链路优化实施方案

> **目标:** 解决数据搜集/清洗/治理/管理中的7个已知问题（排除回测窗口调整）
>
> **原则:** 从高impact低effort开始，快速落地，逐步完善

---

## 依赖关系

```
Phase 1 ──── 全部独立，可并行
├── T1: SearXNG噪音过滤      🔴 ~15min
├── T2: 公告覆盖排查          🔴 ~15min
└── T3: published_date补全    🟠 ~15min

Phase 2 ──── 独立，可并行
├── T4: Tushare Token健康检查  🟠 ~10min
├── T5: DB数据过期策略         🟡 ~20min
└── T6: DB定期VACUUM          🟢 ~5min

Phase 3 ──── 独立
└── T7: alert框架落地         🟡 ~30min
```

---

## Phase 1: 数据质量快速修复（~45min）

### T1: SearXNG结果过滤

**现状:** A股股票的SearXNG搜索结果中，英文噪音内容直接入库（Dolls Kill ×20, Hy-Vee ×3, AASTOCKS ×6），污染新闻情报库。

**方案:** 在 `save_news_intel` 被调用前（search_service.py），对A股中文股票的搜索结果增加中文相关性过滤。

**文件:** `src/search_service.py`

**改动:**
- 在 `search_comprehensive_intel()` 返回结果之前，增加一个 `_filter_relevant_results(response, stock_code)` 函数
- 对非港股/美股的中文股票标题/摘要做中文检测，过滤纯英文噪音

**要点:**
- 只对A股（CN市场）生效，不误伤美股/港股的英文查询
- 用正则检测中文字符 `[\u4e00-\u9fff]`，标题+摘要均无中文则丢弃
- 保留部分中文内容的结果（如标题含中文、摘要含中文）
- 日志记录过滤数量方便监控

**验证:**
- 重构后对前5只A股跑一次 `search_comprehensive_intel`，对比过滤前后噪音减少
- 观察 news_intel 表不再出现 "Dolls Kill" 类条目

---

### T2: 公告覆盖排查

**现状:** news_intel 表显示只有6条公告，但cninfo API代码看了没问题。需要实际验证 cninfo 是否正常工作，排查是 event_monitor 没跑对、还是结果没入库。

**方案:** 实际执行一次cninfo API调用和存储链路的端到端验证。

**验证步骤（不做代码改动，先查明原因）:**
1. 手动调用 `CninfoFetcher.fetch('000592')` 看是否有返回
2. 检查 event_monitor 的 cron 是否正常运行（schedule配置、last_run、last_status）
3. 检查公告抓取后的 `save_news_intel` 调用（event_monitor处理事件后是否存库）
4. 如果 cninfo 正常但没入库，修复存储链路；如果 cninfo 本身返回空，调整查询参数

**预期结果:**
- 确认问题根因：API、调度、存储三选一
- 修复后验证至少2只股票有公告入库

---

### T3: published_date 补全

**现状:** news_intel 中 50.6%（276/545）无 published_date，主要是 SearXNG 搜索结果。影响去重精度和时间线判断。

**方案:** 在保存 news_intel 时，对 `published_date IS NULL` 的结果用 `fetched_at` 兜底。

**文件:** `src/storage.py` 中 `save_news_intel` 方法（~line 1012）

**改动:**
```python
# 在 published_date 赋值后增加兜底
published_date = self._parse_published_date(item.published_date)
if published_date is None:
    # 兜底：使用抓取时间作为近似发布时间
    published_date = datetime.now()
```

**验证:**
- 重构后抓取一条新闻，确认 published_date 不再为 NULL

---

## Phase 2: 运维体系搭建（~35min）

### T4: Tushare Token 健康检查

**现状:** 87% 日线数据来自 Tushare，Token 过期会导致全面数据获取失败。目前无任何主动监控。

**方案:** 创建一个 no_agent cron 脚本，每周一9:00 检查 Tushare API 是否正常响应。

**文件:** `scripts/check_tushare_token.sh`

**脚本逻辑:**
```bash
#!/bin/bash
# 使用 Python 调 Tushare API 做一个简单查询
source /path/to/.venv/bin/activate
python -c "
import tushare as ts
from src.config import get_config
pro = ts.pro_api(get_config().tushare_token)
# 查询一只股票的最新日线 - 最轻量的可用性检查
df = pro.daily(ts_code='000001.SZ', start_date='20260101', fields='trade_date')
if df is not None and not df.empty:
    print(f'Tushare OK, latest: {df.iloc[0][\"trade_date\"]}')
else:
    print('Tushare ERROR: empty response')
    exit(1)
" 2>&1
```

**验证:**
- 手动运行脚本，确认输出日期或错误
- 确认 cron 配置正确

---

### T5: DB 数据过期策略

**现状:** llm_usage 2683行、news_intel 545条、analysis_history 647条持续增长，无任何清理。SQLite 长期不清理会碎片化影响查询性能。

**方案:** 创建一个维护脚本，每周运行一次，按 TTL 清理旧数据。

**文件:** `scripts/db_maintenance.py`

**TTL策略:**
| 表 | TTL | 原因 |
|:---|:---|:------|
| llm_usage | 90天 | 成本追踪，不需要永久保留 |
| news_intel | 30天 | 新闻情报过期快，滚动保留最新 |
| fundamental_snapshot | 90天 | 基本面快照，滚动保留 |
| analysis_history | 180天 | 分析记录，保留半年足够 |

**实现要点:**
- 使用 `DELETE FROM ... WHERE created_at < datetime('now', '-N days')`
- 执行后 `VACUUM` 回收空间
- 日志记录每次清理的行数
- 放到 cron 每周日凌晨执行

---

### T6: DB 定期 VACUUM

**现状:** SQLite 从不 VACUUM，删除数据后空间不回收，查询性能随碎片增加下降。

**方案:** 在 `scripts/db_maintenance.py` 中集成 VACUUM 逻辑，或者单独一个简单的 cron。

**改动:** 直接在 `scripts/db_maintenance.py` 末尾加入：
```python
# VACUUM 回收空间
logger.info(f"VACUUM 前 DB 大小: {os.path.getsize('data/stock_analysis.db')/1024:.0f} KB")
conn.execute("VACUUM")
logger.info(f"VACUUM 后 DB 大小: {os.path.getsize('data/stock_analysis.db')/1024:.0f} KB")
```

**验证:**
- T5+T6 合并执行后，确认 cron 日志中有清理行数和 VACUUM 前后大小的记录

---

## Phase 3: 基础设施增强（~30min）

### T7: alert 框架落地

**现状:** 4张 alert 表（rules, triggers, notifications, cooldowns）全部为空，熔断器告警只写到日志，没有进入 alert 体系。框架代码存在但没被使用。

**方案:** 将熔断器（circuit breaker）的告警接入 alert 框架，作为最小可行用例。

**文件:** `data_provider/realtime_types.py`（熔断器所在处）

**改动:**
- 在 `CircuitBreakerState.record_failure()` 中，当连续失败达到阈值时，调用 alert 框架写入 alert_triggers 表
- 使用最低 severity（"info"），避免弹窗打扰
- 保持熔断器本身逻辑不变，只增加告警侧写

**要点:**
- 不要引入新的依赖或通知通道，只写入 DB 表
- 给后续其他模块（SearXNG 失败、数据源切换）留一个 hook pattern 可复制
- 日志记录 alert trigger 写入情况

**验证:**
- 手动触发一次 circuit breaker failure → 检查 alert_triggers 表是否有对应记录

---

## 执行顺序建议

```
T1 ──── (SearXNG过滤)     → 快，收益高
  ↓
T2 ──── (公告排查)         → 查明就修
  ↓
T3 ──── (published_date)   → 小改动
  ↓
T4+T5+T6 ──── (运维体系)   → 可以一起做
  ↓
T7 ──── (alert框架)       → 最复杂，放最后
```

**合计预估工时:** ~1.5h-2h
