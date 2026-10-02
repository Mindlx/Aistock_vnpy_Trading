# 数据层日期格式污染修复 (2026-10-02)

> 类型: 数据完整性缺陷 / 根因修复
> 影响: `capital_flows` 与 `unified_cache/ohlcv_cache.db` 读取排序失真

## 现象

巡检数据完整性时发现两张表日期格式混存:

| 表 | 格式 A | 格式 B | 重复(同日双格式) |
|----|--------|--------|:---:|
| `data_warehouse.db :: capital_flows` | `20260610`(tushare, 195) | `2026-06-17`(akshare, 874) | 175 |
| `unified_cache/ohlcv_cache.db :: daily_ohlcv` | `20251226`(sina, 7672) | `2021-05-31`(yfinance, 13681) | 5925 |

## 根因

不同数据源写入日期格式不统一 (sina/pytdx/tushare 写 `YYYYMMDD`, akshare/efinance/yfinance 写 `YYYY-MM-DD`), 写入端**不做规范化**。SQLite 以字符串比较:
`'20260930' > '2026-09-30'` (位置4 `'0'(0x30) > '-'(0x2D)`)。

## 影响 (决定性实测)

- **capital_flows**: `ORDER BY date DESC LIMIT 30` 返回**乱序** — 600372 显示"最新=20260901"(实为9/1), 真实最新 `2026-09-24`。消费方: mind_TradingAgent `capital_flow_tracker`。
- **ohlcv_cache**: `days≤120` 侥幸正常(compact 2026 行占满窗口); `days≥365` 混入 hyphen 老行 → 乱序。这正是 10/1 `fix_backtest_pollution.py` 根因; 但该脚本只清洗**副本**供回测, 共享 cache 未修 → **复发**。

## 处置

规范格式按各表消费者约定: `capital_flows`/`ohlcv_cache` → hyphen (`YYYY-MM-DD`)。

```
capital_flows: 删除全部 compact(tushare) 行 — 与 akshare 同日值差异达千倍(单位不一致)
ohlcv_cache:   删除有 hyphen 孪生的 compact 行(5925); 转换独有 compact 行 → hyphen(1747)
```

- 清洗前已备份: `data/backup/{data_warehouse.db,ohlcv_cache.db}.bak_20261002_165516`
- **2026 年孪生值 0 差异** → 清洗对消费者相关窗口值无损 (923 处差异全在 2024-12~2025-12 旧区间)。
- 执行后: `capital_flows` 874 hyphen / 0 compact / 0 重复; `ohlcv_cache` 15428 hyphen / 0 compact / 0 重复。

## 根因修复 (防复发)

写入端统一规范化日期:
- `services/data_warehouse/storage.py`: 新增 `_norm_date_cn()`; 应用于 `upsert_ohlcv`/`query_ohlcv`/`update_ohlcv_turnover`/`upsert_capital_flows`/`upsert_chip_distribution`/`batch_upsert_chip_distribution`/`upsert_index_ohlcv`。
  - 规范: `daily_ohlcv`/`index_ohlcv`/`chip_distribution` → compact; `capital_flows` → hyphen。
- `src/unified_cache.py`: 新增 `_norm_date_iso()`; 应用于 `put_daily_ohlcv`。
- `query_ohlcv(start,end)` 入口也做规范化 (修复 hyphen 入参 vs compact 存储的范围查询失真)。

## 遗留

- capital_flows 丢失 5 个仅 tushare 有的旧日 (20260610~20260616), 可后续用 akshare 回补。
- `data_warehouse.db` 296MB 空闲页 (未 VACUUM)。
- `index_ohlcv` 停更至 2026-07-27 (IndexFetcher 仅源东财被拒; tushare `index_daily` 实测可回补)。

## 验证

- `pytest tests/` 151 passed
- 读取验证: capital_flows 单调 DESC 且最新=2026-09-24; ohlcv_cache days=120/500 均单调 ASC 且全 hyphen
- 脚本: `scripts/fix_date_format_pollution.py` (幂等, 默认 dry-run, `--apply` 执行)
