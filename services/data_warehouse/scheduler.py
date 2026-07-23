"""RefreshScheduler — 数据刷新调度器

两种模式:
  1. 嵌入式: 在现有进程中调用 refresh_all() / refresh_stale()
  2. 独立进程: run_forever() 后台常驻, 按策略矩阵定时刷新

失败补拉队列:
  每种数据类型的失败股票自动加入重试队列, 间隔递增直到成功。
  重试间隔: 5分钟 → 15分钟 → 30分钟 → 1小时 → 2小时(上限)
"""
from __future__ import annotations

import logging
import random
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from services.data_warehouse.config import DataWarehouseConfig
from services.data_warehouse.warehouse import WarehouseReader

logger = logging.getLogger(__name__)

# 失败补拉重试间隔（秒），递增到上限后保持最大间隔
_FAILED_RETRY_INTERVALS = [300, 900, 1800, 3600, 7200]  # 5min, 15min, 30min, 1h, 2h
_MAX_RETRY_INTERVAL = 7200  # 最大间隔2小时

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
        # 失败补拉队列: {data_type: [(code, next_retry_at, attempt_count), ...]}
        self._failed_queue: dict[str, list[tuple[str, float, int]]] = {}

    # ═══════════════════════════════════════
    # 失败补拉队列管理
    # ═══════════════════════════════════════

    def _enqueue_failed(self, dtype: str, code: str, attempt: int = 0):
        """加入失败补拉队列, 计算下次重试时间"""
        now = time.time()
        interval = _FAILED_RETRY_INTERVALS[attempt] if attempt < len(_FAILED_RETRY_INTERVALS) else _MAX_RETRY_INTERVAL
        next_retry = now + interval
        if dtype not in self._failed_queue:
            self._failed_queue[dtype] = []
        # 去重: 先移除旧记录
        self._failed_queue[dtype] = [(c, t, a) for c, t, a in self._failed_queue[dtype] if c != code]
        self._failed_queue[dtype].append((code, next_retry, attempt + 1))
        logger.info("[RetryQueue] %s %s 入队, 第%d次重试, 下次 %.0fs 后", dtype, code, attempt + 1, interval)

    def _dequeue_success(self, dtype: str, code: str):
        """成功拉取, 移出队列"""
        if dtype not in self._failed_queue:
            return
        before = len(self._failed_queue[dtype])
        self._failed_queue[dtype] = [(c, t, a) for c, t, a in self._failed_queue[dtype] if c != code]
        removed = before - len(self._failed_queue[dtype])
        if removed:
            logger.info("[RetryQueue] %s %s 补拉成功, 移出队列", dtype, code)
        if not self._failed_queue[dtype]:
            del self._failed_queue[dtype]

    def _process_failed_queue(self):
        """处理所有到期的失败补拉任务"""
        now = time.time()
        for dtype in list(self._failed_queue.keys()):
            pending = [(c, t, a) for c, t, a in self._failed_queue[dtype] if t <= now]
            if not pending:
                continue
            codes_to_retry = [c for c, _, _ in pending]
            logger.info("[RetryQueue] %s: %d 条待补拉 → %s", dtype, len(codes_to_retry), codes_to_retry)
            try:
                if dtype == "daily_ohlcv":
                    self.refresh_daily_ohlcv(force=True, codes=codes_to_retry)
                elif dtype == "financial_indicators":
                    self.refresh_financial(force=True, codes=codes_to_retry)
                elif dtype == "capital_flows":
                    self.refresh_capital_flows(force=True, codes=codes_to_retry)
                elif dtype == "fundamentals":
                    self.refresh_fundamentals(force=True, codes=codes_to_retry)
                elif dtype == "news_events":
                    self.refresh_news(force=True, codes=codes_to_retry)
            except Exception as exc:
                logger.warning("[RetryQueue] %s 批量补拉异常: %s", dtype, exc)

    # ═══════════════════════════════════════
    # 按数据类型刷新
    # ═══════════════════════════════════════

    def refresh_daily_ohlcv(self, force: bool = False, codes: list[str] | None = None) -> dict[str, int]:
        """刷新日K线 (建议收盘后15:30执行)"""
        results: dict[str, int] = {}
        from services.data_warehouse.fetchers import DailyFetcher
        fetcher = DailyFetcher()
        target_codes = codes or self._stock_codes
        for i, code in enumerate(target_codes):
            try:
                if not force and self._reader.is_fresh(code, "daily_ohlcv"):
                    self._dequeue_success("daily_ohlcv", code)
                    continue
                rows = fetcher.fetch(code, days=365)
                if rows:
                    from services.data_warehouse.storage import DataLake
                    lake = DataLake()
                    cnt = lake.upsert_ohlcv(code, rows)
                    results[code] = cnt
                    self._dequeue_success("daily_ohlcv", code)
                    logger.info("[Scheduler] OHLCV %s: %d rows", code, cnt)
                else:
                    self._enqueue_failed("daily_ohlcv", code)
            except Exception as exc:
                logger.warning("[Scheduler] OHLCV %s 失败: %s", code, exc)
                self._enqueue_failed("daily_ohlcv", code)
            if i < len(target_codes) - 1:
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

    def refresh_financial(self, force: bool = False, codes: list[str] | None = None) -> dict[str, int]:
        """刷新财务指标 (建议收盘后16:00)"""
        results: dict[str, int] = {}
        from services.data_warehouse.fetchers import FinancialFetcher
        fetcher = FinancialFetcher()
        target_codes = codes or self._stock_codes
        for code in target_codes:
            try:
                if not force and self._reader.is_fresh(code, "financial_indicators"):
                    self._dequeue_success("financial_indicators", code)
                    continue
                data = fetcher.fetch(code)
                if data:
                    from services.data_warehouse.storage import DataLake
                    lake = DataLake()
                    for period, indicators in data.items():
                        lake.upsert_financial(code, period, indicators)
                    results[code] = sum(len(v) for v in data.values())
                    self._dequeue_success("financial_indicators", code)
                else:
                    self._enqueue_failed("financial_indicators", code)
            except Exception as exc:
                logger.warning("[Scheduler] 财务 %s 失败: %s", code, exc)
                self._enqueue_failed("financial_indicators", code)
            time.sleep(5 + random.uniform(0, 3))
        return results

    def refresh_capital_flows(self, force: bool = False, codes: list[str] | None = None) -> dict[str, int]:
        """刷新资金流向 (建议收盘后16:30)"""
        results: dict[str, int] = {}
        from services.data_warehouse.fetchers import CapitalFlowFetcher
        fetcher = CapitalFlowFetcher()
        target_codes = codes or self._stock_codes
        for code in target_codes:
            try:
                if not force and self._reader.is_fresh(code, "capital_flows"):
                    self._dequeue_success("capital_flows", code)
                    continue
                rows = fetcher.fetch(code, days=30)
                if rows:
                    from services.data_warehouse.storage import DataLake
                    lake = DataLake()
                    cnt = lake.upsert_capital_flows(code, rows)
                    results[code] = cnt
                    self._dequeue_success("capital_flows", code)
                else:
                    self._enqueue_failed("capital_flows", code)
            except Exception as exc:
                logger.warning("[Scheduler] 资金流 %s 失败: %s", code, exc)
                self._enqueue_failed("capital_flows", code)
            time.sleep(3 + random.uniform(0, 2))
        return results

    def refresh_news(self, force: bool = False, codes: list[str] | None = None) -> dict[str, int]:
        """刷新新闻 (每小时)"""
        results: dict[str, int] = {}
        from services.data_warehouse.fetchers import NewsFetcher
        fetcher = NewsFetcher()
        target_codes = codes or self._stock_codes
        for code in target_codes:
            try:
                if not force and self._reader.is_fresh(code, "news_events"):
                    self._dequeue_success("news_events", code)
                    continue
                items = fetcher.fetch(code, days=2)
                if items:
                    from services.data_warehouse.storage import DataLake
                    lake = DataLake()
                    results[code] = lake.insert_news(items)
                    self._dequeue_success("news_events", code)
                else:
                    self._enqueue_failed("news_events", code)
            except Exception as exc:
                logger.warning("[Scheduler] 新闻 %s 失败: %s", code, exc)
                self._enqueue_failed("news_events", code)
            time.sleep(2 + random.uniform(0, 1))
        return results

    def refresh_fundamentals(self, force: bool = False, codes: list[str] | None = None) -> dict[str, int]:
        """刷新基本面 (每周)"""
        results: dict[str, int] = {}
        from services.data_warehouse.fetchers import FundamentalsFetcher
        fetcher = FundamentalsFetcher()
        target_codes = codes or self._stock_codes
        for code in target_codes:
            try:
                if not force and self._reader.is_fresh(code, "fundamentals"):
                    self._dequeue_success("fundamentals", code)
                    continue
                data = fetcher.fetch(code)
                if data:
                    from services.data_warehouse.storage import DataLake
                    lake = DataLake()
                    lake.upsert_fundamentals(code, data)
                    results[code] = 1
                    self._dequeue_success("fundamentals", code)
                else:
                    self._enqueue_failed("fundamentals", code)
            except Exception as exc:
                logger.warning("[Scheduler] 基本面 %s 失败: %s", code, exc)
                self._enqueue_failed("fundamentals", code)
            time.sleep(5 + random.uniform(0, 3))
        return results

    def backfill_turnover(self, codes: list[str] | None = None) -> dict[str, int]:
        """回填换手率: 查询 daily_ohlcv 中 turnover=0 的行，用 Tushare→akshare 补充。

        降级链: Tushare Pro (daily_basic, 付费) → akshare EM (免费)
        """
        results: dict[str, int] = {}
        from services.data_warehouse.storage import DataLake
        from services.data_warehouse.fetchers import _ts_post

        lake = DataLake()
        target_codes = codes or self._stock_codes

        for code in target_codes:
            pairs: list[tuple[str, float]] = []
            prefix = f"{code}.SZ" if code.startswith(("0", "3")) else f"{code}.SH"

            # ── 1. Tushare Pro (付费, 优先) ──
            try:
                start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
                end = datetime.now().strftime("%Y%m%d")
                fields, items = _ts_post("daily_basic", {
                    "ts_code": prefix, "start_date": start, "end_date": end,
                    "fields": "trade_date,turnover_rate",
                })
                if items and "turnover_rate" in fields:
                    tr_idx = fields.index("turnover_rate")
                    date_idx = fields.index("trade_date")
                    for row in items:
                        d = str(row[date_idx])[:8]
                        tr = float(row[tr_idx]) if row[tr_idx] is not None else 0.0
                        if d and tr > 0:
                            pairs.append((d, tr))
                    if pairs:
                        logger.info("[BackfillTurnover] %s: Tushare %d 行", code, len(pairs))
            except Exception as exc:
                logger.debug("[BackfillTurnover] %s Tushare 失败: %s", code, exc)

            # ── 2. akshare EM (免费, 降级) ──
            if not pairs:
                try:
                    import akshare as ak
                    start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
                    end = datetime.now().strftime("%Y%m%d")
                    df = ak.stock_zh_a_hist(
                        symbol=code, period="daily",
                        start_date=start, end_date=end, adjust="qfq",
                    )
                    if df is not None and not df.empty:
                        for _, r in df.iterrows():
                            d = str(r.get("日期", ""))[:10].replace("-", "")
                            tr = float(r.get("换手率", 0) or 0)
                            if d and tr > 0:
                                pairs.append((d, tr))
                        if pairs:
                            logger.info("[BackfillTurnover] %s: akshare %d 行", code, len(pairs))
                except Exception as exc:
                    logger.debug("[BackfillTurnover] %s akshare 失败: %s", code, exc)

            if not pairs:
                logger.info("[BackfillTurnover] %s: 两源均无换手率数据", code)
                continue

            updated = lake.update_ohlcv_turnover(code, pairs)
            results[code] = updated
            logger.info("[BackfillTurnover] %s: 入库 %d 行", code, updated)
            time.sleep(1.0 + random.uniform(0, 1))  # 更短间隔，Tushare 限流 50/min 已足够

        total = sum(results.values())
        logger.info("[BackfillTurnover] 完成: %d 只股票, 共 %d 行", len(results), total)
        return results

    def backfill_chip_history(self, codes: list[str] | None = None) -> dict[str, int]:
        """回填筹码分布完整历史序列: 用 ChipFetcher.fetch_all() 一次写入全部历史行。

        akshare stock_cyq_em 每只股票返回约 90 行历史数据，
        此方法一次性写入 chip_distribution 表，后续每日增量由 refresh_chip_distribution 处理。
        """
        results: dict[str, int] = {}
        from services.data_warehouse.fetchers import ChipFetcher
        from services.data_warehouse.storage import DataLake

        fetcher = ChipFetcher()
        lake = DataLake()
        target_codes = codes or self._stock_codes

        for code in target_codes:
            try:
                rows = fetcher.fetch_all(code)
                if not rows:
                    logger.info("[BackfillChip] %s: 无历史筹码数据", code)
                    continue
                cnt = lake.batch_upsert_chip_distribution(code, rows)
                results[code] = cnt
                logger.info("[BackfillChip] %s: 写入 %d 行历史筹码", code, cnt)
            except Exception as exc:
                logger.warning("[BackfillChip] %s 失败: %s", code, exc)
            time.sleep(4 + random.uniform(0, 1))

        total = sum(results.values())
        logger.info("[BackfillChip] 完成: %d 只股票, 共 %d 行", len(results), total)
        return results

    def refresh_chip_distribution(self, codes: list[str] | None = None) -> dict[str, bool]:
        """采集筹码分布: 调用 ChipFetcher 写入 chip_distribution 表（每日快照）。

        如果该股票尚无历史数据（首次运行），自动触发 backfill_chip_history。
        """
        results: dict[str, bool] = {}
        from services.data_warehouse.fetchers import ChipFetcher
        from services.data_warehouse.storage import DataLake
        from datetime import timezone as tz, timedelta

        fetcher = ChipFetcher()
        lake = DataLake()
        target_codes = codes or self._stock_codes
        today = datetime.now(tz(timedelta(hours=8))).strftime("%Y%m%d")

        for code in target_codes:
            # 如果该股无历史数据，先回流
            existing = lake.query_chip_distribution(code)
            if existing is None:
                logger.info("[ChipDist] %s: 无历史数据, 触发回填", code)
                hist_rows = fetcher.fetch_all(code)
                if hist_rows:
                    lake.batch_upsert_chip_distribution(code, hist_rows)
                    logger.info("[ChipDist] %s: 历史回填 %d 行", code, len(hist_rows))
                else:
                    results[code] = False
                    continue

            try:
                data = fetcher.fetch(code)
                if data:
                    lake.upsert_chip_distribution(code, today, data)
                    results[code] = True
                    logger.info("[ChipDist] %s: 筹码已采集 profit=%.1f%% cost=%.2f conc=%.2f",
                                code, data.get("profit_ratio", 0),
                                data.get("avg_cost", 0), data.get("concentration", 0))
                else:
                    results[code] = False
                    logger.info("[ChipDist] %s: 采集失败", code)
            except Exception as exc:
                logger.warning("[ChipDist] %s 异常: %s", code, exc)
                results[code] = False
            time.sleep(2 + random.uniform(0, 1))

        success = sum(1 for v in results.values() if v)
        logger.info("[ChipDist] 完成: %d/%d 成功", success, len(results))
        return results

    def refresh_research_data(self) -> dict[str, int]:
        """刷新研究数据: 对所有有 OHLCV 数据的股票执行换手率回填 + 筹码本地计算.

        生产环境只维护 stock_pool 的 18 只股票. 此方法补充维护扩展的 216 只,
        确保研究数据持续积累.
        """
        results: dict[str, int] = {}
        all_codes = self._get_all_db_codes()
        logger.info("[ResearchData] 开始刷新 %d 只研究股票", len(all_codes))

        # Step 1: 换手率回填 (Tushare)
        logger.info("[ResearchData] Step 1/2: 换手率回填")
        turn_results = self.backfill_turnover(codes=all_codes)
        results["turnover"] = sum(turn_results.values())

        # Step 2: 筹码分布 (本地计算, 零API)
        logger.info("[ResearchData] Step 2/2: 筹码本地计算")
        chip_ok = 0
        from services.data_warehouse.storage import DataLake
        import numpy as np

        lake = DataLake()
        for code in all_codes:
            try:
                import sqlite3
                cfg = DataWarehouseConfig.get_instance()
                conn = sqlite3.connect(str(cfg.db_path))
                rows = conn.execute(
                    "SELECT date, open, high, low, close, volume, turnover "
                    "FROM daily_ohlcv WHERE stock_code=? AND turnover > 0 ORDER BY date", (code,)
                ).fetchall()
                conn.close()

                if len(rows) < 30:
                    continue

                # 取最近 150 天计算
                data = rows[-150:]
                o = np.array([r[1] for r in data], dtype=float)
                h = np.array([r[2] for r in data], dtype=float)
                l = np.array([r[3] for r in data], dtype=float)
                c = np.array([r[4] for r in data], dtype=float)
                v = np.array([r[5] for r in data], dtype=float)
                tr = np.array([float(r[6] or 0) for r in data], dtype=float)

                # 调用本地筹码算法
                from scripts.compute_chip_local import compute_chip_distribution
                chip_result = compute_chip_distribution(o, h, l, c, v, tr)
                if chip_result:
                    today = date.today().strftime("%Y%m%d")
                    lake.upsert_chip_distribution(code, today, {
                        "profit_ratio": chip_result["profit_ratio"],
                        "avg_cost": chip_result["avg_cost"],
                        "concentration": chip_result["concentration"],
                        "source": "local_compute",
                    })
                    chip_ok += 1
            except Exception as exc:
                logger.debug("[ResearchData] %s 筹码失败: %s", code, exc)

        results["chip"] = chip_ok
        logger.info("[ResearchData] 完成: 换手率=%d 只, 筹码=%d 只",
                    results.get("turnover", 0), chip_ok)
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
        # P0 研究数据: 换手率回填 + 筹码分布采集
        if force or results.get("daily_ohlcv"):
            results["turnover_backfill"] = self.backfill_turnover()
            results["chip_distribution"] = self.refresh_chip_distribution()
        return results

    def refresh_stale(self) -> dict[str, int]:
        """仅刷新过期数据 (被各子系统自动调用)"""
        refreshed = 0
        for code in self._stock_codes:
            for dtype in ["daily_ohlcv", "financial_indicators",
                          "capital_flows", "fundamentals",
                          "chip_distribution"]:
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

    def _get_all_db_codes(self) -> list[str]:
        """获取 data_warehouse 中所有有 OHLCV 数据的股票代码."""
        import sqlite3
        cfg = DataWarehouseConfig.get_instance()
        conn = sqlite3.connect(str(cfg.db_path))
        codes = [r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM daily_ohlcv ORDER BY stock_code"
        ).fetchall()]
        conn.close()
        return codes

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
        last_turnover = 0
        last_chip = 0
        last_research = 0
        last_calibrate = 0

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

                # 周一 09:00: 刷新基本面 (周度)
                if weekday == 1 and hour == 9 and 0 <= minute < 5 and now - last_fundamentals > 600000:
                    logger.info("[Scheduler] 触发: 基本面刷新(周度)")
                    self.refresh_fundamentals()
                    last_fundamentals = now

                # 每日 10:50 / 13:50: 盘前刷新基本面 (配合 11:00/14:00 整点分析)
                if weekday <= 5 and hour in (10, 13) and 48 <= minute <= 52:
                    if now - last_fundamentals > 1800:  # 至少间隔 30 分钟
                        logger.info("[Scheduler] 触发: 基本面刷新(盘前 %02d:%02d)", hour, minute)
                        self.refresh_fundamentals()
                        last_fundamentals = now

                # 15:35 后: 回填换手率（日K刷新后完成，每日一次）
                if (hour > 15 or (hour == 15 and minute >= 35)) and now - last_turnover > 3600:
                    if weekday <= 5:
                        logger.info("[Scheduler] 触发: 换手率回填")
                        self.backfill_turnover()
                        last_turnover = now

                # 15:40 后: 采集筹码分布（每日一次，收盘后）
                if (hour > 15 or (hour == 15 and minute >= 40)) and now - last_chip > 3600:
                    if weekday <= 5:
                        logger.info("[Scheduler] 触发: 筹码分布采集")
                        self.refresh_chip_distribution()
                        last_chip = now

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

                # 15:45 后: 刷新研究数据 (216只, 换手率+筹码, 每日一次)
                if (hour > 15 or (hour == 15 and minute >= 45)) and now - last_research > 3600:
                    if weekday <= 5:
                        logger.info("[Scheduler] 触发: 研究数据刷新")
                        self.refresh_research_data()
                        last_research = now

                # 20:30: 校准策略评分调整值
                if hour == 20 and minute == 30 and now - last_calibrate > 3600:
                    if weekday <= 5:
                        logger.info("[Scheduler] 触发: 策略评分校准")
                        import subprocess
                        result = subprocess.run(
                            [sys.executable, "scripts/calibrate_skill_scores.py", "--min-samples", "30"],
                            capture_output=True, text=True, timeout=120,
                        )
                        logger.info("[Scheduler] 校准完成:\n%s", result.stdout)
                        if result.stderr:
                            logger.warning("[Scheduler] 校准错误:\n%s", result.stderr)
                        last_calibrate = now

                # 每次循环: 处理失败补拉队列
                self._process_failed_queue()

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
