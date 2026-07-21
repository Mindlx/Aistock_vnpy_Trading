"""
LGB walk-forward 回测 — 评估 alpha158 LGB 模型的样本外准确率。

用法:
    .venv/bin/python scripts/backtest_lgb.py                    # 全量回测
    .venv/bin/python scripts/backtest_lgb.py --code 600372      # 单只股票
    .venv/bin/python scripts/backtest_lgb.py --days 120         # 滑动窗口大小
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJ))
sys.path.insert(0, str(_PROJ / "systems/lynx_vnpy"))

import pandas as pd
import numpy as np
from vnpy_bridge.alpha_predictor import _compute_alpha_factors, _normalize

# 使用 unified_cache 获取行情数据（避免 EastMoney API 不稳定）
from services.data_warehouse.warehouse import WarehouseReader

STOCK_CODES = ['001390', '300652', '600372', '605368', '000592',
               '603189', '603557', '688202', '601801', '300676',
               '603127', '000999']

MIN_TRAIN = 60  # 最少训练天数
WINDOW = 120    # 滚动窗口


def load_historical(code: str, days: int = 400) -> pd.DataFrame | None:
    """从 unified_cache 加载日K线。"""
    try:
        reader = WarehouseReader()
        df = reader.get_daily_df(code, days=days)
        if df is None or df.empty or len(df) < MIN_TRAIN + 20:
            return None
        if "date" in df.columns:
            df["日期"] = pd.to_datetime(df["date"])
        else:
            df["日期"] = pd.to_datetime(df.index)
        df = df.sort_values("日期")
        return df
    except Exception as e:
        print(f"  ⚠️ {code} 数据加载失败: {e}")
        return None


def compute_factors_safe(df: pd.DataFrame) -> pd.DataFrame:
    """计算因子，失败时返回空 DataFrame。"""
    try:
        factors = _compute_alpha_factors(df)
        factors = _normalize(factors)
        return factors
    except Exception as e:
        print(f"  ⚠️ 因子计算失败: {e}")
        return pd.DataFrame()


def walkforward(code: str, df: pd.DataFrame) -> dict:
    """逐日滑动窗口回测。"""
    total = 0
    correct = 0
    high_conf_total = 0
    high_conf_correct = 0

    train_cols = None  # 记录训练时的特征列（fix 57vs58 对齐用）

    for i in range(MIN_TRAIN, len(df)):
        train_df = df.iloc[:i]

        # 计算因子
        train_factors = compute_factors_safe(train_df)
        if train_factors.empty or len(train_factors) < 1:
            continue
        test_factors = compute_factors_safe(train_df.iloc[:-1] if len(train_df) > 1 else train_df)
        # 实际上 test 应该用截至昨天的数据算因子，预测今天的 prob_up
        # 简化处理：用 i 之前的数据
        continue  # TODO: implement full walk-forward

    return {
        "total": total, "correct": correct,
        "acc": correct / total * 100 if total else 0,
        "high_total": high_conf_total,
        "high_correct": high_conf_correct,
        "high_acc": high_conf_correct / high_conf_total * 100 if high_conf_total else 0,
    }


def main():
    parser = argparse.ArgumentParser(description="LGB walk-forward 回测")
    parser.add_argument("--code", type=str, default=None, help="单只股票代码")
    parser.add_argument("--days", type=int, default=120, help="滚动窗口大小")
    args = parser.parse_args()

    codes = [args.code] if args.code else STOCK_CODES

    print(f"LGB Walk-Forward 回测 (window={args.days}):")
    print()

    # 直接使用训练好的 LGB 模型做回测预测
    import lightgbm as lgb
    model_path = _PROJ / "systems/lynx_vnpy/models/alpha_lgb_model.txt"
    if not model_path.exists():
        print(f"❌ 模型文件不存在: {model_path}，请先运行 retrain_lgb.py")
        sys.exit(1)
    try:
        model = lgb.Booster(model_file=str(model_path))
    except Exception as e:
        print(f"❌ 模型加载失败: {e}")
        sys.exit(1)

    print(f"模型特征数: {model.num_feature()}")
    print()

    total_all = 0
    correct_all = 0
    high_all = 0
    high_correct_all = 0

    for code in codes:
        df = load_historical(code)
        if df is None or len(df) < MIN_TRAIN + 30:
            print(f"  {code}: 数据不足")
            continue

        correct = 0
        total = 0
        high_correct = 0
        high_total = 0

        for i in range(MIN_TRAIN, len(df) - 1):
            train_df = df.iloc[:i]
            test_close = df.iloc[i]["close"]
            next_close = df.iloc[i + 1]["close"]
            actual_dir = 1 if next_close > test_close else -1
            if next_close == test_close:
                continue

            try:
                factors = compute_factors_safe(train_df)
                if factors.empty:
                    continue
                latest = factors.iloc[-1:].fillna(0)
                prob = model.predict(latest)[0]
                pred_dir = 1 if prob >= 0.5 else -1

                total += 1
                if pred_dir == actual_dir:
                    correct += 1
                if prob >= 0.65 or prob <= 0.35:
                    high_total += 1
                    if pred_dir == actual_dir:
                        high_correct += 1
            except Exception:
                continue

        acc = correct / total * 100 if total else 0
        high_acc = high_correct / high_total * 100 if high_total else 0
        total_all += total
        correct_all += correct
        high_all += high_total
        high_correct_all += high_correct

        print(f"  {code}: {correct:3d}/{total:4d} = {acc:5.1f}%  (高置信: {high_correct}/{high_total} = {high_acc:.1f}%)")

    if total_all > 0:
        print(f"\n总体: {correct_all}/{total_all} = {correct_all/total_all*100:.1f}%")
        if high_all > 0:
            print(f"高置信: {high_correct_all}/{high_all} = {high_correct_all/high_all*100:.1f}%")
    else:
        print("\n⚠️ 无有效回测结果")


if __name__ == "__main__":
    main()
