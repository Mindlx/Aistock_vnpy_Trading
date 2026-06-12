"""Test reliability.py — 幻觉检测/置信度校准/alpha解析"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from src.reliability import ReliabilityConfig, HallucinationDetector, ConfidenceCalibrator


class TestReliabilityConfig:
    def test_base_alpha_values(self):
        assert ReliabilityConfig.BASE_ALPHA["lynx_vnpy"] == 0.75
        assert ReliabilityConfig.BASE_ALPHA["mindlynx"] == 0.65
        assert ReliabilityConfig.BASE_ALPHA["tradingagent"] == 0.40

    def test_alpha_resolution_lynx(self):
        assert ReliabilityConfig.alpha("lynx_vnpy") == 0.75
        assert ReliabilityConfig.alpha("lynx_vnpy", "000592") == 0.75

    def test_alpha_resolution_tradingagent(self):
        assert ReliabilityConfig.alpha("tradingagent") == 0.40

    def test_alpha_resolution_override(self):
        a = ReliabilityConfig.alpha("mindlynx", "000592")
        assert a == 0.80

    def test_alpha_resolution_fallback(self):
        a = ReliabilityConfig.alpha("mindlynx", "nonexistent_code")
        assert a == 0.65

    def test_default_h_values(self):
        assert ReliabilityConfig.default_h("lynx_vnpy") == 0.0
        assert ReliabilityConfig.default_h("mindlynx") == 0.15
        assert ReliabilityConfig.default_h("tradingagent") == 0.30

    def test_ly_veto_threshold(self):
        assert ReliabilityConfig.LY_VETO_THRESHOLD == 0.30


class TestHallucinationDetector:
    def test_detect_ly_always_zero(self):
        assert HallucinationDetector.detect_ly() == 0.0

    def test_detect_ml_no_hallucination(self):
        h = HallucinationDetector.detect_ml(
            sentiment_score=60, factor_baseline=55,
            operation_advice="买入", trend_prediction="看多"
        )
        assert h < 0.20

    def test_detect_ml_factor_deviation(self):
        h = HallucinationDetector.detect_ml(
            sentiment_score=60, factor_baseline=30,
            operation_advice="买入", trend_prediction="看多"
        )
        assert h == pytest.approx(0.20, abs=0.01)

    def test_detect_ml_direction_contradiction(self):
        h = HallucinationDetector.detect_ml(
            sentiment_score=60, factor_baseline=55,
            operation_advice="买入", trend_prediction="看空"
        )
        assert h == 0.30

    def test_detect_ml_cascade(self):
        h = HallucinationDetector.detect_ml(
            sentiment_score=90, factor_baseline=30,
            operation_advice="买入", trend_prediction="看空"
        )
        assert h > 0.50

    def test_detect_ml_no_baseline(self):
        h = HallucinationDetector.detect_ml(
            sentiment_score=80, operation_advice="买入", trend_prediction="看多"
        )
        assert h == 0.0

    def test_detect_at_high_agreement(self):
        h = HallucinationDetector.detect_at({
            "debate_available": True, "investment_agreement": 0.9,
            "risk_agreement": 0.8, "analyst_variance": 0.1
        })
        assert h < 0.20

    def test_detect_at_low_agreement(self):
        h = HallucinationDetector.detect_at({
            "debate_available": True, "investment_agreement": 0.3,
            "risk_agreement": 0.2, "analyst_variance": 0.8
        })
        assert h > 0.30

    def test_detect_at_empty_state(self):
        h = HallucinationDetector.detect_at({})
        assert h == 0.30


class TestConfidenceCalibrator:
    def test_calibrate_ly_high(self):
        c = ConfidenceCalibrator.calibrate_ly(prob_up=85)
        assert c == pytest.approx(0.70, abs=0.01)

    def test_calibrate_ly_mid(self):
        c = ConfidenceCalibrator.calibrate_ly(prob_up=50)
        assert c == 0.0

    def test_calibrate_ly_low(self):
        c = ConfidenceCalibrator.calibrate_ly(prob_up=15)
        assert c == pytest.approx(0.70, abs=0.01)

    def test_calibrate_ml(self):
        c = ConfidenceCalibrator.calibrate_ml(sentiment_score=80)
        # raw=0.60, min(0.85, 0.60*0.85)=0.51
        assert c == pytest.approx(0.51, abs=0.01)

    def test_calibrate_ml_capped(self):
        c = ConfidenceCalibrator.calibrate_ml(sentiment_score=100)
        # raw=1.0, min(0.85, 1.0*0.85)=0.85
        assert c == pytest.approx(0.85, abs=0.01)

    def test_calibrate_at(self):
        c = ConfidenceCalibrator.calibrate_at(debate_consistency=1.0)
        assert c == 0.50

    def test_calibrate_at_capped(self):
        c = ConfidenceCalibrator.calibrate_at(debate_consistency=2.0)
        assert c == 0.50

    def test_calibrate_at_zero(self):
        c = ConfidenceCalibrator.calibrate_at(debate_consistency=0.0)
        assert c == 0.0
