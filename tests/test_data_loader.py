"""Test data_loader.py — 解析逻辑/路径模式/文件加载"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import pytest
from src.data_loader import (
    MindLynxDataLoader, TradingAgentDataLoader,
    MLFactorLoader, Alpha158Loader,
)


class TestMindLynxMarkdownParsing:
    """MindLynx 报告纯解析逻辑测试 — 不依赖文件系统"""

    @pytest.fixture
    def loader(self):
        return MindLynxDataLoader(reports_dir="/tmp/nonexistent")

    def test_extract_signals_happy(self, loader):
        content = """🟢 **皖新传媒(601801)**: ¥5.83 +2.6% | 买入 | 评分 72 | 看多
🔴 *ST网达(603189)**: ¥3.21 -1.5% | 卖出 | 评分 34 | 强烈看空"""
        signals = loader._extract_signals_from_markdown(content)
        assert "601801" in signals
        assert signals["601801"]["signal"] == "买入"
        assert signals["601801"]["score"] == 72
        assert "603189" in signals
        assert signals["603189"]["signal"] == "卖出"

    def test_extract_signals_empty(self, loader):
        assert loader._extract_signals_from_markdown("") == {}

    def test_extract_signals_no_match(self, loader):
        assert loader._extract_signals_from_markdown("普通文本 without signals") == {}

    def test_get_latest_available_date(self, tmp_path):
        loader = MindLynxDataLoader(reports_dir=str(tmp_path))
        (tmp_path / "report_20260529.md").write_text("dummy")
        (tmp_path / "report_2026-06-01.md").write_text("dummy")
        date = loader.get_latest_available_date()
        assert date == "2026-06-01"  # 按实际日期排序

    def test_load_market_review(self, tmp_path):
        loader = MindLynxDataLoader(reports_dir=str(tmp_path))
        (tmp_path / "market_review_2026-05-29_全天.md").write_text("大盘复盘内容")
        result = loader.load_market_review("2026-05-29")
        assert result == "大盘复盘内容"

    def test_load_market_review_not_found(self, tmp_path):
        loader = MindLynxDataLoader(reports_dir=str(tmp_path))
        assert loader.load_market_review("2099-01-01") is None


class TestTradingAgentPathParsing:
    def test_ticker_variants(self):
        v = TradingAgentDataLoader._ticker_variants("601801")
        assert v == ["601801", "601801.SS", "601801.SZ"]

    def test_ticker_variants_with_suffix(self):
        v = TradingAgentDataLoader._ticker_variants("601801.SS")
        assert v == ["601801.SS"]

    def test_build_path_patterns(self):
        patterns = TradingAgentDataLoader()._build_path_patterns("601801", "2026-05-29")
        assert len(patterns) == 6  # 3 tickers × 2 date formats
        assert all(str(p).endswith(".json") for p in patterns)

    def test_extract_decision_hold_missing(self):
        """缺少final_trade_decision → None"""
        d = TradingAgentDataLoader._extract_decision({})
        assert d is None

    def test_extract_decision_buy(self):
        state = {
            "final_trade_decision": "**Rating**: Buy\n**Price Target**: 15.5"
        }
        d = TradingAgentDataLoader._extract_decision(state)
        assert d is not None
        assert d["rating"] == "Buy"
        assert d["price_target"] == 15.5
        assert d["debate_state"] is not None

    def test_extract_decision_overweight(self):
        state = {
            "final_trade_decision": "**Rating**: Overweight"
        }
        d = TradingAgentDataLoader._extract_decision(state)
        assert d["rating"] == "Overweight"


class TestTradingAgentDebateParsing:
    def test_parse_debate_empty(self):
        r = TradingAgentDataLoader._parse_debate_state({})
        assert r["debate_available"] is False
        assert r["investment_agreement"] == 0.5

    def test_parse_debate_bullish_investment(self):
        state = {"investment_debate_state": "bullish看好买入 upside"}
        r = TradingAgentDataLoader._parse_debate_state(state)
        assert r["debate_available"] is True
        # 公式: 1 - |bull-bear|/(bull+bear+1); bull=3 bear=0 → 1-3/4=0.25
        assert r["investment_agreement"] == 0.25

    def test_parse_debate_risk_conservative(self):
        state = {"risk_debate_state": "保守保守 conservative low risk"}
        r = TradingAgentDataLoader._parse_debate_state(state)
        assert r["debate_available"] is True
        assert r["risk_agreement"] > 0.5

    def test_parse_debate_analyst_unanimous(self):
        state = {
            "analyst_1": "bullish 看多 buy",
            "analyst_2": "看多 positive",
        }
        r = TradingAgentDataLoader._parse_debate_state(state)
        assert r["analyst_variance"] == 0.1

    def test_parse_debate_analyst_split(self):
        state = {
            "analyst_1": "bullish 看多 buy",
            "analyst_2": "bearish 看空 sell",
        }
        r = TradingAgentDataLoader._parse_debate_state(state)
        assert r["analyst_variance"] == 0.5


class TestMLFactorLoader:
    def test_load_from_json(self, tmp_path):
        path = tmp_path / "ml_signal.json"
        path.write_text(json.dumps({
            "stocks": {
                "601801": {"l7_score": 2.0, "composite_score": 75, "composite_label": "看多"},
                "603189": {"l7_score": -1.0, "composite_score": 35, "composite_label": "谨慎"},
            }
        }))
        loader = MLFactorLoader(signal_path=str(path))
        result = loader.load_by_date()
        assert "601801" in result
        assert result["601801"]["ml_factor_l7"] == 2.0
        assert result["603189"]["ml_factor_l7"] == -1.0

    def test_load_missing_file(self, tmp_path):
        loader = MLFactorLoader(signal_path=str(tmp_path / "nonexistent.json"))
        assert loader.load_by_date() == {}


class TestAlpha158Loader:
    def test_load_from_json(self, tmp_path):
        path = tmp_path / "alpha158_signal.json"
        path.write_text(json.dumps({
            "stocks": {
                "601801": {"l7_score": 2.5, "prob_up": 72.0},
            }
        }))
        loader = Alpha158Loader(signal_path=str(path))
        result = loader.load_by_date()
        assert result["601801"]["alpha158_l7"] == 2.5
        assert result["601801"]["alpha158_prob_up"] == 72.0

    def test_load_missing_file(self, tmp_path):
        loader = Alpha158Loader(signal_path=str(tmp_path / "nonexistent.json"))
        assert loader.load_by_date() == {}
