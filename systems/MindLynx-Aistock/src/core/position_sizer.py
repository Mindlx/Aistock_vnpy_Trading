"""ATR-based position sizing calculator.

Replaces qualitative "建议3成" with quantitative position sizing:
    position_size_pct = risk_budget / (ATR_multiplier × ATR / price)

This ensures consistent risk exposure per trade regardless of stock volatility.
"""

from dataclasses import dataclass


# Default risk budget: 1.5% of portfolio per trade
# Conservative: 1.0% | Moderate: 1.5% | Aggressive: 2.5%
DEFAULT_RISK_BUDGET_PCT: float = 1.5


@dataclass
class PositionSize:
    """Calculated position sizing recommendation."""
    risk_budget_pct: float           # % of portfolio at risk per trade
    atr: float                       # absolute ATR value
    atr_pct: float                   # ATR as % of price
    atr_multiplier: float            # stop-loss multiplier
    stop_loss_price: float           # calculated stop loss
    position_pct: float              # recommended position as % of portfolio
    position_label: str              # human-readable label
    max_shares: int                  # max shares (assuming 100 shares/lot for A-shares)


def compute_position_size(
    price: float,
    atr: float,
    *,
    risk_budget_pct: float = DEFAULT_RISK_BUDGET_PCT,
    atr_multiplier: float = 2.0,
    max_position_pct: float = 25.0,
    min_position_pct: float = 2.0,
) -> PositionSize:
    """Compute recommended position size based on ATR and risk budget.

    Formula:
        stop_loss = price - atr_multiplier * atr  (for long positions)
        risk_per_share = atr_multiplier * atr
        position_pct = risk_budget_pct / (risk_per_share / price * 100)

    Cap: position_pct ∈ [min_position_pct, max_position_pct]

    Args:
        price: current stock price
        atr: 14-day Average True Range (absolute value)
        risk_budget_pct: max % of portfolio to risk per trade (default 1.5%)
        atr_multiplier: stop loss ATR multiplier (low=2.0, mid=2.5, high=3.0)
        max_position_pct: cap on position size (default 25%)
        min_position_pct: floor on position size (default 2%)

    Returns:
        PositionSize with recommendation.
    """
    if price <= 0 or atr <= 0:
        return PositionSize(
            risk_budget_pct=risk_budget_pct,
            atr=atr, atr_pct=0.0, atr_multiplier=atr_multiplier,
            stop_loss_price=price, position_pct=0.0,
            position_label="数据不足", max_shares=0,
        )

    atr_pct = atr / price

    stop_loss_price = price - atr_multiplier * atr
    risk_per_share = atr_multiplier * atr
    risk_pct = risk_per_share / price * 100

    if risk_pct <= 0:
        position_pct = max_position_pct
    else:
        position_pct = risk_budget_pct / risk_pct * 100

    position_pct = max(min_position_pct, min(position_pct, max_position_pct))

    if position_pct >= 20:
        label = "重仓"
    elif position_pct >= 10:
        label = "中仓"
    elif position_pct >= 5:
        label = "轻仓"
    else:
        label = "观察仓"

    max_shares = max(100, int((position_pct / 100) / (price / 10000) / 100) * 100)

    return PositionSize(
        risk_budget_pct=risk_budget_pct,
        atr=atr, atr_pct=round(atr_pct, 4),
        atr_multiplier=atr_multiplier,
        stop_loss_price=round(stop_loss_price, 2),
        position_pct=round(position_pct, 1),
        position_label=label,
        max_shares=max_shares,
    )


def build_position_prompt(ps: PositionSize) -> str:
    """Build a human-readable position sizing summary for LLM prompt injection."""
    if ps.position_pct <= 0:
        return "### ATR仓位建议\n- 数据不足，无法计算ATR仓位。保持观望。\n"

    return (
        f"### ATR仓位建议 (ATR-based Position Sizing)\n"
        f"- 风险预算: {ps.risk_budget_pct}% / 笔\n"
        f"- ATR: {ps.atr:.3f} ({ps.atr_pct*100:.1f}% of price)\n"
        f"- 止损ATR倍数: {ps.atr_multiplier}x\n"
        f"- 止损价: {ps.stop_loss_price:.2f}\n"
        f"- **建议仓位**: {ps.position_pct:.1f}% ({ps.position_label})\n"
        f"- 建议股数: {ps.max_shares}股 (参考)\n"
        f"\n> 计算逻辑: 仓位% = 风险预算% / (ATR止损幅度%)。"
        f"高波动股票自动降仓，低波动股票可适度加仓。\n"
    )
