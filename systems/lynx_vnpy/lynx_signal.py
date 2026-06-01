"""lynx_vnpy 量化信号系统 — 特征工程 + ML 模型 + 推送

用法:
    python lynx_signal.py              # 单次运行
    python lynx_signal.py --schedule   # 定时模式，每日 15:50 自动执行
    python lynx_signal.py --schedule --time 15:30
"""
import argparse
import os
import json
import warnings
import numpy as np
import pandas as pd
import requests
import time
from datetime import datetime, timedelta
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
import joblib

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
        strength = "强"

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
    """推送到企业微信"""
    if not WECOM_WEBHOOK:
        print("⚠️  未配置 WECOM_WEBHOOK_URL，跳过推送")
        return

    now = datetime.now()
    lines = [f"🧬 **lynx 量化信号**\n   **{now.strftime('%Y-%m-%d %H:%M:%S')}**\n"]
    for s in signals:
        _sig = s['signal']
        _emoji = _sig.split()[0] if ' ' in _sig else '⚪'
        _label = _sig[len(_emoji):].strip()
        parts = [
            f"{_emoji} {s['name']} ¥{s['price']:.2f} {s['change_pct']:+.2f}%",
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
        lines.append("|".join(parts))

    lines.append("\n> 数据源: efinance")
    lines.append("> 模型: RandomForest")
    text = "\n".join(lines)

    try:
        resp = requests.post(WECOM_WEBHOOK, json={
            "msgtype": "markdown",
            "markdown": {"content": text},
        }, timeout=10)
        print(f"  ✅ 推送结果: {resp.json()}")
    except Exception as e:
        print(f"  ❌ 推送失败: {e}")

# ===== 6. 主流程 =====
def run():
    print(f"{'='*55}")
    print(f"🧬  lynx_vnpy ML 量化信号系统")
    print(f"    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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

def _parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="lynx_vnpy 量化信号系统")
    parser.add_argument("--schedule", action="store_true",
                        help="定时模式，每日指定时间自动执行")
    parser.add_argument("--time", type=str, default="15:50",
                        help="定时执行时间，格式 HH:MM（默认 15:50）")
    return parser.parse_args()


def _schedule_loop(schedule_time: str):
    """定时调度模式：工作日每天 schedule_time 执行一次 run()"""
    try:
        import schedule
    except ImportError:
        print("❌ 请安装 schedule 库: pip install schedule")
        return 1

    schedule.every().monday.at(schedule_time).do(run)
    schedule.every().tuesday.at(schedule_time).do(run)
    schedule.every().wednesday.at(schedule_time).do(run)
    schedule.every().thursday.at(schedule_time).do(run)
    schedule.every().friday.at(schedule_time).do(run)

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
        exit(_schedule_loop(args.time))
    exit(run())
