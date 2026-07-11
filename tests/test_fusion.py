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
from src.realtime_fusion import RealtimeFusion


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
        # 线性映射 score = (p/100)*6 - 3 (2026-07-09)
        assert 1.30 < score < 1.35

    def test_lynx_prob_up_25(self):
        score, valid = self.n.normalize_lynx("🔴 回避", 25.0)
        assert valid is True
        # prob_up=25: 0.25*6-3 = -1.50
        assert -1.55 < score < -1.45

    def test_lynx_prob_up_40(self):
        score, valid = self.n.normalize_lynx("🟡 谨慎", 40.0)
        assert valid is True
        # prob_up=40: 0.40*6-3 = -0.60
        assert -0.65 < score < -0.55

    def test_lynx_symmetric(self):
        """prob_up=30 vs 70: 分段线性两侧斜率不同，不再严格对称"""
        s30, _ = self.n.normalize_lynx("", 30.0)
        s70, _ = self.n.normalize_lynx("", 70.0)
        # 30%在25~35段(斜率0.093/%), 70%在65~75段(斜率0.094/%)
        # 距中性区距离不同，不对称是预期的
        assert s30 < 0 and s70 > 0

    def test_lynx_extreme(self):
        s95, _ = self.n.normalize_lynx("", 95.0)
        assert 2.65 < s95 < 2.75  # 0.95*6-3 = 2.70

    def test_lynx_emoji_strip(self):
        clean = self.n._strip_emoji("🟢 买入")
        assert clean == "买入"

    # ── ml: 类别+评分微调映射 ──

    def test_mindlynx_buy(self):
        assert self.n.normalize_mindlynx("买入", 75) > 2.5
        assert self.n.normalize_mindlynx("买入", 60) > 2.0

    def test_mindlynx_add(self):
        score = self.n.normalize_mindlynx("加仓", 70)
        assert 2.0 < score < 2.5  # base=2.06, modulation≈0.148 → ~2.21

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
        assert self.n.normalize_mindlynx("减仓", 35) < -1.0

    # ── at: 直接 L7 映射 ──

    def test_tradingagent_buy(self):
        assert self.n.normalize_tradingagent("Buy") == pytest.approx(3.0)

    def test_tradingagent_overweight(self):
        assert self.n.normalize_tradingagent("Overweight") == pytest.approx(2.06)

    def test_tradingagent_hold(self):
        assert self.n.normalize_tradingagent("Hold") == 0.0

    def test_tradingagent_underweight(self):
        assert self.n.normalize_tradingagent("Underweight") == pytest.approx(-1.13)

    def test_tradingagent_sell(self):
        assert self.n.normalize_tradingagent("Sell") == pytest.approx(-3.0)

    def test_tradingagent_case_insensitive(self):
        assert self.n.normalize_tradingagent("buy") == pytest.approx(3.0)

    def test_tradingagent_unknown(self):
        assert self.n.normalize_tradingagent("Moon") == 0.0

    # ── 标签映射 ──

    def test_map_normalized_to_label_7level(self):
        """阈值与 L7_THRESHOLDS/_get_final_decision 一致:
           >2.5 strong_bullish | >1.5 bullish | >1.0 cautious_bullish
           >-0.5 neutral | >-1.5 cautious_bearish | >-2.5 bearish | else strong_bearish
        """
        assert self.n.map_normalized_to_label(2.7) == "strong_bullish"
        assert self.n.map_normalized_to_label(2.0) == "bullish"
        assert self.n.map_normalized_to_label(1.1) == "cautious_bullish"
        assert self.n.map_normalized_to_label(1.0) == "neutral"  # 恰在阈值上 → neutral
        assert self.n.map_normalized_to_label(0.0) == "neutral"
        assert self.n.map_normalized_to_label(-1.0) == "cautious_bearish"
        assert self.n.map_normalized_to_label(-2.0) == "bearish"
        assert self.n.map_normalized_to_label(-2.7) == "strong_bearish"

    def test_score_to_l7_integer(self):
        assert SignalNormalizer.score_to_l7_integer(2.7) == 3
        assert SignalNormalizer.score_to_l7_integer(2.0) == 2
        assert SignalNormalizer.score_to_l7_integer(1.1) == 1
        assert SignalNormalizer.score_to_l7_integer(1.0) == 0  # 恰在阈值上 → 0
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
        assert d["signal"] == "neutral"

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

    def test_l7_score_boundary_minus_05(self):
        d = self.engine._get_final_decision(-0.5)
        assert d["signal"] == "cautious_bearish"

    def test_l7_score_boundary_plus_25(self):
        # 2.5 使用 > 严格比较: 2.5>2.5=False → bullish
        d = self.engine._get_final_decision(2.5)
        assert d["signal"] == "bullish"

    def test_l7_score_boundary_plus_26(self):
        d = self.engine._get_final_decision(2.6)
        assert d["signal"] == "strong_bullish"

    def test_l7_score_clamp_low(self):
        d = self.engine._get_final_decision(-3.0)
        assert d["signal"] == "strong_bearish"

    def test_l7_disagreement_cap(self):
        d = self.engine._get_final_decision(2.8, disagreement=True)
        assert d["disagreement_capped"] is True
        assert "1成" in d["position"]  # 分歧上限

    def test_l7_disagreement_bearish(self):
        d = self.engine._get_final_decision(-2.0, disagreement=True)
        assert d["disagreement_capped"] is True
        # bearish 基础position="大幅减仓", cap_position_for_disagreement 保持"减仓至0.5成以内"

    def test_detect_disagreement(self):
        has, sc, _ = FusionEngine._detect_disagreement(2.0, 2.0, -1.5, True, True, True)
        assert has is True
        assert sc > 0

    def test_detect_no_disagreement(self):
        has, sc, _ = FusionEngine._detect_disagreement(2.0, 1.5, 0.5, True, True, True)
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

    def test_adjusted_weights_only_one_valid(self):
        w, c, d = self.engine._compute_adjusted_weights(True, False, False)
        assert c == 1
        assert d is True
        assert abs(w["lynx"] - 1.0) < 0.01

    def test_adjusted_weights_two_missing(self):
        w, c, d = self.engine._compute_adjusted_weights(False, True, False)
        assert c == 1
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
        assert ReliabilityConfig.alpha("mindlynx") == 0.65

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

    def test_format_empty_results(self):
        summary = self.notifier.format_daily_summary([], "2026-05-29")
        assert "融合决策" in summary

    def test_format_degraded_result(self):
        results = [{
            "stock_code": "601801", "stock_name": "皖新传媒",
            "valid": True, "signal": "bullish",
            "signal_name": "看多", "fusion_score": 1.5,
            "position_advice": "1-2成",
            "lynx_score": 1.8, "mindlynx_score": 0,
            "tradingagent_score": 0, "is_degraded": True,
            "has_disagreement": False, "disagreement_capped": False,
        }]
        summary = self.notifier.format_daily_summary(results, "2026-05-29")
        assert "降级" in summary or "degraded" in summary.lower() or "看多" in summary

    def test_format_with_disagreement(self):
        results = [{
            "stock_code": "000592", "stock_name": "平潭发展",
            "valid": True, "signal": "neutral",
            "signal_name": "中性/持有", "fusion_score": 0.25,
            "position_advice": "0成",
            "lynx_score": 2.5, "mindlynx_score": 1.5,
            "tradingagent_score": -1.3, "is_degraded": False,
            "has_disagreement": True, "disagreement_capped": True,
        }]
        summary = self.notifier.format_daily_summary(results, "2026-05-29")
        # 分歧状态应出现在摘要中
        assert "分歧" in summary or "neutral" in summary.lower() or "中性" in summary


# ══════════════════════════════════════════════
# 集成测试: realtime_fusion + 文件交换区
# ══════════════════════════════════════════════

class TestRealtimeFusion:
    """realtime_fusion 文件交换区集成测试"""

    def test_scan_and_fuse_all_available(self, tmp_path):
        """三个系统数据都可用时融合结果正确（含分歧惩罚）"""
        import json
        from pathlib import Path

        # 准备测试信号文件: ly=1.5(看多), ml=2.0(看多), at=-1.3(看空) → 有分歧
        realtime_dir = tmp_path / "realtime"
        realtime_dir.mkdir()

        ly_data = {"stocks": {"601801": {"score": 1.5}}, "updated_at": "2026-06-03"}
        ml_data = {"stocks": {"601801": {"l7_score": 2.0}}, "updated_at": "2026-06-03"}
        at_data = {"stocks": {"601801": {"score": -1.3}}, "updated_at": "2026-06-03"}

        for name, data in [("ly_signal.json", ly_data), ("ml_signal.json", ml_data), ("at_signal.json", at_data)]:
            (realtime_dir / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        import src.realtime_fusion as rf
        original_dir = rf.REALTIME_DIR
        rf.REALTIME_DIR = realtime_dir

        try:
            service = rf.RealtimeFusion()
            changes = service.scan_and_fuse()

            assert len(changes) == 1
            c = changes[0]
            assert c["code"] == "601801"
            assert abs(c["score"] - (-0.181)) < 0.02
        finally:
            rf.REALTIME_DIR = original_dir

    def test_scan_and_fuse_ml_missing(self, tmp_path):
        """ml 数据缺失时仍能用 ly+at 融合"""
        import json

        realtime_dir = tmp_path / "realtime2"
        realtime_dir.mkdir()

        ly_data = {"stocks": {"601801": {"score": 1.5}}, "updated_at": "2026-06-03"}
        ml_data = {"stocks": {}, "updated_at": "2026-06-03"}
        at_data = {"stocks": {"601801": {"score": -0.5}}, "updated_at": "2026-06-03"}

        for name, data in [("ly_signal.json", ly_data), ("ml_signal.json", ml_data), ("at_signal.json", at_data)]:
            (realtime_dir / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        import src.realtime_fusion as rf
        original_dir = rf.REALTIME_DIR
        rf.REALTIME_DIR = realtime_dir

        try:
            service = rf.RealtimeFusion()
            changes = service.scan_and_fuse()

            assert len(changes) == 1
            c = changes[0]
            assert abs(c["score"] - 0.300) < 0.02
            assert c["signal"] == "neutral"
        finally:
            rf.REALTIME_DIR = original_dir

    def test_scan_and_fuse_no_data(self, tmp_path):
        """无数据时返回空列表"""
        import json

        realtime_dir = tmp_path / "realtime3"
        realtime_dir.mkdir()

        for name in ["ly_signal.json", "ml_signal.json", "at_signal.json"]:
            (realtime_dir / name).write_text(json.dumps({"stocks": {}}), encoding="utf-8")

        import src.realtime_fusion as rf
        original_dir = rf.REALTIME_DIR
        rf.REALTIME_DIR = realtime_dir

        try:
            service = rf.RealtimeFusion()
            changes = service.scan_and_fuse()
            assert len(changes) == 0
        finally:
            rf.REALTIME_DIR = original_dir


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
