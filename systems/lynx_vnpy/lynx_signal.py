"""lynx_vnpy 量化信号系统 — 特征工程 + ML 模型 + 推送

用法:
    python lynx_signal.py              # 单次运行
    python lynx_signal.py --schedule   # 定时模式，每日 15:50 自动执行
    python lynx_signal.py --schedule --time 15:30
"""
import argparse
import json
import os
import sys
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler

# 确保项目根目录在路径中（WeComNotifier 所需）
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

warnings.filterwarnings("ignore")

# ===== 配置 =====
STOCK_CODES = ['001390', '300652', '600372', '605368', '000592',
               '603189', '603557', '688202', '601801', '300676']

# 股票名称映射
STOCK_NAMES = {
    '001390': '古麒绒材', '300652': '雷迪克', '600372': '中航机载',
    '605368': '蓝天燃气', '000592': '平潭发展', '603189': '*ST网达',
    '603557': '*ST起步', '688202': '美迪西', '601801': '皖新传媒',
    '300676': '华大基因',
}
WECOM_WEBHOOK = os.getenv("WECOM_WEBHOOK_URL", "")
MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
os.makedirs(MODEL_DIR, exist_ok=True)

# 本地缓存：同一交易时段内避免重复 HTTP 请求
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "cache")
CACHE_TTL_MINUTES = 50
os.makedirs(CACHE_DIR, exist_ok=True)

# ===== 1. 数据获取（Sina finance API） =====
_SESSION = requests.Session()
_SESSION.headers.update({"Referer": "https://finance.sina.com.cn"})

def _prefix(code: str) -> str:
    """判断股票代码所属市场前缀"""
    if code.startswith(('6', '5', '9')):
        return 'sh'
    return 'sz'

def fetch_daily_bars(code: str, days: int = 120, retries: int = 3) -> pd.DataFrame | None:
    """从新浪财经获取个股日K线。自带本地缓存，避免重复请求。"""
    # ── 检查本地缓存 ──
    cache_file = os.path.join(CACHE_DIR, f"lynx_{code}_{days}d.parquet")
    if os.path.exists(cache_file):
        mtime = datetime.fromtimestamp(os.path.getmtime(cache_file))
        now = datetime.now()
        if now - mtime < timedelta(minutes=CACHE_TTL_MINUTES):
            df = pd.read_parquet(cache_file)
            df["股票名称"] = code
            # 收盘后（≥15:00）额外校验：缓存必须包含当天K线
            if now.hour < 15:
                return df  # 盘中：TTL有效即可
            latest_date = str(df['日期'].max())
            today_str = now.strftime('%Y-%m-%d')
            if latest_date >= today_str:
                return df  # 收盘后且已有今天数据：用缓存
            # 否则：收盘了但缓存没有今天K线 → 跳过缓存，重新拉取

    symbol = f"{_prefix(code)}{code}"
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData")
    params = {"symbol": symbol, "scale": 240, "ma": "no", "datalen": days}

    for attempt in range(retries):
        try:
            resp = _SESSION.get(url, params=params, timeout=15)
            resp.encoding = "utf-8"
            data = resp.json()
            if not data:
                if attempt < retries - 1:
                    wait = (attempt + 1) * 3
                    print(f"⏳ 限流, {wait}s后重试({attempt+1}/{retries})", end=" ")
                    time.sleep(wait)
                    continue
                return None
            df = pd.DataFrame(data)
            # 重命名列为中文（兼容现有特征工程）
            df = df.rename(columns={
                "day": "日期", "open": "开盘", "high": "最高",
                "low": "最低", "close": "收盘", "volume": "成交量",
            })
            for col in ["开盘", "最高", "最低", "收盘", "成交量"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.sort_values("日期").reset_index(drop=True)
            df.to_parquet(cache_file)  # 写入本地缓存
            df["股票名称"] = code  # 占位，信号生成时补充
            return df
        except Exception as e:
            if attempt < retries - 1:
                wait = (attempt + 1) * 3
                print(f"⏳ 重试, {wait}s后重试({attempt+1}/{retries})", end=" ")
                time.sleep(wait)
            else:
                print(f"❌ {e}", end=" ")
                return None
    return None

# ===== 2. 特征工程 =====
def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """从日K线计算技术指标特征"""
    closes = df['收盘']
    highs = df['最高']
    lows = df['最低']
    volumes = df['成交量']
    n = len(closes)

    # 价格特征
    df['ret_1d'] = np.append(0, np.diff(closes) / closes[:-1])
    df['ret_5d'] = df['收盘'].pct_change(5)
    df['ret_10d'] = df['收盘'].pct_change(10)
    df['ret_20d'] = df['收盘'].pct_change(20)

    # 均线
    df['ma5'] = df['收盘'].rolling(5).mean()
    df['ma10'] = df['收盘'].rolling(10).mean()
    df['ma20'] = df['收盘'].rolling(20).mean()

    # 均线相对位置
    df['ma5_dist'] = (df['收盘'] - df['ma5']) / df['ma5']
    df['ma20_dist'] = (df['收盘'] - df['ma20']) / df['ma20']
    df['ma_cross'] = (df['ma5'] - df['ma10']) / df['ma10']  # + = 多头

    # RSI
    delta = df['收盘'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df['rsi14'] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = df['收盘'].ewm(span=12).mean()
    ema26 = df['收盘'].ewm(span=26).mean()
    df['macd'] = ema12 - ema26
    df['macd_signal'] = df['macd'].ewm(span=9).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']

    # ATR
    tr = pd.concat([
        highs - lows,
        abs(highs - closes.shift(1)),
        abs(lows - closes.shift(1)),
    ], axis=1).max(axis=1)
    df['atr14'] = tr.rolling(14).mean()
    df['atr_ratio'] = df['atr14'] / df['收盘']  # ATR 占比

    # 布林带
    df['boll_mid'] = df['收盘'].rolling(20).mean()
    df['boll_std'] = df['收盘'].rolling(20).std()
    df['boll_up'] = df['boll_mid'] + 2 * df['boll_std']
    df['boll_down'] = df['boll_mid'] - 2 * df['boll_std']
    df['boll_pos'] = ((df['收盘'] - df['boll_down']) /
                      (df['boll_up'] - df['boll_down']).replace(0, np.nan))

    # CCI
    tp = (highs + lows + closes) / 3
    sma_tp = tp.rolling(20).mean()
    mad = tp.rolling(20).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    df['cci20'] = (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))

    # 成交量特征
    df['vol_ma5'] = pd.Series(volumes).rolling(5).mean()
    df['vol_ratio'] = volumes / df['vol_ma5'].replace(0, np.nan)

    # 目标变量：次日涨
    df['target'] = (df['收盘'].shift(-1) > df['收盘']).astype(int)

    return df

# ===== 3. 模型训练 =====
def train_model(df: pd.DataFrame, stock_code: str) -> tuple | None:
    """训练随机森林模型"""
    FEATURES = [
        'ret_1d', 'ret_5d', 'ret_10d', 'ret_20d',
        'ma5_dist', 'ma20_dist', 'ma_cross',
        'rsi14', 'macd', 'macd_signal', 'macd_hist',
        'atr_ratio', 'boll_pos', 'cci20', 'vol_ratio',
    ]
    data = df[FEATURES + ['target']].dropna()
    if len(data) < 30:
        return None

    X = data[FEATURES].values
    y = data['target'].values

    # 标准化
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # 训练
    model = RandomForestClassifier(
        n_estimators=100, max_depth=5, min_samples_leaf=5,
        random_state=42, class_weight='balanced',
    )
    model.fit(X_scaled, y)

    # 保存
    joblib.dump(model, os.path.join(MODEL_DIR, f"{stock_code}_model.pkl"))
    joblib.dump(scaler, os.path.join(MODEL_DIR, f"{stock_code}_scaler.pkl"))

    return model, scaler

# ===== 4. 信号生成 =====
def predict_signal(df: pd.DataFrame, stock_code: str,
                   name: str) -> dict | None:
    """预测次日信号"""
    FEATURES = [
        'ret_1d', 'ret_5d', 'ret_10d', 'ret_20d',
        'ma5_dist', 'ma20_dist', 'ma_cross',
        'rsi14', 'macd', 'macd_signal', 'macd_hist',
        'atr_ratio', 'boll_pos', 'cci20', 'vol_ratio',
    ]

    model, scaler = None, None
    model_path = os.path.join(MODEL_DIR, f"{stock_code}_model.pkl")
    scaler_path = os.path.join(MODEL_DIR, f"{stock_code}_scaler.pkl")

    if os.path.exists(model_path):
        model = joblib.load(model_path)
        scaler = joblib.load(scaler_path)
    else:
        result = train_model(df, stock_code)
        if result is None:
            return None
        model, scaler = result

    # 取最新一条特征
    row = df[FEATURES].iloc[-1:].dropna()
    if row.empty:
        return None

    X = scaler.transform(row.values)
    prob_up = model.predict_proba(X)[0][1]  # 上涨概率

    # 7级语义对齐: prob_up → L7 连续得分
    score = _l7_score(prob_up)
    label = _l7_label(score)
    emoji = _l7_emoji(score)
    signal = f"{emoji} {label}"

    # 信号强度（保留用于排序）
    if prob_up >= 0.65:
        strength = "强"
    elif prob_up >= 0.35:
        strength = "中"
    else:
        strength = "弱"

    # 获取最新价和涨跌幅
    last = df.iloc[-1]
    close = float(last['收盘'])
    chg = float(last.get('涨跌幅', 0))
    if chg == 0 and len(df) >= 2:
        prev_close = float(df['收盘'].iloc[-2])
        chg = (close - prev_close) / prev_close * 100

    # 关键指标摘要
    latest = df.iloc[-1]
    return {
        "code": stock_code, "name": name,
        "price": close, "change_pct": chg,
        "signal": signal, "strength": strength,
        "l7_score": score,
        "prob_up": round(prob_up * 100, 1),
        "rsi": round(float(latest.get('rsi14', 0)), 1) if not pd.isna(latest.get('rsi14', np.nan)) else None,
        "macd_hist": round(float(latest.get('macd_hist', 0)), 4) if not pd.isna(latest.get('macd_hist', np.nan)) else None,
        "atr_ratio": round(float(latest.get('atr_ratio', 0)) * 100, 2) if not pd.isna(latest.get('atr_ratio', np.nan)) else None,
    }

def _l7_score(prob_up: float) -> float:
    """Map prob_up (0-1) to L7 score (-3~+3) via logit+tanh.
    Same formula as src/normalizer.py normalize_lynx()."""
    import math
    p = max(0.001, min(0.999, prob_up))
    logit = math.log(p / (1 - p))
    return round(3.0 * math.tanh(logit / 2.0), 2)

def _l7_label(score: float) -> str:
    """L7 score → 7-level label."""
    if score >= 2.0:   return "强烈看多"
    if score >= 1.0:   return "看多"
    if score >= 0.33:  return "谨慎看多"
    if score >= -0.33: return "中性/持有"
    if score >= -1.0:  return "谨慎看空"
    if score >= -2.0:  return "看空"
    return "强烈看空"

def _l7_emoji(score: float) -> str:
    """L7 score → color emoji matching fusion system convention."""
    if score >= 1.0:   return "🔴"   # 看多=红
    if score >= 0.33:  return "🟠"   # 谨慎看多=橙
    if score >= -0.33: return "⚪"   # 中性=白
    if score >= -1.0:  return "🟡"   # 谨慎看空=金
    return "🟢"                      # 看空=绿

# ===== 5. 推送 =====
def push_wecom(signals: list[dict]):
    """推送到企业微信（使用统一的 WeComNotifier）。"""
    if not WECOM_WEBHOOK:
        print("⚠️  未配置 WECOM_WEBHOOK_URL，跳过推送")
        return

    # 优先使用统一的 WeComNotifier，fallback 到独立 requests.post
    try:
        from src.wecom_notifier import WeComNotifier
        _send = lambda t: WeComNotifier(WECOM_WEBHOOK, enabled=True).send_markdown(t)
    except ImportError:
        import requests
        _send = lambda t: requests.post(WECOM_WEBHOOK, json={
            "msgtype": "markdown", "markdown": {"content": t},
        }, timeout=10)

    now = datetime.now()
    lines = [f"🧬 {now.strftime('%H:%M')} ly量化信号"]
    for s in signals:
        _sig = s['signal']
        _emoji = _sig.split()[0] if ' ' in _sig else '⚪'
        _label = _sig[len(_emoji):].strip()
        parts = [
            f"{_emoji} **{s['name']}** ¥{s['price']:.2f} {s['change_pct']:+.2f}%",
            f"L7{s['l7_score']:+0.2f}",
            f"置信{s['prob_up']}%",
        ]
        if s.get('rsi') is not None:
            parts.append(f"RSI{s['rsi']}")
        if s.get('macd_hist') is not None:
            arrow = "↗" if s['macd_hist'] > 0 else "↘"
            parts.append(f"MACD{arrow}")
        if s.get('atr_ratio') is not None:
            parts.append(f"ATR{s['atr_ratio']}%")
        parts.append(_label)
        lines.append("｜".join(parts))

    lines.append("\n> 数据源: efinance")
    lines.append("> 模型: RandomForest")
    text = "\n".join(lines)

    result = _send(text)
    ok = False
    if result is not None:
        if isinstance(result, dict):
            ok = result.get("errcode") == 0
        else:
            ok = getattr(result, "status_code", 0) == 200
    print("  ✅ 推送成功" if ok else "  ❌ 推送失败")

# ===== 5.5 Alpha158+LGB路径 =====

def _predict_alpha(df: pd.DataFrame, code: str, name: str) -> dict | None:
    """使用Alpha158+LGB模型预测"""
    try:
        from vnpy_bridge.alpha_predictor import alpha_predict
        prob = alpha_predict(df, code)
    except Exception:
        return None
    if prob is None:
        return None

    prob_pct = round(prob * 100, 1)
    score = _l7_score(prob)
    label = _l7_label(score)
    emoji = _l7_emoji(score)
    signal = f"{emoji} {label}"

    strength = "强" if prob >= 0.65 else ("弱" if prob <= 0.35 else "中")

    # 获取最新价
    closes = df.get("收盘", df.get("close", []))
    price = float(closes.iloc[-1]) if hasattr(closes, 'iloc') else 0
    change = 0.0
    if len(closes) >= 2:
        change = round((closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2] * 100, 2)

    return {
        "code": code, "name": name,
        "prob_up": prob_pct, "signal": signal,
        "l7_score": round(score, 2),
        "price": price, "pct_chg": change,
        "strength": strength,
    }


# ===== 6. 主流程 =====
def run(use_alpha: bool = False):
    """
    主运行函数

    Args:
        use_alpha: 使用Alpha158+LGB替代RF+15TA
    """
    print(f"{'='*55}")
    print(f"🧬  lynx_vnpy ML 量化信号系统")
    print(f"    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if use_alpha:
        print(f"    🔬 模型: Alpha158+LGB (58因子)")
    print(f"{'='*55}")

    all_signals = []
    for code in STOCK_CODES:
        print(f"\n📡  {code}...", end=" ")
        df = fetch_daily_bars(code)
        if df is None:
            print("❌ 无数据")
            time.sleep(2)
            continue

        name = STOCK_NAMES.get(code, code)

        if use_alpha:
            sig = _predict_alpha(df, code, name)
        else:
            df_feat = compute_features(df)
            sig = predict_signal(df_feat, code, name)
        if sig:
            print(f"{sig['signal']} 置信 {sig['prob_up']}%")
            all_signals.append(sig)
        else:
            print("⚠️  信号不足")

    # 排序：L7 得分降序
    all_signals.sort(key=lambda s: s.get('l7_score', 0), reverse=True)

    print(f"\n{'='*55}")
    print(f"📊 共 {len(all_signals)} 只生成信号")
    for s in all_signals:
        print(f"  {s['signal']} {s['name']}({s['code']}) {s['prob_up']}%")
    print(f"{'='*55}")

    # 推送
    if all_signals:
        push_wecom(all_signals)

# ===== 7. 回测 =====

FEATURES_BT = [
    'ret_1d', 'ret_5d', 'ret_10d', 'ret_20d',
    'ma5_dist', 'ma20_dist', 'ma_cross',
    'rsi14', 'macd', 'macd_signal', 'macd_hist',
    'atr_ratio', 'boll_pos', 'cci20', 'vol_ratio',
]

def _bt_predict_at(df: pd.DataFrame, model, scaler, idx: int) -> float | None:
    """在历史位置 idx 处做一次预测（只用 idx 之前的数据）。"""
    if idx < 30:  # 需要至少 30 行数据做特征
        return None
    window = df.iloc[:idx + 1].copy()
    window_feat = compute_features(window)
    row = window_feat[FEATURES_BT].iloc[-1:].dropna()
    if row.empty:
        return None
    try:
        X = scaler.transform(row.values)
        return model.predict_proba(X)[0][1]  # 上涨概率
    except Exception:
        return None


def cmd_backtest() -> int:
    """回测模式：Walk-forward 验证模型预测准确率。

    Walk-forward: 每 RETRAIN_INTERVAL 天用截至当天的数据训练模型，
    然后预测接下来 RETRAIN_INTERVAL 天的方向，逐步滚动。
    消除 in-sample 偏差，得到真实的 out-of-sample 准确率。
    """
    print(f"{'='*55}")
    print(f"🧬  lynx 量化信号 — Walk-Forward 回测")
    print(f"    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*55}")

    RETRAIN_INTERVAL = 20  # 每隔 20 个交易日重新训练一次
    MIN_TRAIN = 60         # 最少 60 个交易日作为初始训练集

    all_results: list[dict] = []
    for code in STOCK_CODES:
        print(f"\n📡  {code} ({STOCK_NAMES.get(code, code)})...")

        df = fetch_daily_bars(code)
        if df is None or len(df) < MIN_TRAIN + RETRAIN_INTERVAL:
            print(f"  ⏭️  数据不足 ({len(df) if df is not None else 0} < {MIN_TRAIN + RETRAIN_INTERVAL})")
            continue

        # 预计算全部特征（rolling 操作只依赖历史数据，无未来信息泄露）
        df_feat = compute_features(df)
        results = []
        train_windows = 0

        # Walk-forward: 用 expanding window 训练，在后续窗口测试
        for train_end in range(MIN_TRAIN, len(df) - 1, RETRAIN_INTERVAL):
            test_start = train_end
            test_end = min(train_end + RETRAIN_INTERVAL, len(df) - 1)

            # 用截至 train_end 的数据训练模型
            train_df = df.iloc[:train_end]
            trained = train_model(train_df, code)
            if trained is None:
                continue
            model, scaler = trained
            train_windows += 1

            # 测试后续 RETRAIN_INTERVAL 天
            for i in range(test_start, test_end):
                prob_up = _bt_predict_at(df_feat, model, scaler, i)
                if prob_up is None:
                    continue

                pred_dir = 1 if prob_up >= 0.5 else -1
                actual_ret = df.iloc[i + 1].get("涨跌幅", 0)
                if actual_ret == 0 and len(df) > i + 2:
                    prev_close = float(df.iloc[i].get("收盘", 0))
                    curr_close = float(df.iloc[i + 1].get("收盘", 0))
                    if prev_close > 0:
                        actual_ret = (curr_close - prev_close) / prev_close * 100
                actual_dir = 1 if actual_ret > 0 else (-1 if actual_ret < 0 else 0)
                correct = 1 if pred_dir == actual_dir else (0 if actual_dir != 0 else None)

                results.append({
                    "code": code, "date": str(df.iloc[i].get("日期", "")),
                    "prob_up": round(prob_up * 100, 1),
                    "pred_dir": pred_dir, "actual_ret": round(actual_ret, 2),
                    "actual_dir": actual_dir, "correct": correct,
                })

        if results:
            total = len(results)
            correct_count = sum(1 for r in results if r["correct"] == 1)
            wrong_count = sum(1 for r in results if r["correct"] == 0)
            eval_count = correct_count + wrong_count
            accuracy = correct_count / eval_count * 100 if eval_count > 0 else 0

            high_conf = [r for r in results if r["prob_up"] >= 65 or r["prob_up"] <= 35]
            high_correct = sum(1 for r in high_conf if r["correct"] == 1)
            high_total = len(high_conf)

            print(f"  准确率 {accuracy:.1f}% ({correct_count}/{eval_count})"
                  f" | 训练窗口 {train_windows} 次"
                  f" | 高置信 {high_correct}/{high_total} ({high_correct/high_total*100:.1f}%"
                  f" )" if high_total > 0 else
                  f"  准确率 {accuracy:.1f}% ({correct_count}/{eval_count})"
                  f" | 训练窗口 {train_windows} 次")

            all_results.append({
                "code": code, "name": STOCK_NAMES.get(code, code),
                "accuracy": accuracy, "total": eval_count, "correct": correct_count,
                "high_conf_correct": high_correct, "high_conf_total": high_total,
                "train_windows": train_windows,
            })
        else:
            print("  无有效回测结果")

    # ── 输出汇总 ──
    if not all_results:
        print("\n❌ 无回测结果")
        return 1

    print(f"\n{'='*55}")
    print(f"📊 Walk-Forward 回测结果汇总 (OOS)")
    print(f"{'='*55}")
    correct_total = sum(r["correct"] for r in all_results)
    total_total = sum(r["total"] for r in all_results)
    overall_acc = correct_total / total_total * 100 if total_total > 0 else 0
    print(f"\n  总体 OOS 准确率: {correct_total}/{total_total} ({overall_acc:.1f}%)")
    print(f"  ({sum(r['train_windows'] for r in all_results)} 次模型训练 / 10 只股票)\n")

    print(f"  个股 OOS 准确率:")
    all_results.sort(key=lambda r: -r["accuracy"])
    for r in all_results:
        bar = "█" * int(r["accuracy"] / 5) + "░" * (20 - int(r["accuracy"] / 5))
        hc_str = f" 高置信: {r['high_conf_correct']}/{r['high_conf_total']} ({r['high_conf_correct']/r['high_conf_total']*100:.1f}%)" if r['high_conf_total'] > 0 else ""
        print(f"    {r['code']} {r['name']:8s}: {r['accuracy']:.1f}% ({r['correct']}/{r['total']}) {bar}")
        if hc_str:
            print(f"      {hc_str}")

    print(f"\n{'='*55}")
    print(f"  回测完成")
    print(f"{'='*55}")
    return 0


def _parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="lynx_vnpy 量化信号系统")
    parser.add_argument("--schedule", action="store_true",
                        help="定时模式，每日指定时间自动执行")
    parser.add_argument("--time", type=str, default="15:50",
                        help="定时执行时间，格式 HH:MM（默认 15:50）")
    parser.add_argument("--backtest", action="store_true",
                        help="回测模式：用历史数据验证模型预测准确率")
    parser.add_argument("--alpha", action="store_true",
                        help="使用Alpha158+LGB模型（替代RF+15TA）")
    return parser.parse_args()


def _schedule_loop(schedule_time: str, use_alpha: bool = False):
    """定时调度模式：工作日每天 schedule_time 执行一次 run()"""
    try:
        import schedule
    except ImportError:
        print("❌ 请安装 schedule 库: pip install schedule")
        return 1

    schedule.every().monday.at(schedule_time).do(run, use_alpha=use_alpha)
    schedule.every().tuesday.at(schedule_time).do(run, use_alpha=use_alpha)
    schedule.every().wednesday.at(schedule_time).do(run, use_alpha=use_alpha)
    schedule.every().thursday.at(schedule_time).do(run, use_alpha=use_alpha)
    schedule.every().friday.at(schedule_time).do(run, use_alpha=use_alpha)

    next_run = schedule.next_run()
    print(f"⏰ lynx 量化信号 — 定时模式已启动")
    print(f"   执行时间: 交易日 {schedule_time}")
    print(f"   下次执行: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   按 Ctrl+C 退出")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        print("\n⏹ 定时模式已退出")
    return 0


if __name__ == "__main__":
    args = _parse_args()
    if args.schedule:
        exit(_schedule_loop(args.time, use_alpha=args.alpha))
    if args.backtest:
        exit(cmd_backtest())
    exit(run(use_alpha=args.alpha))
