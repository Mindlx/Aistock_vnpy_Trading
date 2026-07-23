#!/usr/bin/env python3
"""
策略评分自动校准 — 按策略ID聚合准确率，自动调整 YAML 评分调整值。

用法:
    python scripts/calibrate_skill_scores.py              # 正常模式
    python scripts/calibrate_skill_scores.py --dry-run    # 预览模式（不修改文件）
    python scripts/calibrate_skill_scores.py --min-samples 30
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB = str(PROJECT_ROOT / "systems" / "MindLynx-Aistock" / "data" / "stock_analysis.db")
STRATEGIES_DIR = PROJECT_ROOT / "systems" / "MindLynx-Aistock" / "strategies"

# 评分调整值刻度（从小到大）
ADJUST_SCALE = [0, 2, 3, 5, 8, 10, 12, 14, 15, 17, 20, 22, 23]

# 校准规则: (lower_bound, upper_bound, direction)
# direction: 1=上调一档, -1=下调一档, -999=归零
CALIBRATION_RULES = [
    (0.70, 1.00, 1),     # 高于70%: 上调
    (0.50, 0.70, 0),     # 50-70%: 不变
    (0.40, 0.50, -1),    # 40-50%: 下调
    (0.00, 0.40, -999),  # 低于40%: 归零
]


def compute_skill_accuracy(db_path: str, min_samples: int = 30, days: int = 90) -> dict:
    """从 analysis_history 读取记录，按 skill_id 聚合准确率。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT ah.sentiment_score, ah.skill_id, sp.pct_chg
        FROM analysis_history ah
        JOIN stock_daily sp ON sp.code = ah.code
            AND sp.date = date(ah.created_at, '+1 day')
        WHERE ah.sentiment_score IS NOT NULL
          AND sp.pct_chg IS NOT NULL
          AND ah.created_at >= date('now', ?)
    """, (f'-{days} days',)).fetchall()
    conn.close()

    skill_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "correct": 0})
    for r in rows:
        raw = (r["skill_id"] or "consensus").split(",")
        actual = 1 if r["pct_chg"] > 0 else (-1 if r["pct_chg"] < 0 else 0)
        if actual == 0:
            continue
        score = r["sentiment_score"]
        correct = (score >= 52 and actual > 0) or (score <= 48 and actual < 0) or (49 <= score <= 51)
        for sid in raw:
            sid = sid.strip()
            if sid:
                skill_stats[sid]["total"] += 1
                if correct:
                    skill_stats[sid]["correct"] += 1

    result = {}
    for sid, stats in skill_stats.items():
        if stats["total"] >= min_samples:
            result[sid] = {
                "accuracy": round(stats["correct"] / stats["total"], 4),
                "total": stats["total"],
                "correct": stats["correct"],
            }
    return result


def _clamp_value(value: int, direction: int) -> int:
    """按方向调整评分值一档。"""
    if direction == -999:
        return 0
    try:
        idx = ADJUST_SCALE.index(value)
    except ValueError:
        return value
    new_idx = max(0, min(len(ADJUST_SCALE) - 1, idx + direction))
    return ADJUST_SCALE[new_idx]


def get_direction(accuracy: float) -> int:
    """根据准确率确定调整方向。"""
    for lower, upper, direction in CALIBRATION_RULES:
        if lower <= accuracy < upper:
            return direction
    return 0


def calibrate_yaml(filepath: str, skill_id: str, accuracy: float) -> dict:
    """修改单个 YAML 文件的评分调整值。返回 {'changed', 'changes', 'new_content'}。"""
    with open(filepath, encoding="utf-8") as f:
        content = f.read()
    original = content

    direction = get_direction(accuracy)
    if direction == 0:
        return {"changed": False, "changes": []}

    changes = []
    pattern = r'(sentiment_score\s*)([+-])(\d+)'

    def replacer(m):
        prefix = m.group(1)
        sign = m.group(2)
        val = int(m.group(3))
        if sign == '-':
            return m.group(0)
        new_val = _clamp_value(val, direction)
        if new_val != val:
            changes.append(f"{prefix}{sign}{val} -> +{new_val}")
            return f"{prefix}+{new_val}"
        return m.group(0)

    content = re.sub(pattern, replacer, content)
    if content == original:
        return {"changed": False, "changes": []}

    return {"changed": True, "changes": changes, "new_content": content}


def apply_changes(filepath: str, new_content: str) -> bool:
    """原子写入 YAML 文件。"""
    tmp = str(filepath) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_content)
    os.replace(tmp, str(filepath))
    return True


def find_yaml_for_skill(skill_id: str) -> str | None:
    """根据技能名查找对应的 YAML 文件路径。"""
    for f in Path(STRATEGIES_DIR).glob("*.yaml"):
        try:
            with open(f) as yf:
                if yaml.safe_load(yf).get("name") == skill_id:
                    return str(f)
        except Exception:
            continue
    return None


def push_wecom_report(accuracy_data: dict, changed_count: int) -> None:
    """推送校准报告到 WeCom。"""
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from src.wecom_notifier import WeComNotifier
        notifier = WeComNotifier()
        lines = [f"### 策略评分自动校准\n\n共校准 {changed_count} 个策略\n"]
        for sid, data in sorted(accuracy_data.items(), key=lambda x: -x[1]["accuracy"]):
            acc = data["accuracy"]
            emoji = "\U0001f7e2" if acc > 0.70 else ("\U0001f7e1" if acc > 0.50 else "\U0001f534")
            direction_str = "上调" if get_direction(acc) == 1 else ("下调" if get_direction(acc) == -1 else "不变")
            lines.append(f"{emoji} {sid}: {acc:.1%} (n={data['total']}) -> {direction_str}")
        notifier.send_markdown("\n".join(lines))
    except Exception as exc:
        logger.warning("WeCom 推送失败: %s", exc)


def main():
    parser = argparse.ArgumentParser(description="校准策略评分调整值")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不修改文件")
    parser.add_argument("--min-samples", type=int, default=30, help="最小样本量")
    parser.add_argument("--days", type=int, default=90, help="回看天数")
    args = parser.parse_args()

    accuracy_data = compute_skill_accuracy(DB, args.min_samples, args.days)
    if not accuracy_data:
        logger.info("无足够数据（< %d 样本），跳过校准", args.min_samples)
        return

    print(f"\n{'策略ID':<28} {'准确率':>8} {'样本':>6} {'操作':>12}")
    print("-" * 56)

    total_changed = 0
    for sid, data in sorted(accuracy_data.items(), key=lambda x: -x[1]["accuracy"]):
        acc = data["accuracy"]
        direction = get_direction(acc)
        direction_label = {1: "上调", -1: "下调", -999: "归零", 0: "不变"}.get(direction, "不变")

        yaml_path = find_yaml_for_skill(sid)
        if not yaml_path:
            print(f"{sid:<28} {acc:>7.1%} {data['total']:>6} {'无YAML':>12}")
            continue

        result = calibrate_yaml(yaml_path, sid, acc)
        if result["changed"]:
            total_changed += 1
            changes_str = "; ".join(result["changes"][:3])
            print(f"{sid:<28} {acc:>7.1%} {data['total']:>6} {direction_label:>8} {changes_str}")
            if not args.dry_run:
                apply_changes(yaml_path, result["new_content"])
        else:
            print(f"{sid:<28} {acc:>7.1%} {data['total']:>6} {direction_label:>8}")

    summary = f"预览: 将校准 {total_changed} 个策略" if args.dry_run else f"共校准 {total_changed} 个策略"
    print(f"\n{summary}")

    if total_changed > 0 and not args.dry_run:
        push_wecom_report(accuracy_data, total_changed)


if __name__ == "__main__":
    main()
