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
from typing import Any, Dict, List, Optional, Tuple

import yaml

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

    def __init__(self, config_path: str = "config/settings.yaml"):
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

    # ──────── 决策映射（7 级 L7 空间，从 normalizer 导入） ────────

    def _get_final_decision(self, score: float, disagreement: bool = False) -> Dict[str, Any]:
        """
        根据 L7 得分判断最终决策（7 级）。

        当检测到分歧时，仓位上限为 1成。
        """
        t = L7_THRESHOLDS
        if score > t["strong_bullish"]:
            signal = "strong_bullish"
        elif score > t["bullish"]:
            signal = "bullish"
        elif score > t["cautious_bullish"]:
            signal = "cautious_bullish"
        elif score > t["cautious_bearish"]:
            signal = "neutral"
        elif score > t["bearish"]:
            signal = "cautious_bearish"
        elif score > t["strong_bearish"]:
            signal = "bearish"
        else:
            signal = "strong_bearish"

        name = L7_SIGNAL_NAMES.get(signal, "中性/持有")
        position = L7_POSITION.get(signal, "0成")

        if disagreement:
            position = SignalNormalizer.cap_position_for_disagreement(position)

        return {
            "signal": signal,
            "name": name,
            "position": position,
            "disagreement_capped": disagreement,
        }

    # ──────── 分歧检测 ────────

    @staticmethod
    def _detect_disagreement(
        lynx_score: float, mindlynx_score: float, tradingagent_score: float,
        lynx_valid: bool, mindlynx_valid: bool, tradingagent_valid: bool,
    ) -> Tuple[bool, float]:
        """
        检测系统间分歧。

        Oracle 建议: 线性融合将方向相反的强信号"归零"为中性，这是危险的。
        当至少一个系统看多 且 至少一个系统看空时，标记为分歧。

        返回:
            (有分歧, 分歧分数)
            分歧分数 = 各方向信号的标准差（仅有效系统），用于量化分歧程度
        """
        scores = []
        if lynx_valid:
            scores.append(lynx_score)
        if mindlynx_valid:
            scores.append(mindlynx_score)
        if tradingagent_valid:
            scores.append(tradingagent_score)

        if len(scores) < 2:
            return False, 0.0

        # 判断方向: 正=看多, 负=看空, 0=中性
        # v3.0: 阈值从 0.1 升至 0.5（适配 [-3,+3] 宽范围）
        has_bullish = any(s > 0.5 for s in scores)
        has_bearish = any(s < -0.5 for s in scores)

        disagreement = has_bullish and has_bearish

        # 分歧量化: 分数间的标准差
        if disagreement and len(scores) > 1:
            mean = sum(scores) / len(scores)
            variance = sum((s - mean) ** 2 for s in scores) / len(scores)
            disagreement_score = math.sqrt(variance)
        else:
            disagreement_score = 0.0

        return disagreement, disagreement_score

    @staticmethod
    def _compute_uncertainty_penalty(disagreement_score: float) -> float:
        """
        根据分歧程度计算不确定性惩罚系数。
        v3.0: 适配 [-3,+3] 宽范围，最大惩罚升至 2.0
        """
        if disagreement_score <= 0.5:  # 宽范围下小幅分歧不惩罚
            return 0.0
        penalty = min(2.0, max(0.0, (disagreement_score - 0.5) * 1.2))
        return penalty

    # ──────── 缺失数据处理 ────────

    def _compute_adjusted_weights(
        self, lynx_valid: bool, mindlynx_valid: bool, tradingagent_valid: bool,
    ) -> Tuple[Dict[str, float], int, bool]:
        """
        Oracle 建议: 缺失系统时重新分配权重。

        返回:
            (adjusted_weights, valid_count, is_degraded)
        """
        weight_map = {
            "lynx": self.weights["lynx_vnpy"],
            "mindlynx": self.weights["mindlynx"],
            "tradingagent": self.weights["tradingagent"],
        }
        valid_map = {
            "lynx": lynx_valid,
            "mindlynx": mindlynx_valid,
            "tradingagent": tradingagent_valid,
        }

        # 只保留有效系统
        valid_weights = {
            sys: w for sys, w in weight_map.items() if valid_map.get(sys, True)
        }
        valid_count = len(valid_weights)

        if valid_count == 0:
            return {}, 0, True

        # 重新归一化权重
        total_weight = sum(valid_weights.values())
        adjusted = {
            sys: w / total_weight for sys, w in valid_weights.items()
        }

        # 判断是否降级
        is_degraded = valid_count < 3

        return adjusted, valid_count, is_degraded

    # ──────── 核心融合方法 ────────

    def _fuse_bayesian(
        self,
        stock_code: str,
        stock_name: str = "",
        lynx_signal: str = "观望",
        lynx_prob_up: float = 50.0,
        mindlynx_advice: str = "观望",
        mindlynx_score: int = 50,
        mindlynx_trend: Optional[str] = None,
        mindlynx_valid: bool = True,
        tradingagent_rating: str = "Hold",
        tradingagent_valid: bool = True,
        ta_is_stale: bool = False,
        ta_debate_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        贝叶斯融合模式 — 可靠性调制概率融合。

        融合管线:
          1. 概率空间校准（得分→概率）
          2. 幻觉检测门控（ml:因子偏差, at:辩论一致性）
          3. 置信度校准（各系统独立公式）
          4. 有效权重计算（w = α × c × (1-h)）
          5. 加权概率融合
          6. 数学否决权（ly 强信号时覆盖）
        """
        # Step 1: 归一化 + 概率空间映射
        lynx_normalized, lynx_valid = self.normalizer.normalize_lynx(
            lynx_signal, lynx_prob_up
        )
        mindlynx_normalized = self.normalizer.normalize_mindlynx(
            mindlynx_advice, mindlynx_score, mindlynx_trend
        )
        tradingagent_normalized = self.normalizer.normalize_tradingagent(
            tradingagent_rating, debate_state=ta_debate_state
        )

        p_ly = self.normalizer.to_probability(lynx_normalized, self.probability_k)
        p_ml = self.normalizer.to_probability(mindlynx_normalized, self.probability_k)
        p_at = self.normalizer.to_probability(tradingagent_normalized, self.probability_k)

        # Step 2: 幻觉检测
        h_ly = HallucinationDetector.detect_ly()
        h_ml = HallucinationDetector.detect_ml(
            sentiment_score=mindlynx_score,
            operation_advice=mindlynx_advice,
            trend_prediction=mindlynx_trend,
        )
        h_at = HallucinationDetector.detect_at(debate_state=ta_debate_state)

        # Step 3: 置信度校准
        c_ly = ConfidenceCalibrator.calibrate_ly(lynx_prob_up)
        c_ml = ConfidenceCalibrator.calibrate_ml(float(mindlynx_score))
        c_at = ConfidenceCalibrator.calibrate_at(
            (ta_debate_state or {}).get("investment_agreement", 0.5)
        )

        # Step 4: 有效权重
        w_ly = ReliabilityConfig.alpha("lynx_vnpy") * c_ly * (1.0 - h_ly)
        w_ml = ReliabilityConfig.alpha("mindlynx") * c_ml * (1.0 - h_ml)
        w_at = ReliabilityConfig.alpha("tradingagent") * c_at * (1.0 - h_at)

        # ⚡ TA 数据过期处理：降低有效权重（与 linear 模式一致）
        if ta_is_stale:
            w_at *= 0.7
            self.logger.info(f"[Bayesian][{stock_code}] TA 过期，权重降低 30%")

        total_w = w_ly + w_ml + w_at
        if total_w == 0:
            return {
                "stock_code": stock_code, "stock_name": stock_name,
                "valid": False, "message": "所有系统有效权重为零，无法融合",
                "fusion_mode": "bayesian",
            }

        # Step 5: 加权概率融合
        p_fused = (w_ly * p_ly + w_ml * p_ml + w_at * p_at) / total_w

        # Step 6: 数学否决权
        p_final = self._apply_bayesian_override(p_ly, p_ml, p_at, p_fused)

        # Step 7: 映射到 7 级决策
        signal_label = probability_to_decision(p_final, {
            "strong_bullish": 0.88, "bullish": 0.73,
            "cautious_bullish": 0.62, "cautious_bearish": 0.38,
            "bearish": 0.27, "strong_bearish": 0.12,
        })
        signal_name_map = L7_SIGNAL_NAMES
        position_map = L7_POSITION

        # 检查分歧（使用概率空间的等价判断）
        has_disagreement = (p_ly - 0.50) * (p_at - 0.50) < -0.2
        position = position_map.get(signal_label, "0成")
        if has_disagreement:
            position = SignalNormalizer.cap_position_for_disagreement(position)

        return {
            "stock_code": stock_code, "stock_name": stock_name,
            "valid": True, "fusion_mode": "bayesian",
            "p_ly": round(p_ly, 3), "p_ml": round(p_ml, 3), "p_at": round(p_at, 3),
            "w_ly": round(w_ly, 3), "w_ml": round(w_ml, 3), "w_at": round(w_at, 3),
            "h_ly": h_ly, "h_ml": round(h_ml, 3), "h_at": round(h_at, 3),
            "c_ly": round(c_ly, 3), "c_ml": round(c_ml, 3), "c_at": round(c_at, 3),
            "p_fused": round(p_fused, 3), "p_final": round(p_final, 3),
            "has_disagreement": has_disagreement,
            "lynx_valid": lynx_valid,
            "mindlynx_valid": mindlynx_valid,
            "tradingagent_valid": tradingagent_valid,
            "ta_is_stale": ta_is_stale,
            "signal": signal_label,
            "signal_name": signal_name_map.get(signal_label, "中性/观望"),
            "position_advice": position,
        }

    @staticmethod
    def _apply_bayesian_override(
        p_ly: float, p_ml: float, p_at: float, p_fused: float,
    ) -> float:
        """
        数学否决权: 当 ly 强信号且与融合结果冲突时调整。

        设计原则:
        - ly+ml (数学+混合) vs at (纯LLM) → 信任数学
        - ly+at vs ml → 信任 ly，但 at 附议增加可信度
        - ml+at vs ly → 2v1，不完全采用 ly
        """
        ly_conviction = abs(p_ly - 0.50)
        if ly_conviction <= 0.30:
            return p_fused  # ly 不够强，不触发

        def _direction(p: float) -> int:
            """概率 → 方向: 1=看多, -1=看空, 0=中性"""
            return 1 if p > 0.55 else (-1 if p < 0.45 else 0)

        ly_dir = _direction(p_ly)
        fused_dir = _direction(p_fused)

        if ly_dir == 0 or ly_dir == fused_dir:
            return p_fused  # ly中性或方向一致，不干预

        # 方向冲突: 检查各系统方向
        ml_dir = _direction(p_ml)
        at_dir = _direction(p_at)

        if ml_dir == at_dir == -ly_dir:
            return 0.40 * p_ly + 0.60 * p_fused
        elif ml_dir == ly_dir:
            return 0.80 * p_ly + 0.20 * p_fused
        elif at_dir == ly_dir:
            return 0.70 * p_ly + 0.30 * p_fused
        else:
            return p_fused

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
        tradingagent_rating: str = "Hold",
        tradingagent_valid: bool = True,
        ta_is_stale: bool = False,
        ta_debate_state: Optional[Dict[str, Any]] = None,
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
            )
            bayesian_result = self._fuse_bayesian(
                stock_code=stock_code, stock_name=stock_name,
                lynx_signal=lynx_signal, lynx_prob_up=lynx_prob_up,
                mindlynx_advice=mindlynx_advice, mindlynx_score=mindlynx_score,
                mindlynx_trend=mindlynx_trend,
                mindlynx_valid=mindlynx_valid,
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
        )

    # ──────── 原始线性融合逻辑（重命名自 fuse_single_stock） ────────

    def _fuse_linear(
        self,
        stock_code: str,
        stock_name: str = "",
        lynx_signal: str = "观望",
        lynx_prob_up: float = 50.0,
        mindlynx_advice: str = "观望",
        mindlynx_score: int = 50,
        mindlynx_trend: Optional[str] = None,
        mindlynx_valid: bool = True,
        tradingagent_rating: str = "Hold",
        tradingagent_valid: bool = True,
        ta_is_stale: bool = False,
        ta_debate_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        线性加权融合（原始逻辑）。

        包含 Oracle 建议的 3 项修正:
          ⚡ 分歧检测 + 不确定性惩罚
          ⚡ 置信度调制（lynx prob_up 自然调制，mindlynx 评分细分）
          ⚡ 缺失系统自动重分配权重
        """
        # ── Step 1: 归一化各系统 ──
        lynx_normalized, lynx_valid = self.normalizer.normalize_lynx(
            lynx_signal, lynx_prob_up
        )
        mindlynx_normalized = self.normalizer.normalize_mindlynx(
            mindlynx_advice, mindlynx_score, mindlynx_trend
        )
        tradingagent_normalized = self.normalizer.normalize_tradingagent(
            tradingagent_rating, debate_state=ta_debate_state
        )

        # 子系统有效性（来自 data_loader，不再硬编码 True）


        # ⚡ TA 数据过期处理：降权 30%，权重重新分配
        ta_stale_penalty = 0.0
        if ta_is_stale:
            ta_stale_penalty = 0.30  # TA 权重打七折
            self.logger.info(
                f"[{stock_code}] TA 数据为昨日结果，权重降低 {ta_stale_penalty*100:.0f}%"
            )

        # ── Step 2: 分歧检测 + 不确定性惩罚 (Oracle 建议 1) ──
        has_disagreement, disagreement_score = self._detect_disagreement(
            lynx_normalized, mindlynx_normalized, tradingagent_normalized,
            lynx_valid, mindlynx_valid, tradingagent_valid,
        )
        uncertainty_penalty = self._compute_uncertainty_penalty(disagreement_score)

        # ── Step 3: 处理缺失系统 + 权重重分配 (Oracle 建议 2) ──
        adjusted_weights, valid_count, is_degraded = self._compute_adjusted_weights(
            lynx_valid, mindlynx_valid, tradingagent_valid,
        )

        # ⚡ TA 数据过期：降低 TA 权重，分配给 lynx 和 mindlynx
        if ta_is_stale and "tradingagent" in adjusted_weights:
            ta_weight = adjusted_weights["tradingagent"]
            penalty = ta_weight * ta_stale_penalty
            adjusted_weights["tradingagent"] = ta_weight - penalty
            # 平均分配给其他有效系统
            others = [k for k in adjusted_weights if k != "tradingagent"]
            if others:
                split = penalty / len(others)
                for k in others:
                    adjusted_weights[k] += split

        if valid_count == 0:
            return {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "valid": False,
                "message": "所有系统均无效，无法生成信号",
                "is_degraded": True,
            }

        # ── Step 4: 计算融合得分（含置信度调制） ──
        normalized_scores = {}
        if "lynx" in adjusted_weights:
            normalized_scores["lynx"] = lynx_normalized
        if "mindlynx" in adjusted_weights:
            normalized_scores["mindlynx"] = mindlynx_normalized
        if "tradingagent" in adjusted_weights:
            normalized_scores["tradingagent"] = tradingagent_normalized

        fusion_score = sum(
            normalized_scores[sys] * adjusted_weights[sys]
            for sys in normalized_scores
        )

        # 施加不确定性惩罚 (Oracle 建议 1)
        fusion_score -= uncertainty_penalty

        # ── Step 5: 映射到最终决策 ──
        final = self._get_final_decision(fusion_score, has_disagreement)

        # ── Step 6: 构建结果 ──
        result = {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "valid": True,
            "is_degraded": is_degraded,
            "degraded_info": f"{valid_count}/3 系统有效" if is_degraded else "",
            "has_disagreement": has_disagreement,
            "disagreement_score": round(disagreement_score, 3),
            "uncertainty_penalty": round(uncertainty_penalty, 3),
            "lynx_score": round(lynx_normalized, 3),
            "lynx_valid": lynx_valid,
            "mindlynx_score": round(mindlynx_normalized, 3),
            "mindlynx_valid": mindlynx_valid,
            "tradingagent_score": round(tradingagent_normalized, 3),
            "tradingagent_valid": tradingagent_valid,
            "fusion_score": round(fusion_score, 3),
            "signal": final["signal"],
            "signal_name": final["name"],
            "position_advice": final["position"],
            "disagreement_capped": final.get("disagreement_capped", False),
            "ta_is_stale": ta_is_stale,
            "ta_stale_penalty": ta_stale_penalty,
        }

        # 记录日志
        self.logger.record_decision(
            stock_code=stock_code,
            stock_name=stock_name,
            lynx_score=lynx_normalized,
            lynx_valid=lynx_valid,
            mindlynx_score=mindlynx_normalized,
            mindlynx_valid=mindlynx_valid,
            tradingagent_score=tradingagent_normalized,
            tradingagent_valid=tradingagent_valid,
            fusion_score=fusion_score,
            final_signal=final["signal"],
            position_advice=final["position"],
            is_degraded=is_degraded,
            has_disagreement=has_disagreement,
            fusion_mode=self.fusion_mode,
        )

        return result

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
            )
            # 补充行情数据和子系统原始数据（从输入透传到结果）
            for k in ("price", "pct_chg", "volume_ratio", "ma5", "ma10", "ma20",
                      "mindlynx_trend", "mindlynx_sentiment", "mindlynx_operation",
                      "mindlynx_analysis", "mindlynx_ideal_buy", "mindlynx_stop_loss",
                      "mindlynx_take_profit"):
                if k in item:
                    result[k] = item[k]
            results.append(result)

        # 按融合得分降序排列
        results.sort(key=lambda r: (r.get("valid", False), r.get("fusion_score", 0)), reverse=True)

        return results

    def get_portfolio_summary(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """生成投资组合摘要"""
        valid_results = [r for r in results if r.get("valid", False)]

        strong_bullish = [r for r in valid_results if r["signal"] == "strong_bullish"]
        weak_bullish = [r for r in valid_results if r["signal"] in ("bullish", "cautious_bullish")]
        neutral = [r for r in valid_results if r["signal"] == "neutral"]
        weak_bearish = [r for r in valid_results if r["signal"] in ("cautious_bearish", "bearish")]
        strong_bearish = [r for r in valid_results if r["signal"] == "strong_bearish"]

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
            "degraded_count": sum(1 for r in valid_results if r.get("is_degraded", False)),
            "disagreement_count": sum(1 for r in valid_results if r.get("has_disagreement", False)),
        }
