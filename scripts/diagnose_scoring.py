"""
评分校准诊断脚本 — 扫描 sentiment_score  vs 真实涨跌幅的校准偏差

用法:
    python scripts/diagnose_scoring.py                          # 全量报告
    python scripts/diagnose_scoring.py --stock 605368           # 单只股票
    python scripts/diagnose_scoring.py --output calibration.md  # 输出Markdown
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DB = PROJECT_ROOT / "data" / "stock_analysis.db"
BACKTEST_DB = PROJECT_ROOT / "data" / "backtest" / "bt_results.db"
STOCK_POOL = PROJECT_ROOT / "config" / "stock_pool.csv"


def load_stock_names() -> dict[str, str]:
    """从自选股池加载股票名称"""
    names: dict[str, str] = {}
    if STOCK_POOL.exists():
        import csv
        with open(STOCK_POOL) as f:
            for row in csv.DictReader(f):
                code = row.get("code", "").strip()
                name = row.get("name", "").strip()
                if code:
                    names[code] = name
    return names


def load_analysis_scores() -> list[dict]:
    """从 analysis_history 加载所有评分记录"""
    conn = sqlite3.connect(str(ANALYSIS_DB))
    rows = conn.execute(
        "SELECT code, sentiment_score, report_type, operation_advice, created_at "
        "FROM analysis_history WHERE sentiment_score IS NOT NULL "
        "ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [
        {
            "code": r[0],
            "score": r[1],
            "report_type": r[2],
            "advice": r[3],
            "date": str(r[4])[:10] if r[4] else "",
        }
        for r in rows
    ]


def load_backtest_accuracy() -> dict[str, dict]:
    """从 bt_results.db 加载每只股票的融合准确率"""
    conn = sqlite3.connect(str(BACKTEST_DB))
    rows = conn.execute(
        "SELECT stock_code, "
        "  CAST(SUM(fusion_correct) AS REAL) / NULLIF(COUNT(*), 0), "
        "  COUNT(*), "
        "  CAST(SUM(CASE WHEN fusion_correct=1 THEN 1 ELSE 0 END) AS REAL), "
        "  SUM(CASE WHEN fusion_dir=1 THEN 1 ELSE 0 END), "
        "  SUM(CASE WHEN fusion_dir=-1 THEN 1 ELSE 0 END), "
        "  AVG(next_pct_chg) "
        "FROM bt_predictions GROUP BY stock_code"
    ).fetchall()
    conn.close()
    result: dict[str, dict] = {}
    for r in rows:
        result[r[0]] = {
            "accuracy": round(r[1] * 100, 1) if r[1] else 0,
            "total": r[2],
            "correct": int(r[3]) if r[3] else 0,
            "bullish": r[4] or 0,
            "bearish": r[5] or 0,
            "avg_return": round(r[6] or 0, 2),
        }
    return result


def analyze_calibration(scores: list[dict], bt: dict[str, dict]) -> list[dict]:
    """分析每只股票的评分校准偏差"""
    stock_scores: dict[str, list[int]] = defaultdict(list)
    for s in scores:
        stock_scores[s["code"]].append(s["score"])

    pool_names = load_stock_names()
    results = []
    for code, score_list in sorted(stock_scores.items()):
        avg_score = sum(score_list) / len(score_list)
        max_score = max(score_list)

        bt_info = bt.get(code, {})
        accuracy = bt_info.get("accuracy", None)
        total_preds = bt_info.get("total", 0)

        # 校准偏差 = 平均评分 - 准确率
        cal_bias = round(avg_score - (accuracy or 0), 1)

        # 过信判定: 偏差 > 20 分
        overconfident = cal_bias > 20 if accuracy is not None else None

        results.append({
            "code": code,
            "name": pool_names.get(code, ""),
            "avg_score": round(avg_score, 1),
            "max_score": max_score,
            "count": len(score_list),
            "accuracy": accuracy,
            "total_preds": total_preds,
            "cal_bias": cal_bias,
            "overconfident": overconfident,
            "avg_return": bt_info.get("avg_return"),
        })
    return results


def print_report(results: list[dict], markdown: bool = False):
    """打印校准报告"""
    # 按校准偏差排序（最过信在前）
    sorted_results = sorted(
        [r for r in results if r["accuracy"] is not None],
        key=lambda r: -r["cal_bias"],
    )

    lines = []
    if markdown:
        lines.append(f"# 评分校准诊断报告\n")
        lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        lines.append("## 汇总\n")
        lines.append("| 股票 | 名称 | 平均评分 | 最高分 | 样本数 | 准确率 | 校准偏差 | 判定 | 平均收益 |")
        lines.append("|------|------|---------|-------|-------|-------|---------|------|---------|")

        for r in sorted_results:
            flag = "🔴 过信" if r["overconfident"] else ("🟢 正常" if r["accuracy"] and r["accuracy"] > 40 else "🟡 偏低")
            lines.append(
                f"| {r['code']} | {r['name'] or '-':8s} | {r['avg_score']:>5.1f} | {r['max_score']:>3d} | "
                f"{r['count']:>3d} | {r['accuracy']:>5.1f}% | {r['cal_bias']:>+5.1f} | {flag} | "
                f"{r['avg_return']:>+6.2f}% |"
            )

        # 严重过信股票
        severe = [r for r in sorted_results if r["overconfident"]]
        if severe:
            lines.append("\n## 🔴 严重过信股票\n")
            lines.append("以下股票平均评分显著高于实际准确率（偏差 > 20 分）：\n")
            for r in severe:
                lines.append(
                    f"- **{r['code']} {r['name']}**: 平均评分 {r['avg_score']} vs "
                    f"准确率 {r['accuracy']}%（偏差 {r['cal_bias']:+} 分）"
                )
            lines.append(
                "\n> **建议**: 这些股票的 fusion_score 应在映射到 sentiment_score 时加大折扣力度。"
            )

        # 校准曲线
        lines.append("\n## 评分桶校准曲线\n")
        lines.append("| 评分区间 | 样本数 | 准确率 | 校准偏差 |")
        lines.append("|---------|-------|-------|---------|")
        buckets = [(0, 20), (21, 40), (41, 60), (61, 80), (81, 100)]
        for lo, hi in buckets:
            bucket_stocks = [r for r in sorted_results if lo <= r["avg_score"] <= hi]
            if bucket_stocks:
                avg_acc = sum(r["accuracy"] for r in bucket_stocks if r["accuracy"] is not None) / max(len(bucket_stocks), 1)
                avg_score = sum(r["avg_score"] for r in bucket_stocks) / len(bucket_stocks)
                bias = round(avg_score - avg_acc, 1)
                lines.append(f"| {lo}-{hi} | {len(bucket_stocks)} | {avg_acc:.1f}% | {bias:+.1f} |")
    else:
        print(f"\n评分校准诊断报告 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        print(f"{'代码':>8} {'名称':8s} {'评分(均)':>8s} {'最高':>4s} {'样本':>4s} {'准确率':>7s} {'偏差':>6s} {'判定':8s} {'收益':>6s}")
        print("-" * 75)
        for r in sorted_results:
            flag = "🔴" if r["overconfident"] else ("✅" if r["accuracy"] and r["accuracy"] > 40 else "⚠️")
            print(
                f"{r['code']:>8} {r['name'] or '-':8s} {r['avg_score']:>7.1f} {r['max_score']:>4d} "
                f"{r['count']:>4d} {r['accuracy'] or 0:>6.1f}% {r['cal_bias']:>+5.1f} "
                f"{flag:8s} {r['avg_return'] or 0:>+5.2f}%"
            )

    return "\n".join(lines)


def cmd_window_analysis():
    """分析不同窗口大小下每只股票的准确率变化，检测非平稳性。"""
    print("读取 bt_results.db ...")
    conn = sqlite3.connect(str(BACKTEST_DB))
    rows = conn.execute(
        "SELECT stock_code, date, fusion_correct FROM bt_predictions "
        "WHERE fusion_correct IS NOT NULL ORDER BY stock_code, date"
    ).fetchall()
    conn.close()

    stock_data: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        stock_data[r[0]].append({"date": r[1], "correct": r[2]})

    pool_names = load_stock_names()
    print(f"\n{'='*75}")
    print(f"  窗口敏感性分析 — 不同窗口下每只股票的准确率")
    print(f"{'='*75}")
    print(f"\n{'代码':>8} {'名称':8s} {'全量':>6s} {'N=30':>6s} {'N=20':>6s} {'N=10':>6s} {'极差':>5s} {'稳定性':8s}")
    print("-" * 65)

    unstable_stocks = []
    for code in sorted(stock_data.keys()):
        data = stock_data[code]
        hits = [d for d in data if d["correct"] == 1]
        total = len(data)

        full_acc = len(hits) / total * 100 if total > 0 else 0
        wins = [30, 20, 10]
        win_accs = {}
        for w in wins:
            if total >= w:
                recent = data[-w:]
                win_accs[w] = sum(1 for d in recent if d["correct"] == 1) / w * 100
            else:
                win_accs[w] = None

        # 极差 = 全量与最近窗口的最大差异
        valid_accs = [full_acc] + [v for v in win_accs.values() if v is not None]
        spread = max(valid_accs) - min(valid_accs)
        unstable = spread > 15

        name = pool_names.get(code, "")
        flag = "⚠️ 非平稳" if unstable else "✅ 平稳"
        if unstable:
            unstable_stocks.append((code, name, full_acc, win_accs, spread))

        w30 = f"{win_accs[30]:>5.1f}%" if win_accs[30] is not None else "   N/A"
        w20 = f"{win_accs[20]:>5.1f}%" if win_accs[20] is not None else "   N/A"
        w10 = f"{win_accs[10]:>5.1f}%" if win_accs[10] is not None else "   N/A"
        print(f"{code:>8} {name:8s} {full_acc:>5.1f}% {w30} {w20} {w10} {spread:>4.1f}% {flag}")

    if unstable_stocks:
        print(f"\n{'='*75}")
        print(f"  ⚠️  非平稳股票（极差 > 15%） — 需要近期加权估计准确率")
        print(f"{'='*75}\n")
        for code, name, full, win_accs, spread in unstable_stocks:
            details = " | ".join(f"N={w}={win_accs[w]:.1f}%" for w in [30, 20, 10] if win_accs[w] is not None)
            print(f"  {code} {name}: 全量={full:.1f}% 极差={spread:.1f}% ({details})")
        print()
        print("  建议: 这些股票的校准折扣应使用 EWMA 加权准确率，")
        print("  而非全量历史平均。半衰期建议 30-60 个交易日。")
    else:
        print("\n  所有股票的准确率在不同窗口下保持稳定。全量历史平均即可。")

    print(f"\n  数据范围: {rows[0][1]} ~ {rows[-1][1]}")


def main():
    parser = argparse.ArgumentParser(description="评分校准诊断")
    parser.add_argument("--stock", type=str, default=None, help="指定股票代码")
    parser.add_argument("--output", type=str, default=None, help="输出 Markdown 文件")
    parser.add_argument("--window-analysis", action="store_true", help="窗口敏感性分析")
    args = parser.parse_args()

    if args.window_analysis:
        cmd_window_analysis()
        return

    print(f"读取 analysis_history ...")
    scores = load_analysis_scores()
    print(f"  共 {len(scores)} 条评分记录")

    if args.stock:
        scores = [s for s in scores if s["code"] == args.stock]
        print(f"  筛选 {args.stock}: {len(scores)} 条")

    print(f"读取 bt_results.db ...")
    bt = load_backtest_accuracy()
    print(f"  共 {len(bt)} 只有回测数据")

    results = analyze_calibration(scores, bt)
    report = print_report(results, markdown=bool(args.output))

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(report)
        print(f"\n报告已保存: {out_path}")


if __name__ == "__main__":
    main()
