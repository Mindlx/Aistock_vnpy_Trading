"""
=============================================
事件驱动分析服务 EventMonitor
=============================================

核心流程：
1. 定期从免费公开API获取公司公告/互动易问答
2. 分类过滤（按事件类型）
3. 高重要性事件自动触发股票重新分析
4. 推送结果

独立服务运行，不依赖 realtime_monitor.py。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Callable

# HTTP 客户端（httpx 优先，回退到 requests）
try:
    import httpx

    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False
    import requests

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================

EASTMONEY_ANNOUNCE_API = (
    "https://np-anotice-stock.eastmoney.com/api/security/annount/search"
)
CNINFO_SEARCH_API = "https://www.cninfo.com.cn/new/fulltextSearch/full"
CNINFO_BASE_URL = "https://www.cninfo.com.cn/"
HUDONGYI_SEARCH_API = (
    "https://ir.p5w.net/api/ir-v2/Interaction/GetInteractionList"
)

# 默认检查间隔（秒）
DEFAULT_CHECK_INTERVAL = 300  # 5 分钟
# 触发重新分析的重要性阈值
DEFAULT_IMPORTANCE_THRESHOLD = 7
# 简报推送的重要性下限
DEFAULT_BRIEF_IMPORTANCE_LOWER = 4
# 低重要性忽略上限
DEFAULT_LOW_IMPORTANCE_UPPER = 3
# 去重 TTL（秒）
DEFAULT_DEDUP_TTL = 86400  # 24 小时
# 请求超时（秒）
HTTP_TIMEOUT = 15

# ============================================================
# HTTP 异步包装（httpx 优先，回退 requests + run_in_executor）
# ============================================================


async def _async_post(
    url: str,
    *,
    json_data: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = HTTP_TIMEOUT,
) -> dict[str, Any]:
    """异步 POST 请求，自动选择 httpx 或 requests"""
    if _HAS_HTTPX:
        async with httpx.AsyncClient(timeout=timeout) as client:
            kwargs: dict[str, Any] = {"headers": headers or {}}
            if json_data is not None:
                kwargs["json"] = json_data
            elif data is not None:
                kwargs["data"] = data
            response = await client.post(url, **kwargs)
            response.raise_for_status()
            return response.json()
    else:
        loop = asyncio.get_event_loop()

        def _sync_post():
            resp = requests.post(
                url,
                json=json_data,
                data=data,
                headers=headers or {},
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()

        return await loop.run_in_executor(None, _sync_post)


async def _async_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = HTTP_TIMEOUT,
    follow_redirects: bool = True,
) -> dict[str, Any] | str:
    """异步 GET 请求，自动选择 httpx 或 requests"""
    if _HAS_HTTPX:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects) as client:
            response = await client.get(url, params=params, headers=headers or {})
            response.raise_for_status()
            ct = response.headers.get("content-type", "")
            if "json" in ct:
                return response.json()
            return response.text
    else:
        loop = asyncio.get_event_loop()

        def _sync_get():
            resp = requests.get(
                url, params=params, headers=headers or {}, timeout=timeout
            )
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            if "json" in ct:
                return resp.json()
            return resp.text

        return await loop.run_in_executor(None, _sync_get)

# ============================================================
# 事件类型枚举
# ============================================================


class EventType(str, Enum):
    """事件类型分类"""

    EARNINGS = "earnings"  # 财报（年报/季报）
    CONTRACT = "contract"  # 中标/合同/订单
    BUYBACK = "buyback"  # 回购
    REDUCE = "reduce"  # 减持
    INCREASE = "increase"  # 增持
    RESTRUCTURE = "restructure"  # 资产重组
    DELIST_RISK = "delist_risk"  # 退市风险
    SUSPENSION = "suspension"  # 停复牌
    INVESTOR_QA = "investor_qa"  # 互动易问答
    DIVIDEND = "dividend"  # 分红
    REGULATORY = "regulatory"  # 监管处罚
    POLICY = "policy"  # 行业/政策
    PRICE_ANOMALY = "price_anomaly"  # 股价异动
    OTHER = "other"  # 其他


# ============================================================
# 数据结构
# ============================================================


@dataclass
class StockEvent:
    """一条股票事件"""

    code: str
    type: EventType
    title: str
    content: str
    source: str  # 公告/互动易
    event_time: float  # unix timestamp
    url: str = ""
    importance: int = 5  # 1-10
    stock_name: str = ""

    @property
    def fingerprint(self) -> str:
        """生成去重指纹"""
        raw = f"{self.code}|{self.type}|{self.title}|{self.event_time}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    @property
    def is_high_importance(self) -> bool:
        return self.importance >= DEFAULT_IMPORTANCE_THRESHOLD

    @property
    def is_medium_importance(self) -> bool:
        return DEFAULT_BRIEF_IMPORTANCE_LOWER <= self.importance < DEFAULT_IMPORTANCE_THRESHOLD

    @property
    def is_low_importance(self) -> bool:
        return self.importance <= DEFAULT_LOW_IMPORTANCE_UPPER


# ============================================================
# 事件分类器
# ============================================================


class EventClassifier:
    """根据标题/内容分类事件并评估重要性"""

    # 关键词 → (EventType, 基础重要性)
    KEYWORD_MAP: list[tuple[str, EventType, int]] = [
        # 财报
        ("年度报告", EventType.EARNINGS, 8),
        ("年报", EventType.EARNINGS, 8),
        ("半年度报告", EventType.EARNINGS, 8),
        ("半年报", EventType.EARNINGS, 8),
        ("季度报告", EventType.EARNINGS, 7),
        ("一季报", EventType.EARNINGS, 7),
        ("三季报", EventType.EARNINGS, 7),
        ("业绩预告", EventType.EARNINGS, 8),
        ("业绩快报", EventType.EARNINGS, 8),
        ("业绩修正", EventType.EARNINGS, 9),
        ("净利润", EventType.EARNINGS, 7),
        # 中标/合同
        ("中标", EventType.CONTRACT, 7),
        ("合同", EventType.CONTRACT, 6),
        ("订单", EventType.CONTRACT, 6),
        ("签署", EventType.CONTRACT, 5),
        ("重大项目", EventType.CONTRACT, 7),
        ("战略合作", EventType.CONTRACT, 6),
        # 回购
        ("回购", EventType.BUYBACK, 6),
        ("股份回购", EventType.BUYBACK, 7),
        ("回购计划", EventType.BUYBACK, 7),
        # 减持
        ("减持", EventType.REDUCE, 7),
        ("减持计划", EventType.REDUCE, 7),
        ("减持公告", EventType.REDUCE, 7),
        ("股份减持", EventType.REDUCE, 7),
        # 增持
        ("增持", EventType.INCREASE, 7),
        ("增持计划", EventType.INCREASE, 7),
        # 重组
        ("资产重组", EventType.RESTRUCTURE, 9),
        ("重大资产重组", EventType.RESTRUCTURE, 9),
        ("重组", EventType.RESTRUCTURE, 8),
        ("并购", EventType.RESTRUCTURE, 8),
        ("收购", EventType.RESTRUCTURE, 8),
        ("借壳", EventType.RESTRUCTURE, 9),
        ("注入", EventType.RESTRUCTURE, 8),
        # 退市风险
        ("退市风险", EventType.DELIST_RISK, 9),
        ("ST", EventType.DELIST_RISK, 8),
        ("暂停上市", EventType.DELIST_RISK, 9),
        ("终止上市", EventType.DELIST_RISK, 9),
        # 停复牌
        ("停牌", EventType.SUSPENSION, 7),
        ("复牌", EventType.SUSPENSION, 7),
        ("停复牌", EventType.SUSPENSION, 7),
        # 分红
        ("分红", EventType.DIVIDEND, 5),
        ("利润分配", EventType.DIVIDEND, 5),
        ("送转", EventType.DIVIDEND, 5),
        ("除权", EventType.DIVIDEND, 4),
        # 监管
        ("监管函", EventType.REGULATORY, 6),
        ("处罚", EventType.REGULATORY, 7),
        ("警示", EventType.REGULATORY, 5),
        ("立案", EventType.REGULATORY, 8),
        ("调查", EventType.REGULATORY, 8),
        ("通报批评", EventType.REGULATORY, 6),
        ("公开谴责", EventType.REGULATORY, 7),
        # 政策/行业
        ("政策", EventType.POLICY, 5),
        ("补贴", EventType.POLICY, 5),
        ("规划", EventType.POLICY, 4),
        # 股价异动
        ("异常波动", EventType.PRICE_ANOMALY, 6),
        ("异动", EventType.PRICE_ANOMALY, 5),
    ]

    # 互动易关键词 → (EventType, 基础重要性)
    QA_KEYWORD_MAP: list[tuple[str, EventType, int]] = [
        ("订单", EventType.INVESTOR_QA, 6),
        ("经营情况", EventType.INVESTOR_QA, 5),
        ("利润", EventType.INVESTOR_QA, 6),
        ("回购", EventType.INVESTOR_QA, 7),
        ("重组", EventType.INVESTOR_QA, 8),
        ("风险", EventType.INVESTOR_QA, 5),
        ("中标", EventType.INVESTOR_QA, 7),
        ("合同", EventType.INVESTOR_QA, 6),
        ("减产", EventType.INVESTOR_QA, 6),
        ("停产", EventType.INVESTOR_QA, 7),
        ("复产", EventType.INVESTOR_QA, 6),
        ("分红", EventType.INVESTOR_QA, 5),
        ("转板", EventType.INVESTOR_QA, 7),
        ("退市", EventType.INVESTOR_QA, 9),
        ("处罚", EventType.INVESTOR_QA, 7),
        ("诉讼", EventType.INVESTOR_QA, 6),
        ("资产", EventType.INVESTOR_QA, 5),
        ("业绩", EventType.INVESTOR_QA, 6),
        ("股东", EventType.INVESTOR_QA, 5),
    ]

    @classmethod
    def classify_announcement(cls, title: str) -> tuple[EventType, int]:
        """根据公告标题分类"""
        title_lower = title.lower()
        for keyword, event_type, base_importance in cls.KEYWORD_MAP:
            if keyword.lower() in title_lower:
                return event_type, base_importance
        return EventType.OTHER, 3

    @classmethod
    def classify_qa(cls, question: str, answer: str) -> tuple[EventType, int]:
        """根据互动易问答内容分类"""
        combined = f"{question} {answer}".lower()
        for keyword, event_type, base_importance in cls.QA_KEYWORD_MAP:
            if keyword.lower() in combined:
                return event_type, base_importance
        return EventType.INVESTOR_QA, 3

    @classmethod
    def adjust_importance(cls, event_type: EventType, base_importance: int,
                          title: str, content: str) -> int:
        """根据额外信号调重要性"""
        combined = f"{title} {content}".lower()
        score = base_importance

        # 正面增强信号
        if any(k in combined for k in ["大幅", "显著", "重大", "历史新高", "突破"]):
            score += 1
        if any(k in combined for k in ["超预期", "同比增长", "环比增长"]):
            score += 1
        if "首次" in combined:
            score += 1

        # 负面增强信号
        if any(k in combined for k in ["亏损", "下滑", "下降", "跌"]):
            score += 1
        if "风险" in combined:
            score += 1

        # 范围限制
        return max(1, min(10, score))


# ============================================================
# 事件源抓取器
# ============================================================


class EastMoneyAnnounceFetcher:
    """东方财富公告API抓取（备选回落源）"""

    async def fetch(self, stock_code: str, page_size: int = 5) -> list[dict[str, Any]]:
        """获取股票近期公告"""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Content-Type": "application/json",
                "Referer": "https://data.eastmoney.com/",
            }
            data = await _async_post(
                EASTMONEY_ANNOUNCE_API,
                json_data={
                    "code": stock_code,
                    "page": 1,
                    "pageSize": page_size,
                },
                headers=headers,
            )
            return data.get("data", []) or data.get("list", [])
        except Exception as exc:
            logger.debug("东方财富公告抓取失败 [%s]: %s", stock_code, exc)
            return []

    @staticmethod
    def parse_announcement(item: dict[str, Any], stock_code: str) -> StockEvent | None:
        """将原始公告数据解析为 StockEvent"""
        title = item.get("title") or item.get("announcementTitle") or ""
        if not title:
            return None
        date_str = item.get("noticeDate") or item.get("announcementDate") or item.get("date") or ""
        try:
            event_time = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").timestamp()
        except (ValueError, TypeError):
            event_time = time.time()
        url = item.get("url") or item.get("artCode", "")
        if url and not url.startswith("http"):
            url = f"https://data.eastmoney.com/notices/detail/{url}"
        event_type, base_importance = EventClassifier.classify_announcement(title)
        content = item.get("content") or item.get("summary") or title
        importance = EventClassifier.adjust_importance(event_type, base_importance, title, content)
        return StockEvent(
            code=stock_code,
            type=event_type,
            title=title.strip(),
            content=str(content)[:500],
            source="公告",
            event_time=event_time,
            url=url,
            importance=importance,
            stock_name=item.get("stockName", ""),
        )


class CninfoFetcher:
    """巨潮资讯网公告抓取（主源）"""

    async def fetch(self, stock_code: str, page_size: int = 5) -> list[dict[str, Any]]:
        """通过巨潮搜索API获取公告"""
        try:
            data = await _async_post(
                CNINFO_SEARCH_API,
                data={
                    "searchkey": stock_code,
                    "sdate": "",
                    "edate": "",
                    "isfulltext": "false",
                    "sortName": "pubdate",
                    "sortType": "desc",
                    "pageNum": 1,
                    "pageSize": page_size,
                },
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": "http://www.cninfo.com.cn/",
                },
            )
            return data.get("announcements", []) or data.get("records", [])
        except Exception as exc:
            logger.warning("巨潮资讯抓取失败 [%s]: %s", stock_code, exc)
            return []

    @staticmethod
    def parse_announcement(item: dict[str, Any], stock_code: str) -> StockEvent | None:
        title = item.get("announcementTitle") or item.get("title") or ""
        if not title:
            return None

        # announcementTime is Unix timestamp in milliseconds
        ann_time_ms = item.get("announcementTime")
        if ann_time_ms and isinstance(ann_time_ms, (int, float)) and ann_time_ms > 0:
            event_time = ann_time_ms / 1000.0
        else:
            # Fallback: try string date fields
            date_str = item.get("announcementDate") or item.get("pubdate") or ""
            try:
                event_time = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").timestamp()
            except (ValueError, TypeError):
                event_time = time.time()

        event_type, base_importance = EventClassifier.classify_announcement(title)
        content = item.get("content") or title
        importance = EventClassifier.adjust_importance(event_type, base_importance, title, content)

        adjunct_url = item.get("adjunctUrl", "")
        if adjunct_url and not adjunct_url.startswith("http"):
            adjunct_url = CNINFO_BASE_URL + adjunct_url.lstrip("/")

        return StockEvent(
            code=stock_code,
            type=event_type,
            title=title.strip(),
            content=str(content)[:500],
            source="公告",
            event_time=event_time,
            url=adjunct_url,
            importance=importance,
            stock_name=item.get("secName", item.get("stockName", "")),
        )


class SecEdgarFetcher:
    """SEC EDGAR 美股公告抓取（免费，无需 API Key）"""

    _CIK_CACHE: dict[str, int] | None = None
    _LAST_CIK_FETCH: float = 0
    _CIK_CACHE_TTL = 86400

    @classmethod
    async def _load_cik_map(cls) -> dict[str, int]:
        now = time.time()
        if cls._CIK_CACHE is not None and now - cls._LAST_CIK_FETCH < cls._CIK_CACHE_TTL:
            return cls._CIK_CACHE
        try:
            headers = {"User-Agent": "MindLynx-Aistock/1.0 (bluekuma@mindlynx.top)"}
            async with httpx.AsyncClient() as client:
                resp = await client.get("https://www.sec.gov/files/company_tickers.json", headers=headers, timeout=10)
                if resp.status_code != 200:
                    return cls._CIK_CACHE or {}
                data = resp.json()
            mapping = {}
            for entry in data.values():
                ticker = entry.get("ticker", "").upper()
                cik = entry.get("cik_str")
                if ticker and cik:
                    mapping[ticker] = int(cik)
            cls._CIK_CACHE = mapping
            cls._LAST_CIK_FETCH = now
            logger.info("[SEC EDGAR] 已加载 %d 个 CIK 映射", len(mapping))
            return mapping
        except Exception as exc:
            logger.warning("[SEC EDGAR] 加载 CIK 映射失败: %s", exc)
            return cls._CIK_CACHE or {}

    async def fetch(self, stock_code: str, page_size: int = 5) -> list[dict[str, Any]]:
        ticker = stock_code.upper().strip()
        cik_map = await self._load_cik_map()
        cik = cik_map.get(ticker)
        if not cik:
            return []
        try:
            padded = str(cik).zfill(10)
            headers = {"User-Agent": "MindLynx-Aistock/1.0 (bluekuma@mindlynx.top)"}
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"https://data.sec.gov/submissions/CIK{padded}.json", headers=headers, timeout=10)
                if resp.status_code != 200:
                    return []
                data = resp.json()
            filings = data.get("filings", {}).get("recent", {})
            if not filings:
                return []
            n = min(page_size, len(filings.get("form", [])))
            return [{
                "form": filings["form"][i],
                "filingDate": filings["filingDate"][i],
                "primaryDocument": filings.get("primaryDocument", [""])[i] if i < len(filings.get("primaryDocument", [])) else "",
                "primaryDocDescription": filings.get("primaryDocDescription", [""])[i] if i < len(filings.get("primaryDocDescription", [])) else "",
                "cik": cik,
            } for i in range(n)]
        except Exception as exc:
            logger.debug("[SEC EDGAR] %s 抓取失败: %s", ticker, exc)
            return []

    @staticmethod
    def parse_announcement(item: dict[str, Any], stock_code: str) -> StockEvent | None:
        form = item.get("form", "")
        title = item.get("primaryDocDescription", "") or form
        if not title:
            return None
        date_str = item.get("filingDate", "")
        try:
            event_time = datetime.strptime(date_str, "%Y-%m-%d").timestamp()
        except (ValueError, TypeError):
            event_time = time.time()
        cik, doc = item.get("cik", ""), item.get("primaryDocument", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{doc.replace('.txt', '')}/{doc}" if cik and doc else ""
        form_upper = form.upper()
        etype, imp = EventType.OTHER, 4
        if form_upper in ("10-K", "20-F", "40-F"): etype, imp = EventType.EARNINGS, 9
        elif form_upper in ("10-Q", "6-K"): etype, imp = EventType.EARNINGS, 7
        elif form_upper == "8-K": etype, imp = EventType.OTHER, 8
        elif form_upper in ("S-1", "S-3", "S-4"): etype, imp = EventType.OTHER, 7
        elif form_upper in ("3", "4", "5"): etype, imp = EventType.OTHER, 6
        elif form_upper in ("13F", "13F-NT"): etype, imp = EventType.OTHER, 5
        return StockEvent(code=stock_code, type=etype,
            title=f"[{form}] {title[:80]}".strip(),
            content=f"SEC {form} ({date_str})",
            source="SEC EDGAR", event_time=event_time,
            url=url, importance=imp,
        )


class InteractionQAFetcher:
    """互动易/投资者问答抓取

    使用东方财富/全景网投资者问答API。
    注：免费接口可能变化，提供了降级抓取逻辑。
    """

    async def fetch(self, stock_code: str, page_size: int = 10) -> list[StockEvent]:
        """获取投资者问答"""
        results: list[StockEvent] = []

        # 尝试多个问答源
        fetchers = [
            self._fetch_from_eastmoney,
            self._fetch_from_p5w,
        ]
        for fetcher in fetchers:
            try:
                items = await fetcher(stock_code, page_size)
                results.extend(items)
            except Exception as exc:
                logger.debug("互动易源失败 [%s]: %s", stock_code, exc)

        if not results:
            # 尝试使用搜索服务抓取
            try:
                results = await self._fetch_from_search(stock_code, page_size)
            except Exception as exc:
                logger.debug("互动易搜索降级失败 [%s]: %s", stock_code, exc)

        return results

    async def _fetch_from_eastmoney(
        self, stock_code: str, page_size: int
    ) -> list[StockEvent]:
        """东方财富互动问答"""
        events: list[StockEvent] = []
        url = (
            f"https://so.eastmoney.com/interact/api/getData"
            f"?keyword={stock_code}&page=1&pageSize={page_size}"
        )
        result = await _async_get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://so.eastmoney.com/",
            },
        )
        if isinstance(result, str):
            return events

        items = result.get("data", []) or result.get("list", []) or result.get("result", [])
        if not isinstance(items, list):
            items = []

        for item in items:
            question = item.get("question") or item.get("title", "")
            answer = item.get("answer") or item.get("content", "")
            if not question:
                continue

            event_type, base_imp = EventClassifier.classify_qa(question, answer)
            importance = EventClassifier.adjust_importance(
                event_type, base_imp, question, answer
            )

            date_str = item.get("date") or item.get("time") or ""
            try:
                event_time = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").timestamp()
            except (ValueError, TypeError):
                event_time = time.time()

            events.append(StockEvent(
                code=stock_code,
                type=event_type,
                title=question.strip()[:100],
                content=f"答: {answer.strip()[:400]}" if answer else question.strip()[:400],
                source="互动易",
                event_time=event_time,
                url=item.get("url", ""),
                importance=importance,
            ))

        return events

    async def _fetch_from_p5w(
        self, stock_code: str, page_size: int
    ) -> list[StockEvent]:
        """全景网互动平台"""
        events: list[StockEvent] = []
        data = await _async_post(
            HUDONGYI_SEARCH_API,
            json_data={
                "stockCode": stock_code,
                "pageIndex": 1,
                "pageSize": page_size,
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/json",
                "Referer": "https://ir.p5w.net/",
            },
        )
        items = data.get("data", []) or data.get("list", []) or data.get("result", [])
        if not isinstance(items, list):
            items = []

        for item in items:
            question = item.get("questionContent") or item.get("question", "")
            answer = item.get("answerContent") or item.get("answer", "")
            if not question:
                continue

            event_type, base_imp = EventClassifier.classify_qa(question, answer)
            importance = EventClassifier.adjust_importance(
                event_type, base_imp, question, answer
            )

            date_str = item.get("questionDate") or item.get("createDate", "")
            try:
                event_time = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").timestamp()
            except (ValueError, TypeError):
                event_time = time.time()

            events.append(StockEvent(
                code=stock_code,
                type=event_type,
                title=question.strip()[:100],
                content=f"答: {answer.strip()[:400]}" if answer else question.strip()[:400],
                source="互动易",
                event_time=event_time,
                url=item.get("url", ""),
                importance=importance,
            ))

        return events

    async def _fetch_from_search(
        self, stock_code: str, page_size: int
    ) -> list[StockEvent]:
        """通过搜索引擎降级抓取互动易内容（备用方案，主源失败时使用）"""
        events: list[StockEvent] = []
        search_query = f"{stock_code} 互动易 投资者问答"

        try:
            url = f"https://cn.bing.com/search?q={search_query}&count={page_size}"
            result = await _async_get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if isinstance(result, dict):
                return events

            html = result
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            # Bing 搜索结果常见结构: h2 > a (标题链接)
            for link in soup.select("h2 a"):
                href = str(link.get("href", "") or "")
                title = link.get_text(strip=True)
                if not href or not title:
                    continue
                if "互动" in title or stock_code in title:
                    events.append(StockEvent(
                        code=stock_code,
                        type=EventType.INVESTOR_QA,
                        title=title.strip()[:100],
                        content=title.strip()[:400],
                        source="互动易(搜索)",
                        event_time=time.time(),
                        url=href,
                        importance=3,
                    ))
                    if len(events) >= page_size:
                        break
        except Exception as exc:
            logger.debug("互动易搜索降级失败: %s", exc)

        return events


# ============================================================
# 事件过滤器
# ============================================================


class EventFilter:
    """事件过滤：去重 + 低重要性丢弃，指纹持久化防止重启丢失"""

    _PERSIST_PATH = os.path.join(os.path.expanduser("~"), ".hermes", "data", "event_fingerprints.json")

    def __init__(self, dedup_ttl: float = DEFAULT_DEDUP_TTL):
        self._seen_fingerprints: dict[str, float] = {}
        self.dedup_ttl = dedup_ttl
        self._load_persisted()

    def _load_persisted(self) -> None:
        try:
            if os.path.exists(self._PERSIST_PATH):
                with open(self._PERSIST_PATH) as f:
                    data = json.load(f)
                    now = time.time()
                    for fp, ts in data.items():
                        if now - ts < self.dedup_ttl:
                            self._seen_fingerprints[fp] = ts
                logger.debug("Loaded %d fingerprints from persistence", len(self._seen_fingerprints))
        except Exception:
            pass

    def _save_persisted(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._PERSIST_PATH), exist_ok=True)
            with open(self._PERSIST_PATH, "w") as f:
                json.dump(self._seen_fingerprints, f)
        except Exception:
            pass

    def is_new(self, event: StockEvent) -> bool:
        """检查事件是否未见过"""
        fp = event.fingerprint
        now = time.time()
        # 清理过期指纹
        stale_keys = [k for k, t in self._seen_fingerprints.items() if now - t > self.dedup_ttl]
        for k in stale_keys:
            self._seen_fingerprints.pop(k, None)
        if fp in self._seen_fingerprints:
            return False
        self._seen_fingerprints[fp] = now
        # 每次有新事件都持久化，防止重启后重复推送
        self._save_persisted()
        return True

    def should_keep(self, event: StockEvent) -> bool:
        """是否保留该事件（过滤垃圾）"""
        # 移除低重要性事件
        if event.is_low_importance:
            logger.debug("忽略低重要性事件: %s [%s]", event.title[:50], event.importance)
            return False

        # 如果是其他类型且重要性较低，也丢弃
        if event.type == EventType.OTHER and event.importance < 5:
            return False

        # 去重
        if not self.is_new(event):
            logger.debug("重复事件已过滤: %s", event.title[:50])
            return False

        return True

    def reset(self) -> None:
        """重置去重缓存"""
        self._seen_fingerprints.clear()


# ============================================================
# 事件监视器核心
# ============================================================


class EventMonitor:
    """
    事件驱动分析服务

    定期扫描股票的事件源（公告、互动易），分类过滤，
    高重要性事件自动触发重新分析，中等重要性推送简报。
    """

    def __init__(
        self,
        *,
        stock_codes: list[str] | None = None,
        importance_threshold: int = DEFAULT_IMPORTANCE_THRESHOLD,
        check_interval: int = DEFAULT_CHECK_INTERVAL,
        analyze_callback: Callable[[str], None] | None = None,
        notify_callback: Callable[[str], None] | None = None,
        event_filter: EventFilter | None = None,
    ):
        self.stock_codes = stock_codes or []
        self.importance_threshold = importance_threshold
        self.check_interval = max(30, check_interval)
        self.analyze_callback = analyze_callback
        self.notify_callback = notify_callback
        self.filter = event_filter or EventFilter()

        # 抓取器（A股: cninfo, 东财; 美股: SEC EDGAR）
        self.announce_fetcher = CninfoFetcher()
        self.eastmoney_fetcher = EastMoneyAnnounceFetcher()
        self.qa_fetcher = InteractionQAFetcher()
        self.edgar_fetcher = SecEdgarFetcher()

        # 统计
        self.stats = {
            "total_checks": 0,
            "total_events": 0,
            "high_importance": 0,
            "medium_importance": 0,
            "low_importance": 0,
            "reanalyses_triggered": 0,
            "notifications_sent": 0,
            "errors": 0,
        }

        self._running = False
        self._session_id = ""

    def set_stock_codes(self, codes: list[str]) -> None:
        """更新监控的股票列表"""
        self.stock_codes = codes

    async def check_once(self) -> list[StockEvent]:
        """执行一次事件检查"""
        self.stats["total_checks"] += 1
        all_events: list[StockEvent] = []
        triggered: list[StockEvent] = []

        if not self.stock_codes:
            logger.info("EventMonitor: 没有配置监控股票，跳过检查")
            return triggered

        logger.info("EventMonitor: 开始检查 %d 只股票的事件...", len(self.stock_codes))

        for code in self.stock_codes:
            try:
                events = await self._check_stock(code)
                all_events.extend(events)
            except Exception as exc:
                logger.warning("EventMonitor: 检查[%s]失败: %s", code, exc)
                self.stats["errors"] += 1

        # 处理事件
        for event in all_events:
            if not self.filter.should_keep(event):
                self.stats["low_importance"] += 1
                continue

            # 严格执行重要性阈值：低于阈值的事件全部跳过（不推送、不触发分析）
            if event.importance < self.importance_threshold:
                self.stats["low_importance"] += 1
                logger.debug(
                    "EventMonitor: 重要性%d低于阈值%d, 跳过 %s",
                    event.importance, self.importance_threshold, event.title[:50]
                )
                continue

            if event.is_high_importance:
                self.stats["high_importance"] += 1
                triggered.append(event)
                self._handle_high_importance(event)
            elif event.is_medium_importance:
                self.stats["medium_importance"] += 1
                self._handle_medium_importance(event)

        self.stats["total_events"] += len(triggered)
        logger.info(
            "EventMonitor: 检查完成, %d 高重要性, %d 中等重要性, %d 已过滤",
            self.stats["high_importance"],
            self.stats["medium_importance"],
            self.stats["low_importance"],
        )

        # ── 写入数据湖 (零侵入: ImportError 时跳过) ──
        if all_events:
            try:
                from services.data_warehouse.storage import DataLake
                lake = DataLake()
                news_items = []
                for ev in all_events:
                    news_items.append({
                        "stock_code": ev.code,
                        "title": ev.title[:200],
                        "url": ev.url,
                        "summary": ev.content[:200],
                        "source": ev.source,
                        "category": ev.type.value if ev.type else "事件",
                        "importance": min(3, ev.importance // 3),
                        "published_at": datetime.fromtimestamp(ev.event_time).strftime("%Y-%m-%d %H:%M") if ev.event_time else "",
                    })
                if news_items:
                    lake.insert_news(news_items)
                    logger.debug("EventMonitor: 写入 %d 条事件到数据湖", len(news_items))
            except ImportError:
                pass
            except Exception as exc:
                logger.debug("EventMonitor: 数据湖写入失败: %s", exc)

        return triggered

    async def _check_stock(self, code: str) -> list[StockEvent]:
        """检查单只股票的所有事件源"""
        events: list[StockEvent] = []

        # 1. 巨潮资讯公告（主源）
        try:
            announcements = await self.announce_fetcher.fetch(code)
            for item in announcements:
                event = CninfoFetcher.parse_announcement(item, code)
                if event:
                    events.append(event)
        except Exception as exc:
            logger.warning("EventMonitor: 巨潮资讯[%s]失败: %s", code, exc)
            announcements = []

        # 2. 东方财富公告（回落：主源无数据时启用）
        if not announcements:
            try:
                em_items = await self.eastmoney_fetcher.fetch(code)
                for item in em_items:
                    event = EastMoneyAnnounceFetcher.parse_announcement(item, code)
                    if event:
                        events.append(event)
            except Exception as exc:
                logger.debug("EventMonitor: 东方财富公告[%s]失败: %s", code, exc)

        # 3. 互动易问答
        try:
            qa_events = await self.qa_fetcher.fetch(code)
            events.extend(qa_events)
        except Exception as exc:
            logger.warning("EventMonitor: 互动易[%s]失败: %s", code, exc)

        # 4. 美股 SEC EDGAR 公告
        from data_provider.us_index_mapping import is_us_stock_code
        if is_us_stock_code(code):
            try:
                sec_items = await self.edgar_fetcher.fetch(code)
                for item in sec_items:
                    event = SecEdgarFetcher.parse_announcement(item, code)
                    if event:
                        events.append(event)
            except Exception as exc:
                logger.debug("EventMonitor: SEC EDGAR[%s]失败: %s", code, exc)

        return events

    def _handle_high_importance(self, event: StockEvent) -> None:
        """处理高重要性事件 → 触发重新分析"""
        label = f"[{event.code}] {event.type.value}: {event.title[:60]}"
        logger.info("EventMonitor: 高重要性事件触发重新分析: %s", label)

        if self.analyze_callback:
            try:
                self.analyze_callback(event.code)
                self.stats["reanalyses_triggered"] += 1
            except Exception as exc:
                logger.error("EventMonitor: 触发分析回调失败 [%s]: %s", event.code, exc)
        else:
            logger.info("EventMonitor: 无分析回调配置，仅记录事件")

    def _handle_medium_importance(self, event: StockEvent) -> None:
        """处理中重要性事件 → 推送简报"""
        label = f"[{event.code}] {event.type.value}: {event.title[:60]}"
        logger.info("EventMonitor: 中重要性事件简报: %s", label)

        if self.notify_callback:
            try:
                self.notify_callback(self._format_brief(event))
                self.stats["notifications_sent"] += 1
            except Exception as exc:
                logger.error("EventMonitor: 简报推送失败 [%s]: %s", event.code, exc)

    @staticmethod
    def _format_brief(event: StockEvent) -> str:
        """格式化简报消息（移动端紧凑格式）"""
        import html as _html
        import re as _re

        emoji_map = {
            EventType.EARNINGS: "📊", EventType.CONTRACT: "📋", EventType.BUYBACK: "🔄",
            EventType.REDUCE: "⬇️", EventType.INCREASE: "⬆️", EventType.RESTRUCTURE: "🔄",
            EventType.DELIST_RISK: "⚠️", EventType.SUSPENSION: "⏸️", EventType.INVESTOR_QA: "💬",
            EventType.DIVIDEND: "💰", EventType.REGULATORY: "🔍", EventType.POLICY: "📜",
            EventType.PRICE_ANOMALY: "📈", EventType.OTHER: "📌",
        }
        # 事件类型中文名
        type_cn = {
            EventType.EARNINGS: "业绩", EventType.CONTRACT: "合同", EventType.BUYBACK: "回购",
            EventType.REDUCE: "减持", EventType.INCREASE: "增持", EventType.RESTRUCTURE: "重组",
            EventType.DELIST_RISK: "退市", EventType.SUSPENSION: "停牌", EventType.INVESTOR_QA: "互动",
            EventType.DIVIDEND: "分红", EventType.REGULATORY: "监管", EventType.POLICY: "政策",
            EventType.PRICE_ANOMALY: "异动", EventType.OTHER: "其他",
        }
        emoji = emoji_map.get(event.type, "📌")
        code = event.code
        name = event.stock_name or code

        # 解码HTML实体并剥离标签
        raw_title = _html.unescape(event.title or "")
        title = _re.sub(r"<[^>]+>", "", raw_title).strip()[:60]

        # 清理标题中重复的名称前缀
        if name and title.startswith(name):
            title = title[len(name):].strip().lstrip("，,、：:")
        if title.startswith(code):
            title = title[len(code):].strip().lstrip("，,、：:")

        # 巨潮公告PDF链接有反爬限制，跳过
        url = event.url or ""
        if url and url.startswith("http") and "cninfo" not in url:
            url_part = f" | {url}"
        else:
            url_part = ""

        label = type_cn.get(event.type, event.type.value)
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%H:%M")
        return f"{emoji} {ts} {name}: {title} [{label} 重要性{event.importance}]{url_part}"

    async def run_forever(
        self,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """循环运行事件监视器"""
        self._running = True
        self._session_id = uuid.uuid4().hex[:8]
        logger.info(
            "EventMonitor[%s] 启动: %d 只股票, 检查间隔 %ds, 重要性阈值 %d",
            self._session_id,
            len(self.stock_codes),
            self.check_interval,
            self.importance_threshold,
        )

        try:
            while self._running:
                if stop_event and stop_event.is_set():
                    logger.info("EventMonitor[%s]: 收到停止信号", self._session_id)
                    break

                await self.check_once()

                # 等待间隔或停止信号
                if stop_event:
                    try:
                        await asyncio.wait_for(
                            stop_event.wait(), timeout=self.check_interval
                        )
                        break  # stop_event was set
                    except asyncio.TimeoutError:
                        pass  # 正常间隔结束
                else:
                    await asyncio.sleep(self.check_interval)

        except asyncio.CancelledError:
            logger.info("EventMonitor[%s]: 任务取消", self._session_id)
        finally:
            self._running = False
            logger.info("EventMonitor[%s]: 停止", self._session_id)

    def stop(self) -> None:
        """停止事件监视器"""
        self._running = False

    def get_stats(self) -> dict[str, int]:
        """获取运行统计"""
        return dict(self.stats)

    def reset_stats(self) -> None:
        """重置统计"""
        for key in self.stats:
            self.stats[key] = 0


# ============================================================
# 便捷函数：创建带完整依赖的 EventMonitor
# ============================================================


def create_event_monitor(
    stock_codes: list[str],
    *,
    importance_threshold: int = DEFAULT_IMPORTANCE_THRESHOLD,
    check_interval: int = DEFAULT_CHECK_INTERVAL,
    notify_service: Any = None,
    pipeline: Any = None,
    config: Any = None,
) -> EventMonitor:
    """创建预配置的 EventMonitor 实例

    Args:
        stock_codes: 监控的股票代码列表
        importance_threshold: 触发重新分析的重要性阈值
        check_interval: 检查间隔（秒）
        notify_service: NotificationService 实例
        pipeline: StockAnalysisPipeline 实例
        config: Config 实例

    Returns:
        配置好的 EventMonitor 实例
    """

    def _on_high_importance(code: str) -> None:
        """高重要性事件回调：触发重新分析"""
        if pipeline is not None:
            try:
                from src.enums import ReportType

                query_id = uuid.uuid4().hex[:12]
                logger.info("EventMonitor: 触发[%s]重新分析...", code)
                result = pipeline.analyze_stock(
                    code=code,
                    report_type=ReportType.FULL,
                    query_id=query_id,
                )
                if result and notify_service:
                    notify_service.send(
                        f"🔔 *事件驱动分析* [{result.name}]({code})\n\n{result.operation_advice}",
                        route_type="alert",
                    )
                    logger.info("EventMonitor: [%s]分析报告已推送", code)
            except Exception as exc:
                logger.error("EventMonitor: [%s]重新分析失败: %s", code, exc)
        elif notify_service:
            notify_service.send(
                f"⚠️ *高重要性事件*\n股票: {code}\n请手动运行分析",
                route_type="alert",
            )

    def _on_brief(content: str) -> None:
        """中重要性事件回调：推送简报"""
        if notify_service is not None:
            try:
                notify_service.send(content, route_type="alert")
            except Exception as exc:
                logger.warning("EventMonitor: 简报推送失败: %s", exc)

    return EventMonitor(
        stock_codes=stock_codes,
        importance_threshold=importance_threshold,
        check_interval=check_interval,
        analyze_callback=_on_high_importance if pipeline else None,
        notify_callback=_on_brief if notify_service else None,
    )


# ============================================================
# CLI 入口点
# ============================================================


async def run_event_monitor_cli(
    stock_codes: list[str],
    config: Any = None,
    daemon: bool = True,
) -> None:
    """作为 CLI 命令运行的入口"""
    if config is None:
        from src.config import get_config
        config = get_config()

    # 初始化服务和组件
    from src.core.pipeline import StockAnalysisPipeline
    from src.enums import ReportType
    from src.notification import NotificationService

    query_id = uuid.uuid4().hex[:12]
    pipeline = StockAnalysisPipeline(
        config=config,
        query_id=query_id,
        query_source="event_monitor",
    )
    notifier = NotificationService()

    # 获取配置
    importance_threshold = getattr(
        config, "event_monitor_importance_threshold", DEFAULT_IMPORTANCE_THRESHOLD
    )
    check_interval = getattr(
        config, "event_monitor_check_interval", DEFAULT_CHECK_INTERVAL
    )

    monitor = create_event_monitor(
        stock_codes=stock_codes,
        importance_threshold=importance_threshold,
        check_interval=check_interval,
        notify_service=notifier,
        pipeline=pipeline,
        config=config,
    )

    if daemon:
        # 守护模式：持续运行
        stop_event = asyncio.Event()

        try:
            await monitor.run_forever(stop_event=stop_event)
        except KeyboardInterrupt:
            logger.info("EventMonitor: 用户中断")
            stop_event.set()
        finally:
            stats = monitor.get_stats()
            logger.info(
                "EventMonitor 运行统计: 检查=%d, 高重要性=%d, 中重要性=%d, "
                "重新分析=%d, 通知=%d, 错误=%d",
                stats["total_checks"],
                stats["high_importance"],
                stats["medium_importance"],
                stats["reanalyses_triggered"],
                stats["notifications_sent"],
                stats["errors"],
            )
    else:
        # 单次模式
        await monitor.check_once()
        stats = monitor.get_stats()
        logger.info(
            "EventMonitor 单次检查: 高重要性=%d, 中重要性=%d",
            stats["high_importance"],
            stats["medium_importance"],
        )


# ═══════════════════════════════════════════════════════════════
# 阈值告警（统一至此文件，替代 agent/events.py）
# ═══════════════════════════════════════════════════════════════

class ThresholdAlertType(str, Enum):
    """阈值告警类型。"""
    PRICE_CROSS = "price_cross"
    PRICE_CHANGE_PERCENT = "price_change_percent"
    VOLUME_SPIKE = "volume_spike"
    SENTIMENT_SHIFT = "sentiment_shift"
    RISK_FLAG = "risk_flag"
    CUSTOM = "custom"


class ThresholdAlertStatus(str, Enum):
    ACTIVE = "active"
    TRIGGERED = "triggered"
    EXPIRED = "expired"
    DISMISSED = "dismissed"


_RUNTIME_SUPPORTED_THRESHOLD_TYPES = frozenset({
    ThresholdAlertType.PRICE_CROSS,
    ThresholdAlertType.PRICE_CHANGE_PERCENT,
    ThresholdAlertType.VOLUME_SPIKE,
})


def _supported_threshold_type_names() -> str:
    return ", ".join(sorted(t.value for t in _RUNTIME_SUPPORTED_THRESHOLD_TYPES))


def _ensure_supported_threshold_type(alert_type: ThresholdAlertType) -> None:
    if alert_type not in _RUNTIME_SUPPORTED_THRESHOLD_TYPES:
        raise ValueError(
            f"unsupported threshold alert type: {alert_type.value} "
            f"(supported: {_supported_threshold_type_names()})"
        )


def _read_quote_float(quote: Any, *field_names: str) -> float | None:
    """Read a numeric field from quote objects or dict-like payloads."""
    if quote is None:
        return None
    for field_name in field_names:
        raw_value = quote.get(field_name) if isinstance(quote, dict) else getattr(quote, field_name, None)
        if raw_value is None and hasattr(quote, "to_dict"):
            try:
                raw_value = quote.to_dict().get(field_name)
            except Exception:
                raw_value = None
        if raw_value is None:
            continue
        if isinstance(raw_value, str):
            raw_value = raw_value.strip().replace(",", "")
            if raw_value.endswith("%"):
                raw_value = raw_value[:-1].strip()
            if not raw_value:
                continue
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            continue
    return None


@dataclass
class ThresholdAlertRule:
    """阈值告警规则基类。"""
    stock_code: str
    alert_type: ThresholdAlertType
    description: str = ""
    status: ThresholdAlertStatus = ThresholdAlertStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    triggered_at: float | None = None
    ttl_hours: float = 24.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PriceThresholdAlert(ThresholdAlertRule):
    """价格穿越告警。"""
    alert_type: ThresholdAlertType = ThresholdAlertType.PRICE_CROSS
    direction: str = "above"
    price: float = 0.0

    def __post_init__(self):
        if not self.description:
            self.description = f"{self.stock_code} price {self.direction} {self.price}"


@dataclass
class PriceChangeThresholdAlert(ThresholdAlertRule):
    """涨跌幅告警。"""
    alert_type: ThresholdAlertType = ThresholdAlertType.PRICE_CHANGE_PERCENT
    direction: str = "up"
    change_pct: float = 3.0

    def __post_init__(self):
        if not self.description:
            self.description = f"{self.stock_code} change {self.direction} {self.change_pct}%"


@dataclass
class VolumeThresholdAlert(ThresholdAlertRule):
    """成交量异动告警。"""
    alert_type: ThresholdAlertType = ThresholdAlertType.VOLUME_SPIKE
    multiplier: float = 2.0

    def __post_init__(self):
        if not self.description:
            self.description = f"{self.stock_code} volume > {self.multiplier}× average"


@dataclass
class TriggeredThresholdAlert:
    """已触发的告警结果。"""
    rule: ThresholdAlertRule
    triggered_at: float = field(default_factory=time.time)
    current_value: Any = None
    message: str = ""


class ThresholdEventMonitor:
    """基于实时行情的阈值告警监控器。"""

    def __init__(self):
        self.rules: list[ThresholdAlertRule] = []
        self._callbacks: list[Callable[[TriggeredThresholdAlert], None]] = []

    def add_alert(self, rule: ThresholdAlertRule) -> None:
        _ensure_supported_threshold_type(rule.alert_type)
        self.rules.append(rule)
        logger.info("[ThresholdMonitor] Added alert: %s", rule.description)

    def remove_expired(self) -> int:
        now = time.time()
        before = len(self.rules)
        self.rules = [
            r for r in self.rules
            if r.status != ThresholdAlertStatus.EXPIRED
            and (now - r.created_at) < r.ttl_hours * 3600
        ]
        removed = before - len(self.rules)
        if removed:
            logger.info("[ThresholdMonitor] Removed %d expired alerts", removed)
        return removed

    def on_trigger(self, callback: Callable[[TriggeredThresholdAlert], None]) -> None:
        self._callbacks.append(callback)

    async def check_all(self) -> list[TriggeredThresholdAlert]:
        self.remove_expired()
        triggered: list[TriggeredThresholdAlert] = []
        for rule in self.rules:
            if rule.status != ThresholdAlertStatus.ACTIVE:
                continue
            try:
                result = await self._check_rule(rule)
                if result:
                    triggered.append(result)
                    rule.status = ThresholdAlertStatus.TRIGGERED
                    rule.triggered_at = time.time()
                    for cb in self._callbacks:
                        try:
                            if asyncio.iscoroutinefunction(cb):
                                await cb(result)
                            else:
                                await asyncio.to_thread(cb, result)
                        except Exception as exc:
                            logger.warning("[ThresholdMonitor] Callback error: %s", exc)
            except Exception as exc:
                logger.debug("[ThresholdMonitor] Check failed for %s: %s", rule.description, exc)
        return triggered

    async def _check_rule(self, rule: ThresholdAlertRule) -> TriggeredThresholdAlert | None:
        if isinstance(rule, PriceThresholdAlert):
            return await self._check_price(rule)
        elif isinstance(rule, PriceChangeThresholdAlert):
            return await self._check_price_change(rule)
        elif isinstance(rule, VolumeThresholdAlert):
            return await self._check_volume(rule)
        return None

    def _fetch_realtime_quote(self, stock_code: str) -> Any:
        from data_provider import DataFetcherManager
        return DataFetcherManager().get_realtime_quote(stock_code)

    async def _get_realtime_quote(self, stock_code: str) -> Any:
        return await asyncio.to_thread(self._fetch_realtime_quote, stock_code)

    async def _check_price(self, rule: PriceThresholdAlert) -> TriggeredThresholdAlert | None:
        try:
            quote = await self._get_realtime_quote(rule.stock_code)
            if quote is None:
                return None
            current_price = float(getattr(quote, "price", 0) or 0)
            if current_price <= 0:
                return None
            triggered = (rule.direction == "above" and current_price >= rule.price) or \
                        (rule.direction == "below" and current_price <= rule.price)
            if triggered:
                return TriggeredThresholdAlert(
                    rule=rule, current_value=current_price,
                    message=f"🔔 {rule.stock_code} price {rule.direction} {rule.price}: current = {current_price}",
                )
        except Exception as exc:
            logger.debug("[ThresholdMonitor] _check_price error: %s", exc)
        return None

    async def _check_price_change(self, rule: PriceChangeThresholdAlert) -> TriggeredThresholdAlert | None:
        try:
            quote = await self._get_realtime_quote(rule.stock_code)
            if quote is None:
                return None
            current_change_pct = _read_quote_float(quote, "change_pct", "change_percent", "pct_chg", "change_rate")
            if current_change_pct is None:
                return None
            threshold = abs(float(rule.change_pct))
            direction = rule.direction.lower()
            triggered = (direction == "up" and current_change_pct >= threshold) or \
                        (direction == "down" and current_change_pct <= -threshold)
            if triggered:
                return TriggeredThresholdAlert(
                    rule=rule, current_value=current_change_pct,
                    message=f"🔔 {rule.stock_code} change {direction} {threshold:.2f}%: current = {current_change_pct:+.2f}%",
                )
        except Exception as exc:
            logger.debug("[ThresholdMonitor] _check_price_change error: %s", exc)
        return None

    async def _check_volume(self, rule: VolumeThresholdAlert) -> TriggeredThresholdAlert | None:
        try:
            def _fetch():
                from data_provider import DataFetcherManager
                return DataFetcherManager().get_daily_data(rule.stock_code, days=20)
            result = await asyncio.to_thread(_fetch)
            if result is None:
                return None
            df, _source = result
            if df is None or df.empty:
                return None
            avg_vol = df["volume"].mean()
            latest_vol = df["volume"].iloc[-1]
            if avg_vol > 0 and latest_vol > avg_vol * rule.multiplier:
                return TriggeredThresholdAlert(
                    rule=rule, current_value=latest_vol,
                    message=f"📊 {rule.stock_code} volume spike: {latest_vol:,.0f} ({latest_vol / avg_vol:.1f}× avg)",
                )
        except Exception as exc:
            logger.debug("[ThresholdMonitor] _check_volume error: %s", exc)
        return None

    def to_dict_list(self) -> list[dict[str, Any]]:
        results = []
        for rule in self.rules:
            entry: dict[str, Any] = {
                "stock_code": rule.stock_code,
                "alert_type": rule.alert_type.value,
                "description": rule.description,
                "status": rule.status.value,
                "created_at": rule.created_at,
                "ttl_hours": rule.ttl_hours,
            }
            if isinstance(rule, PriceThresholdAlert):
                entry["direction"] = rule.direction
                entry["price"] = rule.price
            elif isinstance(rule, PriceChangeThresholdAlert):
                entry["direction"] = rule.direction
                entry["change_pct"] = rule.change_pct
            elif isinstance(rule, VolumeThresholdAlert):
                entry["multiplier"] = rule.multiplier
            results.append(entry)
        return results

    @classmethod
    def from_dict_list(cls, data: list[dict[str, Any]]) -> ThresholdEventMonitor:
        monitor = cls()
        for index, entry in enumerate(data, start=1):
            try:
                validate_threshold_alert_rule(entry)
                alert_type = entry.get("alert_type", "custom")
                stock_code = entry.get("stock_code", "")
                if alert_type == ThresholdAlertType.PRICE_CROSS.value:
                    rule = PriceThresholdAlert(stock_code=stock_code, direction=entry.get("direction", "above").lower(), price=float(entry.get("price", 0.0)))
                elif alert_type == ThresholdAlertType.PRICE_CHANGE_PERCENT.value:
                    rule = PriceChangeThresholdAlert(stock_code=stock_code, direction=entry.get("direction", "up").lower(), change_pct=float(entry["change_pct"]))
                elif alert_type == ThresholdAlertType.VOLUME_SPIKE.value:
                    rule = VolumeThresholdAlert(stock_code=stock_code, multiplier=float(entry.get("multiplier", 2.0)))
                else:
                    raise ValueError(f"unsupported alert_type: {alert_type}")
                rule.status = ThresholdAlertStatus(entry.get("status", "active"))
                raw_created = entry.get("created_at")
                try:
                    rule.created_at = float(raw_created) if raw_created is not None else time.time()
                except (TypeError, ValueError):
                    rule.created_at = time.time()
                rule.ttl_hours = float(entry.get("ttl_hours", 24.0))
                monitor.add_alert(rule)
            except Exception as exc:
                logger.warning("[ThresholdMonitor] Skip invalid rule #%d: %s", index, exc)
        return monitor


def parse_threshold_alert_rules(raw_rules: Any) -> list[dict[str, Any]]:
    """从配置解析阈值告警规则。"""
    if raw_rules is None:
        return []
    parsed = raw_rules
    if isinstance(raw_rules, str):
        cleaned = raw_rules.strip()
        if not cleaned:
            return []
        parsed = json.loads(cleaned)
    if isinstance(parsed, dict):
        parsed = parsed.get("rules", [])
    if not isinstance(parsed, list):
        raise ValueError("Threshold alert rules must be a JSON array")
    invalid_indices = [idx for idx, entry in enumerate(parsed) if not isinstance(entry, dict)]
    if invalid_indices:
        raise ValueError(
            f"Threshold alert rules list must contain only objects; invalid entries at positions: {invalid_indices}"
        )
    return parsed


def validate_threshold_alert_rule(rule: dict[str, Any]) -> None:
    """验证一条阈值告警规则。"""
    if not isinstance(rule, dict):
        raise ValueError("Threshold alert rule must be an object")
    stock_code = str(rule.get("stock_code") or "").strip()
    if not stock_code:
        raise ValueError("stock_code is required")
    try:
        alert_type = ThresholdAlertType(rule.get("alert_type", ""))
    except ValueError as exc:
        raise ValueError(f"invalid alert_type: {rule.get('alert_type')}") from exc
    _ensure_supported_threshold_type(alert_type)
    status = rule.get("status")
    if status is not None:
        try:
            ThresholdAlertStatus(status)
        except ValueError as exc:
            raise ValueError(f"invalid status: {status}") from exc
    ttl_hours = rule.get("ttl_hours")
    if ttl_hours is not None:
        try:
            ttl_value = float(ttl_hours)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid ttl_hours: {ttl_hours}") from exc
        if ttl_value <= 0:
            raise ValueError("ttl_hours must be > 0")
    if alert_type == ThresholdAlertType.PRICE_CROSS:
        direction = str(rule.get("direction", "above")).lower()
        if direction not in {"above", "below"}:
            raise ValueError(f"invalid direction: {direction}")
        try:
            price = float(rule.get("price"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid price: {rule.get('price')}") from exc
        if price <= 0:
            raise ValueError("price must be > 0")
    elif alert_type == ThresholdAlertType.PRICE_CHANGE_PERCENT:
        direction = str(rule.get("direction", "up")).lower()
        if direction not in {"up", "down"}:
            raise ValueError(f"invalid direction: {direction}")
        try:
            change_pct = float(rule.get("change_pct"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid change_pct: {rule.get('change_pct')}") from exc
        if change_pct <= 0:
            raise ValueError("change_pct must be > 0")
    elif alert_type == ThresholdAlertType.VOLUME_SPIKE:
        try:
            multiplier = float(rule.get("multiplier", 2.0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid multiplier: {rule.get('multiplier')}") from exc
        if multiplier <= 0:
            raise ValueError("multiplier must be > 0")
