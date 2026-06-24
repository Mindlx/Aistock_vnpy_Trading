"""
东方财富评级 w/f 阈值校准脚本

从 eastmoney_wf_log.csv 读取历史记录，匹配 T+1 涨跌幅，
统计各 w/f 分桶的上涨概率，与当前阈值对比。
数据不足时自动跳过。

2026-06-24 校准结论（354条记录）:
  - 参与意愿(w)与T+1涨跌幅无显著正相关
  - w≥55(理论看多)上涨78.3%, w<45(理论看空)上涨83.8%——方向相反
  - 低意愿+高关注(理论看空)上涨87.8%——看空信号反而最强
  - 阈值分类标签(✅📈💤📉❌)已被移除，w/f仅作为散户情绪氛围指标
  - 后续建议: 积累至1000+条后做5分位切分分析,重新评估w/f价值

用法:
    .venv/bin/python scripts/calibrate_eastmoney_thresholds.py
    .venv/bin/python scripts/calibrate_eastmoney_thresholds.py --min-samples 100  # 覆盖最小样本量
"""
from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 东方财富评级 w/f 阈值配置（经验值，待校准）
_DESIRE_THRESHOLDS = [80, 65, 55, 45, 20]   # 参与意愿分档
_FOCUS_THRESHOLDS = [80, 65, 55]            # 关注指数分档
_WF_LOG = Path("data/realtime/eastmoney_wf_log.csv")
_ML_DB = Path("systems/MindLynx-Aistock/data/stock_analysis.db")


def _load_wf_records() -> list[dict]:
    """加载 eastmoney_wf_log.csv 中的历史记录。"""
    if not _WF_LOG.exists():
        logger.warning("wf 日志文件不存在: %s", _WF_LOG)
        return []
    records = []
    with open(_WF_LOG, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                records.append({
                    "date": row["date"],
                    "code": row["stock_code"],
                    "w": float(row["willingness"]),
                    "f": float(row["focus"]),
                    "l7": row.get("l7_level", ""),
                })
            except (ValueError, KeyError):
                continue
    logger.info("已加载 %d 条 w/f 记录", len(records))
    return records


def _match_next_day_returns(records: list[dict]) -> list[dict]:
    """匹配每条记录的 T+1 涨跌幅。"""
    if not _ML_DB.exists():
        logger.warning("ML 数据库不存在: %s", _ML_DB)
        return records

    conn = sqlite3.connect(str(_ML_DB))
    matched = 0
    for r in records:
        date = r["date"]
        code = r["code"]
        try:
            cur = conn.execute(
                "SELECT pct_chg FROM stock_daily WHERE code=? AND date>? ORDER BY date LIMIT 1",
                (code, date),
            )
            row = cur.fetchone()
            if row:
                r["next_day_return"] = float(row[0])
                matched += 1
        except Exception:
            continue
    conn.close()
    logger.info("匹配 T+1 涨跌幅: %d/%d 条", matched, len(records))
    return records


def _analyze_thresholds(records: list[dict], min_samples: int = 50):
    """按 w/f 分桶统计上涨概率，评估当前阈值。"""
    if len(records) < min_samples:
        logger.info("样本不足 %d 条 (当前 %d)，跳过分析", min_samples, len(records))
        return

    # 只保留有 T+1 数据的记录
    valid = [r for r in records if "next_day_return" in r]
    if not valid:
        logger.warning("无匹配 T+1 数据的记录")
        return

    print(f"\n{'='*60}")
    print(f"东方财富评级 w/f 阈值校准")
    print(f"样本: {len(valid)} 条 (要求≥{min_samples})")
    print(f"{'='*60}")

    def _up_pct(items):
        return sum(1 for r in items if r.get("next_day_return", 0) > 0) / len(items) * 100 if items else 0

    # 按参与意愿 w 分桶
    print(f"\n── 参与意愿(w) 分桶分析 ──")
    desire_buckets = [(th, f"≥{th}") for th in _DESIRE_THRESHOLDS]
    desire_buckets.append((0, "other (<20)"))
    prev = 101
    for th, label in desire_buckets:
        items = [r for r in valid if th <= r["w"] < prev]
        if items:
            print(f"  {label:15s}: {len(items):4d} 条, 上涨 {_up_pct(items):5.1f}%")
        prev = th

    # 按关注指数 f 分桶
    print(f"\n── 关注指数(f) 分桶分析 ──")
    focus_buckets = [(th, f"≥{th}") for th in _FOCUS_THRESHOLDS]
    focus_buckets.append((0, "other (<55)"))
    prev = 101
    for th, label in focus_buckets:
        items = [r for r in valid if th <= r["f"] < prev]
        if items:
            print(f"  {label:15s}: {len(items):4d} 条, 上涨 {_up_pct(items):5.1f}%")
        prev = th

    # 按当前 L7 分级看效果
    print(f"\n── 当前 L7 分级效果 ──")
    l7_order = ["+3", "+2/+1", "0", "-2/-1", "-3"]
    for l7 in l7_order:
        items = [r for r in valid if r["l7"] == l7]
        if items:
            print(f"  L7 {l7:>5s}: {len(items):4d} 条, 上涨 {_up_pct(items):5.1f}%")

    # 简单组合分析：高意愿(w≥55)+低关注(f<65) vs 低意愿(w<45)+高关注(f≥65)
    print(f"\n── 关键组合分析 ──")
    bullish = [r for r in valid if r["w"] >= 55 and r["f"] < 65]
    bearish = [r for r in valid if r["w"] < 45 and r["f"] >= 65]
    if bullish:
        print(f"  高意愿+低关注 (看多信号): {len(bullish)} 条, 上涨 {_up_pct(bullish):.1f}%")
    if bearish:
        print(f"  低意愿+高关注 (看空信号): {len(bearish)} 条, 上涨 {_up_pct(bearish):.1f}%")


def main():
    parser = argparse.ArgumentParser(description="东方财富评级 w/f 阈值校准")
    parser.add_argument("--min-samples", type=int, default=50, help="最小样本量阈值")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    records = _load_wf_records()
    if not records:
        return 1

    records = _match_next_day_returns(records)
    _analyze_thresholds(records, min_samples=args.min_samples)
    return 0


if __name__ == "__main__":
    exit(main())
