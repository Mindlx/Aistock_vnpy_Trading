"""
数据仓库预热脚本 — 每日定时刷新15类数据缓存

用法:
    python scripts/warmup_warehouse.py                    # 全量预热
    python scripts/warmup_warehouse.py --type daily       # 仅日K线
    python scripts/warmup_warehouse.py --type global      # 仅美股/港股
    python scripts/warmup_warehouse.py --codes 601801,000592  # 指定股票
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DATA_TYPE_GROUPS = {
    "daily":       ["daily_ohlcv"],
    "financial":   ["financial_indicators", "fundamentals", "financial_statements"],
    "capital":     ["capital_flows", "block_trade", "shareholder"],
    "news":        ["news_events"],
    "chip":        ["chip_distribution"],
    "index":       ["index_ohlcv"],
    "global":      ["global_ohlcv", "global_fundamentals"],
    "all":         ["daily_ohlcv", "financial_indicators", "capital_flows",
                    "news_events", "fundamentals", "chip_distribution",
                    "financial_statements", "shareholder", "block_trade",
                    "index_ohlcv", "global_ohlcv", "global_fundamentals"],
}


def main():
    parser = argparse.ArgumentParser(description="数据仓库预热")
    parser.add_argument("--type", choices=list(DATA_TYPE_GROUPS.keys()), default="all")
    parser.add_argument("--codes", type=str, default=None, help="股票代码,逗号分隔")
    parser.add_argument("--force", action="store_true", help="强制刷新(跳过缓存检查)")
    parser.add_argument("--full-market", action="store_true", help="全A股历史数据回填(首次/断点续传)")
    parser.add_argument("--days", type=int, default=365, help="回填天数(默认365)")
    parser.add_argument("--workers", type=int, default=8, help="并发线程数(默认8)")
    args = parser.parse_args()

    from services.data_warehouse.warehouse import WarehouseReader
    wr = WarehouseReader()

    if args.full_market:
        logger.info("全市场回填开始 — %d 天, %d 线程", args.days, args.workers)
        t0 = datetime.now()
        result = wr.prefetch_full_market(days=args.days, max_workers=args.workers)
        elapsed = (datetime.now() - t0).total_seconds()
        logger.info("全市场回填完成 — 成功 %d / 失败 %d / 跳过 %d / 耗时 %.0fs",
                     result.get("success", 0), result.get("failed", 0),
                     result.get("skipped", 0), elapsed)
        return

    dtypes = DATA_TYPE_GROUPS[args.type]
    codes = args.codes.split(",") if args.codes else None

    t0 = datetime.now()
    logger.info("仓库预热开始 — %s (%d 数据种类)", args.type, len(dtypes))

    result = wr.prefetch_all(codes=codes, data_types=dtypes, force=args.force)

    total_ok = sum(
        1 for v in result.values() for c in v.values() if isinstance(c, int) and c >= 0
    )
    total_fail = sum(
        1 for v in result.values() for c in v.values() if isinstance(c, int) and c == 0
    )
    elapsed = (datetime.now() - t0).total_seconds()
    logger.info("仓库预热完成 — 成功 %d / 失败 %d / 耗时 %.0fs", total_ok, total_fail, elapsed)


if __name__ == "__main__":
    main()
