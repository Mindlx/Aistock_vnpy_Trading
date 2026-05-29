"""
EventMonitor — 阈值告警系统（向后兼容 shim）。

⚠️ 已统一至 src/services/event_monitor.py。
此文件仅作向后兼容的 re-export，新代码请直接导入 event_monitor。

Usage::
    from src.services.event_monitor import ThresholdEventMonitor, PriceThresholdAlert
"""

from __future__ import annotations

import warnings

from src.services.event_monitor import (
    PriceChangeThresholdAlert as PriceChangeAlert,
    PriceThresholdAlert as PriceAlert,
    ThresholdAlertRule as AlertRule,
    ThresholdAlertStatus as AlertStatus,
    ThresholdAlertType as AlertType,
    ThresholdEventMonitor as EventMonitor,
    TriggeredThresholdAlert as TriggeredAlert,
    VolumeThresholdAlert as VolumeAlert,
    _read_quote_float,
    _supported_threshold_type_names as _supported_alert_type_names,
    _ensure_supported_threshold_type as _ensure_runtime_supported_alert_type,
    parse_threshold_alert_rules as parse_event_alert_rules,
    validate_threshold_alert_rule as validate_event_alert_rule,
    ThresholdAlertType,
    ThresholdAlertStatus,
)

# 向后兼容：原 SentimentAlert 占位类
class SentimentAlert:
    """情感偏移告警（占位，仅向后兼容）。"""
    def __init__(self, **kwargs):
        self.stock_code = kwargs.get("stock_code", "")
        self.alert_type = ThresholdAlertType.SENTIMENT_SHIFT
        self.description = f"{self.stock_code} sentiment shift"
        self.status = ThresholdAlertStatus.ACTIVE

warnings.warn(
    "Import from src.agent.events is deprecated. "
    "Use src.services.event_monitor instead.",
    DeprecationWarning,
    stacklevel=2,
)


def build_event_monitor_from_config(config=None, notifier=None):
    """构建 ThresholdEventMonitor（原 EventMonitor）"""
    from src.services.event_monitor import (
        ThresholdAlertStatus,
        ThresholdAlertType,
        ThresholdEventMonitor,
        TriggeredThresholdAlert,
        parse_threshold_alert_rules,
    )

    if config is None:
        from src.config import get_config
        config = get_config()

    if not getattr(config, "agent_event_monitor_enabled", False):
        return None

    raw_rules = getattr(config, "agent_event_alert_rules_json", "")
    try:
        rules = parse_threshold_alert_rules(raw_rules)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "[EventMonitor] Failed to parse configured alert rules: %s", exc
        )
        return None

    if not rules:
        return None

    monitor = ThresholdEventMonitor.from_dict_list(rules)
    if not monitor.rules:
        return None

    from src.notification import NotificationBuilder, NotificationService

    notification_service = notifier or NotificationService()

    def _notify(triggered: TriggeredThresholdAlert) -> None:
        title = f"Event Alert | {triggered.rule.stock_code}"
        content = triggered.message or triggered.rule.description or "Alert triggered"
        alert_text = NotificationBuilder.build_simple_alert(
            title=title, content=content, alert_type="warning"
        )
        sent = notification_service.send(alert_text, route_type="alert")
        if not sent:
            import logging
            logging.getLogger(__name__).info(
                "[EventMonitor] No notification channel available for alert: %s", title
            )

    monitor.on_trigger(_notify)
    return monitor


def run_event_monitor_once(monitor):
    """运行一次同步监控。"""
    import asyncio
    return asyncio.run(monitor.check_all())
