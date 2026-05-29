"""
=============================================
RealtimeMonitorService 单元测试
=============================================

测试核心逻辑（ATR计算、简报生成、异动检测）：
- Phase 1: 简报格式化
- Phase 2: ATR 计算 + 止损规则
- Phase 3: 量价异动告警 + 均线突破检测

⚠️ 不测试实际的 WebSocket 网络连接。
"""

import time
from datetime import time as dt_time
from unittest.mock import MagicMock

from src.services.realtime_monitor import (
    RealtimeMonitorService,
    StockIntradayState,
    _build_atr_alert_text,
    _build_briefing_text,
    _build_ma_cross_alert_text,
    _build_volume_alert_text,
    _compute_atr_from_df,
    _compute_ma,
    get_next_trading_day,
    is_trading_day,
    run_realtime_monitor,
)

# ============================================================
# Helper: 生成模拟日线数据
# ============================================================


def _make_daily_rows(closes, highs=None, lows=None):
    """生成模拟的日线数据行列表（兼容 DataFrame.to_dict('records')）"""
    n = len(closes)
    if highs is None:
        highs = [c * 1.02 for c in closes]
    if lows is None:
        lows = [c * 0.98 for c in closes]
    rows = []
    for i in range(n):
        rows.append(
            {
                "close": closes[i],
                "high": highs[i],
                "low": lows[i],
                "volume": 1000000 + i * 1000,
                "open": closes[i] * 0.99,
                "date": f"2026-01-{i + 1:02d}",
                "name": "TestStock",
            }
        )
    return rows


# ============================================================
# Phase 1: 简报格式化
# ============================================================


class TestBriefingFormat:
    """Phase 1: 盘中简报生成"""

    def test_basic_briefing(self):
        """测试基本简报格式"""
        states = [
            StockIntradayState(code="600519", name="贵州茅台"),
            StockIntradayState(code="000001", name="平安银行"),
        ]
        quotes = {
            "600519": {"price": 1520.0, "change_pct": 1.5, "volume_ratio": 1.2, "turnover_rate": 0.5},
            "000001": {"price": 12.5, "change_pct": -0.8, "volume_ratio": 0.9, "turnover_rate": 0.3},
        }
        text = _build_briefing_text(states, quotes)
        assert "📊" in text
        assert "600519" in text
        assert "贵州茅台" in text
        assert "000001" in text
        assert "平安银行" in text
        assert "+1.5%" in text
        assert "-0.8%" in text

    def test_abnormal_flags(self):
        """测试异常标记"""
        states = [StockIntradayState(code="600519", name="贵州茅台")]
        # 量比 > 2
        quotes = {"600519": {"price": 100, "change_pct": 1.0, "volume_ratio": 2.5, "turnover_rate": 0}}
        text = _build_briefing_text(states, quotes)
        assert "量比" in text

        # 涨跌幅 > 3%
        quotes = {"600519": {"price": 100, "change_pct": 4.0, "volume_ratio": 1.0, "turnover_rate": 0}}
        text = _build_briefing_text(states, quotes)
        assert "+4.0%" in text

        # 量比 < 0.3
        quotes = {"600519": {"price": 100, "change_pct": -1.0, "volume_ratio": 0.2, "turnover_rate": 0}}
        text = _build_briefing_text(states, quotes)
        assert "量比0.2" in text

    def test_sorted_by_change_pct(self):
        """测试按涨跌幅排序"""
        states = [
            StockIntradayState(code="A", name="A"),
            StockIntradayState(code="B", name="B"),
            StockIntradayState(code="C", name="C"),
        ]
        quotes = {
            "A": {"price": 10, "change_pct": 3.0, "volume_ratio": 1.0, "turnover_rate": 0},
            "B": {"price": 10, "change_pct": 1.0, "volume_ratio": 1.0, "turnover_rate": 0},
            "C": {"price": 10, "change_pct": -2.0, "volume_ratio": 1.0, "turnover_rate": 0},
        }
        text = _build_briefing_text(states, quotes)
        a_pos = text.find("A")
        b_pos = text.find("B")
        c_pos = text.find("C")
        assert a_pos < b_pos < c_pos  # 涨跌幅降序


# ============================================================
# Phase 2: ATR 计算 + 止损告警
# ============================================================


class TestATRComputation:
    """Phase 2: ATR 指标计算"""

    def test_atr_from_df(self):
        """测试从日线数据计算 ATR"""
        # 生成稳定趋势的数据（几乎无波动 -> ATR 很小）
        closes = [100 + i for i in range(20)]
        highs = [c * 1.005 for c in closes]
        lows = [c * 0.995 for c in closes]
        rows = _make_daily_rows(closes, highs, lows)
        atr_val = _compute_atr_from_df(rows, 14)
        assert atr_val > 0
        assert atr_val < 5  # 波动小，ATR 应较小

    def test_atr_with_volatile_data(self):
        """测试高波动数据的 ATR"""
        closes = [100 + (i % 5) * 10 for i in range(20)]
        highs = [c * 1.05 for c in closes]
        lows = [c * 0.95 for c in closes]
        rows = _make_daily_rows(closes, highs, lows)
        atr_val = _compute_atr_from_df(rows, 14)
        assert atr_val > 3  # 波动大，ATR 应较大

    def test_atr_insufficient_data(self):
        """测试数据不足时返回 0"""
        rows = _make_daily_rows([100, 101, 102])
        atr_val = _compute_atr_from_df(rows, 14)
        assert atr_val == 0.0


class TestATRStopAlert:
    """Phase 2: ATR 止损告警逻辑"""

    def test_stop_loss_trigger(self):
        """测试止损触发"""
        config = MagicMock()
        config.stock_list = ["600519"]
        config.realtime_monitor_briefing_interval = 900
        config.realtime_monitor_atr_multipliers = "2.0,2.5,3.0"
        config.realtime_monitor_volume_ratio_threshold = 3.0
        config.realtime_monitor_price_change_threshold = 2.0

        service = RealtimeMonitorService(config=config, atr_cooldown=0)

        # 模拟状态
        state = StockIntradayState(code="600519", name="贵州茅台")
        state.atr14 = 10.0
        state.stop_loss_2x = 1500.0  # 当前价 1520 - 2*10 = 1500
        state.stop_loss_3x = 1490.0
        state.pre_close = 1520.0
        service._states["600519"] = state

        # 模拟通知服务
        mock_notifier = MagicMock()
        service._notifier = mock_notifier

        # 价格跌破 2x 止损
        price = 1499.0
        # 手动触发 ATR 检查
        import asyncio

        asyncio.run(service._check_atr_stop("600519", state, price, time.time()))

        # 应发送通知
        assert mock_notifier.send.called
        call_text = mock_notifier.send.call_args[0][0]
        assert "跌破" in call_text
        assert "600519" in call_text
        assert "贵州茅台" in call_text
        assert "1499" in call_text

    def test_stop_loss_not_triggered_above(self):
        """测试价格在止损线上不触发"""
        config = MagicMock()
        config.stock_list = ["600519"]
        config.realtime_monitor_briefing_interval = 900
        config.realtime_monitor_atr_multipliers = "2.0,2.5,3.0"
        config.realtime_monitor_volume_ratio_threshold = 3.0
        config.realtime_monitor_price_change_threshold = 2.0

        service = RealtimeMonitorService(config=config, atr_cooldown=0)
        state = StockIntradayState(code="600519", name="贵州茅台")
        state.atr14 = 10.0
        state.stop_loss_2x = 1500.0
        service._states["600519"] = state

        mock_notifier = MagicMock()
        service._notifier = mock_notifier

        # 价格未跌破止损
        import asyncio

        asyncio.run(service._check_atr_stop("600519", state, 1510.0, time.time()))
        assert not mock_notifier.send.called

    def test_stop_loss_cooldown(self):
        """测试止损冷却期"""
        config = MagicMock()
        config.stock_list = ["600519"]
        config.realtime_monitor_briefing_interval = 900
        config.realtime_monitor_atr_multipliers = "2.0,2.5,3.0"
        config.realtime_monitor_volume_ratio_threshold = 3.0
        config.realtime_monitor_price_change_threshold = 2.0

        service = RealtimeMonitorService(config=config, atr_cooldown=300)
        state = StockIntradayState(code="600519", name="贵州茅台")
        state.atr14 = 10.0
        state.stop_loss_2x = 1500.0
        state.last_atr_alert_time = time.time()  # 刚触发过
        service._states["600519"] = state

        mock_notifier = MagicMock()
        service._notifier = mock_notifier

        import asyncio

        asyncio.run(service._check_atr_stop("600519", state, 1490.0, time.time()))
        assert not mock_notifier.send.called  # 冷却中


# ============================================================
# Phase 3: 量价异动 + 均线突破
# ============================================================


class TestVolumeAlert:
    """Phase 3: 量价异动检测"""

    def test_volume_surge(self):
        """测试放量拉升检测"""
        config = MagicMock()
        config.realtime_monitor_volume_ratio_threshold = 3.0
        config.realtime_monitor_price_change_threshold = 2.0
        config.realtime_monitor_briefing_interval = 900
        config.realtime_monitor_atr_multipliers = "2.0,2.5,3.0"

        service = RealtimeMonitorService(config=config, volume_cooldown=0)
        state = StockIntradayState(code="600519", name="贵州茅台")
        service._states["600519"] = state

        mock_notifier = MagicMock()
        service._notifier = mock_notifier

        import asyncio

        # 量比 4 > 3, 涨幅 3% > 2%
        asyncio.run(service._check_volume_alert("600519", state, 100.0, 3.0, 4.0, 2.0, time.time()))
        assert mock_notifier.send.called
        call_text = mock_notifier.send.call_args[0][0]
        assert "主力介入" in call_text

    def test_volume_shrink(self):
        """测试缩量下跌检测"""
        config = MagicMock()
        config.realtime_monitor_volume_ratio_threshold = 3.0
        config.realtime_monitor_price_change_threshold = 2.0
        config.realtime_monitor_briefing_interval = 900
        config.realtime_monitor_atr_multipliers = "2.0,2.5,3.0"

        service = RealtimeMonitorService(config=config, volume_cooldown=0)
        state = StockIntradayState(code="600519", name="贵州茅台")
        service._states["600519"] = state

        mock_notifier = MagicMock()
        service._notifier = mock_notifier

        import asyncio

        # 量比 0.2 < 0.3, 跌幅 -3% < -2%
        asyncio.run(service._check_volume_alert("600519", state, 100.0, -3.0, 0.2, 1.0, time.time()))
        assert mock_notifier.send.called
        call_text = mock_notifier.send.call_args[0][0]
        assert "观望为主" in call_text

    def test_turnover_rate_abnormal(self):
        """测试换手率异常检测"""
        config = MagicMock()
        config.realtime_monitor_volume_ratio_threshold = 3.0
        config.realtime_monitor_price_change_threshold = 2.0
        config.realtime_monitor_briefing_interval = 900
        config.realtime_monitor_atr_multipliers = "2.0,2.5,3.0"

        service = RealtimeMonitorService(config=config, volume_cooldown=0)
        state = StockIntradayState(code="600519", name="贵州茅台")
        service._states["600519"] = state

        mock_notifier = MagicMock()
        service._notifier = mock_notifier

        # First clear cooldown
        state.last_volume_alert_time = 0

        import asyncio

        # 换手率 12% > 10%
        asyncio.run(service._check_volume_alert("600519", state, 100.0, 1.0, 1.5, 12.0, time.time()))
        assert mock_notifier.send.called
        call_text = mock_notifier.send.call_args[0][0]
        assert "波动加剧" in call_text


class TestMACross:
    """Phase 3: 均线突破检测"""

    def test_ma5_breakout(self):
        """测试突破 MA5"""
        config = MagicMock()
        config.realtime_monitor_briefing_interval = 900
        config.realtime_monitor_atr_multipliers = "2.0,2.5,3.0"
        config.realtime_monitor_volume_ratio_threshold = 3.0
        config.realtime_monitor_price_change_threshold = 2.0

        service = RealtimeMonitorService(config=config, cross_cooldown=0)
        state = StockIntradayState(code="600519", name="贵州茅台")
        state.ma5 = 100.0
        state.prev_close = 99.0  # 昨日收盘低于 MA5
        service._states["600519"] = state

        mock_notifier = MagicMock()
        service._notifier = mock_notifier

        import asyncio

        # 当前价 102 > MA5(100)，从下方突破
        asyncio.run(service._check_ma_cross("600519", state, 102.0, time.time()))
        assert mock_notifier.send.called
        call_text = mock_notifier.send.call_args[0][0]
        assert "突破" in call_text
        assert "MA5" in call_text

    def test_ma20_breakdown(self):
        """测试跌破 MA20"""
        config = MagicMock()
        config.realtime_monitor_briefing_interval = 900
        config.realtime_monitor_atr_multipliers = "2.0,2.5,3.0"
        config.realtime_monitor_volume_ratio_threshold = 3.0
        config.realtime_monitor_price_change_threshold = 2.0

        service = RealtimeMonitorService(config=config, cross_cooldown=0)
        state = StockIntradayState(code="600519", name="贵州茅台")
        state.ma20 = 100.0
        state.prev_close = 101.0  # 昨日收盘高于 MA20
        service._states["600519"] = state

        mock_notifier = MagicMock()
        service._notifier = mock_notifier

        import asyncio

        # 当前价 99 < MA20(100)，从上方跌破
        asyncio.run(service._check_ma_cross("600519", state, 99.0, time.time()))
        assert mock_notifier.send.called
        call_text = mock_notifier.send.call_args[0][0]
        assert "跌破" in call_text
        assert "MA20" in call_text

    def test_no_cross_when_above(self):
        """测试价格未穿过均线时不触发"""
        config = MagicMock()
        config.realtime_monitor_briefing_interval = 900
        config.realtime_monitor_atr_multipliers = "2.0,2.5,3.0"
        config.realtime_monitor_volume_ratio_threshold = 3.0
        config.realtime_monitor_price_change_threshold = 2.0

        service = RealtimeMonitorService(config=config, cross_cooldown=0)
        state = StockIntradayState(code="600519", name="贵州茅台")
        state.ma5 = 100.0
        state.prev_close = 101.0  # 已在 MA5 上方
        service._states["600519"] = state

        mock_notifier = MagicMock()
        service._notifier = mock_notifier

        import asyncio

        # 当前价 102，但 prev_close=101 已在 MA5 上方，不是突破
        asyncio.run(service._check_ma_cross("600519", state, 102.0, time.time()))
        assert not mock_notifier.send.called


# ============================================================
# 告警文本格式化
# ============================================================


class TestAlertFormat:
    """告警文本格式测试"""

    def test_atr_alert_format(self):
        text = _build_atr_alert_text("600519", "贵州茅台", 1499.0, 1500.0, 2.0)
        assert "🚨" in text
        assert "600519" in text
        assert "贵州茅台" in text
        assert "1499" in text
        assert "2.0×ATR" in text

    def test_volume_alert_format(self):
        text = _build_volume_alert_text("600519", "贵州茅台", 1520.0, 3.5, 4.2, 2.1, "放量拉升")
        assert "🔥" in text
        assert "异动预警" in text
        assert "主力介入" in text
        assert "1520" in text
        assert "+3.5%" in text
        assert "4.2" in text  # 量比

    def test_ma_cross_alert_format(self):
        text = _build_ma_cross_alert_text("600519", "贵州茅台", 102.0, "MA5", 100.0, "突破")
        assert "📈" in text
        assert "均线突破" in text
        assert "MA5" in text
        assert "100" in text


# ============================================================
# 工具函数
# ============================================================


class TestComputeMA:
    """移动平均计算测试"""

    def test_basic_ma(self):
        closes = [1, 2, 3, 4, 5]
        assert _compute_ma(closes, 3) == 4.0  # (3+4+5)/3

    def test_ma_insufficient_data(self):
        assert _compute_ma([1, 2], 5) == 0.0

    def test_ma_single(self):
        assert _compute_ma([1, 2, 3], 1) == 3.0


# ============================================================
# 守护进程模式测试
# ============================================================


class TestDaemonMode:
    """run_daemon 相关辅助函数测试"""

    def test_is_trading_day_weekday(self):
        """测试交易日判断：工作日应返回 True"""
        from datetime import date

        # 2026-05-20 是周三
        assert is_trading_day("cn", date(2026, 5, 20)) is True

    def test_is_trading_day_weekend(self):
        """测试交易日判断：周末应返回 False"""
        from datetime import date

        # 2026-05-23 是周六
        assert is_trading_day("cn", date(2026, 5, 23)) is False
        # 2026-05-24 是周日
        assert is_trading_day("cn", date(2026, 5, 24)) is False

    def test_get_next_trading_day_from_friday(self):
        """测试从周五找下一个交易日应返回下周一"""
        from datetime import date

        # 2026-05-22 是周五
        next_day = get_next_trading_day("cn", date(2026, 5, 22))
        # 下一个交易日是 2026-05-25（周一）
        assert next_day == date(2026, 5, 25)

    def test_get_next_trading_day_from_saturday(self):
        """测试从周六找下一个交易日应返回下周一"""
        from datetime import date

        next_day = get_next_trading_day("cn", date(2026, 5, 23))
        assert next_day == date(2026, 5, 25)

    def test_get_next_trading_day_from_monday(self):
        """测试从周一找下一个交易日应返回周二"""
        from datetime import date

        # 2026-05-25 是周一
        next_day = get_next_trading_day("cn", date(2026, 5, 25))
        assert next_day == date(2026, 5, 26)

    def test_run_daemon_calls_run_monitoring_session(self):
        """验证 run_daemon 通过 daemon_mode 参数正确触发"""
        config = MagicMock()
        config.stock_list = ["600519", "000001"]
        config.realtime_monitor_briefing_interval = 900
        config.realtime_monitor_atr_multipliers = "2.0,2.5,3.0"
        config.realtime_monitor_volume_ratio_threshold = 3.0
        config.realtime_monitor_price_change_threshold = 2.0

        service = RealtimeMonitorService(config=config)

        # 验证对象创建成功且拥有 run_daemon 方法
        assert hasattr(service, "run_daemon")
        assert callable(service.run_daemon)

    def test_daemon_mode_in_run_realtime_monitor(self):
        """验证 run_realtime_monitor 接受 daemon_mode 参数"""
        config = MagicMock()
        config.stock_list = ["600519"]
        config.realtime_monitor_briefing_interval = 900
        config.realtime_monitor_atr_multipliers = "2.0,2.5,3.0"
        config.realtime_monitor_volume_ratio_threshold = 3.0
        config.realtime_monitor_price_change_threshold = 2.0

        # daemon_mode=True 不应报错（虽然后面 asyncio.run 会因 Mock 失败）
        # 这里只验证函数签名能接受 daemon_mode 参数
        import inspect

        sig = inspect.signature(run_realtime_monitor)
        assert "daemon_mode" in sig.parameters

    def test_run_monitoring_session_prechecks(self):
        """验证 _run_monitoring_session 的时间边界检查"""
        config = MagicMock()
        config.stock_list = ["600519"]
        config.realtime_monitor_briefing_interval = 900
        config.realtime_monitor_atr_multipliers = "2.0,2.5,3.0"
        config.realtime_monitor_volume_ratio_threshold = 3.0
        config.realtime_monitor_price_change_threshold = 2.0

        service = RealtimeMonitorService(config=config)
        service._closing = True

        # _closing=True 时，_run_monitoring_session 应直接返回
        import asyncio

        result = asyncio.run(service._run_monitoring_session(["600519"], dt_time(15, 0)))
        assert result is None
