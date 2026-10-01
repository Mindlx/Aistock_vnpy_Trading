#!/usr/bin/env python3
"""修复 ohlcv_cache 日期格式漂移导致的 bt_results.db 整月污染 (2026-07-31 首修, 2026-08-07 复发复用).

触发条件: 周报/回测前发现 `SELECT COUNT(*) FROM bt_predictions WHERE next_date='20260105'` 非零。
根因: sina 源写 plain 格式日期 ('20260805'), efinance 写 hyphen 格式 ('2026-08-05'), 混存于
      data/unified_cache/ohlcv_cache.db; SQL 字符串比较下 '20260105' > '2026-08-06' (位置4 '0'>'-'),
      所有预测被匹配到 1月5日收盘价 → 准确率纯噪声 (可表现为虚假高准确率或信号分布极端)。

流程: 备份 bt_results.db → 复制 cache 到 /tmp → 副本上删重复 plain 行+转换独有行 →
      重置污染记录 (next_date='20260105') → 用副本重算 (只改回测库, 不碰共享 cache)。

用法: cd ~/workspace/Aistock_vnpy_Trading && .venv/bin/python /path/to/fix_backtest_pollution.py
      (或放入 skill scripts/ 目录后直接复用)
"""
import sqlite3
import shutil
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path.home() / "workspace/Aistock_vnpy_Trading"
sys.path.insert(0, str(ROOT / "scripts"))

BT_DB = ROOT / "data/backtest/bt_results.db"
CACHE_DB = ROOT / "data/unified_cache/ohlcv_cache.db"
FIXED_CACHE = Path("/tmp/ohlcv_fixed.db")
POLLUTED_NEXT_DATE = "20260105"  # 最小 2026 plain 日期, 即污染标志

TS = datetime.now().strftime("%Y%m%d_%H%M%S")

# ── 1. 备份 ──
bak = BT_DB.with_name(f"bt_results.db.bak_{TS}")
shutil.copy2(BT_DB, bak)
print(f"✅ 备份: {bak}")

# ── 2. 复制 cache ──
shutil.copy2(CACHE_DB, FIXED_CACHE)
print(f"✅ cache 副本: {FIXED_CACHE}")

# ── 3. 规范化副本 ──
conn = sqlite3.connect(FIXED_CACHE)
cur = conn.cursor()
plain_before = cur.execute("SELECT COUNT(*) FROM daily_ohlcv WHERE date NOT LIKE '%-%-%'").fetchone()[0]
print(f"plain 行数(修复前): {plain_before}")

# 3a. 删除已有 hyphen 对应行的 plain 行 (避免 UNIQUE(stock_code,date) 冲突)
cur.execute("""
DELETE FROM daily_ohlcv
WHERE date NOT LIKE '%-%-%'
  AND EXISTS (
    SELECT 1 FROM daily_ohlcv d2
    WHERE d2.stock_code = daily_ohlcv.stock_code
      AND d2.date = printf('%s-%s-%s', substr(daily_ohlcv.date,1,4),
                           substr(daily_ohlcv.date,5,2), substr(daily_ohlcv.date,7,2))
  )
""")
print(f"删除重复 plain 行: {cur.rowcount}")

# 3b. 转换独有 plain 行
cur.execute("""
UPDATE daily_ohlcv
SET date = printf('%s-%s-%s', substr(date,1,4), substr(date,5,2), substr(date,7,2))
WHERE date NOT LIKE '%-%-%'
""")
print(f"转换独有 plain 行: {cur.rowcount}")
plain_after = cur.execute("SELECT COUNT(*) FROM daily_ohlcv WHERE date NOT LIKE '%-%-%'").fetchone()[0]
print(f"plain 行数(修复后): {plain_after}")
conn.commit()
conn.close()

# ── 4+5. 重置并重算 (复用 backtest.py 模块函数) ──
sys.path.insert(0, str(ROOT))
from backtest import _get_pred_and_next_close, _is_correct, _count_trading_days_between  # noqa: E402

conn_bt = sqlite3.connect(BT_DB)
conn_bt.row_factory = sqlite3.Row
conn_fix = sqlite3.connect(FIXED_CACHE)
conn_fix.row_factory = sqlite3.Row

polluted = conn_bt.execute(
    f"SELECT id, date, stock_code, fusion_dir, ly_dir, ml_dir, at_dir "
    f"FROM bt_predictions WHERE next_date='{POLLUTED_NEXT_DATE}' ORDER BY date"
).fetchall()
print(f"\n污染记录数: {len(polluted)}")

n_matched = n_unmatched = n_skipped = 0
per_day = {}
for row in polluted:
    pid, date, code = row["id"], row["date"], row["stock_code"]
    nd = _get_pred_and_next_close(conn_fix, date, code)
    if nd is None:
        # 无次日行情 (如周五预测 → 下周一未发生) → 清空保持未匹配
        conn_bt.execute("""
            UPDATE bt_predictions SET
                next_date = NULL, next_pct_chg = NULL, next_close = NULL,
                days_offset = NULL, fusion_correct = NULL, ly_correct = NULL,
                ml_correct = NULL, at_correct = NULL,
                updated_at = datetime('now','localtime')
            WHERE id = ?
        """, (pid,))
        n_unmatched += 1
        per_day.setdefault(date, [0, 0])[1] += 1
        continue
    pct_chg = nd["pct_chg"]
    if pct_chg is None:
        n_skipped += 1
        continue
    actual_dir = 1 if pct_chg > 0 else (-1 if pct_chg < 0 else 0)
    days_offset = _count_trading_days_between(conn_fix, code, date, nd["next_date"])
    conn_bt.execute("""
        UPDATE bt_predictions SET
            next_date = ?, next_pct_chg = ?, next_close = ?,
            days_offset = ?, fusion_correct = ?, ly_correct = ?,
            ml_correct = ?, at_correct = ?, updated_at = datetime('now','localtime')
        WHERE id = ?
    """, (nd["next_date"], pct_chg, nd["next_close"], days_offset,
          _is_correct(row["fusion_dir"], actual_dir),
          _is_correct(row["ly_dir"], actual_dir),
          _is_correct(row["ml_dir"], actual_dir),
          _is_correct(row["at_dir"], actual_dir), pid))
    n_matched += 1
    per_day.setdefault(date, [0, 0])[0] += 1

conn_bt.commit()
print(f"✅ 重算完成: 匹配 {n_matched}, 未匹配(次日未发生) {n_unmatched}, 跳过 {n_skipped}")
for d in sorted(per_day):
    print(f"  {d}: 匹配 {per_day[d][0]} / 未匹配 {per_day[d][1]}")
left = conn_bt.execute(
    f"SELECT COUNT(*) FROM bt_predictions WHERE next_date='{POLLUTED_NEXT_DATE}'"
).fetchone()[0]
print(f"剩余污染记录: {left}")
conn_bt.close()
conn_fix.close()
print("\n🎉 修复完成 — 重跑 report / diagnose_agreement / simulate 取真实数据")
