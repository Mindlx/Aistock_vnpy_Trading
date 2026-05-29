"""Return decomposition via CAPM rolling regression.

Decomposes daily stock returns into market-driven and stock-specific (alpha)
components. Uses 60-day rolling window to capture time-varying beta.

Future: extend to multi-factor (market + sector) when sector index OHLCV
data becomes available.

Usage:
    from src.core.return_decomposition import decompose_return
    result = decompose_return(stock_returns, market_returns)
"""

from __future__ import annotations

from typing import Any

import numpy as np


def decompose_return(
    stock_returns: list[float],
    market_returns: list[float],
    window: int = 60,
) -> dict[str, Any]:
    if len(stock_returns) < window or len(market_returns) < window:
        return {"error": "insufficient data"}

    recent_stock = np.array(stock_returns[-window:])
    recent_market = np.array(market_returns[-window:])

    X = np.column_stack([np.ones(window), recent_market])
    coeffs, residuals, rank, sv = np.linalg.lstsq(X, recent_stock, rcond=None)
    alpha_daily, beta = coeffs[0], coeffs[1]

    today_stock = stock_returns[-1]
    today_market = market_returns[-1]
    market_driven = float(beta * today_market)
    alpha_component = float(today_stock - market_driven)

    r_squared = (
        float(1 - np.sum(residuals**2) / np.sum((recent_stock - np.mean(recent_stock)) ** 2))
        if len(residuals) > 0
        else 0
    )

    return {
        "beta": round(float(beta), 3),
        "r_squared": round(r_squared, 3),
        "market_driven_pct": round(market_driven / today_stock * 100, 1) if today_stock != 0 else 0,
        "alpha_pct": round(alpha_component / today_stock * 100, 1) if today_stock != 0 else 0,
        "alpha_daily_bp": round(float(alpha_daily) * 10000, 1),
    }


def build_decomposition_prompt(result: dict[str, Any]) -> str:
    if "error" in result:
        return ""

    beta = result["beta"]
    market_pct = result["market_driven_pct"]
    alpha_pct = result["alpha_pct"]
    r2 = result["r_squared"]

    if abs(alpha_pct) < 5:
        interpretation = "个股走势主要由大盘驱动，个股特有因素影响较小"
    elif alpha_pct > 0:
        interpretation = "个股α为正，表现优于大盘预期，可能有积极个股因素"
    else:
        interpretation = "个股α为负，表现弱于大盘预期，需关注个股特有风险"

    return (
        f"## 收益归因分解\n"
        f"- Beta: {beta:.2f} (R²={r2:.2f})\n"
        f"- 大盘驱动: {market_pct:.0f}% | 个股α: {alpha_pct:.0f}%\n"
        f"- 判断: {interpretation}\n"
    )
