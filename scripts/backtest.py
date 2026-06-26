#!/usr/bin/env python3
"""
融合系统回测工具 — 记录预测→匹配行情→生成报告

用法:
    python scripts/backtest.py init             # 初始化回测数据库
    python scripts/backtest.py record           # 读取今日融合CSV并记录预测
    python scripts/backtest.py check            # 匹配已有预测与次日行情
    python scripts/backtest.py report           # 生成累计回测报告
    python scripts/backtest.py update           # record + check (每日执行一次)

数据流:
    1. record: 从 data/fusion_output/fusion_{date}.csv 读取今日融合结果
    2. check:  从 unified_cache 查询次日实际涨跌幅,标记方向是否正确
    3. report: 汇总所有历史记录,输出准确率/分歧分析/系统对比

⚠️ 仅供学习和研究目的,不构成任何投资建议
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

# ── 路径常量 ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FUSION_OUTPUT_DIR = PROJECT_ROOT / "data" / "fusion_output"
CACHE_DB = PROJECT_ROOT / "data" / "unified_cache" / "ohlcv_cache.db"
BACKTEST_DB = PROJECT_ROOT / "data" / "backtest" / "bt_results.db"

# 方向判断阈值 (融合分数超过此绝对值才视为有方向)
DIRECTION_THRESHOLD = 0.1


# ── 数据库 ────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    """获取回测数据库连接(自动创建目录)."""
    BACKTEST_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(BACKTEST_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bt_predictions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,          -- 预测日期 (交易日) "2026-05-30"
    stock_code  TEXT NOT NULL,
    stock_name  TEXT NOT NULL DEFAULT '',
    fusion_score REAL,
    ly_score    REAL,
    ml_score    REAL,
    at_score    REAL,
    ly_valid    INTEGER DEFAULT 1,      -- 子系统是否有有效数据
    ml_valid    INTEGER DEFAULT 1,
    at_valid    INTEGER DEFAULT 1,
    ta_is_stale INTEGER DEFAULT 0,      -- TA 数据是否过期
    ml_sentiment INTEGER,               -- ML 原始评分 0-100
    ml_trend    TEXT,                    -- ML 趋势预测
    ml_operation TEXT,                   -- ML 操作建议
    ml_trend_score INTEGER,             -- ML dashboard 趋势分 0-100
    ml_risk_alert_count INTEGER DEFAULT 0, -- ML 风险告警数
    signal      TEXT,                   -- "cautious_bearish" / "neutral" / 等
    has_disagreement INTEGER DEFAULT 0,
    is_degraded     INTEGER DEFAULT 0,

    -- 实际行情匹配 (由 check 命令填充)
    next_date   TEXT,                   -- 匹配到的下一个交易日
    next_pct_chg REAL,                  -- 下一个交易日的涨跌幅 (%)
    next_close  REAL,                   -- 下一交易日收盘价
    days_offset INTEGER,                -- 距预测日相差几个交易日 (通常=1)

    -- 方向标记 (由 check 命令计算)
    fusion_dir  INTEGER,                -- 1=看多, -1=看空, 0=中性
    ly_dir      INTEGER,
    ml_dir      INTEGER,
    at_dir      INTEGER,
    fusion_correct INTEGER,             -- 1=正确, 0=错误, NULL=未评估(中性/无数据)
    ly_correct  INTEGER,
    ml_correct  INTEGER,
    at_correct  INTEGER,

    created_at  TEXT DEFAULT (datetime('now','localtime')),
    updated_at  TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(date, stock_code)
);

CREATE TABLE IF NOT EXISTS bt_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def cmd_init() -> None:
    """初始化回测数据库."""
    conn = _get_db()
    for stmt in SCHEMA_SQL.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    # 写入元数据
    conn.execute("INSERT OR IGNORE INTO bt_meta (key, value) VALUES (?, ?)",
                 ("schema_version", "1.0"))
    conn.execute("INSERT OR IGNORE INTO bt_meta (key, value) VALUES (?, ?)",
                 ("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    print(f"✅ 回测数据库已初始化: {BACKTEST_DB}")


# ── 方向判定 ──────────────────────────────────────────────

def _sign(score: float, threshold: float = DIRECTION_THRESHOLD) -> int:
    """分数→方向: 1=看多, -1=看空, 0=中性."""
    if score > threshold:
        return 1
    elif score < -threshold:
        return -1
    return 0


# ── Record ────────────────────────────────────────────────

def cmd_record(target_date: Optional[str] = None) -> int:
    """
    从融合CSV读取预测并写入回测DB.
    返回写入的记录数.
    """
    date = target_date or datetime.now().strftime("%Y-%m-%d")
    csv_path = FUSION_OUTPUT_DIR / f"fusion_{date}.csv"

    if not csv_path.exists():
        print(f"⚠️ 融合CSV不存在: {csv_path}")
        return 0

    # 读取CSV
    records = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            valid = row.get("valid", "").strip()
            if valid.lower() != "true":
                continue

            try:
                rec = {
                    "date": date,
                    "stock_code": row["stock_code"].strip(),
                    "stock_name": row.get("stock_name", "").strip(),
                    "fusion_score": _parse_float(row.get("fusion_score")),
                    "ly_score": _parse_float(row.get("lynx_score")),
                    "ml_score": _parse_float(row.get("mindlynx_score")),
                    "at_score": _parse_float(row.get("tradingagent_score")),
                    "ly_valid": 1 if row.get("lynx_valid", "").strip() == "True" else 0,
                    "ml_valid": 1 if row.get("mindlynx_valid", "").strip() == "True" else 0,
                    "at_valid": 1 if row.get("tradingagent_valid", "").strip() == "True" else 0,
                    "ta_is_stale": 1 if row.get("ta_is_stale", "").strip() == "True" else 0,
                    "ml_sentiment": _parse_float(row.get("mindlynx_sentiment")),
                    "ml_trend": row.get("mindlynx_trend", "").strip(),
                    "ml_operation": row.get("mindlynx_operation", "").strip(),
                    "ml_trend_score": _parse_float(row.get("ml_trend_score")),
                    "ml_risk_alert_count": int(row["ml_risk_alert_count"]) if row.get("ml_risk_alert_count", "").strip() else 0,
                    "signal": row.get("signal", "").strip(),
                    "has_disagreement": 1 if row.get("has_disagreement", "").strip() == "True" else 0,
                    "is_degraded": 1 if row.get("is_degraded", "").strip() == "True" else 0,
                }
                rec["fusion_dir"] = _sign(rec["fusion_score"])
                rec["ly_dir"] = _sign(rec["ly_score"])
                rec["ml_dir"] = _sign(rec["ml_score"])
                rec["at_dir"] = _sign(rec["at_score"])
                records.append(rec)
            except (KeyError, ValueError) as e:
                print(f"⚠️ 解析行错误 [{date}]: {e} | {row}")
                continue

    if not records:
        print(f"⚠️ {date}: 没有有效记录可写入")
        return 0

    # 写入DB
    conn = _get_db()
    inserted = 0
    for rec in records:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO bt_predictions
                (date, stock_code, stock_name,
                 fusion_score, ly_score, ml_score, at_score,
                 ly_valid, ml_valid, at_valid, ta_is_stale,
                 ml_sentiment, ml_trend, ml_operation,
                 ml_trend_score, ml_risk_alert_count,
                 signal, has_disagreement, is_degraded,
                 fusion_dir, ly_dir, ml_dir, at_dir)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                rec["date"], rec["stock_code"], rec["stock_name"],
                rec["fusion_score"], rec["ly_score"], rec["ml_score"], rec["at_score"],
                rec["ly_valid"], rec["ml_valid"], rec["at_valid"], rec["ta_is_stale"],
                rec["ml_sentiment"], rec["ml_trend"], rec["ml_operation"],
                rec["ml_trend_score"], rec["ml_risk_alert_count"],
                rec["signal"], rec["has_disagreement"], rec["is_degraded"],
                rec["fusion_dir"], rec["ly_dir"], rec["ml_dir"], rec["at_dir"],
            ))
            inserted += 1
        except sqlite3.Error as e:
            print(f"⚠️ 写入失败 [{rec['stock_code']}]: {e}")

    conn.commit()
    conn.close()
    print(f"✅ {date}: 写入 {inserted} 条预测记录")
    return inserted


def _parse_float(val: Any) -> Optional[float]:
    """安全解析浮点数."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ── Check (匹配行情) ─────────────────────────────────────

def _get_pred_and_next_close(conn: sqlite3.Connection, date: str,
                              stock_code: str) -> Optional[Dict]:
    """
    获取预测日的基准收盘价(向前取最近交易日)和下一个交易日的收盘价/涨跌幅.

    预测日期可能是非交易日(如周末),此时取之前最近的交易日为基准.
    """
    # 找到 predict_date 当天或之前最近的交易日收盘价
    pred_row = conn.execute("""
        SELECT date, close FROM daily_ohlcv
        WHERE stock_code = ? AND date <= ?
          AND close IS NOT NULL AND close > 0
        ORDER BY date DESC
        LIMIT 1
    """, (stock_code, date)).fetchone()
    if not pred_row:
        return None

    pred_close = pred_row["close"]
    pred_trade_date = pred_row["date"]

    # 找到 pred_trade_date 之后的下一个交易日(不能与基准日相同)
    next_row = conn.execute("""
        SELECT date, close
        FROM daily_ohlcv
        WHERE stock_code = ? AND date > ?
          AND close IS NOT NULL AND close > 0
        ORDER BY date ASC
        LIMIT 1
    """, (stock_code, pred_trade_date)).fetchone()
    if not next_row:
        return None

    next_close = next_row["close"]
    pct_chg = round((next_close - pred_close) / pred_close * 100, 4)

    # ── 数据质量守卫 ──────────────────────────────────────────
    # A-share daily limit varies by market segment:
    #   688xxx/689xxx 科创板(STAR)     ±20%
    #   300xxx/301xxx 创业板(ChiNext)  ±20%
    #   8xxxxx        北交所(BSE)      ±30%
    #   股票名含 *ST/ST               ±5%
    #   其他(主板)                     ±10%
    # Values beyond the limit + 0.5% buffer indicate corrupted cache data.
    # Skip silently to avoid contaminating accuracy metrics.
    if not stock_code or not isinstance(stock_code, str):
        print(f"⚠️  数据异常: stock_code无效({stock_code})，跳过")
        return None
    stock_code_str = str(stock_code).strip()
    if not re.match(r'^\d{6}', stock_code_str):
        print(f"⚠️  数据异常: stock_code格式异常({stock_code_str})，跳过")
        return None
    if stock_code_str.startswith(("688", "689")):
        max_pct = 20.5  # 科创板 STAR
    elif stock_code_str.startswith(("300", "301")):
        max_pct = 20.5  # 创业板 ChiNext
    elif stock_code_str.startswith("8"):
        max_pct = 30.5  # 北交所 BSE
    else:
        max_pct = 10.5  # 主板（含 ST 由数据验证兜底）
    if abs(pct_chg) > max_pct:
        print(f"⚠️  数据异常: {stock_code} {pred_trade_date}->{next_row['date']} "
              f"pct_chg={pct_chg:.2f}% 超出板块涨跌幅限制(±{max_pct-0.5:.0f}%)，跳过")
        return None

    return {
        "pred_trade_date": pred_trade_date,
        "next_date": next_row["date"],
        "next_close": next_close,
        "pct_chg": pct_chg,
        "days_offset": None,  # 后面计算
    }


def cmd_check() -> Tuple[int, int]:
    """
    遍历所有未匹配行情的bt_predictions,从unified_cache获取次日行情并标记.
    返回 (已匹配数, 已正确数).
    """
    # 检查缓存DB是否存在
    if not CACHE_DB.exists():
        print(f"⚠️ 统一缓存DB不存在: {CACHE_DB}")
        print("  请先运行 cache_cli.py warm 预热缓存")
        return 0, 0

    conn_bt = _get_db()
    conn_cache = sqlite3.connect(str(CACHE_DB))
    conn_cache.row_factory = sqlite3.Row

    # 找出所有未匹配的预测(包括之前没找到次日数据的)
    pending = conn_bt.execute("""
        SELECT id, date, stock_code, fusion_dir, ly_dir, ml_dir, at_dir
        FROM bt_predictions
        WHERE next_date IS NULL
        ORDER BY date ASC
    """).fetchall()

    if not pending:
        print("✅ 所有预测均已匹配,无需更新")
        conn_bt.close()
        conn_cache.close()
        return 0, 0

    matched = 0
    correct = {"fusion": 0, "ly": 0, "ml": 0, "at": 0}
    total = {"fusion": 0, "ly": 0, "ml": 0, "at": 0}

    for row in pending:
        pid = row["id"]
        date = row["date"]
        code = row["stock_code"]

        next_day = _get_pred_and_next_close(conn_cache, date, code)
        if next_day is None:
            continue  # 还不够远,下次再查

        pct_chg = next_day["pct_chg"]
        if pct_chg is None:
            continue

        actual_dir = 1 if pct_chg > 0 else (-1 if pct_chg < 0 else 0)

        # 计算每个系统是否正确
        fusion_correct = _is_correct(row["fusion_dir"], actual_dir)
        ly_correct = _is_correct(row["ly_dir"], actual_dir)
        ml_correct = _is_correct(row["ml_dir"], actual_dir)
        at_correct = _is_correct(row["at_dir"], actual_dir)

        days_offset = _count_trading_days_between(conn_cache, code, date, next_day["next_date"])

        conn_bt.execute("""
            UPDATE bt_predictions SET
                next_date = ?,
                next_pct_chg = ?,
                next_close = ?,
                days_offset = ?,
                fusion_correct = ?,
                ly_correct = ?,
                ml_correct = ?,
                at_correct = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ?
        """, (
            next_day["next_date"], next_day["pct_chg"], next_day["next_close"],
            days_offset,
            fusion_correct, ly_correct, ml_correct, at_correct,
            pid,
        ))
        matched += 1

        # 统计
        if fusion_correct is not None:
            total["fusion"] += 1
            if fusion_correct:
                correct["fusion"] += 1
        if ly_correct is not None:
            total["ly"] += 1
            if ly_correct:
                correct["ly"] += 1
        if ml_correct is not None:
            total["ml"] += 1
            if ml_correct:
                correct["ml"] += 1
        if at_correct is not None:
            total["at"] += 1
            if at_correct:
                correct["at"] += 1

    conn_bt.commit()
    conn_bt.close()
    conn_cache.close()

    if matched > 0:
        print(f"✅ 本次匹配 {matched} 条预测")
        _print_accuracy_summary(correct, total)
    else:
        print("ℹ️  还没有可匹配的次日行情数据(可能cache不足或日期太近)")

    return matched, correct["fusion"]


def _is_correct(pred_dir: Optional[int], actual_dir: int) -> Optional[int]:
    """
    判断预测方向是否正确.
    pred_dir: 1=看多, -1=看空, 0=中性, None=无数据
    actual_dir: 1=涨, -1=跌, 0=平
    返回: 1=正确, 0=错误, None=无法判断(中性预测)
    """
    if pred_dir is None or pred_dir == 0:
        return None  # 中性预测,不纳入准确率统计
    return 1 if pred_dir == actual_dir else 0


def _count_trading_days_between(conn: sqlite3.Connection, stock_code: str,
                                 date1: str, date2: str) -> int:
    """计算两个日期之间的交易日数."""
    row = conn.execute("""
        SELECT COUNT(*) as n
        FROM daily_ohlcv
        WHERE stock_code = ? AND date > ? AND date <= ?
    """, (stock_code, date1, date2)).fetchone()
    return row["n"] if row else 0


def _print_accuracy_summary(correct: Dict[str, int], total: Dict[str, int]) -> None:
    """打印实时准确率摘要."""
    systems = [
        ("融合", "fusion"),
        ("lynx", "ly"),
        ("mindlynx", "ml"),
        ("tradingagent", "at"),
    ]
    print("  ── 实时准确率 ──")
    for name, key in systems:
        t = total.get(key, 0)
        c = correct.get(key, 0)
        pct = f"{c/t*100:.1f}%" if t > 0 else "N/A"
        print(f"    {name:12s}: {c}/{t} ({pct})")


# ── Update (record + check 合并) ─────────────────────────

def cmd_update(target_date: Optional[str] = None) -> None:
    """record + check = 每日一次."""
    date = target_date or datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*50}")
    print(f"  回测更新 [{date}]")
    print(f"{'='*50}")

    n_recorded = cmd_record(date)
    n_matched, _ = cmd_check()

    # 输出摘要
    conn = _get_db()
    stats = _compute_stats(conn)
    conn.close()

    if stats["total_predictions"] > 0:
        print(f"\n── 累计概况 ──")
        print(f"  总预测: {stats['total_predictions']} | 已匹配: {stats['total_matched']}")
        print(f"  融合准确率: {stats['fusion_correct']}/{stats['fusion_total']} "
              f"({stats['fusion_pct']:.1f}%)")
        print(f"  回测天数: {stats['backtest_days']}")


# ── Report ────────────────────────────────────────────────

def _compute_stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    """从回测DB计算累计统计数据."""
    stats = {}

    # 基础计数
    stats["total_predictions"] = conn.execute(
        "SELECT COUNT(*) FROM bt_predictions").fetchone()[0]
    stats["total_matched"] = conn.execute(
        "SELECT COUNT(*) FROM bt_predictions WHERE next_pct_chg IS NOT NULL").fetchone()[0]
    stats["total_unmatched"] = conn.execute(
        "SELECT COUNT(*) FROM bt_predictions WHERE next_pct_chg IS NULL").fetchone()[0]

    # 日期范围
    r = conn.execute("SELECT MIN(date), MAX(date) FROM bt_predictions").fetchone()
    stats["date_range"] = (r[0], r[1])
    stats["backtest_days"] = conn.execute(
        "SELECT COUNT(DISTINCT date) FROM bt_predictions").fetchone()[0]

    # 各系统准确率
    for name, field in [("融合", "fusion"), ("Lynx", "ly"),
                         ("MindLynx", "ml"), ("TradingAgent", "at")]:
        c = conn.execute(
            f"SELECT COUNT(*) FROM bt_predictions "
            f"WHERE {field}_correct = 1").fetchone()[0]
        t = conn.execute(
            f"SELECT COUNT(*) FROM bt_predictions "
            f"WHERE {field}_correct IS NOT NULL").fetchone()[0]
        stats[f"{field}_correct"] = c
        stats[f"{field}_total"] = t
        stats[f"{field}_pct"] = (c / t * 100) if t > 0 else 0.0

    # 子系统可用性统计
    for name, field in [("Lynx", "ly"), ("MindLynx", "ml"), ("TradingAgent", "at")]:
        stats[f"{field}_available"] = conn.execute(
            f"SELECT COUNT(*) FROM bt_predictions WHERE {field}_valid = 1").fetchone()[0]
        stats[f"{field}_available_pct"] = round(
            stats[f"{field}_available"] / stats["total_predictions"] * 100, 1
        ) if stats["total_predictions"] > 0 else 0.0

    # 分歧场景分析
    stats["disagreement_count"] = conn.execute(
        "SELECT COUNT(*) FROM bt_predictions WHERE has_disagreement = 1").fetchone()[0]
    stats["disagreement_matched"] = conn.execute(
        "SELECT COUNT(*) FROM bt_predictions WHERE has_disagreement = 1 "
        "AND fusion_correct IS NOT NULL").fetchone()[0]
    stats["disagreement_correct"] = conn.execute(
        "SELECT COUNT(*) FROM bt_predictions WHERE has_disagreement = 1 "
        "AND fusion_correct = 1").fetchone()[0]
    stats["disagreement_pct"] = (
        stats["disagreement_correct"] / stats["disagreement_matched"] * 100
    ) if stats["disagreement_matched"] > 0 else 0.0

    # 非分歧场景准确率
    stats["no_disagreement_matched"] = conn.execute(
        "SELECT COUNT(*) FROM bt_predictions WHERE has_disagreement = 0 "
        "AND fusion_correct IS NOT NULL").fetchone()[0]
    stats["no_disagreement_correct"] = conn.execute(
        "SELECT COUNT(*) FROM bt_predictions WHERE has_disagreement = 0 "
        "AND fusion_correct = 1").fetchone()[0]
    stats["no_disagreement_pct"] = (
        stats["no_disagreement_correct"] / stats["no_disagreement_matched"] * 100
    ) if stats["no_disagreement_matched"] > 0 else 0.0

    # 各信号方向分布
    stats["signal_breakdown"] = conn.execute("""
        SELECT signal, COUNT(*) as cnt,
               SUM(CASE WHEN fusion_correct = 1 THEN 1 ELSE 0 END) as correct,
               SUM(CASE WHEN fusion_correct IS NOT NULL THEN 1 ELSE 0 END) as total
        FROM bt_predictions
        WHERE signal != ''
        GROUP BY signal
        ORDER BY cnt DESC
    """).fetchall()

    # 近期趋势 (最近N天每天的准确率)
    stats["daily_trend"] = conn.execute("""
        SELECT date,
               COUNT(*) as total,
               SUM(CASE WHEN fusion_correct = 1 THEN 1 ELSE 0 END) as correct,
               SUM(CASE WHEN fusion_correct IS NOT NULL THEN 1 ELSE 0 END) as evaluated
        FROM bt_predictions
        GROUP BY date
        ORDER BY date DESC
        LIMIT 14
    """).fetchall()

    # 盈亏比（基于模拟交易数据：平均盈利/平均亏损）
    win_loss = conn.execute("""
        SELECT
            AVG(CASE WHEN next_pct_chg > 0 THEN next_pct_chg ELSE NULL END) as avg_win,
            AVG(CASE WHEN next_pct_chg < 0 THEN ABS(next_pct_chg) ELSE NULL END) as avg_loss
        FROM bt_predictions
        WHERE fusion_dir IS NOT NULL AND next_pct_chg IS NOT NULL
          AND fusion_dir = CASE WHEN next_pct_chg > 0 THEN 1 ELSE -1 END
    """).fetchone()
    stats["avg_win_pct"] = round(win_loss[0], 2) if win_loss and win_loss[0] else 0.0
    stats["avg_loss_pct"] = round(win_loss[1], 2) if win_loss and win_loss[1] else 0.0
    stats["win_loss_ratio"] = (
        round(stats["avg_win_pct"] / stats["avg_loss_pct"], 2)
        if stats["avg_loss_pct"] > 0 else 0.0
    )

    # 最大连续亏损（基于融合方向判断）
    stats["max_consecutive_losses"] = 0
    con_sec = conn.execute("""
        SELECT fusion_correct FROM bt_predictions
        WHERE fusion_correct IS NOT NULL
        ORDER BY date ASC, stock_code ASC
    """).fetchall()
    if con_sec:
        cur_streak = 0
        for row in con_sec:
            if row[0] == 0:  # 判断错误
                cur_streak += 1
                stats["max_consecutive_losses"] = max(stats["max_consecutive_losses"], cur_streak)
            else:
                cur_streak = 0

    # 个股统计
    stats["per_stock"] = conn.execute("""
        SELECT stock_code, stock_name,
               COUNT(*) as total,
               SUM(CASE WHEN fusion_correct = 1 THEN 1 ELSE 0 END) as correct,
               SUM(CASE WHEN fusion_correct IS NOT NULL THEN 1 ELSE 0 END) as evaluated
        FROM bt_predictions
        GROUP BY stock_code
        ORDER BY evaluated DESC
    """).fetchall()

    return stats


def cmd_report(detail: bool = False) -> None:
    """生成累计回测报告."""
    conn = _get_db()
    stats = _compute_stats(conn)
    conn.close()

    date_from, date_to = stats["date_range"]
    print(f"\n{'='*55}")
    print(f"  融合系统回测报告 — {date_from} ~ {date_to}")
    print(f"{'='*55}")

    if stats["total_predictions"] == 0:
        print("  ❌ 无数据,请先运行 python scripts/backtest.py update")
        return

    print(f"\n  📊 样本概况")
    print(f"     总预测: {stats['total_predictions']} 条")
    print(f"     已匹配: {stats['total_matched']} 条 (未匹配: {stats['total_unmatched']})")
    print(f"     回测天数: {stats['backtest_days']} 天")
    print(f"     股票数: {len(stats['per_stock'])} 只")

    print(f"\n  📈 方向准确率 (T+1)")
    print("     {:12s} {:>6s}/{:<6s}".format("系统", "正确", ""))
    for name, field in [("融合", "fusion"), ("Lynx", "ly"),
                         ("MindLynx", "ml"), ("TradingAgent", "at")]:
        c = stats[f"{field}_correct"]
        t = stats[f"{field}_total"]
        pct = stats[f"{field}_pct"]
        bar = _bar(pct, 20)
        print(f"     {name:12s} {c:4d}/{t:<4d}   {pct:5.1f}% {bar}")

    print(f"\n  🔀 分歧场景分析")
    print(f"     分歧次数: {stats['disagreement_count']} 次")
    if stats["disagreement_matched"] > 0:
        print(f"     分歧时融合准确率: {stats['disagreement_correct']}/{stats['disagreement_matched']} "
              f"({stats['disagreement_pct']:.1f}%)")
    if stats["no_disagreement_matched"] > 0:
        print(f"     无分歧时融合准确率: {stats['no_disagreement_correct']}/{stats['no_disagreement_matched']} "
              f"({stats['no_disagreement_pct']:.1f}%)")

    print(f"\n  🏷️  信号分布")
    for row in stats["signal_breakdown"]:
        signal = row["signal"]
        cnt = row["cnt"]
        corr = row["correct"]
        tot = row["total"]
        pct = f"{corr/tot*100:.1f}%" if tot > 0 else "N/A"
        print(f"     {signal:20s}: {cnt:3d}次 | 准确率 {pct}")

    print(f"\n  📅 近期逐日准确率")
    for row in stats["daily_trend"]:
        d = row["date"]
        c = row["correct"]
        t = row["evaluated"]
        tot = row["total"]
        pct = f"{c/t*100:.1f}%" if t > 0 else "—"
        bar = _bar(c/t*100, 15) if t > 0 else ""
        print(f"     {d}  {c:2d}/{t:<2d} 有效 {pct:>6s} {bar}")

    if detail:
        print(f"\n  🏢 个股统计")
        for row in stats["per_stock"]:
            code = row["stock_code"]
            name = row["stock_name"]
            c = row["correct"]
            t = row["evaluated"]
            tot = row["total"]
            pct = f"{c/t*100:.1f}%" if t > 0 else "—"
            print(f"     {code} {name:8s}: {c:2d}/{t:<2d} ({pct}) [{tot}次预测]")

    # 融合 vs 最优单系统
    print(f"\n  🏆 融合 vs 最优单系统")
    sys_acc = [
        ("Lynx", stats["ly_pct"]),
        ("MindLynx", stats["ml_pct"]),
        ("TradingAgent", stats["at_pct"]),
    ]
    sys_acc.sort(key=lambda x: x[1], reverse=True)
    best_name, best_pct = sys_acc[0]
    fusion_pct = stats["fusion_pct"]
    diff = fusion_pct - best_pct
    if diff > 0:
        verdict = f"融合领先 {best_name} {diff:.1f}%"
    elif diff < 0:
        verdict = f"融合落后 {best_name} {abs(diff):.1f}%"
    else:
        verdict = "融合与最优单系统持平"
    print(f"     最优单系统: {best_name} ({best_pct:.1f}%)")
    print(f"     融合系统:   {fusion_pct:.1f}%")
    print(f"     结论: {verdict}")

    # 优势摘要
    print(f"\n  💡 关键指标")
    has_enough_data = stats["total_matched"] >= 30
    print(f"     样本充足: {'✅' if has_enough_data else '❌'} ({stats['total_matched']}/30)")
    if stats["avg_win_pct"] > 0 or stats["avg_loss_pct"] > 0:
        wl = stats["win_loss_ratio"]
        wl_flag = "✅" if wl > 1.5 else ("⚠️" if wl > 1.0 else "❌")
        print(f"     盈亏比: {wl_flag} {wl:.2f} (平均盈利{stats['avg_win_pct']:.2f}% / 平均亏损{stats['avg_loss_pct']:.2f}%)")
    if stats["max_consecutive_losses"] > 0:
        mcl = stats["max_consecutive_losses"]
        mcl_flag = "✅" if mcl <= 5 else ("⚠️" if mcl <= 7 else "❌")
        print(f"     最大连续亏损: {mcl_flag} {mcl} 次")

    print(f"\n  🔌 子系统数据可用率 (有数据天数/总天数)")
    for name, field in [("Lynx", "ly"), ("MindLynx", "ml"), ("TradingAgent", "at")]:
        avail = stats[f"{field}_available"]
        pct = stats[f"{field}_available_pct"]
        bar = _bar(pct, 15)
        print(f"     {name:12s}: {avail}/{stats['total_predictions']} ({pct}%) {bar}")
    if not has_enough_data:
        print(f"     还需 {30 - stats['total_matched']} 个匹配样本才能做统计意义的分析")

    print()


def _bar(pct: float, width: int = 20) -> str:
    """生成ASCII进度条."""
    if pct <= 0:
        return "░" * width
    filled = int(pct / 100 * width)
    filled = max(0, min(filled, width))
    return "█" * filled + "░" * (width - filled)


# ── 模拟交易 ─────────────────────────────────────────

def cmd_simulate() -> None:
    """基于融合信号模拟交易，输出收益曲线和风险指标。"""
    conn = _get_db()

    # 读取所有已匹配的预测，按日期排序
    rows = conn.execute("""
        SELECT date, stock_code, stock_name,
               fusion_dir, next_pct_chg, next_close, fusion_correct
        FROM bt_predictions
        WHERE fusion_dir IS NOT NULL AND next_pct_chg IS NOT NULL
        ORDER BY date ASC, stock_code ASC
    """).fetchall()

    if not rows:
        print("❌ 没有可用的匹配数据，请先运行 backtest.py check")
        conn.close()
        return

    # 按日期分组，每天的投资组合
    from collections import OrderedDict
    daily_portfolios: dict[str, list] = OrderedDict()
    for r in rows:
        daily_portfolios.setdefault(r["date"], []).append(r)

    INITIAL_CAPITAL = 100_000.0
    cash = INITIAL_CAPITAL
    position_value = 0.0
    trade_count = 0
    win_count = 0
    loss_count = 0
    nav_curve: list[tuple[str, float]] = []
    daily_returns: list[float] = []

    for date, stocks in daily_portfolios.items():
        day_pnl = 0.0
        day_trades = 0
        for r in stocks:
            if r["fusion_dir"] == 1:  # 看多 → 买入
                ret = (r["next_pct_chg"] or 0) / 100.0
                # 假设等权重分配资金：每只股票分配 cash / N 的资金
                capital_per_stock = cash / len(stocks)
                pnl = capital_per_stock * ret
                day_pnl += pnl
                trade_count += 1
                day_trades += 1
                if ret > 0:
                    win_count += 1
                elif ret < 0:
                    loss_count += 1

        total_value = cash + day_pnl
        daily_ret = (total_value - cash - position_value) / (cash + position_value) if (cash + position_value) > 0 else 0
        cash = total_value
        nav_curve.append((date, cash))

    conn.close()

    total_value = cash if nav_curve else INITIAL_CAPITAL
    total_return = (total_value - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    win_rate = win_count / (win_count + loss_count) * 100 if (win_count + loss_count) > 0 else 0

    # 计算夏普比率 (假设无风险利率 0)
    daily_rets = []
    for i in range(1, len(nav_curve)):
        prev_nav = nav_curve[i - 1][1]
        curr_nav = nav_curve[i][1]
        if prev_nav > 0:
            daily_rets.append((curr_nav - prev_nav) / prev_nav)

    import statistics, math
    avg_ret = statistics.mean(daily_rets) if daily_rets else 0
    std_ret = statistics.stdev(daily_rets) if len(daily_rets) > 1 else 1
    sharpe = (avg_ret / std_ret) * math.sqrt(252) if std_ret > 0 else 0

    # 最大回撤
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    for _, nav in nav_curve:
        if nav > peak:
            peak = nav
        dd = (peak - nav) / peak * 100
        max_dd = max(max_dd, dd)

    # 输出
    print(f"\n{'='*55}")
    print(f"  融合系统模拟交易报告")
    print(f"{'='*55}")
    print(f"\n  📊 基本参数")
    print(f"     初始资金: ¥{INITIAL_CAPITAL:,.0f}")
    print(f"     最终净值: ¥{total_value:,.0f}")
    print(f"     总收益率: {total_return:+.2f}%")
    print(f"     交易次数: {trade_count}")
    print(f"     回测天数: {len(nav_curve)} 天")
    print(f"\n  📈 绩效指标")
    print(f"     年化夏普比率: {sharpe:.2f}")
    print(f"     最大回撤: {max_dd:.2f}%")
    print(f"     胜率: {win_rate:.1f}% ({win_count}/{win_count + loss_count})")
    print(f"     日均收益: {avg_ret*100:.3f}%")
    print(f"     收益波动率: {std_ret*100:.3f}%")

    print(f"\n  📅 净值曲线 (每日)")
    for date, nav in nav_curve:
        pct = (nav - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
        bar_len = max(1, int(abs(pct)))
        bar = "█" * min(bar_len, 30) if pct >= 0 else "░" * min(bar_len, 30)
        print(f"     {date}  ¥{nav:>8,.0f}  {pct:+.1f}%  {bar}")

    print(f"\n{'='*55}")
    print(f"  模拟交易完成")
    print(f"{'='*55}")


# ── Fix Valid (回填旧CSV的子系统valid字段) ────────────────

KNOWN_DEFAULTS = {-0.1, 0.0}

def cmd_fix_valid() -> None:
    """回填旧CSV中缺失的子系统valid字段。

    旧格式（5月29日~6月5日）fusion CSV中没有 lynx_valid /
    mindlynx_valid / tradingagent_valid 列。回填规则：

    1. 对每个日期 + 子系统，检查全量股票的score是否全部一致
    2. 如果全部一致且值为已知默认值（-0.1 或 0.0）→ 全部 valid=False
    3. 否则逐个股：score ≠ 0 且非空 → valid=True
    """
    conn = _get_db()
    fixed = {"ly": 0, "ml": 0, "at": 0}
    unchanged = {"ly": 0, "ml": 0, "at": 0}
    subsystem_cols = [
        ("ly_valid", "ly_score", 0.0),
        ("ml_valid", "ml_score", -0.1),
        ("at_valid", "at_score", 0.0),
    ]

    # 按日期分组找出所有valid=0的记录所在的日期
    pending = conn.execute("""
        SELECT DISTINCT p.date
        FROM bt_predictions p
        WHERE p.ly_valid = 0 OR p.ml_valid = 0 OR p.at_valid = 0
        ORDER BY p.date
    """).fetchall()

    if not pending:
        print("✅ 无需要回填的记录")
        conn.close()
        return

    print(f"需回填日期: {[r['date'] for r in pending]}")

    for row in pending:
        date = row["date"]

        # 获取该日期所有股票的各子系统score
        scores = conn.execute("""
            SELECT stock_code, ly_score, ml_score, at_score
            FROM bt_predictions WHERE date = ?
        """, (date,)).fetchall()

        if not scores:
            continue

        # 检查每个子系统在该日期是否全部统一默认值
        for valid_col, score_col, default_val in subsystem_cols:
            vals = [r[score_col] for r in scores if r[score_col] is not None]
            if not vals:
                continue

            is_all_same = len(set(vals)) == 1
            is_all_default = is_all_same and vals[0] in KNOWN_DEFAULTS

            for sr in scores:
                code = sr["stock_code"]
                sv = sr[score_col]

                # 规则: 统一默认 → valid=False; score=0/None/空 → valid=False; 否则 valid=True
                if is_all_default:
                    new_valid = 0
                else:
                    new_valid = 1 if (sv is not None and sv != 0.0) else 0

                old_valid = conn.execute(
                    f"SELECT {valid_col} FROM bt_predictions WHERE date=? AND stock_code=?",
                    (date, code)
                ).fetchone()[0]

                if old_valid == 0 and new_valid == 1:
                    conn.execute(
                        f"UPDATE bt_predictions SET {valid_col}=1, updated_at=datetime('now','localtime') "
                        f"WHERE date=? AND stock_code=?",
                        (date, code)
                    )
                    prefix = valid_col.replace("_valid", "")
                    fixed[prefix] += 1
                elif old_valid == 0 and new_valid == 0:
                    prefix = valid_col.replace("_valid", "")
                    unchanged[prefix] += 1

    conn.commit()
    conn.close()

    print(f"✅ 回填完成")
    print(f"  ly_valid: 修正 {fixed['ly']} 条, 保持0 {unchanged['ly']} 条")
    print(f"  ml_valid: 修正 {fixed['ml']} 条, 保持0 {unchanged['ml']} 条")
    print(f"  at_valid: 修正 {fixed['at']} 条, 保持0 {unchanged['at']} 条")

    print(f"\n  子系统准确率 回填后 → 运行 `python scripts/backtest.py report` 查看")


# ── 历史数据回填 ─────────────────────────────────────────

def cmd_backfill() -> int:
    """
    扫描 fusion_output 目录所有历史CSV,记录尚未入库的预测.
    这是首次部署时的初始化操作.
    """
    csv_files = sorted(FUSION_OUTPUT_DIR.glob("fusion_*.csv"))
    if not csv_files:
        print("⚠️  没有历史融合CSV文件")
        return 0

    conn = _get_db()
    existing = set(
        r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM bt_predictions").fetchall()
    )
    conn.close()

    total = 0
    for csv_path in csv_files:
        date = csv_path.stem.replace("fusion_", "")
        if date in existing:
            continue
        n = cmd_record(date)
        if n > 0:
            total += n
            print(f"  ✓ {date}: {n}条")
        else:
            print(f"  - {date}: 跳过(无有效数据)")

    if total > 0:
        print(f"\n✅ 历史回填完成,共 {total} 条记录")
        cmd_check()
    else:
        print("ℹ️  没有新的历史数据需要回填")

    return total


# ── WalkForward验证 ───────────────────────────────────────────

def cmd_walkforward(
    train_window: int = 20,
    test_window: int = 10,
    step: int = 5,
) -> None:
    """
    WalkForward验证：滑动窗口检验融合准确率的样本外稳定性。

    将回测数据按日期排序，用滑动窗口分割为训练集和测试集，
    分别计算样本内(IS)和样本外(OOS)准确率，通过衰减比判断是否过拟合。

    Args:
        train_window: 训练窗口（交易日数）
        test_window:  验证窗口（交易日数）
        step:         滑动步长
    """
    conn = _get_db()
    rows = conn.execute(
        "SELECT date, fusion_correct FROM bt_predictions "
        "WHERE fusion_correct IS NOT NULL ORDER BY date"
    ).fetchall()
    conn.close()

    if not rows:
        print("⚠️  没有足够的回测数据（需要至少1条有T+1结果的记录）")
        return

    # 按日期分组计算每日准确率
    from collections import OrderedDict
    daily: dict[str, list[int]] = OrderedDict()
    for r in rows:
        daily.setdefault(r["date"], []).append(r["fusion_correct"])

    dates = list(daily.keys())
    n = len(dates)

    if n < train_window + test_window:
        print(f"⚠️  数据不足: 需要至少 {train_window + test_window} 个交易日"
              f"（当前 {n} 天）")
        return

    print(f"\n{'='*60}")
    print(f"  WalkForward 验证")
    print(f"{'='*60}")
    print(f"  回测区间: {dates[0]} ~ {dates[-1]} ({n} 交易日)")
    print(f"  训练窗口: {train_window}天 | 验证窗口: {test_window}天 | 步长: {step}天\n")

    windows = []
    for start in range(0, n - train_window - test_window + 1, step):
        train_dates = dates[start:start + train_window]
        test_dates = dates[start + train_window:start + train_window + test_window]

        train_correct = sum(daily[d].count(1) for d in train_dates)
        train_total = sum(len(daily[d]) for d in train_dates)
        test_correct = sum(daily[d].count(1) for d in test_dates)
        test_total = sum(len(daily[d]) for d in test_dates)

        is_acc = train_correct / train_total * 100 if train_total > 0 else 0
        oos_acc = test_correct / test_total * 100 if test_total > 0 else 0
        windows.append({
            "train": f"{train_dates[0]}~{train_dates[-1]}",
            "test": f"{test_dates[0]}~{test_dates[-1]}",
            "is_acc": is_acc,
            "oos_acc": oos_acc,
            "oos_positive": oos_acc > 50,
            "train_n": train_total,
            "test_n": test_total,
        })

    if not windows:
        print("⚠️  无法构建有效的滑动窗口")
        return

    is_accs = [w["is_acc"] for w in windows]
    oos_accs = [w["oos_acc"] for w in windows]
    mean_is = sum(is_accs) / len(is_accs)
    mean_oos = sum(oos_accs) / len(oos_accs)
    oos_pos_ratio = sum(1 for w in windows if w["oos_positive"]) / len(windows)
    decay = (mean_is - mean_oos) / mean_is if mean_is > 0.01 else 0.0

    is_robust = decay < 0.4 and mean_oos > 50

    if is_robust and mean_oos > 55:
        verdict = "✅ 策略稳健 — OOS准确率>55%且衰减可控"
    elif is_robust:
        verdict = "⚠️ 可接受 — 衰减在合理范围内，但OOS准确率偏低"
    elif decay >= 0.4:
        verdict = "❌ 严重过拟合 — OOS衰减>{:.0%}".format(decay)
    else:
        verdict = "❌ 策略无效 — OOS无正向准确率"

    print(f"  ── 各窗口结果 ──")
    print(f"  {'窗口':<40} {'IS准确率':<10} {'OOS准确率':<10} {'样本数':<10}")
    print(f"  {'-'*40} {'-'*10} {'-'*10} {'-'*10}")
    for w in windows:
        mark = " ✓" if w["oos_positive"] else ""
        print(f"  {w['train']}~{w['test']:<12} {w['is_acc']:>5.1f}%{'':<4} "
              f"{w['oos_acc']:>5.1f}%{'':<4} {w['train_n']+w['test_n']}{mark}")

    print(f"\n  ── WalkForward 结论 ──")
    print(f"  平均IS准确率: {mean_is:.1f}%")
    print(f"  平均OOS准确率: {mean_oos:.1f}%")
    print(f"  OOS正向窗口占比: {oos_pos_ratio:.0%}")
    print(f"  准确率衰减: {decay:.1%}")
    print(f"  {verdict}")
    print()


# ── 权重网格扫描 ──────────────────────────────────────────────

def cmd_weight_sweep() -> None:
    """
    网格搜索权重组合，输出各组合准确率曲面。

    从 settings.yaml 读取 weight_search_range 配置，
    对 bt_predictions 中已有T+1结果的记录，枚举所有权重组合重算融合准确率。
    """
    cfg_path = os.path.join(PROJECT_ROOT, "config", "settings.yaml")
    if not os.path.exists(cfg_path):
        print("⚠️  config/settings.yaml 不存在")
        return
    with open(cfg_path, "r") as f:
        cfg = yaml.safe_load(f)
    ranges = cfg.get("backtest", {}).get("weight_search_range", {})
    ly_values = ranges.get("lynx_vnpy", [0.30])
    ml_values = ranges.get("mindlynx", [0.40])
    at_values = ranges.get("tradingagent", [0.30])

    conn = _get_db()
    rows = conn.execute(
        "SELECT ly_score, ml_score, at_score, next_pct_chg, fusion_correct "
        "FROM bt_predictions WHERE fusion_correct IS NOT NULL"
    ).fetchall()
    conn.close()

    if not rows:
        print("⚠️  没有足够的历史回测数据（需要至少1条有T+1结果的记录）")
        return

    print(f"\n{'='*60}")
    print(f"  权重网格扫描 — {len(rows)} 条历史记录")
    print(f"{'='*60}")
    print(f"  ly: {ly_values}")
    print(f"  ml: {ml_values}")
    print(f"  at: {at_values}")
    n_combo = len(ly_values) * len(ml_values) * len(at_values)
    print(f"  共 {n_combo} 种组合\n")

    results = []
    for lw in ly_values:
        for mw in ml_values:
            for aw in at_values:
                total = len(rows)
                correct = 0
                for row in rows:
                    ly_s = row[0] or 0.0
                    ml_s = row[1] or 0.0
                    at_s = row[2] or 0.0
                    fusion = ly_s * lw + ml_s * mw + at_s * aw
                    pred_dir = 1 if fusion > 0.1 else (-1 if fusion < -0.1 else 0)
                    if pred_dir == 0:
                        total -= 1
                        continue
                    actual_dir = 1 if (row[3] or 0) > 0 else -1
                    if pred_dir == actual_dir:
                        correct += 1
                acc = correct / total * 100 if total > 0 else 0.0
                results.append(((lw, mw, aw), acc, correct, total))

    results.sort(key=lambda x: x[1], reverse=True)

    print(f"  {'权重(l,m,a)':<25} {'准确率':<10} {'正确/总数':<15}")
    print(f"  {'-'*25} {'-'*10} {'-'*15}")
    for i, ((lw, mw, aw), acc, cor, tot) in enumerate(results[:10]):
        curr = "(0.30, 0.40, 0.30)"
        tag = " ★ 当前" if (lw, mw, aw) == eval(curr) else ""
        print(f"  ({lw:.2f},{mw:.2f},{aw:.2f}){'':<11} {acc:>5.1f}%{'':<5} {cor}/{tot}{tag}")

    print(f"\n  ── 维度敏感度 ──")
    for dim_name in ("ly", "ml", "at"):
        dim_key = {"ly":"lynx_vnpy", "ml":"mindlynx", "at":"tradingagent"}[dim_name]
        dim_values = ranges.get(dim_key, [])
        if len(dim_values) < 2:
            continue
        accs = []
        for dv in dim_values:
            idx = {"ly":0, "ml":1, "at":2}[dim_name]
            subset = [r for r in results if r[0][idx] == dv]
            if subset:
                avg_acc = sum(r[1] for r in subset) / len(subset)
                accs.append((dv, avg_acc))
        if not accs:
            continue
        spread = max(a[1] for a in accs) - min(a[1] for a in accs)
        print(f"    {dim_name}: {spread:.1f}% 敏感度")
        for dv, avg in accs:
            mark = " ← 当前" if dim_name == "ly" and dv == 0.30 or dim_name == "ml" and dv == 0.40 or dim_name == "at" and dv == 0.30 else ""
            print(f"      {dim_name}={dv:.2f}: {avg:.1f}%{mark}")

    best = results[0]
    print(f"\n  ✅ 最优: ({best[0][0]:.2f}, {best[0][1]:.2f}, {best[0][2]:.2f}) → {best[1]:.1f}%")


# ── CLI ───────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="融合系统回测工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="初始化回测数据库")
    p_record = sub.add_parser("record", help="记录当日融合预测到回测DB")
    p_record.add_argument("--date", type=str, default=None,
                          help="指定日期 (YYYY-MM-DD), 默认今天")
    p_check = sub.add_parser("check", help="匹配已记录预测与次日实际行情")
    p_update = sub.add_parser("update", help="record + check (每日一次)")
    p_update.add_argument("--date", type=str, default=None,
                          help="指定日期 (YYYY-MM-DD), 默认今天")
    p_report = sub.add_parser("report", help="生成累计回测报告")
    p_report.add_argument("--detail", action="store_true",
                          help="显示个股明细")
    p_backfill = sub.add_parser("backfill", help="扫描历史CSV回填数据库(首次部署用)")
    p_fix_valid = sub.add_parser("fix-valid", help="回填旧CSV格式缺失的子系统valid字段")
    p_simulate = sub.add_parser("simulate", help="模拟交易：基于融合信号计算累计收益曲线")
    p_weightsweep = sub.add_parser("weight-sweep", help="网格搜索权重组合，输出准确率曲面与敏感度分析")
    p_walkforward = sub.add_parser("walkforward", help="WalkForward验证：滑动窗口检验样本外准确率稳定性")
    p_walkforward.add_argument("--train", type=int, default=20, help="训练窗口天数(默认20)")
    p_walkforward.add_argument("--test", type=int, default=10, help="验证窗口天数(默认10)")
    p_walkforward.add_argument("--step", type=int, default=5, help="滑动步长(默认5)")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init()
    elif args.command == "record":
        cmd_record(args.date)
    elif args.command == "check":
        cmd_check()
    elif args.command == "update":
        cmd_update(args.date)
    elif args.command == "report":
        cmd_report(detail=args.detail)
    elif args.command == "backfill":
        cmd_backfill()
    elif args.command == "fix-valid":
        cmd_fix_valid()
    elif args.command == "simulate":
        cmd_simulate()
    elif args.command == "weight-sweep":
        cmd_weight_sweep()
    elif args.command == "walkforward":
        cmd_walkforward(train_window=args.train, test_window=args.test, step=args.step)


if __name__ == "__main__":
    main()
