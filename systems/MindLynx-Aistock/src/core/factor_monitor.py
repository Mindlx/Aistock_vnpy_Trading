"""Factor performance monitor — IC/IR tracking and visualization.

Tracks information coefficient (IC) and information ratio (IR) for each core
factor over time. Records daily IC values, computes rolling statistics, and
generates time-series visualisation reports.

Usage:
    from src.core.factor_monitor import FactorMonitor
    monitor = FactorMonitor()
    monitor.record_daily_ic(date, factor_values, forward_returns)
    monitor.generate_report()
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from typing import Any

import numpy as np

from src.config import get_config

logger = logging.getLogger(__name__)

# Core factor names — must match factor_engine.CORE_FACTORS
FACTOR_NAMES = [
    "momentum_reversal",
    "turnover_sentiment",
    "low_volatility",
    "momentum_spread",
    "volume_trend",
    "size_factor",
    "illiquidity",
    "max_effect",
    "price_position",
    "volume_acceleration",
    "consecutive_direction",
    "volatility_ratio",
]

FACTOR_DISPLAY_NAMES = {
    "momentum_reversal": "1月反转",
    "turnover_sentiment": "换手率情绪",
    "low_volatility": "低波动",
    "momentum_spread": "动量价差",
    "volume_trend": "量价配合度",
    "size_factor": "规模因子",
    "illiquidity": "非流动性",
    "max_effect": "极端收益",
    "price_position": "价格位置",
    "volume_acceleration": "量能加速",
    "consecutive_direction": "连续方向",
    "volatility_ratio": "波动率比",
}

# Default rolling window for IR computation (trading days)
DEFAULT_ROLLING_WINDOW = 20

# Number of quantile groups for portfolio stratification
N_GROUPS = 5


def _spearman_rank_correlation(x: np.ndarray, y: np.ndarray) -> float:
    """Compute Spearman rank correlation between two arrays."""
    if len(x) < 2 or len(y) < 2:
        return 0.0
    x_rank = np.argsort(np.argsort(x)).astype(float)
    y_rank = np.argsort(np.argsort(y)).astype(float)
    # If all values are identical, all ranks are tied; correlation is undefined
    if np.std(x_rank) < 1e-10 or np.std(y_rank) < 1e-10:
        return 0.0
    n = len(x_rank)
    d = x_rank - y_rank
    rho = 1.0 - (6.0 * np.sum(d**2)) / (n * (n**2 - 1.0))
    return float(rho) if not np.isnan(rho) else 0.0


def _pearson_correlation(x: np.ndarray, y: np.ndarray) -> float:
    """Compute Pearson correlation between two arrays."""
    if len(x) < 2 or len(y) < 2:
        return 0.0
    if np.std(x) < 1e-10 or np.std(y) < 1e-10:
        return 0.0
    corr = np.corrcoef(x, y)[0, 1]
    return float(corr) if not np.isnan(corr) else 0.0


class FactorMonitor:
    """Tracks and visualises factor IC/IR performance over time.

    Records daily IC values (Spearman and Pearson) for each factor, computes
    rolling IC mean and IR, and generates time-series charts and cumulative
    return curves for stratified portfolios.
    """

    def __init__(
        self,
        output_dir: str | None = None,
        rolling_window: int = DEFAULT_ROLLING_WINDOW,
        enabled: bool | None = None,
    ):
        cfg = get_config()
        self.enabled = enabled if enabled is not None else cfg.factor_monitor_enabled
        self.output_dir = output_dir or cfg.factor_monitor_output_dir
        self.rolling_window = rolling_window

        # Internal storage: dict[date_str, dict[factor_name, dict]]
        # Each entry: {"spearman_ic": float, "pearson_ic": float, "date": str}
        self._daily_ic: dict[str, dict[str, dict[str, Any]]] = {}

        # Per-factor sliding windows for IR computation
        self._ic_history: dict[str, list[float]] = {name: [] for name in FACTOR_NAMES}

        # Load existing data if available
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_multicollinearity(
        self,
        factor_values: dict[str, np.ndarray],
        threshold: float = 0.7,
    ) -> list[str]:
        """Check for highly correlated factor pairs (risk of inflated composite).

        Returns list of warning messages for pairs with |Spearman rho| >= threshold.
        The two momentum factors (reversal + spread, 70% combined weight) are
        the primary concern — if correlated, the composite score is biased.
        """
        warnings: list[str] = []
        names = [n for n in FACTOR_NAMES if n in factor_values and len(factor_values[n]) >= 10]
        for i, n1 in enumerate(names):
            for j, n2 in enumerate(names):
                if j <= i:
                    continue
                rho = _spearman_rank_correlation(factor_values[n1], factor_values[n2])
                if abs(rho) >= threshold:
                    label1 = FACTOR_DISPLAY_NAMES.get(n1, n1)
                    label2 = FACTOR_DISPLAY_NAMES.get(n2, n2)
                    direction = "同向" if rho > 0 else "反向"
                    warnings.append(
                        f"[FactorEngine] 多重共线性: {label1}({n1}) vs {label2}({n2}) "
                        f"Spearman ρ={rho:.3f}({direction}), 建议降低其中一方的权重"
                    )
        return warnings

    def record_daily_ic(
        self,
        trade_date: date | str,
        factor_values: dict[str, np.ndarray],
        forward_returns: np.ndarray,
    ) -> None:
        """Record daily IC (Spearman and Pearson) for all tracked factors.

        Args:
            trade_date: Trading date.
            factor_values: Dict mapping factor name -> array of factor values
                across all stocks in the universe.
            forward_returns: Array of forward returns (same length as factor
                value arrays).
        """
        if not self.enabled:
            return

        date_str = _format_date(trade_date)

        day_data: dict[str, dict[str, Any]] = {}
        for name in FACTOR_NAMES:
            fv = factor_values.get(name)
            if fv is None or len(fv) < 2 or len(forward_returns) < 2:
                continue

            spearman_ic = _spearman_rank_correlation(fv, forward_returns)
            pearson_ic = _pearson_correlation(fv, forward_returns)

            day_data[name] = {
                "spearman_ic": round(spearman_ic, 6),
                "pearson_ic": round(pearson_ic, 6),
                "date": date_str,
            }

            # Update sliding window for IR
            self._ic_history[name].append(spearman_ic)
            if len(self._ic_history[name]) > self.rolling_window * 3:
                # Trim to avoid unbounded growth (keep 3x rolling window)
                self._ic_history[name] = self._ic_history[name][-self.rolling_window * 3 :]

        if day_data:
            self._daily_ic[date_str] = day_data

        logger.debug(
            "Recorded IC for %s: %d factors",
            date_str,
            len(day_data),
        )

    def get_ic_summary(self, date_str: str | None = None) -> dict[str, Any]:
        """Get IC summary for a specific date or latest available.

        Returns dict with factor IC values and rolling IR.
        """
        if date_str is None:
            if not self._daily_ic:
                return {"error": "no data"}
            date_str = max(self._daily_ic.keys())

        day_data = self._daily_ic.get(date_str)
        if day_data is None:
            return {"error": f"date {date_str} not found"}

        result: dict[str, Any] = {"date": date_str, "factors": {}}
        for name in FACTOR_NAMES:
            entry = day_data.get(name)
            if entry is None:
                continue
            ic_series = np.array(self._ic_history[name])
            ic_mean = float(np.mean(ic_series)) if len(ic_series) > 0 else 0.0
            ic_std = float(np.std(ic_series, ddof=1)) if len(ic_series) > 1 else 0.0
            ir = ic_mean / ic_std if ic_std > 1e-10 else 0.0

            result["factors"][name] = {
                "spearman_ic": entry["spearman_ic"],
                "pearson_ic": entry["pearson_ic"],
                "rolling_ic_mean": round(ic_mean, 6),
                "rolling_ir": round(ir, 6),
                "sample_count": len(ic_series),
            }

        return result

    def get_ic_time_series(self, factor_name: str) -> list[dict[str, Any]]:
        """Get the full IC time series for a given factor.

        Returns list of {date, spearman_ic, pearson_ic} sorted by date.
        """
        records = []
        for date_str in sorted(self._daily_ic.keys()):
            entry = self._daily_ic[date_str].get(factor_name)
            if entry:
                records.append(
                    {
                        "date": date_str,
                        "spearman_ic": entry["spearman_ic"],
                        "pearson_ic": entry["pearson_ic"],
                    }
                )
        return records

    def compute_rolling_ir(self, factor_name: str) -> list[dict[str, Any]]:
        """Compute rolling IR (IC_mean / IC_std) over the rolling window.

        Returns list of {date, ir} for each date with a full window.
        """
        ic_series = self._ic_history.get(factor_name, [])
        if len(ic_series) < self.rolling_window:
            return []

        result = []
        dates = sorted(self._daily_ic.keys())
        # Align dates with IC series
        for i in range(self.rolling_window - 1, len(ic_series)):
            window = ic_series[i - self.rolling_window + 1 : i + 1]
            window_mean = np.mean(window)
            window_std = np.std(window, ddof=1)
            ir = window_mean / window_std if window_std > 1e-10 else 0.0
            if i < len(dates):
                result.append(
                    {
                        "date": dates[i],
                        "ir": round(float(ir), 6),
                    }
                )

        return result

    def compute_layered_portfolio_returns(
        self,
        factor_values: dict[str, np.ndarray],
        forward_returns: np.ndarray,
        factor_name: str,
    ) -> dict[str, Any]:
        """Compute stratified portfolio returns for a factor.

        Splits stocks into N_GROUPS (quintiles) by factor value and computes
        the mean forward return for each group.

        Args:
            factor_values: Dict mapping factor name -> factor value array.
            forward_returns: Array of forward returns.
            factor_name: Name of the factor to stratify on.

        Returns:
            Dict mapping group label (e.g. "Q1 (low)" to "Q5 (high)") to
            mean forward return.
        """
        fv = factor_values.get(factor_name)
        if fv is None or len(fv) < N_GROUPS or len(fv) != len(forward_returns):
            return {"error": "insufficient data"}

        # Sort by factor value and split into quintiles
        sorted_indices = np.argsort(fv)
        bucket_size = len(sorted_indices) // N_GROUPS

        result: dict[str, Any] = {}
        for i in range(N_GROUPS):
            start = i * bucket_size
            end = start + bucket_size if i < N_GROUPS - 1 else len(sorted_indices)
            group_indices = sorted_indices[start:end]
            group_return = float(np.mean(forward_returns[group_indices]))

            if i == 0:
                label = "Q1 (低值)"
            elif i == N_GROUPS - 1:
                label = f"Q{N_GROUPS} (高值)"
            else:
                label = f"Q{i + 1}"

            result[label] = round(group_return, 6)

        # Long-short spread (Q5 - Q1)
        result["long_short_spread"] = round(
            result.get(f"Q{N_GROUPS} (高值)", 0.0) - result.get("Q1 (低值)", 0.0),
            6,
        )

        return result

    def generate_report(self, output_path: str | None = None) -> str | None:
        """Generate IC/IR time-series visualisation report as a PNG image.

        Creates a multi-panel chart:
        - Panel 1: IC time series for each factor (Spearman)
        - Panel 2: Rolling IR time series
        - Panel 3: IC heatmap (factors × time)

        Args:
            output_path: Path to save the image. If None, uses default path
                under output_dir with today's date.

        Returns:
            Path to the generated image, or None if failed.
        """
        if not self.enabled or not self._daily_ic:
            logger.info("FactorMonitor disabled or no data; skipping report")
            return None

        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.dates as mdates
            import matplotlib.pyplot as plt
        except ImportError:
            logger.warning("matplotlib not installed; cannot generate report")
            return None

        if output_path is None:
            output_path = os.path.join(
                self.output_dir,
                f"factor_ic_ir_{datetime.now().strftime('%Y%m%d')}.png",
            )

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        dates_list = sorted(self._daily_ic.keys())
        date_objs = [_parse_date_str(d) for d in dates_list]

        # --- Build data arrays ---
        factor_ics: dict[str, list[float]] = {name: [] for name in FACTOR_NAMES}
        for d in dates_list:
            for name in FACTOR_NAMES:
                entry = self._daily_ic[d].get(name)
                factor_ics[name].append(entry["spearman_ic"] if entry else 0.0)

        # --- Create multi-panel figure ---
        fig, axes = plt.subplots(3, 1, figsize=(14, 12), sharex=True)
        colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]

        # Panel 1: IC time series
        ax1 = axes[0]
        for i, name in enumerate(FACTOR_NAMES):
            ics = factor_ics[name]
            if any(abs(v) > 1e-6 for v in ics):
                ax1.plot(
                    date_objs,
                    ics,
                    label=FACTOR_DISPLAY_NAMES.get(name, name),
                    color=colors[i % len(colors)],
                    linewidth=1.2,
                    alpha=0.85,
                )
        ax1.axhline(y=0, color="gray", linestyle="--", linewidth=0.7)
        ax1.set_ylabel("Spearman IC")
        ax1.set_title("因子 IC 时间序列 (Spearman Rank Correlation)")
        ax1.legend(fontsize=8, loc="best")
        ax1.grid(True, alpha=0.3)

        # Panel 2: Rolling IR
        ax2 = axes[1]
        for i, name in enumerate(FACTOR_NAMES):
            ir_data = self.compute_rolling_ir(name)
            if ir_data:
                ir_dates = [_parse_date_str(r["date"]) for r in ir_data]
                ir_values = [r["ir"] for r in ir_data]
                ax2.plot(
                    ir_dates,
                    ir_values,
                    label=FACTOR_DISPLAY_NAMES.get(name, name),
                    color=colors[i % len(colors)],
                    linewidth=1.2,
                    alpha=0.85,
                )
        ax2.axhline(y=0, color="gray", linestyle="--", linewidth=0.7)
        ax2.axhline(y=0.5, color="green", linestyle=":", linewidth=0.5, alpha=0.5)
        ax2.set_ylabel(f"IR ({self.rolling_window}日滚动)")
        ax2.set_title("因子 IR 时间序列 (IC均值 / IC标准差)")
        ax2.legend(fontsize=8, loc="best")
        ax2.grid(True, alpha=0.3)

        # Panel 3: IC heatmap
        ax3 = axes[2]
        heatmap_data = []
        factor_labels = []
        for name in FACTOR_NAMES:
            ics = factor_ics[name]
            if any(abs(v) > 1e-6 for v in ics):
                heatmap_data.append(ics)
                factor_labels.append(FACTOR_DISPLAY_NAMES.get(name, name))

        if heatmap_data:
            im = ax3.imshow(
                heatmap_data,
                aspect="auto",
                cmap="RdYlGn",
                interpolation="nearest",
                vmin=-0.15,
                vmax=0.15,
            )
            ax3.set_yticks(range(len(factor_labels)))
            ax3.set_yticklabels(factor_labels, fontsize=9)
            ax3.set_xlabel("交易日")
            ax3.set_title("IC 热力图 (绿=正, 红=负)")
            plt.colorbar(im, ax=ax3, shrink=0.8)

            # Annotate axis with dates at reasonable intervals
            n_dates = len(dates_list)
            step = max(1, n_dates // 10)
            ax3.set_xticks(range(0, n_dates, step))
            ax3.set_xticklabels(
                [dates_list[i] for i in range(0, n_dates, step)],
                fontsize=7,
                rotation=45,
            )

        plt.tight_layout()
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)

        logger.info("Factor monitor report saved to %s", output_path)
        return output_path

    def generate_cumulative_return_chart(
        self,
        factor_name: str,
        layered_returns: list[dict[str, float]] | None = None,
        output_path: str | None = None,
    ) -> str | None:
        """Generate cumulative return curves for stratified portfolios.

        Args:
            factor_name: Factor name to show.
            layered_returns: List of dicts mapping group labels to returns,
                one per time step. If None, uses stored data.
            output_path: Output image path.

        Returns:
            Path to the generated image, or None if failed.
        """
        if not self.enabled:
            return None
        if layered_returns is None or len(layered_returns) < 2:
            logger.info("Insufficient layered return data for chart")
            return None

        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            logger.warning("matplotlib not installed; cannot generate chart")
            return None

        if output_path is None:
            output_path = os.path.join(
                self.output_dir,
                f"factor_{factor_name}_cumulative_{datetime.now().strftime('%Y%m%d')}.png",
            )

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        fig, ax = plt.subplots(figsize=(12, 6))

        # Extract group labels from first entry
        group_keys = [k for k in layered_returns[0].keys() if k != "date"]
        groups_cum: dict[str, list[float]] = {k: [] for k in group_keys}

        for entry in layered_returns:
            for k in group_keys:
                prev = groups_cum[k][-1] if groups_cum[k] else 0.0
                groups_cum[k].append(prev + entry.get(k, 0.0))

        colors = ["#d62728", "#ff9896", "#cccccc", "#98df8a", "#2ca02c"]
        for i, k in enumerate(group_keys):
            ax.plot(
                groups_cum[k],
                label=k,
                color=colors[i % len(colors)],
                linewidth=1.5,
            )

        ax.set_title(f"因子分层组合累计收益 — {FACTOR_DISPLAY_NAMES.get(factor_name, factor_name)}")
        ax.set_xlabel("时间步")
        ax.set_ylabel("累计收益")
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)

        fig.tight_layout()
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)

        logger.info("Cumulative return chart saved to %s", output_path)
        return output_path

    def save_data(self, filepath: str | None = None) -> str | None:
        """Save IC/IR history to a JSON file.

        Args:
            filepath: Full path to JSON file. If None, uses default path
                under output_dir.

        Returns:
            Path to saved file, or None if failed.
        """
        if not self.enabled:
            return None

        if filepath is None:
            filepath = os.path.join(self.output_dir, "factor_ic_ir_history.json")

        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        data = {
            "metadata": {
                "rolling_window": self.rolling_window,
                "factor_names": FACTOR_NAMES,
                "last_updated": datetime.now().isoformat(),
            },
            "daily_ic": self._daily_ic,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info("Factor monitor data saved to %s (%d dates)", filepath, len(self._daily_ic))
        return filepath

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load IC/IR history from JSON file if it exists."""
        filepath = os.path.join(self.output_dir, "factor_ic_ir_history.json")
        if not os.path.exists(filepath):
            return

        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load factor monitor data: %s", e)
            return

        daily_ic = data.get("daily_ic", {})
        self._daily_ic = daily_ic

        # Rebuild IC history from loaded data
        self._ic_history = {name: [] for name in FACTOR_NAMES}
        for date_str in sorted(daily_ic.keys()):
            for name in FACTOR_NAMES:
                entry = daily_ic[date_str].get(name)
                if entry:
                    self._ic_history[name].append(entry.get("spearman_ic", 0.0))

        logger.info("Loaded factor monitor data: %d dates", len(daily_ic))

    def get_summary_stats(self) -> dict[str, Any]:
        """Compute overall summary statistics across all factors."""
        result: dict[str, Any] = {
            "total_dates": len(self._daily_ic),
            "date_range": {},
            "factors": {},
        }

        if self._daily_ic:
            dates_sorted = sorted(self._daily_ic.keys())
            result["date_range"] = {
                "start": dates_sorted[0],
                "end": dates_sorted[-1],
            }

        for name in FACTOR_NAMES:
            ics = np.array(self._ic_history[name])
            if len(ics) == 0:
                continue

            ic_mean = float(np.mean(ics))
            ic_std = float(np.std(ics, ddof=1)) if len(ics) > 1 else 0.0
            ir = ic_mean / ic_std if ic_std > 1e-10 else 0.0
            ic_positive_ratio = float(np.sum(ics > 0)) / len(ics) if len(ics) > 0 else 0.0

            result["factors"][name] = {
                "display_name": FACTOR_DISPLAY_NAMES.get(name, name),
                "mean_ic": round(ic_mean, 6),
                "std_ic": round(ic_std, 6),
                "ir": round(ir, 6),
                "ic_positive_ratio": round(ic_positive_ratio, 4),
                "sample_count": len(ics),
            }

        return result


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _format_date(d: date | str) -> str:
    """Normalise a date to YYYY-MM-DD string."""
    if isinstance(d, str):
        return d
    return d.isoformat()


def _parse_date_str(s: str) -> datetime:
    """Parse a YYYY-MM-DD string to datetime."""
    parts = s.split("-")
    return datetime(int(parts[0]), int(parts[1]), int(parts[2]))


def check_factor_recalibration() -> dict[str, Any] | None:
    """Check if factor weight recalibration is needed and run if so.

    Delegates to FactorRecalibrator.auto_recalibrate_if_needed().
    Only runs when FACTOR_MONITOR_ENABLED=true.

    Returns the recalibration report dict if ran, None if skipped.
    """
    try:
        from src.core.factor_recalibrate import auto_recalibrate_if_needed

        return auto_recalibrate_if_needed()
    except Exception as e:
        logger.warning("Factor recalibration check failed: %s", e)
        return None


def create_factor_monitor() -> FactorMonitor:
    """Factory function: create a FactorMonitor from global config."""
    cfg = get_config()
    return FactorMonitor(
        enabled=cfg.factor_monitor_enabled,
        output_dir=cfg.factor_monitor_output_dir,
    )
