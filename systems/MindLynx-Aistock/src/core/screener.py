"""Multi-criteria stock screener.

Screens stocks using technical and fundamental criteria from the local database.
Returns ranked results suitable for WebUI display or CLI output.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ScreenCriteria:
    """User-configurable screening criteria."""
    # Technical
    ma_bullish: bool | None = None           # MA5 > MA10 > MA20
    rsi_oversold: float | None = None         # RSI below this threshold
    volume_ratio_min: float | None = None     # minimum volume ratio (量比)
    volume_ratio_max: float | None = None

    # Factor
    factor_score_min: float | None = None     # minimum composite factor score

    # Limit
    max_results: int = 30


@dataclass
class ScreenResult:
    """Single stock screening result."""
    code: str
    name: str = ""
    close: float = 0.0
    pct_chg: float = 0.0
    ma_bullish: bool = False
    volume_ratio: float = 1.0
    factor_score: float = 0.0
    rank: int = 0
    signals: list[str] = field(default_factory=list)


def screen_stocks(
    db_path: str = "data/stock_analysis.db",
    criteria: ScreenCriteria | None = None,
) -> list[ScreenResult]:
    """Run screening against all stocks in the database.

    Args:
        db_path: path to SQLite database
        criteria: screening criteria (default: MA bullish + positive volume)

    Returns:
        List of ScreenResult matching criteria, ranked by relevance.
    """
    if criteria is None:
        criteria = ScreenCriteria(ma_bullish=True, volume_ratio_min=0.8)

    conn = sqlite3.connect(db_path)
    codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM stock_daily ORDER BY code").fetchall()]

    results: list[ScreenResult] = []

    for code in codes:
        # Get latest 2 days of data
        rows = conn.execute(
            "SELECT date, close, pct_chg, ma5, ma10, ma20, volume_ratio "
            "FROM stock_daily WHERE code=? ORDER BY date DESC LIMIT 2",
            (code,),
        ).fetchall()
        if not rows:
            continue

        today = rows[0]
        close, pct_chg, ma5, ma10, ma20, vol_ratio = (
            today[1], today[2] or 0, today[3] or 0, today[4] or 0, today[5] or 0, today[6] or 1.0,
        )

        ma_bullish = ma5 > ma10 > ma20 > 0

        # Apply filters
        signals: list[str] = []

        if criteria.ma_bullish is not None:
            if criteria.ma_bullish and not ma_bullish:
                continue
            if not criteria.ma_bullish and ma_bullish:
                continue
        if ma_bullish:
            signals.append("多头排列")

        if criteria.volume_ratio_min is not None and vol_ratio < criteria.volume_ratio_min:
            continue
        if criteria.volume_ratio_max is not None and vol_ratio > criteria.volume_ratio_max:
            continue
        if vol_ratio > 1.5:
            signals.append(f"放量({vol_ratio:.1f}x)")
        elif vol_ratio < 0.5:
            signals.append(f"缩量({vol_ratio:.1f}x)")

        # Get stock name from realtime or fallback
        name = code
        try:
            name_rows = conn.execute(
                "SELECT payload FROM fundamental_snapshot WHERE code=? ORDER BY created_at DESC LIMIT 1",
                (code,),
            ).fetchall()
        except Exception:
            name_rows = []

        results.append(ScreenResult(
            code=code, name=name, close=float(close), pct_chg=float(pct_chg),
            ma_bullish=ma_bullish, volume_ratio=float(vol_ratio),
            signals=signals,
        ))

    conn.close()

    # Sort by number of positive signals (more signals = stronger match)
    results.sort(key=lambda r: len(r.signals), reverse=True)

    # Assign ranks
    for i, r in enumerate(results):
        r.rank = i + 1

    return results[:criteria.max_results]


def format_screen_results(results: list[ScreenResult]) -> str:
    """Format screening results as a table."""
    if not results:
        return "无符合条件的股票"

    lines = [
        "| 排名 | 代码 | 价格 | 涨跌 | 多头 | 量比 | 信号 |",
        "|------|------|------|------|------|------|------|",
    ]
    for r in results:
        ma = "✅" if r.ma_bullish else "❌"
        chg = f"{r.pct_chg:+.2f}%"
        lines.append(f"| {r.rank} | {r.code} | {r.close:.2f} | {chg} | {ma} | {r.volume_ratio:.1f} | {', '.join(r.signals)} |")

    return "\n".join(lines)
