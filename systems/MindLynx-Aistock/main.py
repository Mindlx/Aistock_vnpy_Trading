"""
===================================
A股自选股智能分析系统 - 主调度程序
===================================

职责：
1. 协调各模块完成股票分析流程
2. 实现低并发的线程池调度
3. 全局异常处理，确保单股失败不影响整体
4. 提供命令行入口

使用方式：
    python main.py              # 正常运行
    python main.py --debug      # 调试模式
    python main.py --dry-run    # 仅获取数据不分析

交易理念（已融入分析）：
- 严进策略：不追高，乖离率 > 5% 不买入
- 趋势交易：只做 MA5>MA10>MA20 多头排列
- 效率优先：关注筹码集中度好的股票
- 买点偏好：缩量回踩 MA5/MA10 支撑
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from src.config import setup_env
from src.notification_noise import get_importance_emoji

_INITIAL_PROCESS_ENV = dict(os.environ)
setup_env()

# 代理配置 - 通过 USE_PROXY 环境变量控制，默认关闭
# GitHub Actions 环境自动跳过代理配置
if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
    # 本地开发环境，启用代理（可在 .env 中配置 PROXY_HOST 和 PROXY_PORT）
    proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
    proxy_port = os.getenv("PROXY_PORT", "10809")
    proxy_url = f"http://{proxy_host}:{proxy_port}"
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url

import argparse
import logging
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

from data_provider.base import canonical_stock_code
from src.config import Config, get_config
from src.logging_config import setup_logging
from src.webui_frontend import prepare_webui_frontend_assets

logger = logging.getLogger(__name__)
_RUNTIME_ENV_FILE_KEYS = set()


def _get_active_env_path() -> Path:
    env_file = os.getenv("ENV_FILE")
    if env_file:
        return Path(env_file)
    return Path(__file__).resolve().parent / ".env"


def _read_active_env_values() -> dict[str, str] | None:
    env_path = _get_active_env_path()
    if not env_path.exists():
        return {}

    try:
        values = dotenv_values(env_path)
    except Exception as exc:  # pragma: no cover - defensive branch
        logger.warning("读取配置文件 %s 失败，继续沿用当前环境变量: %s", env_path, exc)
        return None

    return {str(key): "" if value is None else str(value) for key, value in values.items() if key is not None}


_ACTIVE_ENV_FILE_VALUES = _read_active_env_values() or {}
_RUNTIME_ENV_FILE_KEYS = {key for key in _ACTIVE_ENV_FILE_VALUES if key not in _INITIAL_PROCESS_ENV}

# setup_env() already ran at import time above.
_env_bootstrapped = True


def _bootstrap_environment() -> None:
    """Load .env and apply optional local proxy settings.

    Guarded to be idempotent so it can safely be called from lazy-import
    paths used by API / bot consumers.
    """
    global _env_bootstrapped
    if _env_bootstrapped:
        return

    from src.config import setup_env

    setup_env()

    if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
        proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
        proxy_port = os.getenv("PROXY_PORT", "10809")
        proxy_url = f"http://{proxy_host}:{proxy_port}"
        os.environ["http_proxy"] = proxy_url
        os.environ["https_proxy"] = proxy_url

    _env_bootstrapped = True


def _setup_bootstrap_logging(debug: bool = False) -> None:
    """Initialize stderr-only logging before config is loaded.

    File handlers are deferred until ``config.log_dir`` is known (via the
    subsequent ``setup_logging()`` call) so that healthy runs never create
    log files in a hard-coded directory.
    """
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr for h in root.handlers
    ):
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        root.addHandler(handler)


def _setup_runtime_logging(log_dir: str, debug: bool = False) -> bool:
    """Switch to configured logging, falling back to console on file I/O errors."""
    try:
        setup_logging(log_prefix="stock_analysis", debug=debug, log_dir=log_dir)
        return True
    except OSError as exc:
        logger.warning(
            "文件日志初始化失败，已降级为控制台日志输出；日志目录 %r 当前不可写或不可创建: %s。"
            "官方 Docker 镜像启动入口会自动修复默认挂载目录权限；若仍失败，"
            "请检查是否使用了 --user、只读挂载、rootless Docker 或 NFS 等限制写入的环境。",
            log_dir,
            exc,
        )
        return False


def _get_stock_analysis_pipeline():
    """Lazily import StockAnalysisPipeline for external consumers.

    Also ensures env/proxy bootstrap has run so that API / bot consumers
    that never call ``main()`` still get ``USE_PROXY`` applied.
    """
    _bootstrap_environment()
    from src.core.pipeline import StockAnalysisPipeline as _Pipeline

    return _Pipeline


class _LazyPipelineDescriptor:
    """Descriptor that resolves StockAnalysisPipeline on first attribute access."""

    _resolved = None

    def __set_name__(self, owner, name):
        self._name = name

    def __get__(self, obj, objtype=None):
        if self._resolved is None:
            self._resolved = _get_stock_analysis_pipeline()
        return self._resolved


class _ModuleExports:
    StockAnalysisPipeline = _LazyPipelineDescriptor()


_exports = _ModuleExports()


def __getattr__(name: str):
    if name == "StockAnalysisPipeline":
        return _exports.StockAnalysisPipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _reload_env_file_values_preserving_overrides() -> None:
    """Refresh `.env`-managed env vars without clobbering process env overrides."""
    global _RUNTIME_ENV_FILE_KEYS

    latest_values = _read_active_env_values()
    if latest_values is None:
        return

    # API Key/Token/Secret 类变量不受 _INITIAL_PROCESS_ENV 保护，
    # 允许 ~/.secrets 更新后覆盖进程启动时的旧值
    _FORCE_RELOAD_PATTERNS = ("API_KEY", "_TOKEN", "_SECRET", "API_KEYS")
    managed_keys = {
        key for key in latest_values
        if key not in _INITIAL_PROCESS_ENV
        or any(p in key.upper() for p in _FORCE_RELOAD_PATTERNS)
    }

    for key in _RUNTIME_ENV_FILE_KEYS - managed_keys:
        os.environ.pop(key, None)

    for key in managed_keys:
        os.environ[key] = latest_values[key]

    _RUNTIME_ENV_FILE_KEYS = managed_keys


def parse_arguments() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="A股自选股智能分析系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                    # 正常运行
  python main.py --debug            # 调试模式
  python main.py --dry-run          # 仅获取数据，不进行 AI 分析
  python main.py --stocks 600519,000001  # 指定分析特定股票
  python main.py --no-notify        # 不发送推送通知
  python main.py --check-notify     # 检查通知配置，不发送通知
  python main.py --single-notify    # 启用单股推送模式（每分析完一只立即推送）
  python main.py --schedule         # 启用定时任务模式
  python main.py --market-review    # 仅运行大盘复盘
        """,
    )

    parser.add_argument("--debug", action="store_true", help="启用调试模式，输出详细日志")

    parser.add_argument("--dry-run", action="store_true", help="仅获取数据，不进行 AI 分析")

    parser.add_argument("--stocks", type=str, help="指定要分析的股票代码，逗号分隔（覆盖配置文件）")

    parser.add_argument("--no-notify", action="store_true", help="不发送推送通知")

    parser.add_argument("--check-notify", action="store_true", help="只读检查通知渠道配置，不发送通知")

    parser.add_argument(
        "--single-notify", action="store_true", help="启用单股推送模式：每分析完一只股票立即推送，而不是汇总推送"
    )

    parser.add_argument("--workers", type=int, default=None, help="并发线程数（默认使用配置值）")

    parser.add_argument("--schedule", action="store_true", help="启用定时任务模式，每日定时执行")

    parser.add_argument("--no-run-immediately", action="store_true", help="定时任务启动时不立即执行一次")

    parser.add_argument("--market-review", action="store_true", help="仅运行大盘复盘分析")

    parser.add_argument("--no-market-review", action="store_true", help="跳过大盘复盘分析")

    parser.add_argument("--force-run", action="store_true", help="跳过交易日检查，强制执行全量分析（Issue #373）")

    parser.add_argument("--webui", action="store_true", help="启动 Web 管理界面")

    parser.add_argument("--webui-only", action="store_true", help="仅启动 Web 服务，不执行自动分析")

    parser.add_argument("--serve", action="store_true", help="启动 FastAPI 后端服务（同时执行分析任务）")

    parser.add_argument("--serve-only", action="store_true", help="仅启动 FastAPI 后端服务，不自动执行分析")

    parser.add_argument("--port", type=int, default=8000, help="FastAPI 服务端口（默认 8000）")

    parser.add_argument("--host", type=str, default="0.0.0.0", help="FastAPI 服务监听地址（默认 0.0.0.0）")

    parser.add_argument("--no-context-snapshot", action="store_true", help="不保存分析上下文快照")

    # === Backtest ===
    parser.add_argument("--backtest", action="store_true", help="运行回测（对历史分析结果进行评估）")

    parser.add_argument("--backtest-code", type=str, default=None, help="仅回测指定股票代码")

    parser.add_argument("--backtest-days", type=int, default=None, help="回测评估窗口（交易日数，默认使用配置）")

    parser.add_argument("--backtest-force", action="store_true", help="强制回测（即使已有回测结果也重新计算）")

    parser.add_argument(
        "--backtest-report", action="store_true", help="生成回测报告（对已有回测结果生成报告不重新回测）"
    )

    # === 盘中实时监控 ===
    parser.add_argument(
        "--realtime-monitor", action="store_true", help="启动盘中实时监控服务（WebSocket + ATR止损 + 量价异动）"
    )
    parser.add_argument(
        "--realtime-monitor-daemon", action="store_true", help="启动实时监控守护进程模式（自动跟随A股交易时段）"
    )

    # === 事件驱动分析服务 ===
    parser.add_argument("--event-monitor", action="store_true", help="启动事件驱动分析服务（公告+互动易监控）")
    parser.add_argument("--event-monitor-daemon", action="store_true", help="启动事件驱动分析守护进程模式")

    # === 周末情报搜集 ===
    parser.add_argument("--weekend-intel", action="store_true", help="周末情报搜集（搜索+存储，不AI分析，重要性≥7可推送）")
    parser.add_argument("--weekend-refresh", action="store_true", help="周末情报补量（周一晨间，max_results更小，覆盖周日深夜）")
    parser.add_argument("--weekend-intel-no-push", action="store_true", help="周末情报搜集：仅存储不推送")

    # === 每日情报搜集（三时段） ===
    parser.add_argument("--daily-intel", action="store_true", help="每日情报搜集（午间/晚间/盘前，搜索+存储+高重要推送）")
    parser.add_argument("--daily-intel-slot", choices=["midday", "evening", "preopen"], default="midday",
                       help="每日情报时段: midday(午间12:00), evening(晚间17:00), preopen(盘前09:00)")

    return parser.parse_args()


def _run_weekend_intel(config: Config, is_refresh: bool = False, no_push: bool = False) -> int:
    """Weekend intelligence gathering: search news → score importance → store → optionally push.

    Args:
        config: application config
        is_refresh: True for Monday morning differential (smaller max_results)
        no_push: True to store only without notification
    """
    stock_codes = config.stock_list
    if not stock_codes:
        logger.warning("周末情报: 自选股列表为空")
        return 1

    mode_label = "补量" if is_refresh else "主力采集"
    logger.info("模式: 周末情报搜集 (%s)", mode_label)
    logger.info("股票: %s", ", ".join(stock_codes[:10]))

    # ── 数据源：东方财富个股新闻（免费，akshare 封装，无需 API Key） ──
    from src.search_service import EastMoneyNewsProvider

    news_provider = EastMoneyNewsProvider()

    # ── 数据源：东方财富公告 API（结构化，带日期/分类/重要性） ──
    #  注意：东方财富公告API近期返回405，备用巨潮资讯(cninfo)工作正常
    from src.services.event_monitor import CninfoFetcher

    ann_fetcher = CninfoFetcher()

    max_results = 3 if is_refresh else 5
    importance_threshold = 7
    collected: list[dict] = []

    # ── 0. 宏观情报：全球财经资讯 + 财经早餐 ──
    try:
        import akshare as ak
        today_str = datetime.now().strftime("%Y%m%d")

        # 全球财经资讯
        try:
            df_global = ak.stock_info_global_em()
            if df_global is not None and not df_global.empty:
                for _, row in df_global.head(20).iterrows():
                    title = str(row.get("标题", ""))
                    collected.append({
                        "code": "__market__", "title": title,
                        "url": str(row.get("链接", "")),
                        "snippet": str(row.get("摘要", ""))[:200],
                        "importance": 6,
                        "source": "全球财经",
                    })
                logger.info("周末情报: 全球财经资讯 %d 条", min(len(df_global), 20))
        except Exception as e:
            logger.debug("周末情报: 全球财经资讯获取失败: %s", e)

        # 财经早餐
        try:
            df_breakfast = ak.stock_info_cjzc_em()
            if df_breakfast is not None and not df_breakfast.empty:
                from src.services.cjzc_service import CjzcExtractor
                extractor = CjzcExtractor()
                for _, row in df_breakfast.head(5).iterrows():
                    title = str(row.get("标题", ""))
                    url = str(row.get("链接", ""))
                    full_text = extractor.extract(url, max_chars=1500)
                    snippet = full_text if full_text else str(row.get("摘要", ""))[:200]
                    collected.append({
                        "code": "__market__", "title": title,
                        "url": url,
                        "snippet": snippet,
                        "importance": 7,
                        "source": "财经早餐",
                    })
                logger.info("周末情报: 财经早餐 %d 条", min(len(df_breakfast), 5))
        except Exception as e:
            logger.debug("周末情报: 财经早餐获取失败: %s", e)

    except Exception as e:
        logger.debug("周末情报: 宏观情报获取失败: %s", e)

    # Populate stock name map for push formatting
    for code in stock_codes:
        try:
            from data_provider.base import DataFetcherManager
            mgr = DataFetcherManager()
            name = mgr.get_stock_name(code)
            if name and name != code:
                _STOCK_NAME_MAP[code] = name
        except Exception:
            pass

    for code in stock_codes:
        import random as _random
        import time as _time

        _time.sleep(1.0 + _random.uniform(0, 0.8))

        # ── 1. 个股新闻 ──
        try:
            response = news_provider.search(
                code, max_results=max_results, days=2 if is_refresh else 7
            )
            if response and response.success and response.results:
                for item in response.results:
                    importance = _score_importance(item.title, item.snippet)
                    collected.append({
                        "code": code,
                        "title": item.title,
                        "url": item.url,
                        "snippet": (item.snippet or "")[:200],
                        "importance": importance,
                        "source": getattr(item, "source", "东方财富"),
                    })
                    if importance >= importance_threshold:
                        logger.info("周末情报 %s [新闻 重要性%d]: %s", code, importance, item.title[:60])
        except Exception as e:
            logger.debug("周末情报 %s: 新闻抓取失败: %s", code, e)

        # ── 2. 官方公告 ──
        try:
            import asyncio

            announcements = asyncio.run(ann_fetcher.fetch(code, page_size=3))
            for ann in announcements:
                event = CninfoFetcher.parse_announcement(ann, code)
                if event is None:
                    continue
                importance = min(event.importance + 2, 10)  # 公告类适度提升
                collected.append({
                    "code": code,
                    "title": event.title,
                    "url": event.url or "",
                    "snippet": event.content[:200],
                    "importance": importance,
                    "source": "公告",
                })
                if importance >= importance_threshold:
                    logger.info("周末情报 %s [公告 重要性%d]: %s", code, importance, event.title[:60])
        except Exception as e:
            logger.debug("周末情报 %s: 公告抓取失败: %s", code, e)

    if not collected:
        logger.info("周末情报: 未发现相关新闻")
        return 0

    # Store in news_intel
    try:
        from src.storage import get_db

        db = get_db()
        with db.session_scope() as session:
            from src.storage import NewsIntel

            stored = 0
            for item in collected:
                try:
                    with session.begin_nested():
                        record = NewsIntel(
                            code=item["code"],
                            name=item["title"][:50],
                            title=item["title"][:300],
                            url=item.get("url", ""),
                            dimension="weekend_intel",
                            query=f"weekend:{mode_label}",
                            provider=item.get("source", "search"),
                            snippet=item["snippet"],
                            source="weekend",
                        )
                        session.add(record)
                    stored += 1
                except Exception:
                    pass
            if stored:
                session.commit()
                logger.info("周末情报: 已存储 %d 条新闻（去重 %d 条）", stored, len(collected) - stored)
            else:
                logger.warning("周末情报: 全部 %d 条因重复被跳过", len(collected))
    except Exception as e:
        logger.warning("周末情报: 存储失败: %s", e)

    # Push high-importance events
    if not no_push:
        highlights = [i for i in collected if i["importance"] >= importance_threshold]
        if highlights:
            _push_weekend_highlights(config, highlights, mode_label, is_refresh=is_refresh)

    logger.info("周末情报 (%s) 完成: 共 %d 条, 高重要性 %d 条",
                mode_label, len(collected), len(highlights) if not no_push else 0)
    return 0


def _score_importance(title: str, snippet: str = "") -> int:
    """统一情报重要性评分（使用 EventClassifier 体系）。

    与 event_monitor.py 的 EventClassifier 保持一致的分类标准。
    重要性≥7的新闻才会被推送。
    """
    from src.services.event_monitor import EventClassifier
    event_type, base = EventClassifier.classify_announcement(title)
    return EventClassifier.adjust_importance(event_type, base, title, snippet)


def _push_highlights(
    notifier: Any,
    highlights: list[dict],
    *,
    title_text: str,
    footer_text: str,
    dedup_key_prefix: str,
    log_label: str,
    title_max: int = 45,
) -> None:
    import hashlib
    import re as _re
    import html as _html

    lines = [f"📰 {title_text}"]
    grouped: dict[str, dict] = {}
    for h in sorted(highlights, key=lambda x: -x["importance"]):
        code = h.get("code", "")
        if code == "__market__":
            name = "市场情报"
        else:
            name = _STOCK_NAME_MAP.get(code, code)
        raw_title = _html.unescape(h.get("title", ""))
        title = _re.sub(r"<[^>]+>", "", raw_title)
        title = _re.sub(r"\s+", " ", title).strip()[:title_max]
        url = h.get("url", "")

        # 清理标题中重复的股票名称/代码前缀
        if name != code and title.startswith(name):
            title = title[len(name):].strip().lstrip("，,、：: ")
        if title.startswith(code):
            title = title[len(code):].strip().lstrip("，,、：: ")
        # 通用清理：去除标题中重复出现的挂盘信息/html残留
        title = _re.sub(r"【.*?】", "", title).strip()
        title = _re.sub(r"\s+", " ", title).strip()[:title_max]

        if not title:
            continue

        imp = h.get("importance", 0)
        if imp >= 8:
            sentiment = _score_sentiment(title, h.get("snippet", ""))
            sentiment_tag = f" [{sentiment}]" if sentiment != "中性" else ""
        else:
            sentiment_tag = ""

        if code not in grouped:
            grouped[code] = {"name": name, "items": []}

        source = h.get("source", "")
        snippet = h.get("snippet", "")
        if url and url.startswith("http") and source != "公告":
            clean_url = url.split("&")[0] if "?" in url else url
            item_text = f"[{title}]({clean_url}){sentiment_tag}"
            # 如果 snippet 有实质内容（非默认摘要），嵌入消息体
            if len(snippet) > 100 and code == "__market__":
                text_body = snippet[:600].replace("\n", " ")
                item_text += f"\n  > {text_body}{'……' if len(snippet) > 600 else ''}"
        else:
            item_text = f"{title}{sentiment_tag}"
        grouped[code]["items"].append(item_text)

    for code, entry in grouped.items():
        name = entry["name"]
        # 股票名加粗，不展示代码
        header = f"**{name}**"
        lines.append(header)
        sep = "\n  "
        items = sep.join(entry["items"])
        lines.append(f"  {items}")
    lines.append(f"📊{len(highlights)}条 | {footer_text}")

    if notifier is None:
        logger.warning("%s: notifier 不可用，跳过推送", log_label)
        return
    content = "\n".join(lines)
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    notifier.send(content, route_type="alert", dedup_key=f"{dedup_key_prefix}:{content_hash}")
    logger.info("%s: 已推送 %d 条", log_label, len(highlights))


def _push_weekend_highlights(config: Config, highlights: list[dict], mode_label: str, is_refresh: bool = False) -> None:
    """Push high-importance weekend events via notification alert channels.

    When is_refresh=True, queries the DB for already-pushed weekend_intel
    URLs and skips duplicates to avoid re-pushing the same content.
    """
    try:
        from src.notification import NotificationService

        notifier = NotificationService(config)
        if not notifier.is_available():
            return

        # ── 周一补量时，查询已推送记录做去重 ──
        if is_refresh and highlights:
            try:
                from src.storage import get_db, NewsIntel
                from sqlalchemy import select
                db = get_db()
                with db.session_scope() as session:
                    urls_to_check = [h.get("url", "") for h in highlights if h.get("url")]
                    if urls_to_check:
                        stmt = select(NewsIntel.url).where(
                            NewsIntel.dimension == "weekend_intel",
                            NewsIntel.url.in_(urls_to_check),
                        )
                        existing_urls = {row[0] for row in session.execute(stmt).fetchall()}
                        before = len(highlights)
                        highlights = [h for h in highlights if h.get("url", "") not in existing_urls]
                        skipped = before - len(highlights)
                        if skipped:
                            logger.info("周末情报补量: DB 去重跳过 %d 条已推送新闻", skipped)
            except Exception as e:
                logger.debug("周末情报补量去重查询失败，按原始列表推送: %s", e)

        _push_highlights(
            notifier, highlights,
            title_text=f"周末要闻 | {mode_label}",
            footer_text="完整分析下个交易日推送",
            dedup_key_prefix="weekend",
            log_label="周末情报",
            title_max=50,
        )
    except Exception as e:
        logger.warning("周末情报: 推送失败: %s", e)


def _run_daily_intel(config: Config, slot: str = "midday") -> int:
    """Daily intelligence gathering: search news → score → store → optionally push.

    Only runs at 09:00 preopen once per trading day (midday/evening removed 2026-05-28).
    days_lookback=2 covers overnight + previous day news.
    Midday/evening cancelled due to triple push duplication.
    Daytime news is handled by hourlies (10/11/14/15) which have built-in news search.

    All stored in news_intel with dimension='daily_intel', TTL=24h.
    """
    stock_codes = config.stock_list
    if not stock_codes:
        logger.warning("每日情报: 自选股列表为空")
        return 1

    slot_labels = {"midday": "午间", "evening": "晚间", "preopen": "盘前"}
    label = slot_labels.get(slot, slot)
    logger.info("每日情报 (%s): 开始搜集", label)

    from src.search_service import EastMoneyNewsProvider
    news_provider = EastMoneyNewsProvider()

    from src.services.event_monitor import CninfoFetcher
    ann_fetcher = CninfoFetcher()

    max_results = 3 if slot == "evening" else 2
    importance_threshold = 7
    collected: list[dict] = []
    days_lookback = 2 if slot == "preopen" else 1

    # 填充股票名称映射（用于推送格式化）
    for code in stock_codes:
        try:
            from data_provider.base import DataFetcherManager
            mgr = DataFetcherManager()
            name = mgr.get_stock_name(code)
            if name and name != code:
                _STOCK_NAME_MAP[code] = name
        except Exception:
            pass
    # 确保 _STOCK_NAME_MAP 对常见前缀的股票也覆盖（A股6位代码）
    import re as _re_ss
    for code in stock_codes:
        if code not in _STOCK_NAME_MAP and _re_ss.match(r'^\d{6}$', code):
            _STOCK_NAME_MAP[code] = code  # 标记位, _push_highlights 会格式化为纯代码

    # ── 0. 市场级情报搜集（非个股，政策/板块/宏观） ──  {@calibration 市场情报搜集}
    # 使用 akshare 免费源，替代质量不佳的 SearXNG 搜索
    try:
        import akshare as ak
        import pandas as pd
        today_str = datetime.now().strftime("%Y%m%d")

        # 0a. 新闻联播文字稿（最权威的政策信号源）
        try:
            df_cctv = ak.news_cctv(date=today_str)
            if df_cctv is not None and not df_cctv.empty:
                for _, row in df_cctv.iterrows():
                    title = str(row.get("title", ""))
                    content = str(row.get("content", ""))[:300]
                    collected.append({
                        "code": "__market__", "title": title,
                        "url": "", "snippet": content,
                        "importance": 8,  # 新闻联播默认高重要
                        "source": "新闻联播",
                    })
                    logger.info("市场情报 [新闻联播]: %s", title[:60])
        except Exception as e:
            logger.debug("市场情报: 新闻联播获取失败: %s", e)

        # 0b. 全市场重大事项公告（东方财富公告大全）
        try:
            df_notices = ak.stock_notice_report(symbol="重大事项", date=today_str)
            if df_notices is not None and not df_notices.empty:
                for _, row in df_notices.head(20).iterrows():  # 最多取20条
                    title = str(row.get("公告标题", ""))
                    code = str(row.get("代码", ""))
                    name = str(row.get("名称", ""))
                    collected.append({
                        "code": code, "title": title,
                        "url": str(row.get("网址", "")),
                        "snippet": f"{name}({code}) 重大事项公告",
                        "importance": 7,
                        "source": "东方财富公告",
                    })
                logger.info("市场情报: 重大事项公告 %d 条", min(len(df_notices), 20))
        except Exception as e:
            logger.debug("市场情报: 重大事项公告获取失败: %s", e)

        # 0c. 全市场风险提示公告
        try:
            df_risk = ak.stock_notice_report(symbol="风险提示", date=today_str)
            if df_risk is not None and not df_risk.empty:
                for _, row in df_risk.head(10).iterrows():
                    title = str(row.get("公告标题", ""))
                    code = str(row.get("代码", ""))
                    name = str(row.get("名称", ""))
                    collected.append({
                        "code": code, "title": title,
                        "url": str(row.get("网址", "")),
                        "snippet": f"{name}({code}) 风险提示",
                        "importance": 7,
                        "source": "东方财富公告",
                    })
                logger.info("市场情报: 风险提示公告 %d 条", min(len(df_risk), 10))
        except Exception as e:
            logger.debug("市场情报: 风险提示公告获取失败: %s", e)

        # 0d. 全球财经资讯（东方财富聚合，免费200条）
        try:
            df_global = ak.stock_info_global_em()
            if df_global is not None and not df_global.empty:
                for _, row in df_global.head(15).iterrows():
                    title = str(row.get("标题", ""))
                    collected.append({
                        "code": "__market__", "title": title,
                        "url": str(row.get("链接", "")),
                        "snippet": str(row.get("摘要", ""))[:200],
                        "importance": 6,
                        "source": "全球财经",
                    })
                logger.info("市场情报: 全球财经资讯 %d 条", min(len(df_global), 15))
        except Exception as e:
            logger.debug("市场情报: 全球财经资讯获取失败: %s", e)

        # 0e. 东方财富财经早餐（免费400条）
        try:
            df_breakfast = ak.stock_info_cjzc_em()
            if df_breakfast is not None and not df_breakfast.empty:
                from src.services.cjzc_service import CjzcExtractor
                extractor = CjzcExtractor()
                for _, row in df_breakfast.head(5).iterrows():
                    title = str(row.get("标题", ""))
                    url = str(row.get("链接", ""))
                    full_text = extractor.extract(url, max_chars=1500)
                    snippet = full_text if full_text else str(row.get("摘要", ""))[:200]
                    collected.append({
                        "code": "__market__", "title": title,
                        "url": url,
                        "snippet": snippet,
                        "importance": 7,
                        "source": "财经早餐",
                    })
                logger.info("市场情报: 财经早餐 %d 条", min(len(df_breakfast), 5))
        except Exception as e:
            logger.debug("市场情报: 财经早餐获取失败: %s", e)

    except ImportError:
        logger.warning("akshare 未安装，跳过市场情报搜集")
    except Exception as e:
        logger.warning("市场情报搜集异常: %s", e)

    for code in stock_codes:
        import random as _random, time as _time
        _time.sleep(1.0 + _random.uniform(0, 0.5))

        try:
            response = news_provider.search(code, max_results=max_results, days=days_lookback)
            if response and response.success and response.results:
                for item in response.results:
                    importance = _score_importance(item.title, item.snippet)
                    collected.append({
                        "code": code, "title": item.title, "url": item.url,
                        "snippet": (item.snippet or "")[:200], "importance": importance,
                        "source": getattr(item, "source", "东方财富"),
                    })
                    if importance >= importance_threshold:
                        logger.info("每日情报 %s [新闻%d]: %s", label, importance, item.title[:60])
        except Exception as e:
            logger.debug("每日情报 %s: 新闻抓取失败: %s", code, e)

        try:
            import asyncio
            announcements = asyncio.run(ann_fetcher.fetch(code, page_size=2))
            for ann in announcements:
                event = CninfoFetcher.parse_announcement(ann, code)
                if event is None:
                    continue
                importance = min(event.importance + 1, 10)
                collected.append({
                    "code": code, "title": event.title, "url": event.url or "",
                    "snippet": event.content[:200], "importance": importance,
                    "source": "公告",
                })
                if importance >= importance_threshold:
                    logger.info("每日情报 %s [公告%d]: %s", label, importance, event.title[:60])
        except Exception as e:
            logger.debug("每日情报 %s: 公告抓取失败: %s", code, e)

    if not collected:
        logger.info("每日情报 (%s): 未发现相关新闻", label)
        return 0

    try:
        from src.storage import get_db, NewsIntel
        db = get_db()
        with db.session_scope() as session:
            stored = 0
            for item in collected:
                try:
                    with session.begin_nested():
                        record = NewsIntel(
                            code=item["code"], name=item["title"][:50],
                            title=item["title"][:300], url=item.get("url", ""),
                            dimension="daily_intel",
                            query=f"daily:{slot}",
                            provider=item.get("source", "search"),
                            snippet=item["snippet"], source="daily",
                        )
                        session.add(record)
                    stored += 1
                except Exception:
                    pass
            if stored:
                session.commit()
                logger.info("每日情报 (%s): 已存储 %d 条", label, stored)
    except Exception as e:
        logger.warning("每日情报 (%s): 存储失败: %s", label, e)

    highlights = [i for i in collected if i["importance"] >= importance_threshold]
    if highlights:
        _push_daily_highlights(config, highlights, label)

    logger.info("每日情报 (%s) 完成: 共 %d 条, 高重要 %d 条", label, len(collected), len(highlights))
    return 0


def _push_daily_highlights(config: Config, highlights: list[dict], label: str) -> None:
    """Push high-importance daily events via notification alert channels."""
    try:
        from src.notification import NotificationService
        notifier = NotificationService()
        if not notifier.is_available():
            return

        _push_highlights(
            notifier, highlights,
            title_text=f"每日要闻 | {label}",
            footer_text="下个交易时段自动注入LLM",
            dedup_key_prefix=f"daily:{label}",
            log_label=f"每日情报 ({label})",
        )
    except Exception as e:
        logger.warning("每日情报 (%s): 推送失败: %s", label, e)


_SENTIMENT_BULLISH = {"增持", "回购", "中标", "合同", "利好", "预增", "分红", "重组", "获批", "签约", "突破"}
_SENTIMENT_BEARISH = {"减持", "预亏", "亏损", "ST", "退市", "立案", "处罚", "问询", "监管", "违规", "暴雷"}

# Stock name mapping for push formatting
_STOCK_NAME_MAP = {}  # populated from config at runtime


def _score_sentiment(title: str, snippet: str = "") -> str:
    """Simple bullish/bearish sentiment classifier for push labels."""
    text = f"{title} {snippet}".lower()
    if any(k in text for k in _SENTIMENT_BEARISH):
        return "利空"
    if any(k in text for k in _SENTIMENT_BULLISH):
        return "利好"
    return "中性"


def _compute_trading_day_filter(
    config: Config,
    args: argparse.Namespace,
    stock_codes: list[str],
) -> tuple[list[str], str | None, bool]:
    """
    Compute filtered stock list and effective market review region (Issue #373).

    Returns:
        (filtered_codes, effective_region, should_skip_all)
        - effective_region None = use config default (check disabled)
        - effective_region '' = all relevant markets closed, skip market review
        - should_skip_all: skip entire run when no stocks and no market review to run
    """
    force_run = getattr(args, "force_run", False)
    if force_run or not getattr(config, "trading_day_check_enabled", True):
        return (stock_codes, None, False)

    from src.core.trading_calendar import (
        compute_effective_region,
        get_market_for_stock,
        get_open_markets_today,
    )

    open_markets = get_open_markets_today()
    filtered_codes = []
    for code in stock_codes:
        mkt = get_market_for_stock(code)
        if mkt in open_markets or mkt is None:
            filtered_codes.append(code)

    if config.market_review_enabled and not getattr(args, "no_market_review", False):
        effective_region = compute_effective_region(getattr(config, "market_review_region", "cn") or "cn", open_markets)
    else:
        effective_region = None

    should_skip_all = (not filtered_codes) and (effective_region or "") == ""
    return (filtered_codes, effective_region, should_skip_all)


def _run_market_review_with_shared_lock(
    config: Config,
    run_market_review_func: Callable[..., str | None],
    **kwargs: Any,
) -> str | None:
    from src.core.market_review_lock import (
        release_market_review_lock,
        try_acquire_market_review_lock,
    )

    lock_token = try_acquire_market_review_lock(config)
    if lock_token is None:
        logger.warning("大盘复盘正在执行中，跳过本次大盘复盘")
        return None

    try:
        return run_market_review_func(**kwargs)
    finally:
        release_market_review_lock(lock_token)


def run_full_analysis(config: Config, args: argparse.Namespace, stock_codes: list[str] | None = None):
    """
    执行完整的分析流程（个股 + 大盘复盘）

    这是定时任务调用的主函数
    """
    # Import pipeline modules outside the broad try/except so that import-time
    # failures propagate to the caller instead of being silently swallowed.
    from src.core.market_review import run_market_review
    from src.core.pipeline import StockAnalysisPipeline

    try:
        # Issue #529: Hot-reload STOCK_LIST from .env on each scheduled run
        if stock_codes is None:
            config.refresh_stock_list()

        # Issue #373: Trading day filter (per-stock, per-market)
        effective_codes = stock_codes if stock_codes is not None else config.stock_list
        filtered_codes, effective_region, should_skip = _compute_trading_day_filter(config, args, effective_codes)
        if should_skip:
            logger.info("今日所有相关市场均为非交易日，跳过执行。可使用 --force-run 强制执行。")
            return
        if set(filtered_codes) != set(effective_codes):
            skipped = set(effective_codes) - set(filtered_codes)
            logger.info("今日休市股票已跳过: %s", skipped)
        stock_codes = filtered_codes

        # 命令行参数 --single-notify 覆盖配置（#55）
        if getattr(args, "single_notify", False):
            config.single_stock_notify = True

        # Issue #190: 个股与大盘复盘合并推送
        merge_notification = (
            getattr(config, "merge_email_notification", False)
            and config.market_review_enabled
            and not getattr(args, "no_market_review", False)
            and not config.single_stock_notify
        )

        # 创建调度器
        save_context_snapshot = None
        if getattr(args, "no_context_snapshot", False):
            save_context_snapshot = False
        query_id = uuid.uuid4().hex
        pipeline = StockAnalysisPipeline(
            config=config,
            max_workers=args.workers,
            query_id=query_id,
            query_source="cli",
            save_context_snapshot=save_context_snapshot,
        )

        # 1. 运行个股分析
        results = pipeline.run(
            stock_codes=stock_codes,
            dry_run=args.dry_run,
            send_notification=not args.no_notify,
            merge_notification=merge_notification,
        )

        # Issue #128: 分析间隔 - 在个股分析和大盘分析之间添加延迟
        analysis_delay = getattr(config, "analysis_delay", 0)
        if analysis_delay > 0 and config.market_review_enabled and not args.no_market_review and effective_region != "":
            logger.info(f"等待 {analysis_delay} 秒后执行大盘复盘（避免API限流）...")
            time.sleep(analysis_delay)

        # 2. 运行大盘复盘（如果启用且不是仅个股模式）
        market_report = ""
        if config.market_review_enabled and not args.no_market_review and effective_region != "":
            review_result = _run_market_review_with_shared_lock(
                config,
                run_market_review,
                notifier=pipeline.notifier,
                analyzer=pipeline.analyzer,
                search_service=pipeline.search_service,
                send_notification=not args.no_notify,
                merge_notification=merge_notification,
                override_region=effective_region,
            )
            # 如果有结果，赋值给 market_report 用于后续飞书文档生成
            if review_result:
                market_report = review_result

        # Issue #190: 合并推送（个股+大盘复盘）
        if merge_notification and (results or market_report) and not args.no_notify:
            parts = []
            if market_report:
                parts.append(f"# 📈 大盘复盘\n\n{market_report}")
            if results:
                dashboard_content = pipeline.notifier.generate_aggregate_report(
                    results,
                    getattr(config, "report_type", "simple"),
                )
                parts.append(f"# 🚀 个股决策仪表盘\n\n{dashboard_content}")
            if parts:
                combined_content = "\n\n---\n\n".join(parts)
                if pipeline.notifier.is_available():
                    if pipeline.notifier.send(combined_content, email_send_to_all=True, route_type="report"):
                        logger.info("已合并推送（个股+大盘复盘）")
                    else:
                        logger.warning("合并推送失败")

        # 输出摘要
        if results:
            logger.info("\n===== 分析结果摘要 =====")
            for r in sorted(results, key=lambda x: x.sentiment_score, reverse=True):
                emoji = r.get_emoji()
                logger.info(
                    f"{emoji} {r.name}({r.code}): {r.operation_advice} | "
                    f"评分 {r.sentiment_score} | {r.trend_prediction}"
                )

        logger.info("\n任务执行完成")

        # === 新增：生成飞书云文档 ===
        try:
            from src.feishu_doc import FeishuDocManager

            feishu_doc = FeishuDocManager()
            if feishu_doc.is_configured() and (results or market_report):
                logger.info("正在创建飞书云文档...")

                # 1. 准备标题 "01-01 13:01大盘复盘"
                tz_cn = timezone(timedelta(hours=8))
                now = datetime.now(tz_cn)
                doc_title = f"{now.strftime('%Y-%m-%d %H:%M')} 大盘复盘"

                # 2. 准备内容 (拼接个股分析和大盘复盘)
                full_content = ""

                # 添加大盘复盘内容（如果有）
                if market_report:
                    full_content += f"# 📈 大盘复盘\n\n{market_report}\n\n---\n\n"

                # 添加个股决策仪表盘（使用 NotificationService 生成，按 report_type 分支）
                if results:
                    dashboard_content = pipeline.notifier.generate_aggregate_report(
                        results,
                        getattr(config, "report_type", "simple"),
                    )
                    full_content += f"# 🚀 个股决策仪表盘\n\n{dashboard_content}"

                # 3. 创建文档
                doc_url = feishu_doc.create_daily_doc(doc_title, full_content)
                if doc_url:
                    logger.info(f"飞书云文档创建成功: {doc_url}")
                    # 可选：将文档链接也推送到群里
                    if not args.no_notify:
                        pipeline.notifier.send(
                            f"[{now.strftime('%Y-%m-%d %H:%M')}] 复盘文档创建成功: {doc_url}",
                            route_type="report",
                        )

        except Exception as e:
            logger.error(f"飞书文档生成失败: {e}")

        # === Auto backtest ===
        try:
            if getattr(config, "backtest_enabled", False):
                from src.services.backtest_service import BacktestService

                service = BacktestService()
                for eval_window in [5, 10, 20]:
                    logger.info("开始自动回测 (eval_window=%d)...", eval_window)
                    stats = service.run_backtest(
                        force=False,
                        eval_window_days=eval_window,
                        min_age_days=getattr(config, "backtest_min_age_days", 14),
                        limit=200,
                    )
                    logger.info(
                        f"自动回测完成 (eval_window={eval_window}): processed={stats.get('processed')} saved={stats.get('saved')} "
                        f"completed={stats.get('completed')} insufficient={stats.get('insufficient')} errors={stats.get('errors')}"
                    )

                    # Auto-generate backtest report after automatic backtest
                    if getattr(config, "backtest_report_enabled", True):
                        try:
                            from src.core.backtest_report import BacktestReportGenerator

                            gen = BacktestReportGenerator()
                            summary = service.get_summary(
                                scope="overall",
                                code=None,
                                eval_window_days=eval_window,
                            )
                            if summary:
                                gen.generate(summary, strategy_name=f"Auto Backtest ({eval_window}d)")
                                logger.info(f"自动回测报告已生成 (eval_window={eval_window})")
                        except Exception as rpt_exc:
                            logger.warning("自动生成回测报告失败（已忽略）: %s", rpt_exc)
        except Exception as e:
            logger.warning(f"自动回测失败（已忽略）: {e}")

    except Exception as e:
        logger.exception(f"分析流程执行失败: {e}")


def start_api_server(host: str, port: int, config: Config) -> None:
    """
    在后台线程启动 FastAPI 服务

    Args:
        host: 监听地址
        port: 监听端口
        config: 配置对象
    """
    import threading

    import uvicorn

    probe = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError as exc:
        raise RuntimeError(f"FastAPI port is not available: {host}:{port}") from exc
    finally:
        probe.close()

    level_name = (config.log_level or "INFO").lower()
    use_config_signal_handlers = True
    uvicorn_kwargs = {
        "host": host,
        "port": port,
        "log_level": level_name,
        "log_config": None,
    }
    # Import the ASGI app object in the calling thread instead of handing uvicorn
    # the "api.app:app" import string. With the string, uvicorn imports the app
    # lazily inside the server thread, and that import (litellm + the full app
    # tree, ~10s+ on constrained hosts) runs inside the startup probe window
    # below, tripping the 3.0s timeout and causing a restart loop on slower
    # machines. Importing first keeps the heavy work out of the probe window;
    # genuine import failures still surface immediately to the caller.
    from api.app import app as fastapi_app

    try:
        uvicorn_config = uvicorn.Config(
            fastapi_app,
            install_signal_handlers=False,
            **uvicorn_kwargs,
        )
    except TypeError:
        # Older uvicorn versions do not accept install_signal_handlers in
        # Config; fall back and only disable signal handling via Server attribute
        # when it's a boolean flag.
        use_config_signal_handlers = False
        uvicorn_config = uvicorn.Config(
            fastapi_app,
            **uvicorn_kwargs,
        )
    uvicorn_server = uvicorn.Server(config=uvicorn_config)
    if not use_config_signal_handlers:
        install_signal_handlers = getattr(uvicorn_server, "install_signal_handlers", None)
        if isinstance(install_signal_handlers, bool):
            uvicorn_server.install_signal_handlers = False

    startup_error: list[BaseException] = []

    def run_server():
        level_name = (config.log_level or "INFO").lower()
        uvicorn.run(
            "api.app:app",
            host=host,
            port=port,
            log_level=level_name,
            log_config=None,
        )

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    logger.info(f"FastAPI 服务已启动: http://{host}:{port}")


def _is_truthy_env(var_name: str, default: str = "true") -> bool:
    """Parse common truthy / falsy environment values."""
    value = os.getenv(var_name, default).strip().lower()
    return value not in {"0", "false", "no", "off"}


def start_bot_stream_clients(config: Config) -> None:
    """Start bot stream clients when enabled in config."""
    # 启动钉钉 Stream 客户端
    if config.dingtalk_stream_enabled:
        try:
            from bot.platforms import DINGTALK_STREAM_AVAILABLE, start_dingtalk_stream_background

            if DINGTALK_STREAM_AVAILABLE:
                if start_dingtalk_stream_background():
                    logger.info("[Main] Dingtalk Stream client started in background.")
                else:
                    logger.warning("[Main] Dingtalk Stream client failed to start.")
            else:
                logger.warning("[Main] Dingtalk Stream enabled but SDK is missing.")
                logger.warning("[Main] Run: pip install dingtalk-stream")
        except Exception as exc:
            logger.error(f"[Main] Failed to start Dingtalk Stream client: {exc}")

    # 启动飞书 Stream 客户端
    if getattr(config, "feishu_stream_enabled", False):
        try:
            from bot.platforms import FEISHU_SDK_AVAILABLE, start_feishu_stream_background

            if FEISHU_SDK_AVAILABLE:
                if start_feishu_stream_background():
                    logger.info("[Main] Feishu Stream client started in background.")
                else:
                    logger.warning("[Main] Feishu Stream client failed to start.")
            else:
                logger.warning("[Main] Feishu Stream enabled but SDK is missing.")
                logger.warning("[Main] Run: pip install lark-oapi")
        except Exception as exc:
            logger.error(f"[Main] Failed to start Feishu Stream client: {exc}")


def _resolve_scheduled_stock_codes(stock_codes: list[str] | None) -> list[str] | None:
    """Scheduled runs should always read the latest persisted watchlist."""
    if stock_codes is not None:
        logger.warning(
            "定时模式下检测到 --stocks 参数；计划执行将忽略启动时股票快照，并在每次运行前重新读取最新的 STOCK_LIST。"
        )
    return None


def _reload_runtime_config() -> Config:
    """Reload config from the latest persisted `.env` values for scheduled runs."""
    _reload_env_file_values_preserving_overrides()
    Config.reset_instance()
    return get_config()


def _build_schedule_time_provider(default_schedule_time: str):
    """Read the latest schedule time directly from the active config file.

    Fallback order:
    1. Process-level env override (set before launch) → honour it.
    2. Persisted config file value (written by WebUI) → use it.
    3. Documented system default ``"18:00"`` → always fall back here so
       that clearing SCHEDULE_TIME in WebUI correctly resets the schedule.
    """
    from src.core.config_manager import ConfigManager

    _SYSTEM_DEFAULT_SCHEDULE_TIME = "18:00"
    manager = ConfigManager()

    def _provider() -> str:
        if "SCHEDULE_TIME" in _INITIAL_PROCESS_ENV:
            return os.getenv("SCHEDULE_TIME", default_schedule_time)

        config_map = manager.read_config_map()
        schedule_time = (config_map.get("SCHEDULE_TIME", "") or "").strip()
        if schedule_time:
            return schedule_time
        return _SYSTEM_DEFAULT_SCHEDULE_TIME

    return _provider


def _init_main_environment(args: argparse.Namespace):
    try:
        _setup_bootstrap_logging(debug=args.debug)
    except Exception as exc:
        logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", stream=sys.stderr)
        logger.warning("Bootstrap 日志初始化失败，已回退: %s", exc)
    try:
        config = get_config()
    except Exception as exc:
        logger.exception("加载配置失败: %s", exc)
        return None
    try:
        _setup_runtime_logging(config.log_dir, debug=args.debug)
    except Exception as exc:
        logger.exception("切换日志目录失败: %s", exc)
        return None
    logger.info("=" * 60)
    logger.info("A股自选股智能分析系统 启动")
    logger.info(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    for w in config.validate():
        logger.warning(w)
    return config


def _start_web_if_enabled(config, args) -> bool:
    if args.webui:
        args.serve = True
    if args.webui_only:
        args.serve_only = True
    if config.webui_enabled and not (args.serve or args.serve_only):
        args.serve = True
    start_serve = (args.serve or args.serve_only) and os.getenv("GITHUB_ACTIONS") != "true"
    bot_started = False
    if start_serve:
        if args.host == "0.0.0.0" and os.getenv("WEBUI_HOST"):
            args.host = os.getenv("WEBUI_HOST")
        if args.port == 8000 and os.getenv("WEBUI_PORT"):
            args.port = int(os.getenv("WEBUI_PORT"))
        if not prepare_webui_frontend_assets():
            logger.warning("前端静态资源未就绪")
        try:
            start_api_server(host=args.host, port=args.port, config=config)
            bot_started = True
        except Exception as e:
            logger.error(f"启动 FastAPI 服务失败: {e}")
    if bot_started:
        start_bot_stream_clients(config)
    return start_serve


def _run_schedule_mode(config, args, stock_codes):
    logger.info("模式: 定时任务")
    logger.info(f"每日执行时间: {config.schedule_time}")
    should_run_immediately = False
    logger.info("已禁用启动时立即执行")
    from src.scheduler import run_with_schedule
    scheduled_stock_codes = _resolve_scheduled_stock_codes(stock_codes)
    schedule_time_provider = _build_schedule_time_provider(config.schedule_time)

    def scheduled_task():
        pass

    background_tasks = []
    if getattr(config, "agent_event_monitor_enabled", False):
        from src.services.event_monitor import ThresholdEventMonitor, parse_threshold_alert_rules, TriggeredThresholdAlert
        interval = max(1, getattr(config, "agent_event_monitor_interval_minutes", 5))
        rules = parse_threshold_alert_rules(getattr(config, "agent_event_alert_rules_json", ""))
        if rules:
            monitor = ThresholdEventMonitor.from_dict_list(rules)
            from src.notification import NotificationBuilder, NotificationService
            ns = NotificationService()
            def _notify(t: TriggeredThresholdAlert):
                sent = ns.send(NotificationBuilder.build_simple_alert(
                    title=f"Event Alert | {t.rule.stock_code}",
                    content=t.message or t.rule.description or "Alert triggered",
                    alert_type="warning"), route_type="alert")
                if not sent:
                    logger.info("[ThresholdMonitor] No channel available for: %s", t.rule.stock_code)
            monitor.on_trigger(_notify)
            import asyncio
            def _threshold_task():
                triggered = asyncio.run(monitor.check_all())
                if triggered:
                    logger.info("[ThresholdMonitor] 本轮触发 %d 条提醒", len(triggered))
            background_tasks.append({"task": _threshold_task, "interval_seconds": interval * 60,
                                      "run_immediately": True, "name": "threshold_event_monitor"})

    if getattr(config, "event_monitor_enabled", False):
        em_int = getattr(config, "event_monitor_check_interval", 300)
        em_codes = getattr(config, "event_monitor_stock_codes", []) or scheduled_stock_codes or config.stock_list
        import asyncio
        def _em_task():
            try:
                from src.services.event_monitor import run_event_monitor_cli
                asyncio.run(run_event_monitor_cli(stock_codes=em_codes, config=_reload_runtime_config(), daemon=False))
            except Exception as e:
                logger.exception("EventMonitor后台任务失败: %s", e)
        background_tasks.append({"task": _em_task, "interval_seconds": em_int,
                                  "run_immediately": True, "name": "event_monitor"})

    if getattr(config, "rss_pipeline_enabled", False):
        def _rss_task():
            try:
                from src.services.intelligence_service import IntelligenceService
                results = IntelligenceService().fetch_all_enabled()
                ok = sum(1 for r in results if r.get("status") == "ok")
                items = sum(r.get("items_fetched", 0) for r in results)
                if results:
                    logger.info("[RSS] Fetched %d/%d sources, %d new items", ok, len(results), items)
            except Exception as e:
                logger.warning("RSS background fetch failed: %s", e)
        background_tasks.append({"task": _rss_task, "interval_seconds": 3600,
                                  "run_immediately": True, "name": "rss_fetch"})

    def _db_maint():
        try:
            from scripts.db_maintenance import main as db_maint
            db_maint()
        except Exception as e:
            logger.warning("DB maintenance failed: %s", e)
    background_tasks.append({"task": _db_maint, "interval_seconds": 7*24*3600,
                              "run_immediately": False, "name": "db_maintenance"})

    additional_daily = []
    additional_weekly = []

    def _sched_market_review():
        try:
            if datetime.now().isoweekday() >= 6:
                return
            from src.core.market_review import run_market_review
            from src.core.market_review_runtime import build_market_review_runtime
            cfg = _reload_runtime_config()
            n, a, s = build_market_review_runtime(cfg)
            _run_market_review_with_shared_lock(cfg, run_market_review, notifier=n, analyzer=a, search_service=s, send_notification=True)
        except Exception as e:
            logger.exception("大盘复盘失败: %s", e)

    for t in ("11:45", "15:45"):
        additional_daily.append({"task": _sched_market_review, "time": t, "name": f"大盘复盘@{t}"})

    for t in ("11:00", "14:00"):
        def _make_analysis(slot=t):
            def _run():
                try:
                    cfg = _reload_runtime_config()
                    cfg.market_review_enabled = False
                    run_full_analysis(cfg, args, scheduled_stock_codes)
                except Exception as e:
                    logger.exception("整点分析@%s 失败: %s", slot, e)
            return _run
        additional_daily.append({"task": _make_analysis(), "time": t, "name": f"整点分析@{t}"})

    def _sched_daily_intel():
        try:
            from src.core.trading_calendar import get_open_markets_today
            if 'cn' not in get_open_markets_today():
                return
            _run_daily_intel(_reload_runtime_config(), "preopen")
        except Exception as e:
            logger.exception("日间情报失败: %s", e)
    additional_daily.append({"task": _sched_daily_intel, "time": "09:00", "name": "日间情报(盘前)"})

    def _sched_weekend_intel():
        try:
            _run_weekend_intel(_reload_runtime_config(), is_refresh=False, no_push=False)
        except Exception as e:
            logger.exception("周末情报失败: %s", e)
    additional_weekly.append({"task": _sched_weekend_intel, "time": "20:00", "day": "sunday", "name": "周末情报"})

    def _sched_weekend_refresh():
        try:
            _run_weekend_intel(_reload_runtime_config(), is_refresh=True, no_push=False)
        except Exception as e:
            logger.exception("周末情报补量失败: %s", e)
    additional_weekly.append({"task": _sched_weekend_refresh, "time": "07:30", "day": "monday", "name": "周末情报补量"})

    run_with_schedule(task=scheduled_task, schedule_time=config.schedule_time,
                      run_immediately=should_run_immediately, background_tasks=background_tasks,
                      schedule_time_provider=schedule_time_provider,
                      additional_daily_tasks=additional_daily, additional_weekly_tasks=additional_weekly)
    return 0


def _dispatch_mode(config, args, stock_codes, start_serve) -> bool:
    if getattr(args, "backtest", False):
        from src.services.backtest_service import BacktestService
        svc = BacktestService()
        stats = svc.run_backtest(code=getattr(args, "backtest_code", None),
                                  force=getattr(args, "backtest_force", False),
                                  eval_window_days=getattr(args, "backtest_days", None))
        logger.info("回测完成: processed=%(processed)s saved=%(saved)s completed=%(completed)s" % stats)
        if getattr(config, "backtest_report_enabled", True):
            try:
                from src.core.backtest_report import BacktestReportGenerator
                summary = svc.get_summary(scope="overall", code=None, eval_window_days=getattr(args, "backtest_days", None))
                if summary:
                    BacktestReportGenerator().generate(summary, strategy_name="Overall Backtest")
            except Exception as exc:
                logger.warning("生成回测报告失败: %s", exc)
        return True

    if getattr(args, "backtest_report", False):
        from src.services.backtest_service import BacktestService
        svc = BacktestService()
        try:
            from src.core.backtest_report import BacktestReportGenerator
            summary = svc.get_summary(scope="overall", code=getattr(args, "backtest_code", None) or None,
                                       eval_window_days=getattr(args, "backtest_days", None))
            if summary:
                sc = getattr(args, "backtest_code", None) or None
                BacktestReportGenerator().generate(summary, strategy_name=f"Stock {sc}" if sc else "Overall Backtest", stock_code=sc)
        except Exception as exc:
            logger.exception("生成回测报告失败: %s", exc)
            return True
        return True

    if getattr(args, "realtime_monitor", False) or getattr(config, "realtime_monitor_enabled", False) or \
       getattr(args, "realtime_monitor_daemon", False) or getattr(config, "realtime_monitor_daemon_enabled", False):
        dm = getattr(args, "realtime_monitor_daemon", False) or getattr(config, "realtime_monitor_daemon_enabled", False)
        logger.info("模式: %s", "盘中实时监控守护进程" if dm else "盘中实时监控")
        from src.services.realtime_monitor import run_realtime_monitor
        sc = [canonical_stock_code(c) for c in args.stocks.split(",") if (c or "").strip()] if args.stocks else None
        run_realtime_monitor(stock_codes=sc, config=config, daemon_mode=dm)
        return True

    is_sched = getattr(args, "schedule", False) or getattr(config, "schedule_enabled", False)
    em_flag = getattr(args, "event_monitor", False) or (getattr(config, "event_monitor_enabled", False) and not is_sched)
    em_daemon = getattr(args, "event_monitor_daemon", False)
    if em_flag or em_daemon:
        logger.info("模式: %s", "事件驱动分析守护进程" if em_daemon else "事件驱动分析")
        import asyncio
        from src.services.event_monitor import run_event_monitor_cli
        codes = (getattr(config, "event_monitor_stock_codes", []) or stock_codes or config.stock_list)
        if args.stocks:
            codes = [canonical_stock_code(c) for c in args.stocks.split(",") if (c or "").strip()]
        asyncio.run(run_event_monitor_cli(stock_codes=codes, config=config, daemon=em_daemon or em_flag))
        return True

    if args.market_review:
        effective = None
        if not getattr(args, "force_run", False) and getattr(config, "trading_day_check_enabled", True):
            from src.core.trading_calendar import compute_effective_region, get_open_markets_today
            effective = compute_effective_region(getattr(config, "market_review_region", "cn"), get_open_markets_today())
            if effective == "":
                logger.info("今日大盘复盘相关市场均为非交易日，跳过执行。")
                return True
        logger.info("模式: 仅大盘复盘")
        from src.core.market_review import run_market_review
        from src.core.market_review_runtime import build_market_review_runtime
        n, a, s = build_market_review_runtime(config)
        _run_market_review_with_shared_lock(config, run_market_review, notifier=n, analyzer=a, search_service=s,
                                             send_notification=not args.no_notify, override_region=effective)
        return True

    if getattr(args, "weekend_intel", False) or getattr(args, "weekend_refresh", False):
        _run_weekend_intel(config, is_refresh=getattr(args, "weekend_refresh", False),
                           no_push=getattr(args, "weekend_intel_no_push", False))
        return True

    if getattr(args, "daily_intel", False):
        _run_daily_intel(config, getattr(args, "daily_intel_slot", "midday") or "midday")
        return True

    explicit_single = args.force_run or args.stocks is not None or args.dry_run or args.backtest or args.backtest_report
    if (args.schedule or config.schedule_enabled) and not explicit_single:
        _run_schedule_mode(config, args, stock_codes)
        return True

    if config.run_immediately:
        run_full_analysis(config, args, stock_codes)
    else:
        logger.info("配置为不立即运行分析 (RUN_IMMEDIATELY=false)")
    logger.info("\n程序执行完成")
    if start_serve and not (args.schedule or config.schedule_enabled):
        logger.info("API 服务运行中 (按 Ctrl+C 退出)...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    return True


def main() -> int:
    args = parse_arguments()
    config = _init_main_environment(args)
    if config is None:
        return 1

    if getattr(args, "check_notify", False):
        from src.services.notification_diagnostics import format_notification_diagnostics, run_notification_diagnostics
        result = run_notification_diagnostics(config)
        print(format_notification_diagnostics(result))
        return 0 if result.ok else 1

    stock_codes = None
    if args.stocks:
        stock_codes = [canonical_stock_code(c) for c in args.stocks.split(",") if (c or "").strip()]
        logger.info(f"使用命令行指定的股票列表: {stock_codes}")

    start_serve = _start_web_if_enabled(config, args)
    if args.serve_only:
        logger.info("模式: 仅 Web 服务")
        logger.info(f"Web 服务运行中: http://{args.host}:{args.port}")
        logger.info("按 Ctrl+C 退出...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\n用户中断，程序退出")
        return 0

    try:
        if _dispatch_mode(config, args, stock_codes, start_serve):
            return 0
    except KeyboardInterrupt:
        logger.info("\n用户中断，程序退出")
        return 130
    except Exception as e:
        logger.exception(f"程序执行失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
