"""数据获取器 — 每种数据类型一个 Fetcher 类

所有 Fetcher 通过 `@limiter.retry("source")` 受令牌桶保护。
内置多级降级链, 数据源优先级: Tushare(付费) → pytdx(TCP) → Baostock(TCP) → Sina/Tencent → akshare(EM) → efinance
"""
from __future__ import annotations

import logging
import os
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


# ── Tushare HTTP 客户端 (轻量, 无 SDK 依赖) ──
_TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")

def _ts_post(api_name: str, params: dict) -> list[dict]:
    """调用 Tushare Pro HTTP API"""
    if not _TUSHARE_TOKEN:
        return []
    import requests
    try:
        resp = requests.post(
            "http://api.tushare.pro",
            json={"apiname": api_name, "token": _TUSHARE_TOKEN, "params": params, "fields": ""},
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            return []
        return data.get("data", {}).get("items", [])
    except Exception:
        return []

_TS_FIELD_MAP = {}  # filled by each fetcher


# ═══════════════════════════════════════════
# OHLCV 日K线获取器
# ═══════════════════════════════════════════

class DailyFetcher:
    """日K线获取, 降级链: pytdx(TCP) → Sina → Tushare → akshare(EM) → efinance"""

    FETCHERS = ["pytdx", "akshare_sina", "tushare", "akshare_em", "efinance"]

    def fetch_pytdx(self, code: str, days: int = 365) -> list[dict]:
        """通达信TCP源(永不封IP), 使用pytdx direct"""
        try:
            from pytdx.hq import TdxHq_API
            api = TdxHq_API(multithread=True)
            market = 1 if code.startswith(("6", "5", "9")) else 0
            hosts = [
                ("119.147.212.81", 7709), ("112.74.214.43", 7727),
                ("221.231.141.60", 7709), ("101.227.73.20", 7709),
            ]
            connected = False
            for host, port in hosts:
                try:
                    api.connect(host, port)
                    connected = True
                    break
                except Exception:
                    continue
            if not connected:
                return []
            count = min(days, 800)
            data = api.get_security_bars(9, market, code, 0, count)
            if not data:
                return []
            rows = []
            for d in data:
                rows.append({
                    "date": str(d.get("datetime", ""))[:8],
                    "open": float(d.get("open", 0)),
                    "high": float(d.get("high", 0)),
                    "low": float(d.get("low", 0)),
                    "close": float(d.get("close", 0)),
                    "volume": float(d.get("volume", 0)),
                    "amount": float(d.get("amount", 0)),
                    "pct_chg": 0.0,
                    "turnover": 0.0,
                    "source": "pytdx",
                })
            return rows
        except ImportError:
            logger.debug("pytdx not installed, skipping")
            return []
        except Exception:
            return []  # fall through to next source

    @_get_limiter().retry("tushare")
    def fetch_tushare(self, code: str, days: int = 365) -> list[dict]:
        """Tushare OHLCV — 付费稳定源"""
        ts_code = f"{code}.SZ" if code.startswith(("0","3")) else f"{code}.SH"
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        items = _ts_post("daily", {"ts_code": ts_code, "start_date": start})
        if not items:
            return []
        rows = []
        for item in items:
            if len(item) < 9:
                continue
            rows.append({
                "date": str(item[1]),
                "open": float(item[2] or 0),
                "high": float(item[3] or 0),
                "low": float(item[4] or 0),
                "close": float(item[5] or 0),
                "volume": float(item[6] or 0),
                "amount": float(item[7] or 0),
                "pct_chg": float(item[8] or 0),
                "turnover": 0.0,
                "source": "tushare",
            })
        return rows

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
    """财务指标获取, 降级链: Tushare → Baostock → EM"""

    def _fetch_tushare(self, code: str) -> dict[str, dict[str, float]]:
        """Tushare fina_indicator — 最新一期 ROE/EPS/营收增长"""
        result: dict[str, dict[str, float]] = {}
        items = _ts_post("fina_indicator", {"ts_code": f"{code}.SZ" if code.startswith(("0","3")) else f"{code}.SH", "limit": "1"})
        if not items:
            items = _ts_post("fina_indicator", {"ts_code": f"{code}.SH" if code.startswith(("0","3")) else f"{code}.SZ", "limit": "1"})
        if items:
            fields = ["end_date", "roe", "eps", "bps", "ocfps", "profit_yoy", "revenue_yoy", "retained_eps"]
            row = items[0]
            period = str(row[0])[:7].replace("-", "")
            vals = {}
            for i, f in enumerate(fields[1:], start=1):
                if i < len(row) and row[i] is not None:
                    try: vals[f] = float(row[i])
                    except: pass
            if vals:
                result[period] = vals
        return result

    @_get_limiter().retry("tushare")
    def fetch_tushare_pe_pb(self, code: str) -> dict:
        """Tushare daily_basic — PE/PB"""
        ts_code = f"{code}.SZ" if code.startswith(("0","3")) else f"{code}.SH"
        items = _ts_post("daily_basic", {"ts_code": ts_code, "limit": "1"})
        if items and len(items[0]) >= 6:
            row = items[0]
            return {
                "pe_ttm": float(row[3]) if row[3] else 0,
                "pe_static": float(row[3]) if row[3] else 0,
                "pb": float(row[5]) if row[5] else 0,
            }
        return {}

    @_get_limiter().retry("eastmoney")
    def _fetch_em_financial(self, code: str) -> dict[str, dict[str, float]]:
        """EM 财务指标后备"""
        import akshare as ak
        result: dict[str, dict[str, float]] = {}
        df = ak.stock_financial_analysis_indicator(symbol=code, start_year=datetime.now().year - 2)
        if df is not None and not df.empty:
            period_col = next((c for c in ["报告期", "date", "end_date"] if c in df.columns), None)
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

    def fetch(self, code: str) -> dict[str, dict[str, float]]:
        """降级链: Tushare → EM"""
        result = self._fetch_tushare(code)
        if not result:
            result = self._fetch_em_financial(code)
        return result

    @_get_limiter().retry("tushare")
    def fetch_pe_pb(self, code: str) -> dict:
        """降级链: Tushare → EM"""
        result = self.fetch_tushare_pe_pb(code)
        if not result:
            return self._fetch_em_pe_pb(code)
        return result

    @_get_limiter().retry("eastmoney")
    def _fetch_em_pe_pb(self, code: str) -> dict:
        """EM PE/PB 后备"""
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
            logger.debug("[FinancialFetcher] PE/PB EM 失败 %s: %s", code, exc)
        return {}


# ═══════════════════════════════════════════
# 资金流向获取器
# ═══════════════════════════════════════════

class CapitalFlowFetcher:
    """资金流向获取, 降级链: Tushare → EM"""

    @_get_limiter().retry("tushare")
    def _fetch_tushare(self, code: str, days: int = 30) -> list[dict]:
        """Tushare moneyflow — 个股资金流"""
        ts_code = f"{code}.SZ" if code.startswith(("0","3")) else f"{code}.SH"
        items = _ts_post("moneyflow", {"ts_code": ts_code, "limit": str(days)})
        if not items:
            return []
        rows = []
        for item in items:
            if len(item) < 18:
                continue
            rows.append({
                "date": str(item[1])[:10],
                "main_net_flow": float(item[17] or 0),     # net_mf_amount
                "super_large_net": float(item[13] or 0),   # buy_elg_amount - sell_elg_amount
                "large_net": float(item[11] or 0),
                "medium_net": float(item[9] or 0),
                "small_net": float(item[7] or 0),
                "north_flow": 0.0,
                "north_hold_pct": 0.0,
                "source": "tushare",
            })
        return rows

    @_get_limiter().retry("eastmoney")
    def fetch(self, code: str, days: int = 30) -> list[dict]:
        """降级链: Tushare → EM"""
        rows = self._fetch_tushare(code, days)
        if rows:
            return rows
        return self._fetch_em(code, days)

    def _fetch_em(self, code: str, days: int = 30) -> list[dict]:
        """EM 资金流后备"""
        import akshare as ak
        market = "sh" if code.startswith(("6", "5", "9")) else "sz"
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        if df is None or df.empty:
            return []
        rows = []
        for _, r in df.tail(days).iterrows():
            date_col = next((c for c in ["日期", "date"] if c in df.columns), None)
            if not date_col:
                continue
            rows.append({
                "date": str(r[date_col])[:10],
                "main_net_flow": float(r.get("主力净流入-净额", r.get("main_net_flow", 0))),
                "super_large_net": float(r.get("超大单净流入-净额", r.get("super_large_net", 0))),
                "large_net": float(r.get("大单净流入-净额", r.get("large_net", 0))),
                "medium_net": float(r.get("中单净流入-净额", r.get("medium_net", 0))),
                "small_net": float(r.get("小单净流入-净额", r.get("small_net", 0))),
                "north_flow": 0.0, "north_hold_pct": 0.0,
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
    """基本面快照获取 (每周), 降级链: Tushare → EM"""

    @_get_limiter().retry("tushare")
    def _fetch_tushare(self, code: str) -> dict:
        """Tushare stock_basic + daily_basic + fina_indicator"""
        result: dict[str, Any] = {}
        ts_code = f"{code}.SZ" if code.startswith(("0","3")) else f"{code}.SH"

        # 名称/行业
        items = _ts_post("stock_basic", {"ts_code": ts_code})
        if items and len(items[0]) >= 7:
            result["name"] = items[0][1] or ""
            result["industry"] = items[0][4] or ""

        # PE/PB/市值
        items = _ts_post("daily_basic", {"ts_code": ts_code, "limit": "1"})
        if items and len(items[0]) >= 8:
            result["pe_ttm"] = float(items[0][3] or 0)
            result["pb"] = float(items[0][5] or 0)
            result["market_cap"] = float(items[0][6] or 0) / 1e8 if items[0][6] else 0

        # ROE
        items = _ts_post("fina_indicator", {"ts_code": ts_code, "limit": "1"})
        if items:
            try: result["roe"] = float(items[0][2] or 0)
            except: pass

        result.setdefault("source", "tushare")
        return result

    @_get_limiter().retry("eastmoney")
    def fetch(self, code: str) -> dict:
        """降级链: Tushare → EM"""
        result = self._fetch_tushare(code)
        if result.get("name"):
            return result
        return self._fetch_em(code)

    def _fetch_em(self, code: str) -> dict:
        """EM 基本面后备"""
        import akshare as ak
        result: dict[str, Any] = {}
        try:
            df = ak.stock_individual_info_em(symbol=code)
            if df is not None and not df.empty:
                info = dict(zip(df.iloc[:, 0], df.iloc[:, 1]))
                result["name"] = info.get("股票简称", "")
                result["industry"] = info.get("行业", "")
                result["market_cap"] = float(info.get("总市值", 0) or 0) / 1e8
        except Exception:
            pass
        try:
            ff = FinancialFetcher()
            pe_pb = ff.fetch_pe_pb(code)
            result.update(pe_pb)
        except Exception:
            pass
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
