"""Portfolio optimization engine.

Provides allocation and position sizing for multi-stock portfolios:
- Risk Parity (Equal Risk Contribution)
- Minimum Variance
- Kelly-based position sizing
- Correlation-aware weight adjustment

All methods use numpy only — no scipy dependency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

MAX_ITER: int = 100
TOLERANCE: float = 1e-6


@dataclass
class PortfolioAllocation:
    weights: list[float]
    method: str
    risk_contributions: list[float] | None = None
    expected_vol: float = 0.0
    diversification_ratio: float = 0.0

    def to_dict(self) -> dict:
        return {
            "weights": [round(w, 4) for w in self.weights],
            "method": self.method,
            "risk_contributions": [round(rc, 4) for rc in (self.risk_contributions or [])],
            "expected_vol_pct": round(self.expected_vol * 100, 2),
            "diversification_ratio": round(self.diversification_ratio, 2),
        }


@dataclass
class KellyPosition:
    code: str
    win_rate: float
    avg_win: float
    avg_loss: float
    kelly_fraction: float
    half_kelly_fraction: float
    recommended_pct: float
    position_label: str


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def compute_covariance_matrix(
    returns: list[list[float]],
    method: str = "sample",
) -> np.ndarray:
    """Compute covariance matrix from return series.

    Args:
        returns: list of per-stock return series, each a list of daily returns
        method: "sample" | "ewma" (exponential weighted)

    Returns:
        N×N covariance matrix.
    """
    data = np.array(returns, dtype=float).T  # T×N
    if data.shape[0] < 2 or data.shape[1] < 2:
        return np.eye(len(returns)) * 0.0001

    if method == "ewma":
        lambd = 0.94
        weights = np.array([(1 - lambd) * lambd ** i for i in range(data.shape[0] - 1, -1, -1)])
        weights /= weights.sum()
        mean_ret = np.average(data, axis=0, weights=weights)
        centered = data - mean_ret
        cov = centered.T @ np.diag(weights) @ centered
    else:
        cov = np.cov(data, rowvar=False)
    return cov


def compute_correlation_matrix(returns: list[list[float]]) -> np.ndarray:
    """Compute Pearson correlation matrix from return series."""
    if len(returns) < 2:
        return np.eye(len(returns))
    data = np.array(returns, dtype=float).T
    return np.corrcoef(data, rowvar=False)


def _compute_risk_contributions(weights: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """Compute risk contribution of each asset (MRC)."""
    port_vol = np.sqrt(weights @ cov @ weights)
    if port_vol < TOLERANCE:
        return np.zeros_like(weights)
    mrc = cov @ weights
    rc = weights * mrc / port_vol
    return rc


def _risk_parity_objective(weights: np.ndarray, cov: np.ndarray) -> float:
    """Sum of squared differences in risk contributions."""
    rc = _compute_risk_contributions(weights, cov)
    target = 1.0 / len(weights)
    return float(np.sum((rc / rc.sum() - target) ** 2))


def risk_parity_weights(
    cov: np.ndarray,
    max_iter: int = MAX_ITER,
    lr: float = 0.1,
) -> np.ndarray:
    """Compute risk parity (equal risk contribution) weights.

    Uses gradient descent to minimize the variance of risk contributions.
    Falls back to inverse-volatility if convergence fails.

    Args:
        cov: N×N covariance matrix
        max_iter: maximum iterations
        lr: learning rate

    Returns:
        N-element weight vector (sums to 1).
    """
    n = cov.shape[0]
    if n == 0:
        return np.array([])
    if n == 1:
        return np.ones(1)

    # Initialize with inverse-volatility weights
    vols = np.sqrt(np.diag(cov))
    vols = np.where(vols < TOLERANCE, TOLERANCE, vols)
    weights = (1.0 / vols) / np.sum(1.0 / vols)

    best_weights = weights.copy()
    best_obj = _risk_parity_objective(weights, cov)

    for _ in range(max_iter):
        rc = _compute_risk_contributions(weights, cov)
        target_rc = 1.0 / n
        grad = 2 * (rc / rc.sum() - target_rc) * (cov @ weights) / (weights @ cov @ weights) ** 0.5
        weights = weights - lr * grad
        weights = np.maximum(weights, 0.0)
        if weights.sum() < TOLERANCE:
            weights = np.ones(n) / n
        else:
            weights /= weights.sum()

        obj = _risk_parity_objective(weights, cov)
        if obj < best_obj:
            best_obj = obj
            best_weights = weights.copy()
        if obj < 1e-6:
            break

    return best_weights


def min_variance_weights(cov: np.ndarray) -> np.ndarray:
    """Compute minimum variance portfolio weights (long-only).

    Uses iterative quadratic optimization approximation since we don't
    depend on scipy. The closed-form for 2 assets is exact; for N>2,
    falls back to a simple constrained solver.

    Args:
        cov: N×N covariance matrix

    Returns:
        N-element weight vector (sums to 1).
    """
    n = cov.shape[0]
    if n <= 1:
        return np.ones(max(n, 1))
    if n == 2:
        v1, v2 = cov[0, 0], cov[1, 1]
        c12 = cov[0, 1]
        w1 = (v2 - c12) / (v1 + v2 - 2 * c12) if (v1 + v2 - 2 * c12) > 0 else 0.5
        w1 = max(0.0, min(1.0, w1))
        return np.array([w1, 1.0 - w1])

    # For N>2, use gradient descent
    weights = np.ones(n) / n
    lr = 0.01
    for _ in range(MAX_ITER):
        grad = cov @ weights
        weights = weights - lr * grad
        weights = np.maximum(weights, 0.0)
        s = weights.sum()
        if s > TOLERANCE:
            weights /= s
        else:
            weights = np.ones(n) / n
    return weights


# ---------------------------------------------------------------------------
# Kelly sizing
# ---------------------------------------------------------------------------

def kelly_position_size(
    win_rate: float,
    avg_win_pct: float,
    avg_loss_pct: float,
    *,
    max_fraction: float = 0.25,
) -> tuple[float, float]:
    """Compute Kelly-optimal position size.

    f* = (p * W - (1-p) * L) / (W * L)
    Half-Kelly: f*/2 (more conservative, recommended)

    Args:
        win_rate: probability of winning (0-1)
        avg_win_pct: average winning return as percentage
        avg_loss_pct: average losing return as percentage (positive value)
        max_fraction: cap on Kelly fraction (default 25%)

    Returns:
        (kelly_fraction, half_kelly_fraction) as decimals (e.g., 0.15 = 15%).
    """
    if avg_win_pct <= 0 or avg_loss_pct <= 0 or win_rate <= 0:
        return 0.0, 0.0

    p = win_rate
    w = avg_win_pct / 100.0
    l = avg_loss_pct / 100.0

    kelly = (p * w - (1 - p) * l) / (w * l) if (w * l) > 0 else 0.0
    kelly = max(0.0, min(kelly, max_fraction))
    half_kelly = kelly / 2.0
    return round(kelly, 4), round(half_kelly, 4)


# ---------------------------------------------------------------------------
# Correlation-aware adjustment
# ---------------------------------------------------------------------------

def correlation_penalty(
    weights: np.ndarray,
    corr_matrix: np.ndarray,
    penalty_strength: float = 0.5,
) -> np.ndarray:
    """Reduce weights of highly correlated stocks.

    For each stock, if its average correlation with other portfolio
    members exceeds a threshold, reduce its weight.

    Args:
        weights: initial weight vector
        corr_matrix: N×N correlation matrix
        penalty_strength: how aggressively to penalize (0=none, 1=full)

    Returns:
        Adjusted weight vector.
    """
    n = len(weights)
    if n <= 1:
        return weights

    avg_corr = (corr_matrix.sum(axis=1) - 1.0) / max(1, n - 1)
    penalty = 1.0 - penalty_strength * np.maximum(avg_corr - 0.3, 0.0)
    penalty = np.maximum(penalty, 0.3)

    adjusted = weights * penalty
    s = adjusted.sum()
    return adjusted / s if s > 0 else weights


# ---------------------------------------------------------------------------
# Pipeline integration helpers
# ---------------------------------------------------------------------------

def build_portfolio_allocation(
    codes: list[str],
    daily_returns: list[list[float]],
    method: str = "risk_parity",
    *,
    apply_correlation_penalty: bool = True,
) -> PortfolioAllocation:
    """Main entry point: compute optimal portfolio allocation.

    Args:
        codes: stock codes
        daily_returns: list of per-stock daily return series
        method: "risk_parity" | "min_variance" | "inverse_vol" | "equal"
        apply_correlation_penalty: whether to reduce weights of correlated stocks

    Returns:
        PortfolioAllocation with weights and risk metrics.
    """
    n = len(codes)
    if n == 0:
        return PortfolioAllocation(weights=[], method=method)

    cov = compute_covariance_matrix(daily_returns)
    corr = compute_correlation_matrix(daily_returns)

    if method == "risk_parity":
        weights = risk_parity_weights(cov)
    elif method == "min_variance":
        weights = min_variance_weights(cov)
    elif method == "inverse_vol":
        vols = np.sqrt(np.diag(cov))
        vols = np.where(vols < TOLERANCE, TOLERANCE, vols)
        weights = (1.0 / vols) / np.sum(1.0 / vols)
    else:
        weights = np.ones(n) / n

    if apply_correlation_penalty and n > 1:
        weights = correlation_penalty(weights, corr)

    port_vol = float(np.sqrt(weights @ cov @ weights))
    rc = _compute_risk_contributions(weights, cov)

    # Diversification ratio: weighted average vol / portfolio vol
    wavg_vol = float(np.sum(weights * np.sqrt(np.diag(cov))))
    div_ratio = wavg_vol / port_vol if port_vol > 0 else 1.0

    return PortfolioAllocation(
        weights=list(weights),
        method=method,
        risk_contributions=list(rc),
        expected_vol=port_vol,
        diversification_ratio=div_ratio,
    )


def build_allocation_prompt(allocation: PortfolioAllocation, codes: list[str]) -> str:
    """Build a human-readable allocation summary for LLM prompt injection."""
    if not allocation.weights:
        return ""

    lines = [
        "### 组合优化建议 (Portfolio Optimization)",
        f"方法: {allocation.method} | 预期波动率: {allocation.expected_vol*100:.1f}% | 分散化比率: {allocation.diversification_ratio:.2f}",
        "",
        "| 股票 | 建议权重 | 风险贡献 |",
        "|------|---------|---------|",
    ]
    for i, code in enumerate(codes):
        w = allocation.weights[i] * 100
        rc = (allocation.risk_contributions or [0]*len(codes))[i] * 100
        lines.append(f"| {code} | {w:.1f}% | {rc:.1f}% |")

    lines.append(f"\n> 基于{'风险平价' if allocation.method == 'risk_parity' else allocation.method}计算。")
    lines.append("> 高波动/高相关性股票自动降权，低波动/低相关股票适度加仓。")
    return "\n".join(lines)
