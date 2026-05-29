"""Dragon Tiger Board (龙虎榜) and fund flow (资金流向) analysis.

Fetches institutional trading activity data from akshare and generates
structured analysis for the market review report.

Usage:
    python -m src.core.dragon_tiger_flow
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class FundFlowResult:
    code: str
    name: str = ""
    main_net_inflow: float = 0.0       # 主力净流入 (万元)
    super_large_net: float = 0.0        # 超大单净流入
    large_net: float = 0.0              # 大单净流入
    medium_net: float = 0.0             # 中单净流入
    small_net: float = 0.0              # 小单净流入

    @property
    def total_net(self) -> float:
        return self.super_large_net + self.large_net + self.medium_net + self.small_net

    @property
    def signal(self) -> str:
        if self.main_net_inflow > 500:
            return "强力买入"
        if self.main_net_inflow > 100:
            return "买入"
        if self.main_net_inflow < -500:
            return "强力卖出"
        if self.main_net_inflow < -100:
            return "卖出"
        return "中性"


@dataclass
class DragonTigerResult:
    date: str
    code: str
    name: str
    reason: str = ""         # 上榜原因
    buy_amount: float = 0.0   # 买入金额(万元)
    sell_amount: float = 0.0  # 卖出金额(万元)
    net_amount: float = 0.0   # 净买入

    @property
    def signal(self) -> str:
        if self.net_amount > 5000:
            return "强势"
        if self.net_amount > 1000:
            return "偏多"
        if self.net_amount < -5000:
            return "弱势"
        if self.net_amount < -1000:
            return "偏空"
        return "中性"


def fetch_fund_flow(stock_code: str) -> FundFlowResult | None:
    """Fetch individual stock fund flow from akshare.

    Returns None on failure (fail-open).
    """
    try:
        import akshare as ak
        df = ak.stock_individual_fund_flow(stock=stock_code, market="sh" if stock_code.startswith("6") else "sz")
        if df is None or df.empty:
            return None
        latest = df.iloc[-1]
        return FundFlowResult(
            code=stock_code,
            name=str(latest.get("名称", "")),
            main_net_inflow=float(latest.get("主力净流入", 0) or 0),
            super_large_net=float(latest.get("超大单净流入", 0) or 0),
            large_net=float(latest.get("大单净流入", 0) or 0),
            medium_net=float(latest.get("中单净流入", 0) or 0),
            small_net=float(latest.get("小单净流入", 0) or 0),
        )
    except Exception as exc:
        logger.debug("[FundFlow] %s failed: %s", stock_code, exc)
        return None


def fetch_dragon_tiger_top(days: int = 5) -> list[DragonTigerResult]:
    """Fetch recent dragon-tiger board activity.

    Returns top entries sorted by net buy amount.
    """
    try:
        import akshare as ak
        df = ak.stock_lhb_detail_em(date="")  # latest
        if df is None or df.empty:
            return []
        results = []
        for _, row in df.head(50).iterrows():
            results.append(DragonTigerResult(
                date=str(row.get("日期", "")),
                code=str(row.get("代码", "")),
                name=str(row.get("名称", "")),
                reason=str(row.get("上榜原因", "")),
                buy_amount=float(row.get("买入金额", 0) or 0),
                sell_amount=float(row.get("卖出金额", 0) or 0),
                net_amount=float(row.get("净买额", 0) or 0),
            ))
        results.sort(key=lambda r: r.net_amount, reverse=True)
        return results[:20]
    except Exception as exc:
        logger.debug("[DragonTiger] fetch failed: %s", exc)
        return []


def build_fund_flow_prompt(flows: list[FundFlowResult]) -> str:
    """Build fund flow summary for LLM prompt injection."""
    if not flows:
        return ""
    lines = [
        "### 资金流向 (Fund Flow)",
        "| 代码 | 名称 | 主力净流入(万) | 信号 |",
        "|------|------|---------------|------|",
    ]
    for f in sorted(flows, key=lambda x: x.main_net_inflow, reverse=True):
        lines.append(f"| {f.code} | {f.name} | {f.main_net_inflow:+.0f} | {f.signal} |")
    lines.append("> 主力净流入=超大单+大单净流入。正值=资金净流入，负值=资金净流出。")
    return "\n".join(lines)


def build_dragon_tiger_prompt(entries: list[DragonTigerResult]) -> str:
    """Build dragon-tiger board summary for LLM prompt injection."""
    if not entries:
        return ""
    lines = [
        "### 龙虎榜 (Dragon-Tiger Board)",
        "| 日期 | 代码 | 名称 | 净买入(万) | 信号 |",
        "|------|------|------|-----------|------|",
    ]
    for e in entries[:10]:
        lines.append(f"| {e.date} | {e.code} | {e.name} | {e.net_amount:+.0f} | {e.signal} |")
    lines.append("> 龙虎榜追踪每日机构/游资大额交易。净买入>5000万=强势信号。")
    return "\n".join(lines)
