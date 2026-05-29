"""
=============================================
WebSocket Realtime Quote Provider (A股/港股/美股)
=============================================

使用东方财富 push2 WebSocket 协议实现实时行情推送，
替换现有 HTTP 轮询模式。

设计：
1. WebSocketRealtimeProvider 维护一个长连接，订阅多只股票
2. subscribe_quotes() 返回 AsyncIterator[UnifiedRealtimeQuote]
3. 自动重连 + 指数退避
4. 失败时降级到 HTTP 轮询（自动检测并调用 DataFetcherManager）
5. 通过 DataFetcherManager 集成：当 enable_realtime_websocket=true 时优先使用 WebSocket
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import websockets.exceptions

from data_provider.realtime_types import (
    RealtimeSource,
    UnifiedRealtimeQuote,
    safe_float,
    safe_int,
)

logger = logging.getLogger(__name__)

# ============================================================
# Tencent WebSocket 配置
# ============================================================
# 腾讯财经 WebSocket 实时行情推送
# 协议：连接后发送股票代码（逗号分隔），持续接收行情数据
TENCENT_WS_URL = "wss://wptcp.qt.gtimg.cn/"

# EastMoney HTTP API（作为备用数据源）
EASTMONEY_HTTP_URL = "https://push2.eastmoney.com/api/qt/stock/get"

# EastMoney WebSocket 推送 URL
EASTMONEY_PUSH_URL = "wss://push2.eastmoney.com/PushServer/WebSocket"

# 字段枚举（EastMoney 返回的 JSON 键）
# f2=最新价, f3=涨跌幅(%), f4=涨跌额, f5=成交量(手), f6=成交额(元)
# f7=振幅(%), f8=换手率(%), f9=动态市盈率, f10=量比
# f12=股票代码, f14=股票名称
# f15=最高, f16=最低, f17=今开, f18=昨收
# f20=总市值, f21=流通市值, f23=市净率
# f37=60日涨跌幅(%), f45=52周最高, f46=52周最低

_EM_FIELDS = "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23,f37,f45,f46"

# A 股市场代码：SH=1, SZ=0, BJ=0x4A
_MARKET_MAP: dict[str, int] = {}
# 上证代码前缀
for _prefix in (
    "600",
    "601",
    "603",
    "605",
    "688",
    "689",
    "510",
    "511",
    "512",
    "513",
    "515",
    "516",
    "517",
    "518",
    "560",
    "561",
    "562",
    "563",
    "564",
    "588",
    "501",
    "502",
    "503",
    "506",
):
    _MARKET_MAP[_prefix] = 1
# 深证代码前缀
for _prefix in ("000", "001", "002", "003", "004", "300", "301", "302", "159", "160", "161", "162"):
    _MARKET_MAP[_prefix] = 0
# 北证代码前缀
for _prefix in (
    "920",
    "430",
    "830",
    "831",
    "832",
    "833",
    "834",
    "835",
    "836",
    "837",
    "838",
    "839",
    "870",
    "871",
    "872",
    "873",
    "874",
    "875",
    "876",
    "877",
    "878",
    "879",
):
    _MARKET_MAP[_prefix] = 0x4A

# ============================================================
# 内部数据结构
# ============================================================


@dataclass
class _WsState:
    """WebSocket 连接状态"""

    reconnect_delay: int = 5  # 重连延迟（秒）
    max_reconnect_delay: int = 60  # 最大重连延迟
    consecutive_failures: int = 0
    last_connected: float = 0.0


# ============================================================
# 工具函数
# ============================================================


def _to_em_secid(code: str) -> str | None:
    """将股票代码转换为 EastMoney secid 格式 (market.code)

    Args:
        code: 纯6位数字代码（如 600519, 000001）

    Returns:
        EastMoney secid 字符串 (如 "1.600519", "0.000001")，不支持的代码返回 None
    """
    code = code.strip().upper()
    # 去掉可能的 SH/SZ 前缀
    for prefix in ("SH", "SZ", "BJ", "SS"):
        if code.startswith(prefix):
            code = code[len(prefix) :]
            break
    # 去掉 .SH/.SZ/.BJ 后缀
    if "." in code:
        code = code.split(".")[0]
    # 去掉 HK 前缀（港股用专用接口）
    if code.startswith("HK") or not code.isdigit():
        return None
    if len(code) != 6:
        return None
    # 找到市场代码
    for prefix_len in range(3, 7):
        _prefix = code[:prefix_len]
        market = _MARKET_MAP.get(_prefix)
        if market is not None:
            return f"{market}.{code}"
    # 默认深证
    return f"0.{code}"


def _parse_em_quote(data: dict[str, Any], code: str) -> UnifiedRealtimeQuote | None:
    """解析 EastMoney WebSocket 返回的单条行情数据

    Args:
        data: EastMoney JSON 中的股票数据字段
        code: 原始股票代码

    Returns:
        UnifiedRealtimeQuote 对象，解析失败返回 None
    """
    try:
        price = safe_float(data.get("f2"))
        if price is None or price <= 0:
            return None

        name = str(data.get("f14", "")).strip()
        code_raw = str(data.get("f12", code))
        # 使用原始代码（优先从数据中获取）
        actual_code = code_raw or code

        quote = UnifiedRealtimeQuote(
            code=actual_code,
            name=name,
            source=RealtimeSource.EFINANCE,  # 东财数据源
            price=price,
            change_pct=safe_float(data.get("f3")),
            change_amount=safe_float(data.get("f4")),
            volume=safe_int(data.get("f5")),
            amount=safe_float(data.get("f6")),
            amplitude=safe_float(data.get("f7")),
            turnover_rate=safe_float(data.get("f8")),
            volume_ratio=safe_float(data.get("f10")),
            high=safe_float(data.get("f15")),
            low=safe_float(data.get("f16")),
            open_price=safe_float(data.get("f17")),
            pre_close=safe_float(data.get("f18")),
            total_mv=safe_float(data.get("f20")),
            circ_mv=safe_float(data.get("f21")),
            pe_ratio=safe_float(data.get("f9")),
            pb_ratio=safe_float(data.get("f23")),
            change_60d=safe_float(data.get("f37")),
            high_52w=safe_float(data.get("f45")),
            low_52w=safe_float(data.get("f46")),
        )
        return quote
    except Exception as exc:
        logger.debug("[WebSocket实时] 解析 EastMoney 行情失败: %s", exc)
        return None


def _parse_em_response(raw: bytes) -> list[dict[str, Any]] | None:
    """解析 EastMoney WebSocket 二进制响应

    EastMoney 返回格式：
    - 4 bytes: total length (little-endian int32，包含这4字节)
    - 4 bytes: 未知 (通常为0)
    - 剩余部分: UTF-8 JSON 字符串

    Args:
        raw: WebSocket 收到的原始二进制数据

    Returns:
        解析后的行情数据列表，解析失败返回 None
    """
    try:
        if len(raw) < 8:
            return None
        # 跳过前 8 字节头部
        payload = raw[8:]
        # 尝试解码为 UTF-8 JSON
        text = payload.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        result = json.loads(text)
        if not isinstance(result, dict):
            return None
        data_arr = result.get("data")
        if data_arr is None:
            return None
        if isinstance(data_arr, dict):
            return [data_arr]
        if isinstance(data_arr, list):
            return data_arr
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, Exception) as exc:
        logger.debug("[WebSocket实时] 解析 EastMoney 协议失败: %s", exc)
        return None


# ============================================================
# WebSocket Realtime Provider
# ============================================================


class WebSocketRealtimeProvider:
    """
    基于 WebSocket 的实时行情 Provider

    使用东方财富 push2 WebSocket 协议获取 A 股实时行情。
    支持自动重连、订阅管理、优雅降级。

    使用方式：
        provider = WebSocketRealtimeProvider()
        async for quote in provider.subscribe_quotes(["600519", "000001"]):
            print(quote.price)
    """

    def __init__(
        self,
        reconnect_delay: int = 5,
        max_reconnect_delay: int = 60,
        ping_interval: float = 20.0,
        fallback_callback: Callable | None = None,
    ):
        """
        Args:
            reconnect_delay: 初始重连延迟（秒）
            max_reconnect_delay: 最大重连延迟（秒）
            ping_interval: WebSocket ping 间隔（秒）
            fallback_callback: 降级回调函数，接收 (codes: list[str]) -> list[UnifiedRealtimeQuote]
        """
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._ping_interval = ping_interval
        self._fallback_callback = fallback_callback

        # 连接状态
        self._ws: Any = None
        self._connected = False
        self._closing = False
        self._consecutive_failures = 0

        # 订阅管理
        self._subscribed_codes: set[str] = set()
        self._subscribed_lock = asyncio.Lock()

        # 事件通知
        self._quote_event = asyncio.Event()

        logger.info(
            "[WebSocket实时] WebSocketRealtimeProvider 初始化完成 (reconnect_delay=%ss, max_delay=%ss)",
            reconnect_delay,
            max_reconnect_delay,
        )

    # ==========================================================
    # 公共接口
    # ==========================================================

    async def subscribe_quotes(self, codes: list[str]) -> AsyncIterator[UnifiedRealtimeQuote]:
        """
        订阅实时行情，返回异步迭代器

        对每个订阅的股票代码，在收到行情更新时 yield UnifiedRealtimeQuote。
        连接失败时自动尝试重连；超过配置次数后降级到 HTTP 轮询。

        Args:
            codes: 股票代码列表（A股纯6位数字，如 ["600519", "000001"]）

        Yields:
            UnifiedRealtimeQuote: 实时行情数据

        Raises:
            asyncio.CancelledError: 当迭代器被取消时
        """
        # 过滤有效代码
        valid_codes = [c for c in codes if c and c.strip().isdigit() and len(c.strip()) == 6]
        if not valid_codes:
            logger.warning("[WebSocket实时] 没有有效的 A 股代码可订阅")
            return

        self._closing = False
        async with self._subscribed_lock:
            self._subscribed_codes.update(valid_codes)
            self._quote_event.set()  # 唤醒连接管理

        logger.info("[WebSocket实时] 订阅股票: %s", ", ".join(valid_codes))

        # 主循环：连接 → 接收 → yield → 失败重试
        retry_count = 0
        while not self._closing:
            try:
                ws = await self._connect()
                self._connected = True
                retry_count = 0

                # 发送订阅请求
                await self._send_subscribe(ws, valid_codes)

                # 持续接收行情
                async for quote in self._receive_loop(ws, set(valid_codes)):
                    if self._closing:
                        break
                    yield quote

            except asyncio.CancelledError:
                logger.info("[WebSocket实时] 订阅被取消")
                break
            except Exception as exc:
                self._connected = False
                retry_count += 1
                logger.warning(
                    "[WebSocket实时] 连接异常 (%s), 将在 %ss 后重试 (第 %s 次)", exc, self._reconnect_delay, retry_count
                )

                # 尝试降级到 HTTP 轮询
                if self._fallback_callback and retry_count >= 3:
                    logger.info("[WebSocket实时] WebSocket 连续失败 %s 次，降级到 HTTP 轮询", retry_count)
                    try:
                        fallback_quotes = self._fallback_callback(valid_codes)
                        if fallback_quotes:
                            for q in fallback_quotes:
                                if q is not None:
                                    yield q
                    except Exception as fb_exc:
                        logger.warning("[WebSocket实时] 降级回退也失败: %s", fb_exc)

                # 重连等待（指数退避）
                if retry_count >= 5:
                    logger.error("[WebSocket实时] 连续重连失败 %s 次，停止 WebSocket 连接", retry_count)
                    break

                if not self._closing:
                    await asyncio.sleep(self._reconnect_delay * min(2 ** (retry_count - 1), 30))

    async def close(self) -> None:
        """关闭 WebSocket 连接，释放资源"""
        self._closing = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as exc:
                logger.debug("[WebSocket实时] 关闭连接时忽略: %s", exc)
            self._ws = None
        self._connected = False
        logger.info("[WebSocket实时] 连接已关闭")

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._connected

    # ==========================================================
    # 内部方法
    # ==========================================================

    async def _connect(self) -> Any:
        """建立 WebSocket 连接（含重连逻辑）"""
        import websockets

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        logger.info("[WebSocket实时] 正在连接 EastMoney WebSocket: %s", EASTMONEY_PUSH_URL)
        extra_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Origin": "https://www.eastmoney.com",
        }
        ws = await websockets.connect(
            EASTMONEY_PUSH_URL,
            ping_interval=self._ping_interval,
            ping_timeout=10,
            close_timeout=5,
            additional_headers=extra_headers,
            open_timeout=15,
        )
        self._ws = ws
        self._consecutive_failures = 0
        logger.info("[WebSocket实时] EastMoney WebSocket 连接成功")
        return ws

    async def _send_subscribe(self, ws: Any, codes: list[str]) -> None:
        """发送订阅请求

        将股票代码转换为 EastMoney secid 格式并发送订阅
        """
        secids = []
        for code in codes:
            secid = _to_em_secid(code)
            if secid:
                secids.append(secid)

        if not secids:
            logger.warning("[WebSocket实时] 没有可转换的 secid")
            return

        # 分批发送（每批最多 100 只）
        batch_size = 100
        for offset in range(0, len(secids), batch_size):
            batch = secids[offset : offset + batch_size]
            request = {
                "page": 0,
                "pagesize": len(batch),
                "fields": _EM_FIELDS,
                "secids": batch,
            }
            request_json = json.dumps(request, ensure_ascii=False)
            await ws.send(request_json)
            logger.debug("[WebSocket实时] 发送订阅请求: secids=%s", batch)

    async def _receive_loop(
        self,
        ws: Any,
        valid_codes: set[str],
    ) -> AsyncIterator[UnifiedRealtimeQuote]:
        """接收行情数据并解析

        持续从 WebSocket 读取数据，解析为 UnifiedRealtimeQuote，逐个 yield。

        Args:
            ws: WebSocket 连接对象
            valid_codes: 有效的股票代码集合（用于过滤）

        Yields:
            UnifiedRealtimeQuote: 解析后的行情数据
        """
        while not self._closing:
            try:
                # 读取原始数据
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
                if isinstance(raw, (bytes, bytearray)):
                    quotes_data = _parse_em_response(bytes(raw))
                elif isinstance(raw, str):
                    # 尝试直接解析为 JSON
                    try:
                        result = json.loads(raw)
                        data_arr = result.get("data") if isinstance(result, dict) else None
                    except json.JSONDecodeError:
                        # 尝试作为二进制解析（字符串可能包含二进制数据）
                        quotes_data = _parse_em_response(raw.encode("utf-8"))
                        if not quotes_data:
                            continue
                        data_arr = quotes_data
                    if data_arr is None:
                        continue
                    if isinstance(data_arr, dict):
                        quotes_data = [data_arr]
                    else:
                        quotes_data = data_arr if isinstance(data_arr, list) else None
                else:
                    continue

                if not quotes_data:
                    continue

                self._consecutive_failures = 0

                # 解析每条行情
                for item in quotes_data:
                    if not isinstance(item, dict):
                        continue
                    code_raw = str(item.get("f12", "")).strip()
                    code = code_raw or ""
                    # 如果代码不在订阅列表中，跳过（兼容返回额外数据的情况）
                    if valid_codes and code not in valid_codes:
                        continue
                    quote = _parse_em_quote(item, code)
                    if quote is not None:
                        yield quote

            except TimeoutError:
                # 超时是正常的，发送心跳维持连接
                logger.debug("[WebSocket实时] 接收超时，发送心跳")
                try:
                    await ws.send("ping")
                except Exception:
                    pass
                continue
            except websockets.exceptions.ConnectionClosed as exc:
                logger.warning("[WebSocket实时] 连接关闭 (code=%s): %s", exc.code, exc.reason)
                break
            except Exception as exc:
                logger.warning("[WebSocket实时] 接收数据异常: %s", exc)
                break


# ============================================================
# 工厂函数：创建 WebSocket 降级回调
# ============================================================


def _make_http_fallback() -> Callable:
    """创建 HTTP 降级回调函数

    当 WebSocket 连接失败时，使用 DataFetcherManager 的 HTTP 接口获取行情。
    """

    def fallback(codes: list[str]) -> list[UnifiedRealtimeQuote]:
        """降级获取：使用 DataFetcherManager 的 HTTP 轮询"""
        try:
            from data_provider.base import DataFetcherManager

            manager = DataFetcherManager()
            results = []
            for code in codes:
                try:
                    quote = manager.get_realtime_quote(code, log_final_failure=False)
                    if quote is not None:
                        results.append(quote)
                except Exception as exc:
                    logger.debug("[WebSocket降级] HTTP 获取 %s 失败: %s", code, exc)
            return results
        except ImportError:
            logger.warning("[WebSocket降级] 无法导入 DataFetcherManager")
            return []
        except Exception as exc:
            logger.error("[WebSocket降级] 降级获取失败: %s", exc)
            return []

    return fallback


# ============================================================
# 便捷函数
# ============================================================


async def get_quotes_via_websocket(
    codes: list[str],
    reconnect_delay: int = 5,
    max_retries: int = 3,
) -> list[UnifiedRealtimeQuote]:
    """通过 WebSocket 批量获取实时行情（一次性快照）

    这是 subscribe_quotes 的便捷包装，适用于"获取一次就退出"的场景。
    连接 WebSocket，等待数据返回，然后关闭连接。

    Args:
        codes: 股票代码列表
        reconnect_delay: 重连延迟（秒）
        max_retries: 最大重试次数（包括初始连接）

    Returns:
        行情数据列表
    """
    fallback = _make_http_fallback()
    provider = WebSocketRealtimeProvider(
        reconnect_delay=reconnect_delay,
        fallback_callback=fallback,
    )

    results: list[UnifiedRealtimeQuote] = []
    received_codes: set[str] = set()

    try:
        # 超时控制
        timeout = min(10 + len(codes) * 0.5, 30)
        async with asyncio.timeout(timeout):
            async for quote in provider.subscribe_quotes(codes):
                if quote.code not in received_codes:
                    results.append(quote)
                    received_codes.add(quote.code)
                # 收到所有数据后退出
                if len(received_codes) >= len(codes):
                    break
    except TimeoutError:
        logger.warning("[WebSocket实时] 获取超时，已获取 %s/%s 只股票", len(received_codes), len(codes))
    except Exception as exc:
        logger.warning("[WebSocket实时] 获取异常: %s", exc)
    finally:
        await provider.close()

    # 如果 WebSocket 获取不足，尝试 HTTP 降级
    missing = [c for c in codes if c not in received_codes]
    if missing:
        logger.info("[WebSocket实时] %s 只股票由 HTTP 降级获取: %s", len(missing), missing)
        try:
            fallback_results = fallback(missing)
            results.extend(fallback_results)
        except Exception as exc:
            logger.warning("[WebSocket降级] 降级失败: %s", exc)

    return results
