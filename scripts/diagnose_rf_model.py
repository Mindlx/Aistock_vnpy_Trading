#!/usr/bin/env python3
"""
Diagnose why RF model lost predictive power.

Compares:
1. Current model (Jun 28) vs upstream model (May 29) on the same test data
2. Training data quality - what data was used for each model
3. Feature distributions - any shift?
4. UnifiedCache health - was training data corrupted?
"""

import os, sys, json, joblib, pickle
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "systems/lynx_vnpy")
import lynx_signal

PROJECT = Path(".")
LY_ROOT = Path("systems/lynx_vnpy")
UPSTREAM_LY = Path("../lynx_vnpy")
MODEL_DIR = LY_ROOT / "models"
UP_MODEL_DIR = UPSTREAM_LY / "models"

def compare_models(code="601801"):
    """Compare current vs upstream model structure and features."""
    cur_model_path = MODEL_DIR / f"{code}_model.pkl"
    cur_scaler_path = MODEL_DIR / f"{code}_scaler.pkl"
    up_model_path = UP_MODEL_DIR / f"{code}_model.pkl"
    up_scaler_path = UP_MODEL_DIR / f"{code}_scaler.pkl"
    
    print(f"\n{'='*60}")
    print(f"模型对比: {code}")
    print(f"{'='*60}")
    
    for label, mp, sp in [
        ("当前 (Jun 28)", cur_model_path, cur_scaler_path),
        ("上游 (May 29)", up_model_path, up_scaler_path),
    ]:
        if not mp.exists():
            print(f"\n  {label}: 文件不存在")
            continue
        model = joblib.load(mp)
        scaler = joblib.load(sp)
        
        print(f"\n  ── {label} ──")
        print(f"  文件: {mp.name} ({mp.stat().st_size} bytes)")
        print(f"  模型类型: {type(model).__name__}")
        if hasattr(model, 'n_estimators'):
            print(f"  树数量: {model.n_estimators}")
        if hasattr(model, 'max_depth'):
            print(f"  最大深度: {model.max_depth}")
        if hasattr(model, 'feature_importances_'):
            feat_imp = model.feature_importances_
            print(f"  特征重要性: min={feat_imp.min():.4f} max={feat_imp.max():.4f} mean={feat_imp.mean():.4f}")
            print(f"  前3特征: {np.argsort(feat_imp)[-3:][::-1]}")
        if hasattr(model, 'estimators_'):
            print(f"  树数量(estimators): {len(model.estimators_)}")
            depths = [e.tree_.max_depth for e in model.estimators_]
            print(f"  树深度: min={min(depths)} max={max(depths)} avg={np.mean(depths):.1f}")
            # sklearn 1.9 uses n_node_samples to measure tree complexity
            n_samples = [e.tree_.n_node_samples.sum() for e in model.estimators_]
            print(f"  总样本量(所有树): min={min(n_samples)} max={max(n_samples)} avg={np.mean(n_samples):.0f}")
            # Feature count at each node
            n_features = [e.tree_.n_features for e in model.estimators_]
            print(f"  特征数: {set(n_features)}")

    # ── Training data comparison ──
    print(f"\n  ── 训练数据诊断 ──")
    # Fetch current data
    df = lynx_signal.fetch_daily_bars(code)
    if df is not None:
        print(f"  当前OHLCV数据: {len(df)} 行, {df.iloc[0].get('日期', 'N/A')} ~ {df.iloc[-1].get('日期', 'N/A')}")
        print(f"  最新价格: {df.iloc[-1].get('收盘', 'N/A')}")
        
        # Check feature completeness
        FEATURES = [
            'ret_1d', 'ret_5d', 'ret_10d', 'ret_20d',
            'ma5_dist', 'ma20_dist', 'ma_cross',
            'rsi14', 'macd', 'macd_signal', 'macd_hist',
            'atr_ratio', 'boll_pos', 'cci20', 'vol_ratio',
        ]
        df_feat = lynx_signal.compute_features(df.copy())
        na_count = df_feat[FEATURES].isna().sum().sum()
        total = len(df_feat) * len(FEATURES)
        print(f"  特征完整性: {total - na_count}/{total} ({100-na_count/total*100:.1f}%)")
        print(f"  训练可用样本 (dropna后): {len(df_feat[FEATURES + ['target']].dropna())}")

def test_model_inference(code="601801"):
    """Test both models on same latest data point."""
    print(f"\n{'='*60}")
    print(f"推理对比: {code}")
    print(f"{'='*60}")
    
    df = lynx_signal.fetch_daily_bars(code)
    if df is None:
        print("  无法获取数据")
        return
    
    df_feat = lynx_signal.compute_features(df.copy())
    FEATURES = [
        'ret_1d', 'ret_5d', 'ret_10d', 'ret_20d',
        'ma5_dist', 'ma20_dist', 'ma_cross',
        'rsi14', 'macd', 'macd_signal', 'macd_hist',
        'atr_ratio', 'boll_pos', 'cci20', 'vol_ratio',
    ]
    
    for label, mp, sp in [
        ("当前模型", MODEL_DIR / f"{code}_model.pkl", MODEL_DIR / f"{code}_scaler.pkl"),
        ("上游模型", UP_MODEL_DIR / f"{code}_model.pkl", UP_MODEL_DIR / f"{code}_scaler.pkl"),
    ]:
        if not mp.exists():
            continue
        model = joblib.load(mp)
        scaler = joblib.load(sp)
        
        row = df_feat[FEATURES].iloc[-1:].dropna()
        if row.empty:
            print(f"  {label}: 最新行特征为空")
            continue
        
        X = scaler.transform(row.values)
        prob = model.predict_proba(X)[0][1]
        
        # Also test with all recent data points
        all_valid = df_feat[FEATURES].dropna()
        if len(all_valid) >= 20:
            recent = all_valid.iloc[-20:]
            X_recent = scaler.transform(recent.values)
            probs = model.predict_proba(X_recent)[:, 1]
            print(f"\n  ── {label} ({mp.stat().st_size} bytes) ──")
            print(f"  最新预测 prob_up: {prob:.4f} ({prob*100:.1f}%)")
            print(f"  最近20日 prob_up 分布: min={probs.min():.4f} max={probs.max():.4f} mean={probs.mean():.4f} std={probs.std():.4f}")
            print(f"  中性区域(45-55%): {(probs >= 0.45).sum()}/20")
            print(f"  > 60%: {(probs > 0.60).sum()}/20, < 40%: {(probs < 0.40).sum()}/20")

def check_historical_data():
    """Check what historical prediction data is available."""
    print(f"\n{'='*60}")
    print(f"历史预测数据可用性")
    print(f"{'='*60}")
    
    # prob_up_log.csv
    log_path = PROJECT / "data/realtime/prob_up_log.csv"
    if log_path.exists():
        df = pd.read_csv(log_path)
        print(f"\n  prob_up_log.csv:")
        print(f"    行数: {len(df)}")
        print(f"    日期范围: {df['date'].min()} ~ {df['date'].max()}")
        print(f"    股票数: {df['stock_code'].nunique()}")
        print(f"    日期数: {df['date'].nunique()}")
    
    # bt_results.db check
    bt_path = PROJECT / "data/bt_results.db"
    if bt_path.exists():
        size = bt_path.stat().st_size
        import sqlite3
        conn = sqlite3.connect(str(bt_path))
        count = conn.execute("SELECT COUNT(*) FROM bt_predictions").fetchone()[0]
        date_range = conn.execute("SELECT MIN(pred_date), MAX(pred_date) FROM bt_predictions").fetchone()
        conn.close()
        print(f"\n  bt_results.db ({size/1024:.0f} KB):")
        print(f"    预测记录: {count}")
        print(f"    日期范围: {date_range[0]} ~ {date_range[1]}")
    
    # stock_analysis.db backtest data
    sa_path = PROJECT / "systems/MindLynx-Aistock/data/stock_analysis.db"
    if sa_path.exists():
        import sqlite3
        conn = sqlite3.connect(str(sa_path))
        bt_count = conn.execute("SELECT COUNT(*) FROM backtest_results").fetchone()[0]
        bt_dates = conn.execute("SELECT MIN(analysis_date), MAX(analysis_date) FROM backtest_results").fetchone()
        ah_count = conn.execute("SELECT COUNT(*) FROM analysis_history").fetchone()[0]
        ah_dates = conn.execute("SELECT MIN(created_at), MAX(created_at) FROM analysis_history").fetchone()
        conn.close()
        print(f"\n  stock_analysis.db:")
        print(f"    analysis_history: {ah_count} 条 ({str(ah_dates[0])[:10]} ~ {str(ah_dates[1])[:10]})")
        print(f"    backtest_results: {bt_count} 条 ({str(bt_dates[0])[:10]} ~ {str(bt_dates[1])[:10]})")

def test_upstream_models_all_stocks():
    """Test upstream models on all stocks to see if they have predictive power."""
    print(f"\n{'='*60}")
    print(f"上游模型 vs 当前模型 — 全股票方向预测对比")
    print(f"{'='*60}")
    
    FEATURES = [
        'ret_1d', 'ret_5d', 'ret_10d', 'ret_20d',
        'ma5_dist', 'ma20_dist', 'ma_cross',
        'rsi14', 'macd', 'macd_signal', 'macd_hist',
        'atr_ratio', 'boll_pos', 'cci20', 'vol_ratio',
    ]
    
    stocks = sorted(s.stem.replace('_model','') for s in MODEL_DIR.glob("*_model.pkl"))
    results = []
    
    for code in stocks:
        cur_mp = MODEL_DIR / f"{code}_model.pkl"
        up_mp = UP_MODEL_DIR / f"{code}_model.pkl"
        
        if not cur_mp.exists() or not up_mp.exists():
            continue
        
        cur_model = joblib.load(cur_mp)
        cur_scaler = joblib.load(MODEL_DIR / f"{code}_scaler.pkl")
        up_model = joblib.load(up_mp)
        up_scaler = joblib.load(UP_MODEL_DIR / f"{code}_scaler.pkl")
        
        df = lynx_signal.fetch_daily_bars(code)
        if df is None or len(df) < 30:
            continue
        
        df_feat = lynx_signal.compute_features(df.copy())
        valid = df_feat[FEATURES].dropna()
        if len(valid) < 20:
            continue
        
        # Test on LAST 30 trading days
        test_data = valid.iloc[-30:]
        # Get actual returns
        target = (df['收盘'].shift(-1) > df['收盘']).astype(int)
        actual = target.iloc[-len(test_data):].dropna().values
        
        # Align
        min_len = min(len(test_data), len(actual))
        if min_len < 10:
            continue
        
        test_data = test_data.iloc[:min_len]
        actual = actual[:min_len]
        
        cur_X = cur_scaler.transform(test_data.values)
        up_X = up_scaler.transform(test_data.values)
        
        cur_probs = cur_model.predict_proba(cur_X)[:, 1]
        up_probs = up_model.predict_proba(up_X)[:, 1]
        
        cur_correct = ((cur_probs > 0.5) == actual).mean()
        up_correct = ((up_probs > 0.5) == actual).mean()
        
        results.append({
            "code": code,
            "cur_acc": round(cur_correct * 100, 1),
            "up_acc": round(up_correct * 100, 1),
            "cur_mean_prob": round(cur_probs.mean(), 3),
            "up_mean_prob": round(up_probs.mean(), 3),
            "cur_std_prob": round(cur_probs.std(), 3),
            "up_std_prob": round(up_probs.std(), 3),
            "n": min_len,
        })
    
    if results:
        print(f"\n  {'代码':<8} {'样本':>4} {'当前准确率':>10} {'上游准确率':>10} {'当前均值':>8} {'上游均值':>8} {'当前std':>8} {'上游std':>8}")
        print(f"  {'-'*72}")
        for r in sorted(results, key=lambda x: x['code']):
            print(f"  {r['code']:<8} {r['n']:>4} {r['cur_acc']:>9.1f}% {r['up_acc']:>9.1f}% {r['cur_mean_prob']:>8.3f} {r['up_mean_prob']:>8.3f} {r['cur_std_prob']:>8.3f} {r['up_std_prob']:>8.3f}")
        
        avg_cur = np.mean([r['cur_acc'] for r in results])
        avg_up = np.mean([r['up_acc'] for r in results])
        print(f"\n  平均: 当前={avg_cur:.1f}%  上游={avg_up:.1f}%")
        
        # Count how many upstream models beat current
        wins = sum(1 for r in results if r['up_acc'] > r['cur_acc'])
        losses = sum(1 for r in results if r['up_acc'] < r['cur_acc'])
        print(f"  上游胜出: {wins}/{len(results)}, 当前胜出: {losses}/{len(results)}")

if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "601801"
    compare_models(code)
    test_model_inference(code)
    check_historical_data()
    test_upstream_models_all_stocks()
