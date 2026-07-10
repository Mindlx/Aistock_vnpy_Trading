"""
===================================
NotificationMixin — 通知相关方法
===================================

从 StockAnalysisPipeline 巨石类中提取的通知相关方法。
"""

import logging
import threading
from collections import defaultdict
from collections.abc import Callable

from data_provider.base import normalize_stock_code
from src.analyzer import AnalysisResult
from src.config import get_config
from src.enums import ReportType
from src.notification import NotificationChannel

logger = logging.getLogger(__name__)

# 防御性 guard：当实例绕过 __init__（如测试中 __new__）构造时，
# double-check 初始化 _single_stock_notify_lock 仍然线程安全。
_SINGLE_STOCK_NOTIFY_LOCK_INIT_GUARD = threading.Lock()


class NotificationMixin:
    """通知相关方法 mixin，由 StockAnalysisPipeline 多重继承。"""

    def _send_single_stock_notification(
        self,
        result: AnalysisResult,
        report_type: ReportType = ReportType.SIMPLE,
        fallback_code: str | None = None,
    ) -> None:
        """发送单股通知，供直接单股入口和批量串行推送共用。"""
        if not self.notifier.is_available():
            return

        stock_code = getattr(result, "code", None) or fallback_code or "unknown"
        notify_lock = getattr(self, "_single_stock_notify_lock", None)
        if notify_lock is None:
            with _SINGLE_STOCK_NOTIFY_LOCK_INIT_GUARD:
                notify_lock = getattr(self, "_single_stock_notify_lock", None)
                if notify_lock is None:
                    notify_lock = threading.Lock()
                    self._single_stock_notify_lock = notify_lock

        with notify_lock:
            try:
                if report_type == ReportType.FULL:
                    report_content = self.notifier.generate_dashboard_report([result])
                    logger.info(f"[{stock_code}] 使用完整报告格式")
                elif report_type == ReportType.BRIEF:
                    report_content = self.notifier.generate_brief_report([result])
                    logger.info(f"[{stock_code}] 使用简洁报告格式")
                else:
                    report_content = self.notifier.generate_single_stock_report(result)
                    logger.info(f"[{stock_code}] 使用精简报告格式")

                if self.notifier.send(
                    report_content,
                    email_stock_codes=[stock_code],
                    route_type="report",
                    severity="info",
                    dedup_key=f"report:single:{stock_code}:{report_type.value}",
                    cooldown_key=f"report:single:{stock_code}:{report_type.value}",
                ):
                    logger.info(f"[{stock_code}] 单股推送成功")
                else:
                    logger.warning(f"[{stock_code}] 单股推送失败")
            except Exception as e:
                logger.error(f"[{stock_code}] 单股推送异常: {e}")

    def _save_local_report(
        self,
        results: list[AnalysisResult],
        report_type: ReportType = ReportType.SIMPLE,
    ) -> None:
        """保存分析报告到本地文件（与通知推送解耦）"""
        try:
            report = self._generate_aggregate_report(results, report_type)
            filepath = self.notifier.save_report_to_file(report)
            logger.info(f"决策仪表盘日报已保存: {filepath}")
        except Exception as e:
            logger.error(f"保存本地报告失败: {e}")

    def _send_notifications(
        self,
        results: list[AnalysisResult],
        report_type: ReportType = ReportType.SIMPLE,
        skip_push: bool = False,
    ) -> None:
        """
        发送分析结果通知

        生成决策仪表盘格式的报告

        Args:
            results: 分析结果列表
            skip_push: 是否跳过推送（仅保存到本地，用于单股推送模式）
        """
        noise_decision = None
        noise_finalized = False
        try:
            logger.info("生成决策仪表盘日报...")
            report = self._generate_aggregate_report(results, report_type)

            # Multi-user routing: process grouped stock→channel mappings before default dispatch
            try:
                from src.core.multi_user import parse_user_groups, route_stock_results, send_to_group

                user_groups = parse_user_groups()
                if user_groups:
                    routed = route_stock_results(results, user_groups)
                    for idx, group_results in routed.items():
                        if idx == -1:
                            continue
                        if not group_results:
                            continue
                        group_report = self._generate_aggregate_report(group_results, report_type)
                        group = user_groups[idx]
                        send_to_group(self.notifier, group_report, group)
                        logger.info(
                            "[MultiUser] ✅ 已向用户组 %d 推送 %d 只股票分析 (%s)",
                            idx + 1,
                            len(group_results),
                            ", ".join(str(getattr(r, "code", "?")) for r in group_results),
                        )
            except Exception as exc:
                logger.warning("[MultiUser] 多用户路由失败，回退到默认通知: %s", exc)

            # 跳过推送（单股推送模式 / 合并模式：报告已由 _save_local_report 保存）
            if skip_push:
                return

            # 推送通知
            if self.notifier.is_available():
                channels = self.notifier.get_available_channels()
                channels = self.notifier.get_channels_for_route("report", channels=channels)
                context_success = self.notifier.send_to_context(report)
                if channels and hasattr(self.notifier, "evaluate_noise_control"):
                    report_type_key = report_type.value if isinstance(report_type, ReportType) else str(report_type)
                    codes_key = ",".join(sorted(str(getattr(result, "code", "") or "") for result in results))
                    noise_key = f"report:aggregate:{report_type_key}:{codes_key}"
                    noise_decision = self.notifier.evaluate_noise_control(
                        report,
                        route_type="report",
                        severity="info",
                        dedup_key=noise_key,
                        cooldown_key=noise_key,
                    )
                    if not noise_decision.should_send:
                        logger.info(noise_decision.message)
                        return

                # Issue #455: Markdown 转图片（与 notification.send 逻辑一致）
                from src.md2img import markdown_to_image

                channels_needing_image = {
                    ch
                    for ch in channels
                    if ch.value in self.notifier._markdown_to_image_channels
                    and ch not in {NotificationChannel.NTFY, NotificationChannel.GOTIFY}
                }
                non_wechat_channels_needing_image = {
                    ch for ch in channels_needing_image if ch != NotificationChannel.WECHAT
                }

                def _get_md2img_hint() -> str:
                    try:
                        engine = getattr(get_config(), "md2img_engine", "wkhtmltoimage")
                    except Exception:
                        engine = "wkhtmltoimage"
                    return (
                        "npm i -g markdown-to-file"
                        if engine == "markdown-to-file"
                        else "wkhtmltopdf (apt install wkhtmltopdf / brew install wkhtmltopdf)"
                    )

                def _send_channel_safely(channel_label: str, send_func: Callable[[], bool]) -> bool:
                    try:
                        return bool(send_func())
                    except Exception as e:
                        logger.exception(
                            "通知渠道 %s 推送异常，继续尝试其他渠道: %s",
                            channel_label,
                            e,
                        )
                        return False

                image_bytes = None
                if non_wechat_channels_needing_image:
                    image_bytes = markdown_to_image(report, max_chars=self.notifier._markdown_to_image_max_chars)
                    if image_bytes:
                        logger.info(
                            "Markdown 已转换为图片，将向 %s 发送图片",
                            [ch.value for ch in non_wechat_channels_needing_image],
                        )
                    else:
                        logger.warning(
                            "Markdown 转图片失败，将回退为文本发送。请检查 MARKDOWN_TO_IMAGE_CHANNELS 配置并安装 %s",
                            _get_md2img_hint(),
                        )

                # 企业微信：发送精简文本（generate_wechat_dashboard 紧凑版）
                wechat_success = False
                if NotificationChannel.WECHAT in channels:

                    def _send_wechat_report() -> bool:
                        from datetime import datetime as _dt
                        dashboard_content = (
                            self.notifier.generate_brief_report(results)
                            if report_type == ReportType.BRIEF
                            else self.notifier.generate_wechat_dashboard(results)
                        )

                        # FULL 报告（整点分析）改为 PDF 推送：简短摘要 + PDF 附件
                        if report_type != ReportType.BRIEF and len(dashboard_content) > 500:
                            try:
                                from src.md2img import markdown_to_pdf
                                pdf_data = markdown_to_pdf(dashboard_content, font_size="18pt")
                                if pdf_data:
                                    _date_str = _dt.now().strftime("%Y%m%d_%H%M")
                                    _pdf_name = f"{_date_str}_整点分析报告.pdf"
                                    # 提取简讯: 统计 + 建议买入 + 建议卖出
                                    _lines = dashboard_content.split("\n")
                                    _buy = [l for l in _lines if "🟢" in l and "买入" in l]
                                    _sell = [l for l in _lines if "🔴" in l and "卖出" in l]
                                    _total = next((l for l in _lines if "共分析" in l), "")
                                    _buy_names = []
                                    if _buy:
                                        _buy_names = [l.split("**")[1] if "**" in l else l.split()[1] for l in _buy[:5]]
                                    _sell_names = []
                                    if _sell:
                                        _sell_names = [l.split("**")[1] if "**" in l else l.split()[1] for l in _sell[:5]]
                                    _neutral = len(results) - len(_buy) - len(_sell)
                                    _now_str = _dt.now().strftime("%H:%M")
                                    _brief = (
                                        f"👾 {_now_str} 整点分析\n"
                                        f"本次共分析{len(results)}只自选股票，建议买入{len(_buy)}只-{', '.join(_buy_names)}；"
                                        f"卖出{len(_sell)}只-{', '.join(_sell_names)}；"
                                        f"中立持有{_neutral}只。\n"
                                        f"完整报告见附件PDF"
                                    )
                                    if self.notifier.send_to_wechat(_brief):
                                        return self.notifier.send_to_wechat_file(pdf_data, _pdf_name)
                            except Exception as e:
                                logger.warning(f"整点分析 PDF 生成失败, 回退到文本: {e}")

                        logger.info(f"企业微信仪表盘长度: {len(dashboard_content)} 字符")
                        logger.debug(f"企业微信推送内容:\n{dashboard_content}")
                        return self.notifier.send_to_wechat(dashboard_content)

                    wechat_success = _send_channel_safely(
                        NotificationChannel.WECHAT.value,
                        _send_wechat_report,
                    )

                # 其他渠道：发完整报告（避免自定义 Webhook 被 wechat 截断逻辑污染）
                non_wechat_success = False
                stock_email_groups = getattr(self.config, "stock_email_groups", []) or []
                for channel in channels:
                    if channel == NotificationChannel.WECHAT:
                        continue
                    if channel == NotificationChannel.FEISHU:
                        non_wechat_success = (
                            _send_channel_safely(
                                channel.value,
                                lambda: self.notifier.send_to_feishu(report),
                            )
                            or non_wechat_success
                        )
                    elif channel == NotificationChannel.TELEGRAM:

                        def _send_telegram_report() -> bool:
                            use_image = self.notifier._should_use_image_for_channel(channel, image_bytes)
                            if use_image:
                                return self.notifier._send_telegram_photo(image_bytes)
                            return self.notifier.send_to_telegram(report)

                        non_wechat_success = (
                            _send_channel_safely(
                                channel.value,
                                _send_telegram_report,
                            )
                            or non_wechat_success
                        )
                    elif channel == NotificationChannel.EMAIL:
                        if stock_email_groups:
                            code_to_emails: dict[str, list[str] | None] = {}
                            for r in results:
                                if r.code not in code_to_emails:
                                    canonical = normalize_stock_code(r.code)
                                    emails = []
                                    for stocks, emails_list in stock_email_groups:
                                        if canonical in stocks:
                                            emails.extend(emails_list)
                                    code_to_emails[r.code] = list(dict.fromkeys(emails)) if emails else None
                            emails_to_results: dict[tuple | None, list] = defaultdict(list)
                            for r in results:
                                recs = code_to_emails.get(r.code)
                                key = tuple(recs) if recs else None
                                emails_to_results[key].append(r)
                            for key, group_results in emails_to_results.items():
                                receivers = list(key) if key is not None else None

                                def _send_email_group(
                                    group_results=group_results,
                                    receivers=receivers,
                                ) -> bool:
                                    grp_report = self._generate_aggregate_report(group_results, report_type)
                                    grp_image_bytes = None
                                    if channel.value in self.notifier._markdown_to_image_channels:
                                        grp_image_bytes = markdown_to_image(
                                            grp_report,
                                            max_chars=self.notifier._markdown_to_image_max_chars,
                                        )
                                    use_image = self.notifier._should_use_image_for_channel(channel, grp_image_bytes)
                                    if use_image:
                                        return self.notifier._send_email_with_inline_image(
                                            grp_image_bytes, receivers=receivers
                                        )
                                    return self.notifier.send_to_email(grp_report, receivers=receivers)

                                email_label = (
                                    f"{channel.value}:{','.join(receivers)}"
                                    if receivers
                                    else f"{channel.value}:default"
                                )
                                non_wechat_success = (
                                    _send_channel_safely(
                                        email_label,
                                        _send_email_group,
                                    )
                                    or non_wechat_success
                                )
                        else:

                            def _send_email_report() -> bool:
                                use_image = self.notifier._should_use_image_for_channel(channel, image_bytes)
                                if use_image:
                                    return self.notifier._send_email_with_inline_image(image_bytes)
                                return self.notifier.send_to_email(report)

                            non_wechat_success = (
                                _send_channel_safely(
                                    channel.value,
                                    _send_email_report,
                                )
                                or non_wechat_success
                            )
                    elif channel == NotificationChannel.CUSTOM:

                        def _send_custom_report() -> bool:
                            use_image = self.notifier._should_use_image_for_channel(channel, image_bytes)
                            if use_image:
                                return self.notifier._send_custom_webhook_image(image_bytes, fallback_content=report)
                            return self.notifier.send_to_custom(report)

                        non_wechat_success = (
                            _send_channel_safely(
                                channel.value,
                                _send_custom_report,
                            )
                            or non_wechat_success
                        )
                    elif channel == NotificationChannel.PUSHPLUS:
                        non_wechat_success = (
                            _send_channel_safely(
                                channel.value,
                                lambda: self.notifier.send_to_pushplus(report),
                            )
                            or non_wechat_success
                        )
                    elif channel == NotificationChannel.SERVERCHAN3:
                        non_wechat_success = (
                            _send_channel_safely(
                                channel.value,
                                lambda: self.notifier.send_to_serverchan3(report),
                            )
                            or non_wechat_success
                        )
                    elif channel == NotificationChannel.DISCORD:
                        non_wechat_success = (
                            _send_channel_safely(
                                channel.value,
                                lambda: self.notifier.send_to_discord(report),
                            )
                            or non_wechat_success
                        )
                    elif channel == NotificationChannel.PUSHOVER:
                        non_wechat_success = (
                            _send_channel_safely(
                                channel.value,
                                lambda: self.notifier.send_to_pushover(report),
                            )
                            or non_wechat_success
                        )
                    elif channel == NotificationChannel.NTFY:
                        non_wechat_success = (
                            _send_channel_safely(
                                channel.value,
                                lambda: self.notifier.send_to_ntfy(report),
                            )
                            or non_wechat_success
                        )
                    elif channel == NotificationChannel.GOTIFY:
                        non_wechat_success = (
                            _send_channel_safely(
                                channel.value,
                                lambda: self.notifier.send_to_gotify(report),
                            )
                            or non_wechat_success
                        )
                    elif channel == NotificationChannel.ASTRBOT:
                        non_wechat_success = (
                            _send_channel_safely(
                                channel.value,
                                lambda: self.notifier.send_to_astrbot(report),
                            )
                            or non_wechat_success
                        )
                    elif channel == NotificationChannel.SLACK:

                        def _send_slack_report() -> bool:
                            use_image = self.notifier._should_use_image_for_channel(channel, image_bytes)
                            if use_image and self.notifier._slack_bot_token and self.notifier._slack_channel_id:
                                return self.notifier._send_slack_image(image_bytes, fallback_content=report)
                            return self.notifier.send_to_slack(report)

                        non_wechat_success = (
                            _send_channel_safely(
                                channel.value,
                                _send_slack_report,
                            )
                            or non_wechat_success
                        )
                    else:
                        logger.warning(f"未知通知渠道: {channel}")

                success = wechat_success or non_wechat_success or context_success
                if (
                    (wechat_success or non_wechat_success)
                    and noise_decision is not None
                    and hasattr(self.notifier, "record_noise_control")
                ):
                    self.notifier.record_noise_control(noise_decision)
                    noise_finalized = True
                elif noise_decision is not None and hasattr(self.notifier, "release_noise_control"):
                    self.notifier.release_noise_control(noise_decision)
                    noise_finalized = True
                if success:
                    logger.info("决策仪表盘推送成功")
                else:
                    logger.warning("决策仪表盘推送失败")
            else:
                logger.info("通知渠道未配置，跳过推送")

        except Exception as e:
            if noise_decision is not None and not noise_finalized and hasattr(self.notifier, "release_noise_control"):
                self.notifier.release_noise_control(noise_decision)
            import traceback

            logger.error(f"发送通知失败: {e}\n{traceback.format_exc()}")

    def _generate_aggregate_report(
        self,
        results: list[AnalysisResult],
        report_type: ReportType,
    ) -> str:
        """Generate aggregate report with backward-compatible notifier fallback."""
        generator = getattr(self.notifier, "generate_aggregate_report", None)
        if callable(generator):
            return generator(results, report_type)
        if report_type == ReportType.BRIEF and hasattr(self.notifier, "generate_brief_report"):
            return self.notifier.generate_brief_report(results)
        return self.notifier.generate_dashboard_report(results)
