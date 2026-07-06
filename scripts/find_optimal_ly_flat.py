#!/usr/bin/env python3
"""寻找 LY 最佳 flat zone 宽度 — 基于 walkforward 逐笔预测数据。

用法:
    # 首先生成逐笔预测数据:
    python systems/lynx_vnpy/lynx_signal.py --backtest --save-predictions data/research/ly_predictions.json

    # 然后运行本脚本分析最优 flat zone:
    python scripts/find_optimal_ly_flat.py
"""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PREDICTIONS_PATH = PROJECT_ROOT / "data" / "research" / "ly_predictions.json"


def _l7_score_custom(prob_up_pct: float, anchors: list[tuple[float, float]]) -> float:
    """用自定义锚点表计算 L7 得分"""
    for i in range(len(anchors) - 1):
        x1, y1 = anchors[i]
        x2, y2 = anchors[i + 1]
        if x1 <= prob_up_pct <= x2:
            if x2 == x1:
                return y1
            return y1 + (y2 - y1) * (prob_up_pct - x1) / (x2 - x1)
    return 0.0


def evaluate_flat_zone(predictions: list[dict],
                       flat_lo: float, flat_hi: float,
                       label: str = "") -> tuple[float, int, int]:
    """评估一组 flat zone 参数的准确率"""
    # 构建自定义锚点表
    anchors = [
        (0, -3.0), (25, -2.0), (35, -1.0), (42, -0.5),
        (flat_lo, 0.0), (flat_hi, 0.0),
        (52, 0.25), (59, 1.0), (65, 2.0), (75, 3.0), (100, 3.0),
    ]
    # 确保锚点单调递增
    anchors.sort(key=lambda x: x[0])

    correct, total = 0, 0
    for pred in predictions:
        prob = pred.get("prob_up", 50)
        actual_dir = pred.get("actual_dir", 0)
        if actual_dir == 0:
            continue
        l7_score = _l7_score_custom(prob, anchors)
        l7_dir = 1 if l7_score > 0 else (-1 if l7_score < 0 else 0)
        if l7_dir == 0:
            continue  # flat zone → neutral → skip
        total += 1
        if l7_dir == actual_dir:
            correct += 1

    acc = round(correct / total * 100, 1) if total > 0 else 0.0
    return acc, correct, total


def main():
    if not PREDICTIONS_PATH.exists():
        print(f"请先生成逐笔预测数据:")
        print(f"  python systems/lynx_vnpy/lynx_signal.py --backtest "
              f"--save-predictions data/research/ly_predictions.json")
        return

    predictions = json.loads(PREDICTIONS_PATH.read_text(encoding="utf-8"))
    print(f"加载 {len(predictions)} 条逐笔预测\n")

    # 遍历各种 flat zone
    candidates = [
        # (lo, hi, label)
        (42, 42, "无 flat zone (42单点)"),
        (45, 45, "无 flat zone (45单点)"),
        (42, 45, "42-45 (3pp)"),
        (42, 48, "42-48 (6pp)"),
        (42, 50, "42-50 (8pp)"),
        (42, 52, "42-52 (10pp, 当前融合引擎)"),
        (45, 48, "45-48 (3pp)"),
        (45, 50, "45-50 (5pp, 当前 LY 内部)"),
        (45, 52, "45-52 (7pp)"),
        (45, 55, "45-55 (10pp, 文档旧值)"),
        (48, 50, "48-50 (2pp)"),
        (48, 52, "48-52 (4pp)"),
        (42, 55, "42-55 (13pp)"),
        (42, 58, "42-58 (16pp)"),
        (40, 60, "40-60 (20pp, 极宽)"),
    ]

    print(f"{'flat zone':>20s} {'宽度':>5s} {'准确率':>8s} {'正确/总':>12s} {'排除':>6s}")
    print("-" * 55)

    results = []
    for flat_lo, flat_hi, label in candidates:
        acc, correct, total = evaluate_flat_zone(predictions, flat_lo, flat_hi)
        excluded = len(predictions) - total
        results.append((acc, flat_lo, flat_hi, label, excluded, correct, total))
        bar = "█" * int(acc / 5) + "░" * (20 - int(acc / 5))
        print(f"{label:>20s} {flat_hi-flat_lo:>4.0f}pp {acc:>6.1f}% {bar} "
              f"{correct:>4d}/{total:<4d} 排{excluded}")

    # 最优
    results.sort(key=lambda r: -r[0])
    best = results[0]
    print(f"\n最优: {best[3]} — 准确率 {best[0]}% ({best[5]}/{best[6]})")


if __name__ == "__main__":
    main()
