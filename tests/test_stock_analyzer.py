from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.stock_analyzer import StockAnalyzer


class TestStockAnalyzer:
    def setup_method(self):
        self.sa = StockAnalyzer()

    def _make_hist(self, close_values, volume_values=None):
        import pandas as pd
        n = len(close_values)
        if volume_values is None:
            volume_values = [1_000_000] * n
        return pd.DataFrame({
            "Close": close_values,
            "High": [c * 1.02 for c in close_values],
            "Low": [c * 0.98 for c in close_values],
            "Volume": volume_values,
        })

    def test_hold_insufficient_data(self):
        hist = self._make_hist([10.0] * 15)
        assert self.sa.technical_rating(hist) == "Hold"

    def test_hold_neutral(self):
        hist = self._make_hist([10.0] * 30)
        assert self.sa.technical_rating(hist) == "Hold"

    def test_buy_strong_uptrend(self):
        vals = list(np.linspace(8.0, 12.0, 30))
        hist = self._make_hist(vals, volume_values=[2_000_000] * 30)
        assert self.sa.technical_rating(hist) in ("Buy", "Overweight")

    def test_sell_strong_downtrend(self):
        vals = list(np.linspace(12.0, 8.0, 30))
        hist = self._make_hist(vals, volume_values=[2_000_000] * 30)
        assert self.sa.technical_rating(hist) in ("Sell", "Underweight")

    def test_bullish_volume_confirmation(self):
        vals = list(np.linspace(10.0, 11.0, 30))
        vols = [1_000_000] * 25 + [3_000_000] * 5
        hist = self._make_hist(vals, volume_values=vols)
        rating = self.sa.technical_rating(hist)
        assert rating in ("Buy", "Overweight", "Hold")

    def test_empty_result(self):
        r = self.sa._empty_result("601801", "test")
        assert r["code"] == "601801"
        assert r["success"] is False
        assert r["rating"] == "Hold"
