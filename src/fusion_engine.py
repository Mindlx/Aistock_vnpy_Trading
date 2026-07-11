"""
三系统融合引擎 — 线性积分融合

融合策略:
1. 归一化各系统得分（含置信度调制）
2. 检测系统间分歧（如果有方向冲突，施加不确定性惩罚）
3. 缺失系统自动重分配权重
4. 线性积分计算融合得分
5. 映射到最终决策+仓位建议

⚠️ 仅供学习和研究目的，不构成任何投资建议
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from src.fusion import (
    BayesianFusionStrategy,
    LinearFusionStrategy,
    PortfolioSummarizer,
    apply_bayesian_override,
    compute_adjusted_weights,
    compute_uncertainty_penalty,
    detect_disagreement,
    get_final_decision,
)
from src.logger import FusionLogger
from src.normalizer import (
    SignalNormalizer,
    L7_THRESHOLDS,
    L7_SIGNAL_NAMES,
    L7_POSITION,
    MAX_POSITION_DISAGREEMENT,
)
from src.reliability import (
    ConfidenceCalibrator,
    HallucinationDetector,
    ReliabilityConfig,
    score_to_probability,
    probability_to_decision,
)


class FusionEngine:
    """三系统信号融合引擎 — 支持线性加权与贝叶斯两种模式"""

    MODE_LINEAR = "linear"
    MODE_BAYESIAN = "bayesian"
    MODE_DUAL = "dual"  # 同时输出两种结果

    # 仓位上限（分歧情况下硬性限制）
    MAX_POSITION_DISAGREEMENT = "1成"

    def __init__(self, config_path: str | None = None):
        _root = Path(__file__).resolve().parent.parent
        config_path = config_path or str(_root / "config/settings.yaml")
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        self.weights = self.config["weights"]
        self.logger = FusionLogger(
            log_dir=self.config.get("logging", {}).get("log_dir", "config/logs"),
            retention_days=self.config.get("logging", {}).get("retention_days", 90),
        )
        self.normalizer = SignalNormalizer()

        # 融合模式: linear / bayesian / dual
        self.fusion_mode = self.config.get("fusion_mode", "linear")

        # 贝叶斯模式参数（来自可靠性配置）
        rel_config = self.config.get("reliability", {})
        self.probability_k = rel_config.get("probability_k", 1.0)
        override = rel_config.get("override", {})
        self.ly_veto_threshold = override.get("ly_veto_threshold", 0.30)

        # 系统名称列表（用于权重迭代）
        self.systems = ["lynx_vnpy", "mindlynx", "tradingagent"]

        # sentiment_score 方向阈值（从 config 读取，默认 52/49）
        st = self.config.get("sentiment_threshold", {})
        self.sentiment_threshold_bull = st.get("bull", 52)
        self.sentiment_threshold_bear = st.get("bear", 49)

        # 策略类
        w = self.weights
        self._linear = LinearFusionStrategy(
            self.normalizer, self.logger,
            {"lynx": w.get("lynx_vnpy", 0.35), "mindlynx": w.get("mindlynx", 0.35),
             "tradingagent": w.get("tradingagent", 0.30)},
            self.sentiment_threshold_bull, self.sentiment_threshold_bear,
        )
        self._bayesian = BayesianFusionStrategy(
            self.normalizer, self.logger,
            self.probability_k, self.ly_veto_threshold,
        )
        self._summarizer = PortfolioSummarizer()

    # ──────── 决策映射（7 级 L7 空间，从 normalizer 导入） ────────

    def _get_final_decision(self, score: float, disagreement: bool = False) -> Dict[str, Any]:
        result = get_final_decision(score, disagreement, L7_THRESHOLDS)
        if disagreement:
            from src.normalizer import SignalNormalizer
            result["position"] = SignalNormalizer.cap_position_for_disagreement(result["position"])
        return result

    # ──────── 分歧检测 ────────

    @staticmethod
    def _detect_disagreement(*args, **kwargs) -> Tuple[bool, float, int]:
        return detect_disagreement(*args, **kwargs)

    @staticmethod
    def _compute_uncertainty_penalty(disagreement_score: float) -> float:
        return compute_uncertainty_penalty(disagreement_score)

    # ──────── 缺失数据处理 ────────

    def _compute_adjusted_weights(
        self, lynx_valid: bool, mindlynx_valid: bool, tradingagent_valid: bool,
    ) -> Tuple[Dict[str, float], int, bool]:
        w = {"lynx": self.weights["lynx_vnpy"],
             "mindlynx": self.weights["mindlynx"],
             "tradingagent": self.weights["tradingagent"]}
        return compute_adjusted_weights(lynx_valid, mindlynx_valid, tradingagent_valid, w)

    # ──────── 核心融合方法 ────────

    def _fuse_bayesian(self, **kwargs) -> Dict[str, Any]:
        return self._bayesian.fuse(**kwargs)

    @staticmethod
    @staticmethod
    def _apply_bayesian_override(*args, **kwargs) -> float:
        return apply_bayesian_override(*args, **kwargs)

    def fuse_single_stock(
        self,
        stock_code: str,
        stock_name: str = "",
        lynx_signal: str = "观望",
        lynx_prob_up: float = 50.0,
        mindlynx_advice: str = "观望",
        mindlynx_score: int = 50,
        mindlynx_trend: Optional[str] = None,
        mindlynx_valid: bool = True,
        mindlynx_factor_baseline: Optional[float] = None,
        tradingagent_rating: str = "Hold",
        tradingagent_valid: bool = True,
        ta_is_stale: bool = False,
        ta_debate_state: Optional[Dict[str, Any]] = None,
        alpha158_l7: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        对单只股票进行融合分析。

        支持三种模式:
          linear:   当前线性加权 + 分歧检测（默认）
          bayesian: 可靠性调制贝叶斯融合
          dual:     同时输出两种结果

        mindlynx_valid/tradingagent_valid: 数据是否真实可用（来自 data_loader）
        ta_debate_state: 来自 data_loader._parse_debate_state()，贝叶斯模式使用
        """
        # 模式分发
        if self.fusion_mode == self.MODE_BAYESIAN:
            return self._fuse_bayesian(
                stock_code=stock_code, stock_name=stock_name,
                lynx_signal=lynx_signal, lynx_prob_up=lynx_prob_up,
                mindlynx_advice=mindlynx_advice, mindlynx_score=mindlynx_score,
                mindlynx_trend=mindlynx_trend,
                mindlynx_valid=mindlynx_valid,
                mindlynx_factor_baseline=mindlynx_factor_baseline,
                tradingagent_rating=tradingagent_rating,
                tradingagent_valid=tradingagent_valid,
                ta_debate_state=ta_debate_state,
                ta_is_stale=ta_is_stale,
            )
        if self.fusion_mode == self.MODE_DUAL:
            linear_result = self._fuse_linear(
                stock_code=stock_code, stock_name=stock_name,
                lynx_signal=lynx_signal, lynx_prob_up=lynx_prob_up,
                mindlynx_advice=mindlynx_advice, mindlynx_score=mindlynx_score,
                mindlynx_trend=mindlynx_trend,
                mindlynx_valid=mindlynx_valid,
                tradingagent_rating=tradingagent_rating,
                tradingagent_valid=tradingagent_valid,
                ta_is_stale=ta_is_stale,
                ta_debate_state=ta_debate_state,
                alpha158_l7=alpha158_l7,
            )
            bayesian_result = self._fuse_bayesian(
                stock_code=stock_code, stock_name=stock_name,
                lynx_signal=lynx_signal, lynx_prob_up=lynx_prob_up,
                mindlynx_advice=mindlynx_advice, mindlynx_score=mindlynx_score,
                mindlynx_trend=mindlynx_trend,
                mindlynx_valid=mindlynx_valid,
                mindlynx_factor_baseline=mindlynx_factor_baseline,
                tradingagent_rating=tradingagent_rating,
                tradingagent_valid=tradingagent_valid,
                ta_debate_state=ta_debate_state,
                ta_is_stale=ta_is_stale,
            )
            return {
                "stock_code": stock_code, "stock_name": stock_name,
                "fusion_mode": "dual",
                "linear": linear_result,
                "bayesian": bayesian_result,
                # 向下游暴露linear顶层字段（CSV/通知/汇总均读顶层）
                "valid": linear_result.get("valid", False),
                "fusion_score": linear_result.get("fusion_score", 0),
                "lynx_score": linear_result.get("lynx_score", 0),
                "lynx_valid": linear_result.get("lynx_valid", False),
                "mindlynx_score": linear_result.get("mindlynx_score", 0),
                "mindlynx_valid": linear_result.get("mindlynx_valid", False),
                "tradingagent_score": linear_result.get("tradingagent_score", 0),
                "tradingagent_valid": linear_result.get("tradingagent_valid", False),
                "signal": linear_result.get("signal", "neutral"),
                "signal_name": linear_result.get("signal_name", "中性/持有"),
                "position_advice": linear_result.get("position_advice", "0成"),
                "has_disagreement": linear_result.get("has_disagreement", False),
                "is_degraded": linear_result.get("is_degraded", False),
                "ta_is_stale": linear_result.get("ta_is_stale", False),
                "message": linear_result.get("message", ""),
            }

        return self._fuse_linear(
            stock_code=stock_code, stock_name=stock_name,
            lynx_signal=lynx_signal, lynx_prob_up=lynx_prob_up,
            mindlynx_advice=mindlynx_advice, mindlynx_score=mindlynx_score,
            mindlynx_trend=mindlynx_trend,
            mindlynx_valid=mindlynx_valid,
            tradingagent_rating=tradingagent_rating,
            tradingagent_valid=tradingagent_valid,
            ta_is_stale=ta_is_stale,
            ta_debate_state=ta_debate_state,
            alpha158_l7=alpha158_l7,
        )

    # ──────── 原始线性融合逻辑（重命名自 fuse_single_stock） ────────
    def _fuse_linear(self, **kwargs) -> Dict[str, Any]:
        return self._linear.fuse(**kwargs)

    def fuse_stock_pool(
        self, stock_signals: List[Dict[str, Any]],
        ta_is_stale: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        对多只股票批量融合。

        stock_signals: [
            {
                "code": str, "name": str,
                "lynx_signal": str, "lynx_prob_up": float,
                "mindlynx_advice": str, "mindlynx_score": int,
                "tradingagent_rating": str,
            },
            ...
        ]
            ta_is_stale: TA 数据是否为昨日结果（延时降权）
        """
        results = []
        for item in stock_signals:
            result = self.fuse_single_stock(
                stock_code=item["code"],
                stock_name=item.get("name", ""),
                lynx_signal=item.get("lynx_signal", "观望"),
                lynx_prob_up=item.get("lynx_prob_up", 50.0),
                mindlynx_advice=item.get("mindlynx_advice", "观望"),
                mindlynx_score=item.get("mindlynx_score", 50),
                mindlynx_valid=item.get("mindlynx_valid", True),
                mindlynx_trend=item.get("mindlynx_trend", ""),
                tradingagent_rating=item.get("tradingagent_rating", "Hold"),
                tradingagent_valid=item.get("tradingagent_valid", True),
                ta_is_stale=ta_is_stale,
                ta_debate_state=item.get("ta_debate_state", {}),
                alpha158_l7=item.get("alpha158_l7"),
            )
            # 补充行情数据和子系统原始数据（从输入透传到结果）
            for k in ("price", "pct_chg", "volume_ratio", "ma5", "ma10", "ma20",
                      "mindlynx_trend", "mindlynx_sentiment", "mindlynx_operation",
                      "mindlynx_analysis", "mindlynx_ideal_buy", "mindlynx_stop_loss",
                      "mindlynx_take_profit",
                      "ml_trend_score", "ml_support_level", "ml_resistance_level",
                      "ml_volume_ratio_dash", "ml_turnover_rate",
                      "ml_risk_alert_count", "ml_catalyst_count"):
                if k in item:
                    result[k] = item[k]
            results.append(result)

        # 按融合得分降序排列
        results.sort(key=lambda r: (r.get("valid", False), r.get("fusion_score", 0)), reverse=True)

        # ── v4.0: 附加 UnifiedPosition 仓位建议 + 组合约束 ──
        from src.position import UnifiedPosition, PositionConstraintEngine

        positions = []
        disagreements = []
        for item in results:
            if item.get("valid"):
                sig = item.get("signal", "neutral")
                pos = UnifiedPosition.from_signal(sig)
                positions.append(pos)
                disagreements.append(item.get("has_disagreement", False))
            else:
                positions.append(UnifiedPosition.from_signal("neutral"))
                disagreements.append(False)

        constraint = PositionConstraintEngine(total_stocks=len(results))
        constrained = constraint.apply(positions, disagreements)

        for item, pos in zip(results, constrained):
            item["unified_position"] = {
                "pct": pos.pct,
                "label": pos.label,
                "min_pct": pos.min_pct,
                "max_pct": pos.max_pct,
            }

        return results

    def get_portfolio_summary(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """生成投资组合摘要（含 v4.0 仓位约束后汇总）。"""
        from src.position import PositionConstraintEngine

        valid_results = [r for r in results if r.get("valid", False)]

        strong_bullish = [r for r in valid_results if r.get("signal") == "strong_bullish"]
        weak_bullish = [r for r in valid_results if r.get("signal") in ("bullish", "cautious_bullish")]
        neutral = [r for r in valid_results if r.get("signal") == "neutral"]
        weak_bearish = [r for r in valid_results if r.get("signal") in ("cautious_bearish", "bearish")]
        strong_bearish = [r for r in valid_results if r.get("signal") == "strong_bearish"]

        # 仓位约束后摘要
        from src.position import UnifiedPosition, PositionConstraintEngine
        constraint = PositionConstraintEngine(total_stocks=len(valid_results))
        positions = [UnifiedPosition.from_signal(r.get("signal", "neutral")) for r in valid_results]
        constrained = constraint.apply(positions)
        portfolio = constraint.summary(constrained)

        # ── 方向偏倚监控（c1skill P0: 追踪各系统方向分布）──
        def _direction_bias(scores: list, threshold: float = 0.5) -> dict:
            bullish = sum(1 for s in scores if s is not None and s > threshold)
            bearish = sum(1 for s in scores if s is not None and s < -threshold)
            neutral = sum(1 for s in scores if s is not None and -threshold <= s <= threshold)
            total = bullish + bearish + neutral
            return {
                "bullish_pct": round(bullish / total * 100, 1) if total else 0,
                "bearish_pct": round(bearish / total * 100, 1) if total else 0,
                "neutral_pct": round(neutral / total * 100, 1) if total else 0,
                "count": total,
            } if total else {"bullish_pct": 0, "bearish_pct": 0, "neutral_pct": 0, "count": 0}

        return {
            "total_valid": len(valid_results),
            "total_results": len(results),
            "distribution": {
                "strong_bullish": len(strong_bullish),
                "weak_bullish": len(weak_bullish),
                "neutral": len(neutral),
                "weak_bearish": len(weak_bearish),
                "strong_bearish": len(strong_bearish),
            },
            "direction_bias": {
                "fusion": _direction_bias([r.get("score", 0) for r in valid_results]),
                "ly": _direction_bias([r.get("ly_score") for r in valid_results]),
                "ml": _direction_bias([r.get("ml_score") for r in valid_results]),
                "at": _direction_bias([r.get("at_score") for r in valid_results]),
            },
            "degraded_count": sum(1 for r in valid_results if r.get("is_degraded", False)),
            "disagreement_count": sum(1 for r in valid_results if r.get("has_disagreement", False)),
            # v4.0 仓位
            "portfolio_position": portfolio,
            "portfolio_position_display": constraint.summary_display(constrained),
        }
