"""
=============================================
盘中实时监控服务 RealtimeMonitorService
=============================================

基于 WebSocket 实时行情的盘中监控服务，覆盖 3 个阶段：

Phase 1: 每15分钟盘中简报
Phase 2: ATR止损实时监控
Phase 3: 量价异动 + 均线突破告警

依赖：
- data_provider/websocket_realtime.py  (WebSocket 实时行情)
- src/core/indicators.py              (ATR/MA 指标计算)
- src/core/position_sizer.py          (ATR 止损位计算)
- src/notification.py                 (多渠道告警推送)
- data_provider/__init__.py           (DataFetcherManager 获取日线数据)
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from dataclasses import dataclass, field
from datetime import date as dt_date
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from typing import Any

from src.config import Config, get_config

logger = logging.getLogger(__name__)

# ============================================================
# A股交易时段常量
# ============================================================

T_0915 = dt_time(9, 15)  # 集合竞价开始 / 监控预热
T_0930 = dt_time(9, 30)  # 连续交易开始
T_1130 = dt_time(11, 30)  # 午休开始
T_1300 = dt_time(13, 0)  # 下午交易开始
T_1500 = dt_time(15, 0)  # 收盘

# ============================================================
# 数据结构
# ============================================================


@dataclass
class StockIntradayState:
    """单只股票的盘中状态（协程内可变，非线程安全）"""

    code: str
    name: str = ""

    # --- Phase 2: ATR 止损 ---
    atr14: float = 0.0
    stop_loss_2x: float = 0.0
    stop_loss_2_5x: float = 0.0
    stop_loss_3x: float = 0.0
    pre_close: float = 0.0

    # --- Phase 3: 均线 ---
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0

    # --- Phase 3: 历史价格用于跨越检测 ---
    prev_close: float = 0.0
    last_tick_price: float = 0.0  # 上一 tick 价格，用于真正穿越检测（首 tick 回退到 prev_close）

    # --- 简报缓存 ---
    last_brief_price: float = 0.0
    last_brief_change_pct: float = 0.0
    last_brief_volume_ratio: float = 0.0
    last_brief_turnover_rate: float = 0.0
    # 窗口追踪（每15分钟重置）
    window_high: float = 0.0
    window_low: float = 1e9
    window_start_price: float = 0.0

    # --- 最新分析评分 ---
    score: int = 0

    # --- 去重 ---
    last_atr_alert_time: float = 0.0
    last_cross_alert_time_per_ma: dict[str, float] = field(
        default_factory=lambda: {
            "ma5": 0.0,
            "ma10": 0.0,
            "ma20": 0.0,
        }
    )
    # 记录上次发送的均线穿越方向（"突破"/"跌破"/""），方向不变不重复推
    last_cross_direction_per_ma: dict[str, str] = field(
        default_factory=lambda: {"ma5": "", "ma10": "", "ma20": ""}
    )
    # 同只股票任意均线预警的全局冷却（秒），防止不同MA周期密集触发
    last_any_cross_alert_time: float = 0.0
    last_volume_alert_time: float = 0.0

    # --- 支撑/阻力 ---
    support_str: str = ""
    resistance_str: str = ""
    position_label: str = ""


# ============================================================
# ATR 计算（精简版，直接使用 indicators.py）
# ============================================================


def _compute_atr_from_df(df_rows: list[dict[str, Any]], period: int = 14) -> float:
    """从日线数据计算最近 ATR 值（委托 indicators.atr，统一实现）。"""
    from src.core.indicators import atr as _atr_indicator
    highs = [r.get("high", 0) or 0 for r in df_rows]
    lows = [r.get("low", 0) or 0 for r in df_rows]
    closes = [r.get("close", 0) or 0 for r in df_rows]
    atr_arr = _atr_indicator(highs, lows, closes, period=period)
    # Return last valid (non-NaN) value
    valid = [v for v in atr_arr if v == v]
    return valid[-1] if valid else 0.0


def _compute_ma(closes: list[float], period: int) -> float:
    """计算简单移动平均"""
    if len(closes) < period:
        return 0.0
    return sum(closes[-period:]) / period


# ============================================================
# 简报格式化
# ============================================================


def _build_briefing_text(
    stocks: list[StockIntradayState],
    quotes: dict[str, Any],
) -> str:
    """生成盘中简报文本（15分钟窗口总结 + 走势判断）"""
    lines: list[str] = []
    tz = timezone(timedelta(hours=8))
    now_str = datetime.now(tz).strftime("%H:%M")
    lines.append(f"📊{now_str}盘中速报")

    # 先算每支股票的状态 → 按类别 + 涨跌幅排序
    _sorted_data = []
    for s in stocks:
        q = quotes.get(s.code, {})
        price = q.get("price", s.last_brief_price)
        change_pct = q.get("change_pct", s.last_brief_change_pct)
        vol = q.get("volume_ratio", s.last_brief_volume_ratio)
        name = s.name or s.code

        change_str = f"{change_pct:+.1f}%" if change_pct != 0 else " 0.0%"

        # MA方向
        ma_parts = []
        for period, val in [("MA5", s.ma5), ("MA10", s.ma10), ("MA20", s.ma20)]:
            if val > 0 and price > 0:
                if price > val:
                    ma_parts.append(f">{period}")
                elif price < val:
                    ma_parts.append(f"<{period}")
                else:
                    ma_parts.append(f"={period}")
        ma_info = " ".join(ma_parts) if ma_parts else ""

        status = _briefing_status(price, change_pct, vol, s, ma_info)

        # 分类排序: 涨=0 震荡=1 跌=2 止损=3
        if status in ("强势拉升", "温和上行"):
            cat = 0
        elif status in ("均线上方盘整", "短线争夺", "回踩企稳", "震荡观望"):
            cat = 1
        elif status in ("加速下行", "明显走弱", "弱势震荡"):
            cat = 2
        else:  # 止损警戒
            cat = 3
        _sorted_data.append((cat, -change_pct, s, q, price, change_pct, vol, name, change_str, ma_info, status))

    _sorted_data.sort(key=lambda x: (x[0], x[1]))

    for _, _, s, q, price, change_pct, vol, name, change_str, ma_info, status in _sorted_data:

        # 止损价
        stop_info = ""
        if s.stop_loss_2x > 0:
            if price <= s.stop_loss_2x:
                stop_info = "已破止损"
            else:
                stop_info = f"止损 ¥{s.stop_loss_2x:.2f}"

        # 走势判断
        outlook = _briefing_outlook(price, change_pct, vol, s, status)

        # ── 颜色标记（每支股票开头，符合中国股市习惯）──
        # 🔴红=涨 🟢绿=跌 🟡金=震荡 ⚪灰=止损
        if status in ("强势拉升", "温和上行"):
            color_icon = "🔴"
        elif status in ("加速下行", "明显走弱", "弱势震荡"):
            color_icon = "🟢"
        elif status == "止损警戒":
            color_icon = "⚪"
        else:
            color_icon = "🟡"  # 均线上方盘整/短线争夺/回踩企稳/震荡观望

        fields = [f"{color_icon} {name}({s.code})", f"¥{price:.2f} {change_str}"]
        if s.score > 0:
            fields.append(f"评分{s.score}")
        # 窗口区间（最近15分钟价格区间）
        if s.window_high > 0 and s.window_low < 1e8:
            fields.append(f"¥{s.window_low:.2f}~¥{s.window_high:.2f}")
        fields.append(f"量比{vol:.1f}")
        if ma_info:
            fields.append(ma_info)
        if stop_info:
            fields.append(stop_info)
        # 走势预判: 方向箭头+状态+展望（无标点）
        direction = "→"
        for kw in ["加速下行", "明显走弱", "弱势", "不宜抄底", "磨底", "止跌", "规避", "回落", "警戒", "关注止损"]:
            if kw in status or (outlook and kw in outlook):
                direction = "↘"
                break
        for kw in ["强势拉升", "温和上行", "趋势偏多", "持有", "反弹", "上行"]:
            if kw in status or (outlook and kw in outlook):
                direction = "↗"
                break
        assessment = f"{direction}{status}"
        if outlook:
            assessment += outlook
        fields.append(f"走势{assessment}")
        lines.append(" | ".join(fields))

    return "\n".join(lines)


def _briefing_status(price: float, change_pct: float, vol: float, s: StockIntradayState, ma_info: str) -> str:
    """根据当前数据生成状态总结（一两个词）"""
    # 止损临近
    if s.stop_loss_2x > 0 and price > s.stop_loss_2x:
        dist = (price - s.stop_loss_2x) / s.stop_loss_2x * 100
        if dist < 3:
            return "止损警戒"

    # 加速下跌
    if "<MA5" in ma_info:
        if change_pct < -1.5 and vol > 1.2:
            return "加速下行"
        if change_pct < -1.0:
            return "明显走弱"
        return "弱势震荡"

    # 加速上涨
    if ">MA5" in ma_info and ">MA10" in ma_info:
        if change_pct > 1.5 and vol > 1.2:
            return "强势拉升"
        if change_pct > 1.0:
            return "温和上行"
        return "均线上方盘整"

    # 均线缠绕
    if ">MA5" in ma_info and "<MA10" in ma_info:
        return "短线争夺"
    if "<MA5" in ma_info and ">MA10" in ma_info:
        return "回踩企稳"

    return "震荡观望"


def _briefing_outlook(price: float, change_pct: float, vol: float, s: StockIntradayState, status: str) -> str:
    """基于窗口数据生成后续15分钟走势判断"""
    # 窗口振幅
    if s.window_high > 0 and s.window_low < 1e8:
        amp = (s.window_high - s.window_low) / price * 100
    else:
        amp = 0

    window_change = 0.0
    if s.window_start_price > 0 and price > 0:
        window_change = (price - s.window_start_price) / s.window_start_price * 100

    if status == "止损警戒":
        return "关注止损"
    if status == "加速下行":
        return "回避等企稳" if vol > 1.5 else "关注止跌"
    if status == "强势拉升":
        return "持有观察" if vol > 1.5 else "量能不足防回落"
    if status == "温和上行":
        return "趋势偏多"
    if status == "均线上方盘整":
        if amp < 0.5:
            return "窄幅整理等方向"
        return "高位震荡关注压力"
    if status == "短线争夺":
        if vol > 1.2:
            return "放量中方向待明朗"
        return "缩量整理等待选择"
    if status == "回踩企稳":
        if window_change > 0:
            return "回踩获支撑反弹"
        return "回踩中关注MA20"
    if status == "明显走弱":
        return "不宜抄底"
    if status == "弱势震荡":
        if vol < 0.5:
            return "缩量磨底"
        return "弱势整理"

    return ""


def _build_atr_alert_text(
    code: str,
    name: str,
    price: float,
    change_pct: float,
    stop_price: float,
    multiplier: float,
    score: int = 0,
) -> str:
    """生成 ATR 止损预警文本（移动端紧凑格式）"""
    now_str = datetime.now().strftime("%H:%M")
    change_sign = "+" if change_pct >= 0 else ""
    score_str = f" | 评分{score}" if score > 0 else ""
    return f"🚨{now_str} ATR止损{score_str}\n{name}({code}) ¥{price:.2f} {change_sign}{change_pct:.1f}% 跌破{multiplier}×ATR ¥{stop_price:.2f}"


def _build_volume_alert_text(
    code: str,
    name: str,
    price: float,
    change_pct: float,
    volume_ratio: float,
    turnover_rate: float,
    alert_type: str,
    stop_price: float = 0.0,
    score: int = 0,
) -> str:
    """生成量价异动预警文本"""
    emoji_map = {
        "放量拉升": "🔥",
        "缩量下跌": "💤",
        "换手率异常": "⚡",
    }
    emoji = emoji_map.get(alert_type, "📊")
    change_sign = "+" if change_pct >= 0 else ""

    tips = {
        "放量拉升": "主力介入",
        "缩量下跌": "观望为主",
        "换手率异常": "波动加剧",
    }
    tip = tips.get(alert_type, "")

    now_str = datetime.now().strftime("%H:%M")
    score_str = f" | 评分{score}" if score > 0 else ""
    return f"{emoji}{now_str}异动预警{score_str}\n{name}({code}) ¥{price:.2f} {change_sign}{change_pct:.1f}% 量比 {volume_ratio:.2f} 换手率 {turnover_rate:.1f}%"


def _build_ma_cross_alert_text(
    code: str,
    name: str,
    price: float,
    change_pct: float,
    ma_period: str,
    ma_value: float,
    cross_type: str,  # "突破" or "跌破"
    stop_price: float = 0.0,  # ATR止损价, 0=无
    score: int = 0,
) -> str:
    """生成均线突破/跌破预警文本"""
    emoji = "📈" if cross_type == "突破" else "📉"

    tips = {
        ("跌破", "MA5"): "短线转弱",
        ("跌破", "MA10"): "趋势走弱",
        ("跌破", "MA20"): "中期破位",
        ("突破", "MA5"): "短线企稳",
        ("突破", "MA10"): "趋势回暖",
        ("突破", "MA20"): "中期走强",
    }
    tip = tips.get((cross_type, ma_period), "")

    now_str = datetime.now().strftime("%H:%M")
    change_sign = "+" if change_pct >= 0 else ""
    tip_suffix = f" | {tip}" if tip else ""
    score_str = f" | 评分{score}" if score > 0 else ""
    return f"{emoji}{now_str}均线{cross_type}{score_str}\n{name}({code}) ¥{price:.2f} {change_sign}{change_pct:.1f}% {cross_type}{ma_period}(¥{ma_value:.2f})"


# ============================================================
# 辅助：交易日判断
# ============================================================


def is_trading_day(market: str, check_date: dt_date) -> bool:
    """检查是否为交易日，exchange_calendars 不可用时 fallback 到跳过周六日"""
    from src.core.trading_calendar import is_market_open

    try:
        if not is_market_open(market, check_date):
            return False
    except Exception:
        pass
    # Fallback: skip weekends
    return check_date.weekday() < 5


def get_next_trading_day(market: str, from_date: dt_date) -> dt_date:
    """找到 from_date 之后的下一个交易日"""
    next_date = from_date
    for _ in range(365):  # Safety limit
        next_date += timedelta(days=1)
        if is_trading_day(market, next_date):
            return next_date
    return from_date + timedelta(days=1)


# ============================================================
# 主服务
# ============================================================


class RealtimeMonitorService:
    """
    盘中实时监控服务

    基于 WebSocket 实时行情，实现：
    - Phase 1: 每 15 分钟输出盘中简报
    - Phase 2: ATR 止损实时监控（三级警戒）
    - Phase 3: 量价异动 + 均线突破告警

    使用方式：
        service = RealtimeMonitorService(config)
        await service.run()
    """

    def __init__(
        self,
        config: Config | None = None,
        *,
        briefing_interval: int = 900,
        atr_multipliers: list[float] = None,
        volume_ratio_threshold: float = 3.0,
        price_change_threshold: float = 2.0,
        turnover_rate_threshold: float = 10.0,
        atr_cooldown: float = 300.0,
        cross_cooldown: float = 600.0,
        cross_global_cooldown: float = 300.0,
        volume_cooldown: float = 300.0,
    ):
        self._config = config or get_config()

        # 配置参数
        self._briefing_interval = briefing_interval
        self._atr_multipliers = atr_multipliers or [2.0, 2.5, 3.0]
        self._volume_ratio_threshold = volume_ratio_threshold
        self._price_change_threshold = price_change_threshold
        self._turnover_rate_threshold = turnover_rate_threshold
        self._atr_cooldown = atr_cooldown
        self._cross_cooldown = cross_cooldown
        self._cross_global_cooldown = cross_global_cooldown
        self._volume_cooldown = volume_cooldown

        # 股票列表
        self._stock_codes: list[str] = []

        # 盘中状态（code -> StockIntradayState）
        self._states: dict[str, StockIntradayState] = {}
        # 最新行情快照（code -> quote dict）
        self._quotes: dict[str, dict[str, Any]] = {}

        # 简报定时器
        self._last_briefing_time: float = 0.0

        # 通知服务（延迟初始化）
        self._notifier = None

        # 终止信号
        self._closing = False

    async def _init_notifier(self) -> None:
        """延迟初始化通知服务"""
        if self._notifier is None:
            from src.notification import NotificationService

            self._notifier = NotificationService()

    ALERT_FILE = "/tmp/realtime_alerts.log"

    def _send_notification(self, text: str, route_type: str = "alert") -> None:
        """发送通知 — 推通知服务 + 写入共享文件供内部任务使用"""
        # 通道1: 通知服务（企业微信等）
        if self._notifier is not None and hasattr(self._notifier, "send"):
            try:
                self._notifier.send(text, route_type=route_type)
            except Exception:
                pass
        # 通道2: 写入共享文件（供 risk_radar.py 等内部脚本读取分析）
        # 不再作为 Hermes cron 桥接用途
        try:
            with open(self.ALERT_FILE, "a", encoding="utf-8") as f:
                f.write(f"{text}\n---\n")
        except Exception:
            pass

    def _append_alert_context(self, text: str, state: StockIntradayState, price: float = 0.0, stop_price: float = 0.0, tip: str = "") -> str:
        """追加支撑/压力/止损/操盘建议/操作建议到预警文本（顺序：支撑→压力→止损→操盘建议→操作）"""
        extras = []
        if state.support_str:
            extras.append(f"支撑 {state.support_str}")
        if state.resistance_str:
            extras.append(f"压力 {state.resistance_str}")
        if stop_price > 0:
            extras.append(f"止损 ¥{stop_price:.2f}")
        if tip:
            extras.append(tip)

        # 仓位标签 → 操作建议
            uptrend = price > state.ma5 > state.ma10 > 0
            mild = state.ma5 > price > state.ma10 > 0
            downtrend = state.ma10 > state.ma5 > price

            if state.position_label == "重仓":
                action = "持有" if uptrend else ("持有观察" if mild else "减仓")
            elif state.position_label == "中仓":
                action = "持有" if uptrend else ("观望" if not downtrend else "减仓")
            elif state.position_label == "轻仓":
                action = "加仓" if uptrend else "观望"
            else:
                action = state.position_label
            extras.append(action)
        elif state.position_label:
            extras.append(state.position_label)

        if extras:
            suffix = " | ".join(extras)
            # 最后两项去掉 | 和空格，直接连写让信息更紧凑
            # 例如"主力介入 | 持有"→"主力介入持有"
            if len(extras) >= 4:
                last_sep = suffix.rfind(" | ")
                if last_sep > 0:
                    suffix = suffix[:last_sep] + suffix[last_sep + 3:]
            text += " | " + suffix
        return text

    async def _load_historical_data(self) -> None:
        """从 DataFetcherManager 获取日线数据并计算 ATR / MA"""
        from data_provider import create_fetcher_manager

        fetcher = create_fetcher_manager()
        logger.info("[RealtimeMonitor] 开始加载各股票日线数据...")

        for code in self._stock_codes:
            try:
                df, source = await asyncio.to_thread(fetcher.get_daily_data, code, None, None, 60)
                if df is None or df.empty:
                    logger.warning("[RealtimeMonitor] %s 日线数据为空", code)
                    continue

                # 转换为 list of dict
                rows = df.to_dict("records")
                if not rows:
                    continue

                # 状态
                state = self._states.get(code)
                if state is None:
                    state = StockIntradayState(code=code)
                    self._states[code] = state
                    # prev_close (倒数第二条) + last_tick_price 初始化（仅首次创建时，避免 session 内重设）
                    _closes_tmp = [r.get("close", 0) or 0 for r in rows]
                    state.prev_close = _closes_tmp[-2] if len(_closes_tmp) >= 2 else _closes_tmp[-1]
                    state.last_tick_price = state.prev_close  # 首 tick 回退到 prev_close

                # 读取最新分析评分
                try:
                    import sqlite3
                    from pathlib import Path
                    db_path = Path(getattr(self._config, "database_path", "./data/stock_analysis.db"))
                    if db_path.exists():
                        with sqlite3.connect(str(db_path)) as db_con:
                            cur = db_con.execute(
                                "SELECT sentiment_score FROM analysis_history "
                                "WHERE code=? AND sentiment_score IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                                (code,),
                            )
                            row = cur.fetchone()
                            if row:
                                state.score = int(row[0])
                except Exception:
                    pass

                closes = [r.get("close", 0) or 0 for r in rows]
                highs = [r.get("high", 0) or 0 for r in rows]
                lows = [r.get("low", 0) or 0 for r in rows]

                # ATR
                atr_val = _compute_atr_from_df(rows, 14)
                state.atr14 = atr_val
                if atr_val > 0 and closes:
                    last_close = closes[-1]
                    state.stop_loss_2x = last_close - 2.0 * atr_val
                    state.stop_loss_2_5x = last_close - 2.5 * atr_val
                    state.stop_loss_3x = last_close - 3.0 * atr_val

                # MA
                if len(closes) >= 5:
                    state.ma5 = _compute_ma(closes, 5)
                if len(closes) >= 10:
                    state.ma10 = _compute_ma(closes, 10)
                if len(closes) >= 20:
                    state.ma20 = _compute_ma(closes, 20)

                # 名称
                if "name" in rows[-1]:
                    state.name = str(rows[-1].get("name", ""))

                # 支撑/阻力 + 仓位建议
                if len(rows) >= 20:
                    volumes = [r.get("volume", r.get("vol", 0)) or 0 for r in rows]
                    current = closes[-1]
                    from src.core.support_resistance import compute_levels

                    try:
                        supp, res = compute_levels(closes, highs, lows, volumes, current)
                        sd = [f"¥{s.price}({s.label})" for s in supp[:2] if "VWAP" not in s.label]
                        rd = [f"¥{r.price}({r.label})" for r in res[:2] if "VWAP" not in r.label]
                        if sd:
                            state.support_str = " ".join(sd)
                        if rd:
                            state.resistance_str = " ".join(rd)
                    except Exception:
                        pass
                    from src.core.position_sizer import compute_position_size

                    try:
                        ps = compute_position_size(current, atr_val) if atr_val > 0 else None
                        if ps:
                            state.position_label = ps.position_label
                    except Exception:
                        pass

                logger.info(
                    "[RealtimeMonitor] %s 初始化完成: ATR=%.3f 止损2x=%.2f 止损3x=%.2f MA5=%.2f MA10=%.2f MA20=%.2f",
                    code,
                    atr_val,
                    state.stop_loss_2x,
                    state.stop_loss_3x,
                    state.ma5,
                    state.ma10,
                    state.ma20,
                )

            except Exception as exc:
                logger.warning("[RealtimeMonitor] 加载 %s 日线数据失败: %s", code, exc)

        logger.info(
            "[RealtimeMonitor] 日线数据加载完成，共 %d 只股票",
            sum(1 for s in self._states.values() if s.atr14 > 0),
        )
        # 强制 GC 清理 Pytdx 等数据源的残留 socket 引用，避免 FD 泄漏 -> 进程崩溃
        import gc
        gc.collect()

    def run(self, stock_codes: list[str] | None = None) -> None:
        """同步入口：启动监控（会创建事件循环并阻塞）"""
        codes = stock_codes or self._stock_codes or self._config.stock_list
        asyncio.run(self._run_async(codes))

    async def _run_async(self, stock_codes: list[str]) -> None:
        """异步入口：启动监控主循环"""
        self._stock_codes = stock_codes
        self._closing = False

        logger.info(
            "[RealtimeMonitor] 启动盘中实时监控，股票: %s",
            ", ".join(stock_codes),
        )

        # 1. 加载日线数据（ATR / MA）
        await self._load_historical_data()

        # 2. 初始化通知服务
        await self._init_notifier()

        # 3. 连接到 WebSocket 并处理行情
        await self._websocket_loop()

    async def _websocket_loop(self) -> None:
        """WebSocket 行情处理主循环"""
        from data_provider.websocket_realtime import WebSocketRealtimeProvider

        provider = WebSocketRealtimeProvider()

        # 简报定时器（Phase 1）
        self._last_briefing_time = time.time()

        try:
            async for quote in provider.subscribe_quotes(self._stock_codes):
                if self._closing:
                    break

                code = quote.code
                if code not in self._stock_codes:
                    continue

                # 确保状态存在
                if code not in self._states:
                    self._states[code] = StockIntradayState(code=code)

                state = self._states[code]
                if not state.name and quote.name:
                    state.name = quote.name

                price = quote.price or 0
                change_pct = quote.change_pct or 0
                volume_ratio = quote.volume_ratio or 0
                turnover_rate = quote.turnover_rate or 0

                # 更新行情快照
                self._quotes[code] = {
                    "price": price,
                    "change_pct": change_pct,
                    "volume_ratio": volume_ratio,
                    "turnover_rate": turnover_rate,
                    "pre_close": quote.pre_close or state.pre_close,
                    "name": state.name or quote.name or "",
                }

                # 更新简报缓存
                state.last_brief_price = price
                state.last_brief_change_pct = change_pct
                state.last_brief_volume_ratio = volume_ratio
                state.last_brief_turnover_rate = turnover_rate

                # 更新窗口追踪
                if price > state.window_high:
                    state.window_high = price
                if price < state.window_low:
                    state.window_low = price
                if state.window_start_price == 0:
                    state.window_start_price = price

                now = time.time()

                # === Phase 2: ATR 止损检查 ===
                if state.atr14 > 0:
                    await self._check_atr_stop(code, state, price, change_pct, now)

                # === Phase 3: 量价异动 + 均线突破 ===
                await self._check_volume_alert(code, state, price, change_pct, volume_ratio, turnover_rate, now)
                await self._check_ma_cross(code, state, price, change_pct, now)

                # 更新 last_tick_price 供下次穿越检测（必须放在所有 Phase 检查之后）
                state.last_tick_price = price

                # === Phase 1: 每 15 分钟简报 ===
                await self._maybe_send_briefing(now)

        except asyncio.CancelledError:
            logger.info("[RealtimeMonitor] 监控被取消")
        except Exception as exc:
            logger.exception("[RealtimeMonitor] WebSocket 循环异常: %s", exc)
        finally:
            await provider.close()
            logger.info("[RealtimeMonitor] 监控服务已停止")

    async def _check_atr_stop(
        self,
        code: str,
        state: StockIntradayState,
        price: float,
        change_pct: float,
        now: float,
    ) -> None:
        """Phase 2: ATR 止损检查"""
        if state.atr14 <= 0:
            return

        # 冷却检查
        if now - state.last_atr_alert_time < self._atr_cooldown:
            return

        # 三级止损 [2.0, 2.5, 3.0]
        stop_levels = [
            (2.0, state.stop_loss_2x),
            (2.5, state.stop_loss_2_5x),
            (3.0, state.stop_loss_3x),
        ]

        trigger_level: float | None = None
        trigger_price: float | None = None

        for mult, stop_price in stop_levels:
            if stop_price > 0 and price <= stop_price:
                trigger_level = mult
                trigger_price = stop_price

        if trigger_level is not None and trigger_price is not None:
            state.last_atr_alert_time = now
            name = state.name or code
            text = _build_atr_alert_text(code, name, price, change_pct, trigger_price, trigger_level, state.score)
            text = self._append_alert_context(text, state, price, 0, "")
            logger.warning("[RealtimeMonitor] %s ATR止损触发: %.2f (%.1f×)", code, price, trigger_level)
            self._send_notification(text, route_type="alert")

    async def _check_volume_alert(
        self,
        code: str,
        state: StockIntradayState,
        price: float,
        change_pct: float,
        volume_ratio: float,
        turnover_rate: float,
        now: float,
    ) -> None:
        """Phase 3: 量价异动检测"""
        if now - state.last_volume_alert_time < self._volume_cooldown:
            return

        alert_type: str | None = None

        # 放量拉升：量比 > 阈值 且 涨幅 > 阈值
        if volume_ratio > self._volume_ratio_threshold and change_pct >= self._price_change_threshold:
            alert_type = "放量拉升"

        # 缩量下跌：量比 < 0.3 且 跌幅 < -阈值
        if volume_ratio < 0.3 and change_pct <= -self._price_change_threshold:
            alert_type = "缩量下跌"

        # 换手率异常：换手率 > 阈值
        if turnover_rate > self._turnover_rate_threshold:
            alert_type = "换手率异常"

        if alert_type:
            state.last_volume_alert_time = now
            name = state.name or code
            text = _build_volume_alert_text(
                code, name, price, change_pct, volume_ratio, turnover_rate, alert_type, state.stop_loss_2x, state.score
            )
            _tip_map = {"放量拉升": "主力介入", "缩量下跌": "观望为主", "换手率异常": "波动加剧"}
            text = self._append_alert_context(text, state, price, 0, _tip_map.get(alert_type, ""))
            logger.info("[RealtimeMonitor] %s 量价异动: %s", code, alert_type)
            self._send_notification(text, route_type="alert")

    async def _check_ma_cross(
        self,
        code: str,
        state: StockIntradayState,
        price: float,
        change_pct: float,
        now: float,
    ) -> None:
        """Phase 3: 均线突破/跌破检测（基于 last_tick_price 判断真正穿越事件）"""
        ma_configs = [
            ("MA5", state.ma5, state.last_tick_price, state.last_cross_alert_time_per_ma.get("ma5", 0.0), "ma5"),
            ("MA10", state.ma10, state.last_tick_price, state.last_cross_alert_time_per_ma.get("ma10", 0.0), "ma10"),
            ("MA20", state.ma20, state.last_tick_price, state.last_cross_alert_time_per_ma.get("ma20", 0.0), "ma20"),
        ]

        for ma_period, ma_value, prev_close, last_alert_time, ma_key in ma_configs:
            if ma_value <= 0:
                continue

            # 判断当前方向
            prev_dir = state.last_cross_direction_per_ma.get(ma_key, "")

            break_condition = prev_close < ma_value < price  # 突破
            drop_condition = prev_close > ma_value > price   # 跌破

            # 当前是否满足触发条件
            triggered = False
            new_direction = ""
            if break_condition:
                triggered = True
                new_direction = "突破"
            elif drop_condition:
                triggered = True
                new_direction = "跌破"

            if not triggered:
                continue

            # === 去重规则 ===

            # 规则1: 同方向不重复推（方向没变且冷却期内）
            if prev_dir == new_direction and now - last_alert_time < self._cross_cooldown:
                continue

            # 规则2: 方向变了但全局冷却期内（防止来回穿刷屏）
            if prev_dir != new_direction and now - state.last_any_cross_alert_time < self._cross_global_cooldown:
                continue

            # 通过去重 → 推送
            state.last_cross_alert_time_per_ma[ma_key] = now
            state.last_cross_direction_per_ma[ma_key] = new_direction
            state.last_any_cross_alert_time = now

            name = state.name or code
            if break_condition:
                text = _build_ma_cross_alert_text(code, name, price, change_pct, ma_period, ma_value, "突破", state.stop_loss_2x, state.score)
                logger.info("[RealtimeMonitor] %s %s突破: %.2f > %.2f", code, ma_period, price, ma_value)
                _ma_tip = {"MA5": "短线企稳", "MA10": "趋势回暖", "MA20": "中期走强"}.get(ma_period, "")
            else:
                text = _build_ma_cross_alert_text(code, name, price, change_pct, ma_period, ma_value, "跌破", state.stop_loss_2x, state.score)
                logger.info("[RealtimeMonitor] %s %s跌破: %.2f < %.2f", code, ma_period, price, ma_value)
                _ma_tip = {"MA5": "短线转弱", "MA10": "趋势走弱", "MA20": "中期破位"}.get(ma_period, "")
            text = self._append_alert_context(text, state, price, 0, _ma_tip)
            self._send_notification(text, route_type="alert")

    async def _maybe_send_briefing(self, now: float) -> None:
        """Phase 1: 按间隔发送盘中简报"""
        if now - self._last_briefing_time < self._briefing_interval:
            return

        self._last_briefing_time = now
        stocks = list(self._states.values())
        if not stocks:
            return

        text = _build_briefing_text(stocks, self._quotes)
        logger.info("[RealtimeMonitor] 发送盘中简报 (%d 只股票)", len(stocks))
        self._send_notification(text, route_type="briefing")

        # 重置窗口追踪
        for s in self._states.values():
            s.window_high = 0.0
            s.window_low = 1e9
            s.window_start_price = 0.0

    # ============================================================
    # 守护进程模式
    # ============================================================

    def run_daemon(self, stock_codes: list[str] | None = None) -> None:
        """
        守护进程模式：自动跟随 A 股交易时段。

        流程：
        1. 检查今日是否为交易日，否则等待下一个交易日
        2. 等待开盘（9:15 预热，9:30 开始推送）
        3. 运行盘中监控直到收盘（15:00）
        4. 收盘后等待下一个交易日，循环
        """
        from src.core.trading_calendar import get_market_now

        codes = stock_codes or self._config.stock_list
        self._stock_codes = codes
        logger.info("[RealtimeMonitor] 守护进程模式启动，股票: %s", ", ".join(codes))

        # 注册信号处理器，支持 systemd 优雅关闭
        def _sig_handler(signum, frame):
            self._closing = True
            sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT" if signum == signal.SIGINT else str(signum)
            logger.info("[RealtimeMonitor] 收到 %s，开始优雅关闭...", sig_name)

        signal.signal(signal.SIGTERM, _sig_handler)
        signal.signal(signal.SIGINT, _sig_handler)

        while not self._closing:
            try:
                now = get_market_now("cn")  # timezone-aware
                today = now.date()

                # 如果是非交易日，等待下一个交易日
                if not is_trading_day("cn", today):
                    next_day = get_next_trading_day("cn", today)
                    wait_seconds = (datetime.combine(next_day, T_0915, tzinfo=now.tzinfo) - now).total_seconds()
                    if wait_seconds > 0:
                        logger.info(
                            "[RealtimeMonitor] 今日非交易日，等待 %dh %.0fm 后下一个交易日 %s",
                            wait_seconds // 3600,
                            (wait_seconds % 3600) / 60,
                            next_day.isoformat(),
                        )
                        time.sleep(wait_seconds)
                    continue

                # 在交易时段内运行监控（分上午盘和下午盘，午休自动暂停）
                morning_start = datetime.combine(today, T_0915, tzinfo=now.tzinfo)
                morning_end = datetime.combine(today, T_1130, tzinfo=now.tzinfo)
                afternoon_start = datetime.combine(today, T_1300, tzinfo=now.tzinfo)
                afternoon_end = datetime.combine(today, T_1500, tzinfo=now.tzinfo)

                if now < morning_start:
                    # 还没开盘，等待开盘
                    wait_seconds = (morning_start - now).total_seconds()
                    logger.info(
                        "[RealtimeMonitor] 距离开盘还有 %dh %.0fm，等待中...",
                        wait_seconds // 3600,
                        (wait_seconds % 3600) / 60,
                    )
                    time.sleep(wait_seconds)

                elif now < morning_end:
                    # 上午盘：运行监控到 11:30
                    asyncio.run(self._run_monitoring_session(codes, T_1130))
                    logger.info("[RealtimeMonitor] 上午盘结束，进入午休")
                    continue

                elif now < afternoon_start:
                    # 午休时间：等待到 13:00
                    wait_seconds = (afternoon_start - now).total_seconds()
                    logger.info(
                        "[RealtimeMonitor] 午休中，等待 %.0f 分钟后下午开盘...",
                        wait_seconds / 60,
                    )
                    # 分片 sleep，每 20s 检查 closing，避免 systemd SIGTERM 超时
                    while not self._closing:
                        now_t = get_market_now("cn")
                        if now_t >= afternoon_start:
                            break
                        remaining = (afternoon_start - now_t).total_seconds()
                        time.sleep(min(20, max(1, remaining)))
                    continue

                elif now < afternoon_end:
                    # 下午盘：运行监控到 15:00
                    asyncio.run(self._run_monitoring_session(codes, T_1500))
                    # 会话结束（收盘），等待下一个交易日
                    now = get_market_now("cn")
                    today = now.date()
                    next_day = get_next_trading_day("cn", today)
                    next_start = datetime.combine(next_day, T_0915, tzinfo=now.tzinfo)
                    wait_seconds = (next_start - now).total_seconds()
                    if wait_seconds > 0:
                        logger.info(
                            "[RealtimeMonitor] 今日已收盘，等待 %dh %.0fm 后下个交易日 %s",
                            wait_seconds // 3600,
                            (wait_seconds % 3600) / 60,
                            next_day.isoformat(),
                        )
                        # 分片 sleep，每 20s 检查 closing，避免 systemd SIGTERM 超时
                        while not self._closing:
                            now_t = get_market_now("cn")
                            next_day_t = get_next_trading_day("cn", now_t.date())
                            next_start_t = datetime.combine(next_day_t, T_0915, tzinfo=now_t.tzinfo)
                            remaining = (next_start_t - now_t).total_seconds()
                            if remaining <= 0:
                                break
                            time.sleep(min(20, max(1, remaining)))
                    continue

                else:
                    # 已收盘，等待下一个交易日（兜底）
                    next_day = get_next_trading_day("cn", today)
                    next_start = datetime.combine(next_day, T_0915, tzinfo=now.tzinfo)
                    wait_seconds = (next_start - now).total_seconds()
                    logger.info(
                        "[RealtimeMonitor] 今日已收盘（兜底），等待 %dh %.0fm 后下一个交易日 %s",
                        wait_seconds // 3600,
                        (wait_seconds % 3600) / 60,
                        next_day.isoformat(),
                    )
                    # 分片 sleep，每 20s 检查 closing，避免 systemd SIGTERM 超时
                    while not self._closing:
                        now_t = get_market_now("cn")
                        next_day_t = get_next_trading_day("cn", now_t.date())
                        next_start_t = datetime.combine(next_day_t, T_0915, tzinfo=now_t.tzinfo)
                        remaining = (next_start_t - now_t).total_seconds()
                        if remaining <= 0:
                            break
                        time.sleep(min(20, max(1, remaining)))
                    continue

            except KeyboardInterrupt:
                logger.info("[RealtimeMonitor] 守护进程收到中断信号，退出")
                break
            except Exception as exc:
                logger.exception("[RealtimeMonitor] 守护进程异常: %s", exc)
                # 异常后等待 20 秒再重试
                time.sleep(20)

    async def _run_monitoring_session(
        self,
        codes: list[str],
        end_time: dt_time,
    ) -> None:
        """
        运行一次盘中监控会话。

        在交易时段内启动 WebSocket 连接，运行 Phase 1/2/3 监控逻辑。
        到达 end_time 后自动结束会话。
        """
        if self._closing:
            return

        logger.info("[RealtimeMonitor] 开始盘中监控会话，计划结束时间 %s", end_time)

        # 重置状态
        self._states.clear()
        self._quotes.clear()
        self._last_briefing_time = time.time()

        # 加载日线数据
        self._stock_codes = codes
        await self._load_historical_data()

        # 初始化通知服务
        await self._init_notifier()

        # 计算会话结束的 timeout
        from src.core.trading_calendar import get_market_now

        now_dt = get_market_now("cn")  # timezone-aware
        session_end_dt = datetime.combine(now_dt.date(), end_time, tzinfo=now_dt.tzinfo)

        if now_dt >= session_end_dt:
            logger.info("[RealtimeMonitor] 当前时间已过结束时间 %s，跳过本会话", end_time)
            return

        remaining = (session_end_dt - now_dt).total_seconds()
        logger.info("[RealtimeMonitor] 本次会话剩余 %.0f 秒", remaining)

        # 启动 HTTP 轮询监控（替代 WebSocket，稳定性更高）
        from data_provider import DataFetcherManager

        fetcher = DataFetcherManager()
        poll_interval = 15  # 每 15 秒轮询一次

        while not self._closing:
            try:
                # 检查是否超时
                dt_now = get_market_now("cn")
                if dt_now >= session_end_dt:
                    logger.info("[RealtimeMonitor] 到达收盘时间 %s，结束会话", end_time)
                    break

                # HTTP 轮询获取所有股票实时行情
                for code in codes:
                    try:
                        quote = await asyncio.to_thread(
                            fetcher.get_realtime_quote,
                            code,
                        )
                        if quote is None:
                            continue

                        # 确保状态存在
                        if code not in self._states:
                            self._states[code] = StockIntradayState(code=code)

                        state = self._states[code]
                        if not state.name and getattr(quote, "name", None):
                            state.name = quote.name

                        price = getattr(quote, "price", 0) or 0
                        change_pct = getattr(quote, "change_pct", 0) or 0
                        volume_ratio = getattr(quote, "volume_ratio", 0) or 0
                        turnover_rate = getattr(quote, "turnover_rate", 0) or 0

                        # 更新行情快照
                        self._quotes[code] = {
                            "price": price,
                            "change_pct": change_pct,
                            "volume_ratio": volume_ratio,
                            "turnover_rate": turnover_rate,
                            "name": state.name or "",
                        }

                        state.last_brief_price = price
                        state.last_brief_change_pct = change_pct
                        state.last_brief_volume_ratio = volume_ratio
                        state.last_brief_turnover_rate = turnover_rate

                        now_ts = time.time()

                        # === Phase 2: ATR 止损检查 ===
                        if state.atr14 > 0:
                            await self._check_atr_stop(code, state, price, change_pct, now_ts)

                        # === Phase 3: 量价异动 + 均线突破 ===
                        await self._check_volume_alert(
                            code, state, price, change_pct, volume_ratio, turnover_rate, now_ts
                        )
                        await self._check_ma_cross(code, state, price, change_pct, now_ts)

                        # 更新 last_tick_price 供下次穿越检测
                        state.last_tick_price = price

                    except Exception as exc:
                        logger.debug("[RealtimeMonitor] %s 轮询失败: %s", code, exc)
                        continue

                # === Phase 1: 简报 ===
                await self._maybe_send_briefing(time.time())

                # 等待下一次轮询
                await asyncio.sleep(poll_interval)

            except asyncio.CancelledError:
                logger.info("[RealtimeMonitor] 监控会话被取消")
                break
            except Exception as exc:
                logger.exception("[RealtimeMonitor] 监控会话异常: %s", exc)
                await asyncio.sleep(poll_interval)


# ============================================================
# 便捷入口函数
# ============================================================


def run_realtime_monitor(
    stock_codes: list[str] | None = None,
    config: Config | None = None,
    *,
    daemon_mode: bool = False,
    briefing_interval: int | None = None,
    atr_multipliers: list[float] | None = None,
    volume_ratio_threshold: float | None = None,
    price_change_threshold: float | None = None,
    atr_cooldown: float | None = None,
    cross_cooldown: float | None = None,
    cross_global_cooldown: float | None = None,
    volume_cooldown: float | None = None,
) -> None:
    """
    便捷函数：创建并启动实时监控服务。

    Args:
        stock_codes: 股票代码列表，None 时使用 config.stock_list
        config: 配置实例，None 时使用全局配置
        daemon_mode: 是否启用守护进程模式（自动跟随交易时段）
        briefing_interval: 简报间隔（秒），默认 900（15分钟）
        atr_multipliers: ATR 止损倍数列表
        volume_ratio_threshold: 量比异常阈值
        price_change_threshold: 涨跌幅异常阈值（%）
        atr_cooldown: ATR 止损告警冷却时间（秒）
        cross_cooldown: 均线突破同方向告警冷却时间（秒）
        cross_global_cooldown: 均线突破方向变化全局冷却时间（秒）
        volume_cooldown: 量价异动告警冷却时间（秒）
    """
    _config = config or get_config()

    # 从配置解析参数
    if briefing_interval is None:
        briefing_interval = _config.realtime_monitor_briefing_interval

    if atr_multipliers is None:
        atr_mult_str = _config.realtime_monitor_atr_multipliers
        try:
            atr_multipliers = [float(x.strip()) for x in atr_mult_str.split(",")]
        except (ValueError, AttributeError):
            atr_multipliers = [2.0, 2.5, 3.0]

    if volume_ratio_threshold is None:
        volume_ratio_threshold = _config.realtime_monitor_volume_ratio_threshold

    if price_change_threshold is None:
        price_change_threshold = _config.realtime_monitor_price_change_threshold

    codes = stock_codes or _config.stock_list
    if not codes:
        logger.warning("[RealtimeMonitor] 股票列表为空，无法启动监控")
        return

    service = RealtimeMonitorService(
        config=_config,
        briefing_interval=briefing_interval,
        atr_multipliers=atr_multipliers,
        volume_ratio_threshold=volume_ratio_threshold,
        price_change_threshold=price_change_threshold,
        atr_cooldown=atr_cooldown or 300.0,
        cross_cooldown=cross_cooldown or 600.0,
        cross_global_cooldown=cross_global_cooldown or 300.0,
        volume_cooldown=volume_cooldown or 300.0,
    )

    if daemon_mode:
        service.run_daemon(codes)
    else:
        service.run(codes)
