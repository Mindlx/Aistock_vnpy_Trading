#!/usr/bin/env python3
"""
c1test — 统一回测编排器 (Phase 1)

单入口执行全系统回测，输出统一 JSON + Markdown 报告。

用法:
    python scripts/c1test.py                    # quick: 融合回测 + 缓存子系统数据
    python scripts/c1test.py --full             # 全面: 融合 + LY + ML + AT 全量回测
    python scripts/c1test.py --quick            # 同默认 (快速)
    python scripts/c1test.py --report           # 只看上次报告，不重跑
    python scripts/c1test.py --push             # 跑完后推送企业微信

数据流:
    1. 融合回测: 子进程调用 backtest.py update/report → 解析输出
    2. LY 回测:  子进程调用 lynx_signal.py --backtest
    3. ML 回测:  子进程调用 main.py --backtest-report + 直查 stock_analysis.db
    4. AT 回测:  从 bt_results.db 提取 AT 方向准确率
    5. 统一报告: 合并 → data/c1test/unified_report.json + unified_report.md

⚠️ 仅供学习和研究目的，不构成任何投资建议
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKTEST_DB = PROJECT_ROOT / "data" / "backtest" / "bt_results.db"
C1TEST_DIR = PROJECT_ROOT / "data" / "c1test"
LAST_RUN_FILE = C1TEST_DIR / "last_run.json"
UNIFIED_REPORT_JSON = C1TEST_DIR / "unified_report.json"
UNIFIED_REPORT_MD = C1TEST_DIR / "unified_report.md"
FUSION_OUTPUT_DIR = PROJECT_ROOT / "data" / "fusion_output"
ML_DB = PROJECT_ROOT / "systems" / "MindLynx-Aistock" / "data" / "stock_analysis.db"

# (DIRECTION_THRESHOLD 已移除 — AT 相位改用 0.1 与融合回测对齐)


# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════

def _bar(pct: float, width: int = 20) -> str:
    """生成文本进度条"""
    filled = max(0, min(width, int(pct / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today_str() -> str:
    return date.today().isoformat()


def _save_meta(key: str, value: str) -> None:
    """写入一条 key-value 到 bt_meta (upsert)."""
    conn = sqlite3.connect(str(BACKTEST_DB))
    conn.execute("INSERT OR REPLACE INTO bt_meta (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def _get_meta(key: str) -> Optional[str]:
    """从 bt_meta 读取一条 key 的值."""
    conn = sqlite3.connect(str(BACKTEST_DB))
    cur = conn.execute("SELECT value FROM bt_meta WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


# ══════════════════════════════════════════════════════════════
# Phase 1: 融合回测
# ══════════════════════════════════════════════════════════════

def phase1_fusion() -> Dict[str, Any]:
    """运行融合回测并收集结果.
    
    1. 如果有今日 fusion CSV → backtest.py update (记录新预测)
    2. 运行 backtest.py report 并解析输出
    3. 同时直查 bt_results.db 获取精确数字
    """
    result: Dict[str, Any] = {"status": "ok", "timestamp": _now()}

    # ── Step 1: 如果有今日 CSV，更新回测 ──
    today_csv = FUSION_OUTPUT_DIR / f"fusion_{_today_str()}.csv"
    if today_csv.exists():
        print(f"  [c1test] 发现今日融合 CSV → backtest.py update")
        subprocess.run(
            [sys.executable, "scripts/backtest.py", "update"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60,
        )
    else:
        print(f"  [c1test] 无今日融合 CSV ({today_csv.name})，使用现有回测数据")

    # ── Step 2: 直查 DB 获取精确指标 ──
    if not BACKTEST_DB.exists():
        result["status"] = "error"
        result["message"] = f"回测数据库不存在: {BACKTEST_DB}"
        return result

    conn = sqlite3.connect(str(BACKTEST_DB))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 总览统计
    cursor.execute("SELECT COUNT(*) FROM bt_predictions")
    total_pred = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM bt_predictions WHERE fusion_correct IS NOT NULL")
    total_matched = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM bt_predictions WHERE fusion_correct IS NULL")
    total_unmatched = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT date) FROM bt_predictions")
    days = cursor.fetchone()[0]

    # 各系统方向准确率 (融合/ly/ml/at)
    systems = {
        "fusion": "融合",
        "ly": "Lynx",
        "ml": "MindLynx",
        "at": "TradingAgent",
    }
    accuracies = {}
    for sys_key, sys_name in systems.items():
        cursor.execute(
            f"SELECT COUNT(*) FROM bt_predictions WHERE {sys_key}_correct IS NOT NULL"
        )
        total = cursor.fetchone()[0]
        cursor.execute(
            f"SELECT COUNT(*) FROM bt_predictions WHERE {sys_key}_correct = 1"
        )
        correct = cursor.fetchone()[0]
        pct = round(correct / total * 100, 1) if total > 0 else 0.0
        accuracies[sys_key] = {
            "correct": correct,
            "total": total,
            "accuracy": pct,
        }

    # LY flat zone 统计: 被 L7 映射标记为中性而排除的信号数
    cursor.execute("""
        SELECT COUNT(*) FROM bt_predictions
        WHERE fusion_correct IS NOT NULL AND ly_valid = 1
          AND (ly_correct IS NULL OR ly_dir = 0)
    """)
    ly_flat_zone_neutral = cursor.fetchone()[0]

    # 子系统数据覆盖率 (指标信号有效的比例)
    coverage = {}
    for sys_key in ["ly", "ml", "at"]:
        cursor.execute(
            f"SELECT COUNT(*) FROM bt_predictions WHERE {sys_key}_valid = 1 "
            f"AND fusion_correct IS NOT NULL"
        )
        valid = cursor.fetchone()[0]
        coverage[sys_key] = {
            "valid": valid,
            "total": total_matched,
            "pct": round(valid / total_matched * 100, 1) if total_matched > 0 else 0.0,
        }

    # 分歧场景
    cursor.execute("""
        SELECT COUNT(*) FROM bt_predictions 
        WHERE fusion_correct IS NOT NULL AND has_disagreement = 1
    """)
    disagreement_matched = cursor.fetchone()[0]
    cursor.execute("""
        SELECT COUNT(*) FROM bt_predictions 
        WHERE fusion_correct = 1 AND has_disagreement = 1
    """)
    disagreement_correct = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM bt_predictions 
        WHERE fusion_correct IS NOT NULL AND (has_disagreement = 0 OR has_disagreement IS NULL)
    """)
    no_disagreement_matched = cursor.fetchone()[0]
    cursor.execute("""
        SELECT COUNT(*) FROM bt_predictions 
        WHERE fusion_correct = 1 AND (has_disagreement = 0 OR has_disagreement IS NULL)
    """)
    no_disagreement_correct = cursor.fetchone()[0]

    # 信号分布 (按 signal L7 标签分组)
    cursor.execute("""
        SELECT signal, COUNT(*), 
               SUM(CASE WHEN fusion_correct = 1 THEN 1 ELSE 0 END),
               SUM(CASE WHEN fusion_correct IS NOT NULL THEN 1 ELSE 0 END)
        FROM bt_predictions 
        WHERE signal IS NOT NULL
        GROUP BY signal
        ORDER BY signal
    """)
    signal_breakdown = []
    for row in cursor.fetchall():
        sig, cnt, corr, eval_total = row
        acc = round(corr / eval_total * 100, 1) if eval_total > 0 else 0.0
        signal_breakdown.append({
            "signal": sig,
            "count": cnt,
            "correct": corr,
            "evaluated": eval_total,
            "accuracy": acc,
        })

    # 个股统计
    cursor.execute("""
        SELECT stock_code, stock_name, 
               SUM(CASE WHEN fusion_correct = 1 THEN 1 ELSE 0 END),
               SUM(CASE WHEN fusion_correct IS NOT NULL THEN 1 ELSE 0 END),
               COUNT(*)
        FROM bt_predictions
        GROUP BY stock_code
        ORDER BY stock_code
    """)
    per_stock = []
    for row in cursor.fetchall():
        code, name, corr, eval_total, total = row
        acc = round(corr / eval_total * 100, 1) if eval_total > 0 else 0.0
        per_stock.append({
            "code": code, "name": name,
            "correct": corr, "evaluated": eval_total,
            "total_predictions": total, "accuracy": acc,
        })

    # 逐日趋势 (最近14天)
    cursor.execute("""
        SELECT date,
               SUM(CASE WHEN fusion_correct = 1 THEN 1 ELSE 0 END),
               SUM(CASE WHEN fusion_correct IS NOT NULL THEN 1 ELSE 0 END),
               COUNT(*)
        FROM bt_predictions
        GROUP BY date
        ORDER BY date DESC
        LIMIT 14
    """)
    daily = []
    for row in cursor.fetchall():
        d, corr, eval_total, total = row
        acc = round(corr / eval_total * 100, 1) if eval_total > 0 else 0.0
        daily.append({
            "date": d, "correct": corr, "evaluated": eval_total,
            "total": total, "accuracy": acc,
        })

    conn.close()

    # 装填 result
    result.update({
        "total_predictions": total_pred,
        "total_matched": total_matched,
        "total_unmatched": total_unmatched,
        "backtest_days": days,
        "subsystem_accuracy": accuracies,
        "subsystem_coverage": coverage,
        "ly_flat_zone_neutral": ly_flat_zone_neutral,
        "disagreement": {
            "matched": disagreement_matched,
            "correct": disagreement_correct,
            "accuracy": round(disagreement_correct / disagreement_matched * 100, 1)
            if disagreement_matched > 0 else 0.0,
            "no_disagreement_matched": no_disagreement_matched,
            "no_disagreement_correct": no_disagreement_correct,
            "no_disagreement_accuracy": round(
                no_disagreement_correct / no_disagreement_matched * 100, 1
            ) if no_disagreement_matched > 0 else 0.0,
        },
        "signal_breakdown": signal_breakdown,
        "per_stock": per_stock,
        "daily_trend": daily,
    })

    return result


# ══════════════════════════════════════════════════════════════
# Phase 2: LY 独立回测
# ══════════════════════════════════════════════════════════════

def phase2_ly(timeout: int = 180) -> Dict[str, Any]:
    """运行 LY 独立回测 (lynx_signal.py --backtest).

    解析 stdout 输出获取各股票准确率。
    """
    result: Dict[str, Any] = {"status": "ok", "timestamp": _now()}

    ly_script = PROJECT_ROOT / "systems" / "lynx_vnpy" / "lynx_signal.py"
    if not ly_script.exists():
        result["status"] = "skipped"
        result["message"] = f"LY 回测脚本不存在: {ly_script}"
        return result

    print(f"  [c1test] 运行 LY 独立回测 ...")
    try:
        proc = subprocess.run(
            [sys.executable, str(ly_script), "--backtest"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["message"] = f"LY 回测超时 ({timeout}s)"
        return result

    stdout = proc.stdout
    returncode = proc.returncode

    # 解析 stdout: 找 "总体准确率" / "个股准确率"
    overall_match = re.search(r"总体[^:]*:\s*(\d+)/(\d+)\s*\(([\d.]+)%\)", stdout)
    if overall_match:
        correct = int(overall_match.group(1))
        total = int(overall_match.group(2))
        pct = float(overall_match.group(3))
        result["overall"] = {"correct": correct, "total": total, "accuracy": pct}
    # L7 映射后准确率
    l7_match = re.search(r"L7 映射后\):\s*(\d+)/(\d+)\s*\(([\d.]+)%\)", stdout)
    if l7_match:
        result["overall_l7"] = {
            "correct": int(l7_match.group(1)),
            "total": int(l7_match.group(2)),
            "accuracy": float(l7_match.group(3)),
        }
    else:
        # fallback: 找最后一行结论
        found = False
        for line in stdout.splitlines():
            if "准确率" in line and ("%" in line):
                m = re.search(r"([\d.]+)%", line)
                if m:
                    result["overall"] = {"accuracy": float(m.group(1)), "raw": line.strip()}
                    found = True
                    break
        if not found:
            # 格式守卫: LY 输出格式可能与预期不符
            preview = stdout[:600] + ("..." if len(stdout) > 600 else "")
            result["status"] = "parse_failed"
            result["parse_error"] = "LY stdout 格式不匹配预期 regex"
            result["stdout_preview"] = preview
            print(f"  ⚠️ [c1test] LY 回测输出解析失败，原始输出前 600 字符:\n{preview}")

    # 解析个股准确率 (含 L7 映射后)
    per_stock = []
    for line in stdout.splitlines():
        m = re.match(r"\s+(\d{6})\s+(.*?):\s+([\d.]+)%\((\d+)/(\d+)\)\s*→\s*L7\s+([\d.]+)%\((\d+)/(\d+)\)", line)
        if m:
            per_stock.append({
                "code": m.group(1), "name": m.group(2).strip(),
                "raw_accuracy": float(m.group(3)),
                "raw_correct": int(m.group(4)), "raw_total": int(m.group(5)),
                "l7_accuracy": float(m.group(6)),
                "l7_correct": int(m.group(7)), "l7_total": int(m.group(8)),
            })
            continue
        m = re.match(r"\s+(\d{6})\s+(.*?):\s+([\d.]+)%\s+\((\d+)/(\d+)\)", line)
        if m:
            per_stock.append({
                "code": m.group(1), "name": m.group(2).strip(),
                "raw_accuracy": float(m.group(3)),
                "raw_correct": int(m.group(4)), "raw_total": int(m.group(5)),
            })
    result["per_stock"] = per_stock
    result["returncode"] = returncode
    result["stderr_preview"] = proc.stderr[:500] if proc.stderr else ""

    return result


# ══════════════════════════════════════════════════════════════
# Phase 3: ML 独立回测
# ══════════════════════════════════════════════════════════════

def phase3_ml() -> Dict[str, Any]:
    """收集 ML 回测数据.

    从 stock_analysis.db 直查:
    1. backtest_summaries → 操作建议胜率
    2. analysis_history → sentiment_score 方向准确率
    """
    result: Dict[str, Any] = {"status": "ok", "timestamp": _now()}

    if not ML_DB.exists():
        result["status"] = "skipped"
        result["message"] = f"ML 数据库不存在: {ML_DB}"
        return result

    conn = sqlite3.connect(str(ML_DB))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # ── 1. operation_advice → 方向准确率 (T+1, 与 sentiment 完全一致口径) ──
    # 使用与 sentiment_score 完全相同的条件:
    #   - 同一样本(同时有 sentiment_score + operation_advice)
    #   - 同一 flat zone (49-51)
    #   - 同一 T+1 窗口
    _BULLISH_KW = ("买入", "加仓", "强烈买入", "增持", "建仓", "strong buy", "buy", "add")
    _BEARISH_KW = ("卖出", "减仓", "强烈卖出", "清仓", "strong sell", "sell", "reduce")

    def _op_dir(advice: str) -> int:
        t = advice.strip().lower()
        if any(k in t for k in _BEARISH_KW):
            if any(k in t for k in ("观望", "等待", "wait")):
                return 0  # 矛盾(减仓但观望) → 中性
            return -1
        if any(k in t for k in _BULLISH_KW):
            return 1
        if any(k in t for k in ("持有", "hold", "观望", "等待", "wait", "查看复盘")):
            return 0  # 非方向性 → 中性
        return 0

    try:
        cursor.execute("""
            SELECT ah.sentiment_score, ah.operation_advice, sp.pct_chg
            FROM analysis_history ah
            JOIN stock_daily sp ON sp.code = ah.code
                AND sp.date = date(ah.created_at, '+1 day')
            WHERE ah.sentiment_score IS NOT NULL
              AND ah.operation_advice IS NOT NULL
              AND ah.operation_advice != ''
              AND sp.pct_chg IS NOT NULL
              AND ah.created_at >= date('now', '-90 days')
        """)
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        rows = []

    if rows:
        op_correct, op_total, op_neutral, op_no_dir = 0, 0, 0, 0
        for score, advice, pct_chg in rows:
            actual = 1 if pct_chg > 0 else (-1 if pct_chg < 0 else 0)

            # 与 sentiment_score 完全相同的 flat zone 逻辑
            if 49 <= score <= 51:
                op_neutral += 1
                op_correct += 1  # 中性→正确 (与 sentiment 一致)
                op_total += 1
                continue

            # 非 flat zone: 系统有方向
            # 只有 op_advice 也表达了明确方向时才评估
            pred = _op_dir(advice)
            if pred == 0:
                op_no_dir += 1  # op_advice 无表达 → 跳过
                continue

            op_total += 1
            if pred == actual:
                op_correct += 1

        result["operation_advice"] = {
            "direction_accuracy": round(op_correct / op_total * 100, 1) if op_total > 0 else 0.0,
            "correct": op_correct,
            "total": op_total,
            "neutral": op_neutral,
            "no_direction": op_no_dir,
            "source": "analysis_history T+1 (与 sentiment 完全一致口径)",
        }

    # ── 2. analysis_history → sentiment_score 方向准确率 (直查 stock_daily T+1) ──
    # 规则: sentiment_score >= 52=看多, <=48=看空, 49-51=中性
    try:
        cursor.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN direction_correct = 1 THEN 1 ELSE 0 END) as correct
            FROM (
                SELECT ah.sentiment_score, sp.pct_chg,
                       CASE
                           WHEN ah.sentiment_score >= 52 AND sp.pct_chg > 0 THEN 1
                           WHEN ah.sentiment_score <= 48 AND sp.pct_chg < 0 THEN 1
                           WHEN ah.sentiment_score BETWEEN 49 AND 51 THEN 1  -- 中性视为正确
                           ELSE 0
                       END as direction_correct
                FROM analysis_history ah
                JOIN stock_daily sp ON sp.code = ah.code
                    AND sp.date = date(ah.created_at, '+1 day')
                WHERE ah.sentiment_score IS NOT NULL
                  AND sp.pct_chg IS NOT NULL
                  AND ah.created_at >= date('now', '-90 days')
            )
        """)
        row = cursor.fetchone()
        if row and row[0] > 0:
            result["sentiment_score"] = {
                "correct": row[1] or 0,
                "total": row[0],
                "accuracy": round(row[1] / row[0] * 100, 1),
                "source": "analysis_history + stock_daily T+1",
            }

    except sqlite3.OperationalError as e:
        result["sentiment_score"] = {"status": "error", "message": str(e)}

    # ── 3. 融合等效准确率: 用融合引擎的归一化管线处理 analysis_history ──
    # 复现 fusion_engine.py 的 pipeline:
    #   normalize_mindlynx_score(sentiment, 52, 49) → × 0.8 → _sign(threshold=0.1) → vs T+1
    # 目的: 使用与融合回测一致的统计口径, 覆盖所有 ML 分析记录而非仅 19:00
    try:
        cursor.execute("""
            SELECT ah.sentiment_score, sp.pct_chg
            FROM analysis_history ah
            JOIN stock_daily sp ON sp.code = ah.code
                AND sp.date = date(ah.created_at, '+1 day')
            WHERE ah.sentiment_score IS NOT NULL
              AND sp.pct_chg IS NOT NULL
              AND ah.created_at >= date('now', '-90 days')
        """)
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        rows = []

    if rows:
        def _ml_sign(normalized: float, threshold: float = 0.1) -> int:
            if normalized > threshold:
                return 1
            if normalized < -threshold:
                return -1
            return 0

        def _normalize_v4(score: int) -> float:
            if score <= 19:
                return -3.0
            if score <= 30:
                return -2.5
            if score <= 40:
                return -2.0
            if score <= 48:
                return -1.5
            if score <= 51:
                return 0.0
            if score <= 59:
                return 0.8
            if score <= 79:
                return 1.0
            return 1.5

        correct, total, neutral = 0, 0, 0
        for score, pct_chg in rows:
            l7 = _normalize_v4(score) * 0.8          # fusion 管线
            ml_dir = _ml_sign(l7)                     # 与 backtest._sign 一致
            if ml_dir == 0:
                neutral += 1
                continue
            actual_dir = 1 if pct_chg > 0 else (-1 if pct_chg < 0 else 0)
            total += 1
            if ml_dir == actual_dir:
                correct += 1

        result["fusion_equivalent"] = {
            "correct": correct,
            "total": total,
            "neutral": neutral,
            "accuracy": round(correct / total * 100, 1) if total > 0 else 0.0,
            "source": "analysis_history → v4.0 L7 ×0.8 → _sign(0.1) → T+1",
        }

    # ── 3. 策略级准确率 (从 backtest_results.skill_id) ──
    try:
        cursor.execute('''
            SELECT COALESCE(br.skill_id, 'consensus') as sid,
                   SUM(CASE WHEN br.direction_correct = 1 THEN 1 ELSE 0 END) as correct,
                   COUNT(*) as total
            FROM backtest_results br
            WHERE br.eval_status = 'completed' AND br.direction_correct IS NOT NULL
            GROUP BY sid
            HAVING total >= 5
            ORDER BY correct * 1.0 / total DESC
        ''')
        strategies = []
        for row in cursor.fetchall():
            strategies.append({
                "skill_id": row["sid"],
                "correct": row["correct"],
                "total": row["total"],
                "accuracy": round(row["correct"] / row["total"] * 100, 1),
            })
        if strategies:
            result["strategy_breakdown"] = strategies
    except sqlite3.OperationalError:
        pass  # skill_id 列可能尚不存在

    # ── 4. ML 内部分歧分析: op_advice 与 sentiment_score 方向不一致时, 谁更准 ──
    try:
        cursor.execute("""
            SELECT ah.sentiment_score, ah.operation_advice, sp.pct_chg
            FROM analysis_history ah
            JOIN stock_daily sp ON sp.code = ah.code
                AND sp.date = date(ah.created_at, '+1 day')
            WHERE ah.sentiment_score IS NOT NULL
              AND ah.operation_advice IS NOT NULL AND ah.operation_advice != ''
              AND sp.pct_chg IS NOT NULL
              AND ah.created_at >= date('now', '-90 days')
        """)
        div_rows = cursor.fetchall()
    except sqlite3.OperationalError:
        div_rows = []

    if div_rows:
        def _op_dir(advice: str) -> int:
            t = advice.strip().lower()
            if any(k in t for k in ("卖出", "减仓", "强烈卖出", "清仓", "sell", "reduce")):
                return -1
            if any(k in t for k in ("买入", "加仓", "强烈买入", "增持", "建仓", "buy", "add")):
                return 1
            return 0

        agree_correct, agree_total = 0, 0
        diverge_correct_sent, diverge_correct_op, diverge_total = 0, 0, 0
        sent_diverge_op_neutral = 0
        flat_skipped = 0
        total_processed = 0

        for score, advice, pct_chg in div_rows:
            if 49 <= score <= 51:
                flat_skipped += 1
                continue
            total_processed += 1
            sent_dir = 1 if score >= 52 else -1
            op_dir = _op_dir(advice)
            if op_dir == 0:
                sent_diverge_op_neutral += 1
                continue
            actual = 1 if pct_chg > 0 else (-1 if pct_chg < 0 else 0)
            if sent_dir == op_dir:
                agree_total += 1
                if sent_dir == actual:
                    agree_correct += 1
            else:
                diverge_total += 1
                if sent_dir == actual:
                    diverge_correct_sent += 1
                if op_dir == actual:
                    diverge_correct_op += 1

        if agree_total + diverge_total > 0:
            diverge_rate = round(diverge_total / (agree_total + diverge_total) * 100, 1)
            result["internal_divergence"] = {
                "agree_accuracy": round(agree_correct / agree_total * 100, 1) if agree_total > 0 else 0,
                "agree_total": agree_total,
                "diverge_rate": diverge_rate,
                "diverge_total": diverge_total,
                "diverge_sentiment_accuracy": round(diverge_correct_sent / diverge_total * 100, 1) if diverge_total > 0 else 0,
                "diverge_op_accuracy": round(diverge_correct_op / diverge_total * 100, 1) if diverge_total > 0 else 0,
                "sent_diverge_op_neutral": sent_diverge_op_neutral,
            }

    # ── 5. 最近回测报告文件路径 (如果有) ──
    report_dir = PROJECT_ROOT / "systems" / "MindLynx-Aistock" / "reports" / "backtest"
    latest_reports = sorted(report_dir.glob("backtest_report_overall_*.md"))
    if latest_reports:
        result["latest_report"] = str(latest_reports[-1].relative_to(PROJECT_ROOT))

    conn.close()
    return result


# ══════════════════════════════════════════════════════════════
# Phase 4: AT 回测 (从融合 DB 提取)
# ══════════════════════════════════════════════════════════════

def phase4_at() -> Dict[str, Any]:
    """AT 独立回测 — 从 fusion CSV 历史+stock_daily 提取 T+1 方向准确率。

    1. 扫描 data/fusion_output/ 下所有 fusion CSV
    2. 提取 tradingagent_score + 次日行情 (stock_daily.pct_chg)
    3. 计算 AT 独立方向准确率
    4. 同时保留融合层面的 AT 数据作为对比
    """
    result: Dict[str, Any] = {"status": "ok", "timestamp": _now(),
                               "fusion_level_note": "融合层面 AT 数据见 fusion.subsystems.at"}

    # ── 从 fusion CSV + stock_daily 独立计算 ──
    csv_dir = FUSION_OUTPUT_DIR
    if not csv_dir.exists():
        result["status"] = "skipped"
        result["message"] = "fusion_output 目录不存在"
        return result

    csv_files = sorted(csv_dir.glob("fusion_*.csv"))
    if not csv_files:
        result["status"] = "skipped"
        result["message"] = "无 fusion CSV 文件"
        return result

    # 读取本周所有 fusion CSV, 提取 AT 分数
    at_records = []  # (stock_code, at_score, date)
    for f in csv_files:
        try:
            with open(f, newline="") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    at_score_str = row.get("tradingagent_score", "").strip()
                    if not at_score_str:
                        continue
                    try:
                        at_score = float(at_score_str)
                    except ValueError:
                        continue
                    # 提取日期: fusion_2026-06-29.csv → 2026-06-29
                    date_str = f.stem.replace("fusion_", "")
                    at_records.append((row["stock_code"], at_score, date_str))
        except Exception:
            continue

    if not at_records:
        result["status"] = "skipped"
        result["message"] = "fusion CSV 中无 AT 分数数据"
        return result

    # 匹配 stock_daily 获取 T+1 涨跌
    try:
        conn = sqlite3.connect(str(ML_DB))
        cursor = conn.cursor()

        correct, total = 0, 0
        per_stock: Dict[str, dict] = {}
        for code, at_score, date_str in at_records:
            # AT 方向: tradingagent_score > 0 = 看多, < 0 = 看空, ==0 = 中性
            # 使用与融合回测相同的阈值 _sign(threshold=0.1), 确保口径一致
            if abs(at_score) < 0.1:
                continue  # 中性跳过, 与 backtest._sign 一致
            at_bullish = at_score > 0

            # 查 T+1 涨跌
            cursor.execute(
                "SELECT pct_chg FROM stock_daily WHERE code = ? AND date = ?",
                (code, date_str),
            )
            row = cursor.fetchone()
            if row is None or row[0] is None:
                continue
            pct_chg = row[0]

            total += 1
            correct += 1 if (at_bullish and pct_chg > 0) or (not at_bullish and pct_chg < 0) else 0

            if code not in per_stock:
                per_stock[code] = {"correct": 0, "total": 0}
            per_stock[code]["correct"] += 1 if (at_bullish and pct_chg > 0) or (not at_bullish and pct_chg < 0) else 0
            per_stock[code]["total"] += 1

        conn.close()

        result["correct"] = correct
        result["total"] = total
        result["accuracy"] = round(correct / total * 100, 1) if total > 0 else 0.0
        result["records_scanned"] = len(at_records)
        result["per_stock"] = [
            {"code": code, "correct": v["correct"], "total": v["total"],
             "accuracy": round(v["correct"] / v["total"] * 100, 1)}
            for code, v in sorted(per_stock.items())
        ]

    except (sqlite3.OperationalError, FileNotFoundError) as e:
        result["status"] = "error"
        result["message"] = f"AT 独立回测失败: {e}"

    return result


# ══════════════════════════════════════════════════════════════
# Phase 5: 因子独立回测
# ══════════════════════════════════════════════════════════════

def phase5_factor_backtest(timeout: int = 300) -> Dict[str, Any]:
    """运行因子独立回测 (factor_backtest.py).

    子进程调用 scripts/factor_backtest.py，解析 stdout 获取因子方向准确率。
    """
    result: Dict[str, Any] = {"status": "ok", "timestamp": _now()}

    factor_script = PROJECT_ROOT / "scripts" / "factor_backtest.py"
    if not factor_script.exists():
        result["status"] = "skipped"
        result["message"] = f"因子回测脚本不存在: {factor_script}"
        return result

    print(f"  [c1test] 运行因子独立回测 ...")
    try:
        proc = subprocess.run(
            [sys.executable, str(factor_script)],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["message"] = f"因子回测超时 ({timeout}s)"
        return result

    stdout = proc.stdout

    # 解析整体准确率
    acc_match = re.search(r"整体准确率[：:]\s*([\d.]+)%", stdout)
    if acc_match:
        result["accuracy"] = float(acc_match.group(1))
    # 总评估次数
    eval_match = re.search(r"总评估次数[：:]\s*(\d+)", stdout)
    if eval_match:
        result["total_evaluations"] = int(eval_match.group(1))
    # 评估股票数
    stock_match = re.search(r"评估股票数[：:]\s*(\d+)", stdout)
    if stock_match:
        result["stocks_evaluated"] = int(stock_match.group(1))
    # 结论
    conclusion_match = re.search(r"结论[：:]\s*(.+)", stdout)
    if conclusion_match:
        result["conclusion"] = conclusion_match.group(1).strip()
    # 解析个股准确率
    per_stock = []
    for line in stdout.splitlines():
        m = re.match(r"\s+(\d{6})\s+(\d+)\s+(\d+)\s+([\d.]+)%", line)
        if m:
            per_stock.append({
                "code": m.group(1),
                "evaluations": int(m.group(2)),
                "correct": int(m.group(3)),
                "accuracy": float(m.group(4)),
            })
    if per_stock:
        result["per_stock"] = per_stock

    result["returncode"] = proc.returncode
    result["stdout_preview"] = stdout[:500] if len(stdout) > 500 else stdout
    result["stderr_preview"] = proc.stderr[:500] if proc.stderr else ""

    return result


# ══════════════════════════════════════════════════════════════
# Phase 6: WalkForward 滑动窗口验证
# ══════════════════════════════════════════════════════════════

def phase6_walkforward(timeout: int = 120) -> Dict[str, Any]:
    """运行 WalkForward 滑动窗口验证 (backtest.py walkforward).

    子进程调用 backtest.py walkforward，解析 stdout 获取 IS/OOS 准确率。
    """
    result: Dict[str, Any] = {"status": "ok", "timestamp": _now()}

    bt_script = PROJECT_ROOT / "scripts" / "backtest.py"
    if not bt_script.exists():
        result["status"] = "skipped"
        result["message"] = f"回测脚本不存在: {bt_script}"
        return result

    print(f"  [c1test] 运行 WalkForward 验证 ...")
    try:
        proc = subprocess.run(
            [sys.executable, "scripts/backtest.py", "walkforward"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["message"] = f"WalkForward 超时 ({timeout}s)"
        return result

    stdout = proc.stdout
    if not stdout.strip() or "数据不足" in stdout:
        result["status"] = "skipped"
        result["message"] = "数据不足，跳过 WalkForward"
        result["stdout_preview"] = stdout[:500]
        return result

    # 解析回测区间和窗口
    range_match = re.search(r"回测区间[：:]\s*(.+?)\s*\((\d+)\s*交易日\)", stdout)
    if range_match:
        result["date_range"] = range_match.group(1).strip()
        result["trading_days"] = int(range_match.group(2))

    win_match = re.search(r"训练窗口[：:]\s*(\d+)天.*验证窗口[：:]\s*(\d+)天.*步长[：:]\s*(\d+)天", stdout)
    if win_match:
        result["train_window"] = int(win_match.group(1))
        result["test_window"] = int(win_match.group(2))
        result["step"] = int(win_match.group(3))

    # 解析各窗口
    windows = []
    for line in stdout.splitlines():
        m = re.match(r".*IS[：:]\s*([\d.]+)%\s*\((\d+)/(\d+)\)\s*[|]\s*OOS[：:]\s*([\d.]+)%\s*\((\d+)/(\d+)\)", line)
        if m:
            windows.append({
                "is_accuracy": float(m.group(1)),
                "is_correct": int(m.group(2)),
                "is_total": int(m.group(3)),
                "oos_accuracy": float(m.group(4)),
                "oos_correct": int(m.group(5)),
                "oos_total": int(m.group(6)),
            })
    if windows:
        result["windows"] = windows
        # 汇总 OOS
        avg_oos = sum(w["oos_accuracy"] for w in windows) / len(windows)
        avg_is = sum(w["is_accuracy"] for w in windows) / len(windows)
        result["avg_oos_accuracy"] = round(avg_oos, 1)
        result["avg_is_accuracy"] = round(avg_is, 1)
        result["decay_ratio"] = round(avg_oos / avg_is * 100, 1) if avg_is > 0 else 0

    result["returncode"] = proc.returncode
    return result


# ══════════════════════════════════════════════════════════════
# Phase 7: 权重网格扫描
# ══════════════════════════════════════════════════════════════

def phase7_weight_sweep(timeout: int = 120) -> Dict[str, Any]:
    """运行权重网格扫描 (backtest.py weight-sweep).

    枚举 settings.yaml 中定义的权重组合，输出最优权重。
    """
    result: Dict[str, Any] = {"status": "ok", "timestamp": _now()}

    bt_script = PROJECT_ROOT / "scripts" / "backtest.py"
    if not bt_script.exists():
        result["status"] = "skipped"
        result["message"] = f"回测脚本不存在: {bt_script}"
        return result

    print(f"  [c1test] 运行权重网格扫描 ...")
    try:
        proc = subprocess.run(
            [sys.executable, "scripts/backtest.py", "weight-sweep"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["message"] = f"权重扫描超时 ({timeout}s)"
        return result

    stdout = proc.stdout
    if not stdout.strip() or "没有足够的" in stdout:
        result["status"] = "skipped"
        result["message"] = "数据不足，跳过权重扫描"
        result["stdout_preview"] = stdout[:500]
        return result

    # 解析搜索范围
    range_match = re.search(r"ly[：:]\s*(\[.*?\]).*ml[：:]\s*(\[.*?\]).*at[：:]\s*(\[.*?\])", stdout, re.DOTALL)
    if range_match:
        try:
            result["ly_range"] = json.loads(range_match.group(1).replace("'", '"'))
            result["ml_range"] = json.loads(range_match.group(2).replace("'", '"'))
            result["at_range"] = json.loads(range_match.group(3).replace("'", '"'))
        except json.JSONDecodeError:
            pass
    combo_match = re.search(r"共\s*(\d+)\s*种组合", stdout)
    if combo_match:
        result["combo_count"] = int(combo_match.group(1))

    # 解析结果表: 找最优组合
    best_match = re.search(r"最优[：:].*?ly=([\d.]+).*?ml=([\d.]+).*?at=([\d.]+).*?(\d+)/(\d+)\s*\(([\d.]+)%\)", stdout)
    if best_match:
        result["best_weights"] = {
            "ly": float(best_match.group(1)),
            "ml": float(best_match.group(2)),
            "at": float(best_match.group(3)),
            "correct": int(best_match.group(4)),
            "total": int(best_match.group(5)),
            "accuracy": float(best_match.group(6)),
        }

    result["returncode"] = proc.returncode
    result["stdout_preview"] = stdout[:800] if len(stdout) > 800 else stdout
    return result


# ══════════════════════════════════════════════════════════════
# Phase 8: 模拟交易
# ══════════════════════════════════════════════════════════════

def phase8_simulate(timeout: int = 120) -> Dict[str, Any]:
    """运行模拟交易回测 (backtest.py simulate).

    解析 stdout 获取收益曲线和风险指标。
    """
    result: Dict[str, Any] = {"status": "ok", "timestamp": _now()}

    bt_script = PROJECT_ROOT / "scripts" / "backtest.py"
    if not bt_script.exists():
        result["status"] = "skipped"
        result["message"] = f"回测脚本不存在: {bt_script}"
        return result

    print(f"  [c1test] 运行模拟交易 ...")
    try:
        proc = subprocess.run(
            [sys.executable, "scripts/backtest.py", "simulate"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["message"] = f"模拟交易超时 ({timeout}s)"
        return result

    stdout = proc.stdout
    if not stdout.strip() or "没有可用的" in stdout:
        result["status"] = "skipped"
        result["message"] = "数据不足，跳过模拟交易"
        result["stdout_preview"] = stdout[:500]
        return result

    # 解析关键指标
    for key, pattern in [
        ("total_return", r"总收益率[：:]\s*([+-]?[\d.]+)%"),
        ("annual_return", r"年化收益率[：:]\s*([+-]?[\d.]+)%"),
        ("max_drawdown", r"最大回撤[：:]\s*([+-]?[\d.]+)%"),
        ("win_rate", r"胜率[：:]\s*([\d.]+)%"),
        ("trade_count", r"交易次数[：:]\s*(\d+)"),
        ("sharpe", r"夏普比率[：:]\s*([\d.]+)"),
        ("avg_win", r"平均盈利[：:]\s*([+-]?[\d.]+)%"),
        ("avg_loss", r"平均亏损[：:]\s*([+-]?[\d.]+)%"),
    ]:
        m = re.search(pattern, stdout)
        if m:
            val = float(m.group(1)) if "." in m.group(1) or "%" not in pattern else int(m.group(1))
            result[key] = val

    result["returncode"] = proc.returncode
    return result


# ══════════════════════════════════════════════════════════════
# 统一报告生成
# ══════════════════════════════════════════════════════════════

def generate_unified_report(phases: Dict[str, Any]) -> Dict[str, Any]:
    """合并各阶段结果，生成统一报告 + 变化检测。"""
    report: Dict[str, Any] = {
        "run_id": f"c1test-{_today_str()}-{datetime.now().strftime('%H%M%S')}",
        "timestamp": _now(),
        "mode": "quick" if "--quick" in sys.argv or len(sys.argv) == 1 else "full",
    }

    # 从融合阶段提取关键指标
    fusion = phases.get("fusion", {})
    if fusion.get("status") == "ok":
        sa = fusion.get("subsystem_accuracy", {})
        report["fusion"] = {
            "accuracy_pct": sa.get("fusion", {}).get("accuracy", 0),
            "total_matched": fusion.get("total_matched", 0),
            "backtest_days": fusion.get("backtest_days", 0),
            "disagreement_pct": fusion.get("disagreement", {}).get("accuracy", 0),
            "no_disagreement_pct": fusion.get("disagreement", {}).get("no_disagreement_accuracy", 0),
        }
        report["subsystems"] = {}
        for sys_key in ["ly", "ml", "at"]:
            s = sa.get(sys_key, {})
            report["subsystems"][sys_key] = {
                "accuracy_pct": s.get("accuracy", 0),
                "correct": s.get("correct", 0),
                "total": s.get("total", 0),
            }

        # P2: 子系统覆盖率
        coverage = fusion.get("subsystem_coverage", {})
        if coverage:
            report["subsystem_coverage"] = coverage

        # LY flat zone 中性排除数
        report["ly_flat_zone_neutral"] = fusion.get("ly_flat_zone_neutral", 0)

    # LY 独立回测
    ly = phases.get("ly", {})
    if ly.get("status") == "ok" and ly.get("overall"):
        report["ly_independent"] = ly["overall"]
        l7 = ly.get("overall_l7")
        if l7:
            report["ly_independent_l7"] = l7

    # ML 独立回测
    ml = phases.get("ml", {})
    if ml.get("status") == "ok":
        report["ml"] = {
            "operation_advice": ml.get("operation_advice", {}),
            "sentiment_score": ml.get("sentiment_score", {}),
            "fusion_equivalent": ml.get("fusion_equivalent", {}),
            "strategy_breakdown": ml.get("strategy_breakdown", []),
            "internal_divergence": ml.get("internal_divergence", {}),
        }

    # AT (独立)
    at = phases.get("at", {})
    if at.get("status") == "ok":
        report["at_fusion_level"] = at

    # 因子回测 / WalkForward / 权重扫描 / 模拟交易 (直接传递)
    for key in ("factor", "walkforward", "weight_sweep", "simulate"):
        p = phases.get(key, {})
        if p.get("status") == "ok" or p.get("status") == "skipped":
            report[key] = p

    # ── 变化检测 ──
    changes = detect_changes(report)
    report["changes"] = changes

    # ── 子系统准确率覆写（独立回测口径） ──
    # LY 用独立回测 L7 口径替代融合层面的子集数据
    l7 = report.get("ly_independent_l7", {})
    if l7.get("accuracy"):
        report.setdefault("subsystems", {}).setdefault("ly", {})["accuracy_pct"] = l7["accuracy"]
        report.setdefault("subsystems", {}).setdefault("ly", {})["correct"] = l7["correct"]
        report.setdefault("subsystems", {}).setdefault("ly", {})["total"] = l7["total"]
    # ML 用 fusion_equivalent 替代融合层面的子集数据
    fe = report.get("ml", {}).get("fusion_equivalent", {})
    if fe.get("accuracy"):
        report.setdefault("subsystems", {}).setdefault("ml", {})["accuracy_pct"] = fe["accuracy"]
        report.setdefault("subsystems", {}).setdefault("ml", {})["correct"] = fe["correct"]
        report.setdefault("subsystems", {}).setdefault("ml", {})["total"] = fe["total"]

    return report


def detect_changes(report: Dict[str, Any]) -> Dict[str, Any]:
    """对比上次运行，检测变化和告警。"""
    changes: Dict[str, Any] = {
        "regressions": [],
        "improvements": [],
        "alerts": [],
        "vs_previous": {},
    }

    if not LAST_RUN_FILE.exists():
        changes["note"] = "首次运行 — 无历史基线对比"
        return changes

    try:
        last = json.loads(LAST_RUN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        changes["note"] = "上次报告损坏 — 跳过变化检测"
        return changes

    # 对比融合准确率
    cur_fusion = report.get("fusion", {}).get("accuracy_pct", 0)
    last_fusion = last.get("fusion", {}).get("accuracy_pct", 0)
    if last_fusion > 0:
        diff = round(cur_fusion - last_fusion, 1)
        changes["vs_previous"]["fusion_accuracy"] = f"{diff:+.1f}%"
        if diff <= -5:
            changes["regressions"].append(f"融合准确率 {diff:+.1f}% ({cur_fusion}% → 上次 {last_fusion}%)")
            changes["alerts"].append("🔴 融合准确率下降 ≥5%")
        elif diff <= -2:
            changes["alerts"].append(f"🟡 融合准确率小幅下降 {diff:+.1f}%")
        elif diff >= 3:
            changes["improvements"].append(f"融合准确率提升 {diff:+.1f}%")
            changes["alerts"].append(f"🟢 融合准确率提升 {diff:+.1f}% ({cur_fusion}%)")

    # 对比各子系统
    cur_subs = report.get("subsystems", {})
    last_subs = last.get("subsystems", {})
    name_map = {"ly": "LY", "ml": "ML", "at": "AT"}
    for sk, sn in name_map.items():
        cur_pct = cur_subs.get(sk, {}).get("accuracy_pct", 0)
        last_pct = last_subs.get(sk, {}).get("accuracy_pct", 0)
        if last_pct > 0:
            diff = round(cur_pct - last_pct, 1)
            changes["vs_previous"][f"{sk}_accuracy"] = f"{diff:+.1f}%"
            if diff <= -5:
                changes["regressions"].append(f"{sn} 准确率下降 {diff:+.1f}%")
                changes["alerts"].append(f"🔴 {sn} 准确率下降 ≥5%")

    # 警示: ML 语意差距
    ml = report.get("ml", {})
    op_acc = ml.get("operation_advice", {}).get("direction_accuracy", 0)
    fe_acc = ml.get("fusion_equivalent", {}).get("accuracy", 0)
    if op_acc > 0 and fe_acc > 0:
        gap = abs(fe_acc - op_acc)
        if gap > 15:
            changes["alerts"].append(f"🟡 ML 语义差距 {gap:.0f}% (fusion等效 {fe_acc}% vs operation {op_acc}%)")

    # 警示: 系统可用性
    for sk, sn in name_map.items():
        total = cur_subs.get(sk, {}).get("total", 0)
        if total == 0:
            changes["alerts"].append(f"🔴 {sn} 无有效数据")

    # 警示: 子系统覆盖率过低
    coverage = report.get("subsystem_coverage", {})
    for sk, sn in name_map.items():
        c = coverage.get(sk, {})
        pct = c.get("pct", 100)
        if 0 < pct < 50:
            changes["alerts"].append(f"🟡 {sn} 数据覆盖率仅 {pct}% ({c.get('valid',0)}/{c.get('total',0)})")

    changes["alert_count"] = len([a for a in changes["alerts"] if a.startswith("🔴")])
    changes["warning_count"] = len([a for a in changes["alerts"] if a.startswith("🟡")])
    changes["pass_count"] = len([a for a in changes["alerts"] if a.startswith("🟢")])

    return changes


def render_markdown(report: Dict[str, Any]) -> str:
    """将统一报告渲染为 Markdown。"""
    lines = []
    lines.append(f"# c1test 统一回测报告")
    lines.append(f"")
    lines.append(f"> 运行: {report['timestamp']} | 模式: {report['mode']} | 运行ID: {report['run_id']}")
    lines.append(f"")

    # ── 告警区块 ──
    alerts = report.get("changes", {}).get("alerts", [])
    if alerts:
        lines.append(f"## ⚠️ 告警")
        for a in alerts:
            lines.append(f"- {a}")
        lines.append(f"")

    # ── 融合概览 ──
    fusion = report.get("fusion", {})
    lines.append(f"## 📊 融合回测概览")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 融合准确率 | **{fusion.get('accuracy_pct', 'N/A')}%** |")
    lines.append(f"| 匹配样本量 | {fusion.get('total_matched', 0)} |")
    lines.append(f"| 回测天数 | {fusion.get('backtest_days', 0)} |")
    lines.append(f"| 分歧时准确率 | {fusion.get('disagreement_pct', 'N/A')}% |")
    lines.append(f"| 无分歧准确率 | {fusion.get('no_disagreement_pct', 'N/A')}% |")
    lines.append(f"")

    # ── 子系统对比（独立回测口径） ──
    subs = report.get("subsystems", {})
    lines.append(f"## 📈 子系统方向准确率 (独立回测)")
    lines.append(f"")
    lines.append(f"| 系统 | 准确率 | 正确/总 |")
    lines.append(f"|------|:------:|:-------:|")
    name_map = {"ly": "Lynx", "ml": "MindLynx", "at": "TradingAgent"}
    for sk, sn in name_map.items():
        s = subs.get(sk, {})
        acc = s.get("accuracy_pct", 0)
        corr = s.get("correct", 0)
        total = s.get("total", 0)
        bar = _bar(acc, 12)
        lines.append(f"| {sn} | {acc}% {bar} | {corr}/{total} |")
    lines.append(f"")

    # 子系统覆盖率
    coverage = report.get("subsystem_coverage", {})
    if coverage:
        lines.append(f"### 子系统数据覆盖率")
        lines.append(f"")
        lines.append(f"| 系统 | 有效信号 | 覆盖率 |")
        lines.append(f"|------|:--------:|:------:|")
        name_map_coverage = {"ly": "LY", "ml": "ML", "at": "AT"}
        for sk, sn in name_map_coverage.items():
            c = coverage.get(sk, {})
            valid = c.get("valid", 0)
            total = c.get("total", 0)
            pct = c.get("pct", 0)
            bar = _bar(pct, 10)
            lines.append(f"| {sn} | {valid}/{total} | {pct}% {bar} |")
        lines.append(f"")

    # ── LY 独立 ──
    ly = report.get("ly_independent", {})
    l7 = report.get("ly_independent_l7", {})
    if ly:
        lines.append(f"## 🔬 LY 独立回测 (Walk-Forward OOS)")
        lines.append(f"")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| raw prob_up 准确率 | **{ly.get('accuracy', 'N/A')}%** |")
        lines.append(f"| 样本量 | {ly.get('total', 0)} |")
        if l7:
            lines.append(f"| L7 映射后准确率 | **{l7.get('accuracy', 'N/A')}%** |")
        lz = report.get("ly_flat_zone_neutral", 0)
        if lz:
            lines.append(f"| L7 flat zone 中性排除 | {lz} 条（不计入融合层面 LY 准确率） |")
        lines.append(f"")

    # ── ML 详情 ──
    ml = report.get("ml", {})
    if ml.get("operation_advice") or ml.get("sentiment_score") or ml.get("fusion_equivalent"):
        lines.append(f"## 🤖 ML 独立回测")
        lines.append(f"")
        op = ml.get("operation_advice", {})
        if op:
            lines.append(f"### 操作建议路径 (operation_advice, 与sentiment一致口径)")
            lines.append(f"")
            lines.append(f"| 指标 | 数值 |")
            lines.append(f"|------|------|")
            lines.append(f"| 方向准确率 | **{op.get('direction_accuracy', 'N/A')}%** |")
            lines.append(f"| 样本量 | {op.get('total', 0)} |")
            lines.append(f"| 其中中性(flat zone) | {op.get('neutral', 0)} 条 |")
            lines.append(f"| 数据源 | {op.get('source', '')} |")
            lines.append(f"")

        ss = ml.get("sentiment_score", {})
        if ss:
            lines.append(f"### 评分路径 (sentiment_score, 直查 DB)")
            lines.append(f"")
            lines.append(f"| 指标 | 数值 |")
            lines.append(f"|------|------|")
            lines.append(f"| 方向准确率 | **{ss.get('accuracy', 'N/A')}%** |")
            lines.append(f"| 样本量 | {ss.get('total', 0)} |")
            lines.append(f"")

        fe = ml.get("fusion_equivalent", {})
        if fe:
            lines.append(f"### 融合等效 (fusion_equivalent, 全量管线回放)")
            lines.append(f"")
            lines.append(f"| 指标 | 数值 |")
            lines.append(f"|------|------|")
            lines.append(f"| 方向准确率 | **{fe.get('accuracy', 'N/A')}%** |")
            lines.append(f"| 样本量 | {fe.get('total', 0)} |")
            lines.append(f"| 中性跳过 | {fe.get('neutral', 0)} |")
            if op.get("direction_accuracy") and fe.get("accuracy"):
                gap = abs(fe["accuracy"] - op["direction_accuracy"])
                lines.append(f"| 语义差距 | **{gap:.1f}%** |")
            lines.append(f"")

        # 策略级准确率
        strategies = ml.get("strategy_breakdown", [])
        if strategies:
            lines.append(f"### 🧩 策略级准确率 (backtest_results.skill_id)")
            lines.append(f"")
            lines.append(f"| 策略 | 准确率 | 正确/总 |")
            lines.append(f"|------|:------:|:-------:|")
            for s in strategies:
                bar = _bar(s.get("accuracy", 0), 10)
                lines.append(f"| {s.get('skill_id', '?')} | {s.get('accuracy', 0):.1f}% {bar} | {s.get('correct',0)}/{s.get('total',0)} |")
            lines.append(f"")
        else:
            lines.append(f"<!-- 策略级数据积累中（需至少 5 条/skill） -->")
            lines.append(f"")

        # ML 内部分歧分析
        div = ml.get("internal_divergence", {})
        if div.get("diverge_total", 0) > 0:
            lines.append(f"### 🔀 ML 内部分歧: op vs sentiment")
            lines.append(f"")
            lines.append(f"| 指标 | 数值 |")
            lines.append(f"|------|------|")
            lines.append(f"| 一致时准确率 | {div.get('agree_accuracy', 'N/A')}% ({div.get('agree_total', 0)}样本) |")
            lines.append(f"| 分歧率 | {div.get('diverge_rate', 'N/A')}% |")
            lines.append(f"| 分歧时 sentiment 准确率 | {div.get('diverge_sentiment_accuracy', 'N/A')}% |")
            lines.append(f"| 分歧时 op_advice 准确率 | {div.get('diverge_op_accuracy', 'N/A')}% |")
            lines.append(f"| op 无方向(sent 有方向) | {div.get('sent_diverge_op_neutral', 'N/A')} 条 |")
            lines.append(f"")

    # ── AT ──
    at = report.get("at_fusion_level", {})
    if at:
        lines.append(f"## 👤 AT 回测 (融合层面)")
        lines.append(f"")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 方向准确率 | **{at.get('accuracy', 'N/A')}%** |")
        lines.append(f"| 样本量 | {at.get('total', 0)} |")
        lines.append(f"| 说明 | 阈值已统一为 0.1 与融合回测口径一致 |")
        lines.append(f"")

    # ── Factor 因子回测 ──
    factor = report.get("factor", {})
    if factor and factor.get("status") == "ok":
        lines.append(f"## 📐 因子独立回测 (Factor Backtest)")
        lines.append(f"")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 方向准确率 | **{factor.get('accuracy', 'N/A')}%** |")
        lines.append(f"| 评估次数 | {factor.get('total_evaluations', 0)} |")
        lines.append(f"| 评估股票数 | {factor.get('stocks_evaluated', 0)} |")
        lines.append(f"| 结论 | {factor.get('conclusion', 'N/A')} |")
        lines.append(f"")

    # ── WalkForward ──
    wf = report.get("walkforward", {})
    if wf and wf.get("status") == "ok":
        lines.append(f"## 🔄 WalkForward 滑动窗口验证")
        lines.append(f"")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 回测区间 | {wf.get('date_range', 'N/A')} |")
        lines.append(f"| 交易日数 | {wf.get('trading_days', 'N/A')} |")
        lines.append(f"| 训练窗口 | {wf.get('train_window', 'N/A')}天 |")
        lines.append(f"| 验证窗口 | {wf.get('test_window', 'N/A')}天 |")
        lines.append(f"| 平均 IS 准确率 | **{wf.get('avg_is_accuracy', 'N/A')}%** |")
        lines.append(f"| 平均 OOS 准确率 | **{wf.get('avg_oos_accuracy', 'N/A')}%** |")
        lines.append(f"| 衰减比 (OOS/IS) | {wf.get('decay_ratio', 'N/A')}% |")
        if wf.get("windows"):
            lines.append(f"")
            lines.append(f"| 窗口 | IS | OOS |")
            lines.append(f"|:----:|:--:|:---:|")
            for i, w in enumerate(wf["windows"]):
                lines.append(f"| {i+1} | {w.get('is_accuracy','?')}% ({w.get('is_correct',0)}/{w.get('is_total',0)})"
                             f" | {w.get('oos_accuracy','?')}% ({w.get('oos_correct',0)}/{w.get('oos_total',0)}) |")
        lines.append(f"")

    # ── Weight Sweep ──
    ws = report.get("weight_sweep", {})
    if ws and ws.get("status") == "ok" and ws.get("best_weights"):
        bw = ws["best_weights"]
        lines.append(f"## ⚖️ 权重网格扫描")
        lines.append(f"")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 搜索范围 | ly{ws.get('ly_range','')} ml{ws.get('ml_range','')} at{ws.get('at_range','')} |")
        lines.append(f"| 组合数 | {ws.get('combo_count', 'N/A')} |")
        lines.append(f"| **最优权重** | ly={bw.get('ly','?')}  ml={bw.get('ml','?')}  at={bw.get('at','?')} |")
        lines.append(f"| 最优准确率 | **{bw.get('accuracy','?')}%** ({bw.get('correct',0)}/{bw.get('total',0)}) |")
        lines.append(f"")

    # ── Simulate ──
    si = report.get("simulate", {})
    if si and si.get("status") == "ok":
        lines.append(f"## 💰 模拟交易")
        lines.append(f"")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        for label, key in [("总收益率", "total_return"), ("年化收益率", "annual_return"),
                           ("最大回撤", "max_drawdown"), ("胜率", "win_rate"),
                           ("交易次数", "trade_count"), ("夏普比率", "sharpe"),
                           ("平均盈利", "avg_win"), ("平均亏损", "avg_loss")]:
            val = si.get(key, "N/A")
            lines.append(f"| {label} | {val}{'%' if key not in ('trade_count','sharpe') else ''} |")
        lines.append(f"")

    # ── 变化 ──
    changes = report.get("changes", {})
    vsp = changes.get("vs_previous", {})
    if vsp:
        lines.append(f"## 📉 变化检测 vs 上次运行")
        lines.append(f"")
        for k, v in vsp.items():
            lines.append(f"- {k}: {v}")
        lines.append(f"")

    # ── 信号分布 ──
    fusion_data = report.get("fusion_raw", {})
    signal_data = fusion_data.get("signal_breakdown", [])
    if signal_data:
        lines.append(f"## 🏷️  信号分布")
        lines.append(f"")
        lines.append(f"| 信号 | 次数 | 准确率 |")
        lines.append(f"|------|:----:|:------:|")
        for s in signal_data:
            bar = _bar(s.get("accuracy", 0), 10)
            lines.append(f"| {s.get('signal', 'N/A'):20s} | {s.get('count', 0):3d}次 | {s.get('accuracy', 0):.1f}% {bar} |")
        lines.append(f"")

    # ── 逐日趋势 ──
    daily = fusion_data.get("daily_trend", [])
    if daily:
        lines.append(f"## 📅 逐日准确率 (最近)")
        lines.append(f"")
        lines.append(f"| 日期 | 准确率 |")
        lines.append(f"|------|:-----:|")
        for d in daily[:10]:
            bar = _bar(d.get("accuracy", 0), 10)
            lines.append(f"| {d.get('date', '')} | {d.get('accuracy', 0):.1f}% {bar} |")
        lines.append(f"")

    # 底部
    lines.append(f"---")
    lines.append(f"*报告由 c1test 自动生成 | {report['timestamp']}*")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="c1test 统一回测编排器")
    parser.add_argument("--quick", action="store_true", help="快速模式 (仅融合回测+WalkForward+网格+模拟, 跳过独立回测)")
    parser.add_argument("--full", action="store_true", help="(已废弃, 默认全量)")
    parser.add_argument("--report", action="store_true", help="只显示上次报告")
    parser.add_argument("--push", action="store_true", help="发送企业微信通知")
    args = parser.parse_args()

    C1TEST_DIR.mkdir(parents=True, exist_ok=True)

    # ── 只显示上次报告 ──
    if args.report:
        if UNIFIED_REPORT_MD.exists():
            print(UNIFIED_REPORT_MD.read_text())
        elif UNIFIED_REPORT_JSON.exists():
            report = json.loads(UNIFIED_REPORT_JSON.read_text())
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print("❌ 无上次报告。请先运行 c1test.py")
        return

    # ── 判断模式 ──
    is_quick = args.quick

    print(f"{'='*55}")
    print(f"  c1test 统一回测 — {_now()}")
    print(f"  模式: {'快速' if is_quick else '全量'}")
    print(f"{'='*55}")
    print()

    phases: Dict[str, Any] = {}

    # ── Phase 1: 融合回测 (always) ──
    print(f"▶ Phase 1/8: 融合回测")
    phases["fusion"] = phase1_fusion()
    f_status = phases["fusion"].get("status", "error")
    if f_status == "ok":
        sa = phases["fusion"].get("subsystem_accuracy", {})
        print(f"   ✅ 融合 {sa.get('fusion', {}).get('accuracy', 0)}% | "
              f"LY {sa.get('ly', {}).get('accuracy', 0)}% | "
              f"ML {sa.get('ml', {}).get('accuracy', 0)}% | "
              f"AT {sa.get('at', {}).get('accuracy', 0)}%")
    else:
        print(f"   ❌ {phases['fusion'].get('message', '未知错误')}")
    print()

    # ── Phase 6: WalkForward (always, 轻量DB查询) ──
    print(f"▶ Phase 6/8: WalkForward 滑动窗口验证")
    phases["walkforward"] = phase6_walkforward()
    wf_status = phases["walkforward"].get("status", "error")
    if wf_status == "ok":
        wf = phases["walkforward"]
        print(f"   ✅ IS {wf.get('avg_is_accuracy', 'N/A')}% → OOS {wf.get('avg_oos_accuracy', 'N/A')}% "
              f"(衰减比 {wf.get('decay_ratio', 'N/A')}%)")
    elif wf_status == "skipped":
        print(f"   ⏭️ {phases['walkforward'].get('message', '跳过')}")
    else:
        print(f"   ⚠️  WalkForward 异常")
    print()

    # ── Phase 7: 权重网格扫描 (always, 轻量DB查询) ──
    print(f"▶ Phase 7/8: 权重网格扫描")
    phases["weight_sweep"] = phase7_weight_sweep()
    ws_status = phases["weight_sweep"].get("status", "error")
    if ws_status == "ok":
        bw = phases["weight_sweep"].get("best_weights", {})
        if bw:
            print(f"   ✅ 最优 ly={bw.get('ly','?')} ml={bw.get('ml','?')} at={bw.get('at','?')} "
                  f"→ {bw.get('accuracy','?')}% ({bw.get('correct',0)}/{bw.get('total',0)})")
        else:
            print(f"   ✅ 扫描完成 (未找到最优行)")
    elif ws_status == "skipped":
        print(f"   ⏭️ {phases['weight_sweep'].get('message', '跳过')}")
    else:
        print(f"   ⚠️  权重扫描异常")
    print()

    # ── Phase 8: 模拟交易 (always, 轻量DB查询) ──
    print(f"▶ Phase 8/8: 模拟交易")
    phases["simulate"] = phase8_simulate()
    si_status = phases["simulate"].get("status", "error")
    if si_status == "ok":
        si = phases["simulate"]
        print(f"   ✅ 总收益 {si.get('total_return', 'N/A')}% | "
              f"年化 {si.get('annual_return', 'N/A')}% | "
              f"最大回撤 {si.get('max_drawdown', 'N/A')}%")
    elif si_status == "skipped":
        print(f"   ⏭️ {phases['simulate'].get('message', '跳过')}")
    else:
        print(f"   ⚠️  模拟交易异常")
    print()

    # ── Phase 2: LY 独立回测 (only --full) ──
    if not is_quick:
        print(f"▶ Phase 2/4: LY 独立回测")
        phases["ly"] = phase2_ly()
        ly_status = phases["ly"].get("status", "error")
        if ly_status == "ok":
            ov = phases["ly"].get("overall", {})
            print(f"   ✅ LY 总体: {ov.get('accuracy', 'N/A')}% ({ov.get('correct',0)}/{ov.get('total',0)})")
            # P1: 持久化 LY walkforward 到 bt_meta
            _save_meta(f"ly_walkforward_{_today_str()}", json.dumps(ov))
        elif ly_status == "timeout":
            print(f"   ⏰ LY 超时，跳过")
        else:
            print(f"   ⚠️ {phases['ly'].get('message', '跳过')}")
        print()

    # ── Phase 5: 因子独立回测 (only --full) ──
    if not is_quick:
        print(f"▶ Phase 5/8: 因子独立回测")
        phases["factor"] = phase5_factor_backtest()
        fa_status = phases["factor"].get("status", "error")
        if fa_status == "ok":
            fa = phases["factor"]
            print(f"   ✅ 因子准确率: {fa.get('accuracy', 'N/A')}% "
                  f"({fa.get('total_evaluations', 0)}次评估, {fa.get('stocks_evaluated', 0)}只股票)")
            if fa.get("conclusion"):
                print(f"      {fa['conclusion']}")
        elif fa_status == "skipped":
            print(f"   ⏭️ {phases['factor'].get('message', '跳过')}")
        else:
            print(f"   ⚠️  因子回测异常")
        print()

    # ── Phase 3: ML 独立回测 (only --full) ──
    if not is_quick:
        print(f"▶ Phase 3/8: ML 独立回测")
        phases["ml"] = phase3_ml()
        ml_status = phases["ml"].get("status", "error")
        if ml_status == "ok":
            op = phases["ml"].get("operation_advice", {})
            ss = phases["ml"].get("sentiment_score", {})
            fe = phases["ml"].get("fusion_equivalent", {})
            print(f"   ✅ ML operation: {op.get('direction_accuracy', 'N/A')}% | "
                  f"sentiment: {ss.get('accuracy', 'N/A')}% | "
                  f"fusion等效: {fe.get('accuracy', 'N/A')}% ({fe.get('total', 0)}样本)")
        else:
            print(f"   ⚠️ {phases['ml'].get('message', '跳过')}")
        print()

    # ── Phase 4: AT 回测 (always) ──
    if not is_quick:
        print(f"▶ Phase 4/8: AT 回测 (融合层面)")
        phases["at"] = phase4_at()
        at_status = phases["at"].get("status", "ok")
        if at_status == "ok":
            print(f"   ✅ AT: {phases['at'].get('accuracy', 'N/A')}% ({phases['at'].get('correct',0)}/{phases['at'].get('total',0)})")
        print()

    # ── 生成报告 ──
    print(f"▶ 生成统一报告 ...")
    report = generate_unified_report(phases)

    # 把融合原始数据附在报告里用于渲染
    report["fusion_raw"] = phases.get("fusion", {})

    # 保存 JSON
    UNIFIED_REPORT_JSON.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"   ✅ JSON: {UNIFIED_REPORT_JSON.relative_to(PROJECT_ROOT)}")

    # 保存 Markdown
    md = render_markdown(report)
    UNIFIED_REPORT_MD.write_text(md, encoding="utf-8")
    print(f"   ✅ MD:   {UNIFIED_REPORT_MD.relative_to(PROJECT_ROOT)}")

    # 保存 last_run (用于下次变化检测)
    LAST_RUN_FILE.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"   ✅ State: {LAST_RUN_FILE.relative_to(PROJECT_ROOT)}")

    # 统一口径控制台输出（覆盖 Phase 1 的融合原始数据）
    _subs = report.get("subsystems", {})
    _fusion = report.get("fusion_raw", {}).get("subsystem_accuracy", {}).get("fusion", {}).get("accuracy", 0)
    _ly = _subs.get("ly", {}).get("accuracy_pct", "?")
    _ml = _subs.get("ml", {}).get("accuracy_pct", "?")
    _at = _subs.get("at", {}).get("accuracy_pct", "?")
    print(f"\n  📊 融合 {_fusion}% | LY {_ly}% | ML {_ml}% | AT {_at}% (独立回测)")

    # 告警摘要
    alerts = report.get("changes", {}).get("alerts", [])
    if alerts:
        print(f"\n  ⚠️ 告警 ({len(alerts)} 条):")
        for a in alerts:
            print(f"     {a}")

    print(f"\n{'='*55}")
    print(f"  c1test 完成")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
