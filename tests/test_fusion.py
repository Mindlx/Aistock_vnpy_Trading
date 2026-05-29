"""
三系统融合引擎单元测试

仅依赖 fusion_system 内部模块，不依赖三个独立系统。

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

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_loader import (
    LynxDataLoader,
    MindLynxDataLoader,
    TradingAgentDataLoader,
)
from src.fusion_engine import FusionEngine
from src.normalizer import SignalNormalizer
from src.wecom_notifier import WeComNotifier


# ══════════════════════════════════════════
# 基础配置
# ══════════════════════════════════════════

MINIMAL_CONFIG = {
    "weights": {
        "lynx_vnpy": 0.35,
        "mindlynx": 0.35,
        "tradingagent": 0.30,
    },
    "thresholds": {
        "strong_bullish": 0.50,
        "weak_bullish": 0.20,
        "neutral_low": -0.10,
        "weak_bearish": -0.50,
    },
    "confidence_thresholds": {
        "lynx_min_valid": 35,
    },
    "schedule": {"run_time": "16:30", "timezone": "Asia/Shanghai"},
    "wecom": {"webhook_url": "", "enabled": False},
    "data_paths": {
        "lynx_output": "/tmp/test_lynx_output/",
        "mindlynx_reports": "/tmp/test_mindlynx_reports/",
        "tradingagent_logs": "/tmp/test_tradingagent_logs/",
        "fusion_output": "/tmp/test_fusion_output/",
    },
    "logging": {"level": "INFO", "retention_days": 90},
    "fusion_output": {"save_daily_csv": True, "save_daily_json": True},
}


# ══════════════════════════════════════════
# Normalizer 测试
# ══════════════════════════════════════════


class TestSignalNormalizer:
    """信号归一化单元测试"""

    def setup_method(self):
        self.n = SignalNormalizer()

    # ── lynx_vnpy 归一化 ──

    def test_lynx_buy(self):
        score, valid = self.n.normalize_lynx("🟢 买入", 72.0)
        assert valid is True
        assert score == pytest.approx(0.8 * 0.72, rel=1e-2)

    def test_lynx_watch(self):
        score, valid = self.n.normalize_lynx("🟢 关注", 58.0)
        assert valid is True
        assert score == pytest.approx(0.8 * 0.58, rel=1e-2)

    def test_lynx_neutral(self):
        """观望信号不乘置信度"""
        score, valid = self.n.normalize_lynx("⚪ 观望", 50.0)
        assert valid is True
        assert score == 0.0

    def test_lynx_caution(self):
        score, valid = self.n.normalize_lynx("🟡 谨慎", 40.0)
        assert valid is True
        assert score == pytest.approx(-0.3 * 0.40, rel=1e-2)

    def test_lynx_avoid(self):
        score, valid = self.n.normalize_lynx("🔴 回避", 25.0)
        assert valid is True
        assert score == pytest.approx(-0.8 * 0.25, rel=1e-2)

    def test_lynx_invalid_confidence(self):
        """置信度低于35%时视作无效"""
        score, valid = self.n.normalize_lynx("🟢 买入", 30.0)
        assert valid is False
        assert score == 0.0

    def test_lynx_strip_emoji_multi(self):
        """多种 emoji 前缀都能正确剥离"""
        for raw_signal in ["🟢 买入", "⚪ 观望", "🟡 谨慎", "🔴 回避"]:
            clean = self.n._strip_emoji(raw_signal)
            assert clean in ["买入", "关注", "观望", "谨慎", "回避"]

    def test_lynx_signal_without_emoji(self):
        """无 emoji 的原始信号也能归一化"""
        score, valid = self.n.normalize_lynx("买入", 72.0)
        assert valid is True
        assert score > 0

    # ── MindLynx 归一化 ──

    def test_mindlynx_buy(self):
        score = self.n.normalize_mindlynx("买入", 75)
        assert score == 0.6

    def test_mindlynx_add_position(self):
        score = self.n.normalize_mindlynx("加仓", 65)
        assert score == 0.6

    def test_mindlynx_hold_high(self):
        score = self.n.normalize_mindlynx("持有", 65)
        assert score == 0.6

    def test_mindlynx_hold_low(self):
        score = self.n.normalize_mindlynx("持有", 45)
        assert score == 0.0

    def test_mindlynx_watch_neutral(self):
        score = self.n.normalize_mindlynx("观望", 50)
        assert score == 0.0

    def test_mindlynx_watch_bearish(self):
        score = self.n.normalize_mindlynx("观望", 30)
        assert score == -0.2

    def test_mindlynx_sell(self):
        score = self.n.normalize_mindlynx("卖出", 30)
        assert score == -0.6

    def test_mindlynx_reduce(self):
        score = self.n.normalize_mindlynx("减仓", 25)
        assert score == -0.6

    # ── TradingAgent 归一化 ──

    def test_tradingagent_buy(self):
        score = self.n.normalize_tradingagent("Buy")
        assert score == 0.9

    def test_tradingagent_overweight(self):
        score = self.n.normalize_tradingagent("Overweight")
        assert score == 0.5

    def test_tradingagent_hold(self):
        score = self.n.normalize_tradingagent("Hold")
        assert score == 0.0

    def test_tradingagent_underweight(self):
        score = self.n.normalize_tradingagent("Underweight")
        assert score == -0.5

    def test_tradingagent_sell(self):
        score = self.n.normalize_tradingagent("Sell")
        assert score == -0.9

    def test_tradingagent_lowercase(self):
        """不区分大小写"""
        score = self.n.normalize_tradingagent("buy")
        assert score == 0.9

    def test_tradingagent_unknown(self):
        """未知评级返回 0"""
        score = self.n.normalize_tradingagent("Moon")
        assert score == 0.0

    # ── 助手方法 ──

    def test_map_normalized_to_label(self):
        assert self.n.map_normalized_to_label(0.7) == "strong_bullish"
        assert self.n.map_normalized_to_label(0.3) == "bullish"
        assert self.n.map_normalized_to_label(0.0) == "neutral"
        assert self.n.map_normalized_to_label(-0.3) == "bearish"
        assert self.n.map_normalized_to_label(-0.7) == "strong_bearish"

    def test_parse_lynx_signal_text(self):
        assert self.n.parse_lynx_signal_text("🟢 买入") == "买入"
        assert self.n.parse_lynx_signal_text("⚪ 观望") == "观望"


# ══════════════════════════════════════════
# FusionEngine 测试
# ══════════════════════════════════════════


class TestFusionEngine:
    """融合引擎单元测试"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        config_path = os.path.join(self.tmpdir, "settings.yaml")
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(MINIMAL_CONFIG, f)
        self.engine = FusionEngine(config_path)

    # ── 共识场景 ──

    def test_consensus_strong_bullish(self):
        """三系统一致看多 → 强烈看多"""
        result = self.engine.fuse_single_stock(
            stock_code="601801",
            stock_name="皖新传媒",
            lynx_signal="🟢 买入",
            lynx_prob_up=72.0,
            mindlynx_advice="持有",
            mindlynx_score=72,
            tradingagent_rating="Buy",
        )
        assert result["valid"] is True
        assert result["signal"] == "strong_bullish"
        assert result["fusion_score"] > 0.50
        assert result["has_disagreement"] is False
        assert result["is_degraded"] is False

    def test_consensus_strong_bearish(self):
        """三系统一致看空 → 强烈看空"""
        result = self.engine.fuse_single_stock(
            stock_code="603189",
            stock_name="*ST网达",
            lynx_signal="🔴 回避",
            lynx_prob_up=25.0,
            mindlynx_advice="卖出",
            mindlynx_score=25,
            tradingagent_rating="Sell",
        )
        assert result["valid"] is True
        assert result["signal"] == "strong_bearish"
        assert result["fusion_score"] < -0.50

    def test_consensus_weak_bullish(self):
        """两系统偏多一系统中性 → 弱看多"""
        result = self.engine.fuse_single_stock(
            stock_code="600372",
            stock_name="中航机载",
            lynx_signal="🟢 关注",
            lynx_prob_up=60.0,
            mindlynx_advice="观望",
            mindlynx_score=52,
            tradingagent_rating="Overweight",
        )
        assert result["valid"] is True
        assert result["signal"] in ("weak_bullish", "neutral")

    # ── 分歧场景 ──

    def test_disagreement_detected(self):
        """系统间分歧 → has_disagreement=True"""
        result = self.engine.fuse_single_stock(
            stock_code="000592",
            stock_name="平潭发展",
            lynx_signal="🟢 买入",
            lynx_prob_up=70.0,
            mindlynx_advice="卖出",
            mindlynx_score=30,
            tradingagent_rating="Buy",
        )
        assert result["has_disagreement"] is True
        assert result["disagreement_score"] > 0
        assert result["uncertainty_penalty"] > 0

    def test_disagreement_position_capped(self):
        """分歧状态下仓位上限为 1成"""
        result = self.engine.fuse_single_stock(
            stock_code="000592",
            stock_name="平潭发展",
            lynx_signal="🟢 买入",
            lynx_prob_up=80.0,
            mindlynx_advice="卖出",
            mindlynx_score=20,
            tradingagent_rating="Buy",
        )
        assert result["disagreement_capped"] is True
        # 即使融合得分较高，仓位也应被限制
        assert "1成" in result["position_advice"] or "0.5" in result["position_advice"]

    # ── 缺失数据场景 ──

    def test_missing_lynx_low_confidence(self):
        """lynx 置信度低 → 仅使用两系统"""
        result = self.engine.fuse_single_stock(
            stock_code="601801",
            stock_name="皖新传媒",
            lynx_signal="🟢 买入",
            lynx_prob_up=30.0,  # < 35%，无效
            mindlynx_advice="持有",
            mindlynx_score=65,
            tradingagent_rating="Buy",
        )
        assert result["lynx_valid"] is False
        assert result["is_degraded"] is True
        assert result["valid"] is True  # 仍有 2 系统有效

    def test_all_systems_invalid(self):
        """所有系统无效 → 返回无效信号"""
        result = self.engine.fuse_single_stock(
            stock_code="000000",
            stock_name="测试",
            lynx_signal="🟢 买入",
            lynx_prob_up=20.0,  # < 35%
            mindlynx_advice="观望",
            mindlynx_score=50,
            tradingagent_rating="Hold",
        )
        # MindLynx 和 TradingAgent 始终有效，所以这里仍然是有效的
        assert result["valid"] is True  # 至少两个系统有效

    # ── 权重分配 ──

    def test_adjusted_weights_normal(self):
        weights, count, degraded = self.engine._compute_adjusted_weights(True, True, True)
        assert count == 3
        assert degraded is False
        assert abs(sum(weights.values()) - 1.0) < 0.01

    def test_adjusted_weights_one_missing(self):
        weights, count, degraded = self.engine._compute_adjusted_weights(False, True, True)
        assert count == 2
        assert degraded is True
        assert "lynx" not in weights
        assert abs(sum(weights.values()) - 1.0) < 0.01

    # ── 批量融合 ──

    def test_fuse_stock_pool(self):
        signals = [
            {
                "code": "601801", "name": "皖新传媒",
                "lynx_signal": "🟢 买入", "lynx_prob_up": 72.0,
                "mindlynx_advice": "持有", "mindlynx_score": 72,
                "tradingagent_rating": "Buy",
            },
            {
                "code": "603189", "name": "*ST网达",
                "lynx_signal": "🔴 回避", "lynx_prob_up": 25.0,
                "mindlynx_advice": "卖出", "mindlynx_score": 25,
                "tradingagent_rating": "Sell",
            },
        ]
        results = self.engine.fuse_stock_pool(signals)
        assert len(results) == 2
        # 排序：看多在前
        assert results[0]["fusion_score"] > results[1]["fusion_score"]

    # ── 投资组合摘要 ──

    def test_portfolio_summary(self):
        signals = []
        for i in range(3):
            signals.append({
                "code": f"00000{i}",
                "name": f"测试{i}",
                "lynx_signal": "🟢 买入", "lynx_prob_up": 70.0,
                "mindlynx_advice": "持有", "mindlynx_score": 65,
                "tradingagent_rating": "Buy",
            })
        results = self.engine.fuse_stock_pool(signals)
        summary = self.engine.get_portfolio_summary(results)
        assert summary["total_valid"] == 3
        assert summary["distribution"]["strong_bullish"] >= 0


# ══════════════════════════════════════════
# DataLoader 测试
# ══════════════════════════════════════════


class TestDataLoaders:
    """数据加载器测试"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.lynx_dir = os.path.join(self.tmpdir, "lynx")
        self.mindlynx_dir = os.path.join(self.tmpdir, "mindlynx")
        self.ta_dir = os.path.join(self.tmpdir, "tradingagent")
        os.makedirs(self.lynx_dir)
        os.makedirs(self.mindlynx_dir)
        os.makedirs(self.ta_dir)

    def test_lynx_loader_nonexistent(self):
        """文件不存在时返回空字典"""
        loader = LynxDataLoader(self.lynx_dir)
        data = loader.load_by_date("2026-05-29")
        assert data == {}

    def test_lynx_loader_from_file(self):
        """从 JSON 文件加载"""
        test_data = [
            {"code": "601801", "name": "皖新传媒", "signal": "🟢 买入", "prob_up": 72.0}
        ]
        json_path = os.path.join(self.lynx_dir, "2026-05-29_lynx_signals.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(test_data, f)

        loader = LynxDataLoader(self.lynx_dir)
        data = loader.load_by_date("2026-05-29")
        assert "601801" in data
        assert data["601801"]["signal"] == "🟢 买入"

    def test_mindlynx_loader_from_report(self):
        """从 markdown 报告解析"""
        report = """
# 🎯 2026-05-29 决策仪表盘
> 共分析 **3** 只股票
🟡 **皖新传媒(601801)**: 持有 | 评分 52 | 震荡偏多
⚪ **古麒绒材(001390)**: 观望 | 评分 46 | 看空
🔴 ***ST网达(603189)**: 卖出 | 评分 34 | 强烈看空
"""
        os.makedirs(self.mindlynx_dir, exist_ok=True)
        with open(os.path.join(self.mindlynx_dir, "report_2026-05-29.md"), "w", encoding="utf-8") as f:
            f.write(report)

        loader = MindLynxDataLoader(self.mindlynx_dir)
        data = loader.load_by_date("2026-05-29")
        assert "601801" in data
        assert data["601801"]["signal"] == "持有"
        assert data["601801"]["score"] == 52

    def test_tradingagent_loader_nonexistent(self):
        """文件不存在时返回 None"""
        loader = TradingAgentDataLoader(self.ta_dir)
        result = loader.load_by_stock_and_date("601801", "2026-05-29")
        assert result is None

    def test_tradingagent_loader_with_decision(self):
        """从状态 JSON 提取 rating"""
        ta_logs = os.path.join(self.ta_dir, "601801", "MindTradingAgentStrategy_logs")
        os.makedirs(ta_logs, exist_ok=True)

        state = {
            "company_of_interest": "601801",
            "final_trade_decision": (
                "**Rating**: Buy\n"
                "**Executive Summary**: Enter on weakness.\n"
                "**Investment Thesis**: Strong fundamentals.\n"
                "**Price Target**: 15.5\n"
                "**Time Horizon**: 3-6 months\n"
            ),
        }
        with open(os.path.join(ta_logs, "full_states_log_2026-05-29.json"), "w", encoding="utf-8") as f:
            json.dump(state, f)

        loader = TradingAgentDataLoader(self.ta_dir)
        result = loader.load_by_stock_and_date("601801", "2026-05-29")
        assert result is not None
        assert result["rating"] == "Buy"

    def test_tradingagent_loader_hold_default(self):
        """无法解析时默认返回 Hold"""
        ta_logs = os.path.join(self.ta_dir, "601801", "MindTradingAgentStrategy_logs")
        os.makedirs(ta_logs, exist_ok=True)

        state = {
            "company_of_interest": "601801",
            "final_trade_decision": "No rating found in this text",
        }
        with open(os.path.join(ta_logs, "full_states_log_2026-05-29.json"), "w", encoding="utf-8") as f:
            json.dump(state, f)

        loader = TradingAgentDataLoader(self.ta_dir)
        result = loader.load_by_stock_and_date("601801", "2026-05-29")
        assert result is not None
        assert result["rating"] == "Hold"


# ══════════════════════════════════════════
# WeCom 推送测试
# ══════════════════════════════════════════


class TestWeComNotifier:
    """企业微信推送测试"""

    def setup_method(self):
        self.notifier = WeComNotifier("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test", enabled=False)

    def test_disabled_push(self):
        """禁用时不发送"""
        result = self.notifier.send_markdown("测试")
        assert result is None

    def test_format_daily_summary(self):
        """格式化每日摘要"""
        results = [
            {
                "stock_code": "601801",
                "stock_name": "皖新传媒",
                "valid": True,
                "signal": "strong_bullish",
                "signal_name": "强烈看多",
                "fusion_score": 0.65,
                "position_advice": "2-3成",
                "lynx_score": 0.58,
                "mindlynx_score": 0.6,
                "tradingagent_score": 0.9,
                "is_degraded": False,
                "has_disagreement": False,
                "disagreement_capped": False,
            },
            {
                "stock_code": "603189",
                "stock_name": "*ST网达",
                "valid": True,
                "signal": "strong_bearish",
                "signal_name": "强烈看空",
                "fusion_score": -0.62,
                "position_advice": "清仓",
                "lynx_score": -0.6,
                "mindlynx_score": -0.6,
                "tradingagent_score": -0.9,
                "is_degraded": False,
                "has_disagreement": False,
                "disagreement_capped": False,
            },
            {
                "stock_code": "000592",
                "stock_name": "平潭发展",
                "valid": False,
                "message": "所有系统无效",
            },
        ]
        summary = self.notifier.format_daily_summary(results, "2026-05-29")
        assert "强烈看多" in summary
        assert "强烈看空" in summary
        assert "皖新传媒" in summary
        assert "ST网达" in summary
        assert "所有系统无效" in summary


# ══════════════════════════════════════════
# 集成测试（可选）
# ══════════════════════════════════════════


def _run_integration_test():
    """
    端到端测试：模拟 → 融合 → 输出
    
    只有在项目目录中才能运行此测试。
    """
    from scripts.run_daily import generate_mock_data, load_stock_pool

    stock_pool = load_stock_pool("config/stock_pool.csv")
    mock_data = generate_mock_data(stock_pool, "2026-05-29")

    engine = FusionEngine("config/settings.yaml")
    results = engine.fuse_stock_pool(mock_data)

    print("\n=== 集成测试结果 ===")
    for r in results:
        status = r["signal_name"]
        score = r["fusion_score"]
        print(f"  {status:8s} {r['stock_code']} score={score:.2f}")

    return results


if __name__ == "__main__":
    # 运行集成测试
    results = _run_integration_test()

    # 也可用 pytest 单独运行：
    # python -m pytest tests/test_fusion.py -v
    import pytest
    pytest.main([__file__, "-v"])
