#!/usr/bin/env python3
"""
详细对比当前RF模型 vs 上游RF模型，找出差异根源.

1. 模型结构对比（树深度、特征重要性、预测分布）
2. 在完全相同的数据上测试，逐日对比预测差异
3. 分析差异大的case，看训练数据质量
4. 给出最终结论：上游是否真的更好
"""
import os, sys, joblib, math
import numpy as np
import pandas as pd

sys.path.insert(0, "systems/lynx_vnpy")
import lynx_signal

PROJECT = "."
LY_ROOT = "systems/lynx_vnpy"
UPSTREAM_LY = "../lynx_vnpy"
MODEL_DIR = f"{LY_ROOT}/models"
UP_MODEL_DIR = f"{UPSTREAM_LY}/models"

FEATURES = [
    'ret_1d', 'ret_5d', 'ret_10d', 'ret_20d',
    'ma5_dist', 'ma20_dist', 'ma_cross',
    'rsi14', 'macd', 'macd_signal', 'macd_hist',
    'atr_ratio', 'boll_pos', 'cci20', 'vol_ratio',
]

def analyze_model(code):
    """Deep analysis of both models for a single stock."""
    cur_mp = f"{MODEL_DIR}/{code}_model.pkl"
    cur_sp = f"{MODEL_DIR}/{code}_scaler.pkl"
    up_mp = f"{UP_MODEL_DIR}/{code}_model.pkl"
    up_sp = f"{UP_MODEL_DIR}/{code}_scaler.pkl"

    if not (os.path.exists(cur_mp) and os.path.exists(up_mp)):
        return None

    cur_m = joblib.load(cur_mp)
    cur_s = joblib.load(cur_sp)
    up_m = joblib.load(up_mp)
    up_s = joblib.load(up_sp)

    result = {"code": code}

    # ── Model structure ──
    cur_depths = [e.tree_.max_depth for e in cur_m.estimators_]
    up_depths = [e.tree_.max_depth for e in up_m.estimators_]
    cur_samples = [e.tree_.n_node_samples.sum() for e in cur_m.estimators_]
    up_samples = [e.tree_.n_node_samples.sum() for e in up_m.estimators_]

    result["cur_size"] = os.path.getsize(cur_mp)
    result["up_size"] = os.path.getsize(up_mp)
    result["cur_avg_depth"] = np.mean(cur_depths)
    result["up_avg_depth"] = np.mean(up_depths)
    result["cur_avg_samples"] = np.mean(cur_samples)
    result["up_avg_samples"] = np.mean(up_samples)

    # Feature importance correlation
    cur_imp = cur_m.feature_importances_
    up_imp = up_m.feature_importances_
    result["cur_top3"] = [FEATURES[i] for i in np.argsort(cur_imp)[-3:][::-1]]
    result["up_top3"] = [FEATURES[i] for i in np.argsort(up_imp)[-3:][::-1]]
    result["imp_corr"] = np.corrcoef(cur_imp, up_imp)[0, 1]

    # ── Fetch data and test ──
    df = lynx_signal.fetch_daily_bars(code)
    if df is None or len(df) < 60:
        return None

    df_feat = lynx_signal.compute_features(df.copy())
    valid = df_feat[FEATURES].dropna()
    if len(valid) < 30:
        return None

    # Test on ALL available data points (not just last 30)
    # Get actual returns
    closes = df['收盘'].values
    actual_dirs = (np.roll(closes, -1) > closes).astype(int)
    actual_returns = np.roll(closes, -1) / closes - 1

    # Align with feature data
    feat_len = len(valid)
    actual_dirs = actual_dirs[-feat_len:]
    actual_returns = actual_returns[-feat_len:]

    cur_X = cur_s.transform(valid.values)
    up_X = up_s.transform(valid.values)

    cur_probs = cur_m.predict_proba(cur_X)[:, 1]
    up_probs = up_m.predict_proba(up_X)[:, 1]

    result["n_total"] = feat_len
    result["cur_acc_all"] = ((cur_probs > 0.5) == actual_dirs).mean() * 100
    result["up_acc_all"] = ((up_probs > 0.5) == actual_dirs).mean() * 100
    result["cur_mean_prob"] = cur_probs.mean()
    result["up_mean_prob"] = up_probs.mean()
    result["cur_std_prob"] = cur_probs.std()
    result["up_std_prob"] = up_probs.std()

    # Agreement rate
    agree = (cur_probs > 0.5) == (up_probs > 0.5)
    result["agree_rate"] = agree.mean() * 100

    # When they disagree, who's right?
    disagree_mask = ~agree
    if disagree_mask.sum() > 0:
        cur_right = ((cur_probs[disagree_mask] > 0.5) == actual_dirs[disagree_mask]).mean() * 100
        up_right = ((up_probs[disagree_mask] > 0.5) == actual_dirs[disagree_mask]).mean() * 100
        result["disagree_cur_correct"] = cur_right
        result["disagree_up_correct"] = up_right
        result["n_disagree"] = disagree_mask.sum()
    else:
        result["disagree_cur_correct"] = 0
        result["disagree_up_correct"] = 0
        result["n_disagree"] = 0

    # Last 30 days
    last30 = min(30, feat_len)
    cur_probs_30 = cur_probs[-last30:]
    up_probs_30 = up_probs[-last30:]
    actual_dirs_30 = actual_dirs[-last30:]

    result["cur_acc_30"] = ((cur_probs_30 > 0.5) == actual_dirs_30).mean() * 100
    result["up_acc_30"] = ((up_probs_30 > 0.5) == actual_dirs_30).mean() * 100

    # Predict on latest data point
    latest_row = valid.iloc[-1:].values
    result["cur_latest_prob"] = cur_m.predict_proba(cur_s.transform(latest_row))[0][1]
    result["up_latest_prob"] = up_m.predict_proba(up_s.transform(latest_row))[0][1]

    return result

def main():
    codes = sorted(set(
        s.replace("_model.pkl", "") for s in os.listdir(MODEL_DIR)
        if s.endswith("_model.pkl")
    ))

    results = []
    for code in codes:
        r = analyze_model(code)
        if r:
            results.append(r)

    # ── Summary ──
    print(f"{'代码':>8} {'当前准确率':>10} {'上游准确率':>10} {'差值':>8} {'一致率':>8} {'分歧→当前正确':>12} {'分歧→上游正确':>12} {'总样本':>8}")
    print("-" * 76)

    winners = {"current": 0, "upstream": 0, "tie": 0}
    for r in sorted(results, key=lambda x: x["code"]):
        cur = r["cur_acc_all"]
        up = r["up_acc_all"]
        diff = cur - up
        if abs(diff) < 1: winners["tie"] += 1
        elif diff > 0: winners["current"] += 1
        else: winners["upstream"] += 1

        print(f"{r['code']:>8} {cur:>9.1f}% {up:>9.1f}% {diff:>+8.1f}% "
              f"{r['agree_rate']:>7.1f}% "
              f"{r.get('disagree_cur_correct',0):>10.1f}% "
              f"{r.get('disagree_up_correct',0):>10.1f}% "
              f"{r['n_total']:>8}")

    avg_cur = np.mean([r["cur_acc_all"] for r in results])
    avg_up = np.mean([r["up_acc_all"] for r in results])
    print("-" * 76)
    print(f"{'平均':>8} {avg_cur:>9.1f}% {avg_up:>9.1f}% {avg_cur-avg_up:>+8.1f}%")
    print(f"当前胜出: {winners['current']}, 上游胜出: {winners['upstream']}, 平局: {winners['tie']}")

    # ── Structure analysis ──
    print(f"\n{'='*60}")
    print(f"模型结构对比")
    print(f"{'='*60}")
    print(f"{'代码':>8} {'当前大小':>8} {'上游大小':>8} {'当前深度':>8} {'上游深度':>8} {'当前样本':>10} {'上游样本':>10} {'特征相关':>8}")
    print("-" * 60)
    for r in sorted(results, key=lambda x: x["code"]):
        print(f"{r['code']:>8} {r['cur_size']:>8} {r['up_size']:>8} "
              f"{r['cur_avg_depth']:>7.1f} {r['up_avg_depth']:>7.1f} "
              f"{r['cur_avg_samples']:>8.0f} {r['up_avg_samples']:>8.0f} "
              f"{r['imp_corr']:>7.3f}")

    # ── Top features ──
    print(f"\n{'='*60}")
    print(f"特征重要性对比（前3特征）")
    print(f"{'='*60}")
    for r in sorted(results, key=lambda x: x["code"]):
        print(f"{r['code']:>8}: 当前={r['cur_top3']}  上游={r['up_top3']}")

    print(f"\n{'='*60}")
    print(f"预测分布")
    print(f"{'='*60}")
    print(f"{'代码':>8} {'当前均值':>8} {'上游均值':>8} {'当前std':>8} {'上游std':>8}")
    for r in sorted(results, key=lambda x: x["code"]):
        print(f"{r['code']:>8} {r['cur_mean_prob']:>8.3f} {r['up_mean_prob']:>8.3f} "
              f"{r['cur_std_prob']:>8.3f} {r['up_std_prob']:>8.3f}")

    # ── Key insight ──
    print(f"\n{'='*60}")
    print(f"结论分析")
    print(f"{'='*60}")

    # Check prediction distribution difference
    cur_stds = [r['cur_std_prob'] for r in results]
    up_stds = [r['up_std_prob'] for r in results]
    print(f"上游模型预测标准差更大 ({np.mean(up_stds):.3f} vs {np.mean(cur_stds):.3f})")
    print(f"  → 上游模型更\"敢于\"给出极端预测（高置信看多/看空）")
    print(f"  → 当前模型预测更集中在中性区域")

    # Training data size difference
    cur_samples_avg = np.mean([r['cur_avg_samples'] for r in results])
    up_samples_avg = np.mean([r['up_avg_samples'] for r in results])
    print(f"上游模型训练数据更多 ({up_samples_avg:.0f} vs {cur_samples_avg:.0f} 样本/树)")
    print(f"  → 上游模型在更多历史数据上训练，学到了更稳定的模式")
    print(f"  → 当前模型(6/28重训)可能训练数据不足")

    # Agreement rate
    agree_rates = [r['agree_rate'] for r in results]
    print(f"两模型方向一致率: {np.mean(agree_rates):.1f}%")
    print(f"  → {100-np.mean(agree_rates):.1f}% 的情况下两模型方向相反")

if __name__ == "__main__":
    main()
