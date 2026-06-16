"""RefreshScheduler — 数据刷新调度器

两种模式:
  1. 嵌入式: 在现有进程中调用 refresh_all() / refresh_stale()
  2. 独立进程: run_forever() 后台常驻, 按策略矩阵定时刷新
"""
from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import Any

from services.data_warehouse.config import DataWarehouseConfig
from services.data_warehouse.warehouse import WarehouseReader

logger = logging.getLogger(__name__)

# 交易时段 (北京时间)
_MARKET_OPEN = (9, 30)
_MARKET_CLOSE = (15, 0)


def _is_trading_hour() -> bool:
    now = datetime.now(timezone.utc).astimezone()
    h, m = now.hour, now.minute
    if h < 9 or h >= 15:
        return False
    if h == 9 and m < 30:
        return False
    return True


def _is_trading_day() -> bool:
    return datetime.now().isoweekday() <= 5


def _now_cn() -> datetime:
    from datetime import timezone as tz, timedelta
    return datetime.now(tz(timedelta(hours=8)))


class RefreshScheduler:
    """数据刷新调度器"""

    def __init__(self, reader: WarehouseReader | None = None):
        self._cfg = DataWarehouseConfig.get_instance()
        self._reader = reader or WarehouseReader()
        self._stock_codes = self._cfg.stock_pool

    # ═══════════════════════════════════════
    # 按数据类型刷新
    # ═══════════════════════════════════════

    def refresh_daily_ohlcv(self, force: bool = False) -> dict[str, int]:
        """刷新日K线 (建议收盘后15:30执行)"""
        results: dict[str, int] = {}
        from services.data_warehouse.fetchers import DailyFetcher
        fetcher = DailyFetcher()
        for i, code in enumerate(self._stock_codes):
            try:
                if not force and self._reader.is_fresh(code, "daily_ohlcv"):
                    continue
                rows = fetcher.fetch(code, days=365)
                if rows:
                    from services.data_warehouse.storage import DataLake
                    lake = DataLake()
                    cnt = lake.upsert_ohlcv(code, rows)
                    results[code] = cnt
                    logger.info("[Scheduler] OHLCV %s: %d rows", code, cnt)
            except Exception as exc:
                logger.warning("[Scheduler] OHLCV %s 失败: %s", code, exc)
            # 受控延迟, 避免触发东财反爬
            if i < len(self._stock_codes) - 1:
                time.sleep(3 + random.uniform(0, 2))
        return results

    def refresh_realtime(self, force: bool = False) -> int:
        """刷新实时行情 (盘中每5min)"""
        if not _is_trading_day() or not _is_trading_hour():
            return 0
        try:
            self._reader._refresh_realtime_batch()
            return len(self._stock_codes)
        except Exception as exc:
            logger.warning("[Scheduler] 实时行情刷新失败: %s", exc)
            return 0

    def refresh_financial(self, force: bool = False) -> dict[str, int]:
        """刷新财务指标 (建议收盘后16:00)"""
        results: dict[str, int] = {}
        from services.data_warehouse.fetchers import FinancialFetcher
        fetcher = FinancialFetcher()
        for code in self._stock_codes:
            try:
                if not force and self._reader.is_fresh(code, "financial_indicators"):
                    continue
                data = fetcher.fetch(code)
                if data:
                    from services.data_warehouse.storage import DataLake
                    lake = DataLake()
                    for period, indicators in data.items():
                        lake.upsert_financial(code, period, indicators)
                    results[code] = sum(len(v) for v in data.values())
            except Exception as exc:
                logger.warning("[Scheduler] 财务 %s 失败: %s", code, exc)
            time.sleep(5 + random.uniform(0, 3))
        return results

    def refresh_capital_flows(self, force: bool = False) -> dict[str, int]:
        """刷新资金流向 (建议收盘后16:30)"""
        results: dict[str, int] = {}
        from services.data_warehouse.fetchers import CapitalFlowFetcher
        fetcher = CapitalFlowFetcher()
        for code in self._stock_codes:
            try:
                if not force and self._reader.is_fresh(code, "capital_flows"):
                    continue
                rows = fetcher.fetch(code, days=30)
                if rows:
                    from services.data_warehouse.storage import DataLake
                    lake = DataLake()
                    cnt = lake.upsert_capital_flows(code, rows)
                    results[code] = cnt
            except Exception as exc:
                logger.warning("[Scheduler] 资金流 %s 失败: %s", code, exc)
            time.sleep(3 + random.uniform(0, 2))
        return results

    def refresh_news(self, force: bool = False) -> dict[str, int]:
        """刷新新闻 (每小时)"""
        results: dict[str, int] = {}
        from services.data_warehouse.fetchers import NewsFetcher
        fetcher = NewsFetcher()
        for code in self._stock_codes:
            try:
                if not force and self._reader.is_fresh(code, "news_events"):
                    continue
                items = fetcher.fetch(code, days=2)
                if items:
                    from services.data_warehouse.storage import DataLake
                    lake = DataLake()
                    results[code] = lake.insert_news(items)
            except Exception as exc:
                logger.warning("[Scheduler] 新闻 %s 失败: %s", code, exc)
            time.sleep(2 + random.uniform(0, 1))
        return results

    def refresh_fundamentals(self, force: bool = False) -> dict[str, int]:
        """刷新基本面 (每周)"""
        results: dict[str, int] = {}
        from services.data_warehouse.fetchers import FundamentalsFetcher
        fetcher = FundamentalsFetcher()
        for code in self._stock_codes:
            try:
                if not force and self._reader.is_fresh(code, "fundamentals"):
                    continue
                data = fetcher.fetch(code)
                if data:
                    from services.data_warehouse.storage import DataLake
                    lake = DataLake()
                    lake.upsert_fundamentals(code, data)
                    results[code] = 1
            except Exception as exc:
                logger.warning("[Scheduler] 基本面 %s 失败: %s", code, exc)
            time.sleep(5 + random.uniform(0, 3))
        return results

    # ═══════════════════════════════════════
    # 批量操作
    # ═══════════════════════════════════════

    def refresh_all(self, force: bool = False) -> dict[str, Any]:
        """全量刷新所有数据类型"""
        results: dict[str, Any] = {}
        results["daily_ohlcv"] = self.refresh_daily_ohlcv(force)
        results["realtime"] = self.refresh_realtime(force)
        results["financial"] = self.refresh_financial(force)
        results["capital_flows"] = self.refresh_capital_flows(force)
        results["news"] = self.refresh_news(force)
        results["fundamentals"] = self.refresh_fundamentals(force)
        return results

    def refresh_stale(self) -> dict[str, int]:
        """仅刷新过期数据 (被各子系统自动调用)"""
        refreshed = 0
        for code in self._stock_codes:
            for dtype in ["daily_ohlcv", "financial_indicators",
                          "capital_flows", "fundamentals"]:
                if not self._reader.is_fresh(code, dtype):
                    logger.info("[Scheduler] %s %s 过期, 触发刷新", code, dtype)
                    # 按 type 调用对应刷新方法
                    if dtype == "daily_ohlcv":
                        r = self.refresh_daily_ohlcv(force=True)
                        refreshed += sum(r.values())
                    elif dtype == "financial_indicators":
                        r = self.refresh_financial(force=True)
                        refreshed += sum(r.values())
                    elif dtype == "capital_flows":
                        r = self.refresh_capital_flows(force=True)
                        refreshed += sum(r.values())
                    elif dtype == "fundamentals":
                        r = self.refresh_fundamentals(force=True)
                        refreshed += sum(r.values())
        return {"refreshed": refreshed}

    # ═══════════════════════════════════════
    # 独立进程循环
    # ═══════════════════════════════════════

    def run_forever(self) -> None:
        """独立进程模式: 循环执行调度计划"""
        logger.info("[Scheduler] 启动独立进程模式")
        last_daily = 0
        last_financial = 0
        last_capital = 0
        last_news = 0
        last_fundamentals = 0
        last_purge = 0

        while True:
            try:
                now = time.time()
                now_cn = _now_cn()
                hour = now_cn.hour
                minute = now_cn.minute
                weekday = now_cn.isoweekday()

                # 15:30 后: 刷新日K
                if (hour > 15 or (hour == 15 and minute >= 30)) and now - last_daily > 3600:
                    if weekday <= 5:
                        logger.info("[Scheduler] 触发: 日K刷新")
                        self.refresh_daily_ohlcv()
                        last_daily = now

                # 16:00 后: 刷新财务
                if hour >= 16 and now - last_financial > 3600:
                    if weekday <= 5:
                        logger.info("[Scheduler] 触发: 财务指标刷新")
                        self.refresh_financial()
                        last_financial = now

                # 16:30 后: 刷新资金流
                if (hour > 16 or (hour == 16 and minute >= 30)) and now - last_capital > 3600:
                    if weekday <= 5:
                        logger.info("[Scheduler] 触发: 资金流刷新")
                        self.refresh_capital_flows()
                        last_capital = now

                # 每小时: 刷新新闻
                if minute == 5 and now - last_news > 3300:
                    logger.info("[Scheduler] 触发: 新闻刷新")
                    self.refresh_news()
                    last_news = now

                # 周一 09:00: 刷新基本面
                if weekday == 1 and hour == 9 and 0 <= minute < 5 and now - last_fundamentals > 600000:
                    logger.info("[Scheduler] 触发: 基本面刷新")
                    self.refresh_fundamentals()
                    last_fundamentals = now

                # 每日 03:00: 清理过期数据
                if hour == 3 and 0 <= minute < 5 and now - last_purge > 82000:
                    from services.data_warehouse.storage import DataLake
                    lake = DataLake()
                    purged = lake.purge_expired()
                    logger.info("[Scheduler] 过期清理: %s", purged)
                    last_purge = now

                # 盘中 5 分钟: 刷新实时行情
                if _is_trading_hour() and weekday <= 5:
                    if int(minute) % 5 == 0 and now - last_daily > 240:
                        self.refresh_realtime()

            except Exception as exc:
                logger.exception("[Scheduler] 循环异常: %s", exc)

            time.sleep(60)  # 每分钟检查一次


def run_scheduler_daemon():
    """CLI 入口: python -m services.data_warehouse.scheduler"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    reader = WarehouseReader()
    scheduler = RefreshScheduler(reader)
    logger.info("[Scheduler] 数据仓库调度器启动")
    scheduler.run_forever()


def main():
    """python -m services.data_warehouse.scheduler [--daemon] [--oneshot]"""
    import argparse
    parser = argparse.ArgumentParser(description="数据仓库调度器")
    parser.add_argument("--daemon", action="store_true", default=True,
                        help="守护进程模式 (默认)")
    parser.add_argument("--oneshot", action="store_true",
                        help="单次执行全量刷新后退出")
    args = parser.parse_args()

    if args.oneshot:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        reader = WarehouseReader()
        scheduler = RefreshScheduler(reader)
        logger.info("[Scheduler] 单次全量刷新...")
        result = scheduler.refresh_all(force=True)
        for dtype, data in result.items():
            if isinstance(data, dict):
                logger.info("  %s: %s", dtype, {k: v for k, v in list(data.items())[:3]})
            else:
                logger.info("  %s: %s", dtype, data)
        logger.info("[Scheduler] 单次刷新完成")
    else:
        run_scheduler_daemon()


if __name__ == "__main__":
    main()
