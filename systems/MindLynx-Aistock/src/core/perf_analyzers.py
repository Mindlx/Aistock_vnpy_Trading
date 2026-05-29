"""Performance analyzers for backtest evaluation.

Composable analyzers following the backtrader pattern.
Computes industry-standard risk/reward metrics from equity curves.

Analyzers:
    SharpeRatio   — risk-adjusted return (annualized)
    SortinoRatio  — downside-risk-adjusted return
    CalmarRatio   — return / max drawdown
    MaxDrawdown   — maximum peak-to-trough decline
    WinRate       — percentage of profitable trades
    ProfitFactor  — gross profit / gross loss
    UlcerIndex    — depth and duration of drawdowns
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PerformanceReport:
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    ulcer_index: float = 0.0
    annual_return_pct: float = 0.0
    annual_volatility_pct: float = 0.0
    total_return_pct: float = 0.0
    positive_months: int = 0
    total_months: int = 0

    def to_dict(self) -> dict:
        return {
            "sharpe": round(self.sharpe_ratio, 3),
            "sortino": round(self.sortino_ratio, 3),
            "calmar": round(self.calmar_ratio, 3),
            "max_dd_pct": round(self.max_drawdown_pct, 1),
            "win_rate_pct": round(self.win_rate_pct, 1),
            "profit_factor": round(self.profit_factor, 2),
            "ulcer_index": round(self.ulcer_index, 3),
            "annual_return_pct": round(self.annual_return_pct, 1),
            "annual_vol_pct": round(self.annual_volatility_pct, 1),
            "total_return_pct": round(self.total_return_pct, 1),
        }

    def grade(self) -> str:
        """A/B/C/D/F grade based on Sharpe + drawdown."""
        score = 0
        if self.sharpe_ratio >= 2.0:
            score += 2
        elif self.sharpe_ratio >= 1.0:
            score += 1
        if self.calmar_ratio >= 1.0:
            score += 2
        elif self.calmar_ratio >= 0.5:
            score += 1
        if self.max_drawdown_pct <= 15:
            score += 2
        elif self.max_drawdown_pct <= 30:
            score += 1
        if self.win_rate_pct >= 55:
            score += 1
        if self.profit_factor >= 1.5:
            score += 1
        if score >= 7:
            return "A"
        if score >= 5:
            return "B"
        if score >= 3:
            return "C"
        if score >= 2:
            return "D"
        return "F"


# ---------------------------------------------------------------------------
# Metric calculators
# ---------------------------------------------------------------------------

def compute_max_drawdown(equity: list[float]) -> tuple[float, float, float]:
    """Compute maximum drawdown and related metrics.

    Returns:
        (max_drawdown_pct, peak_value, trough_value)
    """
    if not equity or len(equity) < 2:
        return 0.0, 0.0, 0.0

    peak = equity[0]
    max_dd = 0.0
    peak_at_dd = peak
    trough_at_dd = peak

    for val in equity[1:]:
        if val > peak:
            peak = val
        dd = (peak - val) / peak
        if dd > max_dd:
            max_dd = dd
            peak_at_dd = peak
            trough_at_dd = val

    return max_dd * 100, peak_at_dd, trough_at_dd


def compute_ulcer_index(equity: list[float]) -> float:
    """Compute Ulcer Index — penalizes both depth and duration of drawdowns."""
    if not equity or len(equity) < 2:
        return 0.0

    peak = equity[0]
    squared_dd_sum = 0.0

    for val in equity[1:]:
        if val > peak:
            peak = val
        dd = (peak - val) / peak
        squared_dd_sum += dd * dd

    return math.sqrt(squared_dd_sum / len(equity))


def compute_sharpe_ratio(
    returns: list[float],
    risk_free_rate: float = 0.02,
    trading_periods: int = 252,
) -> float:
    """Compute annualized Sharpe ratio.

    Args:
        returns: daily return series (decimal, e.g., 0.01 = 1%)
        risk_free_rate: annual risk-free rate (default 2%)
        trading_periods: days per year (252 for stocks, 365 for crypto)
    """
    if not returns or len(returns) < 2:
        return 0.0
    arr = np.array(returns, dtype=float)
    mean_ret = float(np.mean(arr))
    std_ret = float(np.std(arr, ddof=1))
    if std_ret < 1e-8:
        return 0.0
    daily_rf = risk_free_rate / trading_periods
    return float((mean_ret - daily_rf) / std_ret * math.sqrt(trading_periods))


def compute_sortino_ratio(
    returns: list[float],
    risk_free_rate: float = 0.02,
    trading_periods: int = 252,
) -> float:
    """Compute annualized Sortino ratio (uses downside deviation only)."""
    if not returns or len(returns) < 2:
        return 0.0
    arr = np.array(returns, dtype=float)
    mean_ret = float(np.mean(arr))
    daily_rf = risk_free_rate / trading_periods
    downside = arr[arr < daily_rf]
    if len(downside) < 2:
        return 0.0
    downside_std = float(np.std(downside, ddof=1))
    if downside_std < 1e-8:
        return 0.0
    return float((mean_ret - daily_rf) / downside_std * math.sqrt(trading_periods))


def compute_calmar_ratio(
    returns: list[float],
    max_drawdown_pct: float,
    trading_periods: int = 252,
) -> float:
    """Compute Calmar ratio: annualized return / max drawdown."""
    if not returns or max_drawdown_pct < 0.01:
        return 0.0
    annual_return = float(np.mean(returns)) * trading_periods
    return float(annual_return / (max_drawdown_pct / 100))


def compute_win_rate(trade_returns: list[float]) -> float:
    """Compute win rate from a list of per-trade returns."""
    if not trade_returns:
        return 0.0
    wins = sum(1 for r in trade_returns if r > 0)
    return wins / len(trade_returns) * 100


def compute_profit_factor(trade_returns: list[float]) -> float:
    """Compute profit factor: gross profit / gross loss."""
    if not trade_returns:
        return 0.0
    gains = sum(r for r in trade_returns if r > 0)
    losses = abs(sum(r for r in trade_returns if r < 0))
    if losses < 1e-8:
        return 99.0 if gains > 0 else 0.0
    return gains / losses


def monthly_win_rate(equity: list[float], days_per_month: int = 21) -> tuple[int, int]:
    """Count positive months from equity curve."""
    if not equity or len(equity) < days_per_month:
        return 0, 0
    positive = 0
    total = 0
    for i in range(days_per_month - 1, len(equity), days_per_month):
        if i >= len(equity):
            break
        total += 1
        if equity[i] > equity[i - days_per_month + 1]:
            positive += 1
    return positive, total


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyze_performance(
    equity_curve: list[float],
    trade_returns: list[float] | None = None,
    risk_free_rate: float = 0.02,
    trading_periods: int = 252,
) -> PerformanceReport:
    """Produce a complete performance report from an equity curve.

    Args:
        equity_curve: time-series of portfolio/cash values
        trade_returns: optional list of per-trade returns for win rate
        risk_free_rate: annual risk-free rate
        trading_periods: trading days per year

    Returns:
        PerformanceReport with all computed metrics.
    """
    if not equity_curve or len(equity_curve) < 2:
        return PerformanceReport()

    # Daily returns
    daily_returns = []
    for i in range(1, len(equity_curve)):
        if equity_curve[i - 1] > 0:
            daily_returns.append((equity_curve[i] / equity_curve[i - 1]) - 1)

    max_dd, _, _ = compute_max_drawdown(equity_curve)
    total_return = (equity_curve[-1] / equity_curve[0] - 1) * 100 if equity_curve[0] > 0 else 0

    trades = trade_returns or []
    positive_months, total_months = monthly_win_rate(equity_curve)

    return PerformanceReport(
        sharpe_ratio=compute_sharpe_ratio(daily_returns, risk_free_rate, trading_periods),
        sortino_ratio=compute_sortino_ratio(daily_returns, risk_free_rate, trading_periods),
        calmar_ratio=compute_calmar_ratio(daily_returns, max_dd, trading_periods),
        max_drawdown_pct=max_dd,
        win_rate_pct=compute_win_rate(trades) if trades else 0.0,
        profit_factor=compute_profit_factor(trades) if trades else 0.0,
        ulcer_index=compute_ulcer_index(equity_curve),
        annual_return_pct=float(np.mean(daily_returns)) * trading_periods * 100 if daily_returns else 0.0,
        annual_volatility_pct=float(np.std(daily_returns, ddof=1)) * math.sqrt(trading_periods) * 100 if daily_returns else 0.0,
        total_return_pct=total_return,
        positive_months=positive_months,
        total_months=total_months,
    )


def format_performance_report(report: PerformanceReport, label: str = "") -> str:
    """Format a performance report as markdown table."""
    lines = [f"### 绩效报告 {label}".strip()]
    lines.append(f"| 指标 | 数值 | 评级 |")
    lines.append(f"|------|------|------|")

    def _grade_sharpe(v): return "A" if v >= 2 else ("B" if v >= 1 else ("C" if v >= 0.5 else "D"))
    def _grade_calmar(v): return "A" if v >= 2 else ("B" if v >= 1 else ("C" if v >= 0.3 else "D"))
    def _grade_dd(v): return "A" if v <= 10 else ("B" if v <= 20 else ("C" if v <= 30 else "D"))
    def _grade_wr(v): return "A" if v >= 60 else ("B" if v >= 50 else ("C" if v >= 40 else "D"))

    lines.append(f"| Sharpe 比率 | {report.sharpe_ratio:.3f} | {_grade_sharpe(report.sharpe_ratio)} |")
    lines.append(f"| Sortino 比率 | {report.sortino_ratio:.3f} | {_grade_sharpe(report.sortino_ratio)} |")
    lines.append(f"| Calmar 比率 | {report.calmar_ratio:.3f} | {_grade_calmar(report.calmar_ratio)} |")
    lines.append(f"| 最大回撤 | {report.max_drawdown_pct:.1f}% | {_grade_dd(report.max_drawdown_pct)} |")
    lines.append(f"| 胜率 | {report.win_rate_pct:.1f}% | {_grade_wr(report.win_rate_pct)} |")
    lines.append(f"| 盈亏比 | {report.profit_factor:.2f} | — |")
    lines.append(f"| 年化收益 | {report.annual_return_pct:.1f}% | — |")
    lines.append(f"| 年化波动 | {report.annual_volatility_pct:.1f}% | — |")
    lines.append(f"| 总收益 | {report.total_return_pct:.1f}% | — |")
    lines.append(f"| 综合评级 | **{report.grade()}** | |")
    return "\n".join(lines)
