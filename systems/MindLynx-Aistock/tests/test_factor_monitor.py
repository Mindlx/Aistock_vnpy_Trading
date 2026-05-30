"""Tests for the factor monitor module (IC/IR tracking and visualisation)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

import numpy as np

from src.core.factor_monitor import (
    FACTOR_NAMES,
    FactorMonitor,
    _pearson_correlation,
    _spearman_rank_correlation,
    create_factor_monitor,
)


class TestCorrelationFunctions(unittest.TestCase):
    """Unit tests for rank/pearson correlation helpers."""

    def test_spearman_perfect_positive(self) -> None:
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
        rho = _spearman_rank_correlation(x, y)
        self.assertAlmostEqual(rho, 1.0, places=5)

    def test_spearman_perfect_negative(self) -> None:
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y = np.array([10.0, 8.0, 6.0, 4.0, 2.0])
        rho = _spearman_rank_correlation(x, y)
        self.assertAlmostEqual(rho, -1.0, places=5)

    def test_spearman_no_correlation(self) -> None:
        rng = np.random.default_rng(99)
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        y = rng.normal(0, 1.0, 10)  # independent random noise
        rho = _spearman_rank_correlation(x, y)
        # With n=10, rho should not be extreme; typical |rho| < 0.6 for independent data
        self.assertLess(abs(rho), 0.6)

    def test_spearman_short_input(self) -> None:
        rho = _spearman_rank_correlation(np.array([1.0]), np.array([2.0]))
        self.assertEqual(rho, 0.0)

    def test_pearson_perfect_positive(self) -> None:
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
        r = _pearson_correlation(x, y)
        self.assertAlmostEqual(r, 1.0, places=5)

    def test_pearson_short_input(self) -> None:
        r = _pearson_correlation(np.array([1.0]), np.array([2.0]))
        self.assertEqual(r, 0.0)

    def test_pearson_zero_variance(self) -> None:
        r = _pearson_correlation(np.array([1.0, 1.0, 1.0]), np.array([2.0, 3.0, 4.0]))
        self.assertEqual(r, 0.0)


class TestFactorMonitorBasic(unittest.TestCase):
    """Unit tests for FactorMonitor core functionality."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = self._temp_dir.name
        self.monitor = FactorMonitor(
            output_dir=self.output_dir,
            enabled=True,
            rolling_window=5,
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _make_ic_record(self, date_str: str, n_stocks: int = 50) -> None:
        """Helper: generate synthetic factor values and forward returns, then record IC."""
        rng = np.random.default_rng(42)
        fwd_returns = rng.normal(0.001, 0.02, n_stocks)
        factor_vals: dict[str, np.ndarray] = {}
        for name in FACTOR_NAMES:
            # Slight positive correlation for momentum_spread, noise for others
            if name == "momentum_spread":
                factor_vals[name] = fwd_returns + rng.normal(0, 0.01, n_stocks)
            elif name == "turnover_sentiment":
                factor_vals[name] = -fwd_returns + rng.normal(0, 0.01, n_stocks)
            else:
                factor_vals[name] = rng.normal(0, 1.0, n_stocks)
        self.monitor.record_daily_ic(date_str, factor_vals, fwd_returns)

    def test_record_daily_ic(self) -> None:
        self._make_ic_record("2026-01-05")
        summary = self.monitor.get_ic_summary()
        self.assertIn("date", summary)
        self.assertIn("factors", summary)
        self.assertIn("momentum_spread", summary["factors"])
        self.assertIn("turnover_sentiment", summary["factors"])

    def test_record_daily_ic_disabled(self) -> None:
        monitor = FactorMonitor(output_dir=self.output_dir, enabled=False)
        monitor.record_daily_ic("2026-01-05", {"test": np.array([1.0, 2.0])}, np.array([0.1, 0.2]))
        summary = monitor.get_ic_summary()
        self.assertIn("error", summary)

    def test_multiple_dates(self) -> None:
        for day in range(1, 11):
            date_str = f"2026-01-{day:02d}"
            self._make_ic_record(date_str)
        self.assertEqual(len(self.monitor._daily_ic), 10)

    def test_get_ic_time_series(self) -> None:
        for day in range(1, 6):
            self._make_ic_record(f"2026-01-{day:02d}")
        series = self.monitor.get_ic_time_series("momentum_spread")
        self.assertEqual(len(series), 5)
        for record in series:
            self.assertIn("date", record)
            self.assertIn("spearman_ic", record)
            self.assertIn("pearson_ic", record)

    def test_compute_rolling_ir(self) -> None:
        # Need at least rolling_window + 1 dates for rolling IR
        for day in range(1, 10):
            self._make_ic_record(f"2026-01-{day:02d}")

        # Rolling window = 5, so first valid IR at index 4 (0-indexed)
        ir_data = self.monitor.compute_rolling_ir("momentum_spread")
        self.assertGreaterEqual(len(ir_data), 4)

    def test_get_summary_stats(self) -> None:
        for day in range(1, 11):
            self._make_ic_record(f"2026-01-{day:02d}")
        stats = self.monitor.get_summary_stats()
        self.assertEqual(stats["total_dates"], 10)
        self.assertIn("date_range", stats)
        self.assertIn("factors", stats)
        self.assertIn("momentum_spread", stats["factors"])
        ms = stats["factors"]["momentum_spread"]
        self.assertIn("mean_ic", ms)
        self.assertIn("ir", ms)
        self.assertIn("ic_positive_ratio", ms)

    def test_layered_portfolio_returns(self) -> None:
        rng = np.random.default_rng(42)
        fv = np.array([-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5])
        fwd_ret = np.array([-0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06])
        factor_vals = {"momentum_spread": fv}
        result = self.monitor.compute_layered_portfolio_returns(
            factor_vals,
            fwd_ret,
            "momentum_spread",
        )
        self.assertIn("Q1 (低值)", result)
        self.assertIn("Q5 (高值)", result)
        self.assertIn("long_short_spread", result)
        # With positive factor-return relationship, high group should outperform low
        self.assertGreater(result["long_short_spread"], 0.0)

    def test_save_and_load(self) -> None:
        for day in range(1, 6):
            self._make_ic_record(f"2026-01-{day:02d}")

        saved_path = self.monitor.save_data()
        self.assertIsNotNone(saved_path)
        assert saved_path is not None
        self.assertTrue(os.path.exists(saved_path))

        # Create a new monitor and verify it loads saved data
        monitor2 = FactorMonitor(output_dir=self.output_dir, enabled=True)
        self.assertEqual(len(monitor2._daily_ic), 5)

        # Verify saved JSON structure
        with open(saved_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("metadata", data)
        self.assertIn("daily_ic", data)
        self.assertEqual(data["metadata"]["rolling_window"], 5)

    def test_save_data_disabled(self) -> None:
        monitor = FactorMonitor(output_dir=self.output_dir, enabled=False)
        self.assertIsNone(monitor.save_data())

    def test_get_ic_summary_no_data(self) -> None:
        summary = self.monitor.get_ic_summary()
        self.assertIn("error", summary)

    def test_get_ic_summary_specific_date(self) -> None:
        self._make_ic_record("2026-01-05")
        summary = self.monitor.get_ic_summary("2026-01-05")
        self.assertNotIn("error", summary)
        self.assertEqual(summary["date"], "2026-01-05")

    def test_get_ic_summary_missing_date(self) -> None:
        self._make_ic_record("2026-01-05")
        summary = self.monitor.get_ic_summary("2026-01-99")
        self.assertIn("error", summary)


# ------------------------------------------------------------------
# Helpers (must be defined before any class that references them at decoration time)
# ------------------------------------------------------------------


def _matplotlib_available() -> bool:
    """Check if matplotlib is available."""
    try:
        import matplotlib  # noqa: F401

        return True
    except ImportError:
        return False


@unittest.skipIf(not _matplotlib_available(), "matplotlib not installed")
class TestFactorMonitorCharts(unittest.TestCase):
    """Test chart generation (requires matplotlib)."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = self._temp_dir.name
        self.monitor = FactorMonitor(
            output_dir=self.output_dir,
            enabled=True,
            rolling_window=5,
        )
        rng = np.random.default_rng(42)
        for day in range(1, 15):
            date_str = f"2026-01-{day:02d}"
            n = 50
            fwd = rng.normal(0.001, 0.02, n)
            fv: dict[str, np.ndarray] = {}
            for name in FACTOR_NAMES:
                fv[name] = fwd + rng.normal(0, 0.015, n)
            self.monitor.record_daily_ic(date_str, fv, fwd)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_generate_report(self) -> None:
        output_path = os.path.join(self.output_dir, "test_report.png")
        result = self.monitor.generate_report(output_path=output_path)
        self.assertIsNotNone(result)
        self.assertTrue(os.path.exists(output_path))
        self.assertGreater(os.path.getsize(output_path), 1000)

    def test_generate_report_no_data(self) -> None:
        monitor = FactorMonitor(output_dir=self.output_dir, enabled=True)
        result = monitor.generate_report()
        self.assertIsNone(result)

    def test_generate_cumulative_chart(self) -> None:
        layered = [
            {"Q1 (低值)": -0.02, "Q5 (高值)": 0.03, "long_short_spread": 0.05},
            {"Q1 (低值)": -0.01, "Q5 (高值)": 0.02, "long_short_spread": 0.03},
            {"Q1 (低值)": -0.015, "Q5 (高值)": 0.025, "long_short_spread": 0.04},
        ]
        output_path = os.path.join(self.output_dir, "test_cumulative.png")
        result = self.monitor.generate_cumulative_return_chart(
            "momentum_spread",
            layered_returns=layered,
            output_path=output_path,
        )
        self.assertIsNotNone(result)
        self.assertTrue(os.path.exists(output_path))
        self.assertGreater(os.path.getsize(output_path), 1000)

    def test_cumulative_chart_insufficient_data(self) -> None:
        result = self.monitor.generate_cumulative_return_chart(
            "momentum_spread",
            layered_returns=[{"Q1": 0.01}],
        )
        self.assertIsNone(result)


class TestCreateFactory(unittest.TestCase):
    """Test the create_factor_monitor factory function."""

    def test_create(self) -> None:
        monitor = create_factor_monitor()
        self.assertIsInstance(monitor, FactorMonitor)
        self.assertTrue(monitor.enabled)
        self.assertEqual(monitor.rolling_window, 20)
