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
from src.normalizer import SignalNormalizer


class FusionEngine:
    """三系统信号融合引擎"""

    # 仓位上限（分歧情况下硬性限制）
    MAX_POSITION_DISAGREEMENT = "1成"

    def __init__(self, config_path: str = "config/settings.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        self.weights = self.config["weights"]
        self.thresholds = self.config["thresholds"]
        self.confidence_thresholds = self.config.get("confidence_thresholds", {})
        self.logger = FusionLogger(
            log_dir=self.config.get("logging", {}).get("log_dir", "config/logs"),
            retention_days=self.config.get("logging", {}).get("retention_days", 90),
        )
        self.normalizer = SignalNormalizer()

        # 系统名称列表（用于权重迭代）
        self.systems = ["lynx_vnpy", "mindlynx", "tradingagent"]

    # ──────── 决策映射 ────────

    def _get_final_decision(self, score: float, disagreement: bool = False) -> Dict[str, Any]:
        """
        根据融合得分判断最终决策。

        当检测到分歧时，无论分数如何，仓位上限为 1成。
        """
        t = self.thresholds

        if score > t["strong_bullish"]:
            return {
                "signal": "strong_bullish",
                "name": "强烈看多",
                "position": "2-3成",
                "disagreement_capped": disagreement,
            }
        elif score > t["weak_bullish"]:
            return {
                "signal": "weak_bullish",
                "name": "弱看多",
                "position": "0.5-1成",
                "disagreement_capped": disagreement,
            }
        elif score > t["neutral_low"]:
            return {
                "signal": "neutral",
                "name": "中性/观望",
                "position": "0成",
                "disagreement_capped": disagreement,
            }
        elif score > t["weak_bearish"]:
            return {
                "signal": "weak_bearish",
                "name": "弱看空",
                "position": "减仓至0.5成以内",
                "disagreement_capped": disagreement,
            }
        else:
            return {
                "signal": "strong_bearish",
                "name": "强烈看空",
                "position": "清仓",
                "disagreement_capped": disagreement,
            }

    @staticmethod
    def _cap_position_for_disagreement(position: str) -> str:
        """分歧状态下限制仓位上限"""
        # 只保留不超过 1成的仓位
        if "2-3" in position or "0.5-1" in position:
            return FusionEngine.MAX_POSITION_DISAGREEMENT
        if "0.5成以内" in position or "减仓" in position:
            return "减仓至0.5成以内"
        if "清仓" in position or "0成" in position:
            return position  # 原本就是减仓/清仓，无需改变
        return "0.5成以内"

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
        has_bullish = any(s > 0.1 for s in scores)    # 正阈值避免微噪
        has_bearish = any(s < -0.1 for s in scores)

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
        0.0 ≤ penalty ≤ 0.6，分歧越大 = 越大的惩罚（从融合得分中扣减）
        """
        # 无分歧 → 不惩罚
        if disagreement_score <= 0.1:
            return 0.0

        # 线性映射：0.1 std → 0.1 惩罚, 0.8+ std → 0.6 惩罚
        penalty = min(0.6, max(0.0, (disagreement_score - 0.1) * 0.85))
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

    def fuse_single_stock(
        self,
        stock_code: str,
        stock_name: str = "",
        lynx_signal: str = "观望",
        lynx_prob_up: float = 50.0,
        mindlynx_advice: str = "观望",
        mindlynx_score: int = 50,
        mindlynx_trend: Optional[str] = None,
        tradingagent_rating: str = "Hold",
    ) -> Dict[str, Any]:
        """
        对单只股票进行融合分析。

        Oracle 建议融入:
          ⚡ 分歧检测 + 不确定性惩罚
          ⚡ 置信度调制（lynx prob_up 已自然调制，mindlynx 用评分细分）
          ⚡ 缺失系统自动重分配权重

        参数:
            stock_code: 股票代码
            stock_name: 股票名称
            lynx_signal: lynx_vnpy 原始信号（如 "🟢 买入"）
            lynx_prob_up: lynx_vnpy 上涨概率（0-100）
            mindlynx_advice: MindLynx 操作建议（如 "持有"）
            mindlynx_score: MindLynx 评分（0-100）
            mindlynx_trend: MindLynx 趋势预测（可选）
            tradingagent_rating: TradingAgent 评级（Buy/Overweight/Hold/Underweight/Sell）

        返回:
            融合决策字典
        """
        # ── Step 1: 归一化各系统 ──
        lynx_normalized, lynx_valid = self.normalizer.normalize_lynx(
            lynx_signal, lynx_prob_up
        )
        mindlynx_normalized = self.normalizer.normalize_mindlynx(
            mindlynx_advice, mindlynx_score, mindlynx_trend
        )
        tradingagent_normalized = self.normalizer.normalize_tradingagent(
            tradingagent_rating
        )

        # MindLynx 和 TradingAgent 目前总是有效（基于设计文档假设）
        mindlynx_valid = True
        tradingagent_valid = True

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
            "tradingagent_score": round(tradingagent_normalized, 3),
            "fusion_score": round(fusion_score, 3),
            "signal": final["signal"],
            "signal_name": final["name"],
            "position_advice": final["position"],
            "disagreement_capped": final.get("disagreement_capped", False),
        }

        # 记录日志
        self.logger.record_decision(
            stock_code=stock_code,
            stock_name=stock_name,
            lynx_score=lynx_normalized,
            lynx_valid=lynx_valid,
            mindlynx_score=mindlynx_normalized,
            tradingagent_score=tradingagent_normalized,
            fusion_score=fusion_score,
            final_signal=final["signal"],
            position_advice=final["position"],
        )

        return result

    def fuse_stock_pool(
        self, stock_signals: List[Dict[str, Any]]
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
                tradingagent_rating=item.get("tradingagent_rating", "Hold"),
            )
            results.append(result)

        # 按融合得分降序排列
        results.sort(key=lambda r: (r.get("valid", False), r.get("fusion_score", 0)), reverse=True)

        return results

    def get_portfolio_summary(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """生成投资组合摘要"""
        valid_results = [r for r in results if r.get("valid", False)]

        strong_bullish = [r for r in valid_results if r["signal"] == "strong_bullish"]
        weak_bullish = [r for r in valid_results if r["signal"] == "weak_bullish"]
        neutral = [r for r in valid_results if r["signal"] == "neutral"]
        weak_bearish = [r for r in valid_results if r["signal"] == "weak_bearish"]
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
