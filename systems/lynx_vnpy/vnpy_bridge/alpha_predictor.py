#!/usr/bin/env python3
"""
Alpha+LGB 推理模块

用法:
    from vnpy_bridge.alpha_predictor import alpha_predict
    prob_up = alpha_predict(df, code)  # df是pandas DataFrame含close/high/low/open/volume
"""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # vnpy_bridge → lynx_vnpy → systems → .
sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger(__name__)

MODEL_PATH = _PROJECT_ROOT / "systems/lynx_vnpy/models/alpha_lgb_model.txt"


def _sma(arr: np.ndarray, w: int) -> np.ndarray:
    n = len(arr)
    result = np.full(n, np.nan)
    if n < w:
        return result
    cum = np.cumsum(arr)
    result[w - 1:] = (cum[w - 1:] - np.concatenate([[0], cum[:-w]])) / w
    return result


def _std(arr: np.ndarray, w: int) -> np.ndarray:
    n = len(arr)
    result = np.full(n, np.nan)
    if n < w:
        return result
    for i in range(w - 1, n):
        result[i] = np.std(arr[i - w + 1:i + 1])
    return result


def _rmax(arr: np.ndarray, w: int) -> np.ndarray:
    n = len(arr)
    result = np.full(n, np.nan)
    if n < w:
        return result
    for i in range(w - 1, n):
        result[i] = np.max(arr[i - w + 1:i + 1])
    return result


def _rmin(arr: np.ndarray, w: int) -> np.ndarray:
    n = len(arr)
    result = np.full(n, np.nan)
    if n < w:
        return result
    for i in range(w - 1, n):
        result[i] = np.min(arr[i - w + 1:i + 1])
    return result


def _compute_alpha_factors(df: pd.DataFrame) -> pd.DataFrame:
    """计算Alpha158精选因子（纯pandas/numpy实现）"""
    c = df['close'].values.astype(float)
    h = df['high'].values.astype(float)
    l = df['low'].values.astype(float)
    o = df['open'].values.astype(float)
    v = df['volume'].values.astype(float)
    n = len(c)
    eps = 1e-12

    factors = {}

    # K线形态
    factors['kmid'] = (c - o) / (o + eps)
    factors['klen'] = (h - l) / (o + eps)
    factors['kup'] = (h - np.maximum(o, c)) / (o + eps)
    factors['klow'] = (np.minimum(o, c) - l) / (o + eps)
    factors['ksft'] = (2 * c - h - l) / (o + eps)

    # 价格归一化
    factors['open_0'] = o / (c + eps)
    factors['high_0'] = h / (c + eps)
    factors['low_0'] = l / (c + eps)

    # ROC
    for w in [5, 10, 20, 30, 60]:
        values = np.full(n, np.nan)
        if n > w:
            values[w:] = c[w:] / c[:-w] - 1
        factors[f'roc_{w}'] = values

    # MA
    for w in [5, 10, 20, 30, 60]:
        ma = _sma(c, w)
        factors[f'ma_{w}'] = ma / (c + eps)

    # STD
    for w in [5, 10, 20, 30, 60]:
        s = _std(c, w)
        factors[f'std_{w}'] = s / (c + eps)
        vs = _std(v, w)
        factors[f'vstd_{w}'] = vs / (v + eps)

    # MAX/MIN
    for w in [5, 10, 20, 30, 60]:
        factors[f'max_{w}'] = _rmax(h, w) / (c + eps)
        factors[f'min_{w}'] = _rmin(l, w) / (c + eps)

    # RSV
    for w in [5, 10, 20, 30, 60]:
        mn = _rmin(l, w)
        mx = _rmax(h, w)
        factors[f'rsv_{w}'] = (c - mn) / (mx - mn + eps)

    # 涨跌统计
    direction = np.sign(np.diff(c, prepend=c[0]))
    for w in [5, 10, 20, 30, 60]:
        cntp = np.full(n, np.nan)
        cntn = np.full(n, np.nan)
        cntd = np.full(n, np.nan)
        if n >= w:
            for i in range(w - 1, n):
                win = direction[i - w + 1:i + 1]
                pos = np.sum(win > 0)
                neg = np.sum(win < 0)
                cntp[i] = pos / w
                cntn[i] = neg / w
                cntd[i] = (pos - neg) / w
        factors[f'cntp_{w}'] = cntp
        factors[f'cntn_{w}'] = cntn
        factors[f'cntd_{w}'] = cntd

    return pd.DataFrame(factors, index=df.index)


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Robust Z-score (MAD-based, clip to [-3,3])"""
    result = df.copy()
    for col in result.columns:
        vals = result[col].values
        valid = ~(np.isnan(vals) | np.isinf(vals))
        if valid.sum() < 10:
            continue
        med = np.median(vals[valid])
        mad = np.median(np.abs(vals[valid] - med))
        if mad < 1e-8:
            result[col] = 0.0
        else:
            z = (vals - med) / (mad * 1.4826)
            result[col] = np.clip(z, -3, 3)
    return result


def alpha_predict(df: pd.DataFrame, code: str | None = None) -> float | None:
    """使用Alpha158+LGB预测上涨概率"""
    try:
        import lightgbm as lgb

        # 列名兼容（中文/英文）
        pdf = df.copy()
        col_aliases = {
            'close': ['收盘', 'close'], 'high': ['最高', 'high'],
            'low': ['最低', 'low'], 'open': ['开盘', 'open'],
            'volume': ['成交量', 'volume'],
        }
        for target, aliases in col_aliases.items():
            for a in aliases:
                if a in pdf.columns:
                    pdf = pdf.rename(columns={a: target})
                    break

        # 计算因子
        factors = _compute_alpha_factors(pdf)
        factors = _normalize(factors)

        last = factors.iloc[-1:]
        # @calibration fillna(0)替换 — 记录NaN比例以便监控系统性偏差
        nan_ratios = last.isna().mean()
        nan_cols = nan_ratios[nan_ratios > 0]
        if len(nan_cols) > 0:
            col_msg = ", ".join(f"{c}={r:.0%}" for c, r in nan_cols.items())
            logger.debug(f"alpha_predict({code}): fillna(0) 列数={len(nan_cols)}/58, {col_msg}")
        last = last.fillna(0)
        if last.empty:
            return None

        model = lgb.Booster(model_file=str(MODEL_PATH))
        prob = model.predict(last.values)[0]
        return float(np.clip(prob, 0, 1))

    except Exception as e:
        if code:
            logger.warning(f"alpha_predict({code}): {e}")
        return None
