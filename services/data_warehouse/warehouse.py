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

    def get_chip_distribution(self, code: str) -> dict | None:
        """获取筹码分布 (获利比例/平均成本/集中度)"""
        cached = self._lake.query_chip_distribution(code)
        if cached:
            return cached
        from services.data_warehouse.fetchers import ChipFetcher
        try:
            data = ChipFetcher().fetch(code)
            if data:
                self._lake.upsert_chip_distribution(code, "", data)
                return data
        except Exception as exc:
            logger.debug("[Warehouse] 筹码分布获取失败 %s: %s", code, exc)
        return None

    def get_global_ohlcv(self, market: str, code: str, days: int = 120) -> list[dict]:
        """获取美股/港股日K线"""
        cached = self._lake.query_global_ohlcv(market, code, days)
        if len(cached) >= 2:
            return cached
        from services.data_warehouse.fetchers import GlobalFetcher
        try:
            data = GlobalFetcher().fetch_ohlcv(market, code, days)
            if data:
                self._lake.upsert_global_ohlcv(market, code, data)
                return data
        except Exception as exc:
            logger.debug("[Warehouse] 全球行情获取失败 %s/%s: %s", market, code, exc)
        return cached

    def get_global_fundamentals(self, market: str, code: str) -> dict | None:
        """获取美股/港股基本面"""
        cached = self._lake.query_global_fundamentals(market, code)
        if cached:
            return cached
        from services.data_warehouse.fetchers import GlobalFetcher
        try:
            data = GlobalFetcher().fetch_fundamentals(market, code)
            if data:
                self._lake.upsert_global_fundamentals(market, code, data)
                return data
        except Exception as exc:
            logger.debug("[Warehouse] 全球基本面获取失败 %s/%s: %s", market, code, exc)
        return None

    def get_index_ohlcv(self, index_code: str, days: int = 60) -> list[dict]:
        """获取指数日K线"""
        cached = self._lake.query_index_ohlcv(index_code, days)
        if len(cached) >= 2:
            return cached
        from services.data_warehouse.fetchers import IndexFetcher
        try:
            all_data = IndexFetcher().fetch_all()
            if all_data:
                code_data = [r for r in all_data if r["index_code"] == index_code]
                if code_data:
                    self._lake.upsert_index_ohlcv(index_code, code_data)
                    return code_data[:days]
        except Exception as exc:
            logger.debug("[Warehouse] 指数获取失败 %s: %s", index_code, exc)
        return cached

    def get_market_breadth(self) -> dict | None:
        """获取市场广度 (涨停/跌停家数)"""
        from services.data_warehouse.fetchers import MarketBreadthFetcher
        try:
            return MarketBreadthFetcher().fetch()
        except Exception as exc:
            logger.debug("[Warehouse] 市场广度获取失败: %s", exc)
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
                          "capital_flows", "news_events", "fundamentals",
                          "chip_distribution", "financial_statements",
                          "shareholder", "block_trade", "index_ohlcv",
                          "global_ohlcv", "global_fundamentals"]

        result: dict[str, dict[str, int]] = {}
        for code in codes:
            result[code] = {}
            for dtype in data_types:
                if not force and self.is_fresh(code, dtype):
                    result[code][dtype] = -1  # 已有缓存
                    continue
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
                    elif dtype == "chip_distribution":
                        data = self.get_chip_distribution(code)
                        count = 1 if data else 0
                    elif dtype == "financial_statements":
                        from services.data_warehouse.fetchers import FinancialStatementFetcher
                        data = FinancialStatementFetcher().fetch(code)
                        count = len(data) if data else 0
                    elif dtype == "shareholder":
                        from services.data_warehouse.fetchers import ShareholderFetcher
                        rows = ShareholderFetcher().fetch(code)
                        count = len(rows) if rows else 0
                    elif dtype == "block_trade":
                        from services.data_warehouse.fetchers import BlockTradeFetcher
                        rows = BlockTradeFetcher().fetch(code)
                        count = len(rows) if rows else 0
                except Exception as exc:
                    logger.warning("[Warehouse] 预热 %s %s 失败: %s", code, dtype, exc)
                result[code][dtype] = count

        # 指数(全局一次, 非per-stock)
        if "index_ohlcv" in data_types:
            try:
                from services.data_warehouse.fetchers import IndexFetcher
                all_data = IndexFetcher().fetch_all()
                if all_data:
                    code_set = {r["index_code"] for r in all_data}
                    for ic in code_set:
                        ic_data = [r for r in all_data if r["index_code"] == ic]
                        self._lake.upsert_index_ohlcv(ic, ic_data)
                result["__index__"] = {"index_ohlcv": len(all_data) if all_data else 0}
            except Exception as exc:
                logger.warning("[Warehouse] 预热指数失败: %s", exc)

        # 全球行情(美股指数, 一次性)
        if "global_ohlcv" in data_types or "global_fundamentals" in data_types:
            from services.data_warehouse.fetchers import GlobalFetcher
            gf = GlobalFetcher()
            for market, codes_list in [("us", ["SPY", "QQQ", "DIA", "IWM"]), ("hk", ["^HSI"])]:
                for gcode in codes_list:
                    try:
                        if "global_ohlcv" in data_types:
                            odata = gf.fetch_ohlcv(market, gcode)
                            if odata:
                                self._lake.upsert_global_ohlcv(market, gcode, odata)
                        if "global_fundamentals" in data_types:
                            fdata = gf.fetch_fundamentals(market, gcode)
                            if fdata:
                                self._lake.upsert_global_fundamentals(market, gcode, fdata)
                    except Exception as exc:
                        logger.warning("[Warehouse] 预热全球 %s/%s 失败: %s", market, gcode, exc)

        return result

    @staticmethod
    def get_full_market_stock_list() -> list[dict]:
        """加载全A股股票列表（本地缓存优先, Tushare 备用）"""
        import json, os
        from pathlib import Path
        cache_path = Path(__file__).resolve().parent.parent.parent / "data" / "stock_list.json"
        if cache_path.exists():
            with open(cache_path) as f:
                return json.load(f)
        try:
            import tushare as ts
            token = os.popen('grep TUSHARE_TOKEN ~/.secrets 2>/dev/null').read().strip().split('=')[-1].strip()
            if token:
                ts.set_token(token)
                pro = ts.pro_api()
                df = pro.stock_basic(exchange='', list_status='L', fields='symbol,name')
                stocks = [{"code": r["symbol"], "name": r["name"]} for _, r in df.iterrows()]
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_path, "w") as f:
                    json.dump(stocks, f, ensure_ascii=False)
                logger.info("stock_list.json 已生成 (%d 只)", len(stocks))
                return stocks
        except Exception as exc:
            logger.warning("获取全市场股票列表失败: %s", exc)
        return []

    def prefetch_full_market(self, days: int = 365, max_workers: int = 8,
                              batch_size: int = 100, max_calls_per_min: int = 60) -> dict:
        """全A股历史数据回填（首次执行/断点续传）

        流程:
          1. 加载 stock_list.json
          2. 跳过已有缓存且未过期的股票
          3. 并发获取, 限速保护
          4. 每 batch_size 只保存进度
        """
        import json, os, time
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from pathlib import Path
        from services.data_warehouse.fetchers import DailyFetcher

        all_stocks = self.get_full_market_stock_list()
        if not all_stocks:
            logger.error("股票列表为空, 无法执行全市场回填")
            return {}
        logger.info("全市场回填: %d 只股票, 每只 %d 天, %d 线程", len(all_stocks), days, max_workers)
        t_start = time.time()

        # 断点续传: 已有数据的跳过(从 daily_ohlcv 直接查, cache_meta 可能不全)
        import sqlite3 as _sq
        _db_path = Path(__file__).resolve().parent.parent.parent / "data" / "data_warehouse.db"
        _existing = set()
        try:
            _conn = _sq.connect(str(_db_path))
            _existing = {r[0] for r in _conn.execute("SELECT DISTINCT stock_code FROM daily_ohlcv").fetchall()}
            _conn.close()
        except Exception:
            pass
        pending = []
        for s in all_stocks:
            code = s["code"]
            if code.startswith(("8", "4", "2")):
                continue
            if code in _existing:
                continue
            pending.append(code)
        logger.info("需更新: %d / %d 只", len(pending), len(all_stocks))

        fetcher = DailyFetcher()
        progress_path = Path(__file__).resolve().parent.parent.parent / "data" / "full_market_progress.json"
        done = set()
        if progress_path.exists():
            try:
                done = set(json.load(open(progress_path)))
                logger.info("断点续传: 已有 %d 只完成", len(done))
            except Exception:
                pass

        res = {"total": len(all_stocks), "success": len(done), "failed": 0, "skipped": (len(all_stocks) - len(pending))}
        rlimiter = _RateLimiter(max_calls_per_min, 60)
        lock = threading.Lock()
        batch_count = 0

        def _fetch_one(code: str) -> bool:
            if code in done:
                return True
            rlimiter.wait()
            try:
                rows = fetcher.fetch(code, days=days)
                if rows:
                    self._lake.upsert_daily_ohlcv(code, rows)
                with lock:
                    done.add(code)
                return True
            except Exception as exc:
                logger.debug("[全市场回填] %s 失败: %s", code, exc)
                return False

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_one, code): code for code in pending if code not in done}
            for fut in as_completed(futures):
                code = futures[fut]
                ok = fut.result()
                batch_count += 1
                if ok:
                    res["success"] += 1
                else:
                    res["failed"] += 1
                if batch_count % batch_size == 0:
                    with open(progress_path, "w") as f:
                        json.dump(sorted(done)[-5000:], f)
                    elapsed = time.time() - t_start
                    rate = batch_count / elapsed * 60 if elapsed > 0 else 0
                    logger.info("进度: %d/%d | 成功 %d 失败 %d | %.1f 只/分",
                                batch_count, len(pending), res["success"],
                                res["failed"], rate)

        with open(progress_path, "w") as f:
            json.dump(sorted(list(done))[-5000:], f)
        elapsed = time.time() - t_start
        logger.info("全市场回填完成: 成功 %d 失败 %d 跳过 %d 耗时 %.0fs",
                     res["success"], res["failed"], res["skipped"], elapsed)
        return res

    def stats(self) -> dict:
        """数据湖统计"""
        return self._lake.stats()


class _RateLimiter:
    """简易令牌桶限速器（内部使用）"""

    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self.timestamps: list[float] = []

    def wait(self):
        import time
        now = time.time()
        self.timestamps = [t for t in self.timestamps if now - t < self.period]
        if len(self.timestamps) >= self.max_calls:
            sleep = self.timestamps[0] + self.period - now
            if sleep > 0:
                time.sleep(sleep)
        self.timestamps.append(time.time())
