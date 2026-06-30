"""
===================================
股票智能分析系统 - 大盘复盘模块（支持 A 股 / 港股 / 美股）
===================================

职责：
1. 根据 MARKET_REVIEW_REGION 配置选择市场区域（cn / hk / us / both）
2. 执行大盘复盘分析并生成复盘报告
3. 保存和发送复盘报告
"""

import logging
import re
import uuid
from datetime import datetime

from src.analyzer import AnalysisResult, GeminiAnalyzer
from src.config import get_config
from src.market_analyzer import MarketAnalyzer
from src.notification import NotificationService
from src.report_language import normalize_report_language
from src.search_service import SearchService

logger = logging.getLogger(__name__)

MARKET_REVIEW_HISTORY_CODE = "MARKET"
MARKET_REVIEW_REPORT_TYPE = "market_review"


def _get_market_review_text(language: str) -> dict[str, str]:
    normalized = normalize_report_language(language)
    if normalized == "en":
        return {
            "root_title": "# 🎯 Market Review",
            "push_title": "🎯 Market Review",
            "cn_title": "# A-share Market Recap",
            "us_title": "# US Market Recap",
            "hk_title": "# HK Market Recap",
            "separator": "> Next market recap follows",
        }
    return {
        "root_title": "# 🎯ml 大盘复盘",
        "push_title": "🎯ml 大盘复盘",
        "cn_title": "# A股大盘复盘",
        "us_title": "# 美股大盘复盘",
        "hk_title": "# 港股大盘复盘",
        "separator": "> 以下为下一市场大盘复盘",
    }


def _extract_summary_text(content: str, pdf_ok: bool = False, session_label: str = "全天") -> str:
    """提取精简微信摘要：🎯HH:MM大盘复盘（午盘/全天）：概述 +（可选）PDF 链接。"""
    lines = content.split("\n")
    sections: dict[str, list[str]] = {}
    current_heading = "_overview"
    for line in lines:
        m = re.match(r"^###\s+(.+)", line.strip())
        if m:
            current_heading = m.group(1)
            sections.setdefault(current_heading, [])
        else:
            sections.setdefault(current_heading, []).append(line)

    def _first_para(lines: list[str]) -> str:
        for line in lines:
            s = line.strip()
            if not s or s == "---" or "|" in s:
                continue
            if s.startswith("```") or s.startswith("!"):
                continue
            if s.startswith("#"):
                continue
            clean = s.lstrip("> *-").strip()
            if re.match(r"^[🟢🟡🔴✅❌📊📈📉🎯🚨💡⚡🔥💤↗↘→\s\d%./()\-+:,]+$", clean):
                continue
            if len(clean) > 15:
                return clean
        return ""

    # Build header: 🎯HH:MM大盘复盘（午盘/全天）
    now_str = datetime.now().strftime("%H:%M")
    header = f"🎯{now_str}大盘复盘（{session_label}）"

    # Overview paragraph — 从 一、盘面总览 第一段提取
    overview_para = _first_para(sections.get("一、盘面总览", []))
    if overview_para:
        # Truncate long paragraphs to keep the one-line format compact
        if len(overview_para) > 120:
            overview_para = overview_para[:117] + "..."
        summary = f"{header}：{overview_para}"
    else:
        summary = header

    # 附加上期验证行（拼接到摘要末尾，保留信息量）
    for line in sections.get("_overview", []):
        s = line.strip()
        if s.startswith("> 上期建议"):
            verify_text = s.lstrip("> ").strip()
            if len(verify_text) <= 100:
                summary += f" {verify_text}"
            break

    if len(summary) > 1500:
        summary = summary[:1497] + "..."
    if pdf_ok:
        summary += "\n\n📎 详细内容见PDF文档"
    return summary


def _wecom_markdown_clean(content: str) -> str:
    """将 markdown 转换为企业微信兼容格式。

    企业微信 Webhook markdown 只支持：**加粗**、[链接](url)、- 列表。
    标题(#/##/###)、引用(>)、表格(|)、分隔线(---) 会显示为纯文本，需转换。
    """
    lines = content.split("\n")
    result = []

    for line in lines:
        # 分隔线 --- → 跳过
        if re.match(r"^---+\s*$", line):
            continue

        # 表格行
        if "|" in line and re.search(r"\|.*\|", line):
            # 表头分隔行（|---|---|---|）→ 跳过
            if re.match(r"^\|[\s\-:|]+\|$", line):
                continue
            # 数据行
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if cells:
                result.append("  " + " | ".join(cells))
            continue

        # 引用行
        if line.startswith("> "):
            result.append(line[2:])
            continue
        if line == ">":
            result.append("")
            continue

        # 标题 ##/### → **标题**
        m = re.match(r"^(#{1,3})\s+(.+)$", line)
        if m:
            result.append(f"**{m.group(2)}**")
            continue

        result.append(line)

    return "\n".join(result)


def run_market_review(
    notifier: NotificationService,
    analyzer: GeminiAnalyzer | None = None,
    search_service: SearchService | None = None,
    send_notification: bool = True,
    merge_notification: bool = False,
    override_region: str | None = None,
    query_id: str | None = None,
) -> str | None:
    """
    执行大盘复盘分析

    Args:
        notifier: 通知服务
        analyzer: AI分析器（可选）
        search_service: 搜索服务（可选）
        send_notification: 是否发送通知
        merge_notification: 是否合并推送（跳过本次推送，由 main 层合并个股+大盘后统一发送，Issue #190）
        override_region: 覆盖 config 的 market_review_region（Issue #373 交易日过滤后有效子集）
        query_id: 历史记录关联 ID；API 后台任务会传入 task_id，CLI/Bot 为空时自动生成

    Returns:
        复盘报告文本
    """
    logger.info("开始执行大盘复盘分析...")
    config = get_config()
    review_text = _get_market_review_text(getattr(config, "report_language", "zh"))
    region = override_region if override_region is not None else (getattr(config, "market_review_region", "cn") or "cn")
    _ALL_MARKETS = [("cn", "cn_title", "A 股"), ("hk", "hk_title", "港股"), ("us", "us_title", "美股")]
    _VALID_SINGLES = {"cn", "us", "hk"}

    # Determine which markets to run.
    # region can be: 'cn', 'hk', 'us', 'both', or a comma-joined subset like 'cn,us'.
    if "," in region:
        run_markets = [m.strip() for m in region.split(",") if m.strip() in _VALID_SINGLES]
    elif region == "both":
        run_markets = list(_VALID_SINGLES)
    elif region in _VALID_SINGLES:
        run_markets = [region]
    else:
        run_markets = ["cn"]

    # 判断时段标记（提前到 run_daily_review 之前，用于成交额对比数据源选择）
    hour = datetime.now().hour
    session_label = "午盘" if hour < 15 else "全天"

    try:
        if len(run_markets) > 1:
            # 多市场顺序执行，合并报告
            parts = []
            for mkt, title_key, label in _ALL_MARKETS:
                if mkt not in run_markets:
                    continue
                logger.info("生成 %s 大盘复盘报告...", label)
                mkt_analyzer = MarketAnalyzer(search_service=search_service, analyzer=analyzer, region=mkt)
                mkt_report = mkt_analyzer.run_daily_review(session_label)
                if mkt_report:
                    parts.append(f"{review_text[title_key]}\n\n{mkt_report}")
            review_report = f"\n\n---\n\n{review_text['separator']}\n\n".join(parts) if parts else None
        else:
            market_analyzer = MarketAnalyzer(
                search_service=search_service,
                analyzer=analyzer,
                region=region,
            )
            review_report = market_analyzer.run_daily_review(session_label)

        # 时段标记已在 try 块前初始化，下方直接使用
        date_str = datetime.now().strftime("%Y%m%d")
        date_str_display = datetime.now().strftime("%Y-%m-%d")
        review_text['root_title'] = f"# 🎯 {date_str_display} 大盘复盘（{session_label}）"
        review_text['push_title'] = f"🎯ml {date_str_display} 大盘复盘（{session_label}）"

        # 午盘复盘：明日交易计划 → 午后交易计划
        if review_report and session_label == "午盘":
            review_report = review_report.replace("六、明日交易计划", "六、午后交易计划")
            review_report = review_report.replace("明日交易计划", "午后交易计划")

        # 报告正文的日期标题也加上时段标记
        if review_report:
            review_report = re.sub(
                r"(##\s+\d{4}-\d{2}-\d{2}\s+大盘复盘)",
                rf"\g<1>（{session_label}）",
                review_report,
            )

        if review_report:
            # 保存报告到文件（含 treemap，独立于推送通道）
            report_content = review_report
            try:
                from src.market_analyzer import build_sector_treemap, get_cached_sector_rankings
                _sectors_raw = get_cached_sector_rankings(n=40)  # @calibration 缓存板块排名，合入上游时禁止修改
                # get_sector_rankings 返回 (上涨板块列表, 下跌板块列表)
                if _sectors_raw and len(_sectors_raw) == 2:
                    # 合并涨跌列表，按成交额排序取 Top 16（确保方块面积可读）
                    _all_sectors_raw = _sectors_raw[0] + _sectors_raw[1]
                    _all_sectors = sorted(
                        _all_sectors_raw,
                        key=lambda s: float(s.get('amount', 0) or 0),
                        reverse=True,
                    )[:16]
                else:
                    _all_sectors = _sectors_raw if isinstance(_sectors_raw, list) else []
                if _all_sectors:
                    _treemap_uri = build_sector_treemap([{
                        "name": s.get("name", "?"),
                        "change_pct": s.get("change_pct", 0),
                        "amount": s.get("amount", 1),
                    } for s in _all_sectors if s.get("change_pct") is not None])
                    if _treemap_uri:
                        _treemap_section = f"\n\n![]({_treemap_uri})\n"
                        # 插入到领涨板块 Top 5 表之前
                        report_content = report_content.replace(
                            "\n#### 领涨板块 Top 5",
                            _treemap_section + "\n#### 领涨板块 Top 5",
                        )
            except Exception:
                logger.debug("板块 treemap 生成跳过", exc_info=True)

            report_filename = f"market_review_{date_str}_{session_label}.md"
            filepath = notifier.save_report_to_file(f"{review_text['root_title']}\n\n{report_content}", report_filename)
            logger.info(f"大盘复盘报告已保存: {filepath}")

            _persist_market_review_history(
                review_report=review_report,
                markdown_report=f"{review_text['root_title']}\n\n{report_content}",
                region=region,
                config=config,
                query_id=query_id,
            )

            # 推送通知（合并模式下跳过，由 main 层统一发送）
            if merge_notification and send_notification:
                logger.info("合并推送模式：跳过大盘复盘单独推送，将在个股+大盘复盘后统一发送")
            elif send_notification and notifier.is_available():
                from src.md2img import markdown_to_pdf
                from src.notification import NotificationChannel

                wechat_channels = [ch for ch in notifier.get_channels_for_route("report")
                                   if ch == NotificationChannel.WECHAT]
                if wechat_channels:
                    # 企业微信通道：PDF 生成 + 推送（treemap 已在 report_content 中）
                    pdf_date = datetime.now().strftime("%Y%m%d")
                    pdf_filename = f"{pdf_date}大盘复盘报告_{session_label}.pdf"
                    pdf_data = markdown_to_pdf(report_content, font_size="18pt")

                    # 2. 先发文字摘要（PDF 生成成功则附带链接）
                    summary = _extract_summary_text(review_report, pdf_ok=bool(pdf_data), session_label=session_label)
                    notifier.send_to_wechat(summary)

                    # 3. 再发 PDF 文件
                    if pdf_data and notifier.send_to_wechat_file(pdf_data, pdf_filename):
                        logger.info("大盘复盘: PDF 已推送 (%s)", pdf_filename)
                    else:
                        logger.warning("大盘复盘: PDF 推送失败（文字摘要已发送）")

                # 其他渠道：发送完整 Markdown
                for ch in notifier.get_channels_for_route("report"):
                    if ch == NotificationChannel.WECHAT:
                        continue
                    notifier._send_to_static_channel(
                        ch, report_content,
                        image_bytes=None,
                        email_stock_codes=None,
                        email_send_to_all=True,
                    )

                logger.info("大盘复盘推送完成")
            elif not send_notification:
                logger.info("已跳过推送通知 (--no-notify)")

            return review_report

    except Exception as e:
        logger.error(f"大盘复盘分析失败: {e}")

    return None


def _persist_market_review_history(
    *,
    review_report: str,
    markdown_report: str,
    region: str,
    config: object,
    query_id: str | None = None,
) -> int:
    """Persist market review output into the existing analysis history table."""
    try:
        from src.storage import DatabaseManager

        report_language = normalize_report_language(getattr(config, "report_language", "zh"))
        summary = _summarize_market_review(review_report, report_language)
        if report_language == "en":
            stock_name = "Market Review"
            operation_advice = "View review"
            trend_prediction = "Market review"
        else:
            stock_name = "大盘复盘"
            operation_advice = "查看复盘"
            trend_prediction = "大盘复盘"

        result = AnalysisResult(
            code=MARKET_REVIEW_HISTORY_CODE,
            name=stock_name,
            sentiment_score=50,
            trend_prediction=trend_prediction,
            operation_advice=operation_advice,
            analysis_summary=summary,
            report_language=report_language,
            news_summary=review_report,
            raw_response=markdown_report,
            data_sources="market_review",
        )

        history_query_id = query_id or f"market_review_{uuid.uuid4().hex}"
        context_snapshot = {
            "report_kind": MARKET_REVIEW_REPORT_TYPE,
            "market_review_region": region,
            "report_language": report_language,
        }

        saved = DatabaseManager.get_instance().save_analysis_history(
            result=result,
            query_id=history_query_id,
            report_type=MARKET_REVIEW_REPORT_TYPE,
            news_content=review_report,
            context_snapshot=context_snapshot,
            save_snapshot=True,
        )
        if saved:
            logger.info("大盘复盘历史记录已保存: query_id=%s", history_query_id)
        else:
            logger.warning("大盘复盘历史记录保存失败: query_id=%s", history_query_id)
        return saved
    except Exception as exc:
        logger.warning("大盘复盘历史记录保存异常，报告文件与推送流程继续: %s", exc, exc_info=True)
        return 0


def _summarize_market_review(review_report: str, report_language: str) -> str:
    for line in (review_report or "").splitlines():
        text = line.strip().lstrip("#").strip()
        if text and not text.startswith("---") and not text.startswith(">"):
            return text[:200]
    return "Market review report generated." if report_language == "en" else "大盘复盘报告已生成。"
