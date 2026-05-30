"""
三系统融合引擎单元测试 — v3.0 7 级语义对齐

⚠️ 仅供学习和研究目的，不构成任何投资建议
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_loader import (
    LynxDataLoader,
    MindLynxDataLoader,
    TradingAgentDataLoader,
)
from src.fusion_engine import FusionEngine
from src.normalizer import SignalNormalizer
from src.reliability import (
    ConfidenceCalibrator,
    HallucinationDetector,
    ReliabilityConfig,
    score_to_probability,
    probability_to_decision,
)
from src.wecom_notifier import WeComNotifier


MINIMAL_CONFIG = {
    "weights": {"lynx_vnpy": 0.35, "mindlynx": 0.35, "tradingagent": 0.30},
    "logging": {"level": "INFO", "retention_days": 90},
}


# ══════════════════════════════════════════
# v3.0 Normalizer 测试 — 7 级语义对齐
# ══════════════════════════════════════════


class TestSignalNormalizer:
    def setup_method(self):
        self.n = SignalNormalizer()

    # ── ly: logit+tanh 连续映射 ──

    def test_lynx_prob_up_50_neutral(self):
        score, valid = self.n.normalize_lynx("⚪ 观望", 50.0)
        assert valid is True
        assert abs(score) < 0.01

    def test_lynx_prob_up_72(self):
        score, valid = self.n.normalize_lynx("🟢 买入", 72.0)
        assert valid is True
        assert 1.3 < score < 1.4

    def test_lynx_prob_up_25(self):
        score, valid = self.n.normalize_lynx("🔴 回避", 25.0)
        assert valid is True
        assert -1.51 < score < -1.48

    def test_lynx_prob_up_40(self):
        score, valid = self.n.normalize_lynx("🟡 谨慎", 40.0)
        assert valid is True
        assert -0.65 < score < -0.55

    def test_lynx_symmetric(self):
        """prob_up=30 vs 70 应大致对称"""
        s30, _ = self.n.normalize_lynx("", 30.0)
        s70, _ = self.n.normalize_lynx("", 70.0)
        assert abs(s30 + s70) < 0.05

    def test_lynx_extreme(self):
        s95, _ = self.n.normalize_lynx("", 95.0)
        assert s95 < 3.0  # 永远不会饱和到+3

    def test_lynx_emoji_strip(self):
        clean = self.n._strip_emoji("🟢 买入")
        assert clean == "买入"

    # ── ml: 类别+评分微调映射 ──

    def test_mindlynx_buy(self):
        assert self.n.normalize_mindlynx("买入", 75) > 2.0
        assert self.n.normalize_mindlynx("买入", 60) > 2.0

    def test_mindlynx_add(self):
        assert 1.0 < self.n.normalize_mindlynx("加仓", 70) < 2.0

    def test_mindlynx_hold_neutral(self):
        """持有 → L7=0"""
        score = self.n.normalize_mindlynx("持有", 65)
        assert abs(score) < 0.2  # 即使高评分也在中性带

    def test_mindlynx_hold_low_score(self):
        score = self.n.normalize_mindlynx("持有", 30)
        assert abs(score) < 0.2  # 低评分也在中性带

    def test_mindlynx_watch(self):
        assert abs(self.n.normalize_mindlynx("观望", 50)) < 0.2

    def test_mindlynx_sell(self):
        assert self.n.normalize_mindlynx("卖出", 30) < -1.5

    def test_mindlynx_reduce(self):
        assert self.n.normalize_mindlynx("减仓", 50) < -1.0

    # ── at: 直接 L7 映射 ──

    def test_tradingagent_buy(self):
        assert self.n.normalize_tradingagent("Buy") == pytest.approx(2.3)

    def test_tradingagent_overweight(self):
        assert self.n.normalize_tradingagent("Overweight") == pytest.approx(1.3)

    def test_tradingagent_hold(self):
        assert self.n.normalize_tradingagent("Hold") == 0.0

    def test_tradingagent_underweight(self):
        assert self.n.normalize_tradingagent("Underweight") == pytest.approx(-1.3)

    def test_tradingagent_sell(self):
        assert self.n.normalize_tradingagent("Sell") == pytest.approx(-2.3)

    def test_tradingagent_case_insensitive(self):
        assert self.n.normalize_tradingagent("buy") == pytest.approx(2.3)

    def test_tradingagent_unknown(self):
        assert self.n.normalize_tradingagent("Moon") == 0.0

    # ── 标签映射 ──

    def test_map_normalized_to_label_7level(self):
        assert self.n.map_normalized_to_label(2.7) == "strong_bullish"
        assert self.n.map_normalized_to_label(2.0) == "bullish"
        assert self.n.map_normalized_to_label(1.0) == "cautious_bullish"
        assert self.n.map_normalized_to_label(0.0) == "neutral"
        assert self.n.map_normalized_to_label(-1.0) == "cautious_bearish"
        assert self.n.map_normalized_to_label(-2.0) == "bearish"
        assert self.n.map_normalized_to_label(-2.7) == "strong_bearish"

    def test_score_to_l7_integer(self):
        assert SignalNormalizer.score_to_l7_integer(2.7) == 3
        assert SignalNormalizer.score_to_l7_integer(2.0) == 2
        assert SignalNormalizer.score_to_l7_integer(1.0) == 1
        assert SignalNormalizer.score_to_l7_integer(0.0) == 0
        assert SignalNormalizer.score_to_l7_integer(-1.0) == -1
        assert SignalNormalizer.score_to_l7_integer(-2.0) == -2
        assert SignalNormalizer.score_to_l7_integer(-2.7) == -3


# ══════════════════════════════════════════
# FusionEngine 测试 — 7 级决策
# ══════════════════════════════════════════


class TestFusionEngine:
    def setup_method(self):
        self.engine = FusionEngine("config/settings.yaml")

    def test_l7_strong_bullish(self):
        d = self.engine._get_final_decision(2.8)
        assert d["signal"] == "strong_bullish"
        assert "2-3成" in d["position"]

    def test_l7_bullish(self):
        d = self.engine._get_final_decision(2.0)
        assert d["signal"] == "bullish"
        assert "1-2成" in d["position"]

    def test_l7_cautious_bullish(self):
        d = self.engine._get_final_decision(1.0)
        assert d["signal"] == "cautious_bullish"

    def test_l7_neutral(self):
        d = self.engine._get_final_decision(0.0)
        assert d["signal"] == "neutral"
        assert "0成" in d["position"]

    def test_l7_cautious_bearish(self):
        d = self.engine._get_final_decision(-1.0)
        assert d["signal"] == "cautious_bearish"

    def test_l7_bearish(self):
        d = self.engine._get_final_decision(-2.0)
        assert d["signal"] == "bearish"

    def test_l7_strong_bearish(self):
        d = self.engine._get_final_decision(-2.8)
        assert d["signal"] == "strong_bearish"
        assert "清仓" in d["position"]

    def test_l7_disagreement_cap(self):
        d = self.engine._get_final_decision(2.8, disagreement=True)
        assert d["disagreement_capped"] is True
        assert "1成" in d["position"]  # 分歧上限

    def test_detect_disagreement(self):
        has, sc = FusionEngine._detect_disagreement(2.0, 2.0, -1.5, True, True, True)
        assert has is True
        assert sc > 0

    def test_detect_no_disagreement(self):
        has, sc = FusionEngine._detect_disagreement(2.0, 1.5, 0.5, True, True, True)
        assert has is False
        assert sc == 0.0

    def test_adjusted_weights_all_valid(self):
        w, c, d = self.engine._compute_adjusted_weights(True, True, True)
        assert abs(sum(w.values()) - 1.0) < 0.01
        assert c == 3
        assert d is False

    def test_adjusted_weights_one_missing(self):
        w, c, d = self.engine._compute_adjusted_weights(True, False, True)
        assert abs(sum(w.values()) - 1.0) < 0.01
        assert c == 2
        assert d is True

    def test_all_systems_invalid(self):
        w, c, d = self.engine._compute_adjusted_weights(False, False, False)
        assert c == 0
        assert d is True


# ══════════════════════════════════════════
# 概率映射测试
# ══════════════════════════════════════════


class TestProbabilityMapping:
    def test_score_zero_to_prob(self):
        assert SignalNormalizer.to_probability(0.0) == 0.5

    def test_score_positive_to_prob(self):
        p = SignalNormalizer.to_probability(2.0)
        assert 0.87 < p < 0.89

    def test_score_negative_to_prob(self):
        p = SignalNormalizer.to_probability(-2.0)
        assert 0.11 < p < 0.13

    def test_score_extreme(self):
        p = SignalNormalizer.to_probability(3.0)
        assert p > 0.93

    def test_custom_k(self):
        p1 = SignalNormalizer.to_probability(1.0, k=0.5)
        p2 = SignalNormalizer.to_probability(1.0, k=2.0)
        assert p1 > 0.5
        assert p2 > p1

    def test_prob_to_decision_7level(self):
        thresholds = {
            "strong_bullish": 0.88, "bullish": 0.73,
            "cautious_bullish": 0.62, "cautious_bearish": 0.38,
            "bearish": 0.27, "strong_bearish": 0.12,
        }
        assert probability_to_decision(0.90, thresholds) == "strong_bullish"
        assert probability_to_decision(0.80, thresholds) == "bullish"
        assert probability_to_decision(0.65, thresholds) == "cautious_bullish"
        assert probability_to_decision(0.50, thresholds) == "neutral"
        assert probability_to_decision(0.30, thresholds) == "cautious_bearish"
        assert probability_to_decision(0.20, thresholds) == "bearish"
        assert probability_to_decision(0.05, thresholds) == "strong_bearish"


# ══════════════════════════════════════════
# 可靠性配置测试
# ══════════════════════════════════════════


class TestReliability:
    def test_alpha_ly(self):
        assert ReliabilityConfig.alpha("lynx_vnpy") == 0.75

    def test_alpha_ml(self):
        assert ReliabilityConfig.alpha("mindlynx") == 0.55

    def test_alpha_at(self):
        assert ReliabilityConfig.alpha("tradingagent") == 0.40

    def test_calibrate_ly(self):
        assert ConfidenceCalibrator.calibrate_ly(85.0) == 0.70
        assert ConfidenceCalibrator.calibrate_ly(50.0) == 0.0

    def test_ly_no_hallucination(self):
        assert HallucinationDetector.detect_ly() == 0.0

    def test_at_default_h(self):
        assert HallucinationDetector.detect_at() == 0.30

    def test_at_debate_low_hallucination(self):
        h = HallucinationDetector.detect_at(debate_state={
            "debate_available": True,
            "investment_agreement": 0.9,
            "risk_agreement": 0.8,
            "analyst_variance": 0.1,
        })
        assert h < 0.20


# ══════════════════════════════════════════
# 否决权规则测试
# ══════════════════════════════════════════


class TestOverrideRules:
    def test_no_override_same_dir(self):
        assert FusionEngine._apply_bayesian_override(0.55, 0.60, 0.65, 0.62) == 0.62

    def test_override_ly_ml_vs_at(self):
        p = FusionEngine._apply_bayesian_override(0.85, 0.60, 0.20, 0.45)
        expected = 0.80 * 0.85 + 0.20 * 0.45
        assert abs(p - expected) < 0.01

    def test_override_2v1_against_ly(self):
        p = FusionEngine._apply_bayesian_override(0.15, 0.60, 0.70, 0.55)
        expected = 0.40 * 0.15 + 0.60 * 0.55
        assert abs(p - expected) < 0.01


# ══════════════════════════════════════════
# 数据加载器测试
# ══════════════════════════════════════════


class TestDataLoaders:
    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_lynx_loader_nonexistent(self):
        loader = LynxDataLoader(str(self.tmpdir / "lynx"))
        data = loader.load_by_date("2026-05-29")
        assert data == {}

    def test_tradingagent_loader_nonexistent(self):
        loader = TradingAgentDataLoader(str(self.tmpdir / "ta"))
        result = loader.load_by_stock_and_date("601801", "2026-05-29")
        assert result is None

    def test_tradingagent_extract_decision(self):
        state = {"final_trade_decision": "**Rating**: Buy\n**Price Target**: 15.5"}
        result = TradingAgentDataLoader._extract_decision(state)
        assert result["rating"] == "Buy"
        assert "debate_state" in result

    def test_tradingagent_debate_parsing(self):
        state = {"investment_debate_state": "bullish bullish buy buy"}
        result = TradingAgentDataLoader._parse_debate_state(state)
        assert result["debate_available"] is True

    def test_tradingagent_debate_defaults(self):
        result = TradingAgentDataLoader._parse_debate_state({})
        assert result["debate_available"] is False
        assert result["investment_agreement"] == 0.5


# ══════════════════════════════════════════
# WeCom 推送测试
# ══════════════════════════════════════════


class TestWeComNotifier:
    def setup_method(self):
        self.notifier = WeComNotifier(
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
            enabled=False,
        )

    def test_disabled_push(self):
        assert self.notifier.send_markdown("测试") is None

    def test_format_daily_summary(self):
        results = [{
            "stock_code": "601801", "stock_name": "皖新传媒",
            "valid": True, "signal": "strong_bullish",
            "signal_name": "强烈看多", "fusion_score": 2.7,
            "position_advice": "2-3成",
            "lynx_score": 1.8, "mindlynx_score": 2.0,
            "tradingagent_score": 2.3, "is_degraded": False,
            "has_disagreement": False, "disagreement_capped": False,
        }]
        summary = self.notifier.format_daily_summary(results, "2026-05-29")
        assert "强烈看多" in summary


# ══════════════════════════════════════════
# 集成测试
# ══════════════════════════════════════════


def test_fusion_accepts_7level_scores():
    """验证融合引擎接受 L7 范围得分"""
    engine = FusionEngine("config/settings.yaml")
    result = engine.fuse_single_stock(
        "601801", "皖新传媒",
        lynx_signal="🟢 买入", lynx_prob_up=75.0,
        mindlynx_advice="买入", mindlynx_score=80,
        tradingagent_rating="Buy",
    )
    assert result["valid"] is True
    # L7 范围应在 -3~+3
    assert -3 <= result["fusion_score"] <= 3


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
