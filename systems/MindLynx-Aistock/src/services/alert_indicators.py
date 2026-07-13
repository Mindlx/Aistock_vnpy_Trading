"""Pure pandas technical indicator calculation functions for alert generation.

All functions operate on a daily kline DataFrame with at minimum columns:
    close, high, low, volume

No TA-Lib dependency — all calculations use pandas rolling/ewm/std.

Each function returns a signal string (or None) suitable for alert worker
integration. Functions are standalone, stateless, and accept a DataFrame
as the first argument.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def calc_ma_cross_signal(
    df: pd.DataFrame,
    fast: int = 5,
    slow: int = 20,
) -> str | None:
    """Detect golden cross / death cross between two moving averages.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain a ``close`` column, or pre-calculated ``ma{fast}`` /
        ``ma{slow}`` columns.
    fast : int
        Fast MA period (default 5).
    slow : int
        Slow MA period (default 20).

    Returns
    -------
    str or None
        ``"golden_cross"`` when the fast MA crosses **above** the slow MA.
        ``"death_cross"`` when the fast MA crosses **below** the slow MA.
        ``None`` otherwise.
    """
    _require_column(df, "close")

    fast_col = f"ma{fast}"
    slow_col = f"ma{slow}"

    if fast_col in df.columns and slow_col in df.columns:
        ma_fast = df[fast_col]
        ma_slow = df[slow_col]
    else:
        ma_fast = df["close"].rolling(window=fast).mean()
        ma_slow = df["close"].rolling(window=slow).mean()

    if len(ma_fast) < 2 or len(ma_slow) < 2:
        return None

    prev_fast = ma_fast.iloc[-2]
    prev_slow = ma_slow.iloc[-2]
    curr_fast = ma_fast.iloc[-1]
    curr_slow = ma_slow.iloc[-1]

    if pd.isna(prev_fast) or pd.isna(prev_slow) or pd.isna(curr_fast) or pd.isna(curr_slow):
        return None

    if prev_fast <= prev_slow and curr_fast > curr_slow:
        return "golden_cross"
    if prev_fast >= prev_slow and curr_fast < curr_slow:
        return "death_cross"

    return None


def calc_rsi_signal(
    df: pd.DataFrame,
    period: int = 14,
    oversold: float = 30.0,
    overbought: float = 70.0,
) -> str | None:
    """Calculate RSI and return overbought/oversold signal.

    Uses Wilder's EMA smoothing (``ewm(alpha=1/period, adjust=False)``).

    Parameters
    ----------
    df : pd.DataFrame
        Must contain a ``close`` column.
    period : int
        Look-back period (default 14).
    oversold : float
        Threshold below which the asset is considered oversold (default 30).
    overbought : float
        Threshold above which the asset is considered overbought (default 70).

    Returns
    -------
    str or None
        ``"oversold"`` when RSI < *oversold*.
        ``"overbought"`` when RSI > *overbought*.
        ``None`` otherwise.
    """
    _require_column(df, "close")

    closes = df["close"]
    if len(closes) < period + 1:
        return None

    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()

    # Where avg_loss ≈ 0 → RS → ∞ → RSI → 100 (all gains, no losses)
    # Where both avg_gain and avg_loss are 0 → RSI = 50 (flat, neutral)
    rs = avg_gain / avg_loss.where(avg_loss > 1e-10, math.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # Fill NaN from zero-loss case with 100 (overbought)
    rsi = rsi.where(avg_loss > 1e-10, 100.0)
    # Fill NaN from leading NaT entries (insufficient data period)
    rsi = rsi.fillna(50.0)

    latest = rsi.iloc[-1]
    if pd.isna(latest):
        return None

    if latest < oversold:
        return "oversold"
    if latest > overbought:
        return "overbought"
    return None


def calc_macd_signal(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> str | None:
    """Calculate MACD and return bullish/bearish/divergence signal.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain a ``close`` column.
    fast : int
        Fast EMA period (default 12).
    slow : int
        Slow EMA period (default 26).
    signal : int
        Signal line EMA period (default 9).

    Returns
    -------
    str or None
        ``"bullish"`` when MACD line crosses **above** the signal line.
        ``"bearish"`` when MACD line crosses **below** the signal line.
        ``"divergence"`` when price and MACD move in opposite directions
        (price makes a higher high while MACD makes a lower high, or
        price makes a lower low while MACD makes a higher low).
        ``None`` otherwise.
    """
    _require_column(df, "close")

    closes = df["close"]
    if len(closes) < max(fast, slow, signal) + 1:
        return None

    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    # --- Cross detection ---
    if len(macd_line) >= 2:
        prev_macd = macd_line.iloc[-2]
        prev_sig = signal_line.iloc[-2]
        curr_macd = macd_line.iloc[-1]
        curr_sig = signal_line.iloc[-1]

        if (
            not pd.isna(prev_macd)
            and not pd.isna(prev_sig)
            and not pd.isna(curr_macd)
            and not pd.isna(curr_sig)
        ):
            if prev_macd <= prev_sig and curr_macd > curr_sig:
                return "bullish"
            if prev_macd >= prev_sig and curr_macd < curr_sig:
                return "bearish"

    # --- Divergence detection ---
    # Compare the most recent two peaks/troughs in price and MACD.
    # Split the last ~slow*2 bars in half, find the max/min of each half.
    lookback = min(slow * 2, len(closes))
    if lookback < 10:
        return None

    mid = lookback // 2
    recent_p = closes.iloc[-lookback:]
    recent_m = macd_line.iloc[-lookback:]

    first_p = recent_p.iloc[:mid]
    second_p = recent_p.iloc[mid:]
    first_m = recent_m.iloc[:mid]
    second_m = recent_m.iloc[mid:]

    if len(first_p) == 0 or len(second_p) == 0:
        return None

    # Collect extreme values using pandas Series, handling NaN gracefully
    fp_max = first_p.max()
    fp_min = first_p.min()
    sp_max = second_p.max()
    sp_min = second_p.min()
    fm_max = first_m.max()
    fm_min = first_m.min()
    sm_max = second_m.max()
    sm_min = second_m.min()

    if pd.isna(fp_max) or pd.isna(fp_min) or pd.isna(sp_max) or pd.isna(sp_min):
        return None
    if pd.isna(fm_max) or pd.isna(fm_min) or pd.isna(sm_max) or pd.isna(sm_min):
        return None

    hp1_val, hp2_val = float(fp_max), float(sp_max)
    lp1_val, lp2_val = float(fp_min), float(sp_min)
    hm1_val, hm2_val = float(fm_max), float(sm_max)
    lm1_val, lm2_val = float(fm_min), float(sm_min)

    # Bearish divergence: price makes higher high, MACD makes lower high
    if hp2_val > hp1_val and hm2_val < hm1_val:
        return "divergence"
    # Bullish divergence: price makes lower low, MACD makes higher low
    if lp2_val < lp1_val and lm2_val > lm1_val:
        return "divergence"

    return None


def calc_kdj_signal(
    df: pd.DataFrame,
    n: int = 9,
    oversold: float = 20.0,
    overbought: float = 80.0,
) -> str | None:
    """Calculate KDJ (Stochastic) indicator and return signal.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain ``close``, ``high``, and ``low`` columns.
    n : int
        Look-back period for RSV (default 9).
    oversold : float
        Oversold threshold for J and K (default 20).
    overbought : float
        Overbought threshold for J and K (default 80).

    Returns
    -------
    str or None
        ``"oversold"`` when J < *oversold* and K < *oversold*.
        ``"overbought"`` when J > *overbought* and K > *overbought*.
        ``"golden_cross"`` when K crosses **above** D.
        ``"death_cross"`` when K crosses **below** D.
        ``None`` otherwise.
    """
    for col in ("close", "high", "low"):
        _require_column(df, col)

    if len(df) < n:
        return None

    low_n = df["low"].rolling(window=n).min()
    high_n = df["high"].rolling(window=n).max()
    denom = high_n - low_n

    rsv = (df["close"] - low_n) / denom.where(denom != 0, math.nan) * 100.0

    k = rsv.ewm(span=3, adjust=False).mean()
    d = k.ewm(span=3, adjust=False).mean()
    j = 3.0 * k - 2.0 * d

    latest_k = k.iloc[-1]
    latest_d = d.iloc[-1]
    latest_j = j.iloc[-1]

    if any(pd.isna(v) for v in (latest_k, latest_d, latest_j)):
        return None

    # K-D cross detection
    if len(k) >= 2 and len(d) >= 2:
        prev_k = k.iloc[-2]
        prev_d = d.iloc[-2]
        if not pd.isna(prev_k) and not pd.isna(prev_d):
            if prev_k <= prev_d and latest_k > latest_d:
                return "golden_cross"
            if prev_k >= prev_d and latest_k < latest_d:
                return "death_cross"

    # Overbought / oversold
    if latest_j < oversold and latest_k < oversold:
        return "oversold"
    if latest_j > overbought and latest_k > overbought:
        return "overbought"

    return None


def calc_cci_signal(
    df: pd.DataFrame,
    period: int = 20,
    oversold: float = -100.0,
    overbought: float = 100.0,
) -> str | None:
    """Calculate Commodity Channel Index and return signal.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain ``close``, ``high``, and ``low`` columns.
    period : int
        Look-back period (default 20).
    oversold : float
        Oversold threshold (default -100).
    overbought : float
        Overbought threshold (default 100).

    Returns
    -------
    str or None
        ``"oversold"`` when CCI < *oversold*.
        ``"overbought"`` when CCI > *overbought*.
        ``None`` otherwise.
    """
    for col in ("close", "high", "low"):
        _require_column(df, col)

    if len(df) < period:
        return None

    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    sma_tp = tp.rolling(window=period).mean()

    # Mean absolute deviation
    mad = tp.rolling(window=period).apply(
        lambda x: float(np.abs(x - x.mean()).mean()), raw=True,
    )

    cci = (tp - sma_tp) / (0.015 * mad.where(mad != 0, math.nan))

    latest = cci.iloc[-1]
    if pd.isna(latest):
        return None

    if latest < oversold:
        return "oversold"
    if latest > overbought:
        return "overbought"
    return None


def calc_bollinger_signal(
    df: pd.DataFrame,
    period: int = 20,
    std_dev: float = 2.0,
) -> str | None:
    """Calculate Bollinger Bands and return touch / squeeze signal.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain a ``close`` column.
    period : int
        SMA period for the middle band (default 20).
    std_dev : float
        Number of standard deviations for the upper/lower bands (default 2).

    Returns
    -------
    str or None
        ``"lower_touch"`` when close touches or breaks below the lower band.
        ``"upper_touch"`` when close touches or breaks above the upper band.
        ``"squeeze"`` when Bandwidth is at its 6-month (approx 120-bar) minimum,
        indicating low volatility / potential breakout.
        ``None`` otherwise.
    """
    _require_column(df, "close")

    if len(df) < period:
        return None

    closes = df["close"]
    middle = closes.rolling(window=period).mean()
    std = closes.rolling(window=period).std(ddof=0)

    upper = middle + std_dev * std
    lower = middle - std_dev * std

    current_close = closes.iloc[-1]
    current_upper = upper.iloc[-1]
    current_lower = lower.iloc[-1]

    if any(pd.isna(v) for v in (current_close, current_upper, current_lower)):
        return None

    # Touch detection
    if current_close >= current_upper:
        return "upper_touch"
    if current_close <= current_lower:
        return "lower_touch"

    # Squeeze detection: Bandwidth at minimum over the lookback window.
    # Use a 120-bar lookback (~6 months of trading days).
    bandwidth = (upper - lower) / middle.where(middle != 0, math.nan)
    squeeze_lookback = min(120, len(bandwidth))

    if squeeze_lookback >= period:
        recent_bw = bandwidth.iloc[-squeeze_lookback:]
        if not recent_bw.isna().all():
            current_bw = bandwidth.iloc[-1]
            min_bw = recent_bw.min()
            if not pd.isna(current_bw) and not pd.isna(min_bw):
                if current_bw <= min_bw * 1.01:  # within 1% of the minimum
                    return "squeeze"

    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_column(df: pd.DataFrame, col: str) -> None:
    """Raise ``ValueError`` if *col* is missing from *df*."""
    if col not in df.columns:
        raise ValueError(
            f"DataFrame must contain a '{col}' column; got columns: {list(df.columns)}"
        )


def compute_market_signal(
    df: pd.DataFrame,
    *,
    indicator: str = "index_change",
    threshold: float = 1.0,
    direction: str = "above",
) -> dict[str, Any] | None:
    """Compute market-level indicator signals for P7 alert rules.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame with at minimum ``close`` column.
    indicator : str
        Indicator name. Supported: ``"index_change"``, ``"ma_cross"``,
        ``"volume_surge"``, ``"volatility"``, ``"breadth"``.
    threshold : float
        Threshold value for the indicator.
    direction : str
        ``"above"`` or ``"below"``.

    Returns
    -------
    dict or None
        ``{"value": ..., "message": ...}`` if triggered, else ``None``.
    """
    _require_column(df, "close")

    if indicator == "index_change":
        if len(df) < 2:
            return None
        prev_close = float(df["close"].iloc[-2])
        curr_close = float(df["close"].iloc[-1])
        if prev_close <= 0:
            return None
        change_pct = (curr_close - prev_close) / prev_close * 100
        triggered = (direction == "above" and change_pct >= threshold) or \
                    (direction == "below" and change_pct <= -threshold)
        if triggered:
            return {
                "value": change_pct,
                "message": f"Index change {change_pct:+.2f}% ({direction} {threshold}%)",
            }
        return None

    if indicator == "ma_cross":
        signal = calc_ma_cross_signal(df)
        if signal:
            return {
                "value": 1.0 if signal == "golden_cross" else -1.0,
                "message": f"MA cross: {signal}",
            }
        return None

    if indicator == "volume_surge":
        if "volume" not in df.columns or len(df) < 20:
            return None
        avg_vol = float(df["volume"].iloc[-20:].mean())
        latest_vol = float(df["volume"].iloc[-1])
        if avg_vol <= 0:
            return None
        ratio = latest_vol / avg_vol
        triggered = (direction == "above" and ratio >= threshold) or \
                    (direction == "below" and ratio <= threshold)
        if triggered:
            return {
                "value": ratio,
                "message": f"Volume surge: {ratio:.1f}x average ({direction} {threshold}x)",
            }
        return None

    if indicator == "volatility":
        if len(df) < 20:
            return None
        returns = df["close"].pct_change().dropna()
        if len(returns) < 20:
            return None
        current_vol = float(returns.iloc[-20:].std()) * 100
        triggered = (direction == "above" and current_vol >= threshold) or \
                    (direction == "below" and current_vol <= threshold)
        if triggered:
            return {
                "value": current_vol,
                "message": f"Volatility {current_vol:.2f}% ({direction} {threshold}%)",
            }
        return None

    if indicator == "breadth":
        # Simplified breadth: close vs SMA(20) percentage
        if len(df) < 20:
            return None
        sma20 = float(df["close"].iloc[-20:].mean())
        curr = float(df["close"].iloc[-1])
        if sma20 <= 0:
            return None
        ratio = (curr / sma20 - 1) * 100
        triggered = (direction == "above" and ratio >= threshold) or \
                    (direction == "below" and ratio <= -threshold)
        if triggered:
            return {
                "value": ratio,
                "message": f"Breadth: price vs SMA20 = {ratio:+.2f}% ({direction} {threshold}%)",
            }
        return None

    return None


def compute_all_signals(df: pd.DataFrame, **kwargs: Any) -> dict[str, str | None]:
    """Run all indicator signal functions and return a dict of results.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame with at minimum ``close``, ``high``, ``low`` columns.
    **kwargs
        Additional keyword arguments forwarded to individual ``calc_*``
        functions where supported.

    Returns
    -------
    dict[str, str | None]
        Keys are short indicator names, values are the signal string or None.
    """
    return {
        "ma_cross": calc_ma_cross_signal(df, **{k: v for k, v in kwargs.items() if k in ("fast", "slow")}),
        "rsi": calc_rsi_signal(df, **{k: v for k, v in kwargs.items() if k in ("period", "oversold", "overbought")}),
        "macd": calc_macd_signal(df, **{k: v for k, v in kwargs.items() if k in ("fast", "slow", "signal")}),
        "kdj": calc_kdj_signal(df, **{k: v for k, v in kwargs.items() if k in ("n", "oversold", "overbought")}),
        "cci": calc_cci_signal(df, **{k: v for k, v in kwargs.items() if k in ("period", "oversold", "overbought")}),
        "bollinger": calc_bollinger_signal(df, **{k: v for k, v in kwargs.items() if k in ("period", "std_dev")}),
    }
