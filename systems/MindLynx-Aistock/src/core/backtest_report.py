"""
====================================
策略回测报告自动生成器
====================================

职责：
1. 从 backtest_engine 的回测结果生成可读的 Markdown 回测报告
2. 计算性能指标：总收益率、年化收益率、夏普比率、最大回撤、胜率、盈亏比
3. 生成净值曲线图（使用 matplotlib）
4. 保存报告到 reports/backtest/ 目录
5. 集成到通知系统（作为 report_type=BACKTEST）
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import matplotlib
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    MATPLOTLIB_AVAILABLE = True
except ImportError:  # pragma: no cover
    MATPLOTLIB_AVAILABLE = False

logger = logging.getLogger(__name__)

BACKTEST_REPORT_TYPE = "BACKTEST"


@dataclass
class BacktestPerformanceMetrics:
    """Comprehensive backtest performance metrics beyond what backtest_engine provides."""

    # Summary-level data (from BacktestService summary)
    total_evaluations: int = 0
    completed_count: int = 0
    insufficient_count: int = 0
    long_count: int = 0
    cash_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    neutral_count: int = 0
    win_rate_pct: float | None = None
    neutral_rate_pct: float | None = None
    direction_accuracy_pct: float | None = None
    avg_stock_return_pct: float | None = None
    avg_simulated_return_pct: float | None = None
    stop_loss_trigger_rate: float | None = None
    take_profit_trigger_rate: float | None = None
    avg_days_to_first_hit: float | None = None

    # Derived / additional metrics
    total_return_pct: float | None = None
    annualized_return_pct: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown_pct: float | None = None
    profit_loss_ratio: float | None = None
    avg_win_pct: float | None = None
    avg_loss_pct: float | None = None

    # Metadata
    strategy_name: str = "Overall"
    stock_code: str | None = None
    scope: str = "overall"
    eval_window_days: int = 10
    engine_version: str = "v1"
    report_generated_at: str = ""
    backtest_start_date: str = ""
    backtest_end_date: str = ""
    initial_capital: float = 100000.0

    # Per-result raw data for NAV curve (list of dicts with date, cumulative_return)
    nav_points: list[dict[str, Any]] = field(default_factory=list)


class BacktestReportGenerator:
    """Generate readable backtest reports from backtest_engine results."""

    def __init__(self, output_dir: str | None = None):
        self._enabled = True
        self._output_dir = Path(output_dir or "reports/backtest/")

        try:
            from src.config import get_config

            config = get_config()
            self._enabled = getattr(config, "backtest_report_enabled", True)
            resolved_dir = output_dir or getattr(config, "backtest_report_output_dir", "reports/backtest/")
            self._output_dir = Path(resolved_dir)
        except Exception:
            # Fallback to defaults when config is not available (e.g., in testing)
            pass

        self._output_dir.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def generate(
        self,
        summary: dict[str, Any] | None,
        results: list[dict[str, Any]] | None = None,
        *,
        strategy_name: str = "Overall",
        stock_code: str | None = None,
        initial_capital: float = 100000.0,
    ) -> str | None:
        """Generate and save a backtest report.

        Args:
            summary: Summary metrics from BacktestService (get_summary / _summary_to_dict).
            results: Optional list of per-evaluation result dicts for NAV curve.
            strategy_name: Display name for the strategy.
            stock_code: Stock code for per-stock reports (None for overall).
            initial_capital: Assumed initial capital for NAV curve scaling.

        Returns:
            Path to the generated report file, or None if disabled / no data.
        """
        if not self._enabled:
            logger.debug("Backtest report generation is disabled.")
            return None

        if not summary:
            logger.warning("No summary data provided; skipping backtest report.")
            return None

        metrics = self._compute_metrics(
            summary=summary,
            results=results,
            strategy_name=strategy_name,
            stock_code=stock_code,
            initial_capital=initial_capital,
        )

        report_md = self._render_markdown(metrics)
        report_path = self._save_report(report_md, metrics)
        self._save_nav_curve(metrics)

        logger.info("Backtest report saved to %s", report_path)
        return report_path

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @staticmethod
    def compute_stock_return_pct(series: list[float]) -> float | None:
        """Compute total return from a list of cumulative NAV values."""
        if not series or len(series) < 2:
            return None
        try:
            return (series[-1] - series[0]) / series[0] * 100
        except (ZeroDivisionError, TypeError):
            return None

    @staticmethod
    def compute_annualized_return(total_return_pct: float | None, num_trading_days: int | None) -> float | None:
        """Annualize a return over a given number of trading days (~252/year)."""
        if total_return_pct is None or num_trading_days is None or num_trading_days <= 0:
            return None
        years = num_trading_days / 252.0
        if years <= 0:
            return None
        try:
            return ((1 + total_return_pct / 100.0) ** (1.0 / years) - 1) * 100
        except (ValueError, ZeroDivisionError):
            return None

    @staticmethod
    def compute_sharpe_ratio(returns: list[float], risk_free_rate: float = 0.02) -> float | None:
        """Compute Sharpe ratio from a list of daily return pcts using ~252 trading days."""
        if not returns or len(returns) < 3:
            return None
        try:
            excess_returns = [r - risk_free_rate for r in returns]
            mean_excess = sum(excess_returns) / len(excess_returns)
            variance = sum((r - mean_excess) ** 2 for r in excess_returns) / (len(excess_returns) - 1)
            if variance <= 0:
                return None
            std_dev = math.sqrt(variance)
            if std_dev == 0:
                return None
            daily_sharpe = mean_excess / std_dev
            return round(daily_sharpe * math.sqrt(252), 4)
        except (ZeroDivisionError, TypeError, ValueError):
            return None

    @staticmethod
    def compute_max_drawdown(nav_series: list[float]) -> float | None:
        """Compute maximum drawdown percentage from a NAV series."""
        if not nav_series or len(nav_series) < 2:
            return None
        try:
            peak = nav_series[0]
            max_dd = 0.0
            for val in nav_series:
                if val > peak:
                    peak = val
                dd = (val - peak) / peak
                if dd < max_dd:
                    max_dd = dd
            return round(max_dd * 100, 2)
        except (ZeroDivisionError, TypeError):
            return None

    @staticmethod
    def compute_profit_loss_ratio(
        win_count: int,
        loss_count: int,
        total_win_return: float,
        total_loss_return: float,
    ) -> float | None:
        """Compute profit/loss ratio (average win / average loss)."""
        if win_count <= 0 or loss_count <= 0:
            return None
        try:
            avg_win = total_win_return / win_count
            avg_loss = abs(total_loss_return / loss_count)
            if avg_loss == 0:
                return None
            return round(avg_win / avg_loss, 2)
        except (ZeroDivisionError, TypeError):
            return None

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _compute_metrics(
        self,
        summary: dict[str, Any],
        results: list[dict[str, Any]] | None,
        strategy_name: str,
        stock_code: str | None,
        initial_capital: float,
    ) -> BacktestPerformanceMetrics:
        """Build a BacktestPerformanceMetrics from summary + optional results."""
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

        metrics = BacktestPerformanceMetrics(
            total_evaluations=summary.get("total_evaluations", 0),
            completed_count=summary.get("completed_count", 0),
            insufficient_count=summary.get("insufficient_count", 0),
            long_count=summary.get("long_count", 0),
            cash_count=summary.get("cash_count", 0),
            win_count=summary.get("win_count", 0),
            loss_count=summary.get("loss_count", 0),
            neutral_count=summary.get("neutral_count", 0),
            win_rate_pct=summary.get("win_rate_pct"),
            neutral_rate_pct=summary.get("neutral_rate_pct"),
            direction_accuracy_pct=summary.get("direction_accuracy_pct"),
            avg_stock_return_pct=summary.get("avg_stock_return_pct"),
            avg_simulated_return_pct=summary.get("avg_simulated_return_pct"),
            stop_loss_trigger_rate=summary.get("stop_loss_trigger_rate"),
            take_profit_trigger_rate=summary.get("take_profit_trigger_rate"),
            avg_days_to_first_hit=summary.get("avg_days_to_first_hit"),
            strategy_name=strategy_name,
            stock_code=stock_code,
            scope=summary.get("scope", "overall"),
            eval_window_days=summary.get("eval_window_days", 10),
            engine_version=summary.get("engine_version", "v1"),
            report_generated_at=now_str,
            backtest_start_date=summary.get("analysis_date_from", ""),
            backtest_end_date=summary.get("analysis_date_to", ""),
            initial_capital=initial_capital,
        )

        # Derive total return from avg_simulated_return_pct as a proxy
        # For more accurate total return, we build a simulated NAV curve.
        metrics.total_return_pct = self._compute_total_return(results, summary, initial_capital)

        # Build NAV curve for max drawdown and Sharpe
        nav_points, daily_returns = self._build_nav_curve(results, initial_capital)
        metrics.nav_points = nav_points

        if nav_points:
            nav_values = [p["nav"] for p in nav_points]
            metrics.max_drawdown_pct = self.compute_max_drawdown(nav_values)
            num_trading_days = len(nav_points) - 1
            if metrics.total_return_pct is not None:
                metrics.annualized_return_pct = self.compute_annualized_return(
                    metrics.total_return_pct, num_trading_days
                )
            if daily_returns:
                metrics.sharpe_ratio = self.compute_sharpe_ratio(daily_returns)

        # Compute avg win/loss and P/L ratio
        if results:
            win_returns = [
                r.get("simulated_return_pct", 0) or 0
                for r in results
                if r.get("outcome") == "win" and r.get("simulated_return_pct") is not None
            ]
            loss_returns = [
                r.get("simulated_return_pct", 0) or 0
                for r in results
                if r.get("outcome") == "loss" and r.get("simulated_return_pct") is not None
            ]
            if win_returns:
                metrics.avg_win_pct = round(sum(win_returns) / len(win_returns), 2)
            if loss_returns:
                metrics.avg_loss_pct = round(sum(loss_returns) / len(loss_returns), 2)
            metrics.profit_loss_ratio = self.compute_profit_loss_ratio(
                len(win_returns),
                len(loss_returns),
                sum(win_returns),
                sum(loss_returns),
            )

        return metrics

    def _compute_total_return(
        self,
        results: list[dict[str, Any]] | None,
        summary: dict[str, Any],
        initial_capital: float,
    ) -> float | None:
        """Compute total return from results or fall back to avg_simulated_return_pct."""
        if results:
            nav_points, _ = self._build_nav_curve(results, initial_capital)
            if len(nav_points) >= 2:
                start_nav = nav_points[0]["nav"]
                end_nav = nav_points[-1]["nav"]
                if start_nav > 0:
                    return round((end_nav - start_nav) / start_nav * 100, 2)

        # Fallback: use avg_simulated_return_pct * number of trades as rough estimate
        avg_ret = summary.get("avg_simulated_return_pct")
        completed = summary.get("completed_count", 0)
        if avg_ret is not None and completed > 0:
            return round(float(avg_ret) * completed, 2)
        return None

    def _build_nav_curve(
        self,
        results: list[dict[str, Any]] | None,
        initial_capital: float,
    ) -> tuple[list[dict[str, Any]], list[float]]:
        """Build a simulated NAV curve from sequential trade results.

        Returns:
            (nav_points, daily_returns) where each nav_point is
            {date, nav, cumulative_return_pct} and daily_returns is
            a list of per-trade return percentages.
        """
        nav_points: list[dict[str, Any]] = []
        daily_returns: list[float] = []

        if not results:
            return nav_points, daily_returns

        nav = initial_capital
        nav_points.append(
            {
                "date": "start",
                "nav": nav,
                "cumulative_return_pct": 0.0,
            }
        )

        for r in results:
            sim_ret = r.get("simulated_return_pct")
            if sim_ret is None:
                continue
            trade_date = r.get("analysis_date") or r.get("first_hit_date") or "unknown"
            pnl = nav * float(sim_ret) / 100.0
            nav += pnl
            cumulative_ret = (nav - initial_capital) / initial_capital * 100
            daily_returns.append(float(sim_ret))
            nav_points.append(
                {
                    "date": trade_date,
                    "nav": round(nav, 2),
                    "cumulative_return_pct": round(cumulative_ret, 2),
                }
            )

        return nav_points, daily_returns

    def _render_markdown(self, metrics: BacktestPerformanceMetrics) -> str:
        """Render the metrics as a Markdown report string."""
        lines: list[str] = []

        # Title
        title = f"# 回测报告 - {metrics.strategy_name}"
        if metrics.stock_code:
            title += f" ({metrics.stock_code})"
        lines.append(title)
        lines.append("")

        # Basic parameters
        lines.append("## 基本参数")
        lines.append("")
        lines.append("| 参数 | 值 |")
        lines.append("|------|-----|")
        lines.append(f"| 回测引擎版本 | {metrics.engine_version} |")
        lines.append(f"| 评估窗口（交易日） | {metrics.eval_window_days} |")
        lines.append(f"| 初始资金 | {metrics.initial_capital:,.0f} |")
        if metrics.backtest_start_date:
            lines.append(f"| 回测区间起始 | {metrics.backtest_start_date} |")
        if metrics.backtest_end_date:
            lines.append(f"| 回测区间结束 | {metrics.backtest_end_date} |")
        lines.append(f"| 总评估次数 | {metrics.total_evaluations} |")
        lines.append(f"| 完成次数 | {metrics.completed_count} |")
        lines.append(f"| 报告生成时间 | {metrics.report_generated_at} |")
        lines.append("")

        # Performance indicators
        lines.append("## 绩效指标")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")

        def _fmt_pct(val: float | None, suffix: str = "%") -> str:
            if val is None:
                return "N/A"
            sign = "+" if val > 0 else ""
            return f"{sign}{val:.2f}{suffix}"

        lines.append(f"| 总收益率 | {_fmt_pct(metrics.total_return_pct)} |")
        lines.append(f"| 年化收益率 | {_fmt_pct(metrics.annualized_return_pct)} |")

        if metrics.sharpe_ratio is not None:
            lines.append(f"| 夏普比率 | {metrics.sharpe_ratio:.2f} |")
        else:
            lines.append("| 夏普比率 | N/A |")

        lines.append(f"| 最大回撤 | {_fmt_pct(metrics.max_drawdown_pct)} |")
        lines.append(f"| 胜率 | {_fmt_pct(metrics.win_rate_pct)} |")
        lines.append(f"| 方向准确率 | {_fmt_pct(metrics.direction_accuracy_pct)} |")
        lines.append(f"| 盈亏比 | {metrics.profit_loss_ratio if metrics.profit_loss_ratio is not None else 'N/A'} |")
        lines.append(f"| 平均盈利 | {_fmt_pct(metrics.avg_win_pct)} |")
        lines.append(f"| 平均亏损 | {_fmt_pct(metrics.avg_loss_pct)} |")
        lines.append("")

        # Trade analysis
        lines.append("## 交易分析")
        lines.append("")
        lines.append("| 分类 | 数量 | 占比 |")
        lines.append("|------|------|------|")

        completed = metrics.completed_count or 1
        loss_rate = (metrics.loss_count / completed * 100) if metrics.loss_count > 0 else 0.0
        lines.append(f"| 盈利交易 | {metrics.win_count} | {_fmt_pct(metrics.win_rate_pct)} |")
        lines.append(f"| 亏损交易 | {metrics.loss_count} | {_fmt_pct(loss_rate)} |")
        lines.append(f"| 中性交易 | {metrics.neutral_count} | {_fmt_pct(metrics.neutral_rate_pct)} |")
        lines.append(f"| 多头建议 | {metrics.long_count} | {_fmt_pct(metrics.long_count / completed * 100)} |")
        lines.append(f"| 空仓建议 | {metrics.cash_count} | {_fmt_pct(metrics.cash_count / completed * 100)} |")

        if metrics.stop_loss_trigger_rate is not None:
            lines.append(f"| 止损触发率 | {_fmt_pct(metrics.stop_loss_trigger_rate)} |")
        if metrics.take_profit_trigger_rate is not None:
            lines.append(f"| 止盈触发率 | {_fmt_pct(metrics.take_profit_trigger_rate)} |")
        if metrics.avg_days_to_first_hit is not None:
            lines.append(f"| 平均触发天数 | {metrics.avg_days_to_first_hit:.1f} 天 |")
        lines.append("")

        # Risk analysis
        lines.append("## 风险分析")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 最大回撤 | {_fmt_pct(metrics.max_drawdown_pct)} |")
        lines.append(f"| 夏普比率 | {metrics.sharpe_ratio if metrics.sharpe_ratio is not None else 'N/A'} |")
        if (
            metrics.annualized_return_pct is not None
            and metrics.max_drawdown_pct is not None
            and metrics.max_drawdown_pct != 0
        ):
            calmar = round(metrics.annualized_return_pct / abs(metrics.max_drawdown_pct), 2)
            lines.append(f"| Calmar 比率 | {calmar} |")
        lines.append(f"| 平均持仓天数 | {metrics.eval_window_days} |")
        lines.append("")

        # NAV curve image
        image_filename = self._nav_image_filename(metrics)
        image_rel = f"reports/backtest/{image_filename}"
        lines.append("## 净值曲线")
        lines.append("")
        lines.append(f"![净值曲线]({image_rel})")
        lines.append("")

        # Footer
        lines.append("---")
        lines.append(f"*报告由 BacktestReportGenerator 自动生成 | {metrics.report_generated_at}*")
        lines.append("")

        return "\n".join(lines)

    def _nav_image_filename(self, metrics: BacktestPerformanceMetrics) -> str:
        """Generate a filename for the NAV curve image."""
        # Use date-only portion of report_generated_at
        date_part = metrics.report_generated_at.split(" ")[0] if metrics.report_generated_at else "unknown"
        code_part = f"_{metrics.stock_code}" if metrics.stock_code else ""
        return f"nav_curve_{metrics.scope}{code_part}_{date_part}.png"

    def _save_report(self, markdown: str, metrics: BacktestPerformanceMetrics) -> str:
        """Save the Markdown report to the output directory."""
        filename = f"backtest_report_{metrics.scope}"
        if metrics.stock_code:
            filename += f"_{metrics.stock_code}"
        date_part = metrics.report_generated_at.split(" ")[0] if metrics.report_generated_at else "unknown"
        filename += f"_{date_part}.md"

        report_path = self._output_dir / filename
        report_path.write_text(markdown, encoding="utf-8")
        return str(report_path)

    def _save_nav_curve(self, metrics: BacktestPerformanceMetrics) -> str | None:
        """Generate and save the NAV curve chart using matplotlib.

        Returns:
            Path to the saved image, or None if matplotlib is unavailable or no data.
        """
        if not MATPLOTLIB_AVAILABLE:
            logger.warning("matplotlib not available; skipping NAV curve image generation.")
            return None

        nav_points = metrics.nav_points
        if not nav_points or len(nav_points) < 2:
            logger.warning("Not enough data points for NAV curve.")
            return None

        try:
            matplotlib.use("Agg")  # Non-interactive backend
            fig, ax = plt.subplots(figsize=(12, 6))

            dates = [p["date"] for p in nav_points]
            nav_values = [p["nav"] for p in nav_points]
            cumulative_returns = [p["cumulative_return_pct"] for p in nav_points]

            # X-axis: use index for string dates, parse where possible
            x_indices = list(range(len(nav_points)))

            # Plot NAV
            color_nav = "#2196F3"
            color_ret = "#4CAF50"
            ax.plot(x_indices, nav_values, color=color_nav, linewidth=2, label="NAV")
            ax.set_xlabel("Trade Sequence", fontsize=12)
            ax.set_ylabel("NAV (Capital)", fontsize=12, color=color_nav)
            ax.tick_params(axis="y", labelcolor=color_nav)
            ax.set_title(
                f"NAV Curve - {metrics.strategy_name}" + (f" ({metrics.stock_code})" if metrics.stock_code else ""),
                fontsize=14,
                fontweight="bold",
            )
            ax.grid(True, alpha=0.3)

            # Overlay cumulative return on secondary Y-axis
            ax2 = ax.twinx()
            ax2.plot(
                x_indices,
                cumulative_returns,
                color=color_ret,
                linewidth=1.5,
                linestyle="--",
                alpha=0.7,
                label="Cumulative Return %",
            )
            ax2.set_ylabel("Cumulative Return (%)", fontsize=12, color=color_ret)
            ax2.tick_params(axis="y", labelcolor=color_ret)

            # Annotate final values
            if nav_values:
                final_nav = nav_values[-1]
                final_ret = cumulative_returns[-1]
                ax.annotate(
                    f"{final_nav:,.0f}",
                    xy=(x_indices[-1], final_nav),
                    xytext=(5, 5),
                    textcoords="offset points",
                    fontsize=10,
                    color=color_nav,
                )
                ax2.annotate(
                    f"{final_ret:+.2f}%",
                    xy=(x_indices[-1], final_ret),
                    xytext=(5, -10),
                    textcoords="offset points",
                    fontsize=10,
                    color=color_ret,
                )

            # Combined legend
            lines1, labels1 = ax.get_legend_handles_labels()
            lines2, labels2 = ax2.get_legend_handles_labels()
            ax.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

            fig.tight_layout()

            output_path = self._output_dir / self._nav_image_filename(metrics)
            fig.savefig(output_path, dpi=150, bbox_inches="tight")
            plt.close(fig)

            logger.info("NAV curve saved to %s", output_path)
            return str(output_path)

        except Exception as exc:
            logger.warning("Failed to generate NAV curve chart: %s", exc)
            try:
                plt.close("all")
            except Exception:
                pass
            return None

    def generate_report_content(
        self,
        summary: dict[str, Any],
        results: list[dict[str, Any]] | None = None,
        *,
        strategy_name: str = "Overall",
        stock_code: str | None = None,
    ) -> str:
        """Generate the Markdown report content without saving.

        Useful for notification systems that need the text directly.
        """
        metrics = self._compute_metrics(
            summary=summary,
            results=results,
            strategy_name=strategy_name,
            stock_code=stock_code,
            initial_capital=100000.0,
        )
        return self._render_markdown(metrics)
