from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.trading_calendar import is_trading_day, should_skip_run, should_skip_warmup


class TestIsTradingDay:
    def test_normal_weekday_is_trading_day(self):
        assert is_trading_day("2026-09-30") is True

    def test_national_day_holiday_is_not_trading_day(self):
        assert is_trading_day("2026-10-01") is False

    def test_weekend_is_not_trading_day(self):
        assert is_trading_day("2026-10-03") is False

    def test_post_holiday_is_trading_day(self):
        assert is_trading_day("2026-10-08") is True

    def test_invalid_date_fails_open(self):
        assert is_trading_day("not-a-date") is True

    def test_unknown_market_fails_open(self):
        assert is_trading_day("2026-10-01", market="xx") is True

    def test_accepts_datetime_like_prefix(self):
        assert is_trading_day("2026-10-01 15:00:00") is False


class TestShouldSkipRun:
    def test_skips_on_holiday_without_force(self):
        assert should_skip_run("2026-10-01") is True

    def test_runs_on_trading_day(self):
        assert should_skip_run("2026-09-30") is False

    def test_force_run_overrides_holiday(self):
        assert should_skip_run("2026-10-01", force_run=True) is False


class TestShouldSkipWarmup:
    def test_skips_on_holiday(self):
        assert should_skip_warmup("2026-10-01") is True

    def test_runs_on_trading_day(self):
        assert should_skip_warmup("2026-09-30") is False

    def test_full_market_backfill_bypasses_guard(self):
        assert should_skip_warmup("2026-10-01", full_market=True) is False

    def test_force_run_overrides_holiday(self):
        assert should_skip_warmup("2026-10-01", force_run=True) is False
