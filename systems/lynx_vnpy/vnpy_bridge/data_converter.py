#!/usr/bin/env python3
"""
数据桥接：pandas DataFrame → vnpy BarData → AlphaLab Parquet

用法:
    python systems/lynx_vnpy/vnpy_bridge/data_converter.py          # 从stock_daily DB读取
    python systems/lynx_vnpy/vnpy_bridge/data_converter.py --all    # 全部历史数据
    python systems/lynx_vnpy/vnpy_bridge/data_converter.py --days 60 # 最近N天
"""

import argparse
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent.parent  # systems/lynx_vnpy/vnpy_bridge → project root
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_SCRIPT_DIR.parent.parent / "lynx_vnpy"))  # systems/lynx_vnpy/lynx_vnpy

from lynx_vnpy.trader.object import BarData
from lynx_vnpy.trader.constant import Exchange, Interval
from lynx_vnpy.alpha.lab import AlphaLab

logger = logging.getLogger(__name__)

PROJECT_ROOT = _PROJECT_ROOT
ML_DB = PROJECT_ROOT / "systems/MindLynx-Aistock/data/stock_analysis.db"
LAB_PATH = PROJECT_ROOT / "data/vnpy_lab"

# 代码→交易所映射
EXCHANGE_MAP: dict[str, Exchange] = {}
# 自动检测: sh→SSE, sz→SZSE
for prefix_group, exch in [
    (("6", "5", "9", "7"), Exchange.SSE),
    (("0", "3", "2"), Exchange.SZSE),
]:
    for p in prefix_group:
        EXCHANGE_MAP[p] = exch


def _detect_exchange(code: str) -> Exchange:
    """根据A股代码前缀判断交易所"""
    for prefix, exch in EXCHANGE_MAP.items():
        if code.startswith(prefix):
            return exch
    return Exchange.SZSE  # default


def load_from_db(
    max_days: int | None = None,
) -> pd.DataFrame:
    """从stock_daily DB加载数据

    Args:
        max_days: 限制每只股票最近N天数据，None=全部

    Returns:
        DataFrame with columns [code, date, open, high, low, close, volume, amount]
    """
    if not ML_DB.exists():
        raise FileNotFoundError(f"stock_daily DB not found: {ML_DB}")

    conn = sqlite3.connect(str(ML_DB))
    df = pd.read_sql(
        "SELECT code, date, open, high, low, close, volume, amount "
        "FROM stock_daily ORDER BY code, date",
        conn,
    )
    conn.close()

    if max_days:
        df = df.groupby("code").tail(max_days).reset_index(drop=True)

    logger.info("Loaded %d rows, %d stocks from DB", len(df), df["code"].nunique())
    return df


def df_to_bars(df: pd.DataFrame) -> list[BarData]:
    """pandas DataFrame → vnpy BarData 列表"""
    bars: list[BarData] = []
    for _, row in df.iterrows():
        code = str(row["code"])
        bars.append(
            BarData(
                symbol=code,
                exchange=_detect_exchange(code),
                datetime=datetime.strptime(str(row["date"]), "%Y-%m-%d"),
                interval=Interval.DAILY,
                open_price=float(row["open"]),
                high_price=float(row["high"]),
                low_price=float(row["low"]),
                close_price=float(row["close"]),
                volume=float(row.get("volume", 0)),
                turnover=float(row.get("amount", 0)),
                gateway_name="SINA",
            )
        )
    return bars


def save_to_lab(bars: list[BarData], lab_path: str | Path = LAB_PATH) -> AlphaLab:
    """保存BarData到AlphaLab Parquet缓存

    Args:
        bars: BarData列表
        lab_path: AlphaLab数据目录

    Returns:
        AlphaLab实例（可用于后续研究）
    """
    lab = AlphaLab(str(lab_path))

    # 按股票分组保存（vnpy的save_bar_data只支持单股票）
    stock_groups: dict[str, list[BarData]] = {}
    for b in bars:
        stock_groups.setdefault(b.vt_symbol, []).append(b)

    for vt_symbol, symbol_bars in stock_groups.items():
        lab.save_bar_data(symbol_bars)

    n_stocks = len(stock_groups)
    n_bars = len(bars)
    logger.info("Saved %d bars (%d stocks) to %s", n_bars, n_stocks, lab_path)
    for code in sorted(stock_groups):
        logger.info("  %s: %d bars", code, len(stock_groups[code]))

    return lab


def main() -> None:
    parser = argparse.ArgumentParser(description="vnpy AlphaLab数据桥接")
    parser.add_argument("--all", action="store_true", help="全部历史数据")
    parser.add_argument("--days", type=int, default=120, help="每只股票最近N天(默认120)")
    parser.add_argument("--db", type=str, default=str(ML_DB), help="stock_daily DB路径")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    max_days = None if args.all else args.days
    df = load_from_db(max_days=max_days)
    bars = df_to_bars(df)
    lab = save_to_lab(bars)

    # 验证
    parquet_files = list(Path(LAB_PATH / "daily").glob("*.parquet"))
    total_size = sum(f.stat().st_size for f in parquet_files)
    print(f"\n✅ 数据桥接完成")
    print(f"   股票数: {len(parquet_files)}")
    print(f"   总条数: {len(bars)}")
    print(f"   总大小: {total_size / 1024:.0f} KB")
    print(f"   AlphaLab: {LAB_PATH}")
    print(f"\n下一步: python systems/lynx_vnpy/vnpy_bridge/run_alpha_pipeline.py")


if __name__ == "__main__":
    main()
