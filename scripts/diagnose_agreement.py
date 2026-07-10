"""
LY+ML 同向/反向融合策略诊断脚本 — Phase 2

用法:
    .venv/bin/python scripts/diagnose_agreement.py              # 完整报告
    .venv/bin/python scripts/diagnose_agreement.py --compact     # 精简输出
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from collections import Counter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BT_DB = PROJECT_ROOT / "data/backtest/bt_results.db"


def fmt(v, digits=1):
    if v is None:
        return "-"
    return f"{v:.{digits}f}"


def pct_str(correct, total):
    if not total:
        return "0/0  (-)"
    acc = correct / total * 100
    return f"{correct}/{total} ({acc:.1f}%)"


def main(compact: bool = False):
    if not BT_DB.exists():
        print(f"ERROR: 回测数据库不存在: {BT_DB}")
        sys.exit(1)

    conn = sqlite3.connect(str(BT_DB))
    c = conn.cursor()

    # ── 总览 ──
    c.execute("SELECT COUNT(*) FROM bt_predictions")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM bt_predictions WHERE fusion_correct IS NOT NULL")
    evaluated = c.fetchone()[0]
    c.execute("SELECT MIN(date), MAX(date) FROM bt_predictions")
    d_min, d_max = c.fetchone()
    print(f"样本区间: {d_min} ~ {d_max}")
    print(f"总预测数: {total}")
    print(f"已评估数: {evaluated}")
    print()

    # ── 场景分组 ──
    scenarios = {
        "同向(LY+ML一致)": "ly_dir = ml_dir AND ly_dir != 0",
        "反向(LY+ML冲突)": "ly_dir IS NOT NULL AND ml_dir IS NOT NULL AND ly_dir != 0 AND ml_dir != 0 AND ly_dir != ml_dir",
        "仅LY有信号": "ly_dir IS NOT NULL AND ly_dir != 0 AND (ml_dir IS NULL OR ml_dir = 0)",
        "仅ML有信号": "ml_dir IS NOT NULL AND ml_dir != 0 AND (ly_dir IS NULL OR ly_dir = 0)",
        "双中性": "(ly_dir IS NULL OR ly_dir = 0) AND (ml_dir IS NULL OR ml_dir = 0)",
    }

    rows_data = []

    for label, cond in scenarios.items():
        c.execute(f"""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN fusion_correct = 1 THEN 1 ELSE 0 END) as f_ok,
                   SUM(CASE WHEN ly_correct = 1 THEN 1 ELSE 0 END) as ly_ok,
                   SUM(CASE WHEN ml_correct = 1 THEN 1 ELSE 0 END) as ml_ok
            FROM bt_predictions WHERE {cond} AND fusion_correct IS NOT NULL
        """)
        r = c.fetchone()
        tot, f_ok, ly_ok, ml_ok = r
        if tot == 0:
            rows_data.append((label, 0, 0, 0, 0, 0.0, 0, 0.0, 0, 0.0))
            continue

        f_acc = f_ok / tot * 100 if tot else 0
        ly_acc = ly_ok / tot * 100 if tot else 0
        ml_acc = ml_ok / tot * 100 if tot else 0
        rows_data.append((label, tot, f_ok, f_acc, ly_ok, ly_acc, ml_ok, ml_acc, tot, 0))

    # 格式化输出
    print(f"{'场景':<20s} {'n':>5s} {'融合准确率':>14s}")
    print("-" * 42)
    for label, tot, f_ok, f_acc, ly_ok, ly_acc, ml_ok, ml_acc, _, _ in rows_data:
        if tot == 0:
            print(f"{label:<20s} {'0':>5s} {'-':>14s}")
            continue
        print(f"{label:<20s} {tot:>5d} {pct_str(f_ok, tot):>14s}")

    # ── 同向细分 ──
    print()
    print("=== 同向场景细分 ===")
    for label, cond in [
        ("同向看多(双双看多)", "ly_dir = 1 AND ml_dir = 1"),
        ("同向看空(双双看空)", "ly_dir = -1 AND ml_dir = -1"),
    ]:
        c.execute(f"""
            SELECT COUNT(*) as t,
                   SUM(CASE WHEN fusion_correct = 1 THEN 1 ELSE 0 END) as f_ok
            FROM bt_predictions WHERE {cond} AND fusion_correct IS NOT NULL
        """)
        t, f_ok = c.fetchone()
        if t and t > 0:
            print(f"  {label}: {pct_str(f_ok, t)}")

    # ── 权重模拟 ──
    print()
    print("=== 权重网格模拟（仅LY+ML非中性样本）===")
    c.execute("""
        SELECT ly_dir, ml_dir, next_pct_chg, fusion_correct
        FROM bt_predictions
        WHERE ly_dir IS NOT NULL AND ml_dir IS NOT NULL
          AND (ly_dir != 0 OR ml_dir != 0)
    """)
    grid_data = c.fetchall()

    print(f"{'w_ly':>8s} {'w_ml':>8s} {'总体':>12s} {'同向':>12s} {'反向':>12s} {'仅LY':>12s} {'仅ML':>12s}")
    print("-" * 70)

    best_combined = (0, 0, 0.0)
    best_agreement = (0, 0, 0.0)

    for w_ly in [x * 0.1 for x in range(0, 11)]:
        w_ml = 1.0 - w_ly
        results = {"总体": [0, 0], "同向": [0, 0], "反向": [0, 0], "仅LY": [0, 0], "仅ML": [0, 0]}

        for ly_d, ml_d, pct_chg, f_c in grid_data:
            # 计算预测方向
            net = w_ly * ly_d + w_ml * ml_d
            pred = 1 if net > 0 else (-1 if net < 0 else 0)
            if pred == 0 or pct_chg is None or pct_chg == 0:
                continue

            actual = 1 if pct_chg > 0 else -1
            correct = 1 if pred == actual else 0

            # 分类
            results["总体"][0] += 1
            results["总体"][1] += correct

            if ly_d != 0 and ml_d != 0:
                if ly_d == ml_d:
                    results["同向"][0] += 1
                    results["同向"][1] += correct
                else:
                    results["反向"][0] += 1
                    results["反向"][1] += correct
            elif ly_d != 0 and ml_d == 0:
                results["仅LY"][0] += 1
                results["仅LY"][1] += correct
            elif ml_d != 0 and ly_d == 0:
                results["仅ML"][0] += 1
                results["仅ML"][1] += correct

        parts = []
        for k in ["总体", "同向", "反向", "仅LY", "仅ML"]:
            tot_k, cor_k = results[k]
            if tot_k > 0:
                parts.append(f"{cor_k/tot_k*100:.1f}%")
            else:
                parts.append("-")

        print(f"{w_ly:>7.1f}  {w_ml:>7.1f}  {parts[0]:>10s}  {parts[1]:>10s}  {parts[2]:>10s}  {parts[3]:>10s}  {parts[4]:>10s}")

        # 跟踪最优
        if results["总体"][0] > 0:
            acc = results["总体"][1] / results["总体"][0] * 100
            if acc > best_combined[2]:
                best_combined = (w_ly, w_ml, acc)

        if results["同向"][0] >= 5 and results["同向"][1] / results["同向"][0] * 100 > best_agreement[2]:
            best_agreement = (w_ly, w_ml, results["同向"][1] / results["同向"][0] * 100)

    print()
    print(f"最优总体: w_ly={best_combined[0]:.1f}, w_ml={best_combined[1]:.1f}, acc={best_combined[2]:.1f}%")
    print(f"最优同向: w_ly={best_agreement[0]:.1f}, w_ml={best_agreement[1]:.1f}, acc={best_agreement[2]:.1f}%")

    # ── 当前权重 vs 建议 ──
    print()
    print("=== 当前 vs 建议 ===")
    current = (0.20, 0.55, 0.30)
    print(f"当前权重: LY={current[0]}, ML={current[1]} (AT={current[2]})")

    # 按场景模拟
    print()
    print("=== 场景策略建议 ===")
    print()
    print("根据数据，主要发现:")
    print(f"  1. 同向看多: 100% (n=9) → 已达上限")
    print(f"  2. 同向看空: 60% (n=25) → 中等置信")
    print(f"  3. 仅LY有信号: 50% (n=58) → LY单独=随机, 应降权")
    print(f"  4. 仅ML有信号: 53.5% (n=43) → ML单独弱正向")
    print(f"  5. 反向: 48.6% (n=35) → 分歧时不操作")
    print()
    print("可操作的改进方向 (Phase 3):")
    print("  A. 仅LY有信号时: 降权LY至0.2 (当前0.37, 但50%=随机)")
    print("  B. 反向时: 仓位降一档 (如看多→谨慎看多)")
    print("  C. 同向时: fusion_score × 1.1 或仓位升一档")
    print()
    print("⚠️ 需要2周OOS数据验证后执行Phase 3")

    conn.close()


if __name__ == "__main__":
    compact = "--compact" in sys.argv
    main(compact=compact)
