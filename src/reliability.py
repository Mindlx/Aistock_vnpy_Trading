"""
可靠性调制贝叶斯融合 — 配置、置信度校准与幻觉检测

基于 Oracle 架构设计，为三系统提供动态权重计算：
  w_eff = base_alpha × confidence × (1 - hallucination)

每个系统根据其数学本质（确定性 vs 随机性）获得不同的 α 值和幻觉检测策略。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import math

from src.normalizer import SignalNormalizer

logger = logging.getLogger(__name__)


class ReliabilityConfig:
    """
    系统基础可靠度配置。

    α (base_alpha) 由系统本质决定，非日常调优参数：
      - ly:  0.75  — sklearn predict_proba，可回测，100% 可重复
      - ml:  0.55  — ~40% 因子数学 + ~60% LLM，混合误差
      - at:  0.40  — 纯 LLM 角色扮演，角色不一致 + prompt 敏感

    h (default_h) 是默认幻觉度（无辩论数据时的保守估计）：
      - ly:  0.00  — 无幻觉概念
      - ml:  0.15  — 默认中等怀疑
      - at:  0.30  — 默认高度怀疑
    """

    BASE_ALPHA = {"lynx_vnpy": 0.75, "mindlynx": 0.65, "tradingagent": 0.40}
    DEFAULT_H = {"lynx_vnpy": 0.0, "mindlynx": 0.15, "tradingagent": 0.30}
    PROBABILITY_K = 1.0  # sigmoid 陡度，v3.0 适配 [-3,+3] 宽范围

    LY_VETO_THRESHOLD = 0.30  # |P_ly - 0.50| > 0.30 触发数学否决权

    # Per-stock ML alpha overrides (static fallback, updated monthly by calibrate_alphas.py)
    STOCK_ALPHA_OVERRIDE: dict[str, float] = {
        "000592": 0.3,
        "001390": 0.3,
        "300676": 0.3,
        "600372": 0.3,
        "601801": 0.4,
        "603189": 0.4,
        "603557": 0.3,
        "605368": 0.3,
        "688202": 0.4,
    }

    # Dynamic alpha: cache for DB query results (TTL=3600s=1h)
    _alpha_cache: dict[str, tuple[float | None, float]] = {}  # stock_code -> (alpha, cached_at)

    @classmethod
    def _alpha_from_db(cls, stock_code: str) -> float | None:
        """Query backtest_summaries for per-stock sentiment accuracy, return alpha.

        Uses DB from ML subsystem (stock_analysis.db). Falls back silently.
        Cache TTL=3600s to avoid repeated queries during fusion loops.
        """
        # Check cache
        now = time.time()
        cached = cls._alpha_cache.get(stock_code)
        if cached and (now - cached[1]) < 3600:
            return cached[0]

        try:
            import sqlite3
            from pathlib import Path

            db_path = Path(__file__).resolve().parent.parent / "systems" / "MindLynx-Aistock" / "data" / "stock_analysis.db"
            if not db_path.exists():
                return None

            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("""
                SELECT sentiment_direction_accuracy_pct
                FROM backtest_summaries
                WHERE scope = 'stock' AND code = ? AND eval_window_days = 5
                  AND sentiment_direction_accuracy_pct IS NOT NULL
                ORDER BY computed_at DESC
                LIMIT 1
            """, (stock_code,))
            row = cur.fetchone()
            conn.close()

            if row and row[0] is not None:
                acc = float(row[0])
                # Map accuracy to alpha (same logic as calibrate_alphas.py)
                if acc >= 65.0:
                    alpha = 0.80
                elif acc >= 50.0:
                    alpha = 0.65
                elif acc >= 25.0:
                    alpha = 0.40
                else:
                    alpha = 0.30
                cls._alpha_cache[stock_code] = (alpha, now)
                return alpha
        except Exception as exc:
            logger.debug(f"[Reliability] DB alpha query failed for {stock_code}: {exc}")

        cls._alpha_cache[stock_code] = (None, now)  # cache negative
        return None

    @classmethod
    def alpha(cls, system: str, stock_code: str | None = None) -> float:
        """Get base reliability for a system, optionally overridden per stock.

        Resolution order (mindlynx only):
          1. DB query (dynamic, from backtest_summaries)
          2. STOCK_ALPHA_OVERRIDE (static fallback)
          3. BASE_ALPHA[system] (default)
        """
        base = cls.BASE_ALPHA.get(system, 0.50)
        if system == "mindlynx" and stock_code is not None:
            db_alpha = cls._alpha_from_db(stock_code)
            if db_alpha is not None:
                return db_alpha
            return cls.STOCK_ALPHA_OVERRIDE.get(stock_code, base)
        return base

    @classmethod
    def default_h(cls, system: str) -> float:
        return cls.DEFAULT_H.get(system, 0.20)


class ConfidenceCalibrator:
    """各系统置信度校准器"""

    @staticmethod
    def calibrate_ly(prob_up: float) -> float:
        """
        ly 置信度: |prob_up - 50| / 50

        predict_proba() 是校准概率，距离 50% 中性点直接编码置信度。
        prob_up=85 -> c=0.70, prob_up=50 -> c=0 (最大不确定性)
        """
        if math.isnan(prob_up):
            return 0.0
        return min(1.0, abs(prob_up - 50.0) / 50.0)

    @staticmethod
    def calibrate_ml(sentiment_score: float) -> float:
        """
        ml 置信度: |score - 50| / 50 × 0.85

        sentiment_score 是 LLM 生成，不如真实概率校准，乘 0.85 降权。
        score=80 -> c=0.60×0.85=0.51
        """
        raw = abs(sentiment_score - 50.0) / 50.0
        return min(0.85, raw * 0.85)

    @staticmethod
    def calibrate_at(debate_consistency: float) -> float:
        """
        at 置信度: debate_consistency × 0.50

        纯 LLM 系统基础置信度上限 0.50。辩论一致性高时提升。
        debate_consistency 由 _parse_debate_state() 计算，范围 0-1。
        """
        return min(0.50, debate_consistency * 0.50)


class HallucinationDetector:
    """
    幻觉检测门控。

    ly: h=0 (无需检测)
    ml: h = factor_deviation + direction_contradiction
    at: h = 1 - debate_consistency + role_check
    """

    # ml 因子偏差参数
    ML_FACTOR_DEVIATION_THRESHOLD = 20.0   # |score - 因子基线| > 20 触发
    ML_FACTOR_DEVIATION_MAX = 50.0         # 最大偏差上限

    @classmethod
    def detect_ly(cls) -> float:
        """ly 无幻觉风险"""
        return 0.0

    @classmethod
    def detect_ml(
        cls,
        sentiment_score: float,
        factor_baseline: Optional[float] = None,
        operation_advice: Optional[str] = None,
        trend_prediction: Optional[str] = None,
    ) -> float:
        """
        ml 幻觉检测。

        两个维度：
          1. factor_deviation: LLM 评分偏离因子基线程度
          2. direction_contradiction: 操作建议与趋势预测矛盾

        无因子基线时返回默认 h=0.15。
        """
        # 维度 1: 因子偏差
        factor_deviation = 0.0
        if factor_baseline is not None:
            deviation = abs(sentiment_score - factor_baseline)
            if deviation > cls.ML_FACTOR_DEVIATION_THRESHOLD:
                # 超过阈值后线性映射到 0~1
                excess = deviation - cls.ML_FACTOR_DEVIATION_THRESHOLD
                factor_deviation = min(1.0, excess / cls.ML_FACTOR_DEVIATION_MAX)

        # 维度 2: 方向矛盾
        direction_contradiction = 0.0
        if operation_advice and trend_prediction:
            advice_bullish = operation_advice in ("买入", "加仓")
            advice_bearish = operation_advice in ("减仓", "卖出")
            trend_bullish = any(w in trend_prediction for w in ["涨", "多", "牛", "up"])
            trend_bearish = any(w in trend_prediction for w in ["跌", "空", "熊", "down"])
            if (advice_bullish and trend_bearish) or (advice_bearish and trend_bullish):
                direction_contradiction = 0.30

        h = factor_deviation + direction_contradiction
        return min(1.0, max(0.0, h))

    @classmethod
    def detect_at(
        cls,
        debate_state: Optional[Dict[str, Any]] = None,
    ) -> float:
        """
        at 幻觉检测。

        基于辩论状态：
          h = (1 - investment_agreement) + (1 - risk_agreement) + analyst_variance
          除以 3 归一化到 [0, 1]

        无辩论数据时返回默认 h=0.30。
        """
        if not debate_state or not debate_state.get("debate_available", False):
            return ReliabilityConfig.default_h("tradingagent")

        inv_agree = debate_state.get("investment_agreement", 0.5)
        risk_agree = debate_state.get("risk_agreement", 0.5)
        anal_var = debate_state.get("analyst_variance", 0.5)

        # 辩论不一致度 + 分析师分歧
        disagreement = (1.0 - inv_agree) + (1.0 - risk_agree) + anal_var
        h = disagreement / 3.0

        return min(1.0, max(0.0, h))


# ══════════════════════════════════════════════
# 概率空间映射工具
# ══════════════════════════════════════════════

def score_to_probability(score: float, k: float = 1.0) -> float:
    """将 L7 得分映射到概率 [0,1]，委托 normalizer 实现。"""
    return SignalNormalizer.to_probability(score, k)


def probability_to_decision(p: float, thresholds: Dict[str, float]) -> str:
    """
    将融合概率映射到 7 级决策标签。

    参数:
        p: 融合概率 [0, 1]
        thresholds: 阈值配置
            {strong_bullish: 0.88, bullish: 0.73,
             cautious_bullish: 0.62,
             cautious_bearish: 0.38, bearish: 0.27, strong_bearish: 0.12}

    返回: 7 级标签
    """
    if p >= thresholds.get("strong_bullish", 0.88):
        return "strong_bullish"
    elif p >= thresholds.get("bullish", 0.73):
        return "bullish"
    elif p >= thresholds.get("cautious_bullish", 0.62):
        return "cautious_bullish"
    elif p >= thresholds.get("cautious_bearish", 0.38):
        return "neutral"
    elif p >= thresholds.get("bearish", 0.27):
        return "cautious_bearish"
    elif p >= thresholds.get("strong_bearish", 0.12):
        return "bearish"
    else:
        return "strong_bearish"
