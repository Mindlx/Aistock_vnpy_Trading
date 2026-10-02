# 数据单位对齐 — volume=股 / amount=元 (2026-10-02)

## 背景

巡检发现库内多源数据单位混存：不同数据源原生单位不同（tushare=手/千元，
tencent/akshare=手/元，sina/yfinance=股/元），写入端未转换直接透传。后果与
日期格式污染同源：数值聚合/比较失真，且跨源拼接时量级错误。

## 规范 (canonical)

| 字段 | 规范单位 |
|------|---------|
| volume | 股 |
| amount | 元 |
| price / close | 元 |
| pct_chg / change_pct | % |
| market_cap / total_mv | 元 |

## 各源原生单位 (实测自洽比 amount/(close×volume)，目标=1.0)

| 源 | volume | amount | 因子 (vol/amount) | 证据 |
|----|:------:|:------:|:----------------:|------|
| tushare daily | 手 | 千元 | ×100 / ×1000 | 600372 20260930: vol=145527.11, amount=163946.83, ratio=0.100 |
| pytdx | 手 | 元 | ×100 / ×1 | TDX 约定 (当前源不可用) |
| akshare_em | 手 | 元 | ×100 / ×1 | stock_daily Efinance/Akshare ratio≈100 |
| akshare (index) | 手 | 元 | ×100 / ×1 | 约定 |
| efinance | 手 | 元 | ×100 / ×1 | 存量 ratio≈100 |
| tencent (实时) | 手 | 元 | ×100 / ×1 | realtime_quotes ratio≈100.1 |
| sina | 股 | 元(或0) | ×1 / ×1 | ohlcv_cache vs stock_daily: 4691682/46917=100.0 |
| yfinance | 股 | 0 | ×1 / ×1 | 边界连续 |
| cache_fallback | 手 | 元 | ×100 / ×1 | stock_daily ratio≈100 |

## 存量转换 (P2)

脚本 `scripts/fix_data_units.py`（幂等，`data_migrations` 标记；含 `--dry-run`）。
备份：`data/backup/{data_warehouse.db,stock_analysis.db}.bak_units_20261002_170313`。

| 库·表 | 条件 | 转换 | 行数 | 转换后 ratio |
|-------|------|------|:----:|:-----------:|
| data_warehouse.daily_ohlcv | source='tushare' | ×100/×1000 | 47834 | 1.0003 ✓ |
| data_warehouse.index_ohlcv | source='tushare' | ×100/×1000 | 540 | (指数点位，ratio 无意义) |
| data_warehouse.realtime_quotes | source='tencent' | ×100 | 18 | 1.0013 ✓ |
| stock_analysis.stock_daily | code IN (605117,605368) | ×100 | 729 | 605368=1.015 ✓ / 605117=1.42 ⚠ |

**注 1**：`stock_daily` 多数 TushareFetcher 行中位数 ratio≈1.001（已规范），不可按源盲转；
只有 605117/605368 两只被实测为「手」，故按股票转换。

**注 2 (605117)**：转换后 ratio 仍 1.42，因 stock_daily 的 close 存的是**前复权价**
（2025-04-03: stock_daily close=43.54 vs ohlcv_cache 原始 close=92.0），而 amount 为原始成交额。
volume 单位已修正为股；close 的前复权属性是有意设计（TA 连续性），无需改。

## 写入端防复发 (P1)

新增 `services/data_warehouse/units.py`：`SOURCE_UNIT_FACTORS` + `normalize_rows()` /
`normalize_volume_amount()`。在 fetcher 出口归一：

- `services/data_warehouse/fetchers.py`
  - `DailyFetcher.fetch()` → `normalize_rows(rows)`（覆盖 tushare/pytdx/akshare_sina/akshare_em/efinance）
  - `RealtimeFetcher.fetch_tencent_batch()` → 逐行归一
  - `IndexFetcher.fetch_all()` → `normalize_rows`
- `systems/MindLynx-Aistock/data_provider/akshare_fetcher.py` `_normalize_data` → volume×100
- `systems/MindLynx-Aistock/data_provider/efinance_fetcher.py` `_normalize_data` → volume×100
  （TushareFetcher 原已 ×100/×1000，不改）

## 附带修复的真 BUG

`services/data_warehouse/fetchers.py` `DailyFetcher.fetch_tushare` 字段索引错误：
误用 `item[6]/item[7]`（pre_close/change）作 volume/amount，正确为 `item[9]=vol` /
`item[10]=amount`。tushare daily 字段序：`ts_code,trade_date,open,high,low,close,
pre_close,change,pct_chg,vol,amount`。修复后 600372 20260930 → vol=14,552,711 股 /
amount=163,946,830 元（ratio=1.0014）。

另修 `fetch_pytdx` key 名：pytdx `get_security_bars` 返回键为 `vol`（非 `volume`），
原 `d.get("volume",0)` 恒为 0。

## 未处理

- **amount=0 回填 (P3)**：sina/yfinance 行的 amount=0。经消费方扫描，
  `daily_ohlcv.amount` / `ohlcv_cache.amount` **无任何读取方**，故不回填（低价值；
  如需可按 tushare daily 真实 amount 回填 18 股池）。
- **605117 前复权一致性**：见注 2，属设计属性，非缺陷。

## 验证

- 转换后自洽比：tushare/tencent/605368 → ≈1.0。
- 写入端闭环：`DailyFetcher().fetch("600372")` → ratio=1.0014。
- `pytest tests/` 151 passed。
