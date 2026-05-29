"""lynx_vnpy 量化信号系统 — 特征工程 + ML 模型 + 推送"""
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
WECOM_WEBHOOK = os.getenv("WECOM_WEBHOOK_URL", "")
MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
os.makedirs(MODEL_DIR, exist_ok=True)

# ===== 1. 数据获取（Sina finance API） =====
_SESSION = requests.Session()
_SESSION.headers.update({"Referer": "https://finance.sina.com.cn"})

def _prefix(code: str) -> str:
    """判断股票代码所属市场前缀"""
    if code.startswith(('6', '5', '9')):
        return 'sh'
    return 'sz'

def fetch_daily_bars(code: str, days: int = 120, retries: int = 3) -> pd.DataFrame | None:
    """从新浪财经获取个股日K线"""
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

    # 信号强度
    if prob_up >= 0.65:
        signal = "🟢 买入"
        strength = "强"
    elif prob_up >= 0.55:
        signal = "🟢 关注"
        strength = "中"
    elif prob_up >= 0.45:
        signal = "⚪ 观望"
        strength = "弱"
    elif prob_up >= 0.35:
        signal = "🟡 谨慎"
        strength = "中"
    else:
        signal = "🔴 回避"
        strength = "强"

    # 获取最新价
    last = df.iloc[-1]
    close = float(last['收盘'])
    chg = float(last.get('涨跌幅', 0))

    # 关键指标摘要
    latest = df.iloc[-1]
    return {
        "code": stock_code, "name": name,
        "price": close, "change_pct": chg,
        "signal": signal, "strength": strength,
        "prob_up": round(prob_up * 100, 1),
        "rsi": round(float(latest.get('rsi14', 0)), 1) if not pd.isna(latest.get('rsi14', np.nan)) else None,
        "macd_hist": round(float(latest.get('macd_hist', 0)), 4) if not pd.isna(latest.get('macd_hist', np.nan)) else None,
        "atr_ratio": round(float(latest.get('atr_ratio', 0)) * 100, 2) if not pd.isna(latest.get('atr_ratio', np.nan)) else None,
    }

# ===== 5. 推送 =====
def push_wecom(signals: list[dict]):
    """推送到企业微信"""
    if not WECOM_WEBHOOK:
        print("⚠️  未配置 WECOM_WEBHOOK_URL，跳过推送")
        return

    now = datetime.now()
    lines = [f"🧬 **lynx 量化信号**\n   **{now.strftime('%Y-%m-%d %H:%M:%S')}**\n"]
    for s in signals:
        lines.append(
            f"{s['signal']} {s['name']}({s['code']}) "
            f"¥{s['price']:.2f} {s['change_pct']:+.2f}% "
            f"| 置信 {s['prob_up']}%"
        )
        details = []
        if s.get('rsi') is not None:
            details.append(f"RSI {s['rsi']}")
        if s.get('macd_hist') is not None:
            arrow = "↗" if s['macd_hist'] > 0 else "↘"
            details.append(f"MACD柱 {arrow}{abs(s['macd_hist']):.4f}")
        if s.get('atr_ratio') is not None:
            details.append(f"ATR {s['atr_ratio']}%")
        if details:
            lines.append(f"  {' · '.join(details)}")

    lines.append("\n> 数据源: efinance · 模型: RandomForest")
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

        name = df.iloc[-1].get('股票名称', code)
        df_feat = compute_features(df)

        sig = predict_signal(df_feat, code, name)
        if sig:
            print(f"{sig['signal']} 置信 {sig['prob_up']}%")
            all_signals.append(sig)
        else:
            print("⚠️  信号不足")

    # 排序：买入优先
    order = {"🟢 买入": 0, "🟢 关注": 1, "⚪ 观望": 2, "🟡 谨慎": 3, "🔴 回避": 4}
    all_signals.sort(key=lambda s: order.get(s['signal'], 9))

    print(f"\n{'='*55}")
    print(f"📊 共 {len(all_signals)} 只生成信号")
    for s in all_signals:
        print(f"  {s['signal']} {s['name']}({s['code']}) {s['prob_up']}%")
    print(f"{'='*55}")

    # 推送
    if all_signals:
        push_wecom(all_signals)

if __name__ == "__main__":
    run()
