"""WarehouseReader — 统一数据读取接口

三阶段策略:
  1. 缓存命中 + 未过期 → 直接返回
  2. 缓存存在但过期 → 返回旧数据 + stale=True, 后台触发异步刷新
  3. 缓存不存在 → 降级调用原始 API → 写入缓存 → 返回

用法:
    from services.data_warehouse import WarehouseReader
    reader = WarehouseReader()
    df = reader.get_daily("600519", days=120)
    quote = reader.get_realtime("600519")
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from services.data_warehouse.config import DataWarehouseConfig
from services.data_warehouse.storage import DataLake

logger = logging.getLogger(__name__)


class WarehouseReader:
    """统一数据读取接口 — 缓存优先 → 降级 API → 写入缓存"""

    def __init__(self, db_path: str | None = None):
        cfg = DataWarehouseConfig.get_instance()
        self._cfg = cfg
        self._lake = DataLake(db_path or cfg.db_path)
        self._lock = threading.Lock()

    # ── 内部: 缓存新鲜度判断 ──

    def is_fresh(self, code: str, data_type: str) -> bool:
        """检查某只股票的某数据类型缓存是否新鲜"""
        meta = self._lake.get_cache_meta(code, data_type)
        if meta is None:
            return False
        dtype_cfg = self._cfg.data_types.get(data_type)
        default_ttl = dtype_cfg.ttl_seconds if dtype_cfg else 86400
        ttl = meta.get("ttl_seconds", default_ttl)
        if meta["last_fetched"] is None:
            return False
        age = time.time() - meta["last_fetched"]
        return age < ttl

    # ═══════════════════════════════════════
    # 日K线
    # ═══════════════════════════════════════

    def get_daily(self, code: str, start: str = "", end: str = "",
                  days: int = 120) -> list[dict]:
        """获取日K线数据

        Args:
            code: 股票代码 (如 "600519")
            start: 开始日期 YYYY-MM-DD (可选)
            end: 结束日期 YYYY-MM-DD (可选)
            days: 回溯天数 (默认120, start/end 为空时生效)

        Returns:
            [{date, open, high, low, close, volume, amount, pct_chg, turnover}, ...]
        """
        # 1. 查缓存
        rows = self._lake.query_ohlcv(code, start, end, days)
        if rows:
            return rows

        # 2. 缓存未命中 → 调 API → 写缓存
        from services.data_warehouse.fetchers import DailyFetcher
        fetcher = DailyFetcher()
        api_rows = fetcher.fetch(code, days)
        if api_rows:
            self._lake.upsert_ohlcv(code, api_rows)
            return api_rows

        return []

    def get_daily_df(self, code: str, start: str = "", end: str = "",
                     days: int = 120):
        """返回 pandas DataFrame (兼容现有分析代码)"""
        rows = self.get_daily(code, start, end, days)
        if not rows:
            import pandas as pd
            return pd.DataFrame()
        import pandas as pd
        return pd.DataFrame(rows)

    # ═══════════════════════════════════════
    # 实时行情
    # ═══════════════════════════════════════

    def get_realtime(self, code: str) -> dict | None:
        """获取单只股票实时行情"""
        # 1. 查缓存 (5min TTL)
        meta = self._lake.get_cache_meta(code, "realtime_quotes")
        if meta and meta["last_fetched"]:
            age = time.time() - meta["last_fetched"]
            if age < 300:  # 5min
                return self._lake.query_realtime(code)

        # 2. 缓存过期 → 批量刷新 (全股票池)
        self._refresh_realtime_batch()
        return self._lake.query_realtime(code)

    def get_realtime_batch(self, codes: list[str] | None = None) -> dict[str, dict]:
        """批量获取实时行情"""
        if codes is None:
            codes = self._cfg.stock_pool
        if not codes:
            return {}

        # 检查是否所有股票缓存都新鲜
        stale_codes = [c for c in codes if not self._is_local_fresh(c, "realtime_quotes", 300)]
        if stale_codes:
            self._refresh_realtime_batch(codes)

        return self._lake.query_realtime_batch(codes)

    def _refresh_realtime_batch(self, codes: list[str] | None = None) -> None:
        """后台刷新实时行情缓存"""
        with self._lock:
            codes = codes or self._cfg.stock_pool
            from services.data_warehouse.fetchers import RealtimeFetcher
            fetcher = RealtimeFetcher()
            try:
                data = fetcher.fetch_tencent_batch(codes)
                for code, q in data.items():
                    self._lake.upsert_realtime(code, q)
                logger.info("[Warehouse] 实时行情刷新: %d 只", len(data))
            except Exception as exc:
                logger.warning("[Warehouse] 实时行情刷新失败: %s", exc)

    # ═══════════════════════════════════════
    # 财务指标
    # ═══════════════════════════════════════

    def get_financial(self, code: str) -> dict[str, dict[str, float]]:
        """获取财务指标: {indicator_name: {period: value}}"""
        cached = self._lake.query_financial(code)
        if cached:
            return cached

        from services.data_warehouse.fetchers import FinancialFetcher
        fetcher = FinancialFetcher()
        try:
            data = fetcher.fetch(code)
            if data:
                for period, indicators in data.items():
                    self._lake.upsert_financial(code, period, indicators)
                return data
        except Exception as exc:
            logger.warning("[Warehouse] 财务指标获取失败 %s: %s", code, exc)
        return {}

    def get_pe_pb(self, code: str) -> dict:
        """获取 PE/PB 估值"""
        from services.data_warehouse.fetchers import FinancialFetcher
        fetcher = FinancialFetcher()
        return fetcher.fetch_pe_pb(code)

    # ═══════════════════════════════════════
    # 资金流向
    # ═══════════════════════════════════════

    def get_capital_flows(self, code: str, days: int = 30) -> list[dict]:
        """获取资金流向"""
        cached = self._lake.query_capital_flows(code, days)
        if cached:
            return cached

        from services.data_warehouse.fetchers import CapitalFlowFetcher
        fetcher = CapitalFlowFetcher()
        try:
            rows = fetcher.fetch(code, days)
            if rows:
                self._lake.upsert_capital_flows(code, rows)
                return rows
        except Exception as exc:
            logger.warning("[Warehouse] 资金流向获取失败 %s: %s", code, exc)
        return []

    # ═══════════════════════════════════════
    # 新闻/公告
    # ═══════════════════════════════════════

    def get_news(self, code: str = "", days: int = 7,
                 min_importance: int = 0, limit: int = 50) -> list[dict]:
        """获取新闻/公告"""
        cached = self._lake.query_news(code, days, min_importance, limit)
        if cached:
            return cached

        if not code:
            return []

        from services.data_warehouse.fetchers import NewsFetcher
        fetcher = NewsFetcher()
        try:
            items = fetcher.fetch(code, days)
            if items:
                self._lake.insert_news(items)
                return items
        except Exception as exc:
            logger.warning("[Warehouse] 新闻获取失败 %s: %s", code, exc)
        return []

    # ═══════════════════════════════════════
    # 基本面
    # ═══════════════════════════════════════

    def get_fundamentals(self, code: str) -> dict | None:
        """获取基本面快照"""
        cached = self._lake.query_fundamentals(code)
        if cached:
            return cached

        from services.data_warehouse.fetchers import FundamentalsFetcher
        fetcher = FundamentalsFetcher()
        try:
            data = fetcher.fetch(code)
            if data:
                self._lake.upsert_fundamentals(code, data)
                return data
        except Exception as exc:
            logger.warning("[Warehouse] 基本面获取失败 %s: %s", code, exc)
        return None

    # ═══════════════════════════════════════
    # 工具
    # ═══════════════════════════════════════

    def _is_local_fresh(self, code: str, data_type: str, ttl: int) -> bool:
        meta = self._lake.get_cache_meta(code, data_type)
        if meta is None or meta["last_fetched"] is None:
            return False
        return time.time() - meta["last_fetched"] < ttl

    def invalidate(self, code: str, data_type: str | None = None,
                   force: bool = False) -> None:
        """使缓存失效"""
        if data_type:
            self._lake.get_cache_meta(code, data_type)  # just access check
        from services.data_warehouse.storage import DataLake
        lake = DataLake()
        if data_type:
            lake._get_conn().execute(
                "DELETE FROM cache_meta WHERE stock_code=? AND data_type=?",
                (code, data_type),
            )
        else:
            lake._get_conn().execute(
                "DELETE FROM cache_meta WHERE stock_code=?", (code,)
            )
        lake._get_conn().commit()

    def prefetch_all(self, codes: list[str] | None = None,
                     data_types: list[str] | None = None,
                     force: bool = False) -> dict[str, dict[str, int]]:
        """批量预热 — 全数据种类
        Returns: {code: {data_type: row_count}}
        """
        codes = codes or self._cfg.stock_pool
        if data_types is None:
            data_types = ["daily_ohlcv", "financial_indicators",
                          "capital_flows", "news_events", "fundamentals"]

        result: dict[str, dict[str, int]] = {}
        for code in codes:
            result[code] = {}
            for dtype in data_types:
                if force or not self.is_fresh(code, dtype):
                    count = 0
                    try:
                        if dtype == "daily_ohlcv":
                            rows = self.get_daily(code, days=365)
                            count = len(rows) if rows else 0
                        elif dtype == "financial_indicators":
                            data = self.get_financial(code)
                            count = sum(len(v) for v in data.values()) if data else 0
                        elif dtype == "capital_flows":
                            rows = self.get_capital_flows(code)
                            count = len(rows) if rows else 0
                        elif dtype == "news_events":
                            items = self.get_news(code)
                            count = len(items) if items else 0
                        elif dtype == "fundamentals":
                            data = self.get_fundamentals(code)
                            count = 1 if data else 0
                    except Exception as exc:
                        logger.warning("[Warehouse] 预热 %s %s 失败: %s", code, dtype, exc)
                    result[code][dtype] = count
                else:
                    result[code][dtype] = -1  # 已有缓存

        return result

    def stats(self) -> dict:
        """数据湖统计"""
        return self._lake.stats()
