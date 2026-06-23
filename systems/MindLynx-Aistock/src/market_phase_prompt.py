"""
Market phase prompt generation for LLM analysis context.

Generates Chinese-language market phase descriptions with trading strategy
implications based on the current session phase. Injected into the agent
analysis pipeline so the LLM knows what market context to consider.
"""

from __future__ import annotations

from src.core.trading_calendar import (
    MarketPhase,
    get_market_for_stock,
    infer_market_phase,
)

# Chinese market labels
_MARKET_LABELS: dict[str, str] = {
    "cn": "A股",
    "hk": "港股",
    "us": "美股",
    "jp": "日股",
    "kr": "韩股",
}

# Phase -> (description, strategy_implications)
# Each phase gets a 3-5 sentence Chinese prompt with actionable guidance.
_PHASE_PROMPTS: dict[MarketPhase, tuple[str, str]] = {
    MarketPhase.PREMARKET: (
        "市场处于盘前阶段，正式交易尚未开始。",
        "盘前是信息消化与预期博弈的关键时期。关注隔夜外盘表现、夜间重要公告及政策动向对开盘价的影响。"
        "此时不宜急于操作，应重点观察集合竞价阶段的挂单变化与量价信号。"
        "若盘前消息面偏多，可提前制定开盘后的跟踪计划；若利空密集，则需警惕低开风险。",
    ),
    MarketPhase.INTRADAY: (
        "市场处于盘中交易阶段，量价关系最为关键。",
        "此时应重点关注分时走势中的量能配合与趋势延续性。早盘9:30-10:30决定全天方向，"
        "需观察开盘后前30分钟的量价背离与资金流向。"
        "尾盘14:30-15:00关注资金博弈结果与次日预期布局。"
        "盘中异动需结合板块联动性判断是主升浪还是诱多/诱空。",
    ),
    MarketPhase.LUNCH_BREAK: (
        "市场处于午间休市阶段，上午交易已结束。",
        "午间是复盘上午走势、修正下午策略的黄金窗口。回顾上午的成交量能否支撑当前趋势，"
        "板块轮动是否健康，龙头股是否出现分歧信号。"
        "结合午间新闻与外围市场实时动态，调整下午的操作计划。"
        "若上午放量上涨但缺乏板块支撑，下午需警惕冲高回落。",
    ),
    MarketPhase.CLOSING_AUCTION: (
        "市场处于收盘集合竞价阶段，即将收盘。",
        "收盘集合竞价是全天资金意图的最终体现。重点关注最后几分钟的量价突变——"
        "放量抢筹通常预示次日延续，放量杀跌则需防范次日低开。"
        "此时不宜追高，应以观察和记录为主。"
        "收盘价的定位决定了技术指标的最终形态，是制定次日策略的核心参考。",
    ),
    MarketPhase.POSTMARKET: (
        "市场处于盘后阶段，今日交易已全部结束。",
        "盘后是系统性复盘与策略校准的最佳时机。全面回顾今日的量价关系、板块轮动和资金流向，"
        "验证盘中做出的判断是否准确。"
        "结合盘后公告与晚间外盘走势，预判明日开盘方向。"
        "此时适合进行深度分析而非即时操作，为次日交易做好充分准备。",
    ),
    MarketPhase.NON_TRADING: (
        "今日为非交易日，市场处于休市状态。",
        "非交易日应专注于周末研究、策略回测和知识库更新。"
        "利用休市时间梳理持仓逻辑，检查是否有需要调整的头寸。"
        "关注周末政策面与行业面变化，为下一个交易日制定应对预案。"
        "避免过度交易，保持策略纪律性。",
    ),
    MarketPhase.UNKNOWN: (
        "市场阶段无法准确判断。",
        "当前无法确定市场所处阶段，建议以保守策略为主。"
        "在无明确阶段指引的情况下，重点关注个股自身量价信号和技术面变化。"
        "降低操作频率，等待市场信号更加清晰后再做决策。",
    ),
}


def build_market_phase_prompt(market: str = "cn") -> str:
    """Build a Chinese-language market phase prompt for LLM analysis context.

    Infers the current market phase based on the given market region and
    returns a descriptive prompt with trading strategy implications.

    Args:
        market: Market region key ('cn', 'hk', 'us', 'jp', 'kr').
                Defaults to 'cn' (A-shares).

    Returns:
        A Chinese-language prompt string describing the current market phase
        and its trading implications. Empty string if phase cannot be determined.
    """
    phase = infer_market_phase(market)
    if phase == MarketPhase.UNKNOWN:
        return ""

    label = _MARKET_LABELS.get(market, market or "市场")
    description, implications = _PHASE_PROMPTS.get(phase, _PHASE_PROMPTS[MarketPhase.UNKNOWN])

    return f"{label}{description} {implications}"


def build_market_phase_prompt_for_stock(code: str) -> str:
    """Build a market phase prompt based on stock code market detection.

    Infers the market region from the stock code and returns the appropriate
    phase prompt.

    Args:
        code: Stock code (e.g., '600519', 'AAPL', 'hk00700').

    Returns:
        A Chinese-language prompt string, or empty string if market is unknown.
    """
    market = get_market_for_stock(code)
    if not market:
        return ""
    return build_market_phase_prompt(market)
