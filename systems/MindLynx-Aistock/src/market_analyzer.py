"""
===================================
大盘复盘分析模块
===================================

职责：
1. 获取大盘指数数据（上证、深证、创业板）
2. 搜索市场新闻形成复盘情报
3. 使用大模型生成每日大盘复盘报告
"""

import base64
import io
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from data_provider.base import DataFetcherManager
from src.config import get_config
from src.core.market_profile import MarketProfile, get_profile
from src.core.market_strategy import get_market_strategy_blueprint
from src.report_language import normalize_report_language
from src.search_service import SearchService

logger = logging.getLogger(__name__)


_ENGLISH_SECTION_PATTERNS = {
    "market_summary": r"###\s*(?:1\.\s*)?Market Summary",
    "index_commentary": r"###\s*(?:2\.\s*)?(?:Index Commentary|Major Indices)",
    "sector_highlights": r"###\s*(?:4\.\s*)?(?:Sector Highlights|Sector/Theme Highlights)",
}

_CHINESE_SECTION_PATTERNS = {
    "market_summary": r"###\s*一、(?:盘面总览|市场总结)",
    "index_commentary": r"###\s*二、(?:指数结构|指数点评|主要指数)",
    "sector_highlights": r"###\s*三、(?:板块主线|热点解读|板块表现)",
    "funds_sentiment": r"###\s*四、(?:资金与情绪|资金动向)",
    "news_catalysts": r"###\s*五、(?:消息催化|后市展望)",
}


@dataclass
class MarketIndex:
    """大盘指数数据"""

    code: str  # 指数代码
    name: str  # 指数名称
    current: float = 0.0  # 当前点位
    change: float = 0.0  # 涨跌点数
    change_pct: float = 0.0  # 涨跌幅(%)
    open: float = 0.0  # 开盘点位
    high: float = 0.0  # 最高点位
    low: float = 0.0  # 最低点位
    prev_close: float = 0.0  # 昨收点位
    volume: float = 0.0  # 成交量（手）
    amount: float = 0.0  # 成交额（元）
    amplitude: float = 0.0  # 振幅(%)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "current": self.current,
            "change": self.change,
            "change_pct": self.change_pct,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "volume": self.volume,
            "amount": self.amount,
            "amplitude": self.amplitude,
        }


@dataclass
class MarketOverview:
    """市场概览数据"""

    date: str  # 日期
    indices: list[MarketIndex] = field(default_factory=list)  # 主要指数
    up_count: int = 0  # 上涨家数
    down_count: int = 0  # 下跌家数
    flat_count: int = 0  # 平盘家数
    limit_up_count: int = 0  # 涨停家数
    limit_down_count: int = 0  # 跌停家数
    total_amount: float = 0.0  # 两市成交额（亿元）
    # north_flow: float = 0.0           # 北向资金净流入（亿元）- 已废弃，接口不可用

    # 板块涨幅榜
    top_sectors: list[dict] = field(default_factory=list)  # 涨幅前5板块
    bottom_sectors: list[dict] = field(default_factory=list)  # 跌幅前5板块

    # 概念板块
    top_concepts: list[dict] = field(default_factory=list)  # 涨幅前5概念
    bottom_concepts: list[dict] = field(default_factory=list)  # 跌幅前5概念

    # 涨停池
    limit_up_pool: list[dict] = field(default_factory=list)  # 涨停股详情(前10)
    hot_stocks: list[dict] = field(default_factory=list)  # 人气股(前5)


class MarketAnalyzer:
    """
    大盘复盘分析器

    功能：
    1. 获取大盘指数实时行情
    2. 获取市场涨跌统计
    3. 获取板块涨跌榜
    4. 搜索市场新闻
    5. 生成大盘复盘报告
    """

    def __init__(
        self,
        search_service: SearchService | None = None,
        analyzer=None,
        region: str = "cn",
    ):
        """
        初始化大盘分析器

        Args:
            search_service: 搜索服务实例
            analyzer: AI分析器实例（用于调用LLM）
            region: 市场区域 cn=A股 us=美股
        """
        self.config = get_config()
        self.search_service = search_service
        self.analyzer = analyzer
        self.data_manager = DataFetcherManager()
        self.region = region if region in ("cn", "us", "hk") else "cn"
        self.profile: MarketProfile = get_profile(self.region)
        self.strategy = get_market_strategy_blueprint(self.region)

    def _get_review_language(self) -> str:
        configured = normalize_report_language(getattr(getattr(self, "config", None), "report_language", "zh"))
        if self.region == "us":
            return "en"
        return configured

    def _get_template_review_language(self) -> str:
        return normalize_report_language(getattr(getattr(self, "config", None), "report_language", "zh"))

    def _get_sector_capital_flow_text(self) -> str:
        """获取板块资金流向文本块，供prompt注入。"""
        try:
            rankings = self.data_manager.get_sector_capital_flow_rankings(n=5)
            top = rankings.get("top", [])
            bottom = rankings.get("bottom", [])
            if not top and not bottom:
                return ""
            lines = ["## 板块资金流向"]
            if top:
                top_text = ", ".join([
                    f"{s.get('name', '?')}({s.get('net_flow', 0):+.0f}万)"
                    if isinstance(s.get('net_flow'), (int, float))
                    else f"{s.get('name', '?')}"
                    for s in top
                ])
                lines.append(f"- 主力净流入前5: {top_text}")
            if bottom:
                bottom_text = ", ".join([
                    f"{s.get('name', '?')}({s.get('net_flow', 0):+.0f}万)"
                    if isinstance(s.get('net_flow'), (int, float))
                    else f"{s.get('name', '?')}"
                    for s in bottom
                ])
                lines.append(f"- 主力净流出前5: {bottom_text}")
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"[大盘] 板块资金流向获取失败: {e}")
            return ""

    def _get_eastmoney_rating_text(self) -> str:
        """读取东方财富评级缓存，生成市场情绪摘要文本块。"""
        try:
            from pathlib import Path
            _root = Path(__file__).resolve().parent.parent.parent.parent
            cache_path = _root / "data" / "realtime" / "eastmoney_rating.json"
            if not cache_path.exists():
                return ""
            import json
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            stocks_data = data.get("stocks", {})
            if not stocks_data:
                return ""
            lines = ["## 东方财富参与意愿（散户情绪指标）"]
            # 统计意愿均值
            desires = [s["desire"] for s in stocks_data.values() if isinstance(s.get("desire"), (int, float))]
            if desires:
                avg_desire = sum(desires) / len(desires)
                lines.append(f"- 参与意愿均值: {avg_desire:.1f}/100（0-100，平台用户行为聚合）")
            # 统计关注度均值
            focuses = [s["focus_avg"] for s in stocks_data.values() if isinstance(s.get("focus_avg"), (int, float))]
            if focuses:
                avg_focus = sum(focuses) / len(focuses)
                lines.append(f"- 关注度均值: {avg_focus:.1f}/100")
            lines.append(f"- 注意: 该数据反映散户对个股的当前关注度和参与意愿,与T+1涨跌幅无显著相关性,仅供参考")
            fetched = data.get("fetched_at", "?")
            lines.append(f"- 数据时间: {fetched}")
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"[大盘] 东方财富评级加载失败: {e}")
            return ""

    def _get_eastmoney_stock_map(self) -> dict[str, str]:
        """读取东方财富评级缓存，返回 {stock_code: 'EM意愿XX 关注XX'} 映射。"""
        try:
            from pathlib import Path
            _root = Path(__file__).resolve().parent.parent.parent.parent
            cache_path = _root / "data" / "realtime" / "eastmoney_rating.json"
            if not cache_path.exists():
                return {}
            import json
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            stocks_data = data.get("stocks", {})
            result = {}
            for code, s in stocks_data.items():
                d = s.get("desire")
                f = s.get("focus_avg")
                parts = ["EM"]
                if d is not None:
                    parts.append(f"意愿{d}/100")
                if f is not None:
                    parts.append(f"关注{f}/100")
                result[code] = " ".join(parts)
            return result
        except Exception:
            return {}

    def _get_market_scope_name(self, review_language: str | None = None) -> str:
        review_language = review_language or self._get_review_language()
        if self.region == "us":
            return "US market"
        if self.region == "hk":
            return "Hong Kong market" if review_language == "en" else "港股市场"
        if review_language == "en":
            return "A-share market"
        return "A股市场"

    def _get_turnover_unit_label(self) -> str:
        """Return the turnover unit label for the current market/language."""
        if self.region == "us":
            return "USD bn" if self._get_review_language() == "en" else "十亿美元"
        if self.region == "hk":
            return "HKD bn" if self._get_review_language() == "en" else "十亿港元"
        return "CNY 100m" if self._get_review_language() == "en" else "亿"

    def _format_turnover_value(self, amount_raw: float) -> str:
        """Format raw turnover according to market-specific units."""
        if amount_raw == 0.0:
            return "N/A"
        if self.region in ("us", "hk"):
            return f"{amount_raw / 1e9:.2f}"
        if amount_raw > 1e6:
            return f"{amount_raw / 1e8:.0f}"
        return f"{amount_raw:.0f}"

    def _get_index_change_arrow(self, change_pct: float) -> str:
        if change_pct == 0:
            return "⚪"
        color_scheme = getattr(getattr(self, "config", None), "market_review_color_scheme", "green_up")
        if color_scheme == "red_up":
            return "🔴" if change_pct > 0 else "🟢"
        return "🟢" if change_pct > 0 else "🔴"

    def _get_review_title(self, date: str) -> str:
        if self._get_review_language() == "en":
            market_names = {"us": "US Market Recap", "hk": "HK Market Recap"}
            market_name = market_names.get(self.region, "A-share Market Recap")
            return f"## {date} {market_name}"
        return f"## {date} 大盘复盘"

    def _get_index_hint(self) -> str:
        if self._get_review_language() == "en":
            if self.region == "us":
                return "Analyze the key moves in the S&P 500, Nasdaq, Dow, and other major indices."
            if self.region == "hk":
                return "Analyze the key moves in the HSI, Hang Seng Tech, HSCEI, and other major indices."
            return "Analyze the price action in the SSE, SZSE, ChiNext, and other major indices."
        return self.profile.prompt_index_hint

    def _get_strategy_prompt_block(self) -> str:
        if self.region == "hk" and self._get_review_language() == "en":
            return """## Strategy Blueprint: Hong Kong Market Regime Strategy
Focus on HSI trend, southbound flow dynamics, and sector rotation to define next-session risk posture.

### Strategy Principles
- Read market regime from HSI, HSTECH, and HSCEI alignment first.
- Track southbound capital flow as a key sentiment driver.
- Translate recap into actionable risk-on/risk-off stance with clear invalidation points.

### Analysis Dimensions
- Trend Regime: Classify the market as momentum, range, or risk-off.
  - Are HSI/HSTECH/HSCEI directionally aligned
  - Did volume confirm the move
  - Are key index levels reclaimed or lost
- Capital Flows: Map southbound flow and macro narrative into equity risk appetite.
  - Southbound net flow direction and magnitude
  - USD/HKD and China policy implications
  - Breadth and leadership concentration
- Sector Themes: Identify persistent leaders and vulnerable laggards.
  - Tech/internet platform trend persistence
  - Financials/property sensitivity to policy shifts
  - Defensive vs growth factor rotation

### Action Framework
- Risk-on: broad index breakout with expanding southbound participation.
- Neutral: mixed index signals; focus on selective relative strength.
- Risk-off: failed breakouts and rising volatility; prioritize capital preservation."""
        if not (self.region == "cn" and self._get_review_language() == "en"):
            return self.strategy.to_prompt_block()
        return """## Strategy Blueprint: A-share Three-Phase Recap Strategy
Focus on index trend, liquidity, and sector rotation to shape the next-session trading plan.

### Strategy Principles
- Read index direction first, then confirm liquidity structure, and finally test sector persistence.
- Every conclusion must map to position sizing, trading pace, and risk-control actions.
- Base judgments on today's data and the latest 3-day news flow without inventing unverified information.

### Analysis Dimensions
- Trend Structure: Determine whether the market is in an uptrend, range, or defensive phase.
  - Are the SSE, SZSE, and ChiNext moving in the same direction
  - Is the market advancing on expanding volume or slipping on contracting volume
  - Have key support or resistance levels been reclaimed or broken
- Liquidity & Sentiment: Identify near-term risk appetite and market temperature.
  - Advance/decline breadth and limit-up/limit-down structure
  - Whether turnover is expanding or fading
  - Whether high-beta leaders are showing divergence
- Leading Themes: Distill tradable leadership and areas to avoid.
  - Whether leading sectors have clear event catalysts
  - Whether sector leaders are pulling the group higher
  - Whether weakness is broadening across lagging sectors

### Action Framework
- Offensive: indices rise in sync, turnover expands, and core themes strengthen.
- Balanced: index divergence or low-volume consolidation; keep sizing controlled and wait for confirmation.
- Defensive: indices weaken and laggards broaden; prioritize risk control and de-risking."""

    def _get_strategy_markdown_block(self, review_language: str | None = None) -> str:
        review_language = review_language or self._get_review_language()
        if self.region == "hk" and review_language == "en":
            return """### 6. Strategy Framework
- **Trend Regime**: Classify the market as momentum, range, or risk-off based on HSI/HSTECH/HSCEI alignment.
- **Capital Flows**: Track southbound flow direction and macro narrative for risk appetite signals.
- **Sector Themes**: Focus on tech/internet platform persistence and financials/property policy sensitivity.
"""
        if not (self.region == "cn" and review_language == "en"):
            return self.strategy.to_markdown_block()
        return """### 6. Strategy Framework
- **Trend Structure**: Determine whether the market is in an uptrend, range, or defensive phase.
- **Liquidity & Sentiment**: Track breadth, turnover expansion, and whether leaders are diverging.
- **Leading Themes**: Focus on sectors with catalysts and sustained leadership while avoiding broadening weakness.
"""

    def _get_market_mood_text(self, mood_key: str, review_language: str | None = None) -> str:
        review_language = review_language or self._get_review_language()
        if review_language == "en":
            mapping = {
                "strong_up": "strong gains",
                "mild_up": "moderate gains",
                "mild_down": "mild losses",
                "strong_down": "clear weakness",
                "range": "range-bound trading",
            }
        else:
            mapping = {
                "strong_up": "强势上涨",
                "mild_up": "小幅上涨",
                "mild_down": "小幅下跌",
                "strong_down": "明显下跌",
                "range": "震荡整理",
            }
        return mapping[mood_key]

    def get_market_overview(self) -> MarketOverview:
        """
        获取市场概览数据

        Returns:
            MarketOverview: 市场概览数据对象
        """
        today = datetime.now().strftime("%Y-%m-%d")
        overview = MarketOverview(date=today)

        # 1. 获取主要指数行情（按 region 切换 A 股/美股）
        overview.indices = self._get_main_indices()

        # 2. 获取涨跌统计（A 股有，美股无等效数据）
        if self.profile.has_market_stats:
            self._get_market_statistics(overview)

        # 3. 获取板块涨跌榜（A 股有，美股暂无）
        if self.profile.has_sector_rankings:
            self._get_sector_rankings(overview)
            self._get_concept_rankings(overview)
            self._get_limit_up_detail(overview)
            self._get_hot_stocks_detail(overview)

        # 4. 获取北向资金（可选）
        # self._get_north_flow(overview)

        return overview

    def _get_main_indices(self) -> list[MarketIndex]:
        """获取主要指数实时行情"""
        indices = []

        try:
            logger.info("[大盘] 获取主要指数实时行情...")

            # 使用 DataFetcherManager 获取指数行情（按 region 切换）
            data_list = self.data_manager.get_main_indices(region=self.region)

            if data_list:
                for item in data_list:
                    index = MarketIndex(
                        code=item["code"],
                        name=item["name"],
                        current=item["current"],
                        change=item["change"],
                        change_pct=item["change_pct"],
                        open=item["open"],
                        high=item["high"],
                        low=item["low"],
                        prev_close=item["prev_close"],
                        volume=item["volume"],
                        amount=item["amount"],
                        amplitude=item["amplitude"],
                    )
                    indices.append(index)

            if not indices:
                logger.warning("[大盘] 所有行情数据源失败，将依赖新闻搜索进行分析")
            else:
                logger.info(f"[大盘] 获取到 {len(indices)} 个指数行情")

        except Exception as e:
            logger.error(f"[大盘] 获取指数行情失败: {e}")

        return indices

    def _get_market_statistics(self, overview: MarketOverview):
        """获取市场涨跌统计"""
        try:
            logger.info("[大盘] 获取市场涨跌统计...")

            stats = self.data_manager.get_market_stats()

            if stats:
                overview.up_count = stats.get("up_count", 0)
                overview.down_count = stats.get("down_count", 0)
                overview.flat_count = stats.get("flat_count", 0)
                overview.limit_up_count = stats.get("limit_up_count", 0)
                overview.limit_down_count = stats.get("limit_down_count", 0)
                overview.total_amount = stats.get("total_amount", 0.0)

                logger.info(
                    f"[大盘] 涨:{overview.up_count} 跌:{overview.down_count} 平:{overview.flat_count} "
                    f"涨停:{overview.limit_up_count} 跌停:{overview.limit_down_count} "
                    f"成交额:{overview.total_amount:.0f}亿"
                )

        except Exception as e:
            logger.error(f"[大盘] 获取涨跌统计失败: {e}")

    def _get_sector_rankings(self, overview: MarketOverview):
        """获取板块涨跌榜"""
        try:
            logger.info("[大盘] 获取板块涨跌榜...")

            top_sectors, bottom_sectors = self.data_manager.get_sector_rankings(5)

            if top_sectors or bottom_sectors:
                overview.top_sectors = top_sectors
                overview.bottom_sectors = bottom_sectors

                logger.info(f"[大盘] 领涨板块: {[s['name'] for s in overview.top_sectors]}")
                logger.info(f"[大盘] 领跌板块: {[s['name'] for s in overview.bottom_sectors]}")

        except Exception as e:
            logger.error(f"[大盘] 获取板块涨跌榜失败: {e}")

    def _get_concept_rankings(self, overview: MarketOverview):
        try:
            logger.info("[大盘] 获取概念板块涨跌榜...")
            top, bottom = self.data_manager.get_concept_rankings(5)
            if top or bottom:
                overview.top_concepts = top or []
                overview.bottom_concepts = bottom or []
                if overview.top_concepts:
                    logger.info("[大盘] 领涨概念: %s", [c["name"] for c in overview.top_concepts])
                if overview.bottom_concepts:
                    logger.info("[大盘] 领跌概念: %s", [c["name"] for c in overview.bottom_concepts])
        except Exception as e:
            logger.debug("[大盘] 获取概念板块失败(非关键): %s", e)

    def _get_limit_up_detail(self, overview: MarketOverview):
        try:
            logger.info("[大盘] 获取涨停池详情...")
            pool = self.data_manager.get_limit_up_pool()
            if pool:
                overview.limit_up_pool = pool[:10]
                logger.info("[大盘] 涨停池: %d 只", len(overview.limit_up_pool))
        except Exception as e:
            logger.debug("[大盘] 获取涨停池失败(非关键): %s", e)

    def _get_hot_stocks_detail(self, overview: MarketOverview):
        try:
            logger.info("[大盘] 获取人气股排行...")
            stocks = self.data_manager.get_hot_stocks()
            if stocks:
                overview.hot_stocks = stocks[:5]
                logger.info("[大盘] 人气股: %d 只", len(overview.hot_stocks))
        except Exception as e:
            logger.debug("[大盘] 获取人气股失败(非关键): %s", e)

    # def _get_north_flow(self, overview: MarketOverview):
    #     """获取北向资金流入"""
    #     try:
    #         logger.info("[大盘] 获取北向资金...")
    #
    #         # 获取北向资金数据
    #         df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
    #
    #         if df is not None and not df.empty:
    #             # 取最新一条数据
    #             latest = df.iloc[-1]
    #             if '当日净流入' in df.columns:
    #                 overview.north_flow = float(latest['当日净流入']) / 1e8  # 转为亿元
    #             elif '净流入' in df.columns:
    #                 overview.north_flow = float(latest['净流入']) / 1e8
    #
    #             logger.info(f"[大盘] 北向资金净流入: {overview.north_flow:.2f}亿")
    #
    #     except Exception as e:
    #         logger.warning(f"[大盘] 获取北向资金失败: {e}")

    def search_market_news(self) -> list[dict]:
        """
        搜索市场新闻

        Returns:
            新闻列表
        """
        if not self.search_service:
            logger.warning("[大盘] 搜索服务未配置，跳过新闻搜索")
            return []

        all_news = []

        # 按 region 使用不同的新闻搜索词
        search_queries = self.profile.news_queries

        try:
            logger.info("[大盘] 开始搜索市场新闻...")

            # 根据 region 设置搜索上下文名称，避免美股搜索被解读为 A 股语境
            market_names = {"cn": "大盘", "us": "US market", "hk": "HK market"}
            market_name = market_names.get(self.region, "大盘")
            for query in search_queries:
                response = self.search_service.search_stock_news(
                    stock_code="market", stock_name=market_name, max_results=3, focus_keywords=query.split()
                )
                if response and response.results:
                    all_news.extend(response.results)
                    logger.info(f"[大盘] 搜索 '{query}' 获取 {len(response.results)} 条结果")

            logger.info(f"[大盘] 共获取 {len(all_news)} 条市场新闻")

        except Exception as e:
            logger.error(f"[大盘] 搜索市场新闻失败: {e}")

        return all_news

    def generate_market_review(self, overview: MarketOverview, news: list,
                               previous_plan: str | None = None,
                               session_label: str = "全天",
                               stock_data: str | None = None,
                               capital_flow_text: str | None = None,
                               eastmoney_text: str | None = None) -> str:
        """
        使用大模型生成大盘复盘报告

        Args:
            overview: 市场概览数据
            news: 市场新闻列表 (SearchResult 对象列表)

        Returns:
            大盘复盘报告文本
        """
        if not self.analyzer or not self.analyzer.is_available():
            logger.warning("[大盘] AI分析器未配置或不可用，使用模板生成报告")
            return self._generate_template_review(overview, news)

        # 构建 Prompt（含自选股数据 + 资金流向 + 东方财富评级，如有）
        prompt = self._build_review_prompt(overview, news, previous_plan, session_label,
                                            stock_data=stock_data, capital_flow_text=capital_flow_text,
                                            eastmoney_text=eastmoney_text)

        logger.info("[大盘] 调用大模型生成复盘报告...")
        # Use the public generate_text() entry point — never access private analyzer attributes.
        review = self.analyzer.generate_text(prompt, max_tokens=8192, temperature=0.7)

        if review:
            logger.info("[大盘] 复盘报告生成成功，长度: %d 字符", len(review))
            # Inject structured data tables into LLM prose sections
            return self._inject_data_into_review(review, overview, news)
        else:
            logger.warning("[大盘] 大模型返回为空，使用模板报告")
            return self._generate_template_review(overview, news)

    def _inject_data_into_review(
        self,
        review: str,
        overview: MarketOverview,
        news: list | None = None,
    ) -> str:
        """Inject structured data tables into the corresponding LLM prose sections."""
        # Build data blocks
        stats_block = self._build_stats_block(overview)
        indices_block = self._build_indices_block(overview)
        sector_block = self._build_sector_block(overview)
        concept_block = self._build_concept_block(overview)
        limit_up_block = self._build_limit_up_block(overview)
        news_block = self._build_news_block(news or [])
        patterns = _ENGLISH_SECTION_PATTERNS if self._get_review_language() == "en" else _CHINESE_SECTION_PATTERNS

        if stats_block:
            review = self._insert_after_section(
                review,
                patterns["market_summary"],
                stats_block,
            )

        if indices_block:
            review = self._insert_after_section(
                review,
                patterns["index_commentary"],
                indices_block,
            )

        if sector_block:
            review = self._insert_after_section(
                review,
                patterns["sector_highlights"],
                sector_block
                + ("\n\n" + concept_block if concept_block else "")
                + ("\n\n" + limit_up_block if limit_up_block else ""),
            )

        if news_block and "news_catalysts" in patterns:
            review = self._insert_after_section(
                review,
                patterns["news_catalysts"],
                news_block,
            )

        # ── 后处理 ──
        if not self._get_review_language() == "en":
            import re as _re
            # 仅在 ### 标题末尾移除 emoji（精确匹配标题行尾，不影响正文红绿灯等业务 emoji）
            review = _re.sub(
                r"(^###\s+[^\n]*?)[\U0001F300-\U0001F9FF\u2600-\u27BF\u2700-\u27BF]+",
                r"\1", review, flags=_re.MULTILINE,
            )
            # 统一文末声明
            review = _re.sub(
                r">\s*\*?市场有风险，投资需谨慎[。，]\s*以上数据仅供参考[，,]\s*不构成投资建议[。]*\*?",
                "> 以上数据仅供参考，不构成投资建议",
                review,
            )
            review = _re.sub(
                r">\s*建议仅供参考[，,]\s*不构成投资建议[。]*",
                "> 以上数据仅供参考，不构成投资建议",
                review,
            )

        return review

    @staticmethod
    def _insert_after_section(text: str, heading_pattern: str, block: str) -> str:
        """Insert a data block at the end of a markdown section (before the next ### heading)."""
        import re

        # Find the heading
        match = re.search(heading_pattern, text)
        if not match:
            return text
        start = match.end()
        # Find the next ### heading after this one
        next_heading = re.search(r"\n###\s", text[start:])
        if next_heading:
            insert_pos = start + next_heading.start()
        else:
            # No next heading — append at end
            insert_pos = len(text)
        # Insert the block before the next heading, with spacing
        return text[:insert_pos].rstrip() + "\n\n" + block + "\n\n" + text[insert_pos:].lstrip("\n")

    def _build_stats_block(self, overview: MarketOverview) -> str:
        """Build market statistics block."""
        has_stats = overview.up_count or overview.down_count or overview.total_amount
        if not has_stats:
            # 无涨跌家数/成交额时，若指数存在则基于指数生成简化版红绿灯
            if not overview.indices:
                return ""
            light = self.build_market_light_snapshot(overview)
            score = light["score"]
            if self._get_review_language() == "en":
                return (
                    f"> **Market Light**: {light['status']} ({light['label']}) | "
                    f"**{score}/100** {self._build_temperature_bar(score)}\n"
                    f"> — ⚠️ breadth data unavailable, temperature based on indices only —"
                )
            _status_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
            lines = [
                f"> **大盘红绿灯**：{_status_emoji.get(light['status'], '●')}（{light['label']}）",
                f"> **盘面温度**：{light['temperature_label']} **{score}/100** {self._build_temperature_bar(score)}",
                "> — ⚠️ 市场宽度数据暂不可用，温度仅基于指数涨跌幅估算 —",
            ]
            return "\n".join(lines)
        if self._get_review_language() == "en":
            light = self.build_market_light_snapshot(overview)
            return "\n".join(
                [
                    f"- **Market Signal**: {light['score']}/100 "
                    f"({light['temperature_label']}, {light['label']})",
                    f"- **Drivers**: {'; '.join(light['reasons'])}",
                    f"- **Guidance**: {light['guidance']}",
                    "",
                    f"- **Breadth**: Advancers {overview.up_count} / Decliners {overview.down_count} / "
                    f"Flat {overview.flat_count}; "
                    f"Limit-up {overview.limit_up_count} / Limit-down {overview.limit_down_count}; "
                    f"Turnover {overview.total_amount:.0f} ({self._get_turnover_unit_label()})",
                ]
            )
        light = self.build_market_light_snapshot(overview)
        score = light["score"]
        _status_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
        participation = overview.up_count + overview.down_count
        up_ratio = overview.up_count / participation if participation else 0.0
        limit_spread = overview.limit_up_count - overview.limit_down_count
        lines = [
            f"- **盘面信号**：{score}/100（{light['temperature_label']}，{light['label']}）",
            f"- **信号依据**：{'；'.join(light['reasons'])}",
            f"- **操作建议**：{light['guidance']}",
            "",
            "| 指标 | 数值 | 观察 |",
            "|------|------|------|",
            f"| 上涨/下跌/平盘 | {overview.up_count} / {overview.down_count} / {overview.flat_count} | 上涨占比(不含平盘) {up_ratio:.1%} |",
            f"| 涨停/跌停 | {overview.limit_up_count} / {overview.limit_down_count} | 涨跌停差 {limit_spread:+d} |",
            f"| 两市成交额 | {overview.total_amount:.0f} 亿 | {self._describe_turnover(overview.total_amount)} |",
        ]
        raw = "\n".join(lines)
        # 清理块引用间的多余空行（把 >...\n\n> 压缩为 >...\n>）
        if raw.count("\n>") > 1:
            import re as _re
            raw = _re.sub(r"\n\n(>)", r"\n\1", raw)
        return raw

    def build_market_light_snapshot(self, overview: MarketOverview) -> dict[str, Any]:
        """Build a deterministic market-light snapshot from structured breadth data."""
        score, temperature_label = self._build_market_temperature(overview)
        if score >= 60:
            status = "green"
        elif score >= 40:
            status = "yellow"
        else:
            status = "red"

        if self._get_review_language() == "en":
            label_map = {
                "green": "risk-on",
                "yellow": "balanced",
                "red": "risk-off",
            }
            guidance_map = {
                "green": "Risk appetite is acceptable; focus on leading themes and position discipline.",
                "yellow": "Signals are mixed; keep position sizing moderate and wait for confirmation.",
                "red": "Risk is elevated; prioritize drawdown control and avoid chasing weak rebounds.",
            }
            reasons = self._build_market_light_reasons_en(overview, score)
        else:
            label_map = {
                "green": "可进攻",
                "yellow": "需观察",
                "red": "偏防守",
            }
            guidance_map = {
                "green": "风险偏好尚可，关注主线延续与仓位纪律。",
                "yellow": "信号分化，控制仓位并等待量价确认。",
                "red": "风险偏高，优先控制回撤，避免追高弱反弹。",
            }
            reasons = self._build_market_light_reasons_zh(overview, score)

        return {
            "status": status,
            "label": label_map[status],
            "score": score,
            "temperature_label": temperature_label,
            "reasons": reasons,
            "guidance": guidance_map[status],
        }

    def _build_market_light_reasons_zh(self, overview: MarketOverview, score: int) -> list[str]:
        participation = overview.up_count + overview.down_count
        up_ratio = overview.up_count / participation if participation else None
        reasons: List[str] = []
        if up_ratio is not None:
            if up_ratio >= 0.6:
                reasons.append(f"上涨家数占比 {up_ratio:.0%}，赚钱效应扩散")
            elif up_ratio <= 0.4:
                reasons.append(f"上涨家数占比 {up_ratio:.0%}，亏钱效应较强")
            else:
                reasons.append(f"上涨家数占比 {up_ratio:.0%}，市场分化")
        if overview.indices:
            avg_change = sum(idx.change_pct for idx in overview.indices) / len(overview.indices)
            reasons.append(f"主要指数平均涨跌幅 {avg_change:+.2f}%")
        if overview.limit_up_count or overview.limit_down_count:
            reasons.append(f"涨跌停差 {overview.limit_up_count - overview.limit_down_count:+d}")
        if not reasons and overview.total_amount:
            reasons.append(f"成交额 {overview.total_amount:.0f} 亿，{self._describe_turnover(overview.total_amount)}")
        if not reasons:
            reasons.append("结构化涨跌数据有限，按可用行情综合判断")
        return reasons[:4]

    def _build_market_light_reasons_en(self, overview: MarketOverview, score: int) -> list[str]:
        participation = overview.up_count + overview.down_count
        up_ratio = overview.up_count / participation if participation else None
        reasons: List[str] = []
        if up_ratio is not None:
            if up_ratio >= 0.6:
                reasons.append(f"advancers ratio {up_ratio:.0%}, breadth is expanding")
            elif up_ratio <= 0.4:
                reasons.append(f"advancers ratio {up_ratio:.0%}, downside pressure dominates")
            else:
                reasons.append(f"advancers ratio {up_ratio:.0%}, breadth is mixed")
        if overview.indices:
            avg_change = sum(idx.change_pct for idx in overview.indices) / len(overview.indices)
            reasons.append(f"average major-index change {avg_change:+.2f}%")
        if overview.limit_up_count or overview.limit_down_count:
            reasons.append(f"limit-up/down spread {overview.limit_up_count - overview.limit_down_count:+d}")
        if not reasons and overview.total_amount:
            reasons.append(f"turnover {overview.total_amount:.0f} ({self._get_turnover_unit_label()})")
        if not reasons:
            reasons.append("limited structured breadth data; using available market inputs")
        return reasons[:4]

    @staticmethod
    def _shorten_index_name(name: str) -> str:
        """简化指数名称，让表格更紧凑。"""
        mapping = {
            "上证指数": "上证",
            "深证成指": "深证",
            "创业板指": "创业板",
            "科创50": "科创50",
            "上证50": "上证50",
            "沪深300": "沪深300",
        }
        return mapping.get(name, name)

    def _build_indices_block(self, overview: MarketOverview) -> str:
        """构建指数行情表格"""
        if not overview.indices:
            return ""
        if self._get_review_language() == "en":
            lines = [
                f"| Index | Last | Change % | Open | High | Low | Amplitude | Turnover ({self._get_turnover_unit_label()}) |",
                "|-------|------|----------|------|------|-----|-----------|-----------------|",
            ]
        else:
            lines = [
                "| 指数 | 最新 | 涨跌幅 | 开盘 | 最高 | 最低 | 振幅 | 成交额(亿) |",
                "|------|------|--------|------|------|------|------|-----------|",
            ]
        for idx in overview.indices:
            arrow = self._get_index_change_arrow(idx.change_pct)
            amount_raw = idx.amount or 0.0
            amount_str = self._format_turnover_value(amount_raw)
            lines.append(
                f"| {self._shorten_index_name(idx.name)} | {idx.current:.2f} | {arrow} {idx.change_pct:+.2f}% | "
                f"{self._format_optional_number(idx.open)} | {self._format_optional_number(idx.high)} | "
                f"{self._format_optional_number(idx.low)} | {self._format_optional_pct(idx.amplitude)} | {amount_str} |"
            )
        return "\n".join(lines)

    def _build_sector_block(self, overview: MarketOverview) -> str:
        """Build sector ranking block."""
        if not overview.top_sectors and not overview.bottom_sectors:
            return ""
        lines = []
        if overview.top_sectors:
            if self._get_review_language() == "en":
                lines.extend(
                    [
                        "#### Leading Sectors",
                        "| Rank | Sector | Change |",
                        "|------|--------|--------|",
                    ]
                )
            else:
                lines.extend(
                    [
                        "#### 领涨板块 Top 5",
                        "| 排名 | 板块 | 涨跌幅 |",
                        "|------|------|--------|",
                    ]
                )
            for rank, sector in enumerate(overview.top_sectors[:5], 1):
                lines.append(
                    f"| {rank} | {sector.get('name', '-')} | {self._format_signed_pct(sector.get('change_pct'))} |"
                )
        if overview.bottom_sectors:
            if lines:
                lines.append("")
            if self._get_review_language() == "en":
                lines.extend(
                    [
                        "#### Lagging Sectors",
                        "| Rank | Sector | Change |",
                        "|------|--------|--------|",
                    ]
                )
            else:
                lines.extend(
                    [
                        "#### 领跌板块 Top 5",
                        "| 排名 | 板块 | 涨跌幅 |",
                        "|------|------|--------|",
                    ]
                )
            for rank, sector in enumerate(overview.bottom_sectors[:5], 1):
                lines.append(
                    f"| {rank} | {sector.get('name', '-')} | {self._format_signed_pct(sector.get('change_pct'))} |"
                )
        return "\n".join(lines)

    def _build_concept_block(self, overview: MarketOverview) -> str:
        if not overview.top_concepts and not overview.bottom_concepts:
            return ""
        lines = []
        if overview.top_concepts:
            if self._get_review_language() == "en":
                lines.extend(["#### Leading Concepts", "| Rank | Concept | Change |", "|------|---------|--------|"])
            else:
                lines.extend(["#### 领涨概念 Top 5", "| 排名 | 概念 | 涨跌幅 |", "|------|------|--------|"])
            for rank, c in enumerate(overview.top_concepts[:5], 1):
                lines.append(f"| {rank} | {c.get('name', '-')} | {self._format_signed_pct(c.get('change_pct'))} |")
        if overview.bottom_concepts:
            if lines:
                lines.append("")
            if self._get_review_language() == "en":
                lines.extend(["#### Lagging Concepts", "| Rank | Concept | Change |", "|------|---------|--------|"])
            else:
                lines.extend(["#### 领跌概念 Top 5", "| 排名 | 概念 | 涨跌幅 |", "|------|------|--------|"])
            for rank, c in enumerate(overview.bottom_concepts[:5], 1):
                lines.append(f"| {rank} | {c.get('name', '-')} | {self._format_signed_pct(c.get('change_pct'))} |")
        return "\n".join(lines)

    def _build_limit_up_block(self, overview: MarketOverview) -> str:
        if not overview.limit_up_pool:
            return ""
        if self._get_review_language() == "en":
            lines = [
                "#### Limit-Up Board",
                "| # | Stock | Chg% | Cons. | Industry |",
                "|---|-------|------|---------|----------|",
            ]
        else:
            lines = ["#### 涨停池 Top 5", "| # | 股票 | 涨幅 | 连板 | 行业 |", "|---|------|------|------|------|"]
        for i, s in enumerate(overview.limit_up_pool[:5], 1):
            cons = s.get("consecutive_boards", 0) or 0
            lines.append(
                f"| {i} | {s.get('name', '-')} | {s.get('change_pct', 0):+.1f}% | {cons}板 | {s.get('industry', '-')} |"
            )
        return "\n".join(lines)

    def _build_news_block(self, news: list) -> str:
        """Build a source-aware news catalyst list for the rendered report."""
        if not news:
            return ""
        language = self._get_review_language()
        if language == "en":
            lines = [
                "#### News Catalysts",
            ]
        else:
            lines = [
                "#### 近三日市场线索",
            ]

        for idx, item in enumerate(news[:5], 1):
            lines.append(self._format_news_catalyst_line(idx, item, language=language))
        return "\n".join(lines)

    @staticmethod
    def _get_news_field(item: Any, field: str) -> str:
        if hasattr(item, field):
            value = getattr(item, field, "") or ""
        elif isinstance(item, dict):
            value = item.get(field, "") or ""
        else:
            value = ""
        return str(value).strip()

    @classmethod
    def _format_news_catalyst_line(cls, idx: int, item: Any, *, language: str = "zh") -> str:
        fallback_title = "Untitled catalyst" if language == "en" else "未命名线索"
        title = cls._compact_news_text(cls._get_news_field(item, "title"), limit=90) or fallback_title
        source = cls._compact_news_text(cls._get_news_field(item, "source"), limit=40)
        date_text = cls._compact_news_text(cls._get_news_field(item, "published_date"), limit=24)
        url = cls._compact_news_text(cls._get_news_field(item, "url"), limit=0)
        title_text = cls._escape_markdown_link_label(title)
        if url:
            title_text = f"[{title_text}]({url})"
        meta_parts = [part for part in (source, date_text) if part]
        if language == "en":
            meta = f" ({' / '.join(meta_parts)})" if meta_parts else ""
        else:
            meta = f"（{' / '.join(meta_parts)}）" if meta_parts else ""
        return f"- {idx}. {title_text}{meta}"

    @staticmethod
    def _compact_news_text(value: str, *, limit: int) -> str:
        text = " ".join(str(value or "").split())
        if limit <= 0 or len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."

    @staticmethod
    def _format_optional_number(value: float) -> str:
        return "N/A" if value in (None, 0, 0.0) else f"{value:.2f}"

    @staticmethod
    def _format_optional_pct(value: float) -> str:
        return "N/A" if value in (None, 0, 0.0) else f"{value:.2f}%"

    @staticmethod
    def _format_signed_pct(value: Any) -> str:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return "N/A"
        return f"{numeric_value:+.2f}%"

    @staticmethod
    def _build_temperature_bar(score: int) -> str:
        filled = max(0, min(10, round(score / 10)))
        return "█" * filled + "░" * (10 - filled)

    @staticmethod
    def _escape_markdown_link_label(value: str) -> str:
        return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")

    @staticmethod
    def _describe_turnover(total_amount: float) -> str:
        if total_amount >= 15000:
            return "高活跃度"
        if total_amount >= 9000:
            return "中等活跃"
        if total_amount > 0:
            return "缩量观望"
        return "暂无数据"

    def _build_market_temperature(self, overview: MarketOverview) -> tuple[int, str]:
        participants = overview.up_count + overview.down_count
        breadth_score = 50
        if participants:
            breadth_score = int(overview.up_count / participants * 100)

        index_changes = [idx.change_pct for idx in overview.indices if idx.change_pct is not None]
        index_score = 50
        if index_changes:
            avg_change = sum(index_changes) / len(index_changes)
            index_score = int(max(0, min(100, 50 + avg_change * 12)))

        limit_total = overview.limit_up_count + overview.limit_down_count
        limit_score = 50
        if limit_total:
            limit_score = int(overview.limit_up_count / limit_total * 100)

        score = int(round(breadth_score * 0.45 + index_score * 0.35 + limit_score * 0.20))
        if self._get_review_language() == "en":
            if score >= 70:
                label = "risk-on"
            elif score >= 55:
                label = "constructive"
            elif score >= 40:
                label = "mixed"
            else:
                label = "defensive"
        else:
            if score >= 70:
                label = "强势"
            elif score >= 55:
                label = "偏暖"
            elif score >= 40:
                label = "震荡"
            else:
                label = "偏弱"
        return score, label

    @staticmethod
    def _load_prev_market_turnover(today_date: str, session_label: str = "全天") -> float | None:
        """读取上一个交易日同时段复盘报告中的成交额，用于 LLM 判断"较昨日放大/缩小"。

        午盘对比：读取最近的 market_review_*_午盘.md
        全天对比：读取最近的 market_review_*_全天.md
        """
        try:
            import glob as _glob, re as _re
            from pathlib import Path
            reports_dir = Path("reports")
            # 根据时段选择对比文件
            suffix = "午盘" if session_label == "午盘" else "全天"
            # 找所有同时段复盘报告，排除今天的
            files = sorted(
                f for f in _glob.glob(str(reports_dir / f"market_review_*_{suffix}.md"))
                if today_date not in f
            )
            if not files:
                return None
            # 取最新的
            with open(files[-1], "r", encoding="utf-8") as f:
                content = f.read()
            # 从 Markdown 表格中找两市成交额行
            m = _re.search(r"\| 两市成交额 \| (\d+)\s*亿", content)
            if m:
                return float(m.group(1))
            # 兜底：从正文找 "成交额xxx亿元"
            m = _re.search(r"成交额[约达]?(\d+(?:\.\d+)?)[万亿]", content)
            if m:
                val = float(m.group(1))
                if "万" in m.group(0) or "万" in content[max(0, m.start() - 10):m.end()]:
                    val *= 10000
                elif "亿" in m.group(0) or "亿" in content[max(0, m.start() - 10):m.end()]:
                    pass  # already in 亿
                return val
        except Exception:
            pass
        return None

    @staticmethod
    def _load_previous_plan() -> str | None:
        """读取上一份全天报告的明日交易计划段落，供 LLM 参考。"""
        import glob as _glob
        from pathlib import Path

        files = sorted(_glob.glob(str(Path("reports") / "market_review_*_全天.md")))
        if len(files) < 2:
            return None
        try:
            with open(files[-2], "r", encoding="utf-8") as f:
                prev = f.read()
        except Exception:
            return None
        m = __import__("re").search(
            r"(?:###\s*六[、.、]明日交易计划|###\s*6[、.、]Strategy Plan)"
            r".*?(?=\n###\s|\Z)",
            prev,
            __import__("re").DOTALL,
        )
        return m.group(0).strip() if m else None

    def _build_review_prompt(self, overview: MarketOverview, news: list,
                             previous_plan: str | None = None,
                             session_label: str = "全天",
                             stock_data: str | None = None,
                             capital_flow_text: str | None = None,
                             eastmoney_text: str | None = None) -> str:
        """构建复盘报告 Prompt"""
        review_language = self._get_review_language()

        # 指数行情信息（简洁格式，不用emoji）
        indices_text = ""
        for idx in overview.indices:
            direction = "↑" if idx.change_pct > 0 else "↓" if idx.change_pct < 0 else "-"
            indices_text += f"- {idx.name}: {idx.current:.2f} ({direction}{abs(idx.change_pct):.2f}%)\n"

        # 板块信息
        top_sectors_text = ", ".join([f"{s['name']}({s['change_pct']:+.2f}%)" for s in overview.top_sectors[:3]])
        bottom_sectors_text = ", ".join([f"{s['name']}({s['change_pct']:+.2f}%)" for s in overview.bottom_sectors[:3]])

        # 新闻信息 - 支持 SearchResult 对象或字典
        news_text = ""
        for i, n in enumerate(news[:6], 1):
            # 兼容 SearchResult 对象和字典
            title = self._compact_news_text(self._get_news_field(n, "title"), limit=90)
            snippet = self._compact_news_text(self._get_news_field(n, "snippet"), limit=220)
            source = self._compact_news_text(self._get_news_field(n, "source"), limit=60)
            published_date = self._compact_news_text(self._get_news_field(n, "published_date"), limit=30)
            url = self._compact_news_text(self._get_news_field(n, "url"), limit=180)
            meta_parts = [part for part in (source, published_date) if part]
            meta = f" ({' / '.join(meta_parts)})" if meta_parts else ""
            url_line = f"\n   URL: {url}" if url else ""
            news_text += f"{i}. {title}{meta}\n   {snippet or '-'}{url_line}\n"

        # 按 region 组装市场概况与板块区块（美股无涨跌家数、板块数据）
        stats_block = ""
        sector_block = ""
        if review_language == "en":
            if self.profile.has_market_stats:
                stats_block = f"""## Market Breadth
- Advancers: {overview.up_count} | Decliners: {overview.down_count} | Flat: {overview.flat_count}
- Limit-up: {overview.limit_up_count} | Limit-down: {overview.limit_down_count}
- Turnover: {overview.total_amount:.0f} ({self._get_turnover_unit_label()})"""
            else:
                stats_block = (
                    "## Market Breadth\n(No equivalent advance/decline statistics are available for this market.)"
                )

            if self.profile.has_sector_rankings:
                sector_block = f"""## Sector Performance
Leading: {top_sectors_text if top_sectors_text else "N/A"}
Lagging: {bottom_sectors_text if bottom_sectors_text else "N/A"}"""
            else:
                sector_block = "## Sector Performance\n(Sector data not available for this market.)"
        else:
            if self.profile.has_market_stats:
                stats_block = f"""## 市场概况
- 上涨: {overview.up_count} 家 | 下跌: {overview.down_count} 家 | 平盘: {overview.flat_count} 家
- 涨停: {overview.limit_up_count} 家 | 跌停: {overview.limit_down_count} 家
- 两市成交额: {overview.total_amount:.0f} 亿元"""
            else:
                stats_block = "## 市场概况\n（该市场暂无涨跌家数等统计）"

            if self.profile.has_sector_rankings:
                sector_block = f"""## 板块表现
领涨: {top_sectors_text if top_sectors_text else "暂无数据"}
领跌: {bottom_sectors_text if bottom_sectors_text else "暂无数据"}"""
            else:
                sector_block = "## 板块表现\n（该市场暂无板块涨跌数据）"

        data_no_indices_hint = (
            "注意：由于行情数据获取失败，请主要根据【市场新闻】进行定性分析和总结，不要编造具体的指数点位。"
            if not indices_text
            else ""
        )
        if review_language == "en":
            data_no_indices_hint = (
                "Note: Market data fetch failed. Rely mainly on [Market News] for qualitative analysis. Do not invent index levels."
                if not indices_text
                else ""
            )
            indices_placeholder = indices_text if indices_text else "No index data (API error)"
            news_placeholder = news_text if news_text else "No relevant news"
        else:
            indices_placeholder = indices_text if indices_text else "暂无指数数据（接口异常）"
            news_placeholder = news_text if news_text else "暂无相关新闻"

        # 读取昨日成交额作为对比参考
        _prev_turnover = self._load_prev_market_turnover(overview.date, session_label)
        _prev_turnover_hint = ""
        if _prev_turnover is not None:
            _prev_turnover_hint = f"\n## 昨日参考\n- 昨日成交额: {_prev_turnover:.0f} 亿元\n"

        # 上期交易计划回顾（让LLM自行分析上期建议的准确性）
        _prev_plan_hint = ""
        if previous_plan:
            _prev_plan_hint = f"\n## 上期交易计划回顾\n{previous_plan}\n"

        if review_language == "en":
            report_title = self._get_review_title(overview.date).removeprefix("## ").strip()
            return f"""You are a professional US/A/H market analyst. Please produce a concise market recap report based on the data below.

[Requirements]
- Output pure Markdown only
- No JSON
- No code blocks
- Use emoji sparingly in headings (at most one per heading)
- The entire fixed shell, headings, guidance, and conclusion must be in English

---

# Today's Market Data

## Date
{overview.date}

## Major Indices
{indices_placeholder}

{stats_block}

{sector_block}
{_prev_turnover_hint}
## Market News
{news_placeholder}

{data_no_indices_hint}

{self._get_strategy_prompt_block()}

---

# Output Template (follow this structure)

## {report_title}

### 1. Market Summary
(2-3 sentences summarizing overall market tone, index moves, and liquidity.)

### 2. Index Commentary
({self._get_index_hint()})

### 3. Fund Flows
(Interpret what turnover, participation, and flow signals imply.)

### 4. Sector Highlights
(Analyze the drivers behind the leading and lagging sectors or themes.)

### 5. Outlook
(Provide the near-term outlook based on price action and news.)

### 6. Risk Alerts
(List the main risks to monitor.)

### 7. Strategy Plan
(Provide an offensive/balanced/defensive stance, a position-sizing guideline, one invalidation trigger, and end with “For reference only, not investment advice.”)

---

Output the report content directly, no extra commentary.
"""

        # A 股场景使用中文提示语
        return f"""你是一位专业的A/H/美股市场分析师，请根据以下数据生成一份结构化的{self._get_market_scope_name("zh")}大盘复盘报告。

【重要】输出要求：
- 必须输出纯 Markdown 文本格式
- 禁止输出 JSON 格式
- 禁止输出代码块
- emoji 仅在标题处少量使用（每个标题最多1个）
- 报告要像交易员盘后工作台：先给结论，再按数据表、主线、催化、计划展开
- 不要重复列出已由系统注入的表格数据；正文负责解释表格背后的含义

---

# 今日市场数据

## 日期
{overview.date}

## 主要指数
{indices_placeholder}

{stats_block}

{sector_block}
{_prev_turnover_hint}{_prev_plan_hint}
## 市场新闻
{news_placeholder}

{data_no_indices_hint}

{self._get_strategy_prompt_block()}

---

# 输出格式模板（请严格按此格式输出）
#
# 规则：
# 1. 禁止在章节标题后添加任何 emoji 图标（如 ✅ 🏛️ 📊 等），标题就是纯文字
# 2. 文末风险提示结束后，用引用形式写：“> 以上数据仅供参考，不构成投资建议”

## {overview.date} 大盘复盘

### 一、盘面总览
（2-3句话概括指数、涨跌家数、成交额和情绪温度，明确"强势/偏暖/震荡/偏弱"判断。
如果【上期交易计划回顾】存在，请在此段末尾自然附上一句对昨日策略的验证，格式参考："上期建议为XXX，今日XX"。不存在则不写。）

### 二、指数结构
（{self._get_index_hint()}，说明谁在护盘、谁在拖累，以及关键支撑/压力）

### 三、板块主线
（分析领涨/领跌板块背后的逻辑、持续性和是否形成主线）

### 四、资金与情绪
（解读成交额、涨跌停结构、市场宽度和东方财富参与意愿数据，综合分析主力资金动向与市场情绪）
{capital_flow_text or ""}
{eastmoney_text or ""}

### 五、消息催化
（结合近三日新闻，提炼真正影响明日交易的催化或扰动）

### 六、明日交易计划
（给出进攻/均衡/防守结论、仓位区间、关注方向、回避方向和一个触发失效条件）

### 七、风险提示
（列出需要关注的风险点；文末用引用形式补充：“> 以上数据仅供参考，不构成投资建议”）

### 八、自选股操盘建议
（如果【自选股因子数据】或【自选股实时行情】不为空，基于这些数据和当日大盘背景，对每只自选股给出独立操盘建议：明确操作方向、仓位参考和关键价位。数据为空则跳过此章节。）

{stock_data or ""}

---

请直接输出复盘报告内容，不要输出其他说明文字。
"""

    def _generate_template_review(self, overview: MarketOverview, news: list) -> str:
        """使用模板生成复盘报告（无大模型时的备选方案）"""
        template_language = self._get_template_review_language()
        mood_code = self.profile.mood_index_code
        # 根据 mood_index_code 查找对应指数
        # cn: mood_code="000001"，idx.code 可能为 "sh000001"（以 mood_code 结尾）
        # us: mood_code="SPX"，idx.code 直接为 "SPX"
        mood_index = next(
            (idx for idx in overview.indices if idx.code == mood_code or idx.code.endswith(mood_code)),
            None,
        )
        if mood_index:
            if mood_index.change_pct > 1:
                market_mood = self._get_market_mood_text("strong_up", template_language)
            elif mood_index.change_pct > 0:
                market_mood = self._get_market_mood_text("mild_up", template_language)
            elif mood_index.change_pct > -1:
                market_mood = self._get_market_mood_text("mild_down", template_language)
            else:
                market_mood = self._get_market_mood_text("strong_down", template_language)
        else:
            market_mood = self._get_market_mood_text("range", template_language)

        # 指数行情（简洁格式）
        indices_text = ""
        for idx in overview.indices[:4]:
            direction = "↑" if idx.change_pct > 0 else "↓" if idx.change_pct < 0 else "-"
            indices_text += f"- **{idx.name}**: {idx.current:.2f} ({direction}{abs(idx.change_pct):.2f}%)\n"

        # 板块信息
        separator = ", " if template_language == "en" else "、"
        top_text = separator.join([s["name"] for s in overview.top_sectors[:3]])
        bottom_text = separator.join([s["name"] for s in overview.bottom_sectors[:3]])

        if template_language == "en":
            stats_section = ""
            if self.profile.has_market_stats:
                stats_section = f"""
### 3. Breadth & Liquidity
| Metric | Value |
|--------|-------|
| Advancers | {overview.up_count} |
| Decliners | {overview.down_count} |
| Limit-up | {overview.limit_up_count} |
| Limit-down | {overview.limit_down_count} |
| Turnover ({self._get_turnover_unit_label()}) | {overview.total_amount:.0f} |
"""
            sector_section = ""
            if self.profile.has_sector_rankings and (top_text or bottom_text):
                sector_section = f"""
### 4. Sector Highlights
- **Leaders**: {top_text or "N/A"}
- **Laggards**: {bottom_text or "N/A"}
"""
            market_names = {"us": "US Market Recap", "hk": "HK Market Recap"}
            market_name = market_names.get(self.region, "A-share Market Recap")
            report = f"""## {overview.date} {market_name}

### 1. Market Summary
Today's {self._get_market_scope_name(template_language)} showed **{market_mood}**.

### 2. Major Indices
{indices_text or "- No index data available"}
{stats_section}
{sector_section}
### 5. Risk Alerts
Market conditions can change quickly. The data above is for reference only and does not constitute investment advice.

{self._get_strategy_markdown_block(template_language)}

---
*Review Time: {datetime.now().strftime("%H:%M")}*
"""
            return report

        market_labels = {"cn": "A股", "us": "美股", "hk": "港股"}
        market_label = market_labels.get(self.region, "A股")
        dashboard_block = self._build_stats_block(overview)
        indices_block = self._build_indices_block(overview)
        sector_block = self._build_sector_block(overview)
        concept_block = self._build_concept_block(overview)
        limit_up_block = self._build_limit_up_block(overview)
        return f"""## {overview.date} 大盘复盘

> 今日{market_label}市场整体呈现**{market_mood}**态势，优先观察指数承接、成交额变化和板块持续性。

### 一、盘面总览
{dashboard_block or "暂无市场宽度数据。"}

### 二、指数结构
{indices_block or indices_text or "暂无指数数据。"}

### 三、板块主线
{sector_block or "- 暂无板块涨跌榜数据。"}

{self._build_concept_block(overview) or ""}

{self._build_limit_up_block(overview) or ""}

### 四、资金与情绪
- 结合成交额和涨跌家数看，当前更适合等待确认，避免仅凭单一热点追高。

### 五、消息催化
- 暂无可用新闻时，应降低对题材持续性的确定性判断。

### 六、明日交易计划
- **结论**：均衡观察。
- **仓位**：控制在中性区间，等待指数与主线共振。
- **关注方向**：{top_text or "强于指数的主线板块"}。
- **回避方向**：{bottom_text or "连续走弱且缺少修复信号的方向"}。

### 七、风险提示
> *市场有风险，投资需谨慎。以上数据仅供参考，不构成投资建议。*

---
*复盘时间: {datetime.now().strftime("%H:%M")}*
"""

    def _build_previous_verification(self, overview: MarketOverview) -> str | None:
        """读取上一期报告，提取计划并对比今日数据，生成一句验证文本。
        作为 LLM 失败的兜底方案（当前未启用，由 LLM prompt 直接生成）。
        """
        import glob as _glob
        from pathlib import Path

        # 始终对比上一个全天报告的下注建议，不因午盘/全天时段不同而变化
        prev_suffix = "_全天.md"

        files = sorted(_glob.glob(str(Path("reports") / f"market_review_*{prev_suffix}")))
        if not files:
            return None
        try:
            with open(files[-1], "r", encoding="utf-8") as f:
                prev = f.read()
        except Exception:
            return None

        # 提取计划段落
        plan_m = __import__("re").search(
            r"(?:六、明日交易计划|六、午后交易计划).*?(?=\n###\s|\Z)",
            prev,
            __import__("re").DOTALL,
        )
        if not plan_m:
            return None
        plan = plan_m.group(0)

        # 提取关键元素（兼容三种格式：- **判断**：xxx、**策略结论**：xxx、当前市场评分：**xxx**）
        _r = __import__("re")
        stance = ""
        # 格式 A: **判断：均衡偏进攻** → 整车包裹
        m = _r.search(r"\*\*(?:判断|攻守判断)[：:]\s*(\S[^，。\n]*?)\*\*", plan)
        # 格式 B: **攻守判断**：均衡偏防守
        if not m:
            m = _r.search(r"(?:判断|攻守判断)\*\*[：:]\s*(\S[^，。\n]*)", plan)
        # 格式 C: 当前市场评分：**均衡偏防守**
        if not m:
            m = _r.search(r"当前市场评分[：:]\s*\*\*(\S[^（\n]*)", plan)
        # 格式 D: - **策略结论**：均衡偏进攻（新格式）
        if not m:
            m = _r.search(r"(?:策略结论)\*\*[：:]\s*(\S[^，。\n]*)", plan)
        # 格式 E: 当前市场状态为"均衡偏进攻"（自由段落格式）
        if not m:
            m = _r.search(r'当前市场状态为[「""]\s*(\S[^」""\n]*)', plan)
        if m:
            stance = m.group(1).strip().rstrip("*")

        position = ""
        m = _r.search(r"\*\*(?:仓位|仓位区间)[：:]\s*([\d.~-]+成)\*\*", plan)
        if not m:
            m = _r.search(r"(?:仓位|仓位区间)\*\*[：:]\s*([\d.~-]+成)", plan)
        if not m:
            m = _r.search(r"(?:中等偏低|偏高|轻仓|重仓|半仓)[^。\n]*?([\d.~-]+成)", plan)
        # 格式 D: 仓位维持中性偏积极（5-7成）（自由段落）
        if not m:
            m = _r.search(r"仓位[^。\n]{0,20}[（(]\s*([\d.~-]+成)\s*[)）]", plan)
        if m:
            position = m.group(1).strip().rstrip("*")

        watch = ""
        m = _r.search(r"\*\*(?:关注方向|关注)[：:]\*\*\s*\n\s*(?:\d[.、])?\s*([^，。、：（\n]+)", plan)
        if not m:
            m = _r.search(r"(?:关注方向|关注)\*\*[：:]\s*(?:\d[.、])?\s*([^，。、：（\n]+)", plan)
        # 格式 D: 关注方向：房屋建筑业能否延续...（自由段落）
        if not m:
            m = _r.search(r"关注方向[：:]\s*([^\n。，、：]{2,15})", plan)
        if m:
            watch = _r.sub(r"\*\*", "", m.group(1)).strip()[:15]

        # ---- 验证 ----
        result_parts = []

        # 1. 攻守方向验证
        if stance:
            indices_chg = [i.change_pct for i in overview.indices if i.change_pct is not None]
            avg_chg = sum(indices_chg) / len(indices_chg) if indices_chg else 0
            is_off = any(k in stance for k in ["进攻", "偏进攻"])
            is_def = any(k in stance for k in ["防守", "偏防守"])
            if (is_off and avg_chg > -0.3) or (is_def and avg_chg < 0.3):
                result_parts.append("判断合理")
            elif is_off and avg_chg <= -0.5:
                result_parts.append("判断偏乐观")
            elif is_def and avg_chg >= 0.5:
                result_parts.append("判断偏保守")
            else:
                result_parts.append("判断基本合理")

        # 2. 关注板块验证
        if watch and overview.top_sectors:
            watch_kws = _r.split(r"[,，、/\s]+", watch)
            top_names = " ".join(s.get("name", "") for s in overview.top_sectors[:3])
            matched = any(kw and kw in top_names for kw in watch_kws if len(kw) >= 2)
            if matched:
                result_parts.append("关注方向命中")

        if not result_parts:
            return None

        # 拼装验证行
        if not stance and not position and not watch:
            return None
        prev_text = stance or ""
        if position:
            prev_text += f"（{position}）"
        if watch:
            prev_text += f"关注{watch[:15]}"

        result_word = "，".join(result_parts)
        text = f"上期建议为{prev_text}，今日{result_word}。"
        return f"> {text}"

    def run_daily_review(self, session_label: str = "全天") -> str:
        """
        执行每日大盘复盘流程

        Args:
            session_label: "午盘" 或 "全天"，影响成交额对比的数据源

        Returns:
            复盘报告文本
        """
        logger.info("========== 开始大盘复盘分析 ==========")

        # 1. 获取市场概览
        overview = self.get_market_overview()

        # 2. 搜索市场新闻
        news = self.search_market_news()

        # 3. 读取上一期全天报告的明日交易计划，供 LLM 参考
        previous_plan = self._load_previous_plan()

        # 3b. 读取当日融合系统自选股分析数据（如有）
        stock_data = self._load_stock_pool_data()
        logger.info(f"[大盘] 自选股数据: {'已加载' if stock_data else '无数据'}")

        # 3c. 获取板块资金流向数据（板块级主力净流入排行）
        capital_flow_text = self._get_sector_capital_flow_text()
        if capital_flow_text:
            logger.info("[大盘] 板块资金流向: 已加载")

        # 3d. 获取东方财富评级数据（市场情绪+个股映射）
        eastmoney_text = self._get_eastmoney_rating_text()
        if eastmoney_text:
            logger.info("[大盘] 东方财富评级: 已加载")

        # 4. 生成复盘报告（LLM 自动包含上期建议验证 + 自选股分析 + 资金流向 + 东方财富评级）
        report = self.generate_market_review(overview, news, previous_plan, session_label,
                                              stock_data=stock_data, capital_flow_text=capital_flow_text,
                                              eastmoney_text=eastmoney_text)

        logger.info("========== 大盘复盘分析完成 ==========")

        return report

    def _load_stock_pool_data(self) -> str | None:
        """加载当日融合系统自选股分析数据，供LLM生成操盘建议。

        优先从融合输出文件读取（含因子信号），融合不存在时降级到实时行情。
        """
        try:
            from pathlib import Path
            today = datetime.now().strftime("%Y-%m-%d")
            _root = Path(__file__).resolve().parent.parent.parent.parent
            fusion_dir = _root / "data" / "fusion_output"
            if fusion_dir.exists():
                files = sorted(fusion_dir.glob(f"fusion_{today}*.json"))
                if files:
                    import json
                    data = json.loads(files[-1].read_text(encoding="utf-8"))
                    lines = ["## 自选股因子数据"]
                    items = data if isinstance(data, list) else data.get("results", [])
                    if items:
                        for item in items:
                            code = item.get("stock_code", item.get("code", ""))
                            name = item.get("stock_name", "")
                            ly = item.get("lynx_score", 0)
                            ml = item.get("mindlynx_score", 0)
                            fusion = item.get("fusion_score", 0)
                            sig = item.get("signal_name", "")
                            price_str = self._fetch_realtime_price_str(code)
                            price_col = f"  当前价={price_str}" if price_str else ""
                            em_suffix = self._get_eastmoney_stock_map().get(code, "")
                            em_col = f"  {em_suffix}" if em_suffix else ""
                            lines.append(f"- {name}({code})  ly={ly:+.2f}  ml={ml:+.2f}  融合={fusion:+.2f}  信号={sig}{price_col}{em_col}")
                        return "\n".join(lines)
        except Exception as e:
            logger.debug(f"[大盘] 自选股融合数据加载失败: {e}")

        return self._fetch_realtime_stock_data()

    def _fetch_realtime_price_str(self, stock_code: str) -> str:
        """获取单只股票的实时价格字符串。"""
        try:
            q = self.data_manager.get_realtime_quote(stock_code, log_final_failure=False)
            if q is not None and q.price is not None:
                chg = f"{q.change_pct:+.2f}%" if q.change_pct is not None else ""
                # 同时输出价格和涨跌幅，避免LLM混淆
                return f"¥{q.price:.2f} | 涨跌幅{chg}"
        except Exception:
            pass
        return ""

    def _fetch_realtime_stock_data(self) -> str | None:
        """获取自选股实时行情数据（无融合因子时的降级方案）。"""
        codes = getattr(self.config, "stock_list", [])
        if not codes or not isinstance(codes, list):
            return None
        lines = ["## 自选股实时行情（融合因子数据暂不可用）"]
        for code in codes:
            try:
                q = self.data_manager.get_realtime_quote(code, log_final_failure=False)
                if q is not None and q.price is not None:
                    name = q.name or code
                    chg = f"{q.change_pct:+.2f}%" if q.change_pct is not None else ""
                    vol_r = f"  量比{q.volume_ratio:.2f}" if q.volume_ratio is not None else ""
                    em_suffix = self._get_eastmoney_stock_map().get(code, "")
                    em_col = f"  {em_suffix}" if em_suffix else ""
                    lines.append(f"- {name}({code})  ¥{q.price:.2f}  {chg}{vol_r}{em_col}")
                else:
                    lines.append(f"- {code}  行情暂不可用")
            except Exception:
                lines.append(f"- {code}  行情获取失败")
        return "\n".join(lines) if len(lines) > 1 else None


def build_sector_treemap(sectors: list[dict],    max_blocks: int = 16) -> str | None:
    """Generate a sector treemap image and return as base64 data URI.

    Each sector dict: {'name': str, 'change_pct': float, 'amount': float (optional)}
    Rectangle size ≈ amount (turnover), color = red for up, green for down.
    Shows top N sectors by |change_pct| — all individual, no aggregation.
    Returns base64 PNG data URI string, or None if unavailable.
    """
    if not sectors:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.font_manager as fm
        import squarify

        _zh_font = None
        for _fp in ["/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                     "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]:
            if __import__("os").path.exists(_fp):
                _zh_font = fm.FontProperties(fname=_fp)
                break
        _tk = {"fontproperties": _zh_font} if _zh_font else {}

        # Sort by |change_pct|, take top N (no aggregation — each block is a real sector)
        sectors_sorted = sorted(sectors, key=lambda s: abs(s.get("change_pct", 0)), reverse=True)[:max_blocks]

        labels = []
        sizes = []
        colors_list = []
        for s in sectors_sorted:
            name = s.get("name", "?")[:6]
            chg = s.get("change_pct", 0)
            vol = max(abs(s.get("amount", 1) or 1), 1)
            amount = s.get("amount", 0)
            label = f"{name}\n{chg:+.1f}%"
            if amount:
                # Format amount in 亿
                amt_b = amount / 1e8 if amount > 1e8 else amount
                amt_unit = "亿" if amount > 1e8 else ""
                label += f"\n{amt_b:.0f}{amt_unit}"
            labels.append(label)
            sizes.append(vol)
            # Solid colors: red for up, green for down (no gradient)
            if chg > 0:
                colors_list.append("#FF6B6B")  # 清新珊瑚粉红
            else:
                colors_list.append("#51CF66")  # 清新薄荷绿

        fig, ax = plt.subplots(1, figsize=(6.5, 3.8))
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")

        squarify.plot(sizes, label=labels, color=colors_list,
                      alpha=0.92, edgecolor="#FFFFFF", linewidth=0.5,
                      text_kwargs={"fontsize": 8, "weight": "bold", **_tk},
                      ax=ax)
        ax.axis("off")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", pad_inches=0.1)
        plt.close(fig)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode()
        return f"data:image/png;base64,{b64}"
    except ImportError:
        logger.debug("squarify/matplotlib not installed, skipping sector treemap")
        return None
    except Exception as e:
        logger.warning("板块 treemap 生成失败: %s", e)
        return None


# 测试入口
if __name__ == "__main__":
    import sys

    sys.path.insert(0, ".")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
    )

    analyzer = MarketAnalyzer()

    # 测试获取市场概览
    overview = analyzer.get_market_overview()
    print("\n=== 市场概览 ===")
    print(f"日期: {overview.date}")
    print(f"指数数量: {len(overview.indices)}")
    for idx in overview.indices:
        print(f"  {idx.name}: {idx.current:.2f} ({idx.change_pct:+.2f}%)")
    print(f"上涨: {overview.up_count} | 下跌: {overview.down_count}")
    print(f"成交额: {overview.total_amount:.0f}亿")

    # 测试生成模板报告
    report = analyzer._generate_template_review(overview, [])
    print("\n=== 复盘报告 ===")
    print(report)
