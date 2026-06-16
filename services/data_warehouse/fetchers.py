"""数据获取器 — 每种数据类型一个 Fetcher 类

所有 Fetcher 通过 `@limiter.retry("source")` 受令牌桶保护。
内置多级降级链: akshare(EM) → akshare(Sina) → akshare(Tencent) → efinance
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from services.data_warehouse.config import DataWarehouseConfig
from services.data_warehouse.limiter import TokenBucketLimiter

logger = logging.getLogger(__name__)

# 共享限流器实例
_limiter = None


def _get_limiter() -> TokenBucketLimiter:
    global _limiter
    if _limiter is None:
        _limiter = TokenBucketLimiter()
    return _limiter


# ═══════════════════════════════════════════
# OHLCV 日K线获取器
# ═══════════════════════════════════════════

class DailyFetcher:
    """日K线获取, 降级链: akshare(EM) → akshare(Sina) → efinance"""

    FETCHERS = ["akshare_em", "akshare_sina", "efinance"]

    @_get_limiter().retry("eastmoney")
    def fetch_akshare_em(self, code: str, days: int = 365) -> list[dict]:
        """东方财富源 (akshare)"""
        import akshare as ak
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                 start_date=start, end_date=end, adjust="qfq")
        return self._df_to_rows(df, code, "akshare_em")

    @_get_limiter().retry("sina")
    def fetch_akshare_sina(self, code: str, days: int = 365) -> list[dict]:
        """Sina 源 (LY 已验证的 HTTP 直连, 比 akshare 包装更稳定)"""
        import requests as _req
        prefix = f"sh{code}" if code.startswith(("6", "5", "9")) else f"sz{code}"
        url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "CN_MarketData.getKLineData")
        session = _req.Session()
        session.headers.update({"Referer": "https://finance.sina.com.cn"})
        resp = session.get(url, params={"symbol": prefix, "scale": 240,
                                        "ma": "no", "datalen": days}, timeout=15)
        data = resp.json()
        if not data:
            return []
        rows = []
        for d in data:
            rows.append({
                "date": str(d["day"]).replace("-", "")[:8],
                "open": float(d["open"]),
                "high": float(d["high"]),
                "low": float(d["low"]),
                "close": float(d["close"]),
                "volume": float(d["volume"]),
                "amount": float(d.get("amount", 0)),
                "pct_chg": 0.0,
                "turnover": 0.0,
                "source": "sina",
            })
        return rows

    @_get_limiter().retry("tencent")
    def fetch_efinance(self, code: str, days: int = 365) -> list[dict]:
        """efinance 源"""
        import efinance as ef
        df = ef.stock.get_quote_history(code)
        df = df.tail(days)
        return self._df_to_rows(df, code, "efinance")

    def fetch(self, code: str, days: int = 365) -> list[dict]:
        """自动降级链, 依次尝试各数据源"""
        for name in self.FETCHERS:
            method = getattr(self, f"fetch_{name}")
            try:
                rows = method(code, days)
                if rows:
                    logger.info("[DailyFetcher] %s ← %s: %d rows", code, name, len(rows))
                    return rows
            except Exception as exc:
                logger.warning("[DailyFetcher] %s ← %s 失败: %s", code, name, exc)
                time.sleep(2)
        logger.error("[DailyFetcher] %s: 所有数据源均失败", code)
        return []

    @staticmethod
    def _df_to_rows(df, code: str, source: str) -> list[dict]:
        if df is None or df.empty:
            return []
        import pandas as pd
        rows = []
        for _, r in df.iterrows():
            date_col = None
            for c in ["日期", "date", "Date"]:
                if c in df.columns:
                    date_col = c
                    break
            if date_col is None:
                continue
            date_val = str(r[date_col])[:10]
            rows.append({
                "date": date_val.replace("-", "") if "-" in date_val else date_val[:10],
                "open": float(r.get("开盘") or r.get("Open") or r.get("open") or 0),
                "high": float(r.get("最高") or r.get("High") or r.get("high") or 0),
                "low": float(r.get("最低") or r.get("Low") or r.get("low") or 0),
                "close": float(r.get("收盘") or r.get("Close") or r.get("close") or 0),
                "volume": float(r.get("成交量") or r.get("Volume") or r.get("volume") or 0),
                "amount": float(r.get("成交额") or r.get("Amount") or r.get("amount") or 0),
                "pct_chg": float(r.get("涨跌幅") or r.get("pctChg") or r.get("pct_chg") or 0),
                "turnover": float(r.get("换手率") or r.get("turnover") or 0),
                "source": source,
            })
        return rows


# ═══════════════════════════════════════════
# 实时行情获取器
# ═══════════════════════════════════════════

class RealtimeFetcher:
    """实时行情获取, 优先腾讯批量"""

    @_get_limiter().retry("tencent")
    def fetch_tencent_batch(self, codes: list[str]) -> dict[str, dict]:
        """腾讯批量实时行情 (一次请求最多50只)"""
        import akshare as ak
        symbols = [f"sh{c}" if c.startswith(("6", "5", "9")) else f"sz{c}" for c in codes]
        df = ak.stock_zh_a_spot_em()  # 全市场快照
        if df is None or df.empty:
            return {}
        result = {}
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            if code in codes:
                result[code] = {
                    "name": row.get("名称", ""),
                    "price": float(row.get("最新价", 0)),
                    "change_pct": float(row.get("涨跌幅", 0)),
                    "change_amt": float(row.get("涨跌额", 0)),
                    "volume": float(row.get("成交量", 0)),
                    "amount": float(row.get("成交额", 0)),
                    "volume_ratio": float(row.get("量比", 0)),
                    "turnover": float(row.get("换手率", 0)),
                    "high": float(row.get("最高", 0)),
                    "low": float(row.get("最低", 0)),
                    "open": float(row.get("今开", 0)),
                    "pre_close": float(row.get("昨收", 0)),
                    "source": "tencent",
                }
        return result

    @_get_limiter().retry("sina")
    def fetch_sina_batch(self, codes: list[str]) -> dict[str, dict]:
        """Sina 批量实时行情"""
        return self.fetch_tencent_batch(codes)  # fallback to tencent


# ═══════════════════════════════════════════
# 财务指标获取器
# ═══════════════════════════════════════════

class FinancialFetcher:
    """财务指标获取"""

    @_get_limiter().retry("eastmoney")
    def fetch(self, code: str) -> dict[str, dict[str, float]]:
        """获取 ROE/PE/营收增长等核心财务指标"""
        import akshare as ak
        from datetime import datetime

        result: dict[str, dict[str, float]] = {}
        now = datetime.now()
        start_year = now.year - 2

        # 1. 财务分析指标 (每股收益 ROE 等)
        df = ak.stock_financial_analysis_indicator(
            symbol=code, start_year=start_year
        )
        if df is not None and not df.empty:
            period_col = None
            for c in ["报告期", "date", "end_date"]:
                if c in df.columns:
                    period_col = c
                    break
            if period_col:
                for _, row in df.iterrows():
                    period = str(row[period_col])[:7].replace("-", "").replace("/", "")
                    result.setdefault(period, {})
                    for map_col, indicator in [
                        ("每股净资产", "bvps"), ("净资产收益率", "roe"),
                        ("每股经营现金流", "ocfps"), ("净利润同比增长率", "profit_yoy"),
                        ("营业总收入同比增长率", "revenue_yoy"), ("每股未分配利润", "retained_eps"),
                    ]:
                        if map_col in df.columns:
                            val = row.get(map_col)
                            result[period][indicator] = float(val) if val else 0.0

        return result

    @_get_limiter().retry("eastmoney")
    def fetch_pe_pb(self, code: str) -> dict:
        """获取 PE_TTM / PB 等估值指标"""
        try:
            import akshare as ak
            df = ak.stock_a_lg_indicator(symbol=code)
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                return {
                    "pe_ttm": float(latest.get("peTTM", 0) or 0),
                    "pe_static": float(latest.get("peStatic", 0) or 0),
                    "pb": float(latest.get("pb", 0) or 0),
                }
        except Exception as exc:
            logger.debug("[FinancialFetcher] PE/PB 获取失败 %s: %s", code, exc)
        return {}


# ═══════════════════════════════════════════
# 资金流向获取器
# ═══════════════════════════════════════════

class CapitalFlowFetcher:
    """资金流向获取"""

    @_get_limiter().retry("eastmoney")
    def fetch(self, code: str, days: int = 30) -> list[dict]:
        """获取个股资金流向"""
        import akshare as ak
        market = "sh" if code.startswith(("6", "5", "9")) else "sz"
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        if df is None or df.empty:
            return []
        rows = []
        for _, r in df.tail(days).iterrows():
            date_col = None
            for c in ["日期", "date"]:
                if c in df.columns:
                    date_col = c
                    break
            if not date_col:
                continue
            rows.append({
                "date": str(r[date_col])[:10],
                "main_net_flow": float(r.get("主力净流入-净额", r.get("main_net_flow", 0))),
                "super_large_net": float(r.get("超大单净流入-净额", r.get("super_large_net", 0))),
                "large_net": float(r.get("大单净流入-净额", r.get("large_net", 0))),
                "medium_net": float(r.get("中单净流入-净额", r.get("medium_net", 0))),
                "small_net": float(r.get("小单净流入-净额", r.get("small_net", 0))),
                "north_flow": 0.0,
                "north_hold_pct": 0.0,
                "source": "akshare",
            })
        return rows


# ═══════════════════════════════════════════
# 新闻获取器
# ═══════════════════════════════════════════

class NewsFetcher:
    """新闻/公告获取"""

    @_get_limiter().retry("eastmoney")
    def fetch_stock_news(self, code: str, days: int = 7) -> list[dict]:
        """个股新闻"""
        import akshare as ak
        df = ak.stock_news_em(symbol=code)
        if df is None or df.empty:
            return []
        rows = []
        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(days=days)
        for _, r in df.iterrows():
            date_str = str(r.get("发布时间", r.get("date", "")))[:10]
            try:
                pub = datetime.strptime(date_str, "%Y-%m-%d")
                if pub < cutoff:
                    continue
            except ValueError:
                pass
            rows.append({
                "stock_code": code,
                "title": str(r.get("新闻标题", r.get("title", "")))[:200],
                "url": str(r.get("新闻链接", r.get("url", ""))),
                "summary": str(r.get("新闻内容", r.get("content", "")))[:200],
                "source": "东方财富",
                "category": "新闻",
                "importance": 1,
                "published_at": date_str,
            })
        return rows

    @_get_limiter().retry("cninfo")
    def fetch_announcements(self, code: str, days: int = 7) -> list[dict]:
        """巨潮公告 — 直接调用 CNINFO API"""
        import httpx
        rows = []
        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(days=days)
        try:
            resp = httpx.post(
                "https://www.cninfo.com.cn/new/fulltextSearch/full",
                data={"searchkey": code, "sdate": "", "edate": "",
                      "isfulltext": "false", "sortName": "pubdate",
                      "sortType": "desc", "pageNum": 1, "pageSize": 5},
                headers={"User-Agent": "Mozilla/5.0",
                         "Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
            data = resp.json()
            items = data.get("announcements", []) or data.get("records", [])
            for item in items:
                title = str(item.get("announcementTitle", item.get("title", "")))
                date_str = str(item.get("announcementDate", ""))[:10]
                try:
                    pub = datetime.strptime(date_str, "%Y-%m-%d")
                    if pub < cutoff:
                        continue
                except ValueError:
                    pass
                rows.append({
                    "stock_code": code,
                    "title": title[:200],
                    "url": item.get("adjunctUrl", ""),
                    "summary": title[:200],
                    "source": "巨潮资讯",
                    "category": "公告",
                    "importance": 2,
                    "published_at": date_str,
                })
        except Exception as exc:
            logger.debug("[NewsFetcher] CNINFO 公告获取失败 %s: %s", code, exc)
        return rows

    def fetch(self, code: str, days: int = 7) -> list[dict]:
        news = self.fetch_stock_news(code, days)
        try:
            ann = self.fetch_announcements(code, days)
            news.extend(ann)
        except Exception as exc:
            logger.debug("[NewsFetcher] 公告获取失败 %s: %s", code, exc)
        return news


# ═══════════════════════════════════════════
# 基本面获取器
# ═══════════════════════════════════════════

class FundamentalsFetcher:
    """基本面快照获取 (每周)"""

    @_get_limiter().retry("eastmoney")
    def fetch(self, code: str) -> dict:
        """获取基本面数据"""
        import akshare as ak
        result: dict[str, Any] = {}

        # 名称/行业
        try:
            df = ak.stock_individual_info_em(symbol=code)
            if df is not None and not df.empty:
                info = dict(zip(df.iloc[:, 0], df.iloc[:, 1]))
                result["name"] = info.get("股票简称", "")
                result["industry"] = info.get("行业", "")
                result["market_cap"] = float(info.get("总市值", 0) or 0) / 1e8
        except Exception:
            pass

        # PE/PB/ROE
        try:
            ff = FinancialFetcher()
            pe_pb = ff.fetch_pe_pb(code)
            result.update(pe_pb)
        except Exception:
            pass

        # 财务指标
        try:
            fin = ff.fetch(code)
            if fin:
                latest_periods = sorted(fin.keys(), reverse=True)
                if latest_periods:
                    p = latest_periods[0]
                    result["roe"] = fin[p].get("roe", 0)
                    result["revenue_yoy"] = fin[p].get("revenue_yoy", 0)
                    result["profit_yoy"] = fin[p].get("profit_yoy", 0)
        except Exception:
            pass

        result.setdefault("source", "akshare")
        return result
