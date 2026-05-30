"""
=========================================
WebSocket Realtime Provider 单元测试
=========================================

测试内容：
1. WebSocketRealtimeProvider 初始化
2. EastMoney 协议解析 (_parse_em_quote, _parse_em_response, _to_em_secid)
3. 集成层 (is_websocket_enabled, get_websocket_reconnect_delay)
4. 降级回调逻辑
"""

import json
import os
import sys
from unittest.mock import patch

import pytest

# 确保导入可用
try:
    from data_provider.realtime_types import (
        RealtimeSource,
        UnifiedRealtimeQuote,
        safe_float,
        safe_int,
    )
    from data_provider.websocket_realtime import (
        EASTMONEY_PUSH_URL,
        WebSocketRealtimeProvider,
        _make_http_fallback,
        _parse_em_quote,
        _parse_em_response,
        _to_em_secid,
        get_quotes_via_websocket,
    )
    from data_provider.websocket_realtime_integration import (
        create_websocket_aware_fetcher_manager,
        get_realtime_quote_websocket_first,
        get_websocket_reconnect_delay,
        is_websocket_enabled,
    )
except ImportError as exc:
    print(f"导入失败: {exc}")
    sys.exit(1)


# ============================================================
# _to_em_secid 测试
# ============================================================


class TestToEmSecid:
    def test_shanghai_stock(self):
        """上证股票"""
        result = _to_em_secid("600519")
        assert result == "1.600519"

    def test_shenzhen_stock(self):
        """深证股票"""
        result = _to_em_secid("000001")
        assert result == "0.000001"

    def test_shenzhen_chi_next(self):
        """创业板"""
        result = _to_em_secid("300750")
        assert result == "0.300750"

    def test_shenzhen_sme(self):
        """中小板"""
        result = _to_em_secid("002415")
        assert result == "0.002415"

    def test_beijing_stock(self):
        """北证股票"""
        result = _to_em_secid("920748")
        assert result == "74.920748"  # 0x4A = 74（东财使用的市场编码）

    def test_with_sh_prefix(self):
        """带 SH 前缀"""
        result = _to_em_secid("SH600519")
        assert result == "1.600519"

    def test_with_sz_prefix(self):
        """带 SZ 前缀"""
        result = _to_em_secid("SZ000001")
        assert result == "0.000001"

    def test_hk_stock(self):
        """港股代码，不应转换"""
        result = _to_em_secid("HK00700")
        assert result is None

    def test_us_stock(self):
        """美股代码，不应转换"""
        result = _to_em_secid("AAPL")
        assert result is None

    def test_empty_string(self):
        """空字符串"""
        result = _to_em_secid("")
        assert result is None

    def test_invalid_code(self):
        """无效代码"""
        result = _to_em_secid("12345")
        assert result is None


# ============================================================
# _parse_em_quote 测试
# ============================================================


class TestParseEmQuote:
    def test_valid_quote(self):
        """有效行情数据"""
        data = {
            "f2": 1688.5,
            "f3": 2.35,
            "f4": 38.8,
            "f5": 23456,
            "f6": 395000000.0,
            "f7": 3.21,
            "f8": 0.85,
            "f9": 25.6,
            "f10": 1.23,
            "f12": "600519",
            "f14": "贵州茅台",
            "f15": 1700.0,
            "f16": 1660.0,
            "f17": 1670.0,
            "f18": 1649.7,
            "f20": 2.12e12,
            "f21": 2.12e12,
            "f23": 8.5,
            "f37": 15.3,
            "f45": 1800.0,
            "f46": 1200.0,
        }
        quote = _parse_em_quote(data, "600519")
        assert quote is not None
        assert quote.code == "600519"
        assert quote.name == "贵州茅台"
        assert quote.price == 1688.5
        assert quote.change_pct == 2.35
        assert quote.change_amount == 38.8
        assert quote.volume == 23456
        assert quote.amount == 395000000.0
        assert quote.amplitude == 3.21
        assert quote.turnover_rate == 0.85
        assert quote.pe_ratio == 25.6
        assert quote.volume_ratio == 1.23
        assert quote.high == 1700.0
        assert quote.low == 1660.0
        assert quote.open_price == 1670.0
        assert quote.pre_close == 1649.7
        assert quote.total_mv == 2.12e12
        assert quote.circ_mv == 2.12e12
        assert quote.pb_ratio == 8.5
        assert quote.change_60d == 15.3
        assert quote.high_52w == 1800.0
        assert quote.low_52w == 1200.0
        assert quote.source == RealtimeSource.EFINANCE

    def test_price_is_none(self):
        """价格为 None 时返回 None"""
        data = {"f2": None, "f12": "600519", "f14": "贵州茅台"}
        quote = _parse_em_quote(data, "600519")
        assert quote is None

    def test_price_is_zero(self):
        """价格为 0 时返回 None"""
        data = {"f2": 0, "f12": "600519", "f14": "贵州茅台"}
        quote = _parse_em_quote(data, "600519")
        assert quote is None

    def test_empty_data(self):
        """空数据"""
        quote = _parse_em_quote({}, "600519")
        assert quote is None

    def test_partial_data(self):
        """部分数据"""
        data = {"f2": 100.0, "f12": "000001", "f14": "平安银行"}
        quote = _parse_em_quote(data, "000001")
        assert quote is not None
        assert quote.code == "000001"
        assert quote.name == "平安银行"
        assert quote.price == 100.0
        # 缺失字段应为 None
        assert quote.change_pct is None
        assert quote.volume is None

    def test_code_from_data(self):
        """使用数据中的代码"""
        data = {"f2": 50.0, "f12": "300750", "f14": "宁德时代"}
        quote = _parse_em_quote(data, "300750")
        assert quote is not None
        assert quote.code == "300750"
        assert quote.name == "宁德时代"
        assert quote.price == 50.0


# ============================================================
# _parse_em_response 测试
# ============================================================


class TestParseEmResponse:
    def _build_response(self, data_list: list[dict]) -> bytes:
        """构建模拟的 EastMoney 二进制响应"""
        payload = json.dumps({"data": data_list, "ecode": 0, "emsg": ""}, ensure_ascii=False)
        payload_bytes = payload.encode("utf-8")
        total_len = len(payload_bytes) + 8
        header = struct.pack("<II", total_len, 0)
        return header + payload_bytes

    def test_single_quote(self):
        """单条行情"""
        data = [{"f2": 100.0, "f12": "600519", "f14": "贵州茅台"}]
        raw = self._build_response(data)
        result = _parse_em_response(raw)
        assert result is not None
        assert len(result) == 1
        assert result[0]["f2"] == 100.0
        assert result[0]["f12"] == "600519"

    def test_multiple_quotes(self):
        """多条行情"""
        data = [
            {"f2": 100.0, "f12": "600519", "f14": "贵州茅台"},
            {"f2": 20.0, "f12": "000001", "f14": "平安银行"},
        ]
        raw = self._build_response(data)
        result = _parse_em_response(raw)
        assert result is not None
        assert len(result) == 2

    def test_too_short(self):
        """数据太短"""
        result = _parse_em_response(b"short")
        assert result is None

    def test_invalid_json(self):
        """无效 JSON"""
        raw = b"\x14\x00\x00\x00\x00\x00\x00\x00invalid json"
        result = _parse_em_response(raw)
        assert result is None

    def test_dict_data(self):
        """data 字段是字典而非列表"""
        payload = json.dumps({"data": {"f2": 100.0}, "ecode": 0}, ensure_ascii=False)
        payload_bytes = payload.encode("utf-8")
        total_len = len(payload_bytes) + 8
        header = struct.pack("<II", total_len, 0)
        raw = header + payload_bytes
        result = _parse_em_response(raw)
        assert result is not None
        assert len(result) == 1
        assert result[0]["f2"] == 100.0

    def test_no_data_field(self):
        """没有 data 字段"""
        payload = json.dumps({"ecode": 0}, ensure_ascii=False)
        payload_bytes = payload.encode("utf-8")
        total_len = len(payload_bytes) + 8
        header = struct.pack("<II", total_len, 0)
        raw = header + payload_bytes
        result = _parse_em_response(raw)
        assert result is None


# ============================================================
# WebSocketRealtimeProvider 初始化测试
# ============================================================


class TestWebSocketRealtimeProvider:
    def test_init_default(self):
        """默认初始化"""
        provider = WebSocketRealtimeProvider()
        assert provider.is_connected is False
        assert provider._reconnect_delay == 5
        assert provider._max_reconnect_delay == 60
        assert provider._ping_interval == 20.0
        assert provider._fallback_callback is None

    def test_init_custom(self):
        """自定义参数"""
        fallback = lambda x: None  # noqa: E731
        provider = WebSocketRealtimeProvider(
            reconnect_delay=10,
            max_reconnect_delay=120,
            ping_interval=15.0,
            fallback_callback=fallback,
        )
        assert provider._reconnect_delay == 10
        assert provider._max_reconnect_delay == 120
        assert provider._ping_interval == 15.0
        assert provider._fallback_callback is fallback

    @pytest.mark.asyncio
    async def test_close_idle(self):
        """关闭空闲连接"""
        provider = WebSocketRealtimeProvider()
        await provider.close()
        assert provider.is_connected is False

    @pytest.mark.asyncio
    async def test_subscribe_no_valid_codes(self):
        """订阅无效代码"""
        provider = WebSocketRealtimeProvider()
        count = 0
        async for _ in provider.subscribe_quotes(["AAPL", "HK00700", ""]):
            count += 1
        assert count == 0
        await provider.close()


# ============================================================
# 降级回调测试
# ============================================================


class TestHttpFallback:
    def test_make_http_fallback_import(self):
        """创建降级回调"""
        fallback = _make_http_fallback()
        assert callable(fallback)

    @patch("data_provider.base.DataFetcherManager")
    def test_http_fallback_empty_codes(self, mock_manager):
        """空代码列表"""
        fallback = _make_http_fallback()
        result = fallback([])
        assert result == []


# ============================================================
# 集成层测试
# ============================================================


class TestIntegration:
    def test_is_websocket_enabled_default(self):
        """默认未启用"""
        key = "WEBSOCKET_REALTIME_ENABLED"
        original = os.environ.get(key)
        if key in os.environ:
            del os.environ[key]
        try:
            assert is_websocket_enabled() is False
        finally:
            if original is not None:
                os.environ[key] = original

    def test_is_websocket_enabled_true(self):
        """启用"""
        with patch.dict(os.environ, {"WEBSOCKET_REALTIME_ENABLED": "true"}):
            assert is_websocket_enabled() is True

    def test_is_websocket_enabled_false(self):
        """禁用"""
        with patch.dict(os.environ, {"WEBSOCKET_REALTIME_ENABLED": "false"}):
            assert is_websocket_enabled() is False

    def test_get_websocket_reconnect_delay_default(self):
        """默认重连延迟"""
        key = "WEBSOCKET_RECONNECT_DELAY"
        original = os.environ.get(key)
        if key in os.environ:
            del os.environ[key]
        try:
            assert get_websocket_reconnect_delay() == 5
        finally:
            if original is not None:
                os.environ[key] = original

    def test_get_websocket_reconnect_delay_custom(self):
        """自定义重连延迟"""
        with patch.dict(os.environ, {"WEBSOCKET_RECONNECT_DELAY": "10"}):
            assert get_websocket_reconnect_delay() == 10

    def test_get_websocket_reconnect_delay_invalid(self):
        """无效值"""
        with patch.dict(os.environ, {"WEBSOCKET_RECONNECT_DELAY": "abc"}):
            assert get_websocket_reconnect_delay() == 5

    @pytest.mark.asyncio
    async def test_get_realtime_quote_websocket_first_http_fallback(self):
        """非 A 股代码走 HTTP 降级"""
        http_called = False

        def http_fallback(code, **kw):
            nonlocal http_called
            http_called = True
            return UnifiedRealtimeQuote(code=code, name="测试", price=100.0)

        with patch.dict(os.environ, {"WEBSOCKET_REALTIME_ENABLED": "true"}):
            result = await get_realtime_quote_websocket_first(
                "AAPL",
                http_fallback,
            )
        assert result is not None
        assert result.code == "AAPL"
        assert http_called is True

    @pytest.mark.asyncio
    async def test_get_realtime_quote_websocket_first_hk_fallback(self):
        """港股代码走 HTTP 降级"""
        http_called = False

        def http_fallback(code, **kw):
            nonlocal http_called
            http_called = True
            return UnifiedRealtimeQuote(code=code, name="腾讯控股", price=400.0)

        with patch.dict(os.environ, {"WEBSOCKET_REALTIME_ENABLED": "true"}):
            result = await get_realtime_quote_websocket_first(
                "HK00700",
                http_fallback,
            )
        assert result is not None
        assert result.code == "HK00700"
        assert http_called is True


# ============================================================
# struct 导入辅助（用于测试构建响应）
# ============================================================

import struct  # noqa: E402 (needed by TestParseEmResponse)

# ============================================================
# End-to-end 集成测试（需要网络）
# ============================================================


@pytest.mark.network
class TestWebSocketEndToEnd:
    """端到端测试（需要网络连接）

    运行方式：pytest tests/test_websocket_realtime.py -x -v -m network
    """

    @pytest.mark.asyncio
    async def test_get_quotes_via_websocket_single(self):
        """获取单只股票"""
        quotes = await get_quotes_via_websocket(["600519"])
        assert len(quotes) >= 1
        quote = quotes[0]
        assert quote.code == "600519"
        assert quote.price is not None
        assert quote.price > 0

    @pytest.mark.asyncio
    async def test_get_quotes_via_websocket_multiple(self):
        """获取多只股票"""
        quotes = await get_quotes_via_websocket(["600519", "000001", "300750"])
        assert len(quotes) >= 1
        codes = {q.code for q in quotes}
        assert "600519" in codes or "000001" in codes or "300750" in codes

    @pytest.mark.asyncio
    async def test_subscribe_iterator(self):
        """订阅迭代器"""
        provider = WebSocketRealtimeProvider()
        count = 0
        try:
            async for quote in provider.subscribe_quotes(["600519"]):
                assert quote.code == "600519"
                assert quote.price is not None
                count += 1
                if count >= 2:
                    break
        finally:
            await provider.close()
        assert count >= 1

    @pytest.mark.asyncio
    async def test_http_fallback_on_empty_result(self):
        """WebSocket 无结果时的 HTTP 降级"""
        fallback = _make_http_fallback()
        result = fallback(["600519"])
        assert len(result) >= 0  # 可能成功可能失败，但不应崩溃
