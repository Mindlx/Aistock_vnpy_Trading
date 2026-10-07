"""交易日门禁测试：systemd ExecCondition 脚本 + 各 daemon 的节假日感知。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import trading_day_gate as gate
import holiday_gate as hgate


class TestTradingDayGate:
    def test_passes_on_trading_day(self, monkeypatch):
        monkeypatch.setattr(gate, "is_trading_day", lambda *a, **k: True)
        monkeypatch.delenv("FORCE_TRADING_DAY", raising=False)
        assert gate.main() == 0

    def test_skips_on_non_trading_day(self, monkeypatch):
        monkeypatch.setattr(gate, "is_trading_day", lambda *a, **k: False)
        monkeypatch.delenv("FORCE_TRADING_DAY", raising=False)
        assert gate.main() == 1

    def test_force_env_bypasses_gate(self, monkeypatch):
        monkeypatch.setattr(gate, "is_trading_day", lambda *a, **k: False)
        monkeypatch.setenv("FORCE_TRADING_DAY", "1")
        assert gate.main() == 0


class TestHolidayGate:
    """holiday_gate: 仅法定节假日跳过, 普通周末放行."""

    @staticmethod
    def _fake_calendar(closed):
        # 未在 closed 集合中的日期视为开市
        return lambda d, market="cn": d not in closed

    def test_normal_sunday_not_holiday(self, monkeypatch):
        from datetime import date
        monkeypatch.setattr(hgate, "is_trading_day", self._fake_calendar({"2026-09-20"}))
        assert hgate.is_holiday_period(date(2026, 9, 20)) is False

    def test_holiday_sunday_is_holiday(self, monkeypatch):
        from datetime import date
        # 10-04 周日, 附近 10-02(五)/10-05(一) 休市 → 属国庆
        monkeypatch.setattr(hgate, "is_trading_day",
                            self._fake_calendar({"2026-10-02", "2026-10-04", "2026-10-05"}))
        assert hgate.is_holiday_period(date(2026, 10, 4)) is True

    def test_weekday_holiday_is_holiday(self, monkeypatch):
        from datetime import date
        monkeypatch.setattr(hgate, "is_trading_day", self._fake_calendar({"2026-10-07"}))
        assert hgate.is_holiday_period(date(2026, 10, 7)) is True

    def test_main_skips_in_holiday_period(self, monkeypatch):
        monkeypatch.setattr(hgate, "is_holiday_period", lambda d: True)
        monkeypatch.delenv("FORCE_TRADING_DAY", raising=False)
        assert hgate.main() == 1

    def test_main_runs_normal_weekend(self, monkeypatch):
        monkeypatch.setattr(hgate, "is_holiday_period", lambda d: False)
        monkeypatch.delenv("FORCE_TRADING_DAY", raising=False)
        assert hgate.main() == 0


class TestRealtimeFusionHolidayAware:
    def test_holiday_weekday_is_not_trading_day(self):
        from src.realtime_fusion import RealtimeFusion

        assert RealtimeFusion._is_trading_day(datetime(2026, 10, 1)) is False

    def test_normal_weekday_is_trading_day(self):
        from src.realtime_fusion import RealtimeFusion

        assert RealtimeFusion._is_trading_day(datetime(2026, 9, 30)) is True


class TestSchedulerHolidayAware:
    def test_holiday_is_not_trading_day(self, monkeypatch):
        from services.data_warehouse import scheduler

        monkeypatch.setattr(scheduler, "_now_cn", lambda: datetime(2026, 10, 1))
        assert scheduler._is_trading_day() is False

    def test_normal_weekday_is_trading_day(self, monkeypatch):
        from services.data_warehouse import scheduler

        monkeypatch.setattr(scheduler, "_now_cn", lambda: datetime(2026, 9, 30))
        assert scheduler._is_trading_day() is True
