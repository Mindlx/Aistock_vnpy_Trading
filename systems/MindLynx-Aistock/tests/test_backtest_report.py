"""Unit tests for BacktestReportGenerator — pure logic tests (no project deps needed)."""

import tempfile
import unittest

# Import static methods directly from the source module
# The non-init-level imports avoid triggering full project config deps
from src.core.backtest_report import (
    BacktestReportGenerator,
)


class TestBacktestReportPureLogic(unittest.TestCase):
    """Test the static/math methods without needing project deps."""

    # --- compute_stock_return_pct ---

    def test_stock_return_normal(self):
        r = BacktestReportGenerator.compute_stock_return_pct([100, 110, 120])
        self.assertAlmostEqual(r, 20.0)

    def test_stock_return_negative(self):
        r = BacktestReportGenerator.compute_stock_return_pct([100, 90, 80])
        self.assertAlmostEqual(r, -20.0)

    def test_stock_return_empty(self):
        self.assertIsNone(BacktestReportGenerator.compute_stock_return_pct([]))

    def test_stock_return_single(self):
        self.assertIsNone(BacktestReportGenerator.compute_stock_return_pct([100]))

    def test_stock_return_zero_start(self):
        self.assertIsNone(BacktestReportGenerator.compute_stock_return_pct([0, 100]))

    # --- compute_annualized_return ---

    def test_annualized_one_year(self):
        r = BacktestReportGenerator.compute_annualized_return(20.0, 252)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r, 20.0, delta=0.5)

    def test_annualized_two_years(self):
        r = BacktestReportGenerator.compute_annualized_return(21.0, 504)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r, 10.0, delta=0.5)

    def test_annualized_zero_days(self):
        self.assertIsNone(BacktestReportGenerator.compute_annualized_return(20.0, 0))

    def test_annualized_negative_days(self):
        self.assertIsNone(BacktestReportGenerator.compute_annualized_return(20.0, -1))

    def test_annualized_none_return(self):
        self.assertIsNone(BacktestReportGenerator.compute_annualized_return(None, 252))

    # --- compute_sharpe_ratio ---

    def test_sharpe_positive_returns(self):
        returns = [0.1, 0.2, 0.15, 0.3, 0.05]
        sharpe = BacktestReportGenerator.compute_sharpe_ratio(returns, risk_free_rate=0.02)
        self.assertIsNotNone(sharpe)
        self.assertGreater(sharpe, 0.0)

    def test_sharpe_negative_returns(self):
        returns = [-0.1, -0.2, -0.15]
        sharpe = BacktestReportGenerator.compute_sharpe_ratio(returns, risk_free_rate=0.02)
        self.assertIsNotNone(sharpe)
        self.assertLess(sharpe, 0.0)

    def test_sharpe_empty(self):
        self.assertIsNone(BacktestReportGenerator.compute_sharpe_ratio([]))

    def test_sharpe_too_few(self):
        self.assertIsNone(BacktestReportGenerator.compute_sharpe_ratio([0.1]))

    def test_sharpe_zero_variance(self):
        self.assertIsNone(BacktestReportGenerator.compute_sharpe_ratio([0.02, 0.02, 0.02], risk_free_rate=0.02))

    # --- compute_max_drawdown ---

    def test_max_drawdown_normal(self):
        nav = [100.0, 110.0, 105.0, 95.0, 102.0, 108.0]
        dd = BacktestReportGenerator.compute_max_drawdown(nav)
        self.assertIsNotNone(dd)
        # peak=110, trough=95, dd=(95-110)/110=-13.64%
        self.assertAlmostEqual(dd, -13.64, delta=0.1)

    def test_max_drawdown_monotonic_up(self):
        dd = BacktestReportGenerator.compute_max_drawdown([100.0, 110.0, 120.0, 130.0])
        self.assertIsNotNone(dd)
        self.assertAlmostEqual(dd, 0.0)

    def test_max_drawdown_monotonic_down(self):
        dd = BacktestReportGenerator.compute_max_drawdown([130.0, 120.0, 110.0, 100.0])
        self.assertIsNotNone(dd)
        # peak=130, trough=100, dd=(100-130)/130=-23.08%
        self.assertAlmostEqual(dd, -23.08, delta=0.1)

    def test_max_drawdown_empty(self):
        self.assertIsNone(BacktestReportGenerator.compute_max_drawdown([]))

    def test_max_drawdown_single(self):
        self.assertIsNone(BacktestReportGenerator.compute_max_drawdown([100.0]))

    # --- compute_profit_loss_ratio ---

    def test_pl_ratio_normal(self):
        pl = BacktestReportGenerator.compute_profit_loss_ratio(
            win_count=10,
            loss_count=5,
            total_win_return=30.0,
            total_loss_return=-10.0,
        )
        self.assertIsNotNone(pl)
        self.assertAlmostEqual(pl, 1.5)

    def test_pl_ratio_no_wins(self):
        self.assertIsNone(BacktestReportGenerator.compute_profit_loss_ratio(0, 5, 0, -10))

    def test_pl_ratio_no_losses(self):
        self.assertIsNone(BacktestReportGenerator.compute_profit_loss_ratio(10, 0, 30, 0))

    def test_pl_ratio_zero_avg_loss(self):
        self.assertIsNone(BacktestReportGenerator.compute_profit_loss_ratio(10, 5, 30, 0))

    def test_pl_ratio_all_zeros(self):
        self.assertIsNone(BacktestReportGenerator.compute_profit_loss_ratio(0, 0, 0, 0))

    # --- Edge case: None inputs ---

    def test_all_methods_none_input(self):
        self.assertIsNone(BacktestReportGenerator.compute_stock_return_pct(None))  # type: ignore
        self.assertIsNone(BacktestReportGenerator.compute_annualized_return(10.0, None))  # type: ignore
        self.assertIsNone(BacktestReportGenerator.compute_sharpe_ratio(None))  # type: ignore
        self.assertIsNone(BacktestReportGenerator.compute_max_drawdown(None))  # type: ignore


class TestBacktestReportWithMocks(unittest.TestCase):
    """Test generate_report_content using a controlled environment."""

    def setUp(self):
        self.gen = BacktestReportGenerator(output_dir=tempfile.mkdtemp())

    def _sample_summary(self, **overrides) -> dict:
        data = {
            "scope": "overall",
            "code": None,
            "eval_window_days": 10,
            "engine_version": "v1",
            "total_evaluations": 50,
            "completed_count": 45,
            "insufficient_count": 5,
            "long_count": 30,
            "cash_count": 15,
            "win_count": 28,
            "loss_count": 12,
            "neutral_count": 5,
            "win_rate_pct": 70.0,
            "neutral_rate_pct": 11.11,
            "direction_accuracy_pct": 65.0,
            "avg_stock_return_pct": 2.5,
            "avg_simulated_return_pct": 3.2,
            "stop_loss_trigger_rate": 15.0,
            "take_profit_trigger_rate": 25.0,
            "avg_days_to_first_hit": 4.5,
            "analysis_date_from": "2025-01-01",
            "analysis_date_to": "2026-05-19",
        }
        data.update(overrides)
        return data

    def _sample_results(self, count: int = 10) -> list[dict]:
        return [
            {
                "analysis_date": f"2025-0{(i % 9) + 1:02d}-0{(i % 28) + 1:02d}",
                "outcome": "win" if i % 3 != 0 else "loss",
                "simulated_return_pct": 3.0 if i % 3 != 0 else -2.0,
                "position_recommendation": "long",
                "direction_correct": i % 3 != 0,
            }
            for i in range(count)
        ]

    def test_report_content_includes_all_sections(self):
        """Verify the markdown output contains expected sections."""
        summary = self._sample_summary()
        content = self.gen.generate_report_content(summary, strategy_name="UnitTest")
        self.assertIn("回测报告 - UnitTest", content)
        self.assertIn("## 基本参数", content)
        self.assertIn("## 绩效指标", content)
        self.assertIn("## 交易分析", content)
        self.assertIn("## 风险分析", content)
        self.assertIn("## 净值曲线", content)
        self.assertIn("总收益率", content)
        self.assertIn("夏普比率", content)
        self.assertIn("最大回撤", content)
        self.assertIn("胜率", content)
        self.assertIn("盈亏比", content)

    def test_report_content_empty_results(self):
        """Should handle empty result lists gracefully."""
        summary = self._sample_summary()
        content = self.gen.generate_report_content(summary, results=[], strategy_name="Empty")
        self.assertIn("Empty", content)

    def test_report_content_disabled(self):
        """When generator is disabled, do not produce content."""
        self.gen._enabled = False
        result = self.gen.generate(None)
        self.assertIsNone(result)

    def test_report_with_results_includes_nav(self):
        """Results should trigger NAV curve section."""
        summary = self._sample_summary()
        results = self._sample_results(5)
        content = self.gen.generate_report_content(summary, results, strategy_name="NAVTest")
        self.assertIn("净值曲线", content)

    def test_report_file_naming(self):
        """Check naming conventions for report files."""
        summary = self._sample_summary()
        path = self.gen.generate(summary, strategy_name="NamingTest")
        self.assertIsNotNone(path)
        self.assertIn("backtest_report_overall", path)

    def test_report_file_naming_stock(self):
        """Per-stock reports should include stock code in filename."""
        summary = self._sample_summary(scope="stock", code="600519")
        path = self.gen.generate(summary, strategy_name="Stock", stock_code="600519")
        self.assertIsNotNone(path)
        self.assertIn("600519", path)
        self.assertIn("backtest_report_stock", path)

    def test_nav_curve_builds(self):
        """_build_nav_curve should produce ordered points."""
        results = self._sample_results(10)
        points, _ = self.gen._build_nav_curve(results, 100000.0)
        self.assertGreater(len(points), 0)
        # First point should be initial capital
        self.assertEqual(points[0]["nav"], 100000.0)
        self.assertEqual(points[0]["cumulative_return_pct"], 0.0)

    def test_daily_returns_non_empty(self):
        """_build_nav_curve should produce one return per trade."""
        results = self._sample_results(10)
        _, daily_returns = self.gen._build_nav_curve(results, 100000.0)
        self.assertEqual(len(daily_returns), 10)

    def test_no_results_nav_empty(self):
        """_build_nav_curve with no results should return empty lists."""
        points, rets = self.gen._build_nav_curve([], 100000.0)
        self.assertEqual(len(points), 0)
        self.assertEqual(len(rets), 0)

    def test_compute_total_return_with_results(self):
        """_compute_total_return should use NAV from results."""
        summary = self._sample_summary()
        results = self._sample_results(10)
        ret = self.gen._compute_total_return(results, summary, 100000.0)
        self.assertIsNotNone(ret)
        self.assertIsInstance(ret, (float, int))


if __name__ == "__main__":
    unittest.main()
