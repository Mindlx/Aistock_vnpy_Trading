#!/usr/bin/env python3
"""
本地筹码分布计算 — 从 OHLCV+turnover 数据直接计算筹码分布，
完全绕过外部 API。

算法来源: akshare stock_cyq_em (东方财富同名算法)
    输入: K线数据 (open, high, low, close, volume, turnover_rate)
    输出: 获利比例, 平均成本, 90%集中度

用法:
    python scripts/compute_chip_local.py --code 600372
    python scripts/compute_chip_local.py --backfill   # 全部股票回填到 chip_distribution
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "data_warehouse.db"

# ── 核心筹码算法（从 akshare stock_cyq_em 的 JS 移植）──

FACTOR = 150       # 价格区间划分数
RANGE_DAYS = 120   # 计算窗口


def compute_chip_distribution(
    open_p: np.ndarray, high: np.ndarray, low: np.ndarray,
    close: np.ndarray, volume: np.ndarray, turnover: np.ndarray,
    window: int = RANGE_DAYS,
) -> dict:
    """计算筹码分布.

    Args:
        open_p, high, low, close, volume, turnover: 等长数组, 按日期升序
        window: 计算窗口天数

    Returns:
        {profit_ratio, avg_cost, concentration} 或 None (数据不足时)
    """
    n = len(close)
    if n < 30:
        return None

    # 取最近 window 天
    start = max(0, n - window)
    o = open_p[start:]
    h = high[start:]
    l = low[start:]
    c = close[start:]
    v = volume[start:]
    tr = turnover[start:]

    m = len(c)
    if m < 10:
        return None

    # 价格区间
    max_price = float(np.max(h))
    min_price = float(np.min(l))
    if max_price - min_price < 0.001:
        return None
    accuracy = max(0.01, (max_price - min_price) / (FACTOR - 1))

    # 价格轴 (yrange): FACTOR 个离散价格位
    yrange = np.array([min_price + accuracy * i for i in range(FACTOR)])

    # 筹码累积数组
    chips = np.zeros(FACTOR, dtype=float)

    for i in range(m):
        # 换手率: 使用日均换手率/100
        tr_i = min(1.0, float(tr[i] / 100.0)) if tr[i] > 0 else 0.0

        # 每日衰减: 已有筹码 * (1 - turnoverRate)
        chips *= (1.0 - tr_i)

        # 当日价格区间索引
        H = int(math.floor((float(h[i]) - min_price) / accuracy))
        L = int(math.ceil((float(l[i]) - min_price) / accuracy))
        H = max(0, min(FACTOR - 1, H))
        L = max(0, min(FACTOR - 1, L))

        avg_price = (float(o[i]) + float(h[i]) + float(l[i]) + float(c[i])) / 4.0
        G = int(math.floor((avg_price - min_price) / accuracy))
        G = max(0, min(FACTOR - 1, G))

        # G 点系数: 一字板特殊处理
        if abs(h[i] - l[i]) < 0.001:
            g_factor = FACTOR - 1  # 一字板
            chips[G] += g_factor * tr_i / 2.0
        else:
            g_factor = 2.0 / (h[i] - l[i])
            for j in range(L, H + 1):
                cur_price = min_price + accuracy * j
                if cur_price <= avg_price:
                    if abs(avg_price - l[i]) < 0.001:
                        pass  # 不额外分配
                    else:
                        weight = (cur_price - l[i]) / (avg_price - l[i])
                        chips[j] += weight * g_factor * tr_i
                else:
                    if abs(h[i] - avg_price) < 0.001:
                        pass
                    else:
                        weight = (h[i] - cur_price) / (h[i] - avg_price)
                        chips[j] += weight * g_factor * tr_i

    # 处理负数
    chips = np.maximum(chips, 0)
    total_chips = float(np.sum(chips))

    if total_chips < 1e-8:
        return None

    # 当前价格
    current_price = float(c[-1])

    # 获利比例
    profit_mask = yrange < current_price
    profit_ratio = float(np.sum(chips[profit_mask])) / total_chips

    # 平均成本 (筹码加权平均价)
    avg_cost = float(np.sum(chips * yrange)) / total_chips

    # 90% 集中度: 找到包含 90% 筹码的最小区间
    sorted_idx = np.argsort(yrange)
    sorted_chips = chips[sorted_idx]
    sorted_prices = yrange[sorted_idx]

    cumsum = np.cumsum(sorted_chips) / total_chips

    # 滑动窗口找 90% 区间
    target = 0.90
    min_range = float('inf')
    left_ptr = 0
    for right_ptr in range(FACTOR):
        while cumsum[right_ptr] - cumsum[left_ptr] >= target:
            rng = sorted_prices[right_ptr] - sorted_prices[left_ptr]
            if rng < min_range:
                min_range = rng
            left_ptr += 1

    concentration = min_range / avg_cost if avg_cost > 0 else 1.0

    return {
        "profit_ratio": round(profit_ratio, 6),
        "avg_cost": round(avg_cost, 2),
        "concentration": round(min(concentration, 1.0), 6),
    }


# ── 数据访问 ──


def load_ohlcv_with_turnover(code: str) -> list[dict] | None:
    """从 data_warehouse.db 加载 OHLCV + 换手率."""
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT date, open, high, low, close, volume, turnover "
        "FROM daily_ohlcv WHERE stock_code=? ORDER BY date", (code,)
    ).fetchall()
    conn.close()
    if not rows:
        return None
    return [{
        "date": r[0], "open": float(r[1] or 0), "high": float(r[2] or 0),
        "low": float(r[3] or 0), "close": float(r[4] or 0),
        "volume": float(r[5] or 0), "turnover": float(r[6] or 0),
    } for r in rows]


def compute_for_stock(code: str) -> dict | None:
    """对单只股票计算并返回最新筹码分布."""
    data = load_ohlcv_with_turnover(code)
    if not data or len(data) < 30:
        return None

    o = np.array([d["open"] for d in data], dtype=float)
    h = np.array([d["high"] for d in data], dtype=float)
    l = np.array([d["low"] for d in data], dtype=float)
    c = np.array([d["close"] for d in data], dtype=float)
    v = np.array([d["volume"] for d in data], dtype=float)
    tr = np.array([d["turnover"] for d in data], dtype=float)

    return compute_chip_distribution(o, h, l, c, v, tr)


def compute_history_for_stock(code: str) -> list[dict] | None:
    """对每只股票计算全部历史筹码分布（每个交易日一个值）."""
    data = load_ohlcv_with_turnover(code)
    if not data or len(data) < 30:
        return None

    o = np.array([d["open"] for d in data], dtype=float)
    h = np.array([d["high"] for d in data], dtype=float)
    l = np.array([d["low"] for d in data], dtype=float)
    c = np.array([d["close"] for d in data], dtype=float)
    v = np.array([d["volume"] for d in data], dtype=float)
    tr = np.array([d["turnover"] for d in data], dtype=float)

    results = []
    for i in range(RANGE_DAYS, len(data)):
        result = compute_chip_distribution(
            o[:i + 1], h[:i + 1], l[:i + 1],
            c[:i + 1], v[:i + 1], tr[:i + 1],
            window=RANGE_DAYS,
        )
        if result:
            results.append({
                "date": data[i]["date"],
                **result,
            })
    return results


# ── 回填到 DB ──


def backfill_all() -> None:
    """对所有有 OHLCV 数据的股票计算筹码分布并写入 chip_distribution."""
    conn = sqlite3.connect(str(DB_PATH))
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM daily_ohlcv ORDER BY stock_code"
    ).fetchall()]
    conn.close()

    logger.info("共有 %d 只股票待处理", len(codes))
    ok = fail = total_rows = 0

    for code in codes:
        try:
            rows = compute_history_for_stock(code)
            if rows:
                _batch_upsert(code, rows)
                total_rows += len(rows)
                ok += 1
                if ok % 20 == 0:
                    logger.info("进度: %d/%d, %d 行", ok, len(codes), total_rows)
            else:
                fail += 1
        except Exception as exc:
            logger.warning("%s 失败: %s", code, exc)
            fail += 1

    logger.info("完成: %d OK, %d fail, %d 行", ok, fail, total_rows)


def _batch_upsert(code: str, rows: list[dict]) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    for r in rows:
        conn.execute(
            """INSERT OR REPLACE INTO chip_distribution
               (stock_code, date, profit_ratio, avg_cost, concentration, source, fetched_at)
               VALUES (?,?,?,?,?,?,?)""",
            (code, r["date"], r["profit_ratio"], r["avg_cost"],
             r["concentration"], "local_compute", time.time()),
        )
    conn.commit()
    conn.close()


# ── 入口 ──


def main():
    parser = argparse.ArgumentParser(description="本地筹码分布计算")
    parser.add_argument("--code", type=str, help="单只股票代码")
    parser.add_argument("--backfill", action="store_true", help="回填全部股票")
    args = parser.parse_args()

    if args.code:
        result = compute_for_stock(args.code)
        if result:
            print(f"{args.code}: 获利比例={result['profit_ratio']:.1%} "
                  f"平均成本={result['avg_cost']:.2f} "
                  f"90集中度={result['concentration']:.2%}")
        else:
            print(f"{args.code}: 数据不足")
    elif args.backfill:
        backfill_all()
    else:
        # 默认: 显示前 10 只
        conn = sqlite3.connect(str(DB_PATH))
        codes = [r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM daily_ohlcv ORDER BY stock_code LIMIT 10"
        ).fetchall()]
        conn.close()
        for code in codes:
            result = compute_for_stock(code)
            if result:
                print(f"{code}: profit={result['profit_ratio']:.1%} "
                      f"cost={result['avg_cost']:.2f} "
                      f"conc={result['concentration']:.2%}")


if __name__ == "__main__":
    main()
