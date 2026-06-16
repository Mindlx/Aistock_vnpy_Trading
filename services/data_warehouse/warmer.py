"""DataWarmer — 首次部署数据预热

批量拉取过去 N 年的日K线 + 最新财务/资金流/基本面数据。
预热期间不会触发 API 限流, 通过令牌桶严格节流。

用法:
    python -m services.data_warehouse.warmer
    python -m services.data_warehouse.warmer --codes 600519,000001 --years 2
"""
from __future__ import annotations

import argparse
import logging
import time

from services.data_warehouse.config import DataWarehouseConfig
from services.data_warehouse.warehouse import WarehouseReader

logger = logging.getLogger(__name__)


class DataWarmer:
    """数据预热工具"""

    def __init__(self):
        self._cfg = DataWarehouseConfig.get_instance()
        self._reader = WarehouseReader()

    def warm(self, codes: list[str] | None = None, years: int = 1) -> dict:
        """执行预热

        Args:
            codes: 股票列表 (默认使用配置)
            years: 回溯年数

        Returns:
            {code: {data_type: rows_count}}
        """
        codes = codes or self._cfg.stock_pool
        if not codes:
            logger.warning("[Warmer] 股票列表为空, 跳过预热")
            return {"error": "empty stock list"}

        logger.info("[Warmer] 开始预热 %d 只股票, 回溯 %d 年", len(codes), years)
        start_time = time.time()
        results: dict[str, dict[str, int]] = {}

        for i, code in enumerate(codes):
            results[code] = {}
            logger.info("[Warmer] [%d/%d] %s ...", i + 1, len(codes), code)

            # 1. OHLCV
            try:
                rows = self._reader.get_daily(code, days=years * 365)
                results[code]["daily_ohlcv"] = len(rows) if rows else 0
                logger.info("  OHLCV: %d rows", results[code]["daily_ohlcv"])
            except Exception as exc:
                logger.warning("  OHLCV 失败: %s", exc)
                results[code]["daily_ohlcv"] = -1

            # 2. 财务指标
            try:
                fin = self._reader.get_financial(code)
                results[code]["financial"] = sum(len(v) for v in fin.values()) if fin else 0
                logger.info("  财务指标: %d", results[code]["financial"])
            except Exception as exc:
                logger.warning("  财务指标 失败: %s", exc)
                results[code]["financial"] = -1

            # 3. 资金流向
            try:
                flows = self._reader.get_capital_flows(code, days=30)
                results[code]["capital_flows"] = len(flows) if flows else 0
                logger.info("  资金流向: %d", results[code]["capital_flows"])
            except Exception as exc:
                logger.warning("  资金流向 失败: %s", exc)
                results[code]["capital_flows"] = -1

            # 4. 新闻 (近7天)
            try:
                news = self._reader.get_news(code, days=7, limit=10)
                results[code]["news"] = len(news) if news else 0
            except Exception as exc:
                logger.warning("  新闻 失败: %s", exc)
                results[code]["news"] = -1

            # 5. 基本面
            try:
                fund = self._reader.get_fundamentals(code)
                results[code]["fundamentals"] = 1 if fund else 0
                if fund:
                    logger.info("  基本面: %s | PE=%.1f", fund.get("name", ""),
                               fund.get("pe_ttm", 0))
            except Exception as exc:
                logger.warning("  基本面 失败: %s", exc)
                results[code]["fundamentals"] = -1

            # 跨股票间隔, 保护 API
            if i < len(codes) - 1:
                time.sleep(5)

        elapsed = time.time() - start_time
        logger.info("[Warmer] 预热完成! 耗时 %.1f 秒", elapsed)
        results["_meta"] = {"elapsed_seconds": round(elapsed, 1), "stock_count": len(codes)}

        # 打印汇总
        total_rows = sum(
            v for code_res in results.values()
            if isinstance(code_res, dict)
            for v in code_res.values()
            if isinstance(v, int) and v > 0
        )
        logger.info("[Warmer] 共获取 %d 条数据记录", total_rows)

        return results


def main():
    parser = argparse.ArgumentParser(description="数据仓库预热工具")
    parser.add_argument("--codes", type=str, default="",
                        help="股票代码, 逗号分隔 (默认使用配置)")
    parser.add_argument("--years", type=int, default=1,
                        help="回溯年数 (默认1)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    codes = [c.strip() for c in args.codes.split(",") if c.strip()] if args.codes else None
    warmer = DataWarmer()
    warmer.warm(codes, years=args.years)


if __name__ == "__main__":
    main()
