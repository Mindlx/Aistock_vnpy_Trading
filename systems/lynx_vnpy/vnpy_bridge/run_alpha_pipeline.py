#!/usr/bin/env python3
"""
Alpha研究院管线：Alpha158因子计算 → IC分析 → 模型训练 → 回测

用法:
    python systems/lynx_vnpy/vnpy_bridge/run_alpha_pipeline.py                    # 完整管线
    python systems/lynx_vnpy/vnpy_bridge/run_alpha_pipeline.py --factors-only     # 仅计算因子
    python systems/lynx_vnpy/vnpy_bridge/run_alpha_pipeline.py --train-model      # 训练模型
    python systems/lynx_vnpy/vnpy_bridge/run_alpha_pipeline.py --backtest         # 回测
    python systems/lynx_vnpy/vnpy_bridge/run_alpha_pipeline.py --all-data         # 全部历史数据
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
from scipy.stats import spearmanr
from sklearn.preprocessing import StandardScaler

# ──paths──────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_SCRIPT_DIR.parent.parent / "lynx_vnpy"))
# ─────────────────────────────────────────────────────

from lynx_vnpy.alpha.lab import AlphaLab
from lynx_vnpy.alpha.dataset import AlphaDataset, Segment
from lynx_vnpy.alpha.dataset.datasets.alpha_158 import Alpha158
from lynx_vnpy.alpha.dataset.datasets.alpha_101 import Alpha101
from lynx_vnpy.alpha.dataset.processor import process_drop_na, process_cs_norm
from lynx_vnpy.alpha.model.models.lgb_model import LgbModel
from lynx_vnpy.alpha.model.models.lasso_model import LassoModel
from lynx_vnpy.alpha.model.models.mlp_model import MlpModel
from lynx_vnpy.trader.constant import Interval

LAB_PATH = str(_PROJECT_ROOT / "data/vnpy_lab")
REPORT_DIR = _PROJECT_ROOT / "data/vnpy_lab/reports"

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
# 1. 数据加载
# ══════════════════════════════════════════════════════

def load_data(
    lab_path: str = LAB_PATH,
    start: str | None = None,
    end: str | None = None,
    max_symbols: int | None = None,
) -> pl.DataFrame:
    """从AlphaLab Parquet加载数据

    返回: Polars DataFrame with columns [datetime, vt_symbol, open, high, low, close, volume, turnover, vwap]
    """
    lab = AlphaLab(lab_path)

    # 检测可用的股票
    daily_dir = Path(lab_path) / "daily"
    vt_symbols = [f.stem for f in sorted(daily_dir.glob("*.parquet"))]
    if max_symbols:
        vt_symbols = vt_symbols[:max_symbols]

    if not vt_symbols:
        print("❌ 没有数据，先运行: python vnpy_bridge/data_converter.py")
        sys.exit(1)

    # 自动确定日期范围
    if not start or not end:
        sample = pl.read_parquet(str(daily_dir / f"{vt_symbols[0]}.parquet"))
        dates = sample["datetime"].to_list()
        default_start = datetime.fromisoformat(str(dates[0]))
        default_end = datetime.fromisoformat(str(dates[-1]))
        start = start or str(default_start.date())
        end = end or str(default_end.date())

    print(f"\n{'='*55}")
    print(f"  数据加载")
    print(f"{'='*55}")
    print(f"  股票数: {len(vt_symbols)}")
    print(f"  日期:   {start} ~ {end}")

    df = lab.load_bar_df(vt_symbols, Interval.DAILY, start, end, extended_days=0)
    if df is None:
        print("❌ 数据加载失败")
        sys.exit(1)
    print(f"  总行数: {len(df)}")
    return df


# ══════════════════════════════════════════════════════
# 2. Alpha158因子计算
# ══════════════════════════════════════════════════════

def compute_alpha158(
    df: pl.DataFrame,
    train_period: tuple[str, str],
    valid_period: tuple[str, str],
    test_period: tuple[str, str],
) -> AlphaDataset:
    """计算全部Alpha158因子

    Returns:
        包含158因子计算结果的AlphaDataset
    """
    print(f"\n{'='*55}")
    print(f"  Alpha158因子计算 (158因子)")
    print(f"{'='*55}")

    # 构建数据集
    dataset = Alpha158(
        df,
        train_period=train_period,
        valid_period=valid_period,
        test_period=test_period,
    )

    # 计算因子（并行）
    dataset.prepare_data()

    # 添加数据处理器（填NaN+截面归一化）
    dataset.add_processor("infer", lambda df: process_drop_na(df))
    dataset.add_processor("learn", lambda df: process_drop_na(df))
    dataset.add_processor("learn", lambda df: process_cs_norm(df, method="robust"))
    dataset.process_data()

    print(f"  因子计算+预处理完成")

    return dataset


# ══════════════════════════════════════════════════════
# 3. IC分析
# ══════════════════════════════════════════════════════

def analyze_ic(dataset: AlphaDataset) -> dict[str, dict]:
    """对每个因子计算IC并排名

    Returns:
        {factor_name: {ic_mean, ic_std, icir, pos_ratio}}
    """
    # 获取因子名称：从已计算的数据集列中识别
    df_sample = dataset.fetch_learn(Segment.TRAIN)
    _skip_cols = {"datetime", "vt_symbol", "label", "open", "high", "low", "close", "volume", "turnover", "vwap", "open_interest"}
    factor_names = [c for c in df_sample.columns if c not in _skip_cols]

    print(f"\n{'='*55}")
    print(f"  因子IC分析 ({len(factor_names)}因子)")
    print(f"{'='*55}")

    # 获取训练集数据（含因子值+标签）
    df = dataset.fetch_learn(Segment.TRAIN)
    results = {}
    n_valid = 0

    for fn in factor_names:
        fv = df[fn].to_numpy()
        label = df["label"].to_numpy()

        valid = ~(np.isnan(fv) | np.isinf(fv) | np.isnan(label))
        if valid.sum() < 30:
            continue

        try:
            ic, pval = spearmanr(fv[valid], label[valid])
        except Exception:
            continue

        if np.isnan(ic):
            continue

        n_valid += 1
        results[fn] = {"ic": round(ic, 4), "pval": round(pval, 4), "n": int(valid.sum())}

    # 排序
    sorted_results = sorted(results.items(), key=lambda x: abs(x[1]["ic"]), reverse=True)

    print(f"  有效因子: {n_valid}/{len(factor_names)}")
    print(f"\n  Top 20 因子 (按|IC|排序):")
    print(f"  {'因子名':<30} {'IC':<10} {'p值':<10} {'样本数'}")
    print(f"  {'-'*30} {'-'*10} {'-'*10} {'-'*6}")
    for fn, info in sorted_results[:20]:
        ic = info["ic"]
        star = "*" if info["pval"] < 0.05 else " "
        print(f"  {fn:<30} {ic:>+7.4f} {star:<3} {info['pval']:<7.4f} {info['n']}")

    # IC分布统计
    ics = [v["ic"] for v in results.values()]
    mean_ic = float(np.mean(ics))
    std_ic = float(np.std(ics))
    pos_ratio = sum(1 for v in ics if v > 0) / len(ics) if ics else 0
    print(f"\n  IC分布: 均值={mean_ic:+.4f} | 标准差={std_ic:.4f} | 正IC占比={pos_ratio:.1%}")

    return {fn: info for fn, info in sorted_results}


# ══════════════════════════════════════════════════════
# 4. 因子筛选
# ══════════════════════════════════════════════════════

def select_factors(
    ic_results: dict[str, dict],
    min_abs_ic: float = 0.01,
    max_factors: int = 30,
) -> list[str]:
    """按IC筛选因子

    策略: |IC|阈值筛选 → IC为正 → 取top-K
    """
    candidates = [
        (fn, info) for fn, info in ic_results.items()
        if abs(info["ic"]) >= min_abs_ic
    ]
    candidates.sort(key=lambda x: abs(x[1]["ic"]), reverse=True)
    selected = [fn for fn, _ in candidates[:max_factors]]

    print(f"\n{'='*55}")
    print(f"  因子筛选")
    print(f"{'='*55}")
    print(f"  候选池: |IC|>={min_abs_ic} → {len(candidates)}个")
    print(f"  精选:   top-{max_factors}")
    print(f"  精选列表:")
    for fn, info in candidates[:max_factors]:
        print(f"    {fn:<30} IC={info['ic']:+.4f}")

    return selected


# ══════════════════════════════════════════════════════
# 5. 模型训练 + 对比
# ══════════════════════════════════════════════════════

def train_models(
    dataset: AlphaDataset,
    selected_factors: list[str],
) -> dict[str, float]:
    """训练LGB + Lasso，输出准确率

    Returns:
        {model_name: accuracy}
    """
    print(f"\n{'='*55}")
    print(f"  模型训练")
    print(f"{'='*55}")

    # ---- LGB（强正则化，防过拟合）----
    print(f"\n  ── LGB Model ──")
    n_features = len(dataset.feature_expressions) if hasattr(dataset, 'feature_expressions') else 240
    print(f"  特征数: {n_features}")

    # 当特征来自合并数据集时,直接训练numpy数组
    train_df_check = dataset.fetch_learn(Segment.TRAIN).to_pandas()
    n_features = train_df_check.select_dtypes(include=['number']).drop(columns=['label'], errors='ignore').shape[1]
    print(f"  特征数: {n_features}")

    if n_features > 200:
        train_df = dataset.fetch_learn(Segment.TRAIN).to_pandas()
        train_df = train_df.replace([np.inf, -np.inf], 0.0).fillna(0.0).select_dtypes(include=['number'])
        y = train_df['label'].values
        X = StandardScaler().fit_transform(np.nan_to_num(train_df.drop(columns=['label'], errors='ignore').values))
        import lightgbm as lgb_m
        lgb_model = lgb_m.train({
            "objective": "binary", "verbosity": -1, "num_leaves": 8, "max_depth": 4,
            "min_data_in_leaf": 20, "feature_fraction": 0.4, "bagging_fraction": 0.7,
            "bagging_freq": 5, "lambda_l1": 0.5, "lambda_l2": 1.0, "learning_rate": 0.03,
        }, lgb_m.Dataset(X, label=y), num_boost_round=200)

        # 验证集预测
        valid_df = dataset.fetch_learn(Segment.VALID).to_pandas()
        valid_df = valid_df.replace([np.inf, -np.inf], 0.0).fillna(0.0).select_dtypes(include=['number'])
        yv = valid_df['label'].values
        Xv = StandardScaler().fit_transform(np.nan_to_num(valid_df.drop(columns=['label'], errors='ignore').values))
        pred = lgb_model.predict(Xv)
        lgb_acc = _calc_accuracy_np(pred, yv)
        print(f"  LGB验证准确率: {lgb_acc:.1f}% (合并数据集)")
    else:
        lgb = LgbModel(learning_rate=0.03, num_leaves=8, num_boost_round=300)
    # 覆写params增加强正则化
    lgb.params.update({
        "lambda_l1": 0.5,
        "lambda_l2": 1.0,
        "min_data_in_leaf": 20,
        "min_sum_hessian_in_leaf": 1e-3,
        "feature_fraction": 0.4,   # 每棵树只看40%特征
        "bagging_fraction": 0.7,
        "bagging_freq": 5,
        "max_depth": 4,
    })
    lgb.fit(dataset)
    pred = lgb.predict(dataset, Segment.VALID)
    lgb_acc = _calc_accuracy(pred, dataset, Segment.VALID)
    print(f"  LGB验证准确率: {lgb_acc:.1f}%")

    # 特征重要性
    if lgb.model:
        importance = lgb.model.feature_importance(importance_type="gain")
        names = lgb.model.feature_name()
        sorted_idx = np.argsort(importance)[::-1]
        print(f"  Top 10 特征 (Gain):")
        for i in sorted_idx[:10]:
            print(f"    {names[i]:<30} gain={importance[i]:.1f}")

    # ---- Lasso（需要无NaN数据）----
    lasso_acc = 0.0
    try:
        print(f"\n  ── Lasso Model ──")
        lasso = LassoModel(alpha=0.01)
        lasso.fit(dataset)
        pred_lasso = lasso.predict(dataset, Segment.VALID)
        lasso_acc = _calc_accuracy(pred_lasso, dataset, Segment.VALID)
        print(f"  Lasso验证准确率: {lasso_acc:.1f}%")
        if hasattr(lasso, "model") and lasso.model is not None:
            coef = lasso.model.coef_
            n_nonzero = np.sum(np.abs(coef) > 1e-6)
            print(f"  Lasso自动选中特征: {n_nonzero}/{len(coef)}")
    except Exception as e:
        print(f"  ⚠️ Lasso训练跳过: {e}")

    # ---- MLP（神经网络）----
    mlp_acc = 0.0
    try:
        from lynx_vnpy.alpha.model.models.mlp_model import MlpModel, _HAS_TORCH
        print(f"\n  ── MLP Model ──")
        if not _HAS_TORCH:
            print(f"  ⚠️ 需要PyTorch,跳过")
        else:
            mlp = MlpModel(input_size=935, hidden_sizes=(64, 32), lr=0.001, n_epochs=100,
                           batch_size=64, early_stop_rounds=20, weight_decay=0.01, seed=42)
            mlp.fit(dataset)
            pred_mlp = mlp.predict(dataset, Segment.VALID)
            mlp_acc = _calc_accuracy(pred_mlp, dataset, Segment.VALID)
            print(f"  MLP验证准确率: {mlp_acc:.1f}%")
    except Exception as e:
        print(f"  ⚠️ MLP训练跳过: {e}")

    return {"lgb": lgb_acc, "lasso": lasso_acc, "mlp": mlp_acc}


def _calc_accuracy(
    pred: np.ndarray,
    dataset: AlphaDataset,
    segment: Segment,
) -> float:
    """计算预测方向准确率"""
    df = dataset.fetch_learn(segment)
    label = df["label"].to_numpy()

    # 对齐长度
    min_len = min(len(pred), len(label))
    pred_dir = (pred[:min_len] > 0).astype(int)
    label_dir = (label[:min_len] > 0).astype(int)

    correct = (pred_dir == label_dir).sum()
    total = min_len
    return correct / total * 100 if total > 0 else 0.0


def _calc_accuracy_np(pred: np.ndarray, label: np.ndarray) -> float:
    min_len = min(len(pred), len(label))
    pred_dir = (pred[:min_len] > 0).astype(int)
    label_dir = (label[:min_len] > 0).astype(int)
    correct = (pred_dir == label_dir).sum()
    return correct / min_len * 100 if min_len > 0 else 0.0


# ══════════════════════════════════════════════════════
# Main 管线编排
# ══════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="Alpha研究院管线")
    parser.add_argument("--factors-only", action="store_true", help="仅计算因子+IC")
    parser.add_argument("--train-model", action="store_true", help="训练模型")
    parser.add_argument("--backtest", action="store_true", help="回测")
    parser.add_argument("--all-data", action="store_true", help="使用全部历史数据")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    run_all = not (args.factors_only or args.train_model or args.backtest)

    # 数据加载
    df = load_data(max_symbols=10 if not args.all_data else None)

    # 分割周期: 70%训练, 15%验证, 15%测试
    dates = df["datetime"].unique().sort().to_list()
    n = len(dates)
    train_end = str(dates[int(n * 0.7)].date())
    valid_end = str(dates[int(n * 0.85)].date())
    test_end = str(dates[-1].date())
    start = str(dates[0].date())

    print(f"  训练: {start} ~ {train_end}")
    print(f"  验证: {train_end} ~ {valid_end}")
    print(f"  测试: {valid_end} ~ {test_end}")

    # Alpha158因子计算
    dataset_158 = compute_alpha158(
        df,
        train_period=(start, train_end),
        valid_period=(train_end, valid_end),
        test_period=(valid_end, test_end),
    )

    # Alpha101因子计算（WorldQuant 101个数学表达式因子）
    print(f"\n  Alpha101因子...")
    dataset_101 = Alpha101(
        df,
        train_period=(start, train_end),
        valid_period=(train_end, valid_end),
        test_period=(valid_end, test_end),
    )
    dataset_101.prepare_data()
    print(f"  完成")

    # 合并两套因子
    import polars as pl
    _skip = {'datetime','vt_symbol','label','open','high','low','close','volume','turnover','vwap','open_interest'}
    factors_158 = [c for c in dataset_158.result_df.columns if c not in _skip]
    factors_101 = [c for c in dataset_101.result_df.columns if c not in _skip]
    combined_factors = list(set(factors_158) | set(factors_101))
    print(f"  Alpha158: {len(factors_158)}因子, Alpha101: {len(factors_101)}因子, 合计: {len(combined_factors)}")

    # 用dataset_158为基础,合并Alpha101的因子列
    df_combined = dataset_158.result_df.select(['datetime','vt_symbol'] + factors_158)
    df_101_cols = dataset_101.result_df.select(['datetime','vt_symbol'] + factors_101)
    merged = df_combined.join(df_101_cols, on=['datetime','vt_symbol'], how='outer')

    # 重建dataset用于后续处理(直接用merged dataframe)
    dataset_158.result_df = merged
    # 同步更新learn_df: 合并Alpha158+Alpha101所有因子列
    df_158_learn = dataset_158.result_df.select(['datetime','vt_symbol'] + factors_158)
    df_101_learn = dataset_101.result_df.select(['datetime','vt_symbol'] + factors_101)
    _label = dataset_158.learn_df.select(['datetime','vt_symbol','label'])
    dataset_158.learn_df = _label.join(df_158_learn, on=['datetime','vt_symbol'], how='left')
    dataset_158.learn_df = dataset_158.learn_df.join(df_101_learn, on=['datetime','vt_symbol'], how='left')
    n_factors = dataset_158.learn_df.shape[1] - 3  # minus datetime, vt_symbol, label
    print(f"  learn_df已更新: {n_factors}因子")

    # IC分析
    ic_results = analyze_ic(dataset_158)

    # 保存IC结果
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with open(REPORT_DIR / "alpha158_ic_results.json", "w") as f:
        json.dump(ic_results, f, ensure_ascii=False, indent=2)
    print(f"\n  IC结果已保存: {REPORT_DIR / 'alpha158_ic_results.json'}")

    if args.factors_only or run_all:
        pass  # 继续执行

    if args.train_model or run_all:
        # LassoCV自动特征筛选(从240因子中精选)
        print(f"\n{'='*55}")
        print(f"  LassoCV特征筛选 (240→?)")
        print(f"{'='*55}")
        from sklearn.linear_model import Lasso
        from sklearn.preprocessing import StandardScaler
        train_df = dataset_158.fetch_learn(Segment.TRAIN).to_pandas()
        train_df = train_df.replace([np.inf, -np.inf], 0.0).fillna(0.0).select_dtypes(include=['number'])
        y = train_df['label'].values
        X = StandardScaler().fit_transform(np.nan_to_num(train_df.drop(columns=['label'], errors='ignore').values))
        lasso = Lasso(alpha=0.005, max_iter=2000, random_state=42).fit(X, y)
        n_selected = np.sum(lasso.coef_ != 0)
        print(f"  选中: {n_selected}/{X.shape[1]} 因子")
        print(f"  选中: {n_selected}/{X.shape[1]} 因子")
        selected_indices = np.where(lasso.coef_ != 0)[0]
        factor_names = [c for c in train_df.columns if c not in {'datetime','vt_symbol','label'}]
        selected_factors = [factor_names[i] for i in selected_indices]
        with open(REPORT_DIR / "lasso_selected_factors.json", "w") as f:
            json.dump(selected_factors, f, ensure_ascii=False, indent=2)

        accs = train_models(dataset_158, selected_factors)

        with open(REPORT_DIR / "model_accuracy.json", "w") as f:
            json.dump(accs, f, ensure_ascii=False, indent=2)

    if args.backtest and not run_all:
        print("\n⚠️  backtest模式需要先用完整数据集训练模型")
        print("   请先运行: python run_alpha_pipeline.py --train-model")

    print(f"\n{'='*55}")
    print(f"  Alpha研究院管线完成")
    print(f"  报告: {REPORT_DIR / 'alpha158_ic_results.json'}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
