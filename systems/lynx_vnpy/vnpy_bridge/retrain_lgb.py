#!/usr/bin/env python3
"""
LGB模型自动重训 — 每月一次, 在15:15与ly信号同步执行

用法:
    python systems/lynx_vnpy/vnpy_bridge/retrain_lgb.py
"""
import sys
from pathlib import Path
_S = Path(__file__).resolve().parent
_PROJ = _S.parent.parent.parent  # → project root (3 up: vnpy_bridge→lynx_vnpy→systems→.)
sys.path.insert(0, str(_PROJ))
sys.path.insert(0, str(_S.parent))  # lynx_vnpy (for vnpy_bridge.*)

PROJ = _PROJ

from pathlib import Path
import numpy as np
import pandas as pd
import sqlite3
import lightgbm as lgb

from vnpy_bridge.alpha_predictor import _compute_alpha_factors, _normalize

DB = _PROJ / "systems/MindLynx-Aistock/data/stock_analysis.db"
MODEL = _PROJ / "systems/lynx_vnpy/models/alpha_lgb_model.txt"
LAST_RETRAIN = _PROJ / "data/vnpy_lab/.last_lgb_retrain"

MIN_DAYS = 20  # 至少20天才重训一次


def should_retrain() -> bool:
    if not MODEL.exists() or not LAST_RETRAIN.exists():
        return True
    import datetime
    last = datetime.datetime.fromtimestamp(LAST_RETRAIN.stat().st_mtime)
    return (datetime.datetime.now() - last).days >= MIN_DAYS


def main():
    import datetime
    print(f"[retrain_lgb] {datetime.datetime.now().isoformat()}")
    print(f"   模型: {MODEL}")

    if MODEL.exists() and not should_retrain():
        print(f"   跳过: 上次重训不足{MIN_DAYS}天")
        return

    conn = sqlite3.connect(str(DB))
    codes = pd.read_sql("SELECT DISTINCT code FROM stock_daily", conn)["code"].tolist()

    X_list, y_list = [], []
    for code in codes:
        df = pd.read_sql(
            f"SELECT date, open, high, low, close, volume FROM stock_daily "
            f"WHERE code='{code}' ORDER BY date", conn
        )
        if len(df) < 120:
            continue
        factors = _compute_alpha_factors(df)
        factors = _normalize(factors)
        close = df["close"].values.astype(float)
        target = ((close[1:] - close[:-1]) / close[:-1] > 0).astype(int)
        X = factors.values[:-1]
        y = target
        valid = ~(np.isnan(X).any(axis=1) | np.isnan(y))
        X_list.append(X[valid])
        y_list.append(y[valid])
    conn.close()

    X_all = np.concatenate(X_list)
    y_all = np.concatenate(y_list)
    print(f"   样本: {len(X_all)}, 特征: {X_all.shape[1]}")

    model = lgb.train({
        "objective": "binary", "verbosity": -1,
        "num_leaves": 8, "max_depth": 4, "min_data_in_leaf": 20,
        "feature_fraction": 0.4, "bagging_fraction": 0.7, "bagging_freq": 5,
        "lambda_l1": 0.5, "lambda_l2": 1.0, "learning_rate": 0.03,
    }, lgb.Dataset(X_all, label=y_all), num_boost_round=200)

    MODEL.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(MODEL))
    LAST_RETRAIN.parent.mkdir(parents=True, exist_ok=True)
    LAST_RETRAIN.touch()
    print(f"   ✅ 模型已保存 ({MODEL})")


if __name__ == "__main__":
    main()
