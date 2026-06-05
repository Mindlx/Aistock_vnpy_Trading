"""
可选功能适配层 — 零侵入集成龙虎榜/雪球等额外数据到每日推送。

每个功能独立导入、独立容错，不修改任何子系统代码。
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _add_path(path: str):
    p = str(_PROJECT_ROOT / path)
    if p not in sys.path:
        sys.path.insert(0, p)


def run_dragon_tiger(stock_codes: list[str], top_n: int = 10) -> str | None:
    """龙虎榜资金流向 — 检查股票池是否有上榜个股"""
    try:
        _add_path("systems/MindLynx-Aistock/src/core")
        from dragon_tiger_flow import fetch_dragon_tiger_top, fetch_fund_flow, build_dragon_tiger_prompt

        dt_results = fetch_dragon_tiger_top(days=1)
        if not dt_results:
            return None

        # 交叉匹配：股票池中哪些上龙虎榜了
        pool_set = set(stock_codes)
        matched = [r for r in dt_results if r.code in pool_set]
        # 最多展示top_n条
        display = matched if matched else dt_results[:top_n]
        return build_dragon_tiger_prompt(display)

    except Exception as e:
        logger.warning(f"[feature] 龙虎榜获取失败: {e}")
        return None


def run_xueqiu_sentiment(stock_codes: list[str]) -> str | None:
    """东方财富个股评级/关注度 — 结构化的机构评级数据"""
    try:
        _add_path("systems/mind_TradingAgent/mind_tradingagent/dataflows")
        from xueqiu import fetch_xueqiu_hot_tweets, fetch_xueqiu_stock_comments

        lines: list[str] = []
        tz = timezone(timedelta(hours=8))
        now = datetime.now(tz).strftime("%H:%M")

        for code in stock_codes:
            tweet = fetch_xueqiu_hot_tweets(code, limit=3)
            focus = fetch_xueqiu_stock_comments(code, limit=3)
            # Extract first meaningful line from each, strip stock code field
            tweet_line = ""
            focus_line = ""
            for line in (tweet or "").split("\n"):
                if line.startswith("- "):
                    raw = line[2:60]
                    # Remove repetitive code field: "股票代码: XXXXX | ..." → "..."
                    import re
                    raw = re.sub(r'股票[代码码]\s*:\s*\w+\s*\|\s*', '', raw).strip()
                    tweet_line = raw
                    break
            for line in (focus or "").split("\n"):
                if line.startswith("- "):
                    raw = line[2:60]
                    raw = re.sub(r'股票[代码码]\s*:\s*\w+\s*\|\s*', '', raw).strip()
                    focus_line = raw
                    break
            if tweet_line or focus_line:
                parts = [p for p in [tweet_line, focus_line] if p]
                # Get stock name
                try:
                    from src.mind_stock_config import get_stock_name
                    name = get_stock_name(code) or code
                except Exception:
                    name = code
                lines.append(f"📊 **{name}({code})**: {' ｜ '.join(parts)}")

        if not lines:
            return None

        return f"📊 东方财富评级｜{now}\n" + "\n".join(lines)

    except Exception as e:
        logger.warning(f"[feature] 东方财富数据获取失败: {e}")
        return None
