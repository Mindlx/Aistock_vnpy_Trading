"""
=============================================
测试事件驱动分析服务 EventMonitor
=============================================

测试范围（不涉及实际网络请求）：
1. 事件分类逻辑（classify_announcement, classify_qa）
2. 重要性评分
3. 事件过滤（去重 + 低重要性丢弃）
4. 指纹生成
5. 数据类基本功能
6. 格式化简报
"""

from __future__ import annotations

import time

import pytest

from src.services.event_monitor import (
    DEFAULT_BRIEF_IMPORTANCE_LOWER,
    DEFAULT_CHECK_INTERVAL,
    DEFAULT_DEDUP_TTL,
    DEFAULT_IMPORTANCE_THRESHOLD,
    DEFAULT_LOW_IMPORTANCE_UPPER,
    EASTMONEY_ANNOUNCE_API,
    CNINFO_SEARCH_API,
    EventClassifier,
    EventFilter,
    EventMonitor,
    EventType,
    StockEvent,
    EastMoneyAnnounceFetcher,
    InteractionQAFetcher,
    create_event_monitor,
)


# ============================================================
# StockEvent 基础测试
# ============================================================


class TestStockEvent:
    def test_minimal_creation(self):
        """测试最小参数创建 StockEvent"""
        event = StockEvent(
            code="600519",
            type=EventType.EARNINGS,
            title="贵州茅台2024年年度报告",
            content="摘要内容",
            source="公告",
            event_time=1700000000.0,
        )
        assert event.code == "600519"
        assert event.type == EventType.EARNINGS
        assert event.importance == 5  # 默认值
        assert event.url == ""
        assert event.stock_name == ""

    def test_fingerprint_uniqueness(self):
        """测试指纹生成 - 相同内容产生相同指纹"""
        e1 = StockEvent(
            code="600519", type=EventType.EARNINGS,
            title="年报", content="", source="公告", event_time=100.0,
        )
        e2 = StockEvent(
            code="600519", type=EventType.EARNINGS,
            title="年报", content="", source="公告", event_time=100.0,
        )
        assert e1.fingerprint == e2.fingerprint

    def test_fingerprint_different(self):
        """测试指纹生成 - 不同内容产生不同指纹"""
        e1 = StockEvent(
            code="600519", type=EventType.EARNINGS,
            title="年报A", content="", source="公告", event_time=100.0,
        )
        e2 = StockEvent(
            code="600519", type=EventType.EARNINGS,
            title="年报B", content="", source="公告", event_time=100.0,
        )
        assert e1.fingerprint != e2.fingerprint

    def test_importance_properties(self):
        """测试重要性属性"""
        high = StockEvent(
            code="600519", type=EventType.EARNINGS,
            title="重大重组", content="", source="公告", event_time=100.0,
            importance=8,
        )
        assert high.is_high_importance is True
        assert high.is_medium_importance is False
        assert high.is_low_importance is False

        medium = StockEvent(
            code="600519", type=EventType.EARNINGS,
            title="中标", content="", source="公告", event_time=100.0,
            importance=5,
        )
        assert medium.is_high_importance is False
        assert medium.is_medium_importance is True
        assert medium.is_low_importance is False

        low = StockEvent(
            code="600519", type=EventType.EARNINGS,
            title="日常公告", content="", source="公告", event_time=100.0,
            importance=2,
        )
        assert low.is_high_importance is False
        assert low.is_medium_importance is False
        assert low.is_low_importance is True


# ============================================================
# 事件分类测试
# ============================================================


class TestEventClassifier:
    def test_classify_earnings(self):
        """财报分类"""
        etype, imp = EventClassifier.classify_announcement("2024年年度报告摘要")
        assert etype == EventType.EARNINGS
        assert imp >= 7

    def test_classify_contract(self):
        """中标/合同分类"""
        etype, imp = EventClassifier.classify_announcement("关于重大项目中标公告")
        assert etype == EventType.CONTRACT
        assert imp >= 6

    def test_classify_buyback(self):
        """回购分类"""
        etype, imp = EventClassifier.classify_announcement("关于股份回购计划公告")
        assert etype == EventType.BUYBACK
        assert imp >= 6

    def test_classify_reduce(self):
        """减持分类"""
        etype, imp = EventClassifier.classify_announcement("大股东减持计划公告")
        assert etype == EventType.REDUCE
        assert imp >= 6

    def test_classify_restructure(self):
        """资产重组分类"""
        etype, imp = EventClassifier.classify_announcement("重大资产重组停牌公告")
        assert etype == EventType.RESTRUCTURE
        assert imp >= 8

    def test_classify_delist_risk(self):
        """退市风险分类"""
        etype, imp = EventClassifier.classify_announcement("退市风险警示公告")
        assert etype == EventType.DELIST_RISK
        assert imp >= 8

    def test_classify_suspension(self):
        """停复牌分类"""
        etype, imp = EventClassifier.classify_announcement("关于股票停牌的公告")
        assert etype == EventType.SUSPENSION
        assert imp >= 7

    def test_classify_regulatory(self):
        """监管分类"""
        etype, imp = EventClassifier.classify_announcement("收到证监会立案调查通知书")
        assert etype == EventType.REGULATORY
        assert imp >= 7

    def test_classify_unknown(self):
        """未知类型"""
        etype, imp = EventClassifier.classify_announcement("日常办公会议通知")
        assert etype == EventType.OTHER
        assert imp == 3

    def test_classify_qa_order(self):
        """互动易问答 - 订单关键词"""
        etype, imp = EventClassifier.classify_qa(
            "请问公司最近有没有新的订单？",
            "公司近期签订了大额订单合同...",
        )
        assert etype == EventType.INVESTOR_QA
        assert imp >= 5

    def test_classify_qa_buyback(self):
        """互动易问答 - 回购关键词"""
        etype, imp = EventClassifier.classify_qa(
            "公司有回购计划吗？",
            "公司目前没有回购计划，未来会考虑...",
        )
        assert etype == EventType.INVESTOR_QA
        assert imp >= 6

    def test_classify_qa_generic(self):
        """互动易问答 - 无关键词"""
        etype, imp = EventClassifier.classify_qa(
            "请问公司何时召开股东大会？",
            "请关注公司公告...",
        )
        assert etype == EventType.INVESTOR_QA
        assert imp == 5

    def test_adjust_importance_boost(self):
        """重要性调整 - 增强"""
        result = EventClassifier.adjust_importance(
            EventType.EARNINGS, 7,
            "业绩大幅超预期，同比增长50%",
            "公司净利润创历史新高",
        )
        assert result >= 9  # 超预期+大幅+历史新高 → +3

    def test_adjust_importance_clamp(self):
        """重要性调整 - 范围限制"""
        result = EventClassifier.adjust_importance(
            EventType.EARNINGS, 10,
            "业绩超预期创历史新高",
            "大幅增长",
        )
        assert result == 10  # 不超上限

    def test_adjust_importance_negative(self):
        """重要性调整 - 负面信号"""
        result = EventClassifier.adjust_importance(
            EventType.REGULATORY, 7,
            "公司亏损风险提示",
            "净利润大幅下滑",
        )
        assert result >= 8

    def test_adjust_importance_minimum(self):
        """重要性调整 - 下限保护"""
        result = EventClassifier.adjust_importance(
            EventType.OTHER, 1,
            "例行公告",
            "无特殊内容",
        )
        assert result >= 1


# ============================================================
# 事件过滤测试
# ============================================================


class TestEventFilter:
    def test_is_new_event(self):
        """测试新事件识别"""
        filt = EventFilter(dedup_ttl=3600)
        event = StockEvent(
            code="600519", type=EventType.EARNINGS,
            title="年报", content="", source="公告",
            event_time=time.time(),
            importance=7,
        )
        assert filt.is_new(event) is True
        assert filt.is_new(event) is False  # 重复

    def test_different_events(self):
        """不同事件不被去重"""
        filt = EventFilter(dedup_ttl=3600)
        e1 = StockEvent(
            code="600519", type=EventType.EARNINGS,
            title="年报", content="", source="公告",
            event_time=100.0, importance=7,
        )
        e2 = StockEvent(
            code="300750", type=EventType.EARNINGS,
            title="年报", content="", source="公告",
            event_time=100.0, importance=7,
        )
        assert filt.is_new(e1) is True
        assert filt.is_new(e2) is True  # 不同股票

    def test_should_keep_high_importance(self):
        """高重要性事件应保留"""
        filt = EventFilter()
        event = StockEvent(
            code="600519", type=EventType.EARNINGS,
            title="重组", content="", source="公告",
            event_time=time.time(), importance=8,
        )
        assert filt.should_keep(event) is True

    def test_should_discard_low_importance(self):
        """低重要性事件应丢弃"""
        filt = EventFilter()
        event = StockEvent(
            code="600519", type=EventType.OTHER,
            title="例行通知", content="", source="公告",
            event_time=time.time(), importance=1,
        )
        assert filt.should_keep(event) is False

    def test_should_discard_other_low(self):
        """其他类型+低重要性应丢弃"""
        filt = EventFilter()
        event = StockEvent(
            code="600519", type=EventType.OTHER,
            title="无关内容", content="", source="公告",
            event_time=time.time(), importance=4,
        )
        assert filt.should_keep(event) is False

    def test_should_keep_medium_qa(self):
        """中等重要性互动易问答应保留"""
        filt = EventFilter()
        event = StockEvent(
            code="600519", type=EventType.INVESTOR_QA,
            title="公司订单情况", content="答: 订单充足",
            source="互动易", event_time=time.time(), importance=5,
        )
        assert filt.should_keep(event) is True

    def test_should_discard_duplicate(self):
        """重复事件应丢弃"""
        filt = EventFilter()
        event = StockEvent(
            code="600519", type=EventType.EARNINGS,
            title="年报", content="", source="公告",
            event_time=100.0, importance=8,
        )
        assert filt.should_keep(event) is True
        assert filt.should_keep(event) is False  # 第二次被去重

    def test_reset(self):
        """重置去重缓存"""
        filt = EventFilter()
        event = StockEvent(
            code="600519", type=EventType.EARNINGS,
            title="年报", content="", source="公告",
            event_time=100.0, importance=8,
        )
        assert filt.should_keep(event) is True
        filt.reset()
        assert filt.should_keep(event) is True  # 重置后可再次通过


# ============================================================
# EventMonitor 核心测试
# ============================================================


class TestEventMonitor:
    def test_create_default(self):
        """测试创建默认 EventMonitor"""
        monitor = EventMonitor()
        assert monitor.stock_codes == []
        assert monitor.importance_threshold == DEFAULT_IMPORTANCE_THRESHOLD
        assert monitor.check_interval == DEFAULT_CHECK_INTERVAL
        assert monitor.filter is not None
        assert monitor._running is False

    def test_create_with_codes(self):
        """测试带股票列表创建"""
        monitor = EventMonitor(stock_codes=["600519", "300750"])
        assert len(monitor.stock_codes) == 2
        assert "600519" in monitor.stock_codes

    def test_set_stock_codes(self):
        """测试更新股票列表"""
        monitor = EventMonitor()
        monitor.set_stock_codes(["600519", "000001"])
        assert len(monitor.stock_codes) == 2

    def test_get_stats_default(self):
        """测试默认统计"""
        monitor = EventMonitor()
        stats = monitor.get_stats()
        assert stats["total_checks"] == 0
        assert stats["total_events"] == 0
        assert stats["high_importance"] == 0

    def test_reset_stats(self):
        """测试重置统计"""
        monitor = EventMonitor(stock_codes=["600519"])
        monitor.stats["total_checks"] = 10
        monitor.stats["high_importance"] = 3
        monitor.reset_stats()
        assert monitor.stats["total_checks"] == 0
        assert monitor.stats["high_importance"] == 0

    def test_stop(self):
        """测试停止"""
        monitor = EventMonitor()
        monitor._running = True
        monitor.stop()
        assert monitor._running is False

    def test_importance_threshold_custom(self):
        """测试自定义重要性阈值"""
        monitor = EventMonitor(importance_threshold=8)
        assert monitor.importance_threshold == 8

    def test_check_interval_minimum(self):
        """测试最小检查间隔"""
        monitor = EventMonitor(check_interval=10)
        assert monitor.check_interval >= 30  # 最小值 30 秒

    def test_format_brief(self):
        """测试简报格式化"""
        event = StockEvent(
            code="600519",
            type=EventType.EARNINGS,
            title="贵州茅台2024年年度报告",
            content="净利润同比增长20%",
            source="公告",
            event_time=1700000000.0,
            importance=8,
            stock_name="贵州茅台",
            url="https://example.com",
        )
        brief = EventMonitor._format_brief(event)
        assert "贵州茅台" in brief
        assert "earnings" in brief
        assert "https://example.com" in brief
        assert "重要性8" in brief


# ============================================================
# create_event_monitor 测试
# ============================================================


class TestCreateEventMonitor:
    def test_create_with_notify_only(self):
        """测试仅带通知服务创建"""
        monitor = create_event_monitor(
            stock_codes=["600519"],
            importance_threshold=8,
            check_interval=60,
        )
        assert monitor.stock_codes == ["600519"]
        assert monitor.importance_threshold == 8
        assert monitor.check_interval == 60

    def test_create_empty_stocks(self):
        """测试空股票列表创建"""
        monitor = create_event_monitor(
            stock_codes=[],
        )
        assert monitor.stock_codes == []


# ============================================================
# 常量测试
# ============================================================


class TestConstants:
    def test_defaults(self):
        """测试默认常量"""
        assert DEFAULT_IMPORTANCE_THRESHOLD == 7
        assert DEFAULT_CHECK_INTERVAL == 300
        assert DEFAULT_BRIEF_IMPORTANCE_LOWER == 4
        assert DEFAULT_LOW_IMPORTANCE_UPPER == 3
        assert DEFAULT_DEDUP_TTL == 86400

    def test_api_urls(self):
        """测试 API URL 常量"""
        assert "eastmoney.com" in EASTMONEY_ANNOUNCE_API
        assert "cninfo.com.cn" in CNINFO_SEARCH_API


# ============================================================
# EventType 测试
# ============================================================


class TestEventType:
    def test_all_types(self):
        """测试所有事件类型枚举值"""
        assert EventType.EARNINGS.value == "earnings"
        assert EventType.CONTRACT.value == "contract"
        assert EventType.BUYBACK.value == "buyback"
        assert EventType.REDUCE.value == "reduce"
        assert EventType.INCREASE.value == "increase"
        assert EventType.RESTRUCTURE.value == "restructure"
        assert EventType.DELIST_RISK.value == "delist_risk"
        assert EventType.SUSPENSION.value == "suspension"
        assert EventType.INVESTOR_QA.value == "investor_qa"
        assert EventType.DIVIDEND.value == "dividend"
        assert EventType.REGULATORY.value == "regulatory"
        assert EventType.POLICY.value == "policy"
        assert EventType.PRICE_ANOMALY.value == "price_anomaly"
        assert EventType.OTHER.value == "other"
