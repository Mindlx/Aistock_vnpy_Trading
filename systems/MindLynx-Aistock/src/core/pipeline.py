"""
===================================
A股自选股智能分析系统 - 核心分析流水线
===================================

职责：
1. 管理整个分析流程
2. 协调数据获取、存储、搜索、分析、通知等模块
3. 实现并发控制和异常处理
4. 提供股票分析的核心功能
"""

import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from bot.models import BotMessage
from data_provider import DataFetcherManager
from data_provider.base import normalize_stock_code
from data_provider.realtime_types import ChipDistribution
from data_provider.us_index_mapping import is_us_stock_code
from src.analyzer import (
    AnalysisResult,
    GeminiAnalyzer,
    fill_chip_structure_if_needed,
    fill_price_position_if_needed,
    stabilize_decision_with_structure,
)
from src.config import FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT, Config, get_config
from src.notification_noise import get_importance_emoji
from src.core.pipeline_data import DataMixin
from src.core.pipeline_notification import NotificationMixin
from src.core.trading_calendar import (
    get_market_for_stock,
    get_market_now,
)
from src.enums import ReportType
from src.notification import NotificationService
from src.report_language import (
    infer_decision_type_from_advice,
    localize_confidence_level,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
)
from src.search_service import SearchService
from src.services.social_sentiment_service import SocialSentimentService
from src.stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult
from src.storage import get_db

logger = logging.getLogger(__name__)


def _regime_atr_multiplier(pipeline: "StockAnalysisPipeline") -> float:
    """Extract ATR multiplier from regime classifier's recommended params."""
    try:
        if hasattr(pipeline, '_regime_prompt') and pipeline._regime_prompt:
            import re
            m = re.search(r"ATR倍数:\s*([\d.]+)x", str(pipeline._regime_prompt))
            if m:
                return float(m.group(1))
    except Exception:
        pass
    return 2.0  # default low-vol multiplier


class StockAnalysisPipeline(DataMixin, NotificationMixin):
    """
    股票分析主流程调度器

    职责：
    1. 管理整个分析流程
    2. 协调数据获取、存储、搜索、分析、通知等模块
    3. 实现并发控制和异常处理
    """

    def __init__(
        self,
        config: Config | None = None,
        max_workers: int | None = None,
        source_message: BotMessage | None = None,
        query_id: str | None = None,
        query_source: str | None = None,
        save_context_snapshot: bool | None = None,
        progress_callback: Callable[[int, str], None] | None = None,
        analysis_skills: list[str] | None = None,
    ):
        """
        初始化调度器

        Args:
            config: 配置对象（可选，默认使用全局配置）
            max_workers: 最大并发线程数（可选，默认从配置读取）
        """
        self.config = config or get_config()
        self.max_workers = max_workers or self.config.max_workers
        self.source_message = source_message
        self.query_id = query_id
        self.query_source = self._resolve_query_source(query_source)
        self.save_context_snapshot = (
            self.config.save_context_snapshot if save_context_snapshot is None else save_context_snapshot
        )
        self.progress_callback = progress_callback
        self.analysis_skills = list(analysis_skills) if analysis_skills is not None else None

        # 初始化各模块
        self.db = get_db()
        self.fetcher_manager = DataFetcherManager()
        # 不再单独创建 akshare_fetcher，统一使用 fetcher_manager 获取增强数据
        self.trend_analyzer = StockTrendAnalyzer()  # 技术分析器
        self.analyzer = GeminiAnalyzer(config=self.config, skills=self.analysis_skills)
        self.notifier = NotificationService(source_message=source_message)
        self._single_stock_notify_lock = threading.Lock()

        # 初始化搜索服务（可选，初始化失败不应阻断主分析流程）
        try:
            self.search_service = SearchService(
                bocha_keys=self.config.bocha_api_keys,
                tavily_keys=self.config.tavily_api_keys,
                anspire_keys=self.config.anspire_api_keys,
                brave_keys=self.config.brave_api_keys,
                serpapi_keys=self.config.serpapi_keys,
                minimax_keys=self.config.minimax_api_keys,
                searxng_base_urls=self.config.searxng_base_urls,
                searxng_public_instances_enabled=self.config.searxng_public_instances_enabled,
                news_max_age_days=self.config.news_max_age_days,
                news_strategy_profile=getattr(self.config, "news_strategy_profile", "short"),
            )
        except Exception as exc:
            logger.warning("搜索服务初始化失败，将以无搜索模式运行: %s", exc, exc_info=True)
            self.search_service = None

        logger.info(f"调度器初始化完成，最大并发数: {self.max_workers}")
        logger.info("已启用技术分析引擎（均线/趋势/量价指标）")
        # 打印实时行情/筹码配置状态
        if self.config.enable_realtime_quote:
            logger.info(f"实时行情已启用 (优先级: {self.config.realtime_source_priority})")
        else:
            logger.info("实时行情已禁用，将使用历史收盘价")
        if self.config.enable_chip_distribution:
            logger.info("筹码分布分析已启用")
        else:
            logger.info("筹码分布分析已禁用")
        if self.search_service is None:
            logger.warning("搜索服务未启用（初始化失败或依赖缺失）")
        elif self.search_service.is_available:
            logger.info("搜索服务已启用")
        else:
            logger.warning("搜索服务未启用（未配置搜索能力）")

        # 初始化社交舆情服务（仅美股，可选）
        try:
            self.social_sentiment_service = SocialSentimentService(
                api_key=self.config.social_sentiment_api_key,
                api_url=self.config.social_sentiment_api_url,
            )
            if self.social_sentiment_service.is_available:
                logger.info("Social sentiment service enabled (Reddit/X/Polymarket, US stocks only)")
        except Exception as exc:
            logger.warning(
                "社交舆情服务初始化失败，将跳过舆情分析: %s",
                exc,
                exc_info=True,
            )
            self.social_sentiment_service = None

    def _emit_progress(self, progress: int, message: str) -> None:
        """Best-effort bridge from pipeline stages to task SSE progress."""
        callback = getattr(self, "progress_callback", None)
        if callback is None:
            return
        try:
            callback(progress, message)
        except Exception as exc:
            query_id = getattr(self, "query_id", None)
            logger.warning(
                "[pipeline] progress callback failed: %s (progress=%s, message=%r, query_id=%s)",
                exc,
                progress,
                message,
                query_id,
                extra={
                    "progress": progress,
                    "progress_message": message,
                    "query_id": query_id,
                },
            )

    def analyze_stock(self, code: str, report_type: ReportType, query_id: str) -> AnalysisResult | None:
        """
        分析单只股票（增强版：含量比、换手率、筹码分析、多维度情报）

        流程：
        1. 获取实时行情（量比、换手率）- 通过 DataFetcherManager 自动故障切换
        2. 获取筹码分布 - 通过 DataFetcherManager 带熔断保护
        3. 进行趋势分析（基于交易理念）
        4. 多维度情报搜索（最新消息+风险排查+业绩预期）
        5. 从数据库获取分析上下文
        6. 调用 AI 进行综合分析

        Args:
            query_id: 查询链路关联 id
            code: 股票代码
            report_type: 报告类型

        Returns:
            AnalysisResult 或 None（如果分析失败）
        """
        stock_name = code
        try:
            self._emit_progress(18, f"{code}：正在获取行情与筹码数据")
            # 获取股票名称（先走轻量名称路径，后续若 realtime_quote 有 name 再覆盖）
            stock_name = self.fetcher_manager.get_stock_name(code, allow_realtime=False)

            # Step 1: 获取实时行情（量比、换手率等）- 使用统一入口，自动故障切换
            realtime_quote = None
            try:
                if self.config.enable_realtime_quote:
                    realtime_quote = self.fetcher_manager.get_realtime_quote(code, log_final_failure=False)
                    if realtime_quote:
                        # 使用实时行情返回的真实股票名称
                        if realtime_quote.name:
                            stock_name = realtime_quote.name
                        # 兼容不同数据源的字段（有些数据源可能没有 volume_ratio）
                        volume_ratio = getattr(realtime_quote, "volume_ratio", None)
                        # ⚡ 量比缺失时从日K线数据自行计算（统一入口，覆盖agent/非agent所有下游路径）
                        if volume_ratio is None:
                            try:
                                rows = self.db.get_latest_data(code, days=6)
                                if rows and len(rows) >= 2:
                                    today_vol = float(getattr(rows[0], "volume", 0) or 0)
                                    vols = [float(getattr(r, "volume", 0) or 0) for r in rows[1:]]
                                    avg_5d = sum(vols) / len(vols) if vols else 0
                                    if today_vol > 0 and avg_5d > 0:
                                        vr = round(today_vol / avg_5d, 2)
                                        realtime_quote.volume_ratio = vr
                                        realtime_quote._vr_is_daily = True  # 标记为日线计算值
                                        volume_ratio = vr
                            except Exception as e:
                                logger.debug(f"[pipeline] 量比计算失败({code}): {e}")
                        turnover_rate = getattr(realtime_quote, "turnover_rate", None)
                        logger.info(
                            f"{stock_name}({code}) 实时行情: 价格={realtime_quote.price}, "
                            f"量比={volume_ratio}, 换手率={turnover_rate}% "
                            f"(来源: {realtime_quote.source.value if hasattr(realtime_quote, 'source') else 'unknown'})"
                        )
                    else:
                        logger.warning(f"{stock_name}({code}) 所有实时行情数据源均不可用，已降级为历史收盘价继续分析")
                else:
                    logger.info(f"{stock_name}({code}) 实时行情已禁用，使用历史收盘价继续分析")
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 实时行情链路异常，已降级为历史收盘价继续分析: {e}")

            # 如果还是没有名称，使用代码作为名称
            if not stock_name:
                stock_name = f"股票{code}"

            # Step 2: 获取筹码分布 - 使用统一入口，带熔断保护
            chip_data = None
            try:
                chip_data = self.fetcher_manager.get_chip_distribution(code)
                if chip_data:
                    logger.info(
                        f"{stock_name}({code}) 筹码分布: 获利比例={chip_data.profit_ratio:.1%}, "
                        f"90%集中度={chip_data.concentration_90:.2%}"
                    )
                else:
                    logger.debug(f"{stock_name}({code}) 筹码分布获取失败或已禁用")
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 获取筹码分布失败: {e}")

            # If agent mode is explicitly enabled, or specific agent skills are configured, use the Agent analysis pipeline.
            # NOTE: use config.agent_mode (explicit opt-in) instead of
            # config.is_agent_available() so that users who only configured an
            # API Key for the traditional analysis path are not silently
            # switched to Agent mode (which is slower and more expensive).
            use_agent = getattr(self.config, "agent_mode", False)
            if not use_agent:
                if self.analysis_skills:
                    use_agent = True
                    logger.info(
                        f"{stock_name}({code}) Auto-enabled agent mode due to request skills: {self.analysis_skills}"
                    )
            if not use_agent:
                # Auto-enable agent mode when specific skills are configured (e.g., scheduled task with strategy)
                configured_skills = getattr(self.config, "agent_skills", [])
                if configured_skills and configured_skills != ["all"]:
                    use_agent = True
                    logger.info(
                        f"{stock_name}({code}) Auto-enabled agent mode due to configured skills: {configured_skills}"
                    )

            self._emit_progress(32, f"{stock_name}：正在聚合基本面与趋势数据")

            # Step 2.5: 基本面能力聚合（统一入口，异常降级）
            # - 失败时返回 partial/failed，不影响既有技术面/新闻链路
            # - 关闭开关时仍返回 not_supported 结构
            fundamental_context = None
            try:
                fundamental_context = self.fetcher_manager.get_fundamental_context(
                    code,
                    budget_seconds=getattr(
                        self.config,
                        "fundamental_stage_timeout_seconds",
                        FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT,
                    ),
                )
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 基本面聚合失败: {e}")
                fundamental_context = self.fetcher_manager.build_failed_fundamental_context(code, str(e))

            fundamental_context = self._attach_belong_boards_to_fundamental_context(
                code,
                fundamental_context,
            )

            # P0: write-only snapshot, fail-open, no read dependency on this table.
            try:
                self.db.save_fundamental_snapshot(
                    query_id=query_id,
                    code=code,
                    payload=fundamental_context,
                    source_chain=fundamental_context.get("source_chain", []),
                    coverage=fundamental_context.get("coverage", {}),
                )
            except Exception as e:
                logger.debug(f"{stock_name}({code}) 基本面快照写入失败: {e}")

            # Step 3: 趋势分析（基于交易理念）— 在 Agent 分支之前执行，供两条路径共用
            trend_result: TrendAnalysisResult | None = None
            try:
                from src.services.history_loader import get_frozen_target_date

                _mkt = get_market_for_stock(normalize_stock_code(code))
                frozen = get_frozen_target_date()
                end_date = frozen if frozen else get_market_now(_mkt).date()
                start_date = end_date - timedelta(days=89)  # ~60 trading days for MA60
                historical_bars = self.db.get_data_range(code, start_date, end_date)
                if historical_bars:
                    df = pd.DataFrame([bar.to_dict() for bar in historical_bars])
                    # Issue #234: Augment with realtime for intraday MA calculation
                    if self.config.enable_realtime_quote and realtime_quote:
                        df = self._augment_historical_with_realtime(df, realtime_quote, code)
                    trend_result = self.trend_analyzer.analyze(df, code)
                    logger.info(
                        f"{stock_name}({code}) 趋势分析: {trend_result.trend_status.value}, "
                        f"买入信号={trend_result.buy_signal.value}, 评分={trend_result.signal_score}"
                    )
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 趋势分析失败: {e}", exc_info=True)

            # Step 3.5: 盘中 EastMoney + Cninfo 情报搜集（无推送）
            # 补充东方财富个股新闻和巨潮公告，写入 news_intel 供 Agent O6 注入
            _daily_intel_eastmoney_digest = None
            try:
                from src.search_service import EastMoneyNewsProvider
                from src.services.event_monitor import CninfoFetcher
                import asyncio

                em_provider = EastMoneyNewsProvider()
                ann_fetcher = CninfoFetcher()
                _em_lines = []

                # 1. EastMoney 个股新闻（按权威媒体优先排序）
                _resp = em_provider.search(code, max_results=8, days=1)
                if _resp and _resp.success and _resp.results:
                    qc = self._build_query_context(query_id=query_id)
                    self.db.save_news_intel(
                        code=code, name=stock_name,
                        dimension="daily_intel",
                        query=f"daily:per_stock:{code}",
                        response=_resp, query_context=qc,
                    )
                    logger.info(f"[{code}] 盘中情报: EastMoney 已存储 {len(_resp.results)} 条")
                    for _item in _resp.results[:5]:
                        source_tag = f"[{_item.source}]" if _item.source and _item.source != "东方财富" else ""
                        _t = (_item.title or "")[:60]
                        _s = (_item.snippet or "")[:80]
                        _em_lines.append(f"- {source_tag} {_t} {_s}")

                # 2. 巨潮公告
                _anns = asyncio.run(ann_fetcher.fetch(code, page_size=2))
                if _anns:
                    from src.storage import NewsIntel
                    with self.db.session_scope() as sess:
                        _stored = 0
                        for _ann in _anns:
                            _evt = CninfoFetcher.parse_announcement(_ann, code)
                            if _evt is None:
                                continue
                            sess.add(NewsIntel(
                                code=code, name=stock_name,
                                title=_evt.title[:300], url=_evt.url or "",
                                dimension="daily_intel",
                                query=f"daily:per_stock:{code}",
                                provider="公告", snippet=_evt.content[:200],
                                source="daily",
                            ))
                            _stored += 1
                            _em_lines.append(f"- [📋 {_evt.title[:60]}] {_evt.content[:80]}")
                        if _stored:
                            sess.commit()
                            logger.info(f"[{code}] 盘中情报: Cninfo 已存储 {_stored} 条公告")

                if _em_lines:
                    _daily_intel_eastmoney_digest = "## 📰 近日要闻（盘中情报）\n" + "\n".join(_em_lines[:8])
            except Exception as e:
                logger.debug(f"[{code}] 盘中 EastMoney+Cninfo 搜集失败: {e}")

            if use_agent:
                logger.info(f"{stock_name}({code}) 启用 Agent 模式进行分析")
                self._emit_progress(58, f"{stock_name}：正在切换 Agent 分析链路")
                return self._analyze_with_agent(
                    code,
                    report_type,
                    query_id,
                    stock_name,
                    realtime_quote,
                    chip_data,
                    fundamental_context,
                    trend_result,
                )

            # Step 4: 多维度情报搜索（最新消息+风险排查+业绩预期）
            news_context = None
            self._emit_progress(46, f"{stock_name}：正在检索新闻与舆情")
            if self.search_service is not None and self.search_service.is_available:
                logger.info(f"{stock_name}({code}) 开始多维度情报搜索...")

                # 使用多维度搜索（最多5次搜索）
                intel_results = self.search_service.search_comprehensive_intel(
                    stock_code=code, stock_name=stock_name, max_searches=5
                )

                # 格式化情报报告
                if intel_results:
                    news_context = self.search_service.format_intel_report(intel_results, stock_name)
                    total_results = sum(len(r.results) for r in intel_results.values() if r.success)
                    logger.info(f"{stock_name}({code}) 情报搜索完成: 共 {total_results} 条结果")
                    logger.debug(f"{stock_name}({code}) 情报搜索结果:\n{news_context}")

                    # 保存新闻情报到数据库（用于后续复盘与查询）
                    try:
                        query_context = self._build_query_context(query_id=query_id)
                        for dim_name, response in intel_results.items():
                            if response and response.success and response.results:
                                self.db.save_news_intel(
                                    code=code,
                                    name=stock_name,
                                    dimension=dim_name,
                                    query=response.query,
                                    response=response,
                                    query_context=query_context,
                                )
                    except Exception as e:
                        logger.warning(f"{stock_name}({code}) 保存新闻情报失败: {e}")
            else:
                logger.info(f"{stock_name}({code}) 搜索服务不可用，跳过情报搜索")

            # 追加 EastMoney/Cninfo 到 news_context（Step 3.5 采集的数据）
            if _daily_intel_eastmoney_digest:
                if news_context:
                    news_context = news_context + "\n\n" + _daily_intel_eastmoney_digest
                else:
                    news_context = _daily_intel_eastmoney_digest

            # Step 4.5: Social sentiment intelligence (US stocks only)
            if (
                self.social_sentiment_service is not None
                and self.social_sentiment_service.is_available
                and is_us_stock_code(code)
            ):
                try:
                    social_context = self.social_sentiment_service.get_social_context(code)
                    if social_context:
                        logger.info(f"{stock_name}({code}) Social sentiment data retrieved")
                        if news_context:
                            news_context = news_context + "\n\n" + social_context
                        else:
                            news_context = social_context
                except Exception as e:
                    logger.warning(f"{stock_name}({code}) Social sentiment fetch failed: {e}")

            # Step 5: 获取分析上下文（技术面数据）
            self._emit_progress(58, f"{stock_name}：正在整理分析上下文")
            context = self.db.get_analysis_context(code)

            if context is None:
                logger.warning(f"{stock_name}({code}) 无法获取历史行情数据，将仅基于新闻和实时行情分析")
                _mkt_date = get_market_now(get_market_for_stock(normalize_stock_code(code))).date()
                context = {
                    "code": code,
                    "stock_name": stock_name,
                    "date": _mkt_date.isoformat(),
                    "data_missing": True,
                    "today": {},
                    "yesterday": {},
                }

            # Step 6: 增强上下文数据（添加实时行情、筹码、趋势分析结果、股票名称）
            enhanced_context = self._enhance_context(
                context,
                realtime_quote,
                chip_data,
                trend_result,
                stock_name,  # 传入股票名称
                fundamental_context,
            )

            # Step 7: 调用 AI 分析（传入增强的上下文和新闻）
            llm_progress_state = {"last_progress": 64}

            def _on_llm_stream(chars_received: int) -> None:
                dynamic_progress = min(92, 64 + min(chars_received // 80, 28))
                if dynamic_progress <= llm_progress_state["last_progress"]:
                    return
                llm_progress_state["last_progress"] = dynamic_progress
                self._emit_progress(
                    dynamic_progress,
                    f"{stock_name}：LLM 正在生成分析结果（已接收 {chars_received} 字符）",
                )

            self._emit_progress(64, f"{stock_name}：正在请求 LLM 生成报告")
            result = self.analyzer.analyze(
                enhanced_context,
                news_context=news_context,
                progress_callback=self._emit_progress,
                stream_progress_callback=_on_llm_stream,
            )

            # Step 7.5: 填充分析时的价格信息到 result
            if result:
                self._emit_progress(94, f"{stock_name}：正在校验并整理分析结果")
                result.query_id = query_id
                realtime_data = enhanced_context.get("realtime", {})
                result.current_price = realtime_data.get("price")
                result.change_pct = realtime_data.get("change_pct")
                # 同步设置 volume_ratio 到 result，供通知推送使用
                result.volume_ratio_5d = realtime_data.get("volume_ratio")
                result.volume_ratio_is_daily = getattr(realtime_quote, "_vr_is_daily", False)

            # Step 7.6: chip_structure fallback (Issue #589)
            if result and chip_data:
                fill_chip_structure_if_needed(result, chip_data)

            # Step 7.7: price_position fallback
            if result:
                fill_price_position_if_needed(result, trend_result, realtime_quote)
                stabilize_decision_with_structure(result, trend_result, fundamental_context)

            # Step 8: 保存分析历史记录
            if result and result.success:
                try:
                    self._emit_progress(97, f"{stock_name}：正在保存分析报告")
                    context_snapshot = self._build_context_snapshot(
                        enhanced_context=enhanced_context,
                        news_content=news_context,
                        realtime_quote=realtime_quote,
                        chip_data=chip_data,
                    )
                    self.db.save_analysis_history(
                        result=result,
                        query_id=query_id,
                        report_type=report_type.value,
                        news_content=news_context,
                        context_snapshot=context_snapshot,
                        save_snapshot=self.save_context_snapshot,
                    )
                except Exception as e:
                    logger.warning(f"{stock_name}({code}) 保存分析历史失败: {e}")

            # 持久化决策信号（P0: fail-open, 不影响主流程）
            if result and result.success:
                try:
                    from src.services.decision_signal_service import DecisionSignalService

                    ds = DecisionSignalService()
                    ds.save_from_agent_result(
                        dashboard=result.dashboard if hasattr(result, "dashboard") else None,
                        stock_code=code,
                        stock_name=stock_name,
                        query_id=query_id,
                    )
                except Exception as e:
                    logger.debug(f"[{code}] 保存决策信号失败: {e}")

            return result

        except Exception as e:
            logger.error(f"{stock_name}({code}) 分析失败: {e}")
            logger.exception(f"{stock_name}({code}) 详细错误信息:")
            return None

    def _enhance_context(
        self,
        context: dict[str, Any],
        realtime_quote,
        chip_data: ChipDistribution | None,
        trend_result: TrendAnalysisResult | None,
        stock_name: str = "",
        fundamental_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        增强分析上下文

        将实时行情、筹码分布、趋势分析结果、股票名称添加到上下文中

        Args:
            context: 原始上下文
            realtime_quote: 实时行情数据（UnifiedRealtimeQuote 或 None）
            chip_data: 筹码分布数据
            trend_result: 趋势分析结果
            stock_name: 股票名称

        Returns:
            增强后的上下文
        """
        enhanced = context.copy()
        enhanced["report_language"] = normalize_report_language(getattr(self.config, "report_language", "zh"))

        # 添加股票名称
        if stock_name:
            enhanced["stock_name"] = stock_name
        elif realtime_quote and getattr(realtime_quote, "name", None):
            enhanced["stock_name"] = realtime_quote.name

        # 将运行时搜索窗口透传给 analyzer，避免与全局配置重新读取产生窗口不一致
        enhanced["news_window_days"] = getattr(self.search_service, "news_window_days", 3)

        # 添加实时行情（兼容不同数据源的字段差异）
        if realtime_quote:
            # 使用 getattr 安全获取字段，缺失字段返回 None 或默认值
            volume_ratio = getattr(realtime_quote, "volume_ratio", None)
            enhanced["realtime"] = {
                "name": getattr(realtime_quote, "name", ""),
                "price": getattr(realtime_quote, "price", None),
                "change_pct": getattr(realtime_quote, "change_pct", None),
                "volume_ratio": volume_ratio,
                "volume_ratio_desc": self._describe_volume_ratio(volume_ratio) if volume_ratio else "无数据",
                "turnover_rate": getattr(realtime_quote, "turnover_rate", None),
                "pe_ratio": getattr(realtime_quote, "pe_ratio", None),
                "pb_ratio": getattr(realtime_quote, "pb_ratio", None),
                "total_mv": getattr(realtime_quote, "total_mv", None),
                "circ_mv": getattr(realtime_quote, "circ_mv", None),
                "change_60d": getattr(realtime_quote, "change_60d", None),
                "source": getattr(realtime_quote, "source", None),
            }
            # 移除 None 值以减少上下文大小
            enhanced["realtime"] = {k: v for k, v in enhanced["realtime"].items() if v is not None}

            # ⚡ 如果实时数据中量比缺失，从日K线数据自行计算
            if "volume_ratio" not in enhanced["realtime"] and "code" in context:
                try:
                    code = context["code"]
                    rows = self.db.get_latest_data(code, days=6)
                    if rows and len(rows) >= 2:
                        today_vol = float(getattr(rows[0], "volume", 0) or 0)
                        vols = [float(getattr(r, "volume", 0) or 0) for r in rows[1:]]
                        avg_5d = sum(vols) / len(vols) if vols else 0
                        if today_vol > 0 and avg_5d > 0:
                            vr = round(today_vol / avg_5d, 2)
                            enhanced["realtime"]["volume_ratio"] = vr
                            enhanced["realtime"]["volume_ratio_desc"] = self._describe_volume_ratio(vr)
                except Exception as e:
                    logger.debug(f"[pipeline] 量比计算失败({context.get('code','?')}): {e}")

        # 添加筹码分布
        if chip_data:
            current_price = getattr(realtime_quote, "price", 0) if realtime_quote else 0
            enhanced["chip"] = {
                "profit_ratio": chip_data.profit_ratio,
                "avg_cost": chip_data.avg_cost,
                "concentration_90": chip_data.concentration_90,
                "concentration_70": chip_data.concentration_70,
                "chip_status": chip_data.get_chip_status(current_price or 0),
            }

        # 添加趋势分析结果
        if trend_result:
            enhanced["trend_analysis"] = {
                "trend_status": trend_result.trend_status.value,
                "ma_alignment": trend_result.ma_alignment,
                "trend_strength": trend_result.trend_strength,
                "bias_ma5": trend_result.bias_ma5,
                "bias_ma10": trend_result.bias_ma10,
                "volume_status": trend_result.volume_status.value,
                "volume_trend": trend_result.volume_trend,
                "buy_signal": trend_result.buy_signal.value,
                "signal_score": trend_result.signal_score,
                "signal_reasons": trend_result.signal_reasons,
                "risk_factors": trend_result.risk_factors,
            }

        # Issue #234: Override today with realtime OHLC + trend MA for intraday analysis
        # Guard: trend_result.ma5 > 0 ensures MA calculation succeeded (data sufficient)
        if realtime_quote and trend_result and trend_result.ma5 > 0:
            price = getattr(realtime_quote, "price", None)
            if price is not None and price > 0:
                yesterday_close = None
                if enhanced.get("yesterday") and isinstance(enhanced["yesterday"], dict):
                    yesterday_close = enhanced["yesterday"].get("close")
                orig_today = enhanced.get("today") or {}
                open_p = (
                    getattr(realtime_quote, "open_price", None)
                    or getattr(realtime_quote, "pre_close", None)
                    or yesterday_close
                    or orig_today.get("open")
                    or price
                )
                high_p = getattr(realtime_quote, "high", None) or price
                low_p = getattr(realtime_quote, "low", None) or price
                vol = getattr(realtime_quote, "volume", None)
                amt = getattr(realtime_quote, "amount", None)
                pct = getattr(realtime_quote, "change_pct", None)
                realtime_today = {
                    "close": price,
                    "open": open_p,
                    "high": high_p,
                    "low": low_p,
                    "ma5": trend_result.ma5,
                    "ma10": trend_result.ma10,
                    "ma20": trend_result.ma20,
                }
                if vol is not None:
                    realtime_today["volume"] = vol
                if amt is not None:
                    realtime_today["amount"] = amt
                if pct is not None:
                    realtime_today["pct_chg"] = pct
                for k, v in orig_today.items():
                    if k not in realtime_today and v is not None:
                        realtime_today[k] = v
                enhanced["today"] = realtime_today
                enhanced["ma_status"] = self._compute_ma_status(
                    price, trend_result.ma5, trend_result.ma10, trend_result.ma20
                )
                enhanced["date"] = (
                    get_market_now(get_market_for_stock(normalize_stock_code(enhanced.get("code", ""))))
                    .date()
                    .isoformat()
                )
                if yesterday_close is not None:
                    try:
                        yc = float(yesterday_close)
                        if yc > 0:
                            enhanced["price_change_ratio"] = round((price - yc) / yc * 100, 2)
                    except (TypeError, ValueError):
                        pass
                if vol is not None and enhanced.get("yesterday"):
                    yest_vol = enhanced["yesterday"].get("volume") if isinstance(enhanced["yesterday"], dict) else None
                    if yest_vol is not None:
                        try:
                            yv = float(yest_vol)
                            if yv > 0:
                                enhanced["volume_change_ratio"] = round(float(vol) / yv, 2)
                        except (TypeError, ValueError):
                            pass

        # ETF/index flag for analyzer prompt (Fixes #274)
        enhanced["is_index_etf"] = SearchService.is_index_or_etf(
            context.get("code", ""), enhanced.get("stock_name", stock_name)
        )

        # P0: append unified fundamental block; keep as additional context only
        enhanced["fundamental_context"] = (
            fundamental_context
            if isinstance(fundamental_context, dict)
            else self.fetcher_manager.build_failed_fundamental_context(
                context.get("code", ""),
                "invalid fundamental context",
            )
        )

        # P2: inject quantitative factor profile
        code = context.get("code", "")
        factor_text = getattr(self, "_factor_profiles", {}).get(code, "")
        if factor_text:
            enhanced["factor_profile"] = factor_text
        regime_text = getattr(self, "_regime_prompt", "")
        if regime_text:
            enhanced["regime_prompt"] = regime_text

        # P2e: inject East Money rating (market sentiment)
        em_text = self._get_stock_eastmoney_rating(code)
        if em_text:
            enhanced["eastmoney_rating"] = em_text

        # P4: portfolio allocation

        # P2+: inject LY quantitative signal (zero-intrusion, disk-file read only)
        ly_text = getattr(self, "_ly_signals", {}).get(code, "")
        if ly_text:
            enhanced["ly_signal"] = ly_text
        allocation_text = getattr(self, "_allocation_prompt", "")
        if allocation_text:
            enhanced["allocation_prompt"] = allocation_text

        # P3: ATR position sizing
        try:
            from src.core.position_sizer import build_position_prompt, compute_position_size

            price = None
            if realtime_quote:
                price = getattr(realtime_quote, "price", None)
            if not price and context.get("today") and isinstance(context["today"], dict):
                price = context["today"].get("close")
            if price and price > 0:
                close_arr = []
                with self.db.session_scope() as _session:
                    rows = _session.execute(
                        __import__("sqlalchemy").text(
                            "SELECT close, high, low FROM stock_daily WHERE code=:code ORDER BY date DESC LIMIT 30"
                        ),
                        {"code": code},
                    ).fetchall()
                if rows and len(rows) >= 14:
                    for r in reversed(rows):
                        close_arr.append(float(r[0]))
                    atr_val = 0.0
                    if len(rows) >= 15:
                        from src.core.indicators import atr as _atr_indicator
                        highs = [float(r[1]) for r in rows]
                        lows = [float(r[2]) for r in rows]
                        closes = [float(r[0]) for r in rows]
                        atr_arr = _atr_indicator(highs, lows, closes, period=14)
                        atr_val = [v for v in atr_arr if v == v][-1] if atr_arr else 0.0
                    atr_mult = _regime_atr_multiplier(self) if hasattr(self, '_regime_prompt') else 2.0
                    ps = compute_position_size(float(price), atr_val, atr_multiplier=atr_mult)
                    enhanced["position_prompt"] = build_position_prompt(ps)
        except Exception:
            logger.debug("[pipeline] 仓位大小计算失败(code=%s)", code)

        try:
            ood_warning = getattr(self, "_ood_warnings", {}).get(code)
            if ood_warning:
                enhanced["ood_warning"] = ood_warning
        except Exception:
            pass

        try:
            factor_dist = getattr(self, "_factor_distributions", {}).get(code)
            if factor_dist:
                enhanced["factor_uncertainty"] = factor_dist
        except Exception:
            pass

        try:
            boards = (fundamental_context or {}).get("belong_boards") or []
            if boards:
                enhanced["concept_context"] = f"所属概念/板块: {', '.join(boards[:5])}"
        except Exception:
            pass

        try:
            pe = getattr(realtime_quote, "pe_ratio", None) if realtime_quote else None
            pb = getattr(realtime_quote, "pb_ratio", None) if realtime_quote else None
            if pe is not None or pb is not None:
                from src.core.fundamental_calibration import (
                    build_calibration_prompt,
                    compute_fundamental_calibration,
                )

                cal = compute_fundamental_calibration(pe, pb)
                if cal.get("status") == "ok":
                    cal_prompt = build_calibration_prompt(cal, code, stock_name)
                    enhanced["fundamental_calibration"] = cal_prompt
                    if not hasattr(self, '_fundamental_calibration'):
                        self._fundamental_calibration = {}
                    self._fundamental_calibration[code] = cal_prompt
        except Exception:
            pass

        # 计算支撑/阻力位（供 LLM 替代硬猜）
        try:
            from src.core.support_resistance import compute_levels, format_levels

            with self.db.session_scope() as sess:
                rows = sess.execute(
                    __import__("sqlalchemy").text(
                        "SELECT close, high, low, volume FROM stock_daily WHERE code=:code ORDER BY date"
                    ),
                    {"code": code},
                ).fetchall()
            if rows and len(rows) >= 20:
                c = [float(r[0]) for r in rows]
                h = [float(r[1]) for r in rows]
                l = [float(r[2]) for r in rows]
                v = [float(r[3]) for r in rows]
                sup, res = compute_levels(c, h, l, v)
                sr_text = format_levels(sup, res)
                if sr_text:
                    enhanced["sr_levels"] = sr_text
        except Exception:
            logger.debug("[pipeline] 支撑压力位(support_resistance)计算失败(code=%s)", code)

        # O6: inject recent daily intelligence if available
        try:
            with self.db.session_scope() as sess:
                from sqlalchemy import text

                rows = sess.execute(
                    text(
                        "SELECT title, snippet, importance, source FROM news_intel "
                        "WHERE dimension='daily_intel' "
                        "AND created_at > datetime('now', '-1 day') "
                        "ORDER BY importance DESC, created_at DESC LIMIT 8"
                    )
                ).fetchall()
                if rows:
                    lines = ["## 📰 近日要闻 (Daily Intel)"]
                    for r in rows:
                        imp = r[2]
                        imp_icon = get_importance_emoji(imp)
                        src = r[3] or ""
                        lines.append(f"{imp_icon} [{src}] {r[0][:60]} — {r[1][:80]}")
                    lines.append("> 以上要闻基于最近24小时搜集，仅供参考。")
                    enhanced["daily_intel_context"] = "\n".join(lines)
        except Exception:
            pass

        # RSS intelligence items: inject pre-fetched RSS data for this stock
        try:
            if getattr(self.config, "rss_pipeline_enabled", False):
                from src.repositories.intelligence_repo import IntelligenceRepository

                _code = context.get("code", "")
                if _code:
                    _repo = IntelligenceRepository()
                    _items = _repo.get_recent_items_by_scope("symbol", _code, limit=10, max_days=7)
                    if _items:
                        _lines = ["## 📡 RSS 情报（最近 7 天）"]
                        for _item in _items:
                            _pub = _item.published_at.strftime("%m-%d") if _item.published_at else ""
                            _src = _item.source_name or _item.source or ""
                            _lines.append(f"- [{_pub}] [{_src}] {_item.title[:80]}")
                            if _item.summary:
                                _lines.append(f"  {_item.summary[:150]}")
                        _lines.append("> 以上 RSS 情报由已配置的资讯源自动抓取，仅供参考。")
                        _rss_text = "\n".join(_lines)
                        if len(_rss_text) > 2000:
                            _rss_text = _rss_text[:2000] + "\n...（已截断）"
                        enhanced["rss_intelligence"] = _rss_text
        except Exception:
            logger.debug("[pipeline] RSS intelligence injection failed(code=%s)", context.get("code", "?"))

        # Extract market background from latest market review report
        try:
            from pathlib import Path
            import glob as _glob, re as _re

            reports_dir = Path("reports")
            pattern = str(reports_dir / "market_review_*.md")
            files = sorted(_glob.glob(pattern), reverse=True)
            if files:
                with open(files[0], "r", encoding="utf-8") as f:
                    content = f.read()
                # Extract key lines: one-liner summary, temperature, main sectors
                lines = []
                for ln in content.split("\n"):
                    if ln.startswith("> ") and "核心原因" not in ln and "操作建议" not in ln:
                        if "盘面温度" in ln or "大盘红绿灯" in ln:
                            lines.append(ln.lstrip("> ").strip())
                        elif not lines or len(lines[-1]) < 120:
                            # First non-cause/non-advice summary line
                            if len(lines) == 0:
                                lines.append(ln.lstrip("> ").strip())
                # Extract sector main line (section 三 first paragraph)
                sec3 = content.find("### 三、板块主线")
                if sec3 > 0:
                    para = content[sec3:].split("\n")
                    for p in para[1:4]:
                        p = p.strip()
                        if p and not p.startswith("|") and not p.startswith("#"):
                            if len(p) > 30:
                                lines.append(p[:150])
                            break
                if lines:
                    enhanced["market_background"] = "## 📊 今日大盘背景\n" + "\n".join(f"> {l}" for l in lines[:3])
                    enhanced["market_background"] += "\n> 以上大盘背景基于最新复盘报告，供个股分析参考。"
        except Exception:
            pass

        return enhanced
    def _load_ly_signals(self, stock_codes: list[str]) -> dict[str, str]:
        """
        Load LY quantitative model signals from data/realtime/ JSON files.

        Zero-intrusion: reads disk files only, no import from lynx_vnpy.
        Returns dict[code] → formatted prompt text, or empty string on failure.
        Silently degrades if files are missing or stale (>1 day old).
        """
        from pathlib import Path
        import json
        from datetime import datetime, timedelta
        from typing import Any

        result: dict[str, dict] = {}
        realtime_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "realtime"

        # ── Source 1: RF model (ly_signal.json) ──
        rf_path = realtime_dir / "ly_signal.json"
        rf_data: dict[str, dict] = {}
        if rf_path.exists():
            try:
                raw = json.loads(rf_path.read_text(encoding="utf-8"))
                updated = raw.get("updated_at", "")
                # Staleness check: skip if older than 36 hours
                try:
                    updated_dt = datetime.strptime(updated[:10], "%Y-%m-%d")
                    if (datetime.now() - updated_dt) > timedelta(hours=36):
                        logger.debug("[LY] ly_signal.json stale (updated %s), skipping", updated)
                    else:
                        rf_data = raw.get("stocks", {})
                except (ValueError, TypeError):
                    rf_data = raw.get("stocks", {})
            except Exception as e:
                logger.debug("[LY] Failed to read ly_signal.json: %s", e)

        # ── Source 2: Alpha158 LGB model (ly_alpha_signal.json) ──
        lgb_path = realtime_dir / "ly_alpha_signal.json"
        lgb_data: dict[str, dict] = {}
        if lgb_path.exists():
            try:
                raw = json.loads(lgb_path.read_text(encoding="utf-8"))
                lgb_data = raw.get("stocks", {})
            except Exception as e:
                logger.debug("[LY] Failed to read ly_alpha_signal.json: %s", e)

        # ── Source 3: prob_up_log.csv (ensemble + individual probabilities) ──
        csv_path = realtime_dir / "prob_up_log.csv"
        csv_latest: dict[str, dict] = {}
        if csv_path.exists():
            try:
                import csv
                with open(csv_path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                if rows:
                    latest_date = rows[-1].get("date", "")
                    for row in rows:
                        if row.get("date") == latest_date:
                            code = row.get("stock_code", "").strip()
                            if code:
                                csv_latest[code] = {
                                    "prob_up_rf": row.get("prob_up_rf", ""),
                                    "prob_up_lgb": row.get("prob_up_lgb", ""),
                                    "prob_up_ensemble": row.get("prob_up_ensemble", ""),
                                    "l7_score_rf": row.get("l7_score_rf", ""),
                                    "l7_score_lgb": row.get("l7_score_lgb", ""),
                                }
            except Exception as e:
                logger.debug("[LY] Failed to read prob_up_log.csv: %s", e)

        # ── Merge per code ──
        for code in stock_codes:
            rf = rf_data.get(code, {})
            lgb = lgb_data.get(code, {})
            csv_row = csv_latest.get(code, {})

            if not rf and not lgb and not csv_row:
                continue

            ly_info: dict[str, Any] = {}

            # Ensemble prob (prefer CSV, fallback to average of RF+LGB probs)
            ensemble = csv_row.get("prob_up_ensemble", "")
            if not ensemble:
                prf = csv_row.get("prob_up_rf") or rf.get("prob_up")
                plgb = csv_row.get("prob_up_lgb") or lgb.get("prob_up")
                try:
                    if prf != "" and plgb != "":
                        ensemble = f"{(float(prf) + float(plgb)) / 2:.1f}"
                except (ValueError, TypeError):
                    pass
            if ensemble:
                ly_info["prob_up_ensemble"] = ensemble

            # Individual model probabilities
            for key, src_key in [("prob_up_rf", "prob_up"), ("prob_up_lgb", "prob_up")]:
                val = csv_row.get(key, "") or (rf if key == "prob_up_rf" else lgb).get(src_key, "")
                if val != "":
                    ly_info[key] = val

            # Raw model scores (from JSON, NOT L7 — these are RF/LGB raw confidence scores)
            for key, src in [("raw_score_rf", rf), ("raw_score_lgb", lgb)]:
                val = src.get("score", "")
                if val != "":
                    try:
                        ly_info[key] = f"{float(val):+.3f}"
                    except (ValueError, TypeError):
                        pass

            # Volume ratio (raw technical indicator from ly_alpha_signal.json)
            vr = lgb.get("volume_ratio", "")
            if vr != "":
                try:
                    ly_info["volume_ratio"] = f"{float(vr):.2f}"
                except (ValueError, TypeError):
                    pass

            # Signal labels
            signal_label = rf.get("signal", "")
            if signal_label:
                ly_info["signal_rf"] = signal_label

            # Model disagreement
            try:
                pr = float(csv_row.get("prob_up_rf") or rf.get("prob_up", 0) or 0)
                pl = float(csv_row.get("prob_up_lgb") or lgb.get("prob_up", 0) or 0)
                if pr and pl:
                    ly_info["model_disagreement"] = f"{abs(pr - pl):.1f}%"
            except (ValueError, TypeError):
                pass

            # Confidence band (strength)
            try:
                prob = float(ly_info.get("prob_up_ensemble", 0) or 0)
                if prob >= 70:
                    strength = "强 🟢"
                elif prob >= 55:
                    strength = "中 🟡"
                else:
                    strength = "弱 🔴"
                ly_info["strength"] = strength
            except (ValueError, TypeError):
                pass

            if ly_info:
                result[code] = ly_info

        # ── Format per-code strings ──
        formatted: dict[str, str] = {}
        for code, info in result.items():
            lines = [
                "## 🤖 量化信号（LY 双模型预判）",
                "",
                "以下信号由 lynx_vnpy 的 RandomForest + Alpha158 LightGBM 双模型独立计算，仅供参考。",
                "两模型基于的技术指标：RSI、MACD、ATR、布林带、CCI 等 15+ 特征。",
                "",
                "| 指标 | 数值 | 解读 |",
                "|------|------|------|",
            ]
            ensemble = info.get("prob_up_ensemble", "N/A")
            prob_rf = info.get("prob_up_rf", "N/A")
            prob_lgb = info.get("prob_up_lgb", "N/A")

            lines.append(f"| **综合上涨概率** | **{ensemble}%** | RF+LGB 双模型集成 |")
            lines.append(f"| RF 上涨概率 | {prob_rf}% | RandomForest 分类器（15+ 技术指标特征） |")
            lines.append(f"| LGB 上涨概率 | {prob_lgb}% | Alpha158 LightGBM（158 因子增强） |")

            raw_rf = info.get("raw_score_rf", "")
            raw_lgb = info.get("raw_score_lgb", "")
            if raw_rf:
                lines.append(f"| RF 原始得分 | {raw_rf} | RandomForest 置信度（范围约 [-3,+3]），正值偏多，负值偏空 |")
            if raw_lgb:
                lines.append(f"| LGB 原始得分 | {raw_lgb} | Alpha158 LightGBM 置信度（范围约 [-3,+3]），正值偏多，负值偏空 |")

            vr = info.get("volume_ratio", "")
            if vr:
                lines.append(f"| 量比(LGB) | {vr} | Alpha158 原始因子：当日成交量 / 近5日均量，>1.2 放量，<0.8 缩量 |")

            signal = info.get("signal_rf", "")
            if signal:
                lines.append(f"| RF 信号标签 | {signal} | 7 级信号分类 |")

            strength = info.get("strength", "")
            if strength:
                lines.append(f"| 综合置信度 | {strength} | 强(≥70%) / 中(55-70%) / 弱(<55%) |")

            disagreement = info.get("model_disagreement", "")
            if disagreement:
                try:
                    dval = float(disagreement.replace("%", ""))
                    level = "⚠️ 高分歧（需结合其他信号综合判断）" if dval > 15 else "✅ 低分歧（模型共识较好）"
                except (ValueError, TypeError):
                    level = "判断中"
                lines.append(f"| 模型分歧度 | {disagreement} | {level} |")

            lines.append("")
            lines.append("> ⚠️ 量化信号仅反映技术面统计概率，不构成投资建议。请结合基本面、消息面综合判断。")
            lines.append("> 当 RF 与 LGB 分歧较大(>15%)时，说明技术形态信号不明确，建议降低此维度权重。")

            formatted[code] = "\n".join(lines)

        if formatted:
            logger.info("[LY] Loaded signal data for %d/%d stocks", len(formatted), len(stock_codes))
        else:
            logger.debug("[LY] No LY signal data available (files missing or stale)")

        return formatted

    def _get_stock_eastmoney_rating(self, code: str) -> str:
        """读取东方财富评级缓存，返回个股评级文本（供LLM prompt注入）。"""
        try:
            from pathlib import Path
            import json
            _root = Path(__file__).resolve().parent.parent.parent.parent
            cache_path = _root / "data" / "realtime" / "eastmoney_rating.json"
            if not cache_path.exists():
                return ""
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            stock = data.get("stocks", {}).get(code)
            if not stock:
                return ""
            parts = ["东方财富(平台用户行为聚合)"]
            d = stock.get("desire")
            if d is not None:
                parts.append(f"意愿={d}")
            f_avg = stock.get("focus_avg")
            if f_avg is not None:
                parts.append(f"关注度={f_avg}")
            inst = stock.get("institution")
            if inst is not None:
                parts.append(f"机构参与度={inst}")
            score = stock.get("score")
            if score is not None:
                parts.append(f"综合得分={score}")
            fetched = data.get("fetched_at", "")
            if fetched:
                parts.append(f"数据时间:{fetched}")
            return " ".join(parts)
        except Exception:
            return ""

    def _attach_belong_boards_to_fundamental_context(
        self,
        code: str,
        fundamental_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Attach A-share board membership as a top-level supplemental field.

        Keep this as a shallow copy so cached fundamental contexts are not
        mutated in place after retrieval.
        """
        if isinstance(fundamental_context, dict):
            enriched_context = dict(fundamental_context)
        else:
            enriched_context = self.fetcher_manager.build_failed_fundamental_context(
                code,
                "invalid fundamental context",
            )

        existing_boards = enriched_context.get("belong_boards")
        if isinstance(existing_boards, list):
            enriched_context["belong_boards"] = list(existing_boards)
            return enriched_context

        boards_block = enriched_context.get("boards")
        boards_status = boards_block.get("status") if isinstance(boards_block, dict) else None
        coverage = enriched_context.get("coverage")
        boards_coverage = coverage.get("boards") if isinstance(coverage, dict) else None
        market = enriched_context.get("market")
        if not isinstance(market, str) or not market.strip():
            market = get_market_for_stock(normalize_stock_code(code))

        if market != "cn" or boards_status == "not_supported" or boards_coverage == "not_supported":
            enriched_context["belong_boards"] = []
            return enriched_context

        boards: list[dict[str, Any]] = []
        try:
            raw_boards = self.fetcher_manager.get_belong_boards(code)
            if isinstance(raw_boards, list):
                boards = raw_boards
        except Exception as e:
            logger.debug("%s attach belong_boards failed (fail-open): %s", code, e)

        enriched_context["belong_boards"] = boards
        return enriched_context

    def _ensure_agent_history(self, code: str, min_days: int = 240) -> None:
        """Ensure at least *min_days* of K-line history is in DB for agent tools."""
        from src.services.history_loader import get_frozen_target_date

        target = get_frozen_target_date()
        if target is None:
            target = self._resolve_resume_target_date(code)
        start = target - timedelta(days=int(min_days * 1.8))
        bars = self.db.get_data_range(code, start, target)
        if bars and len(bars) >= min(min_days, 200):
            logger.debug("[%s] Agent history: %d bars in DB, sufficient", code, len(bars))
            return
        try:
            df, source = self.fetcher_manager.get_daily_data(code, days=min_days)
            if df is not None and not df.empty:
                self.db.save_daily_data(df, code, source)
                logger.info("[%s] Prefetched %d rows of history for agent (source: %s)", code, len(df), source)
        except Exception as e:
            logger.warning("[%s] Agent history prefetch failed: %s", code, e)

    def _analyze_with_agent(
        self,
        code: str,
        report_type: ReportType,
        query_id: str,
        stock_name: str,
        realtime_quote: Any,
        chip_data: ChipDistribution | None,
        fundamental_context: dict[str, Any] | None = None,
        trend_result: TrendAnalysisResult | None = None,
    ) -> AnalysisResult | None:
        """
        使用 Agent 模式分析单只股票。
        """
        try:
            from src.agent.factory import build_agent_executor

            report_language = normalize_report_language(getattr(self.config, "report_language", "zh"))

            requested_skills = (
                self.analysis_skills
                if self.analysis_skills is not None
                else (getattr(self.config, "agent_skills", None) or None)
            )
            # Build executor from shared factory (ToolRegistry and SkillManager prototype are cached)
            executor = build_agent_executor(self.config, requested_skills)

            # Build initial context to avoid redundant tool calls
            initial_context = {
                "stock_code": code,
                "stock_name": stock_name,
                "report_type": report_type.value,
                "report_language": report_language,
                "fundamental_context": fundamental_context,
            }
            if self.analysis_skills is not None:
                initial_context["skills"] = self.analysis_skills

            if realtime_quote:
                initial_context["realtime_quote"] = self._safe_to_dict(realtime_quote)
                # ⚡ 如果实时数据中量比缺失，从日K线数据自行计算（与 _enhance_context 相同逻辑）
                if getattr(realtime_quote, "volume_ratio", None) is None:
                    try:
                        rows = self.db.get_latest_data(code, days=6)
                        if rows and len(rows) >= 2:
                            today_vol = float(getattr(rows[0], "volume", 0) or 0)
                            vols = [float(getattr(r, "volume", 0) or 0) for r in rows[1:]]
                            avg_5d = sum(vols) / len(vols) if vols else 0
                            if today_vol > 0 and avg_5d > 0:
                                vr = round(today_vol / avg_5d, 2)
                                initial_context["realtime_quote"]["volume_ratio"] = vr
                    except Exception as e:
                        logger.debug(f"[pipeline] 整点量比计算失败({code}): {e}")
            if chip_data:
                initial_context["chip_distribution"] = self._safe_to_dict(chip_data)
            if trend_result:
                initial_context["trend_result"] = self._safe_to_dict(trend_result)

            # East Money rating (market sentiment) for agent path
            em_text = self._get_stock_eastmoney_rating(code)
            if em_text:
                initial_context["eastmoney_rating"] = em_text

            # Agent path: inject social sentiment as news_context so both
            # executor (_build_user_message) and orchestrator (ctx.set_data)
            # can consume it through the existing news_context channel
            if (
                self.social_sentiment_service is not None
                and self.social_sentiment_service.is_available
                and is_us_stock_code(code)
            ):
                try:
                    social_context = self.social_sentiment_service.get_social_context(code)
                    if social_context:
                        existing = initial_context.get("news_context")
                        if existing:
                            initial_context["news_context"] = existing + "\n\n" + social_context
                        else:
                            initial_context["news_context"] = social_context
                        logger.info(f"[{code}] Agent mode: social sentiment data injected into news_context")
                except Exception as e:
                    logger.warning(f"[{code}] Agent mode: social sentiment fetch failed: {e}")

            # Issue #1066: ensure deep history is in DB before agent tools run
            self._ensure_agent_history(code)

            # O6: inject daily intelligence into agent path
            try:
                with self.db.session_scope() as sess:
                    from sqlalchemy import text
                    rows = sess.execute(
                        text(
                            "SELECT title, snippet, importance, source FROM news_intel "
                            "WHERE dimension='daily_intel' "
                            "AND created_at > datetime('now', '-1 day') "
                            "ORDER BY importance DESC, created_at DESC LIMIT 8"
                        )
                    ).fetchall()
                    if rows:
                        lines = ["## 📰 近日要闻 (Daily Intel)"]
                        for r in rows:
                            imp = r[2]
                            imp_icon = get_importance_emoji(imp)
                            src = r[3] or ""
                            lines.append(f"{imp_icon} [{src}] {r[0][:60]} — {r[1][:80]}")
                        lines.append("> 以上要闻基于最近24小时搜集，仅供参考。")
                        daily_intel = "\n".join(lines)
                        existing = initial_context.get("news_context", "")
                        initial_context["news_context"] = daily_intel + ("\n\n" + existing if existing else "")
            except Exception:
                logger.debug("[pipeline] 每日情报(daily_intel)注入失败(code=%s)", code)

            # RSS intelligence: inject pre-fetched RSS items for this stock
            try:
                if getattr(self.config, "rss_pipeline_enabled", False):
                    from src.repositories.intelligence_repo import IntelligenceRepository

                    _repo = IntelligenceRepository()
                    _items = _repo.get_recent_items_by_scope("symbol", code, limit=10, max_days=7)
                    if _items:
                        _lines = ["## 📡 RSS 情报（最近 7 天）"]
                        for _item in _items:
                            _pub = _item.published_at.strftime("%m-%d") if _item.published_at else ""
                            _src = _item.source_name or _item.source or ""
                            _lines.append(f"- [{_pub}] [{_src}] {_item.title[:80]}")
                            if _item.summary:
                                _lines.append(f"  {_item.summary[:150]}")
                        _lines.append("> 以上 RSS 情报由已配置的资讯源自动抓取，仅供参考。")
                        _rss_text = "\n".join(_lines)
                        if len(_rss_text) > 2000:
                            _rss_text = _rss_text[:2000] + "\n...（已截断）"
                        initial_context["rss_intelligence"] = _rss_text
                        logger.info("[%s] RSS intelligence injected into agent context", code)
            except Exception:
                logger.debug("[pipeline] RSS情报注入失败(code=%s)", code)

            # Inject market background into agent message
            try:
                from pathlib import Path
                import glob as _glob
                files = sorted(_glob.glob("reports/market_review_*.md"), reverse=True)
                if files:
                    with open(files[0], "r", encoding="utf-8") as f:
                        content = f.read()
                    lines = []
                    for ln in content.split("\n"):
                        if ln.startswith("> ") and "核心原因" not in ln and "操作建议" not in ln:
                            if "盘面温度" in ln or "大盘红绿灯" in ln:
                                lines.append(ln.lstrip("> ").strip())
                            elif not lines:
                                lines.append(ln.lstrip("> ").strip())
                    sec3 = content.find("### 三、板块主线")
                    if sec3 > 0:
                        for p in content[sec3:].split("\n")[1:4]:
                            p = p.strip()
                            if p and not p.startswith("|") and not p.startswith("#") and len(p) > 30:
                                lines.append(p[:150])
                                break
                    if lines:
                        market_bg = "## 📊 今日大盘背景\n" + "\n".join(f"> {l}" for l in lines[:3])
                        market_bg += "\n> 以上大盘背景基于最新复盘报告，供个股分析参考。"
                        existing = initial_context.get("news_context", "")
                        initial_context["news_context"] = market_bg + ("\n\n" + existing if existing else "")
            except Exception:
                pass

            # P4: 注入股票知识库（历史对比，非覆盖）
            try:
                from src.core.stock_knowledge import build_stock_knowledge

                knowledge = build_stock_knowledge(code)
                initial_context["knowledge_prompt"] = knowledge.get("knowledge_prompt", "")
            except Exception as exc:
                logger.debug("[KnowledgeBase] %s unavailable: %s", code, exc)

            # 注入 concept_context（所属板块）到 agent 上下文
            try:
                _boards = (fundamental_context or {}).get("belong_boards") or []
                if _boards:
                    initial_context["concept_context"] = f"所属概念/板块: {', '.join(_boards[:5])}"
            except Exception:
                pass

            # 注入 sr_levels（支撑/阻力位）到 agent 上下文
            try:
                from src.core.support_resistance import compute_levels, format_levels
                with self.db.session_scope() as sess:
                    _rows = sess.execute(
                        __import__("sqlalchemy").text(
                            "SELECT close, high, low, volume FROM stock_daily WHERE code=:code ORDER BY date"
                        ),
                        {"code": code},
                    ).fetchall()
                if _rows and len(_rows) >= 20:
                    _c = [float(r[0]) for r in _rows]
                    _h = [float(r[1]) for r in _rows]
                    _l = [float(r[2]) for r in _rows]
                    _v = [float(r[3]) for r in _rows]
                    _sup, _res = compute_levels(_c, _h, _l, _v)
                    _sr_text = format_levels(_sup, _res)
                    if _sr_text:
                        initial_context["sr_levels"] = _sr_text
            except Exception:
                pass

            # P5: 注入定量锚定数据 — 因子/机制/仓位/估值校准
            factor_text = getattr(self, "_factor_profiles", {}).get(code, "")
            if factor_text:
                initial_context["factor_profile"] = factor_text
            regime_text = getattr(self, "_regime_prompt", "")
            if regime_text:
                initial_context["regime_prompt"] = regime_text
            allocation_text = getattr(self, "_allocation_prompt", "")
            if allocation_text:
                initial_context["allocation_prompt"] = allocation_text

            # Inject market phase context so LLM knows current trading session timing
            try:
                from src.market_phase_prompt import build_market_phase_prompt_for_stock

                _phase_prompt = build_market_phase_prompt_for_stock(code)
                if _phase_prompt:
                    initial_context["market_phase_prompt"] = _phase_prompt
            except Exception:
                pass

            position_text = ""
            try:
                from src.core.position_sizer import build_position_prompt, compute_position_size

                price = None
                if realtime_quote:
                    price = getattr(realtime_quote, "price", None)
                if not price and trend_result:
                    price = getattr(trend_result, "current_price", None)
                if price and price > 0:
                    # Compute ATR from DB (matching traditional path at line 676-700)
                    with self.db.session_scope() as _session:
                        rows = _session.execute(
                            __import__("sqlalchemy").text(
                                "SELECT close, high, low FROM stock_daily WHERE code=:code ORDER BY date DESC LIMIT 30"
                            ),
                            {"code": code},
                        ).fetchall()
                    atr_val = 0.0
                    if rows and len(rows) >= 15:
                        from src.core.indicators import atr as _atr_indicator
                        highs = [float(r[1]) for r in rows]
                        lows = [float(r[2]) for r in rows]
                        closes = [float(r[0]) for r in rows]
                        atr_arr = _atr_indicator(highs, lows, closes, period=14)
                        atr_val = [v for v in atr_arr if v == v][-1] if atr_arr else 0.0
                    # Use regime ATR multiplier if available, else default 2.0
                    atr_mult = _regime_atr_multiplier(self) if hasattr(self, '_regime_prompt') else 2.0
                    ps = compute_position_size(float(price), atr_val, atr_multiplier=atr_mult)
                    position_text = build_position_prompt(ps)
            except Exception:
                pass
            if position_text:
                initial_context["position_prompt"] = position_text
            cal_text = getattr(self, "_fundamental_calibration", {}).get(code, "")
            if cal_text:
                initial_context["fundamental_calibration"] = cal_text

            # 运行 Agent

            # 注入 LY 量化信号（与 traditional path 共用 _ly_signals）
            ly_text = getattr(self, "_ly_signals", {}).get(code, "")
            if ly_text:
                initial_context["ly_signal"] = ly_text
            if report_language == "en":
                message = f"Analyze stock {code} ({stock_name}) and return the full decision dashboard JSON in English."
            else:
                message = f"请分析股票 {code} ({stock_name})，并生成决策仪表盘报告。"

            # 注入知识库到消息末尾（对比参考）
            kb_text = initial_context.get("knowledge_prompt", "")
            if kb_text:
                message += "\n\n---\n\n" + kb_text
                message += "\n\n> 📋 对比指令：请将历史背景与当前数据做比较分析。如历史结论与当前数据存在重大冲突（如上次看空、本次看多），请在分析摘要中备注说明原因。**重要**：上述历史分析分数是 AI 自身之前的判断——仅作为背景参考。应以当前 OHLCV 衍生数据为主要判断依据。若历史判断与当前数据矛盾，优先跟随当前数据。"

            # 注入数据新鲜度：告知 AI 各项数据的采集时间和来源，帮助判断可靠性
            from datetime import datetime as _dt

            _now_str = _dt.now().strftime("%Y-%m-%d %H:%M")
            message += f"\n\n---\n\n## ⏱️ 数据新鲜度\n> 当前时间: {_now_str}\n> 日线数据来源: 17取数器优先级链 (efinance→akshare→tushare→...)\n> 实时行情: 腾讯/新浪/东财 (缓存≤10min)\n> 因子评分: 基于最近60个交易日计算\n> 估值校准: 当日PE/PB vs 行业均值\n> ⚠️ 如某项数据早于当前日期,请降低其在决策中的参考权重"

            # === C1 修复：信号相关性提示 ===
            # 告知 LLM 定量因子、技术指标、体制分类共享相同底层 OHLCV 数据
            message += (
                "\n\n---\n\n## ⚠️ 信号相关性提示\n\n"
                "以下定量因子得分（z-scores）、市场体制状态、ATR 仓位建议均源自相同的基础行情数据（OHLCV）。它们并非独立的验证来源。\n\n"
                "**信号分组**（同组内信号高度相关，跨组间独立性较强）：\n"
                "- **趋势组**：均线排列、momentum_reversal、momentum_spread、regime 趋势分类、consecutive_direction\n"
                "- **波动/风险组**：low_volatility、volatility_ratio、regime 波动分类、max_effect\n"
                "- **位置组**：price_position、乖离率\n\n"
                "**解读规则**：\n"
                "1. 同组内多个信号方向一致 → 构成额外确认（约 1.2-1.5× 证据强度），**不是**多重独立验证\n"
                "2. 跨组信号方向一致（如趋势组看多 + 波动组看多）→ 置信度可适度更高（约 1.5-2.0×）\n"
                "3. 同组内信号方向冲突 → 该组信号本身存在不确定性，降低该维度的权重\n"
            )
            # === C2 修复：成交量多机制区分 ===
            # 告知 LLM 逆向/情绪类 vs 趋势跟随/量价确认类是不同的市场机制
            message += (
                "\n\n---\n\n## 📊 成交量信号多机制说明\n\n"
                "成交量衍生的多个信号测量的是**不同的市场机制**：\n"
                "- **逆向/情绪类**（turnover_sentiment、emotion_cycle）：高成交量 = 散户追涨/情绪过热 → 偏空/谨慎\n"
                "- **趋势跟随/量价确认类**（volume_breakout、volume_status、bottom_volume、放量拉升）：高成交量 = 突破确认/主力介入/恐慌出清 → 偏多\n"
                "> 请分别评估这两种机制，而非将其视为矛盾信号。\n"
            )


                        # === F1+F2 修复：因子 vs 趋势分析关系 + 优先级指引 ===
            message += (
                "\n\n---\n\n## 📊 因子评分与趋势分析的关系\n\n"
                "因子 z-scores（FactorEngine）与趋势评分（StockTrendAnalyzer）代表了**两种不同的评分体系**：\n"
                "- **因子层**：IC/IR 经验校准的定量笼子，划定评分区间（方向性判断优先）\n"
                "- **趋势层**：用户交易理念驱动的规则信号（入场时机判断优先）\n"
                "> 两者一致时适度提升置信度；分歧时：方向性以因子为主导，时机性以趋势为主导。\n"
            )

            # === C5+C6 修复：策略规则关系 + S/R 使用规则 ===
            message += (
                "\n\n---\n\n## 📋 策略规则与支撑阻力使用指引\n\n"
                "**策略规则**：提示中已包含原始行情数据（OHLCV、均线值、乖离率、量比、换手率等）。"
                "策略 YAML 中定义的触发条件（如 MA5 > MA10 > MA20、量比 > 2.0）可直接对照原始数据进行判断。"
                "请勿将「对照数据检查策略条件」视为独立于原始数据的额外分析步骤。\n"
                "**支撑/阻力位**：请直接使用系统计算的关键支撑/阻力位（基于 OHLCV 聚类）"
                "填充 support_level 和 resistance_level 字段，而非自行重新推导。若系统未能提供，再自行计算。\n"
            )

            # 注入定量锚定数据（因子+机制+仓位+PE/PB+概念+S/R校准）
            for _key, _label in [
                ("factor_profile", "📊 量化因子评分"),
                ("regime_prompt", "🌡️ 市场体制状态"),
                ("allocation_prompt", "📐 组合风险分配"),
                ("position_prompt", "🎯 ATR 动态仓位建议"),
                ("concept_context", "📂 所属概念/板块"),
                ("sr_levels", "📊 关键支撑/阻力位"),
                ("fundamental_calibration", "💰 PE/PB 估值校准"),
            ]:
                _val = initial_context.get(_key, "")
                if _val:
                    message += f"\n\n---\n\n## {_label}\n{_val}"

            agent_result = executor.run(message, context=initial_context)

            # 转换为 AnalysisResult
            result = self._agent_result_to_analysis_result(
                agent_result,
                code,
                stock_name,
                report_type,
                query_id,
                trend_result=trend_result,
            )
            if result:
                result.query_id = query_id

            if result and result.success:
                factor_score = self._factor_scores.get(code, 0.0)
                if abs(factor_score) > 0.01:
                    factor_mapped = int(50 + factor_score * 15)
                    factor_mapped = max(10, min(90, factor_mapped))
                    llm_score = result.sentiment_score
                    divergence = abs(llm_score - factor_mapped)
                    if divergence >= 20:
                        # D2 fix: time-series normalization is reliable for N=1 (self-historical
                        # distribution). Use 0.7/0.3 to preserve some cross-sectional context gap.
                        n_stocks = getattr(self, "_stock_count", 0)
                        llm_w = 0.7 if n_stocks == 1 else 0.6
                        factor_w = 0.3 if n_stocks == 1 else 0.4
                        logger.info(
                            "[锚定] %s LLM=%d vs Factor=%d (divergence=%d, N=%d, llm_w=%.1f)",
                            code, llm_score, factor_mapped, divergence, n_stocks, llm_w,
                        )
                        result.sentiment_score = int(llm_score * llm_w + factor_mapped * factor_w)
                        if result.dashboard:
                            result.dashboard.setdefault("quant_summary", {})
                            result.dashboard["quant_summary"]["factor_anchor"] = (
                                f"⚠️ LLM({llm_score})与因子({factor_mapped})分歧{divergence}分,"
                                f" 加权融合为{result.sentiment_score}"
                            )

            # Agent weak integrity: placeholder fill only, no LLM retry
            if result and getattr(self.config, "report_integrity_enabled", False):
                from src.analyzer import apply_placeholder_fill, check_content_integrity

                pass_integrity, missing = check_content_integrity(result)
                if not pass_integrity:
                    apply_placeholder_fill(result, missing)
                    # D3 fix: mark that placeholders were filled so downstream consumers know
                    if result.dashboard:
                        result.dashboard.setdefault("quant_summary", {})
                        result.dashboard["quant_summary"]["integrity_placeholder_filled"] = True
                    logger.info(
                        "[LLM完整性] integrity_mode=agent_weak 必填字段缺失 %s，已占位补全",
                        missing,
                    )
            # chip_structure fallback (Issue #589), before save_analysis_history
            if result and chip_data:
                fill_chip_structure_if_needed(result, chip_data)

            # price_position fallback (same as non-agent path Step 7.7)
            if result:
                fill_price_position_if_needed(result, trend_result, realtime_quote)
                realtime_data = initial_context.get("realtime_quote", {})
                if isinstance(realtime_data, dict):
                    result.current_price = realtime_data.get("price")
                    result.change_pct = realtime_data.get("change_pct")
                    # 同步设置 volume_ratio 到 result，供通知推送使用（与 _enhance_context 相同逻辑）
                    result.volume_ratio_5d = realtime_data.get("volume_ratio")
                    result.volume_ratio_is_daily = getattr(realtime_quote, "_vr_is_daily", False)
                stabilize_decision_with_structure(result, trend_result, fundamental_context)

            # 注入量化摘要到 dashboard（移动端推送用）
            if result:
                quant_extra: dict[str, Any] = {}
                try:
                    factor_text = initial_context.get("factor_profile", "")
                    kb_text = initial_context.get("knowledge_prompt", "")
                    regime_text = getattr(self, "_regime_prompt", "")
                    if factor_text:
                        # 按行边界截断，避免切碎表格行产生孤立符号
                        _truncated = factor_text[:200]
                        _last_newline = _truncated.rfind('\n')
                        if _last_newline > 50:  # 至少在50字之后才回退行尾
                            _truncated = _truncated[:_last_newline]
                        quant_extra["factor_summary"] = _truncated
                    if kb_text:
                        quant_extra["knowledge_summary"] = kb_text[:150]
                    if regime_text:
                        quant_extra["regime_summary"] = regime_text[:100]
                    if quant_extra and result.dashboard:
                        result.dashboard.setdefault("quant_summary", {}).update(quant_extra)
                except Exception:
                    logger.debug("[pipeline] 量化摘要注入失败(code=%s)", code)

                # 注入支撑/压力位
                try:
                    from src.core.support_resistance import compute_levels, format_levels

                    with self.db.session_scope() as _session:
                        rows = _session.execute(
                            __import__("sqlalchemy").text(
                                "SELECT close, high, low, volume FROM stock_daily WHERE code=:code ORDER BY date"
                            ),
                            {"code": code},
                        ).fetchall()
                    if rows and len(rows) >= 20:
                        c = [float(r[0]) for r in rows]
                        h = [float(r[1]) for r in rows]
                        l = [float(r[2]) for r in rows]
                        v = [float(r[3]) for r in rows]
                        sup, res = compute_levels(c, h, l, v)
                        sr_text = format_levels(sup, res)
                        if sr_text and result.dashboard:
                            result.dashboard.setdefault("quant_summary", {})["sr_levels"] = sr_text
                except Exception:
                    pass

            resolved_stock_name = result.name if result and result.name else stock_name

            # 保存新闻情报到数据库（Agent 工具结果仅用于 LLM 上下文，未持久化，Fixes #396）
            # 使用 search_stock_news（与 Agent 工具调用逻辑一致），仅 1 次 API 调用，无额外延迟
            if self.search_service is not None and self.search_service.is_available:
                try:
                    news_response = self.search_service.search_stock_news(
                        stock_code=code, stock_name=resolved_stock_name, max_results=5
                    )
                    if news_response.success and news_response.results:
                        query_context = self._build_query_context(query_id=query_id)
                        self.db.save_news_intel(
                            code=code,
                            name=resolved_stock_name,
                            dimension="latest_news",
                            query=news_response.query,
                            response=news_response,
                            query_context=query_context,
                        )
                        logger.info(f"[{code}] Agent 模式: 新闻情报已保存 {len(news_response.results)} 条")
                except Exception as e:
                    logger.warning(f"[{code}] Agent 模式保存新闻情报失败: {e}")

            # 保存分析历史记录
            if result and result.success:
                try:
                    initial_context["stock_name"] = resolved_stock_name
                    # P2+P3: inject factor_profile and regime_prompt into context_snapshot
                    initial_context["factor_profile"] = self._factor_profiles.get(code, "")
                    initial_context["factor_zscores"] = self._factor_zscores.get(code, {})
                    initial_context["regime_prompt"] = getattr(self, "_regime_prompt", "")
                    self.db.save_analysis_history(
                        result=result,
                        query_id=query_id,
                        report_type=report_type.value,
                        news_content=None,
                        context_snapshot=initial_context,
                        save_snapshot=self.save_context_snapshot,
                    )
                except Exception as e:
                    logger.warning(f"[{code}] 保存 Agent 分析历史失败: {e}")

            # 持久化决策信号（P0: fail-open, 不影响主流程）
            if result and result.success:
                try:
                    from src.services.decision_signal_service import DecisionSignalService

                    ds = DecisionSignalService()
                    ds.save_from_agent_result(
                        dashboard=result.dashboard,
                        stock_code=code,
                        stock_name=resolved_stock_name,
                        query_id=query_id,
                    )
                except Exception as e:
                    logger.debug(f"[{code}] 保存决策信号失败: {e}")

            return result

        except Exception as e:
            logger.error(f"[{code}] Agent 分析失败: {e}")
            logger.exception(f"[{code}] Agent 详细错误信息:")
            return None

    def _agent_result_to_analysis_result(
        self,
        agent_result,
        code: str,
        stock_name: str,
        report_type: ReportType,
        query_id: str,
        trend_result: TrendAnalysisResult | None = None,
    ) -> AnalysisResult:
        """
        将 AgentResult 转换为 AnalysisResult。
        """
        report_language = normalize_report_language(getattr(self.config, "report_language", "zh"))
        result = AnalysisResult(
            code=code,
            name=stock_name,
            sentiment_score=50,
            trend_prediction="Unknown" if report_language == "en" else "未知",
            operation_advice="Watch" if report_language == "en" else "观望",
            confidence_level=localize_confidence_level("medium", report_language),
            report_language=report_language,
            success=agent_result.success,
            error_message=agent_result.error or None,
            data_sources=f"agent:{agent_result.provider}",
            model_used=agent_result.model or None,
        )

        if agent_result.success and agent_result.dashboard:
            dash = agent_result.dashboard
            ai_stock_name = str(dash.get("stock_name", "")).strip()
            if ai_stock_name and self._is_placeholder_stock_name(stock_name, code):
                result.name = ai_stock_name

            nested_dashboard = dash.get("dashboard") if isinstance(dash, dict) else None

            raw_score = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "sentiment_score",
                scalar=True,
            )
            if self._is_agent_field_missing(raw_score, scalar=True):
                fallback_score = self._trend_score_fallback(trend_result)
                if fallback_score is not None:
                    result.sentiment_score = fallback_score
                    self._mark_trend_fallback_source(result)
            else:
                result.sentiment_score = self._safe_int(raw_score, 50)

            raw_trend = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "trend_prediction",
                scalar=True,
                expect_text=True,
            )
            if self._is_agent_field_missing(raw_trend, scalar=True, expect_text=True):
                trend_label = self._trend_label_fallback(
                    trend_result,
                    report_language,
                )
                if trend_label:
                    result.trend_prediction = trend_label
                    self._mark_trend_fallback_source(result)
            else:
                result.trend_prediction = str(raw_trend)

            raw_advice = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "operation_advice",
                scalar=True,
                allow_dict=True,
                expect_text=True,
            )
            extracted_advice = ""
            if isinstance(raw_advice, dict):
                # LLM may return {"no_position": "...", "has_position": "..."}
                extracted_advice = self._extract_advice_text_from_dict(raw_advice)
                if extracted_advice:
                    result.operation_advice = localize_operation_advice(
                        extracted_advice,
                        report_language,
                    )
                else:
                    signal_label = self._trend_signal_fallback(
                        trend_result,
                        report_language,
                    )
                    if signal_label:
                        result.operation_advice = signal_label
                        self._mark_trend_fallback_source(result)
            elif not self._is_agent_field_missing(
                raw_advice,
                scalar=True,
                allow_dict=True,
                expect_text=True,
            ):
                result.operation_advice = (
                    str(raw_advice) if raw_advice else ("Watch" if report_language == "en" else "观望")
                )
            else:
                signal_label = self._trend_signal_fallback(trend_result, report_language)
                if signal_label:
                    result.operation_advice = signal_label
                    self._mark_trend_fallback_source(result)
            from src.agent.protocols import normalize_decision_signal

            raw_decision = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "decision_type",
                scalar=True,
                expect_text=True,
            )
            if self._is_agent_field_missing(raw_decision, scalar=True, expect_text=True):
                trend_decision = self._trend_decision_fallback(trend_result)
                decision_from_advice = infer_decision_type_from_advice(
                    result.operation_advice,
                    default="",
                )
                if decision_from_advice:
                    result.decision_type = decision_from_advice
                    if (
                        self._is_agent_field_missing(
                            raw_advice,
                            scalar=True,
                            allow_dict=True,
                            expect_text=True,
                        )
                        and not extracted_advice
                        and trend_decision
                    ):
                        self._mark_trend_fallback_source(result)
                else:
                    result.decision_type = trend_decision or "hold"
                    if trend_decision:
                        self._mark_trend_fallback_source(result)
            else:
                result.decision_type = normalize_decision_signal(raw_decision)
            result.confidence_level = localize_confidence_level(
                self._agent_dashboard_value(dash, nested_dashboard, "confidence_level") or result.confidence_level,
                report_language,
            )
            raw_summary = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "analysis_summary",
                scalar=True,
                expect_text=True,
            )
            if not self._is_agent_field_missing(raw_summary, scalar=True, expect_text=True):
                result.analysis_summary = str(raw_summary)
            else:
                result.analysis_summary = self._summary_fallback_from_result(result, report_language)
            # The AI returns a top-level dict that contains a nested 'dashboard' sub-key
            # with core_conclusion / battle_plan / intelligence.  AnalysisResult's helper
            # methods (get_sniper_points, get_core_conclusion, etc.) expect that inner
            # structure, so we unwrap it here.
            result.dashboard = nested_dashboard or dash
            self._backfill_agent_dashboard_fields(result, trend_result, report_language)
        else:
            self._apply_trend_fallback(result, trend_result, report_language)
            if trend_result is not None:
                result.analysis_summary = result.analysis_summary or self._summary_fallback_from_result(
                    result, report_language
                )
                self._backfill_agent_dashboard_fields(result, trend_result, report_language)
            if not result.error_message:
                result.error_message = (
                    "Agent failed to generate a valid decision dashboard"
                    if report_language == "en"
                    else "Agent 未能生成有效的决策仪表盘"
                )

        # ── 后处理：校准 operation_advice 向 sentiment_score 对齐 ──
        StockAnalysisPipeline._calibrate_operation_advice(result, report_language)

        return result

    @staticmethod
    def _calibrate_operation_advice(result: "AnalysisResult", report_language: str) -> None:
        """如果 operation_advice 方向与 sentiment_score 矛盾，修正文本建议。

        sentiment_score 是 LLM+因子融合分数（方向准确率 80%+），
        而 operation_advice 是 LLM 直接输出的文本，经常偏保守。
        当两者方向相反时，以 sentiment_score 为准。
        """
        score = result.sentiment_score
        advice = result.operation_advice or ""

        if score is None or score < 0 or score > 100:
            return
        if not advice:
            return

        # Infer direction from score (52/49 thresholds from normalizer)
        score_bullish = score >= 52  # @calibration sentiment阈值
        score_bearish = score <= 49  # @calibration sentiment阈值

        # Infer direction from advice text
        advice_bullish = any(kw in advice for kw in ["买入", "加仓", "买", "加", "Strong Buy", "Buy"])
        advice_bearish = any(kw in advice for kw in ["卖出", "减仓", "卖", "减", "Strong Sell", "Sell"])

        # Correct contradiction: score says up, advice says down
        if score_bullish and advice_bearish:
            result.operation_advice = "买入" if report_language == "zh" else "Buy"
            logger.info("[Calibrate] %s: score=%d ↑ 但 advice=%s → 修正为买入", result.code, score, advice)

        # Correct contradiction: score says down, advice says up
        elif score_bearish and advice_bullish:
            result.operation_advice = "卖出" if report_language == "zh" else "Sell"
            logger.info("[Calibrate] %s: score=%d ↓ 但 advice=%s → 修正为卖出", result.code, score, advice)

    @staticmethod
    def _agent_dashboard_value(
        dash: dict[str, Any],
        nested_dashboard: Any,
        key: str,
        *,
        scalar: bool = False,
        allow_dict: bool = False,
        expect_text: bool = False,
    ) -> Any:
        """Read a scalar from top-level agent payload, then nested dashboard fallback."""
        value = dash.get(key) if isinstance(dash, dict) else None
        if isinstance(nested_dashboard, dict) and StockAnalysisPipeline._is_agent_field_missing(
            value,
            scalar=scalar,
            allow_dict=allow_dict,
            expect_text=expect_text,
        ):
            nested_value = nested_dashboard.get(key)
            if not StockAnalysisPipeline._is_agent_field_missing(
                nested_value,
                scalar=scalar,
                allow_dict=allow_dict,
                expect_text=expect_text,
            ):
                value = nested_value
        return value

    @staticmethod
    def _extract_advice_text_from_dict(raw_advice: dict) -> str:
        for field in ("has_position", "no_position"):
            if isinstance(raw_advice.get(field), str):
                text = raw_advice[field].strip()
                if not StockAnalysisPipeline._is_agent_placeholder_text(text):
                    return text

        for value in raw_advice.values():
            if isinstance(value, str):
                text = value.strip()
                if not StockAnalysisPipeline._is_agent_placeholder_text(text):
                    return text

        return ""

    @staticmethod
    def _is_agent_placeholder_text(text: str) -> bool:
        if not text:
            return True
        return text.lower() in {"n/a", "na", "none", "null", "unknown", "tbd"} or text in {
            "未知",
            "待补充",
            "数据缺失",
            "无",
        }

    @staticmethod
    def _is_agent_field_missing(
        value: Any,
        *,
        scalar: bool = False,
        allow_dict: bool = False,
        expect_text: bool = False,
    ) -> bool:
        if scalar and isinstance(value, dict):
            if not allow_dict or not value:
                return True
            return not StockAnalysisPipeline._extract_advice_text_from_dict(value)
        if value is None:
            return True
        if expect_text and scalar:
            if not isinstance(value, str):
                return True
        if isinstance(value, str):
            text = value.strip()
            return StockAnalysisPipeline._is_agent_placeholder_text(text)
        if isinstance(value, dict):
            if scalar:
                return not allow_dict
            return not value
        if scalar and isinstance(value, (list, tuple, set)):
            return True
        return False

    @staticmethod
    def _trend_score_fallback(trend_result: TrendAnalysisResult | None) -> int | None:
        if trend_result is None:
            return None
        try:
            score = int(getattr(trend_result, "signal_score", 0))
        except (TypeError, ValueError):
            return None
        return score if score > 0 else None

    @staticmethod
    def _trend_label_fallback(
        trend_result: TrendAnalysisResult | None,
        report_language: str = "zh",
    ) -> str:
        if trend_result is None:
            return ""
        trend_status = getattr(trend_result, "trend_status", None)
        value = getattr(trend_status, "value", None) or str(trend_status or "").strip()
        if report_language != "en":
            return value
        return localize_trend_prediction(value, report_language)

    @staticmethod
    def _trend_signal_fallback(
        trend_result: TrendAnalysisResult | None,
        report_language: str = "zh",
    ) -> str:
        if trend_result is None:
            return ""
        buy_signal = getattr(trend_result, "buy_signal", None)
        value = getattr(buy_signal, "value", None) or str(buy_signal or "").strip()
        return localize_operation_advice(value, report_language)

    @staticmethod
    def _trend_decision_fallback(trend_result: TrendAnalysisResult | None) -> str | None:
        if trend_result is None:
            return None
        signal_name = getattr(getattr(trend_result, "buy_signal", None), "name", "").lower()
        return {
            "strong_buy": "buy",
            "buy": "buy",
            "hold": "hold",
            "wait": "hold",
            "sell": "sell",
            "strong_sell": "sell",
        }.get(signal_name)

    @staticmethod
    def _mark_trend_fallback_source(result: AnalysisResult) -> None:
        if "trend:fallback" in (result.data_sources or ""):
            return
        result.data_sources = f"{result.data_sources},trend:fallback" if result.data_sources else "trend:fallback"

    @staticmethod
    def _summary_fallback_from_result(result: AnalysisResult, report_language: str) -> str:
        trend = (result.trend_prediction or "").strip()
        advice = (result.operation_advice or "").strip()
        if trend and advice:
            if report_language == "en":
                return f"Trend view: {trend}; action advice: {advice}."
            return f"趋势结论：{trend}；操作建议：{advice}。"
        return ""

    def _backfill_agent_dashboard_fields(
        self,
        result: AnalysisResult,
        trend_result: TrendAnalysisResult | None,
        report_language: str,
    ) -> None:
        if not isinstance(result.dashboard, dict):
            result.dashboard = {}
        dashboard = result.dashboard

        for key in (
            "sentiment_score",
            "trend_prediction",
            "operation_advice",
            "decision_type",
            "confidence_level",
            "analysis_summary",
        ):
            current = dashboard.get(key)
            if key == "sentiment_score":
                if self._is_agent_field_missing(current, scalar=True):
                    dashboard[key] = getattr(result, key)
            elif self._is_agent_field_missing(current, scalar=True, expect_text=True):
                dashboard[key] = getattr(result, key)

        core = dashboard.get("core_conclusion")
        if not isinstance(core, dict):
            core = {}
            dashboard["core_conclusion"] = core
        if self._is_agent_field_missing(core.get("one_sentence"), scalar=True):
            core["one_sentence"] = (
                result.analysis_summary
                or self._summary_fallback_from_result(
                    result,
                    report_language,
                )
                or ("Analysis pending" if report_language == "en" else "分析待补充")
            )

        intelligence = dashboard.get("intelligence")
        if not isinstance(intelligence, dict):
            intelligence = {}
            dashboard["intelligence"] = intelligence
        risk_alerts = intelligence.get("risk_alerts")
        if (
            "risk_alerts" not in intelligence
            or self._is_agent_field_missing(risk_alerts)
            or not isinstance(risk_alerts, list)
        ):
            risk_factors = getattr(trend_result, "risk_factors", None) or []
            intelligence["risk_alerts"] = list(risk_factors)

        if result.decision_type in ("buy", "hold"):
            battle = dashboard.get("battle_plan")
            if not isinstance(battle, dict):
                battle = {}
                dashboard["battle_plan"] = battle
            sniper_points = battle.get("sniper_points")
            if not isinstance(sniper_points, dict):
                sniper_points = {}
                battle["sniper_points"] = sniper_points
            if self._is_agent_field_missing(sniper_points.get("stop_loss"), scalar=True):
                sniper_points["stop_loss"] = self._stop_loss_fallback_from_trend(
                    trend_result,
                    report_language,
                )
            else:
                # 校验LLM生成的止损价合理性：止损应在当前价的10%~95%之间
                raw_sl = sniper_points.get("stop_loss")
                current_price = getattr(result, "current_price", None)
                if current_price and isinstance(raw_sl, (int, float)):
                    ratio = float(raw_sl) / current_price
                    if ratio <= 0.1 or ratio >= 0.95:
                        logger.warning(
                            "[%s] LLM止损价不合理: %.2f (现价%.2f, 比例%.2f) → 回退到趋势支撑",
                            result.code, raw_sl, current_price, ratio,
                        )
                        sniper_points["stop_loss"] = self._stop_loss_fallback_from_trend(
                            trend_result,
                            report_language,
                        )

            # --- ideal_buy fallback ---
            raw_ideal = sniper_points.get("ideal_buy")
            if self._is_agent_field_missing(raw_ideal, scalar=True):
                # LLM缺失 → 直接fallback
                fb = self._ideal_buy_fallback_from_rating(
                    sentiment_score=result.sentiment_score,
                    current_price=result.current_price,
                    recent_lows=getattr(trend_result, "recent_lows", {}) if trend_result else {},
                    prev_close=getattr(trend_result, "prev_close", 0) if trend_result else 0,
                    support_levels=getattr(trend_result, "support_levels", None) if trend_result else None,
                    report_language=report_language,
                )
                if fb is not None:
                    sniper_points["ideal_buy"] = fb
            else:
                # LLM生成了，校验偏差是否过大
                parsed = self._safe_parse_number(raw_ideal)
                if parsed is not None and result.current_price:
                    deviation = abs(parsed - result.current_price) / result.current_price
                    # 双重检查：偏差超5%或用绝对值超20% → 用fallback替换
                    if deviation > 0.05 or abs(parsed - result.current_price) > result.current_price * 0.20:
                        fb = self._ideal_buy_fallback_from_rating(
                            sentiment_score=result.sentiment_score,
                            current_price=result.current_price,
                            recent_lows=getattr(trend_result, "recent_lows", {}) if trend_result else {},
                            prev_close=getattr(trend_result, "prev_close", 0) if trend_result else 0,
                            support_levels=getattr(trend_result, "support_levels", None) if trend_result else None,
                            report_language=report_language,
                        )
                        if fb is not None:
                            sniper_points["ideal_buy"] = fb

    @staticmethod
    def _stop_loss_fallback_from_trend(
        trend_result: TrendAnalysisResult | None,
        report_language: str,
    ) -> Any:
        levels = getattr(trend_result, "support_levels", None) if trend_result else None
        if levels:
            return levels[0]
        return "To be completed" if report_language == "en" else "待补充"

    @staticmethod
    def _safe_parse_number(val: Any) -> float | None:
        """安全提取数字值（兼容float/int/string）"""
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).strip()
        import re
        m = re.search(r"(\d+\.?\d*)", s)
        return float(m.group(1)) if m else None

    @staticmethod
    def _ideal_buy_fallback_from_rating(
        sentiment_score: int,
        current_price: float | None,
        recent_lows: dict[int, float],
        prev_close: float,
        support_levels: list[float] | None,
        report_language: str,
    ) -> float | None:
        """
        根据系统评分和近期最低价推导 ideal_buy（替代LLM生成）

        逻辑：
        - 评级→(窗口, 缓冲系数): 评级越强→窗口越短→缓冲越小→越接近现价
        - 价格锚 = max(跌停价, N日最低)
        - ideal_buy = 价格锚 × 缓冲系数
        - 安全约束：看多评级时ideal_buy不高于当前价
        """
        if sentiment_score < 40:
            return None  # 看空不出价

        # 评级→参数映射
        if sentiment_score >= 70:
            window, buffer = 3, 1.01
        elif sentiment_score >= 60:
            window, buffer = 5, 1.02
        elif sentiment_score >= 50:
            window, buffer = 9, 1.03
        else:  # 40-49 观望
            window, buffer = 20, 1.02

        # 价格锚 = max(跌停价, N日最低)
        limit_down = round(prev_close * 0.9, 2)
        low_n = recent_lows.get(window)
        if low_n is None and support_levels:
            low_n = support_levels[0]  # fallback: 最强支撑
        if low_n is None:
            return None

        anchor = max(limit_down, low_n)
        ideal_buy = round(anchor * buffer, 2)

        # 安全约束：看多(≥50)时ideal_buy不能高于当前价
        if sentiment_score >= 50 and current_price and ideal_buy > current_price:
            ideal_buy = current_price
        # 观望(40-49)时ideal_buy不能高于当前价×1.02
        elif sentiment_score >= 40 and current_price and ideal_buy > current_price * 1.02:
            ideal_buy = round(current_price * 1.02, 2)

        return ideal_buy

    @staticmethod
    def _apply_trend_fallback(
        result: AnalysisResult,
        trend_result: TrendAnalysisResult | None,
        report_language: str,
    ) -> None:
        if trend_result is None:
            result.sentiment_score = 50
            result.operation_advice = "Watch" if report_language == "en" else "观望"
            return

        score = getattr(trend_result, "signal_score", None)
        try:
            numeric_score = int(score)
        except (TypeError, ValueError):
            numeric_score = 50
        result.sentiment_score = numeric_score if numeric_score > 0 else 50

        trend_label = StockAnalysisPipeline._trend_label_fallback(trend_result, report_language)
        if trend_label:
            result.trend_prediction = trend_label

        buy_signal = getattr(trend_result, "buy_signal", None)
        signal_label = StockAnalysisPipeline._trend_signal_fallback(
            trend_result,
            report_language,
        )
        if signal_label:
            result.operation_advice = signal_label
        else:
            result.operation_advice = "Watch" if report_language == "en" else "观望"

        from src.agent.protocols import normalize_decision_signal

        signal_name = getattr(buy_signal, "name", "").lower()
        signal_to_decision = {
            "strong_buy": "buy",
            "buy": "buy",
            "hold": "hold",
            "wait": "hold",
            "sell": "sell",
            "strong_sell": "sell",
        }
        result.decision_type = signal_to_decision.get(signal_name, result.decision_type or "hold")
        result.decision_type = normalize_decision_signal(result.decision_type)
        result.data_sources = f"{result.data_sources},trend:fallback" if result.data_sources else "trend:fallback"

    @staticmethod
    def _is_placeholder_stock_name(name: str, code: str) -> bool:
        """Return True when the stock name is missing or placeholder-like."""
        if not name:
            return True
        normalized = str(name).strip()
        if not normalized:
            return True
        if normalized == code:
            return True
        if normalized.startswith("股票"):
            return True
        if "Unknown" in normalized:
            return True
        return False

    @staticmethod
    def _safe_int(value: Any, default: int = 50) -> int:
        """安全地将值转换为整数。"""
        if value is None:
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            import re

            match = re.search(r"-?\d+", value)
            if match:
                return int(match.group())
        return default

    def _describe_volume_ratio(self, volume_ratio: float) -> str:
        """
        量比描述

        量比 = 当前成交量 / 过去5日平均成交量
        """
        if volume_ratio < 0.5:
            return "极度萎缩"
        elif volume_ratio < 0.8:
            return "明显萎缩"
        elif volume_ratio < 1.2:
            return "正常"
        elif volume_ratio < 2.0:
            return "温和放量"
        elif volume_ratio < 3.0:
            return "明显放量"
        else:
            return "巨量"

    @staticmethod
    def _compute_ma_status(close: float, ma5: float, ma10: float, ma20: float) -> str:
        """
        Compute MA alignment status from price and MA values.
        Logic mirrors storage._analyze_ma_status (Issue #234).
        """
        close = close or 0
        ma5 = ma5 or 0
        ma10 = ma10 or 0
        ma20 = ma20 or 0
        if close > ma5 > ma10 > ma20 > 0:
            return "多头排列 📈"
        elif close < ma5 < ma10 < ma20 and ma20 > 0:
            return "空头排列 📉"
        elif close > ma5 and ma5 > ma10:
            return "短期向好 🔼"
        elif close < ma5 and ma5 < ma10:
            return "短期走弱 🔽"
        else:
            return "震荡整理 ↔️"

    def _build_context_snapshot(
        self,
        enhanced_context: dict[str, Any],
        news_content: str | None,
        realtime_quote: Any,
        chip_data: ChipDistribution | None,
    ) -> dict[str, Any]:
        """
        构建分析上下文快照

        同时保留原有 dict 组装逻辑作为向后兼容的 fallback。
        """
        snapshot = {
            "enhanced_context": enhanced_context,
            "news_content": news_content,
            "realtime_quote_raw": self._safe_to_dict(realtime_quote),
            "chip_distribution_raw": self._safe_to_dict(chip_data),
            "factor_zscores": getattr(self, "_factor_zscores", {}),
        }
        if self.analysis_skills is not None:
            snapshot["skills"] = list(self.analysis_skills)

        # Enhanced context snapshot using Pydantic model (additive, fallback-safe)
        try:
            from src.schemas.analysis_context_pack import AnalysisSnapshot
            from src.services.analysis_context_builder import AnalysisContextBuilder

            context_snapshot_obj = AnalysisContextBuilder().build_snapshot(
                enhanced_context=enhanced_context,
                news_content=news_content,
                realtime_quote=realtime_quote,
                chip_data=chip_data,
                factor_zscores=getattr(self, "_factor_zscores", {}),
            )
            snapshot["analysis_context_pack"] = context_snapshot_obj.model_dump()
        except Exception:
            pass  # Fall back to existing inline logic — no degradation

        return snapshot

    @staticmethod
    def _safe_to_dict(value: Any) -> dict[str, Any] | None:
        """
        安全转换为字典
        """
        if value is None:
            return None
        if hasattr(value, "to_dict"):
            try:
                return value.to_dict()
            except Exception:
                return None
        if hasattr(value, "__dict__"):
            try:
                return dict(value.__dict__)
            except Exception:
                return None
        return None

    def _resolve_query_source(self, query_source: str | None) -> str:
        """
        解析请求来源。

        优先级（从高到低）：
        1. 显式传入的 query_source：调用方明确指定时优先使用，便于覆盖推断结果或兼容未来 source_message 来自非 bot 的场景
        2. 存在 source_message 时推断为 "bot"：当前约定为机器人会话上下文
        3. 存在 query_id 时推断为 "web"：Web 触发的请求会带上 query_id
        4. 默认 "system"：定时任务或 CLI 等无上述上下文时

        Args:
            query_source: 调用方显式指定的来源，如 "bot" / "web" / "cli" / "system"

        Returns:
            归一化后的来源标识字符串，如 "bot" / "web" / "cli" / "system"
        """
        if query_source:
            return query_source
        if self.source_message:
            return "bot"
        if self.query_id:
            return "web"
        return "system"

    def _build_query_context(self, query_id: str | None = None) -> dict[str, str]:
        """
        生成用户查询关联信息
        """
        effective_query_id = query_id or self.query_id or ""

        context: dict[str, str] = {
            "query_id": effective_query_id,
            "query_source": self.query_source or "",
        }

        if self.source_message:
            context.update(
                {
                    "requester_platform": self.source_message.platform or "",
                    "requester_user_id": self.source_message.user_id or "",
                    "requester_user_name": self.source_message.user_name or "",
                    "requester_chat_id": self.source_message.chat_id or "",
                    "requester_message_id": self.source_message.message_id or "",
                    "requester_query": self.source_message.content or "",
                }
            )

        return context

    def process_single_stock(
        self,
        code: str,
        skip_analysis: bool = False,
        single_stock_notify: bool = False,
        report_type: ReportType = ReportType.SIMPLE,
        analysis_query_id: str | None = None,
        current_time: datetime | None = None,
    ) -> AnalysisResult | None:
        """
        处理单只股票的完整流程

        包括：
        1. 获取数据
        2. 保存数据
        3. AI 分析
        4. 单股推送（可选，#55）

        此方法会被线程池调用，需要处理好异常

        Args:
            analysis_query_id: 查询链路关联 id
            code: 股票代码
            skip_analysis: 是否跳过 AI 分析
            single_stock_notify: 是否启用单股推送模式（每分析完一只立即推送）
            report_type: 报告类型枚举（从配置读取，Issue #119）
            current_time: 本轮运行冻结的参考时间，用于统一断点续传目标交易日判断

        Returns:
            AnalysisResult 或 None
        """
        logger.info(f"========== 开始处理 {code} ==========")

        from src.services.history_loader import reset_frozen_target_date, set_frozen_target_date

        frozen_td = self._resolve_resume_target_date(code, current_time=current_time)
        token = set_frozen_target_date(frozen_td)
        try:
            self._emit_progress(12, f"{code}：正在准备分析任务")
            # Step 1: 获取并保存数据
            success, error = self.fetch_and_save_stock_data(code, current_time=current_time)

            if not success:
                logger.warning(f"[{code}] 数据获取失败: {error}")
                # 即使获取失败，也尝试用已有数据分析
            else:
                self._emit_progress(16, f"{code}：行情数据准备完成")

            # 数据完整性评分 (0-100)
            _completeness = 50  # baseline: data fetched
            if hasattr(self, '_factor_scores') and code in self._factor_scores:
                _completeness += 20  # factor computed
            if self.search_service and self.search_service.is_available:
                _completeness += 15  # news search available
            if getattr(self, '_regime_prompt', None):
                _completeness += 10  # regime available
            if getattr(self, '_fundamental_calibration', {}).get(code):
                _completeness += 5   # PE/PB calibration
            logger.info("[数据完整性] %s: %d/100 (行情+%.0f+新闻+%d+体制+%d+估值+%d)",
                        code, _completeness,
                        20 if hasattr(self, '_factor_scores') and code in self._factor_scores else 0,
                        15 if self.search_service and self.search_service.is_available else 0,
                        10 if getattr(self, '_regime_prompt', None) else 0,
                        5 if getattr(self, '_fundamental_calibration', {}).get(code) else 0)

            # Step 2: AI 分析
            if skip_analysis:
                logger.info(f"[{code}] 跳过 AI 分析（dry-run 模式）")
                return None

            effective_query_id = analysis_query_id or self.query_id or uuid.uuid4().hex
            result = self.analyze_stock(code, report_type, query_id=effective_query_id)

            if result and result.success:
                logger.info(f"[{code}] 分析完成: {result.operation_advice}, 评分 {result.sentiment_score}")

                # 单股推送模式（#55）：每分析完一只股票立即推送
                if single_stock_notify:
                    self._send_single_stock_notification(
                        result,
                        report_type=report_type,
                        fallback_code=code,
                    )
            elif result:
                logger.warning(f"[{code}] 分析未成功: {result.error_message or '未知错误'}")

            return result

        except Exception as e:
            # 捕获所有异常，确保单股失败不影响整体
            logger.exception(f"[{code}] 处理过程发生未知异常: {e}")
            return None
        finally:
            reset_frozen_target_date(token)

    def run(
        self,
        stock_codes: list[str] | None = None,
        dry_run: bool = False,
        send_notification: bool = True,
        merge_notification: bool = False,
    ) -> list[AnalysisResult]:
        """
        运行完整的分析流程

        流程：
        1. 获取待分析的股票列表
        2. 使用线程池并发处理
        3. 收集分析结果
        4. 发送通知

        Args:
            stock_codes: 股票代码列表（可选，默认使用配置中的自选股）
            dry_run: 是否仅获取数据不分析
            send_notification: 是否发送推送通知
            merge_notification: 是否合并推送（跳过本次推送，由 main 层合并个股+大盘后统一发送，Issue #190）

        Returns:
            分析结果列表
        """
        start_time = time.time()

        # 使用配置中的股票列表
        if stock_codes is None:
            self.config.refresh_stock_list()
            stock_codes = self.config.stock_list

        if not stock_codes:
            logger.error("未配置自选股列表，请在 .env 文件中设置 STOCK_LIST")
            return []

        logger.info(f"===== 开始分析 {len(stock_codes)} 只股票 =====")
        logger.info(f"股票列表: {', '.join(stock_codes)}")
        logger.info(f"并发数: {self.max_workers}, 模式: {'仅获取数据' if dry_run else '完整分析'}")
        self._stock_count = len(stock_codes)

        # 冻结本轮运行的统一参考时间，避免跨市场收盘边界时同批股票使用不同目标交易日。
        resume_reference_time = datetime.now(UTC)

        # === 批量预取实时行情（优化：避免每只股票都触发全量拉取）===
        # 只有股票数量 >= 5 时才进行预取，少量股票直接逐个查询更高效
        if len(stock_codes) >= 5:
            prefetch_count = self.fetcher_manager.prefetch_realtime_quotes(stock_codes)
            if prefetch_count > 0:
                logger.info(f"已启用批量预取架构：一次拉取全市场数据，{len(stock_codes)} 只股票共享缓存")

        # Issue #455: 预取股票名称，避免并发分析时显示「股票xxxxx」
        # dry_run 仅做数据拉取，不需要名称预取，避免额外网络开销
        if not dry_run:
            self.fetcher_manager.prefetch_stock_names(stock_codes, use_bulk=False)

        # 单股推送模式（#55）：从配置读取
        single_stock_notify = getattr(self.config, "single_stock_notify", False)
        # Issue #119: 从配置读取报告类型
        report_type_str = getattr(self.config, "report_type", "simple").lower()
        if report_type_str == "brief":
            report_type = ReportType.BRIEF
        elif report_type_str == "full":
            report_type = ReportType.FULL
        else:
            report_type = ReportType.SIMPLE
        # Issue #128: 从配置读取分析间隔
        analysis_delay = getattr(self.config, "analysis_delay", 0)

        if single_stock_notify:
            logger.info(
                "已启用单股推送模式：分析仍并发执行，通知改为在结果收集侧串行发送（报告类型: %s）",
                report_type_str,
            )

        # === 量化因子 + Regime 预计算 ===
        self._factor_profiles: dict[str, str] = {}
        self._factor_scores: dict[str, float] = {}
        self._factor_zscores: dict[str, dict[str, float]] = {}
        self._factor_distributions: dict[str, dict] = {}
        self._ood_warnings: dict[str, str] = {}
        self._regime_prompt: str = ""
        self._ly_signals: dict[str, str] = {}
        try:
            from sqlalchemy import text

            import numpy as np

            from src.core.factor_engine import FactorEngine

            engine = FactorEngine()
            all_results = []
            # Collect full arrays for time-series normalization
            stock_data: dict[str, dict[str, np.ndarray]] = {}
            per_stock_regimes: list[dict] = []  # per-stock regime results for mode vote
            with self.db.session_scope() as session:
                for code in stock_codes:
                    rows = session.execute(
                        text(
                            "SELECT close, volume, high, low, pct_chg FROM stock_daily WHERE code=:code ORDER BY date"
                        ),
                        {"code": code},
                    ).fetchall()
                    if not rows or len(rows) < 30:
                        continue
                    close_arr = np.array([float(r[0]) for r in rows], dtype=float)
                    volume_arr = np.array([float(r[1]) for r in rows], dtype=float)
                    df = [
                        {
                            "close": float(r[0]),
                            "volume": float(r[1]),
                            "high": float(r[2]),
                            "low": float(r[3]),
                            "pct_chg": float(r[4]),
                        }
                        for r in rows
                    ]
                    stock_data[code] = {"close": close_arr, "volume": volume_arr}
                    try:
                        r = engine.compute_for_stock(code, df)
                        all_results.append(r)
                    except Exception as exc:
                        logger.warning("[FactorEngine] compute_for_stock(%s) failed: %s", code, exc)
                        continue
                    # Per-stock regime classification (correct: single-stock price series)
                    if len(close_arr) >= 40:
                        try:
                            from src.core.regime_classifier import classify_regime as _classify
                            regime_r = _classify(close_arr[-40:].tolist())
                            per_stock_regimes.append(regime_r)
                        except Exception:
                            pass
            # ── Regime-conditional factor weights ──────────────────────────
            # Mode-vote per-stock regimes → dominant regime → adjust weights
            try:
                from collections import Counter as _Counter
                from src.core.regime_factor_weights import (
                    get_regime_weights as _get_regime_weights,
                    make_regime_key as _make_regime_key,
                    log_weight_diff as _log_weight_diff,
                )
                from src.core.factor_engine import CORE_FACTORS as _BASE_FACTORS

                if per_stock_regimes:
                    _counter = _Counter(r.get("regime", "sideways_mid_vol") for r in per_stock_regimes)
                    _dominant = _counter.most_common(1)[0][0]
                else:
                    _dominant = "sideways_mid_vol"

                _trend, _vol = _dominant.split("_", 1) if "_" in _dominant else ("sideways", "mid_vol")
                _regime_key = _make_regime_key(_trend, _vol)
                _weight_map = _get_regime_weights(_regime_key)
                if _weight_map is not None:
                    _updated = engine.apply_regime_weights(_weight_map)
                    if _updated:
                        _base_weights = {fd.name: fd.weight for fd in _BASE_FACTORS}
                        _log_weight_diff(_regime_key, _weight_map, _base_weights)
                        logger.info(
                            "[RegimeWeights] %s — %d/%d stocks match, %d factors adjusted",
                            _regime_key,
                            _counter.get(_dominant, 0) if per_stock_regimes else 0,
                            len(per_stock_regimes) if per_stock_regimes else 0,
                            len(_updated),
                        )
            except Exception:
                logger.debug("[RegimeWeights] weight adjustment skipped", exc_info=True)
            # ── End regime weights ─────────────────────────────────────────

            if all_results and len(all_results) >= 2:
                if len(all_results) < 30:
                    engine.time_series_normalize(all_results, stock_data)
                    logger.info(
                        "[FactorEngine] time-series normalization applied for %d stocks (N<30)",
                        len(all_results),
                    )
                else:
                    engine.cross_sectional_normalize(all_results)
                    logger.info(
                        "[FactorEngine] cross-sectional normalization applied for %d stocks",
                        len(all_results),
                    )
            elif all_results and len(all_results) == 1:
                # N=1: 强制使用时序归一化 (截面归一化需要 ≥2 样本)
                engine.time_series_normalize(all_results, stock_data)
                logger.info("[FactorEngine] time-series normalization applied for single stock (N=1)")

            # Store factor profiles and scores for ALL cases (N>=1)
            for r in all_results:
                self._factor_profiles[r.code] = engine.build_factor_profile(r)
                self._factor_scores[r.code] = r.composite_score
                self._factor_zscores[r.code] = r.z_scores

            # Append cross-sectional rankings to factor profiles
            if len(all_results) >= 3:
                rank_data: dict[str, dict[str, tuple[float, int]]] = {}
                for r in all_results:
                    rank_data[r.code] = {}
                    for fd in engine.factors:
                        z = r.z_scores.get(fd.name, 0.0)
                        rank_data[r.code][fd.name] = (z, 0)
                for fd in engine.factors:
                    fn = fd.name
                    scores = [(code, rank_data[code][fn][0]) for code in rank_data]
                    scores.sort(key=lambda x: x[1], reverse=True)
                    for rank, (code, _) in enumerate(scores, 1):
                        old = rank_data[code][fn]
                        rank_data[code][fn] = (old[0], rank)
                for r in all_results:
                    lines_rank = ["", "### 横截面排名 (跨12只)"]
                    for fd in engine.factors:
                        z, rank = rank_data[r.code].get(fd.name, (0.0, 0))
                        arrow = "↑" if z > 0.1 else ("↓" if z < -0.1 else "→")
                        lines_rank.append(f"  {arrow} {fd.display_name}: {rank}/12")
                    self._factor_profiles[r.code] += "\n" + "\n".join(lines_rank)
            # F6 fix: add computation timestamp to factor profiles
            from datetime import datetime as _dt
            _ts = _dt.now().strftime("%Y-%m-%d %H:%M")
            for code in stock_codes:
                if code in self._factor_profiles:
                    self._factor_profiles[code] += f"\n> 因子计算时间: {_ts}（基于此前60个交易日数据）"
            logger.info("[FactorEngine] computed factors for %d stocks", len(all_results))
            if not self._factor_scores:
                logger.warning("[FactorEngine] _factor_scores 为空 — 所有股票因子锚定将静默退化到0.0")

            # Multicollinearity check: diagnostic monitoring, not automatic correction.
            # Factor weights are already IC/IR calibrated (1658-sample Spearman) —
            # correlated factors' joint predictive power is accounted for in the
            # calibration process. Warnings here alert the operator, not the algorithm.
            try:
                from src.core.factor_monitor import FactorMonitor

                factor_vals = {}
                for r in all_results:
                    for name, z in r.z_scores.items():
                        factor_vals.setdefault(name, []).append(z)
                if factor_vals:
                    monitor = FactorMonitor()
                    coll_warnings = monitor.check_multicollinearity(factor_vals)
                    for w in coll_warnings:
                        logger.warning(w)
            except Exception:
                pass
        except Exception as exc:
            logger.debug("Factor engine unavailable: %s", exc)

        try:
            from src.core.uncertainty import UncertaintyQuantifier

            uq = UncertaintyQuantifier()
            with self.db.session_scope() as _session:
                for code in stock_codes:
                    rows = _session.execute(
                        __import__("sqlalchemy").text(
                            "SELECT close, volume, high, low, pct_chg FROM stock_daily WHERE code=:code ORDER BY date"
                        ),
                        {"code": code},
                    ).fetchall()
                    if rows and len(rows) >= 30:
                        df = [
                            {
                                "close": float(r[0]),
                                "volume": float(r[1]),
                                "high": float(r[2]),
                                "low": float(r[3]),
                                "pct_chg": float(r[4]),
                            }
                            for r in rows
                        ]
                        self._factor_distributions[code] = uq.compute_distribution(code, df)
        except Exception:
            pass

        # F3 fix: inject uncertainty CI into factor profiles
        for code in stock_codes:
            dist = self._factor_distributions.get(code)
            profile = self._factor_profiles.get(code, "")
            if dist and profile:
                ci = dist["ci_95"]
                rob = dist["robustness"]
                rob_cn = {"high": "高", "medium": "中", "low": "低"}.get(rob, rob)
                profile += f"\n> 因子可靠性: {rob_cn} (bootstrap 95% CI: [{ci[0]:.1f}, {ci[1]:.1f}], n={dist['n_samples']})"
                self._factor_profiles[code] = profile

        # OOD detection — moved after regime computation (R2 fix)
        try:
            from src.core.uncertainty import UncertaintyQuantifier

            uq = UncertaintyQuantifier()
            if per_stock_regimes:
                from collections import Counter
                regime_label = Counter(r["regime"] for r in per_stock_regimes).most_common(1)[0][0]
            else:
                regime_label = "unknown"
            recent_regimes = [r["regime"] for r in per_stock_regimes]
            for code in stock_codes:
                result = uq.classify_ood(regime_label, recent_regimes)
                if result["is_ood"] and result["warning"]:
                    self._ood_warnings[code] = result["warning"]
        except Exception:
            pass

        # Regime: per-stock classification → mode vote (fix: concatenation produced
        # meaningless synthetic series with artificial price jumps between stocks)
        if per_stock_regimes:
            try:
                from src.core.regime_classifier import build_regime_prompt
                from collections import Counter

                regime_labels = [r["regime"] for r in per_stock_regimes]
                mode_regime = Counter(regime_labels).most_common(1)[0][0]
                # Use the first stock whose regime matches the mode for full prompt
                for r in per_stock_regimes:
                    if r["regime"] == mode_regime:
                        self._regime_prompt = build_regime_prompt(r)
                        logger.info(
                            "[Regime] %s (mode over %d/%d stocks, MA20 slope=%s)",
                            r["regime"], regime_labels.count(mode_regime),
                            len(per_stock_regimes), r["ma20_slope"],
                        )
                        break
            except Exception as exc:
                logger.debug("Regime classifier unavailable: %s", exc)

        # === 组合优化 (Phase 4) ===
        self._allocation_prompt: str = ""
        try:
            returns_per_stock = []
            valid_codes = []
            with self.db.session_scope() as session:
                for code in stock_codes:
                    rows = session.execute(
                        __import__("sqlalchemy").text(
                            "SELECT close FROM stock_daily WHERE code=:code ORDER BY date DESC LIMIT 90"
                        ),
                        {"code": code},
                    ).fetchall()
                    if rows and len(rows) >= 20:
                        closes = [float(r[0]) for r in reversed(rows)]
                        rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
                        returns_per_stock.append(rets)
                        valid_codes.append(code)
            if len(valid_codes) >= 2:
                from src.core.portfolio_optimizer import build_allocation_prompt, build_portfolio_allocation

                alloc = build_portfolio_allocation(valid_codes, returns_per_stock, "risk_parity")
                self._allocation_prompt = build_allocation_prompt(alloc, valid_codes)
                logger.info("[Portfolio] risk-parity allocation for %d stocks", len(valid_codes))
        except Exception as exc:
            logger.debug("Portfolio optimizer unavailable: %s", exc)


        # === 载入 LY 量化信号 (零侵入: 仅读 JSON 文件) ===
        try:
            self._ly_signals = self._load_ly_signals(stock_codes)
        except Exception as e:
            logger.debug("[LY] LY signal loading failed (non-fatal): %s", e)
            self._ly_signals = {}
        # 收集个股分析结果
        results: list[AnalysisResult] = []

        # 注意：max_workers 设置较低（默认3）以避免触发反爬
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交任务
            future_to_code = {
                executor.submit(
                    self.process_single_stock,
                    code,
                    skip_analysis=dry_run,
                    single_stock_notify=False,
                    report_type=report_type,  # Issue #119: 传递报告类型
                    analysis_query_id=uuid.uuid4().hex,
                    current_time=resume_reference_time,
                ): code
                for code in stock_codes
            }

            # 收集结果
            for idx, future in enumerate(as_completed(future_to_code)):
                code = future_to_code[future]
                try:
                    result = future.result()
                    if result and result.success:
                        results.append(result)
                        if single_stock_notify and send_notification and not dry_run:
                            self._send_single_stock_notification(
                                result,
                                report_type=report_type,
                                fallback_code=code,
                            )
                    elif result and not result.success:
                        logger.warning(f"[{code}] 分析结果标记为失败，不计入汇总: {result.error_message or '未知原因'}")

                    # Issue #128: 分析间隔 - 在个股分析和大盘分析之间添加延迟
                    if idx < len(stock_codes) - 1 and analysis_delay > 0:
                        # 注意：此 sleep 发生在“主线程收集 future 的循环”中，
                        # 并不会阻止线程池中的任务同时发起网络请求。
                        # 因此它对降低并发请求峰值的效果有限；真正的峰值主要由 max_workers 决定。
                        # 该行为目前保留（按需求不改逻辑）。
                        logger.debug(f"等待 {analysis_delay} 秒后继续下一只股票...")
                        time.sleep(analysis_delay)

                except Exception as e:
                    logger.error(f"[{code}] 任务执行失败: {e}")

        # 统计
        elapsed_time = time.time() - start_time

        # dry-run 模式下，数据获取成功即视为成功
        if dry_run:
            # 检查哪些股票的最新可复用交易日数据已存在
            success_count = sum(
                1
                for code in stock_codes
                if self.db.has_today_data(
                    code,
                    self._resolve_resume_target_date(code, current_time=resume_reference_time),
                )
            )
            fail_count = len(stock_codes) - success_count
        else:
            success_count = len(results)
            fail_count = len(stock_codes) - success_count

        logger.info("===== 分析完成 =====")
        logger.info(f"成功: {success_count}, 失败: {fail_count}, 耗时: {elapsed_time:.2f} 秒")

        if not dry_run:
            _trigger_factor_recalibration_if_needed()

        # 保存报告到本地文件（无论是否推送通知都保存）
        if results and not dry_run:
            self._save_local_report(results, report_type)

        # 发送通知（单股推送模式下跳过汇总推送，避免重复）
        if results and send_notification and not dry_run:
            if single_stock_notify:
                # 单股推送模式：只保存汇总报告，不再重复推送
                logger.info("单股推送模式：跳过汇总推送，仅保存报告到本地")
                self._send_notifications(results, report_type, skip_push=True)
            elif merge_notification:
                # 合并模式（Issue #190）：仅保存，不推送，由 main 层合并个股+大盘后统一发送
                logger.info("合并推送模式：跳过本次推送，将在个股+大盘复盘后统一发送")
                self._send_notifications(results, report_type, skip_push=True)
            else:
                self._send_notifications(results, report_type)

        return results


def _trigger_factor_recalibration_if_needed() -> None:
    """Trigger factor weight recalibration check after analysis completes.

    Runs asynchronously (fire-and-forget) to avoid blocking the main
    analysis pipeline. Logs warning on failure but never raises.
    """
    try:
        from src.core.factor_monitor import check_factor_recalibration

        check_factor_recalibration()
    except Exception:
        logger.debug("Factor recalibration check skipped or failed", exc_info=True)

    try:
        from src.core.auto_tune import auto_tune_if_ready

        result = auto_tune_if_ready()
        if result:
            logger.info("[AutoTune] parameter adjustments: %s", result)
    except Exception:
        logger.debug("Auto-tune check skipped or failed", exc_info=True)
