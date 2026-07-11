from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.context_preparer import ContextPreparer


class TestContextPreparer:
    def setup_method(self):
        self.cp = ContextPreparer()

    def test_build_injection_payload_format(self):
        context: Dict[str, str] = {
            "ly_signals_context": "| 综合上涨概率 | 65.0% |",
            "ml_factor_context": "综合评分=72",
            "market_context": "**OHLCV:** 10.0",
            "fundamentals_context": "**基本面:** PE=15",
            "sentiment_context": "**ML分析:** 建议=buy",
            "news_context": "**近期新闻:** 利好",
        }
        sys_msg, ai_msg = self.cp.build_injection_payload(context)
        assert "[系统注入]" in sys_msg
        assert "LY" in sys_msg
        assert "ML" in sys_msg
        assert "[预加载数据]" in ai_msg
        assert "65.0" in ai_msg
        assert "72" in ai_msg

    def test_build_injection_empty_signals(self):
        context: Dict[str, str] = {
            "ly_signals_context": "",
            "ml_factor_context": "",
            "market_context": "**OHLCV:** 10.0",
            "fundamentals_context": "**基本面:** PE=15",
            "sentiment_context": "**ML分析:** 无数据",
            "news_context": "**新闻公告:** 无",
        }
        sys_msg, ai_msg = self.cp.build_injection_payload(context)
        assert "--- LY" not in sys_msg
        assert "--- ML" not in sys_msg
        assert "OHLCV" in sys_msg

    def test_build_injection_all_empty(self):
        context: Dict[str, str] = {
            "ly_signals_context": "",
            "ml_factor_context": "",
            "market_context": "",
            "fundamentals_context": "",
            "sentiment_context": "",
            "news_context": "",
        }
        sys_msg, ai_msg = self.cp.build_injection_payload(context)
        assert isinstance(sys_msg, str)
        assert isinstance(ai_msg, str)
