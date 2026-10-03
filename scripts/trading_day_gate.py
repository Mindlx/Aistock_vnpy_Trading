#!/usr/bin/env python3
"""systemd ExecCondition 交易日门禁。

被各 ``.service`` 用作 ``ExecCondition=``：A股非交易日（周末/法定节假日/调休）
时退出码 1，systemd 会跳过剩余命令且**不标记单元失败**（官方语义：退出 1-254
= 跳过且不算失败）；交易日退出 0 放行。

用法（手工排障时强制放行）::

    FORCE_TRADING_DAY=1 python3 scripts/trading_day_gate.py

退出码:
    0 — 交易日（或强制），继续启动
    1 — 非交易日，systemd 优雅跳过
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.trading_calendar import is_trading_day


def main() -> int:
    if os.environ.get("FORCE_TRADING_DAY", "").strip() in ("1", "true", "yes"):
        print("[trading_day_gate] FORCE_TRADING_DAY=1，强制放行", file=sys.stderr)
        return 0

    tz_cn = timezone(timedelta(hours=8))
    today = datetime.now(tz_cn).strftime("%Y-%m-%d")
    if is_trading_day(today, market="cn"):
        return 0
    print(f"[trading_day_gate] {today} 非A股交易日（休市），跳过本次运行", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
