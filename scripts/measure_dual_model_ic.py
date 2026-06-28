#!/usr/bin/env python3
"""
Measure RF and LGB model IC (Information Coefficient).

Reads prob_up_log.csv for individual model predictions, then for each
prediction finds the actual next-trading-day return from stock_daily DB.
Computes:
  - Pearson correlation (predicted prob_up vs actual return)
  - Spearman rank correlation (predicted prob_up vs actual return)
  - Hit rate (prediction > 50% and actual up, or prediction < 50% and actual down)
  - Confusion matrix at threshold 50%
  - Direction accuracy (prob_up vs next_day pct_chg sign)

Output: summary to stdout + raw data to data/ic_measurement.csv
"""

import csv
import sqlite3
import statistics
import math
from pathlib import Path

# ── Configuration ──
PROB_LOG = Path("data/realtime/prob_up_log.csv")
STOCK_DB = Path("systems/MindLynx-Aistock/data/stock_analysis.db")
OUTPUT_CSV = Path("data/ic_measurement.csv")

# ── Load predictions ──
def load_predictions():
    """Load RF/LGB predictions from prob_up_log.csv."""
    preds = []
    with open(PROB_LOG, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rf = float(row["prob_up_rf"])
                lgb = float(row["prob_up_lgb"])
                ensemble = float(row["prob_up_ensemble"]) if row.get("prob_up_ensemble") else None
                preds.append({
                    "date": row["date"],
                    "code": row["stock_code"],
                    "name": row["stock_name"],
                    "prob_up_rf": rf,
                    "prob_up_lgb": lgb,
                    "prob_up_ensemble": ensemble or (rf + lgb) / 2.0,
                })
            except (ValueError, KeyError) as e:
                print(f"  ⚠ Skipping row: {row.get('date','?')}/{row.get('stock_code','?')} - {e}")
    return preds

# ── Load actual returns ──
def load_next_day_returns(preds):
    """
    For each prediction, find the next trading day's actual return.
    Uses stock_daily table to get pct_chg for the NEXT date after prediction date.
    """
    conn = sqlite3.connect(str(STOCK_DB))
    cur = conn.cursor()

    # Group predictions by stock code for efficient lookup
    stocks = set(p["code"] for p in preds)

    # Pre-fetch all trading days for each stock, sorted
    stock_days = {}
    for code in stocks:
        cur.execute(
            "SELECT date, pct_chg, close FROM stock_daily WHERE code = ? ORDER BY date",
            (code,)
        )
        stock_days[code] = cur.fetchall()
    conn.close()

    # For each prediction, find the next trading day
    matched = 0
    skipped = 0
    for p in preds:
        days = stock_days.get(p["code"], [])
        # Find prediction date in the list, get next day
        pred_date = p["date"]
        found = False
        for i, (d, pct, close) in enumerate(days):
            if d == pred_date:
                if i + 1 < len(days):
                    next_d, next_pct, next_close = days[i + 1]
                    p["next_date"] = next_d
                    p["next_pct_chg"] = next_pct
                    p["next_close"] = next_close
                    p["pred_date_close"] = close
                    matched += 1
                    found = True
                break
        if not found:
            p["next_date"] = None
            p["next_pct_chg"] = None
            p["next_close"] = None
            p["pred_date_close"] = None
            skipped += 1

    print(f"  Matched: {matched}, Skipped (no next trading day): {skipped}")
    return preds

# ── IC Calculation ──
def pearson_corr(x, y):
    """Pearson correlation coefficient."""
    n = len(x)
    if n < 3:
        return None
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
    std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))
    if std_x == 0 or std_y == 0:
        return None
    return cov / (std_x * std_y)

def spearman_corr(x, y):
    """Spearman rank correlation coefficient."""
    n = len(x)
    if n < 3:
        return None
    def rank(vals):
        sorted_vals = sorted(vals)
        return [sorted_vals.index(v) + 1 for v in vals]
    rx = rank(x)
    ry = rank(y)
    d_sq = sum((ri - rj) ** 2 for ri, rj in zip(rx, ry))
    return 1 - (6 * d_sq) / (n * (n * n - 1))

# ── Analysis ──
def analyze_ic(preds, prob_key, label):
    """Calculate IC and related metrics for a given model."""
    # Filter valid rows (need next_pct_chg)
    valid = [p for p in preds if p["next_pct_chg"] is not None]
    if not valid:
        print(f"  {label}: No valid data")
        return None

    prob_vals = [p[prob_key] for p in valid]
    ret_vals = [p["next_pct_chg"] for p in valid]

    # IC: Pearson and Spearman
    ic_pearson = pearson_corr(prob_vals, ret_vals)
    ic_spearman = spearman_corr(prob_vals, ret_vals)

    # Direction accuracy (prob_up > 50 → predict up, > 50 → predict down)
    correct = 0
    for p in valid:
        pred_up = p[prob_key] > 50
        actual_up = p["next_pct_chg"] > 0
        if pred_up == actual_up:
            correct += 1
    dir_acc = correct / len(valid) * 100

    # Hit rate: when prob_up > 60% or < 40%, how often correct?
    high_conf = [p for p in valid if p[prob_key] >= 60 or p[prob_key] <= 40]
    hc_correct = sum(1 for p in high_conf
                     if (p[prob_key] > 50) == (p["next_pct_chg"] > 0))
    hc_acc = hc_correct / len(high_conf) * 100 if high_conf else None

    # Confusion matrix at 50% threshold
    tp = sum(1 for p in valid if p[prob_key] > 50 and p["next_pct_chg"] > 0)
    fp = sum(1 for p in valid if p[prob_key] > 50 and p["next_pct_chg"] <= 0)
    tn = sum(1 for p in valid if p[prob_key] <= 50 and p["next_pct_chg"] <= 0)
    fn = sum(1 for p in valid if p[prob_key] <= 50 and p["next_pct_chg"] > 0)

    # Average return when model says up vs down
    up_ret = statistics.mean([p["next_pct_chg"] for p in valid if p[prob_key] > 50]) if any(p[prob_key] > 50 for p in valid) else None
    down_ret = statistics.mean([p["next_pct_chg"] for p in valid if p[prob_key] <= 50]) if any(p[prob_key] <= 50 for p in valid) else None

    # Precision, recall, F1
    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None
    f1 = 2 * precision * recall / (precision + recall) if (precision and recall and precision + recall > 0) else None

    return {
        "label": label,
        "n": len(valid),
        "ic_pearson": ic_pearson,
        "ic_spearman": ic_spearman,
        "dir_accuracy_pct": round(dir_acc, 1),
        "high_conf_pct": round(hc_acc, 1) if hc_acc else None,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": round(precision, 3) if precision else None,
        "recall": round(recall, 3) if recall else None,
        "f1": round(f1, 3) if f1 else None,
        "avg_up_return": round(up_ret, 4) if up_ret else None,
        "avg_down_return": round(down_ret, 4) if down_ret else None,
    }

# ── Main ──
def main():
    print("=" * 60)
    print("LY 双模型 IC 测量")
    print("=" * 60)

    # 1. Load predictions
    print("\n[1/4] 加载 prob_up_log.csv 预测数据...")
    preds = load_predictions()
    print(f"  加载 {len(preds)} 条预测, "
          f"日期范围: {min(p['date'] for p in preds)} ~ {max(p['date'] for p in preds)}")

    # 2. Match with actual returns
    print("\n[2/4] 匹配 stock_daily 实际下个交易日收益率...")
    preds = load_next_day_returns(preds)

    # 3. Calculate IC for each model
    print("\n[3/4] 计算 IC 指标...")
    results = {}
    for prob_key, label in [
        ("prob_up_rf", "RF"),
        ("prob_up_lgb", "LGB"),
        ("prob_up_ensemble", "Ensemble (算术平均)"),
    ]:
        r = analyze_ic(preds, prob_key, label)
        if r:
            results[label] = r

    # 4. Output
    print("\n" + "=" * 60)
    print("IC 测量结果")
    print("=" * 60)

    models = ["RF", "LGB", "Ensemble (算术平均)"]
    header = f"{'指标':<25} | {'RF':<12} {'LGB':<12} {'平均':<12}"
    sep = "-" * 25 + "-+-" + "-" * 12 + "-" + "-" * 12 + "-" + "-" * 12
    print(header)
    print(sep)

    rows = [
        ("样本数", "n"),
        ("Pearson IC", "ic_pearson"),
        ("Spearman IC", "ic_spearman"),
        ("方向准确率", "dir_accuracy_pct"),
        ("高置信准确率", "high_conf_pct"),
        ("精确率(Precision)", "precision"),
        ("召回率(Recall)", "recall"),
        ("F1", "f1"),
        ("预测↑平均收益", "avg_up_return"),
        ("预测↓平均收益", "avg_down_return"),
        ("TP/FP/FN/TN", None),  # special
    ]

    for disp, key in rows:
        if key is None:
            # Confusion matrix
            for m in models:
                if m in results:
                    r = results[m]
                    print(f"{'TP/FP/FN/TN':<25} | {r['tp']}/{r['fp']}/{r['fn']}/{r['tn']:<8}", end="")
                    break
            print()
            continue
        parts = [f"{disp:<25} |"]
        for m in models:
            if m in results:
                v = results[m].get(key)
                if v is None:
                    parts.append(f"{'N/A':>12}")
                elif isinstance(v, float):
                    parts.append(f"{v:>12.4f}" if abs(v) < 100 else f"{v:>12.1f}")
                else:
                    parts.append(f"{v:>12}")
            else:
                parts.append(f"{'N/A':>12}")
        print(" ".join(parts))

    # Ranking consistency check
    print("\n[4/4] 方向分歧分析...")
    disagree = 0
    both_correct = 0
    for p in preds:
        if p["next_pct_chg"] is None:
            continue
        rf_bull = p["prob_up_rf"] > 50
        lgb_bull = p["prob_up_lgb"] > 50
        actual_up = p["next_pct_chg"] > 0

        if rf_bull != lgb_bull:
            disagree += 1
            # When they disagree, who was right?
            if rf_bull == actual_up:
                both_correct += 1  # RF right
            elif lgb_bull == actual_up:
                both_correct -= 1  # LGB right
            # 0 means both wrong

    if disagree > 0:
        rf_wins = max(0, both_correct)
        lgb_wins = max(0, -both_correct)
        both_wrong = disagree - rf_wins - lgb_wins
        print(f"  方向分歧: {disagree}/{sum(1 for p in preds if p['next_pct_chg'] is not None)} 次")
        print(f"    分歧时 RF 正确: {rf_wins} 次 ({rf_wins/disagree*100:.1f}%)")
        print(f"    分歧时 LGB 正确: {lgb_wins} 次 ({lgb_wins/disagree*100:.1f}%)")
        print(f"    分歧时都错: {both_wrong} 次 ({both_wrong/disagree*100:.1f}%)")
    else:
        print("  方向无分歧")

    # Save raw data
    print(f"\n  保存原始数据到 {OUTPUT_CSV}")
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "date", "code", "name",
            "prob_up_rf", "prob_up_lgb", "prob_up_ensemble",
            "next_date", "next_pct_chg", "pred_date_close", "next_close",
        ])
        for p in sorted(preds, key=lambda x: (x["date"], x["code"])):
            writer.writerow([
                p["date"], p["code"], p["name"],
                p["prob_up_rf"], p["prob_up_lgb"], p["prob_up_ensemble"],
                p.get("next_date", ""), p.get("next_pct_chg", ""),
                p.get("pred_date_close", ""), p.get("next_close", ""),
            ])
    print("  ✓ 完成")

if __name__ == "__main__":
    main()
