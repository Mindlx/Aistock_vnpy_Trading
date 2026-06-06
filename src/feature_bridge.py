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


def _desire_level(val: float) -> str:
    """参与意愿等级 (0-100)"""
    if val >= 80: return "强烈做多"
    if val >= 65: return "做多强劲"
    if val >= 55: return "偏多活跃"
    if val >= 45: return "多空均衡"
    if val >= 35: return "偏空观望"
    if val >= 20: return "做空明显"
    return "极度悲观"

def _focus_level(val: float) -> str:
    """关注指数等级 (0-100)"""
    if val >= 80: return "极度拥挤"
    if val >= 65: return "非常活跃"
    if val >= 55: return "较为活跃"
    if val >= 45: return "热度正常"
    if val >= 35: return "偏冷清"
    if val >= 20: return "冷门"
    return "极度冷清"

def _combined_grade(w: float, f: float, is_st: bool = False) -> tuple[str, str]:
    """综合评级: (图标, 结论描述)"""
    if is_st:
        return "❌", "ST股风险，建议规避"
    if w >= 65 or (w >= 55 and f < 65):
        return "✅", f"意愿{_desire_level(w)}，关注{_focus_level(f)}，同步积极"
    if (w >= 55 and f >= 65) or (45 <= w < 55 and f < 65):
        return "📈", f"意愿{_desire_level(w)}，关注{_focus_level(f)}，谨慎偏多"
    if (45 <= w < 55 and f >= 65) or (35 <= w < 45 and f < 65):
        return "📉", f"意愿{_desire_level(w)}，关注{_focus_level(f)}，注意风险"
    # w < 35 or (35 <= w < 45 and f >= 65)
    return "❌", f"意愿{_desire_level(w)}，关注{_focus_level(f)}，危险信号"


def run_xueqiu_sentiment(stock_codes: list[str]) -> str | None:
    """东方财富评级 — 综合参与意愿与关注指数，格式化推送。"""
    try:
        # 先加载真正的 akshare（防止 AT 子系统的本地 akshare.py 文件遮蔽）
        import akshare as _  # noqa: F401
        _add_path("systems/mind_TradingAgent/mind_tradingagent/dataflows")
        from xueqiu import fetch_desire_raw, fetch_focus_raw
        from src.mind_stock_config import get_stock_name

        stock_lines: list[str] = []
        desire_date = focus_date = ""

        for code in stock_codes:
            desire_val = focus_val = None
            d_date = f_date = ""

            # 获取参与意愿
            try:
                ddf = fetch_desire_raw(code)
                if ddf is not None and not ddf.empty:
                    row = ddf.iloc[-1]
                    desire_val = float(row.get("参与意愿", 0) or 0)
                    d_date = str(row.get("交易日期", ""))[5:10].replace("-", "/")
            except Exception:
                pass

            # 获取关注指数
            try:
                fdf = fetch_focus_raw(code)
                if fdf is not None and not fdf.empty:
                    row = fdf.iloc[-1]
                    focus_val = float(row.get("用户关注指数", 0) or 0)
                    f_date = str(row.get("交易日", ""))[5:10].replace("-", "/")
            except Exception:
                pass

            # 取标题日期（用第一只股票的值）
            if d_date and not desire_date:
                desire_date = d_date
            if f_date and not focus_date:
                focus_date = f_date

            if desire_val is None and focus_val is None:
                continue

            # 获取名称
            try:
                name = get_stock_name(code) or code
            except Exception:
                name = code

            w = desire_val or 50
            f = focus_val or 50
            is_st = name.startswith("*ST")
            icon, conclusion = _combined_grade(w, f, is_st)

            w_str = f"{w:.2f}" if desire_val is not None else "--"
            f_str = f"{f:.2f}" if focus_val is not None else "--"
            stock_lines.append(f"{icon}**{name}({code})** {w_str}/{f_str}｜{conclusion}")

        if not stock_lines:
            return None

        title_date = ""
        if desire_date and focus_date:
            title_date = f"\n⏰️参与意愿({desire_date})｜关注指数({focus_date})"
        elif desire_date:
            title_date = f"\n⏰️参与意愿({desire_date})"
        elif focus_date:
            title_date = f"\n⏰️关注指数({focus_date})"

        return f"💰东方财富评级{title_date}\n" + "\n".join(stock_lines)

    except Exception as e:
        logger.warning(f"[feature] 东方财富评级失败: {e}")
        return None
