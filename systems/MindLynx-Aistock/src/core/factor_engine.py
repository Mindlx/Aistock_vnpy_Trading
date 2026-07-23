"""Cross-sectional factor computation engine.

Computes quantitative factors from daily OHLCV data for A-share stocks.
Factors are selected based on highest IC/IR in A-share market research:
- 1-month reversal (IC 4.5%, IR 0.88) — strongest momentum form in A-shares
- Turnover rate (IC 5.0%, IR 1.05) — single strongest factor
- Low volatility (IC 4.2%, IR 0.80)
- Valuation composite (PE percentile, IC 4.2%, IR 0.85)
- Momentum spread (short-term minus mid-term)

All computation is on historical data — no future-looking bias.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Factor definitions
# ---------------------------------------------------------------------------


@dataclass
class FactorDefinition:
    name: str
    category: str  # momentum / sentiment / volatility / valuation / quality
    display_name: str  # Chinese display name for LLM
    higher_better: bool = True  # positive factor → positive signal
    ic: float = 0.0  # monthly IC (information coefficient)
    ir: float = 0.0  # information ratio
    weight: float = 0.0  # composite weight


# ────────── MAD-based winsorization (去极值) ──────────

def winsorize_mad(  # @calibration MAD去极值，合入上游时禁止修改
    values: np.ndarray,
    threshold: float = 5.0,  # @calibration 5 MAD ≈ 3.35σ, 捕获~99.96%数据
) -> np.ndarray:
    """MAD-based winsorization: cap extreme values at median ± threshold × MAD.

    MAD (Median Absolute Deviation) is more robust to outliers than standard
    deviation because the median is unaffected by extreme values.

    For a Gaussian distribution: 5 MAD ≈ 3.35σ, capturing ~99.96% of data.
    threshold=5.0 is conservative — only extreme outliers (>3.35σ) are capped.

    Args:
        values: 1-D array of raw factor values.
        threshold: MAD倍数，越大越宽松（默认5.0 ≈ 3.35σ）

    Returns:
        Winsorized array (same shape).
    """
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad < 1e-8:
        return values
    lower = median - threshold * mad
    upper = median + threshold * mad
    return np.clip(values, lower, upper)


# Core factors targeting A-share strongest signals
# Weights calibrated from 1658-sample Spearman test (2026-05-20):
#   momentum_reversal t=-6.6, momentum_spread t=-6.2 (reversal dominant in A-shares)
#   turnover_sentiment 2026-07-23: 改为对数+横截面, IC从0.050提升至0.084
CORE_FACTORS: list[FactorDefinition] = [
    FactorDefinition(
        name="momentum_reversal",
        category="momentum",
        display_name="1月反转",
        higher_better=False,
        ic=0.045,
        ir=0.88,
        weight=0.35,  # t=-6.6, strongest predictor
    ),
    FactorDefinition(
        name="momentum_spread",
        category="momentum",
        display_name="动量价差(短-中)",
        higher_better=True,
        ic=0.038,
        ir=0.72,
        weight=0.35,  # t=-6.2, second strongest
    ),
    FactorDefinition(
        name="low_volatility",
        category="volatility",
        display_name="低波动",
        higher_better=True,
        ic=0.042,
        ir=0.80,
        weight=0.12,
    ),
    FactorDefinition(
        name="volume_trend",
        category="sentiment",
        display_name="量价配合度",
        higher_better=True,
        ic=0.035,
        ir=0.65,
        weight=0.10,
    ),
    FactorDefinition(
        name="turnover_sentiment",
        category="sentiment",
        display_name="换手率情绪(对数截面)",
        higher_better=False,
        ic=0.084,
        ir=1.20,
        weight=0.08,
    ),
    FactorDefinition(
        name="pattern_chart_elevated",
        category="sentiment",
        display_name="高中间峰双底",
        higher_better=True,
        ic=0.049,
        ir=0.80,
        weight=0.05,
    ),
    # 筹码集中度因子 — 2026-07-23 自研验证 (216只, 22943评估点)
    #   IC=-0.1108 (p<0.0001), 最优切分8%, 集中vs分散差+5.61%
    #   逻辑: 筹码越集中(conc越低) → 未来收益越高
    FactorDefinition(
        name="concentration",
        category="quality",
        display_name="筹码集中度(90%)",
        higher_better=False,  # lower conc = more concentrated = better
        ic=0.111,
        ir=1.20,
        weight=0.10,
    ),
    # --- Augmented factors (Phase B) ---
    FactorDefinition(
        name="price_position",
        category="momentum",
        display_name="价格位置(60d)",
        higher_better=False,
        ic=0.032,
        weight=0.03,
    ),
    FactorDefinition(
        name="volume_acceleration",
        category="sentiment",
        display_name="量能加速度",
        higher_better=True,
        ic=0.028,
        weight=0.03,
    ),
    FactorDefinition(
        name="consecutive_direction",
        category="momentum",
        display_name="连涨连跌偏向",
        higher_better=True,
        ic=0.025,
        weight=0.02,
    ),
    FactorDefinition(
        name="volatility_ratio",
        category="volatility",
        display_name="波动率比率(短/长)",
        higher_better=False,
        ic=0.022,
        weight=0.02,
    ),
    FactorDefinition(
        name="size_factor",
        category="quality",
        display_name="规模因子(小盘溢价)",
        higher_better=True,
        ic=0.038,
        weight=0.04,
    ),
    FactorDefinition(
        name="illiquidity",
        category="quality",
        display_name="非流动性(Amihud)",
        higher_better=True,
        ic=0.041,
        weight=0.04,
    ),
    FactorDefinition(
        name="max_effect",
        category="sentiment",
        display_name="极端收益(MAX)",
        higher_better=True,   # 5.1 fix: raw=-max_ret already negated; True→sign+1→neg z → neg contribution
        ic=0.035,
        weight=0.04,
    ),
]


@dataclass
class FactorResult:
    """Per-stock factor computation result."""

    code: str
    raw_factors: dict[str, float]  # raw factor values
    z_scores: dict[str, float]  # cross-sectional z-scores (-3 to +3)
    composite_score: float  # weighted composite
    composite_label: str  # "强势" / "中性" / "弱势"
    details: dict[str, dict] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Factor computation functions
# ---------------------------------------------------------------------------


def _compute_momentum_reversal(df: np.ndarray, window: int = 21) -> float:
    """1-month reversal: negative recent return predicts positive future.
    A-share: short-term reversal is strong (IC 4.5%).
    Returns raw value (more negative = stronger reversal signal).
    """
    if len(df) < window:
        return 0.0
    recent = df[-window:]
    return (recent[0] - recent[-1]) / recent[0]  # return over window, sign flipped later


def _compute_turnover_sentiment(turnover: np.ndarray, window: int = 20) -> tuple[float, float]:
    """换手率情绪: 高换手率 = 情绪过热 = 看空。

    行业标杆做法 (IC=-0.084, n=43545):
      1. 取 log(换手率%) 使分布正态化
      2. 横截面排名 (跨股票比较)
      3. 行业/市值中性化 (可选, 本实现简化)

    Returns (raw_value, log_turnover).
    raw_value 为 log 换手率, 由 normalizer 做横截面标准化。
    """
    if len(turnover) < 1:
        return 0.0, 0.0
    recent = float(turnover[-1])
    if recent <= 0:
        return 0.0, 0.0
    log_turn = float(np.log(max(recent, 0.01)))
    return log_turn, log_turn


def _compute_low_volatility(close: np.ndarray, window: int = 20) -> tuple[float, float]:
    """Low volatility premium: lower volatility stocks outperform in A-shares.
    Returns (raw_value, annualized_vol).
    """
    if len(close) < window:
        return 0.0, 0.0
    returns = np.diff(close[-window:]) / close[-window:-1]
    vol = np.std(returns) * np.sqrt(252) if len(returns) > 0 else 0.0
    return vol, vol


def _compute_momentum_spread(close: np.ndarray, short: int = 5, mid: int = 20) -> float:
    """Short-term minus mid-term momentum: positive = accelerating.
    Returns raw value.
    """
    if len(close) < mid:
        return 0.0
    short_ret = (close[-1] - close[-short]) / close[-short] if short < len(close) and close[-short] != 0 else 0.0
    mid_ret = (close[-1] - close[-mid]) / close[-mid] if mid < len(close) and close[-mid] != 0 else 0.0
    return short_ret - mid_ret


def _compute_volume_trend(close: np.ndarray, volume: np.ndarray, window: int = 10) -> float:
    """Volume-price alignment: positive correlation = healthy trend.
    Returns raw correlation value (-1 to 1).
    """
    if len(close) < window + 1 or len(volume) < window + 1:
        return 0.0
    pct_close = np.diff(close[-window - 1 :]) / close[-window - 1 : -1]
    pct_vol = np.diff(volume[-window - 1 :])
    if len(pct_close) < 2 or np.std(pct_close) == 0 or np.std(pct_vol) == 0:
        return 0.0
    corr = np.corrcoef(pct_close, pct_vol)[0, 1]
    return float(corr) if not np.isnan(corr) else 0.0


def _compute_rsi_divergence(close: np.ndarray, period: int = 14, lookback: int = 5) -> float:
    """RSI momentum: RSI change over the last `lookback` days.
    Positive = RSI rising (bullish momentum), Negative = RSI falling.
    """
    n = len(close)
    if n < period + lookback + 1:
        return 0.0

    def _rsi_at(idx: int) -> float:
        deltas = np.diff(close[idx - period : idx + 1])
        gains = np.maximum(deltas, 0)
        losses = np.maximum(-deltas, 0)
        avg_gain = np.mean(gains) if len(gains) > 0 else 0
        avg_loss = np.mean(losses) if len(losses) > 0 else 1e-8
        return 100 - 100 / (1 + avg_gain / avg_loss) if avg_loss > 0 else 100

    now_rsi = _rsi_at(-1)
    past_rsi = _rsi_at(-1 - lookback)
    return (now_rsi - past_rsi) / 100  # normalize


def _compute_price_position(close: np.ndarray, window: int = 60) -> float:
    """Price position within its N-day range: 0=bottom, 1=top."""
    if len(close) < window:
        return 0.5
    recent = close[-window:]
    low, high = np.min(recent), np.max(recent)
    if high - low < 1e-8:
        return 0.5
    return (close[-1] - low) / (high - low)


def _compute_volume_acceleration(volume: np.ndarray, short: int = 5, mid: int = 20) -> float:
    """Volume acceleration: short-term avg vol / mid-term avg vol."""
    if len(volume) < mid:
        return 1.0
    short_avg = np.mean(volume[-short:])
    mid_avg = np.mean(volume[-mid:])
    return float(short_avg / mid_avg) if mid_avg > 0 else 1.0


def _compute_consecutive_direction(close: np.ndarray, days: int = 10) -> float:
    """Net consecutive direction bias: (%up - %down) over N days."""
    if len(close) < days + 1:
        return 0.0
    ups = sum(1 for i in range(-days, 0) if close[i] > close[i - 1])
    downs = sum(1 for i in range(-days, 0) if close[i] < close[i - 1])
    total = ups + downs
    return (ups - downs) / total if total > 0 else 0.0


def _compute_volatility_ratio(close: np.ndarray, short: int = 5, long: int = 20) -> float:
    """Volatility ratio: short-term std / long-term std. High = regime change."""
    if len(close) < long + 1:
        return 1.0
    rets_short = np.diff(close[-short - 1 :]) / close[-short - 1 : -1]
    rets_long = np.diff(close[-long - 1 :]) / close[-long - 1 : -1]
    std_short = float(np.std(rets_short))
    std_long = float(np.std(rets_long))
    if std_long < 1e-8:
        return 1.0
    ratio = std_short / std_long
    return float(ratio) if not np.isnan(ratio) else 1.0


def _compute_size_factor(close: np.ndarray, volume: np.ndarray, window: int = 20) -> float:
    """Size premium proxy: avg daily turnover (volume × price) in yuan.
    Lower = smaller market cap → higher expected return in A-shares.
    Returns negative log turnover so smaller stocks score higher.
    """
    if len(close) < window or len(volume) < window:
        return 0.0
    avg_amount = float(np.mean(volume[-window:] * close[-window:]))
    if avg_amount <= 0:
        return 0.0
    return -np.log1p(avg_amount / 1e8)


def _compute_illiquidity(close: np.ndarray, volume: np.ndarray, window: int = 20) -> float:
    """Amihud illiquidity: avg(|daily return| / dollar volume).
    Higher values = more illiquid = liquidity premium (higher expected return).
    Clipped to 100.0 to bound extreme values.
    """
    if len(close) < window + 1 or len(volume) < window + 1:
        return 0.0
    rets = np.abs(np.diff(close[-window - 1 :]) / close[-window - 1 : -1])
    amounts = volume[-window:] * close[-window:]
    valid = amounts > 0
    if not valid.any():
        return 0.0
    illiq = float(np.mean(rets[valid] / (amounts[valid] / 1e8)))
    return float(min(illiq, 100.0))


def _compute_max_effect(close: np.ndarray, window: int = 20) -> float:
    """MAX effect: max daily return in past month.
    Retail investors chase stocks with extreme recent gains (lottery preference),
    which subsequently underperform. Returns negative so high MAX = bearish.
    """
    if len(close) < window + 1:
        return 0.0
    rets = np.diff(close[-window - 1 :]) / close[-window - 1 : -1]
    max_ret = float(np.max(rets)) if len(rets) > 0 else 0.0
    return -max_ret


def _compute_concentration(close: np.ndarray, volume: np.ndarray | None = None,
                           conc: np.ndarray | None = None) -> float:
    """筹码集中度: 90%筹码分布集中度值, 取自 chip_distribution 表的最新数据.

    因子逻辑: lower conc = more concentrated = higher_better=False.
    最优切分点 8% (自研验证, 22943点, p<0.0001).
    数据由调用方通过 df_daily["concentration"] 传入.
    若无数据, 返回 0.5 (中性, 对应约 20-25% 的中等集中度).
    """
    if conc is None or len(conc) < 1:
        return 0.5
    val = float(conc[-1])
    if val <= 0 or val > 1:
        return 0.5
    return val


def _compute_pattern_chart_elevated(
    close: np.ndarray, high: np.ndarray, low: np.ndarray
) -> float:
    """Detect elevated-middle-peak double bottom pattern.

    回测验证 (216只, 250信号):
        Elevated variant IC=+0.049 (p<0.0001, Spearman vs 20d forward return).
        R² vs 12 existing factors = 0.0006 — independent information.

    Returns:
        1.0 if elevated middle peak double bottom detected, else 0.0.
    """
    n = len(close)
    req = 80
    if n < req:
        return 0.0

    recent_lows = sorted(range(n), key=lambda i: low[i])[:5]
    if len(recent_lows) < 2:
        return 0.0
    lo1, lo2 = sorted(recent_lows[:2])
    if lo2 - lo1 < 5:
        return 0.0
    if abs(low[lo1] - low[lo2]) / max(low[lo1], low[lo2], 1e-8) >= 0.03:
        return 0.0
    mid_high = float(np.max(high[lo1:lo2 + 1]))
    if mid_high <= low[lo1] * 1.03:
        return 0.0

    left_shoulder = float(np.max(high[:lo1 + 1])) if lo1 >= 2 else high[0]
    is_elevated = mid_high > left_shoulder * 1.01
    return 1.0 if is_elevated else 0.0


# 因子解读 — 注入 LLM prompt 时附带简要解释
FACTOR_INTERPRETATIONS: dict[str, str] = {
    "momentum_reversal": "1月反转效应(Jegadeesh), 近期跌幅大→反弹概率高",
    "momentum_spread": "短期均线-中期均线价差, 正=短期更强→动量延续",
    "low_volatility": "低波动异象(Ang), 低波动股票长期跑赢高波动",
    "volume_trend": "量价相关性, 正=量价齐升(健康上涨), 负=量价背离",
    "turnover_sentiment": "横截面对数换手率, 高换手=情绪过热=偏空(Grinold)",
    "pattern_chart_elevated": "高中间峰双底(Bulkowski), 84.4%胜率, 极强反转信号",
    "concentration": "90%筹码集中度, <8%=高度集中(偏多), >18%=分散(偏空)",
    "price_position": "价格在60日区间位置, 高位=接近压力, 低位=接近支撑",
    "volume_acceleration": "量能加速, 持续放量=趋势确认, 缩量=动能衰减",
    "consecutive_direction": "连涨/连跌偏向, 极端值意味短期超买/超卖",
    "volatility_ratio": "短期/长期波动率比, 短波动骤升=变盘信号",
    "size_factor": "小盘溢价(Banz), 小市值股票长期跑赢大市值",
    "illiquidity": "非流动性溢价(Amihud), 流动性差=预期收益补偿",
    "max_effect": "极端收益(MAX效应), 近期暴涨过=彩票效应=后续反转",
}


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------

_FACTOR_COMPUTE: dict[str, Callable] = {
    "momentum_reversal": _compute_momentum_reversal,
    "momentum_spread": _compute_momentum_spread,
    "low_volatility": _compute_low_volatility,
    "turnover_sentiment": _compute_turnover_sentiment,
    "volume_trend": _compute_volume_trend,
    "price_position": _compute_price_position,
    "volume_acceleration": _compute_volume_acceleration,
    "consecutive_direction": _compute_consecutive_direction,
    "volatility_ratio": _compute_volatility_ratio,
    "size_factor": _compute_size_factor,
    "illiquidity": _compute_illiquidity,
    "max_effect": _compute_max_effect,
    "pattern_chart_elevated": _compute_pattern_chart_elevated,
    "concentration": _compute_concentration,
}


class FactorEngine:
    """Compute factors across a universe of stocks."""

    def __init__(self, factors: list[FactorDefinition] | None = None):
        self.factors = list(factors) if factors else list(CORE_FACTORS)  # defensive copy

    def apply_regime_weights(self, weight_map: dict[str, float]) -> list[str]:
        """Replace factor weights with regime-conditional weights.

        Creates new FactorDefinition instances with updated weights.
        Does NOT mutate CORE_FACTORS -- self.factors is replaced wholesale.

        Args:
            weight_map: {factor_name: weight} from regime_factor_weights.

        Returns:
            List of factor names whose weights were updated.
        """
        updated: list[str] = []
        new_factors: list[FactorDefinition] = []
        for fd in self.factors:
            new_weight = weight_map.get(fd.name)
            if new_weight is not None and abs(new_weight - fd.weight) > 0.001:
                updated.append(fd.name)
            from dataclasses import replace as _replace
            new_fd = _replace(
                fd,
                weight=new_weight if new_weight is not None else fd.weight,
            )
            new_factors.append(new_fd)
        self.factors = new_factors
        return updated

    def compute_for_stock(self, code: str, df_daily: list[dict]) -> FactorResult:
        """Compute all factors for a single stock.

        Args:
            code: stock code
            df_daily: list of daily bars sorted by date ascending,
                      each dict with keys: close, volume, high, low, pct_chg

        Returns:
            FactorResult with raw values and z_scores (placeholder until
            cross_sectional_normalize is called).
        """
        close_arr = np.array([d["close"] for d in df_daily], dtype=float)
        volume_arr = np.array([d["volume"] for d in df_daily], dtype=float)
        high_arr = np.array([d["high"] for d in df_daily], dtype=float)
        low_arr = np.array([d["low"] for d in df_daily], dtype=float)
        conc_arr = np.array([float(d.get("concentration", 0) or 0) for d in df_daily], dtype=float)
        turnover_arr = np.array([float(d.get("turnover", 0) or 0) for d in df_daily], dtype=float)

        # 输入数据验证：拒绝坏数据，避免静默返回 0.0 污染复合得分
        _issues = []
        if len(close_arr) < 20:
            _issues.append(f"数据不足(N={len(close_arr)}<20)")
        if np.any(np.isnan(close_arr)):
            _issues.append("close数组含NaN")
        if np.any(close_arr <= 0):
            _issues.append("close数组含≤0值")
        if np.any(np.isnan(volume_arr)):
            _issues.append("volume数组含NaN")
        if _issues:
            import logging
            _log = logging.getLogger(__name__)
            _log.warning("[FactorEngine] %s 输入数据异常: %s — 因子将返回中性值", code, ", ".join(_issues))

        raw: dict[str, float] = {}
        details: dict[str, dict] = {}

        for fd in self.factors:
            fn = _FACTOR_COMPUTE.get(fd.name)
            if fn is None:
                raw[fd.name] = 0.0
                continue

            if fd.name == "low_volatility":
                val, vol = fn(close_arr)
                raw[fd.name] = -val  # negate: lower vol → higher signal
                details[fd.name] = {"annualized_vol": round(vol, 4)}
            elif fd.name == "turnover_sentiment":
                log_turn, _ = fn(turnover_arr)
                raw[fd.name] = log_turn  # high log_turn → bearish (sentiment overheating)
                details[fd.name] = {"log_turnover": round(log_turn, 3)}
            elif fd.name == "momentum_reversal":
                val = fn(close_arr)
                raw[fd.name] = val  # positive = reversal opportunity (keep as-is)
                details[fd.name] = {"return_21d": round(val * 100, 2)}
            elif fd.name == "momentum_spread":
                val = fn(close_arr)
                raw[fd.name] = val
                details[fd.name] = {"spread_pct": round(val * 100, 2)}
            elif fd.name == "volume_trend":
                val = fn(close_arr, volume_arr)
                raw[fd.name] = val
                details[fd.name] = {"correlation": round(val, 3)}
            elif fd.name == "price_position":
                val = fn(close_arr)
                raw[fd.name] = val  # high position → near peak → fade (reversal)
                details[fd.name] = {"position": round(val, 3)}
            elif fd.name == "volume_acceleration":
                val = fn(volume_arr)
                raw[fd.name] = val
                details[fd.name] = {"accel_ratio": round(val, 3)}
            elif fd.name == "consecutive_direction":
                val = fn(close_arr)
                raw[fd.name] = val
                details[fd.name] = {"net_bias": round(val, 3)}
            elif fd.name == "volatility_ratio":
                val = fn(close_arr)
                raw[fd.name] = val  # high ratio → vol spiking → bearish (A股低波异常)
                details[fd.name] = {"vol_ratio": round(val, 3)}
            elif fd.name == "size_factor":
                val = fn(close_arr, volume_arr)
                raw[fd.name] = val
                details[fd.name] = {"log_turnover": round(val, 3)}
            elif fd.name == "illiquidity":
                val = fn(close_arr, volume_arr)
                raw[fd.name] = val
                details[fd.name] = {"amihud": round(val, 4)}
            elif fd.name == "max_effect":
                val = fn(close_arr)
                raw[fd.name] = val
                details[fd.name] = {"max_ret": round(-val * 100, 2)}
            elif fd.name == "pattern_chart_elevated":
                val = fn(close_arr, high_arr, low_arr)
                raw[fd.name] = val
                details[fd.name] = {"elevated": bool(val)}
            elif fd.name == "concentration":
                val = fn(close_arr, volume_arr, conc_arr)
                raw[fd.name] = val  # lower = more concentrated = better (higher_better=False)
                details[fd.name] = {"conc_90": round(val, 4)}
            else:
                raw[fd.name] = 0.0

        return FactorResult(
            code=code,
            raw_factors=raw,
            z_scores={},  # filled by cross_sectional_normalize
            composite_score=0.0,
            composite_label="pending",
            details=details,
        )

    def _min_data_for_factor(self, name: str) -> int:
        """Minimum days of data needed to compute a valid factor value."""
        min_req = {
            "momentum_reversal": 21,
            "momentum_spread": 20,
            "low_volatility": 21,
            "turnover_sentiment": 21,
            "volume_trend": 11,
            "price_position": 60,
            "volume_acceleration": 21,
            "consecutive_direction": 11,
            "volatility_ratio": 21,
            "size_factor": 21,
            "illiquidity": 21,
            "max_effect": 21,
            "pattern_chart_elevated": 80,
            "concentration": 1,
        }
        return min_req.get(name, 20)

    def time_series_normalize(
        self,
        results: list[FactorResult],
        stock_data: dict[str, dict[str, np.ndarray]],
        *,
        lookback: int = 120,
        min_samples: int = 30,
    ) -> None:
        """Normalize each stock's factors against its own historical distribution.

        For each factor, computes the factor value over overlapping windows
        of the stock's historical data, then z-scores the current value
        against that distribution. This is robust for any universe size (N>=1).

        Args:
            results: List of FactorResult from compute_for_stock()
            stock_data: Dict mapping code → {"close": np.array, "volume": np.array}
            lookback: Max days of history to use for the distribution window
            min_samples: Minimum historical samples required for a valid z-score

        Mutates each FactorResult in-place with z_scores, composite_score, label.
        """
        fn_map = _FACTOR_COMPUTE

        for result in results:
            code = result.code
            sd = stock_data.get(code)
            if sd is None:
                continue
            close = sd.get("close")
            volume = sd.get("volume")
            if close is None or len(close) < lookback // 2:
                continue

            # Cap lookback to available data
            n_hist = min(lookback, len(close))
            close_hist = close[-n_hist:]
            volume_hist = volume[-n_hist:] if volume is not None else None

            for fd in self.factors:
                fn = fn_map.get(fd.name)
                if fn is None:
                    continue

                min_req = self._min_data_for_factor(fd.name)
                if len(close_hist) < min_req + min_samples // 2:
                    continue

                # Compute historical factor values via sliding window
                hist_vals = []
                # 二值/截面因子不参与时序归一化: 由截面归一化处理
                if fd.name in ("pattern_chart_elevated", "concentration", "turnover_sentiment"):
                    continue

                for i in range(min_req, len(close_hist)):
                    slice_close = close_hist[: i + 1]
                    slice_volume = volume_hist[: i + 1] if volume_hist is not None else None

                    if fd.name == "low_volatility":
                        val, _ = fn(slice_close)
                    elif fd.name == "turnover_sentiment":
                        val, _ = fn(slice_volume)
                    elif fd.name == "volume_acceleration":
                        val = fn(slice_volume)
                    else:
                        val = fn(slice_close, slice_volume) if fd.name in ("volume_trend", "size_factor", "illiquidity") else fn(slice_close)

                    # Apply same sign logic as compute_for_stock
                    if fd.name == "low_volatility":
                        val = -val

                    hist_vals.append(val)

                if len(hist_vals) < min_samples:
                    continue

                # 时序标准化前做MAD去极值
                hist_vals = winsorize_mad(np.array(hist_vals)).tolist()
                mean = float(np.mean(hist_vals))
                std = float(np.std(hist_vals))
                if std < 1e-8:
                    result.z_scores[fd.name] = 0.0
                    continue

                current_raw = result.raw_factors.get(fd.name, 0.0)
                z = (current_raw - mean) / std
                z = max(-3.0, min(3.0, z))
                result.z_scores[fd.name] = round(z, 3)
                if fd.name not in result.details:
                    result.details[fd.name] = {}
                result.details[fd.name]["ts_mean"] = round(mean, 4)
                result.details[fd.name]["ts_std"] = round(std, 4)
                result.details[fd.name]["ts_n"] = len(hist_vals)

        # Fill missing z-scores with 0.0;
        # 免时序归一化的因子直接映射:
        #   pattern_chart_elevated: raw(0/1) → z(0/2.0)
        #   concentration: raw(0~1) → z(线性映射, 8%最优切分)
        #   turnover_sentiment: raw(log_turn) → z(对数映射, 2%为中性)
        n_filled = 0
        for result in results:
            for fd in self.factors:
                if fd.name not in result.z_scores:
                    if fd.name == "pattern_chart_elevated":
                        raw = result.raw_factors.get(fd.name, 0.0)
                        result.z_scores[fd.name] = raw * 2.0
                    elif fd.name == "concentration":
                        raw = result.raw_factors.get(fd.name, 0.5)
                        z = 2.0 * (0.15 - raw) / 0.15 if raw < 0.15 else -2.0 * (raw - 0.15) / 0.15
                        result.z_scores[fd.name] = round(max(-2.0, min(2.0, z)), 3)
                    elif fd.name == "turnover_sentiment":
                        raw = result.raw_factors.get(fd.name, 0.7)
                        # log(2%)=0.7为中心, 每±1.0对应±1个z
                        z = (0.7 - raw)  # 高换手→负z→higher_better=False→看空
                        result.z_scores[fd.name] = round(max(-3.0, min(3.0, z)), 3)
                    else:
                        result.z_scores[fd.name] = 0.0
                        n_filled += 1
        if n_filled > 0:
            logger.warning(
                "[FactorEngine] %d factor-stock pairs filled with 0.0 (insufficient data —"
                " check stock_daily completeness)", n_filled
            )

        # Compute composite scores (same logic as cross_sectional_normalize)
        for r in results:
            composite = 0.0
            total_weight = 0.0
            for fd in self.factors:
                z = r.z_scores.get(fd.name, 0.0)
                sign = 1.0 if fd.higher_better else -1.0
                composite += sign * z * fd.weight
                total_weight += fd.weight
            if total_weight > 0:
                composite /= total_weight
            r.composite_score = round(composite, 3)

            if composite > 0.5:
                r.composite_label = "强势"
            elif composite < -0.5:
                r.composite_label = "弱势"
            else:
                r.composite_label = "中性"

    def cross_sectional_normalize(self, results: list[FactorResult]) -> None:
        """Normalize factors across all stocks to z-scores (-3 to +3).

        ⚠️ Requires a large universe (N >= 30) for statistical validity.
        For small portfolios (N < 30), call time_series_normalize() instead.

        Mutates each FactorResult in-place.
        """
        if len(results) < 30:
            logger.warning(
                "[FactorEngine] cross_sectional_normalize called with N=%d < 30 "
                "— results may be statistically unreliable. "
                "Consider using time_series_normalize() for small portfolios.",
                len(results),
            )
        for fd in self.factors:
            name = fd.name
            raw_vals = [r.raw_factors.get(name, 0.0) for r in results]
            # 截面标准化前做MAD去极值
            raw_vals = winsorize_mad(np.array(raw_vals)).tolist()
            mean = np.mean(raw_vals)
            std = np.std(raw_vals)
            if std < 1e-8:
                for r in results:
                    r.z_scores[name] = 0.0
                continue

            for r in results:
                z = (r.raw_factors.get(name, 0.0) - mean) / std
                z = max(-3.0, min(3.0, z))
                r.z_scores[name] = z

        # Compute composite scores
        for r in results:
            composite = 0.0
            total_weight = 0.0
            for fd in self.factors:
                z = r.z_scores.get(fd.name, 0.0)
                sign = 1.0 if fd.higher_better else -1.0
                composite += sign * z * fd.weight
                total_weight += fd.weight
            if total_weight > 0:
                composite /= total_weight
            r.composite_score = round(composite, 3)

            if composite > 0.5:
                r.composite_label = "强势"
            elif composite < -0.5:
                r.composite_label = "弱势"
            else:
                r.composite_label = "中性"

    def build_factor_profile(self, result: FactorResult) -> str:
        """Build a structured per-factor breakdown for LLM prompt injection.

        v2.0 (2026-06-30): Expanded from 2-line summary to per-factor breakdown.
        Each factor's name, z-score, direction arrow, and category are surfaced
        so the LLM can translate the system's quantitative analysis rather than
        relying on external data alone.
        """
        cs = result.composite_score
        label = result.composite_label
        direction = "↑偏多" if cs > 0.2 else ("↓偏空" if cs < -0.2 else "→中性")

        # Per-factor breakdown grouped by category
        categories: dict[str, list[str]] = {}
        for fd in self.factors:
            z = result.z_scores.get(fd.name, 0.0)
            cat = fd.category
            raw = result.raw_factors.get(fd.name, None)
            raw_str = f" [{raw:.2f}]" if raw is not None else ""

            # Direction arrow based on higher_better
            if fd.higher_better:
                arrow = "↑" if z > 0.1 else ("↓" if z < -0.1 else "→")
            else:
                arrow = "↑" if z < -0.1 else ("↓" if z > 0.1 else "→")

            strength = "★" if abs(z) > 1.5 else ("☆" if abs(z) > 0.5 else "")
            cat_label_map = {
                "momentum": "动量", "sentiment": "情绪",
                "volatility": "波动", "quality": "质量", "valuation": "估值",
            }
            cat_cn = cat_label_map.get(cat, cat)
            hint = FACTOR_INTERPRETATIONS.get(fd.name, "")
            from src.core.prompt_config import should_include_factor_interpretations
            hint_str = f" — {hint}" if hint and abs(z) > 0.3 and should_include_factor_interpretations() else ""
            item = f"  {arrow} {fd.display_name}: {z:+.2f}σ{strength}{raw_str}{hint_str}"

            if cat_cn not in categories:
                categories[cat_cn] = []
            categories[cat_cn].append(item)

        # Find diverging factors
        diverging = []
        for fd in self.factors:
            z = result.z_scores.get(fd.name, 0.0)
            if (cs > 0.2 and z < -0.5) or (cs < -0.2 and z > 0.5):
                diverging.append(f"{fd.display_name}({z:+.1f}σ)")

        lines = [
            f"### 系统量化分析",
            f"综合: {cs:+.2f} {direction} ｜ 复合标签: {label}",
            f"因子逐项:",
        ]
        for cat in ["动量", "情绪", "波动", "质量", "估值"]:
            if cat in categories:
                lines.append(f"  [{cat}]")
                lines.extend(categories[cat])

        if diverging:
            lines.append(f"⚠因子分歧: {'; '.join(diverging[:4])}")

        return "\n".join(lines)
