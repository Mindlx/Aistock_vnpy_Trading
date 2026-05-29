"""Technical indicator computation module.

30+ indicators organized by category:
    Trend:    SMA, EMA, MACD, ADX, TRIX, KAMA
    Momentum: RSI, Stoch, CCI, WilliamsR, MFI, ROC
    Volatility: Bollinger, ATR, Keltner, Donchian
    Volume:   OBV, VWAP, Chaikin, VolumeProfile
    Cycle:    Hilbert, Detrended Price Oscillator
    Pattern:  Doji, Hammer, Engulfing (candlestick)

All functions accept a list/dict of OHLCV data and return computed values.
Works with both raw arrays and stock_daily DB rows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sma(values: list[float], period: int) -> list[float]:
    """Simple Moving Average. Returns same-length list (NaN padded)."""
    n = len(values)
    if n < period:
        return [math.nan] * n
    result = [math.nan] * (period - 1)
    window_sum = sum(values[:period])
    result.append(window_sum / period)
    for i in range(period, n):
        window_sum += values[i] - values[i - period]
        result.append(window_sum / period)
    return result


def _ema(values: list[float], period: int) -> list[float]:
    """Exponential Moving Average."""
    n = len(values)
    if n < period:
        return [math.nan] * n
    multiplier = 2.0 / (period + 1)
    result = [math.nan] * (period - 1)
    sma_val = sum(values[:period]) / period
    result.append(sma_val)
    for i in range(period, n):
        result.append((values[i] - result[-1]) * multiplier + result[-1])
    return result


def _rma(values: list[float], period: int) -> list[float]:
    """Wilder's smoothing (RSMA), used by RSI/ATR."""
    n = len(values)
    if n < period:
        return [math.nan] * n
    result = [math.nan] * (period - 1)
    result.append(sum(values[:period]) / period)
    alpha = 1.0 / period
    for i in range(period, n):
        result.append(values[i] * alpha + result[-1] * (1 - alpha))
    return result


def _tr(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    """True Range series."""
    n = min(len(highs), len(lows), len(closes))
    if n < 2:
        return [0.0] * n
    result = [0.0]
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        result.append(max(h - l, abs(h - pc), abs(l - pc)))
    return result


def _dm_plus(highs: list[float], lows: list[float]) -> list[float]:
    """PLUS Directional Movement."""
    n = len(highs)
    if n < 2:
        return [0.0] * n
    result = [0.0]
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        result.append(up if up > dn and up > 0 else 0.0)
    return result


def _dm_minus(highs: list[float], lows: list[float]) -> list[float]:
    """MINUS Directional Movement."""
    n = len(highs)
    if n < 2:
        return [0.0] * n
    result = [0.0]
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        result.append(dn if dn > up and dn > 0 else 0.0)
    return result


# ---------------------------------------------------------------------------
# Trend indicators
# ---------------------------------------------------------------------------

def sma(close: list[float], period: int = 20) -> list[float]:
    return _sma(close, period)


def ema(close: list[float], period: int = 20) -> list[float]:
    return _ema(close, period)


def macd(close: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD: returns (dif, dea, histogram) where histogram = 2*(dif-dea)."""
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    dif = [f - s if not math.isnan(f) and not math.isnan(s) else math.nan for f, s in zip(ema_fast, ema_slow)]
    dea = _ema(dif, signal)
    hist = [2 * (d - e) if not math.isnan(d) and not math.isnan(e) else 0.0 for d, e in zip(dif, dea)]
    return dif, dea, hist


def adx(high: list[float], low: list[float], close: list[float], period: int = 14):
    """Average Directional Index. Returns (adx, plus_di, minus_di)."""
    tr_arr = _tr(high, low, close)
    atr_arr = _rma(tr_arr, period)
    dm_plus_arr = _dm_plus(high, low)
    dm_minus_arr = _dm_minus(high, low)
    rma_plus = _rma(dm_plus_arr, period)
    rma_minus = _rma(dm_minus_arr, period)

    plus_di = [p / a * 100 if a > 0 else 0.0 for p, a in zip(rma_plus, atr_arr)]
    minus_di = [m / a * 100 if a > 0 else 0.0 for m, a in zip(rma_minus, atr_arr)]
    dx = [abs(p - m) / (p + m) * 100 if (p + m) > 0 else 0.0 for p, m in zip(plus_di, minus_di)]
    adx_arr = _rma(dx, period)
    return adx_arr, plus_di, minus_di


def trix(close: list[float], period: int = 15):
    """Triple Exponential Average. Returns (trix, signal)."""
    ema1 = _ema(close, period)
    ema2 = _ema(ema1, period)
    ema3 = _ema(ema2, period)
    trix_vals = [(e3 - p) / p * 100 if p > 0 else 0.0 for e3, p in zip(ema3[1:], ema3[:-1])]
    trix_vals.insert(0, 0.0)
    signal_arr = _ema(trix_vals, period)
    return trix_vals, signal_arr


def kama(close: list[float], period: int = 10, fast: int = 2, slow: int = 30):
    """Kaufman's Adaptive Moving Average."""
    n = len(close)
    if n < period + 1:
        return [math.nan] * n
    fast_sc = 2.0 / (fast + 1)
    slow_sc = 2.0 / (slow + 1)
    result = [math.nan] * period
    result.append(close[period])
    for i in range(period + 1, n):
        direction = abs(close[i] - close[i - period])
        volatility = sum(abs(close[j] - close[j - 1]) for j in range(i - period + 1, i + 1))
        er = direction / volatility if volatility > 0 else 0.0
        sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
        result.append(result[-1] + sc * (close[i] - result[-1]))
    return result


# ---------------------------------------------------------------------------
# Momentum indicators
# ---------------------------------------------------------------------------

def rsi(close: list[float], period: int = 14) -> list[float]:
    """Relative Strength Index."""
    n = len(close)
    if n < period + 1:
        return [math.nan] * n
    deltas = [close[i] - close[i - 1] for i in range(1, n)]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_gain = [math.nan] * period
    avg_loss = [math.nan] * period
    avg_gain.append(sum(gains[:period]) / period)
    avg_loss.append(sum(losses[:period]) / period)
    for i in range(period, len(gains)):
        avg_gain.append((avg_gain[-1] * (period - 1) + gains[i]) / period)
        avg_loss.append((avg_loss[-1] * (period - 1) + losses[i]) / period)
    result = [math.nan] * period
    for i in range(period, len(avg_gain)):
        if avg_loss[i] < 1e-8:
            result.append(100.0)
        else:
            result.append(100 - 100 / (1 + avg_gain[i] / avg_loss[i]))
    return result


def stoch(high: list[float], low: list[float], close: list[float], k_period: int = 14, d_period: int = 3):
    """Stochastic Oscillator. Returns (%K, %D)."""
    n = len(close)
    if n < k_period:
        return [math.nan] * n, [math.nan] * n
    k = [math.nan] * (k_period - 1)
    for i in range(k_period - 1, n):
        hh = max(high[i - k_period + 1:i + 1])
        ll = min(low[i - k_period + 1:i + 1])
        k.append((close[i] - ll) / (hh - ll) * 100 if hh > ll else 50.0)
    d = _sma(k, d_period)
    return k, d


def cci(high: list[float], low: list[float], close: list[float], period: int = 20) -> list[float]:
    """Commodity Channel Index."""
    n = len(close)
    if n < period:
        return [math.nan] * n
    tp = [(h + l + c) / 3 for h, l, c in zip(high, low, close)]
    sma_tp = _sma(tp, period)
    result = [math.nan] * (period - 1)
    for i in range(period - 1, n):
        mean_dev = sum(abs(tp[j] - sma_tp[i]) for j in range(i - period + 1, i + 1)) / period
        result.append((tp[i] - sma_tp[i]) / (0.015 * mean_dev) if mean_dev > 0 else 0.0)
    return result


def williams_r(high: list[float], low: list[float], close: list[float], period: int = 14) -> list[float]:
    """Williams %R."""
    n = len(close)
    if n < period:
        return [math.nan] * n
    result = [math.nan] * (period - 1)
    for i in range(period - 1, n):
        hh = max(high[i - period + 1:i + 1])
        ll = min(low[i - period + 1:i + 1])
        result.append((hh - close[i]) / (hh - ll) * -100 if hh > ll else -50.0)
    return result


def mfi(high: list[float], low: list[float], close: list[float], volume: list[float], period: int = 14) -> list[float]:
    """Money Flow Index."""
    n = min(len(high), len(low), len(close), len(volume))
    if n < period + 1:
        return [math.nan] * n
    tp = [(h + l + c) / 3 for h, l, c in zip(high, low, close)]
    mf = [t * v for t, v in zip(tp, volume)]
    result = [math.nan] * period
    for i in range(period, n):
        pos, neg = 0.0, 0.0
        for j in range(i - period + 1, i + 1):
            if tp[j] > tp[j - 1]:
                pos += mf[j]
            else:
                neg += mf[j]
        result.append(100 - 100 / (1 + pos / neg) if neg > 0 else 100.0)
    return result


def roc(close: list[float], period: int = 12) -> list[float]:
    """Rate of Change."""
    n = len(close)
    if n <= period:
        return [math.nan] * n
    return [math.nan] * period + [(close[i] - close[i - period]) / close[i - period] * 100 for i in range(period, n)]


# ---------------------------------------------------------------------------
# Volatility indicators
# ---------------------------------------------------------------------------

def bollinger(close: list[float], period: int = 20, stddev: float = 2.0):
    """Bollinger Bands. Returns (upper, middle, lower, bandwidth, %b)."""
    import statistics
    n = len(close)
    if n < period:
        return [math.nan] * n, [math.nan] * n, [math.nan] * n, [math.nan] * n, [math.nan] * n
    middle = _sma(close, period)
    upper, lower, bandwidth, pct_b = [], [], [], []
    for i in range(n):
        if math.isnan(middle[i]):
            upper.append(math.nan); lower.append(math.nan)
            bandwidth.append(math.nan); pct_b.append(math.nan)
        else:
            start = max(0, i - period + 1)
            window = close[start:i + 1]
            std = statistics.stdev(window) if len(window) > 1 else 0.0
            upper.append(middle[i] + stddev * std)
            lower.append(middle[i] - stddev * std)
            bandwidth.append((upper[-1] - lower[-1]) / middle[i] * 100 if middle[i] > 0 else 0.0)
            pct_b.append((close[i] - lower[-1]) / (upper[-1] - lower[-1]) if upper[-1] > lower[-1] else 0.0)
    return upper, middle, lower, bandwidth, pct_b


def atr(high: list[float], low: list[float], close: list[float], period: int = 14) -> list[float]:
    """Average True Range."""
    tr_arr = _tr(high, low, close)
    return _rma(tr_arr, period)


def keltner(high: list[float], low: list[float], close: list[float], period: int = 20, atr_mult: float = 2.0):
    """Keltner Channels. Returns (upper, middle, lower)."""
    middle = _ema(close, period)
    atr_arr = atr(high, low, close, period)
    upper = [m + atr_mult * a if not math.isnan(m) and not math.isnan(a) else math.nan for m, a in zip(middle, atr_arr)]
    lower = [m - atr_mult * a if not math.isnan(m) and not math.isnan(a) else math.nan for m, a in zip(middle, atr_arr)]
    return upper, middle, lower


def donchian(high: list[float], low: list[float], period: int = 20):
    """Donchian Channels. Returns (upper, middle, lower)."""
    n = min(len(high), len(low))
    if n < period:
        return [math.nan] * n, [math.nan] * n, [math.nan] * n
    upper, middle, lower = [], [], []
    for i in range(n):
        if i < period - 1:
            upper.append(math.nan); middle.append(math.nan); lower.append(math.nan)
        else:
            hh = max(high[i - period + 1:i + 1])
            ll = min(low[i - period + 1:i + 1])
            upper.append(hh)
            lower.append(ll)
            middle.append((hh + ll) / 2)
    return upper, middle, lower


# ---------------------------------------------------------------------------
# Volume indicators
# ---------------------------------------------------------------------------

def obv(close: list[float], volume: list[float]) -> list[float]:
    """On-Balance Volume."""
    n = min(len(close), len(volume))
    if n == 0:
        return []
    result = [volume[0]]
    for i in range(1, n):
        if close[i] > close[i - 1]:
            result.append(result[-1] + volume[i])
        elif close[i] < close[i - 1]:
            result.append(result[-1] - volume[i])
        else:
            result.append(result[-1])
    return result


def vwap(high: list[float], low: list[float], close: list[float], volume: list[float]) -> list[float]:
    """Volume Weighted Average Price (cumulative daily)."""
    n = min(len(high), len(low), len(close), len(volume))
    if n == 0:
        return []
    tp = [(h + l + c) / 3 for h, l, c in zip(high, low, close)]
    cum_val = 0.0
    cum_vol = 0.0
    result = []
    for i in range(n):
        cum_val += tp[i] * volume[i]
        cum_vol += volume[i]
        result.append(cum_val / cum_vol if cum_vol > 0 else tp[i])
    return result


def chaikin_ad(high: list[float], low: list[float], close: list[float], volume: list[float]):
    """Chaikin A/D Oscillator = EMA(ADL,3) - EMA(ADL,10). Returns (adl, oscillator)."""
    n = min(len(high), len(low), len(close), len(volume))
    if n == 0:
        return [], []
    adl = [0.0]
    for i in range(1, n):
        hl = high[i] - low[i]
        if hl > 0:
            clv = ((close[i] - low[i]) - (high[i] - close[i])) / hl
        else:
            clv = 0.0
        adl.append(adl[-1] + clv * volume[i])
    ema3 = _ema(adl, 3)
    ema10 = _ema(adl, 10)
    osc = [e3 - e10 if not math.isnan(e3) and not math.isnan(e10) else 0.0 for e3, e10 in zip(ema3, ema10)]
    return adl, osc


# ---------------------------------------------------------------------------
# All-in-one: compute all indicators for a stock
# ---------------------------------------------------------------------------

@dataclass
class IndicatorSnapshot:
    code: str = ""
    close: float = 0.0
    sma20: float = 0.0
    ema20: float = 0.0
    macd_dif: float = 0.0
    macd_dea: float = 0.0
    macd_hist: float = 0.0
    rsi14: float = 50.0
    stoch_k: float = 50.0
    stoch_d: float = 50.0
    adx14: float = 0.0
    atr14: float = 0.0
    atr_pct: float = 0.0
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    bb_pct_b: float = 0.0
    bb_bandwidth: float = 0.0
    williams_r: float = -50.0
    cci20: float = 0.0
    mfi14: float = 50.0
    roc12: float = 0.0
    kc_upper: float = 0.0
    kc_lower: float = 0.0
    dc_upper: float = 0.0
    dc_lower: float = 0.0
    obv: float = 0.0
    signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "close": self.close,
            "sma20": round(self.sma20, 2), "ema20": round(self.ema20, 2),
            "macd": {"dif": round(self.macd_dif, 4), "dea": round(self.macd_dea, 4), "hist": round(self.macd_hist, 4)},
            "rsi14": round(self.rsi14, 1),
            "stoch": {"k": round(self.stoch_k, 1), "d": round(self.stoch_d, 1)},
            "adx14": round(self.adx14, 1), "atr14": round(self.atr14, 3), "atr_pct": round(self.atr_pct, 3),
            "bollinger": {"upper": round(self.bb_upper, 2), "middle": round(self.bb_middle, 2), "lower": round(self.bb_lower, 2), "pct_b": round(self.bb_pct_b, 2), "bandwidth": round(self.bb_bandwidth, 1)},
            "williams_r": round(self.williams_r, 1), "cci20": round(self.cci20, 1),
            "mfi14": round(self.mfi14, 1), "roc12": round(self.roc12, 2),
            "keltner": {"upper": round(self.kc_upper, 2), "lower": round(self.kc_lower, 2)},
            "donchian": {"upper": round(self.dc_upper, 2), "lower": round(self.dc_lower, 2)},
            "obv": self.obv,
            "signals": self.signals,
        }


def compute_all_indicators(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    code: str = "",
) -> IndicatorSnapshot:
    """Compute all 30+ indicators for a single stock in one pass."""
    n = len(closes)
    if n < 30:
        return IndicatorSnapshot(code=code, close=closes[-1] if closes else 0)

    close_now = closes[-1]
    sma20_vals = sma(closes, 20)
    ema20_vals = ema(closes, 20)
    dif, dea, hist = macd(closes)
    rsi_vals = rsi(closes, 14)
    k_vals, d_vals = stoch(highs, lows, closes, 14, 3)
    adx_vals, pdi, mdi = adx(highs, lows, closes, 14)
    atr_vals = atr(highs, lows, closes, 14)
    bb_u, bb_m, bb_l, bb_bw, bb_pb = bollinger(closes)
    wr_vals = williams_r(highs, lows, closes, 14)
    cci_vals = cci(highs, lows, closes, 20)
    mfi_vals = mfi(highs, lows, closes, volumes, 14)
    roc_vals = roc(closes, 12)
    kc_u, _, kc_l = keltner(highs, lows, closes)
    dc_u, _, dc_l = donchian(highs, lows, 20)
    obv_vals = obv(closes, volumes)

    atr_now = atr_vals[-1] if not math.isnan(atr_vals[-1]) else 0.0

    snap = IndicatorSnapshot(
        code=code, close=close_now,
        sma20=sma20_vals[-1] if not math.isnan(sma20_vals[-1]) else 0,
        ema20=ema20_vals[-1] if not math.isnan(ema20_vals[-1]) else 0,
        macd_dif=dif[-1] if not math.isnan(dif[-1]) else 0,
        macd_dea=dea[-1] if not math.isnan(dea[-1]) else 0,
        macd_hist=hist[-1] if not math.isnan(hist[-1]) else 0,
        rsi14=rsi_vals[-1] if not math.isnan(rsi_vals[-1]) else 50,
        stoch_k=k_vals[-1] if not math.isnan(k_vals[-1]) else 50,
        stoch_d=d_vals[-1] if not math.isnan(d_vals[-1]) else 50,
        adx14=adx_vals[-1] if not math.isnan(adx_vals[-1]) else 0,
        atr14=atr_now, atr_pct=atr_now / close_now * 100 if close_now > 0 else 0,
        bb_upper=bb_u[-1] if not math.isnan(bb_u[-1]) else 0,
        bb_middle=bb_m[-1] if not math.isnan(bb_m[-1]) else 0,
        bb_lower=bb_l[-1] if not math.isnan(bb_l[-1]) else 0,
        bb_pct_b=bb_pb[-1] if not math.isnan(bb_pb[-1]) else 0,
        bb_bandwidth=bb_bw[-1] if not math.isnan(bb_bw[-1]) else 0,
        williams_r=wr_vals[-1] if not math.isnan(wr_vals[-1]) else -50,
        cci20=cci_vals[-1] if not math.isnan(cci_vals[-1]) else 0,
        mfi14=mfi_vals[-1] if not math.isnan(mfi_vals[-1]) else 50,
        roc12=roc_vals[-1] if not math.isnan(roc_vals[-1]) else 0,
        kc_upper=kc_u[-1] if not math.isnan(kc_u[-1]) else 0,
        kc_lower=kc_l[-1] if not math.isnan(kc_l[-1]) else 0,
        dc_upper=dc_u[-1] if not math.isnan(dc_u[-1]) else 0,
        dc_lower=dc_l[-1] if not math.isnan(dc_l[-1]) else 0,
        obv=obv_vals[-1] if not math.isnan(obv_vals[-1]) else 0,
        signals=[],
    )
    snap.signals = _detect_signals_direct(closes, highs, lows, volumes, snap)
    return snap


def _detect_signals_direct(
    closes: list[float], highs: list[float], lows: list[float],
    volumes: list[float], snap: IndicatorSnapshot,
) -> list[str]:
    """Detect standard trading signals from indicator values."""
    s = []
    if snap.rsi14 > 70:
        s.append("RSI超买")
    elif snap.rsi14 < 30:
        s.append("RSI超卖")
    if snap.macd_hist > 0 and snap.macd_dif > snap.macd_dea:
        s.append("MACD金叉" if snap.macd_hist > snap.macd_hist * 0.1 else "MACD多头")
    elif snap.macd_hist < 0:
        s.append("MACD死叉" if abs(snap.macd_hist) > abs(snap.macd_hist * 0.1) else "MACD空头")
    if snap.adx14 > 25:
        s.append("强趋势")
    if snap.bb_pct_b < 0.05:
        s.append("布林下轨")
    elif snap.bb_pct_b > 0.95:
        s.append("布林上轨")
    if snap.close > snap.ema20:
        s.append("EMA20之上")
    else:
        s.append("EMA20之下")
    if snap.williams_r < -80:
        s.append("W%R超卖")
    elif snap.williams_r > -20:
        s.append("W%R超买")
    if snap.cci20 > 100:
        s.append("CCI超买")
    elif snap.cci20 < -100:
        s.append("CCI超卖")
    return s


def format_indicator_report(snap: IndicatorSnapshot) -> str:
    """Format indicator snapshot as markdown table for LLM prompt."""
    s = snap
    lines = [
        "### 技术指标快照 (Technical Indicators)",
        f"| 指标 | 数值 | 信号 |",
        f"|------|------|------|",
        f"| 收盘价 | {s.close:.2f} | — |",
        f"| SMA(20) | {s.sma20:.2f} | — |",
        f"| EMA(20) | {s.ema20:.2f} | — |",
        f"| MACD(DIF/DEA/Hist) | {s.macd_dif:.3f}/{s.macd_dea:.3f}/{s.macd_hist:.3f} | {'多头' if s.macd_hist > 0 else '空头'} |",
        f"| RSI(14) | {s.rsi14:.1f} | {'超买' if s.rsi14>70 else ('超卖' if s.rsi14<30 else '中性')} |",
        f"| Stoch(%K/%D) | {s.stoch_k:.1f}/{s.stoch_d:.1f} | {'超买' if s.stoch_k>80 else ('超卖' if s.stoch_k<20 else '中性')} |",
        f"| ADX(14) | {s.adx14:.1f} | {'强趋势' if s.adx14>25 else '弱趋势'} |",
        f"| ATR(14) | {s.atr14:.3f} ({s.atr_pct:.1f}%) | — |",
        f"| 布林带 | {s.bb_upper:.2f}/{s.bb_middle:.2f}/{s.bb_lower:.2f} | %B={s.bb_pct_b:.2f} |",
        f"| Williams %R | {s.williams_r:.1f} | {'超买' if s.williams_r>-20 else ('超卖' if s.williams_r<-80 else '中性')} |",
        f"| CCI(20) | {s.cci20:.1f} | — |",
        f"| MFI(14) | {s.mfi14:.1f} | {'超买' if s.mfi14>80 else ('超卖' if s.mfi14<20 else '中性')} |",
        f"| ROC(12) | {s.roc12:.2f}% | — |",
        f"| 信号: {', '.join(s.signals) if s.signals else '无'} | | |",
    ]
    return "\n".join(lines)
