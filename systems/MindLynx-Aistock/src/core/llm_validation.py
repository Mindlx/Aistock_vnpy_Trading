"""LLM signal accuracy validation.

Compares LLM analysis signals against actual forward returns to
answer: does the LLM add value over the factor-only baseline (53.7%)?

Usage: python -m src.core.llm_validation
"""

from __future__ import annotations

import logging
import math
import sqlite3
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)


def validate_llm_accuracy(
    db_path: str = "data/stock_analysis.db",
    forward_days: int = 5,
) -> dict:
    """Validate LLM signals against forward returns.

    Args:
        db_path: SQLite database path
        forward_days: number of trading days to look forward

    Returns:
        dict with overall and per-stock accuracy stats.
    """
    conn = sqlite3.connect(db_path)
    now = date.today()

    # Get analyses with sentiment scores
    rows = conn.execute(
        "SELECT id, code, sentiment_score, operation_advice, created_at "
        "FROM analysis_history WHERE sentiment_score IS NOT NULL "
        "ORDER BY code, created_at"
    ).fetchall()

    results: list[dict] = []

    for rid, code, score, advice, created_at in rows:
        dt = created_at[:10]  # date part
        age_days = (now - date.fromisoformat(dt)).days
        if age_days < 3:  # MIN_AGE check
            continue

        # Find analysis-day close
        price_row = conn.execute(
            "SELECT close FROM stock_daily WHERE code=? AND date <= ? ORDER BY date DESC LIMIT 1",
            (code, dt),
        ).fetchone()
        if not price_row:
            continue

        # Find forward close
        fwd_rows = conn.execute(
            "SELECT close FROM stock_daily WHERE code=? AND date > ? ORDER BY date ASC LIMIT ?",
            (code, dt, forward_days),
        ).fetchall()
        if len(fwd_rows) < forward_days:
            continue

        close_now = price_row[0]
        close_fwd = fwd_rows[-1][0]
        fwd_ret = (close_fwd - close_now) / close_now * 100 if close_now > 0 else 0

        results.append({
            "code": code,
            "score": score,
            "advice": advice or "",
            "fwd_ret": round(fwd_ret, 2),
            "close_now": close_now,
            "close_fwd": close_fwd,
            "date": dt,
        })

    conn.close()

    if not results:
        return {"error": "no matching records (MIN_AGE=3 not met or insufficient forward data)"}

    n = len(results)
    scores = [r["score"] for r in results]
    returns = [r["fwd_ret"] for r in results]

    # Overall stats
    # Binary: score>50 = predict up, score<=50 = predict down
    binary_correct = sum(
        1 for i in range(n)
        if (scores[i] > 50 and returns[i] > 0) or (scores[i] <= 50 and returns[i] <= 0)
    )
    binary_acc = binary_correct / n * 100

    # Pearson correlation: does higher score → higher return?
    mx = sum(scores) / n
    my = sum(returns) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in scores) / (n - 1))
    sy = math.sqrt(sum((y - my) ** 2 for y in returns) / (n - 1))
    if sx > 0 and sy > 0:
        r_val = sum((scores[i] - mx) * (returns[i] - my) for i in range(n)) / ((n - 1) * sx * sy)
    else:
        r_val = 0
    t_stat = r_val * math.sqrt((n - 2) / (1 - r_val * r_val)) if abs(r_val) < 1 else 99

    # By score band
    bands = {">=60 (看多)": [], "40-60 (中性)": [], "<=40 (看空)": []}
    for r in results:
        if r["score"] >= 60:
            bands[">=60 (看多)"].append(r)
        elif r["score"] <= 40:
            bands["<=40 (看空)"].append(r)
        else:
            bands["40-60 (中性)"].append(r)

    band_stats = {}
    for label, items in bands.items():
        if not items:
            continue
        correct = sum(1 for r in items if (r["score"] > 50 and r["fwd_ret"] > 0) or (r["score"] <= 50 and r["fwd_ret"] <= 0))
        band_stats[label] = {
            "count": len(items),
            "accuracy_pct": round(correct / len(items) * 100, 1),
            "avg_return_pct": round(sum(r["fwd_ret"] for r in items) / len(items), 2),
        }

    # By operation advice
    advice_stats = {}
    for advice in set(r["advice"] for r in results if r["advice"]):
        items = [r for r in results if r["advice"] == advice]
        if len(items) < 3:
            continue
        correct = sum(1 for r in items if r["fwd_ret"] > 0)
        advice_stats[advice] = {
            "count": len(items),
            "accuracy_pct": round(correct / len(items) * 100, 1),
        }

    # Per-stock
    per_stock = {}
    for code in set(r["code"] for r in results):
        items = [r for r in results if r["code"] == code]
        if len(items) < 3:
            continue
        ss = [r["score"] for r in items]
        rs = [r["fwd_ret"] for r in items]
        n2 = len(items)
        mx2 = sum(ss) / n2
        my2 = sum(rs) / n2
        sx2 = math.sqrt(sum((x - mx2)**2 for x in ss) / (n2 - 1))
        sy2 = math.sqrt(sum((y - my2)**2 for y in rs) / (n2 - 1))
        r2 = sum((ss[i] - mx2) * (rs[i] - my2) for i in range(n2)) / ((n2 - 1) * sx2 * sy2) if sx2 > 0 and sy2 > 0 else 0
        t2 = r2 * math.sqrt((n2 - 2) / (1 - r2 * r2)) if abs(r2) < 1 else 99
        bin_correct = sum(1 for i in range(n2) if (ss[i] > 50 and rs[i] > 0) or (ss[i] <= 50 and rs[i] <= 0))
        per_stock[code] = {
            "count": n2,
            "accuracy_pct": round(bin_correct / n2 * 100, 1),
            "correlation": round(r2, 3),
            "t_stat": round(t2, 1),
            "significant": abs(t2) > 1.96,
        }

    overall = {
        "total_evaluations": n,
        "binary_accuracy_pct": round(binary_acc, 1),
        "correlation": round(r_val, 4),
        "t_stat": round(t_stat, 1),
        "significant": abs(t_stat) > 1.96,
        "vs_factor_baseline": round(binary_acc - 53.7, 1),
        "verdict": _verdict(binary_acc, r_val, t_stat),
        "bands": band_stats,
        "advice_stats": advice_stats,
        "per_stock": per_stock,
    }

    return overall


def _verdict(accuracy: float, corr: float, t_stat: float) -> str:
    if accuracy > 53.7 and abs(t_stat) > 1.96:
        return f"✅ LLM 显著优于因子基线 (+{accuracy-53.7:.1f}%), 关系显著 (t={t_stat:.1f})"
    elif accuracy > 50:
        return f"🟡 LLM 略优于随机 ({accuracy:.1f}%), 但统计不显著"
    else:
        return f"❌ LLM 信号无预测能力 ({accuracy:.1f}%)"


def print_llm_report(results: dict) -> None:
    """Print a human-readable validation report."""
    if "error" in results:
        print(f"❌ {results['error']}")
        return

    print("=" * 60)
    print("  LLM 信号准确率验证")
    print(f"  前瞻窗口: 5交易日 | 因子基线: 53.7%")
    print("=" * 60)
    print()
    print(f"总评估: {results['total_evaluations']} 条")
    print(f"二分类准确率: {results['binary_accuracy_pct']}%")
    print(f"Pearson r: {results['correlation']:.4f} (t={results['t_stat']:.1f})")
    print(f"vs 因子基线(53.7%): {results['vs_factor_baseline']:+.1f}%")
    print(f"结论: {results['verdict']}")
    print()

    print("=== 评分段准确率 ===")
    for label, stats in results.get("bands", {}).items():
        print(f"  {label}: {stats['count']}条, 准确率{stats['accuracy_pct']}%, 平均收益{stats['avg_return_pct']}%")
    print()

    print("=== 操作建议准确率 ===")
    for advice, stats in sorted(results.get("advice_stats", {}).items(), key=lambda x: x[1]["accuracy_pct"], reverse=True):
        print(f"  {advice}: {stats['count']}条, 准确率{stats['accuracy_pct']}%")
    print()

    print("=== 每股票 ===")
    for code, stats in sorted(results.get("per_stock", {}).items(), key=lambda x: x[1]["accuracy_pct"], reverse=True):
        sig = "✅" if stats["significant"] else ""
        print(f"  {code}: {stats['count']}条, 准确率{stats['accuracy_pct']}%, r={stats['correlation']}, t={stats['t_stat']} {sig}")


if __name__ == "__main__":
    results = validate_llm_accuracy()
    print_llm_report(results)
