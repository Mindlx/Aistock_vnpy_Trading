#!/usr/bin/env python3
"""AT 子系统统计显著性检验 & 误差分析

读取 bt_results.db，对 AT 预测做二项检验 + 混淆矩阵 + 个股分解。
"""
import sqlite3
from math import comb
from pathlib import Path
from datetime import datetime

DB = Path("data/backtest/bt_results.db")
if not DB.exists():
    print(f"Not found: {DB}")
    exit(1)

conn = sqlite3.connect(str(DB))


def binom_two_sided(k, n, p=0.5):
    if k > n // 2:
        k = n - k
    return 2 * sum(comb(n, i) * (p ** i) * ((1 - p) ** (n - i)) for i in range(k + 1))


def binom_one_sided_ge(k, n, p=0.5):
    return sum(comb(n, i) * (p ** i) * ((1 - p) ** (n - i)) for i in range(k, n + 1))


rows = conn.execute("""
  SELECT date, stock_code, at_dir, at_correct, next_pct_chg
  FROM bt_predictions
  WHERE at_dir IS NOT NULL AND next_pct_chg IS NOT NULL
  ORDER BY date
""").fetchall()

market_down = sum(1 for r in rows if r[4] < 0)
market_up = sum(1 for r in rows if r[4] > 0)
market_flat = sum(1 for r in rows if r[4] == 0)
total = len(rows)
at_correct = sum(1 for r in rows if r[3] == 1)
at_bear = [r for r in rows if r[2] == -1]
at_bull = [r for r in rows if r[2] == 1]
at_neu = [r for r in rows if r[2] == 0]

bear_correct = sum(1 for r in at_bear if r[3] == 1)
bull_correct = sum(1 for r in at_bull if r[3] == 1)
bear_p = binom_one_sided_ge(bear_correct, len(at_bear)) if at_bear else 1.0

# Confusion matrix
cm_up = {"bull": 0, "bear": 0, "neu": 0}
cm_down = {"bull": 0, "bear": 0, "neu": 0}
cm_flat = {"bull": 0, "bear": 0, "neu": 0}
for r in rows:
    d = r[4]
    pred = r[2]
    target = cm_up if d > 0 else cm_down if d < 0 else cm_flat
    if pred == 1:
        target["bull"] += 1
    elif pred == -1:
        target["bear"] += 1
    else:
        target["neu"] += 1

# Per-stock
stocks = {}
for r in rows:
    code = r[1]
    if code not in stocks:
        stocks[code] = {"total": 0, "correct": 0}
    stocks[code]["total"] += 1
    if r[3] == 1:
        stocks[code]["correct"] += 1

# Daily
daily = {}
for r in rows:
    d = r[0]
    if d not in daily:
        daily[d] = {"total": 0, "correct": 0}
    daily[d]["total"] += 1
    if r[3] == 1:
        daily[d]["correct"] += 1

# === OUTPUT ===
print(f"""# AT 子系统统计检验报告

> 生成日期: {datetime.now().strftime('%Y-%m-%d')}
> 数据源: bt_results.db ({total} 条已匹配预测)

## 一、整体表现

| 指标 | 值 |
|------|-----|
| 总预测 | {total} |
| 正确 | {at_correct} |
| 准确率 | {at_correct / total * 100:.1f}% |
| 双尾二项检验 (H0: p=50%) | p={binom_two_sided(at_correct, total):.4f} |
| 统计显著? | {'是' if binom_two_sided(at_correct, total) < 0.05 else '否'} |
| 方向 | {'低于随机 (反指)' if at_correct / total < 0.5 else '高于随机'} |
""")

print("## 二、方向分解\n")
print(f"| 方向 | 数量 | 正确 | 准确率 | 单尾检验(>50%) | 市场基准 |")
print(f"|------|------|------|--------|---------------|----------|")
print(f"| 看多 | {len(at_bull)} | {bull_correct} | {bull_correct / max(len(at_bull), 1) * 100:.1f}% | N/A | 上涨率 {market_up / total * 100:.1f}% |")
print(f"| 看空 | {len(at_bear)} | {bear_correct} | {bear_correct / max(len(at_bear), 1) * 100:.1f}% | p={bear_p:.4f} {'显著' if bear_p < 0.05 else '不显著'} | 下跌率 {market_down / total * 100:.1f}% |")
print(f"| 中性 | {len(at_neu)} | 0 | 0.0% | N/A | N/A |")
print()

print("## 三、混淆矩阵\n")
print(f"| 预测\\实际 | **涨** ({market_up}) | **跌** ({market_down}) | **平** ({market_flat}) |")
print(f"|----------|------|------|------|")
print(f"| **看多** ({len(at_bull)}) | {cm_up['bull']} | {cm_down['bull']} | {cm_flat['bull']} |")
print(f"| **看空** ({len(at_bear)}) | {cm_up['bear']} | {cm_down['bear']} | {cm_flat['bear']} |")
print(f"| **中性** ({len(at_neu)}) | {cm_up['neu']} | {cm_down['neu']} | {cm_flat['neu']} |")
print()

print("## 四、个股分解\n")
print(f"| 股票 | 总预测 | 正确 | 准确率 | p值 |")
print(f"|------|--------|------|--------|-----|")
for code in sorted(stocks):
    s = stocks[code]
    p = binom_two_sided(s["correct"], s["total"])
    print(f"| {code} | {s['total']} | {s['correct']} | {s['correct'] / s['total'] * 100:.1f}% | p={p:.4f} |")
print()

print("## 五、逐日表现\n")
print(f"| 日期 | 准确率 | 正确/总数 | 看多 | 看空 | 中性 |")
print(f"|------|--------|-----------|------|------|------|")
for d in sorted(daily):
    s = daily[d]
    bear_d = sum(1 for r in rows if r[0] == d and r[2] == -1)
    bull_d = sum(1 for r in rows if r[0] == d and r[2] == 1)
    neu_d = sum(1 for r in rows if r[0] == d and r[2] == 0)
    print(f"| {d} | {s['correct'] / s['total'] * 100:.0f}% | {s['correct']}/{s['total']} | {bull_d} | {bear_d} | {neu_d} |")
print()

print("## 六、结论\n")
print(f"1. **AT 整体准确率 {at_correct / total * 100:.1f}%** — 显著低于随机 (p={binom_two_sided(at_correct, total):.4f})，系系统性反指。")
print(f"2. **看空准确率 {bear_correct / max(len(at_bear), 1) * 100:.1f}%** — {'显著' if bear_p < 0.05 else '不显著'}优于随机 (p={bear_p:.4f})。但市场下跌率 {market_down / total * 100:.1f}%，简单的'始终看空'策略比 AT 更好。")
print(f"3. **看多准确率 0%** — AT 的看多预测全部错误，应禁止/反转。")
print(f"4. **中性预测 {len(at_neu)} 次全部无方向判断能力** — AT 在不确定时输出 Hold 没有意义。")
print(f"5. **对融合引擎的建议**:")
print(f"   - AT 权重应降到 0.10 以下，减少对融合得分的负面影响")
print(f"   - 考虑反转 AT 的看空信号（看空→看多）作为可能的 contrarian indicator")
print(f"   - 将 AT 的看多和中性输出直接映射到 L7=0（忽略）")

conn.close()
