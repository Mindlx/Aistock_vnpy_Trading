#!/usr/bin/env python3
"""systemd ExecCondition 法定节假日门禁。

与 ``trading_day_gate.py`` 的区别:
    - ``trading_day_gate``: 非交易日即跳过  → 用于"工作日/市场"任务。
    - ``holiday_gate``: 仅"法定节假日(含与之相连的周末)"跳过, **普通周末照常运行**
      → 用于周日/月度回测类任务 (它们在非交易日运行是设计行为, 但节假日应停)。

判定: 今日 CN 休市 且 今日前后 ±3 天窗口内存在"休市的工作日" → 法定节假日。
    - 普通周末: 前后工作日都开市 → 非节假日 → 放行。
    - 国庆/春节/中秋等: 附近存在休市工作日 → 跳过。

用法（手工排障时强制放行）::

    FORCE_TRADING_DAY=1 python3 scripts/holiday_gate.py

退出码:
    0 — 非节假日（或强制），继续启动
    1 — 法定节假日，systemd 优雅跳过（不计为失败）
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading_calendar import is_trading_day

HOLIDAY_WINDOW_DAYS = 3


def is_holiday_period(check_date: date) -> bool:
    """``check_date`` 是否属法定节假日（含与节假日相连的周末）。

    Fail-open: 日历不可用/异常时返回 False（视为非节假日，放行）。
    """
    try:
        if is_trading_day(check_date.isoformat(), market="cn"):
            return False
        for off in range(-HOLIDAY_WINDOW_DAYS, HOLIDAY_WINDOW_DAYS + 1):
            dd = check_date + timedelta(days=off)
            if dd.weekday() < 5 and not is_trading_day(dd.isoformat(), market="cn"):
                return True
    except Exception:
        return False
    return False


def main() -> int:
    if os.environ.get("FORCE_TRADING_DAY", "").strip() in ("1", "true", "yes"):
        print("[holiday_gate] FORCE_TRADING_DAY=1，强制放行", file=sys.stderr)
        return 0

    tz_cn = timezone(timedelta(hours=8))
    today = datetime.now(tz_cn).date()
    if is_holiday_period(today):
        print(f"[holiday_gate] {today.isoformat()} 属法定节假日，跳过本次运行", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
