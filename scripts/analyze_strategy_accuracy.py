#!/usr/bin/env python3
"""
策略级准确率分析

分析各 Agent 策略/技能的独立准确率，识别高贡献与噪音策略。

用法:
    .venv/bin/python scripts/analyze_strategy_accuracy.py

数据来源:
    - analysis_history.skill_id (2026-07-03 起记录)
    - backtest_results.skill_id + direction_correct
    - decision_signals.skill_id (已有历史)

依赖:
    需积累至少数百条带 skill_id 的回测记录后才有统计意义。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
ML_DB = _PROJECT_ROOT / "systems" / "MindLynx-Aistock" / "data" / "stock_analysis.db"
BT_DB = _PROJECT_ROOT / "data" / "backtest" / "bt_results.db"


def _bar(pct: float, width: int = 15) -> str:
    filled = max(0, min(width, int(pct / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def main():
    print("=" * 65)
    print(f"策略级准确率分析  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 65)

    conn = sqlite3.connect(str(ML_DB))
    cur = conn.cursor()

    # ── 1. analysis_history.skill_id 分布 ──
    cur.execute("PRAGMA table_info(analysis_history)")
    cols = {r[1] for r in cur.fetchall()}
    has_skill = "skill_id" in cols

    if has_skill:
        cur.execute('''
            SELECT COALESCE(skill_id, 'consensus') as sid, COUNT(*)
            FROM analysis_history
            GROUP BY sid
            ORDER BY COUNT(*) DESC
        ''')
        print("\n分析历史 skill_id 分布:")
        print(f"{'策略':25s} {'次数':>6s}")
        print("-" * 35)
        for sid, cnt in cur.fetchall():
            print(f"  {sid:25s} {cnt:5d}")
    else:
        print("\n⚠️ analysis_history 无 skill_id 列，DB 尚未迁移")

    # ── 2. decision_signals.skill_id 分布 ──
    cur.execute('''
        SELECT COALESCE(skill_id, 'consensus') as sid, COUNT(*)
        FROM decision_signals
        GROUP BY sid
        ORDER BY COUNT(*) DESC
    ''')
    print("\n决策信号 skill_id 分布:")
    print(f"{'策略':25s} {'次数':>6s}")
    print("-" * 35)
    for sid, cnt in cur.fetchall():
        print(f"  {sid:25s} {cnt:5d}")

    # ── 3. backtest_results direction_correct per skill_id ──
    cur.execute('''
        SELECT COALESCE(br.skill_id, 'consensus') as sid,
               SUM(CASE WHEN br.direction_correct = 1 THEN 1 ELSE 0 END) as correct,
               SUM(CASE WHEN br.direction_correct IS NOT NULL THEN 1 ELSE 0 END) as total,
               ROUND(AVG(br.stock_return_pct), 2) as avg_return
        FROM backtest_results br
        WHERE br.eval_status = 'completed'
        GROUP BY sid
        HAVING total >= 10
        ORDER BY correct * 1.0 / total DESC
    ''')
    print("\n\n📊 per-strategy 回测准确率 (backtest_results)")
    print(f"{'策略':25s} {'准确率':>8s} {'正确/总':>10s} {'平均收益':>8s}")
    print("-" * 55)
    rows = cur.fetchall()
    if rows:
        best_acc = 0
        worst_acc = 100
        for sid, correct, total, avg_return in rows:
            acc = round(correct / total * 100, 1) if total else 0
            bar = _bar(acc)
            print(f"  {sid:25s} {acc:6.1f}% {bar} {correct:4d}/{total:<4d} {avg_return:>+7.2f}%")
            best_acc = max(best_acc, acc)
            worst_acc = min(worst_acc, acc)

        print(f"\n  最高: {best_acc:.1f}%  最低: {worst_acc:.1f}%  差距: {best_acc - worst_acc:.1f}pp")
        if best_acc - worst_acc > 15:
            print("  结论: 🟢 策略间差异显著，存在优化空间（裁剪低效策略）")
        elif best_acc - worst_acc > 5:
            print("  结论: 🟡 策略间有一定分化，可关注差异趋势")
        else:
            print("  结论: ➡️ 策略间差异不大，进一步积累数据中")
    else:
        print("  ⏳ 暂无足够数据 (< 10 records per strategy)")
        print("  等待 analysis_history.skill_id 积累更多记录...")

    # ── 4. 全局统计 ──
    cur.execute('''
        SELECT COUNT(DISTINCT COALESCE(skill_id, 'consensus'))
        FROM analysis_history
    ''')
    n = cur.fetchone()[0] if has_skill else 0
    print(f"\n  已追踪策略数: {n}")
    print(f"  (自 2026-07-03 起记录，随每日运行自然积累)")

    conn.close()

    # ── 5. 融合层面 per-strategy (从 bt_predictions + decision_signals 关联) ──
    print("\n--- 融合层面策略分析需要更多数据积累 ---")

    # 6. 简单建议
    print("\n" + "=" * 65)
    total_ah = 1328
    print(f"建议: 当前 analysis_history 共 ~{total_ah} 条,")
    print(f"      按每日 ~50 条新增计算, 约 10-15 个交易日后")
    print(f"      大部分 analysis 将带有 skill_id, 届时可跑此脚本")


if __name__ == "__main__":
    main()
