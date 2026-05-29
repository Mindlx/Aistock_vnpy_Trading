"""Multi-user notification routing.

Extends STOCK_GROUP_N pattern to support per-user notification channels.
Users configure their stock list and notification endpoint via env vars:

    STOCK_GROUP_1=600519,300652
    NOTIFY_1=wechat:https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx

    STOCK_GROUP_2=hk00700
    NOTIFY_2=feishu:https://open.feishu.cn/open-apis/bot/v2/hook/xxx

    STOCK_GROUP_3=AAPL
    NOTIFY_3=telegram:123456:ABCdef:123456789

NOTIFY_N format: channel_type:config_value
  wechat:  webhook_url
  feishu:  webhook_url
  telegram: bot_token:chat_id
  discord: webhook_url
  email:   email_address

Analysis runs ONCE on all stocks. Reports are filtered per user at notification time.
No concurrency issue — single analysis, multi-route dispatch.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)


def parse_user_groups() -> list[dict]:
    """Parse STOCK_GROUP_N + NOTIFY_N from environment.

    Returns:
        list of dicts with keys: stocks (list), notifies (list of (channel, config))
    """
    groups: dict[int, dict] = {}
    stock_re = re.compile(r"^STOCK_GROUP_(\d+)$", re.IGNORECASE)
    notify_re = re.compile(r"^NOTIFY_(\d+)$", re.IGNORECASE)

    for key in os.environ:
        m = stock_re.match(key)
        if m:
            idx = int(m.group(1))
            val = os.environ[key].strip()
            groups.setdefault(idx, {})["stocks"] = [c.strip() for c in val.split(",") if c.strip()]

        m = notify_re.match(key)
        if m:
            idx = int(m.group(1))
            val = os.environ[key].strip()
            notifies = groups.setdefault(idx, {}).setdefault("notifies", [])
            for entry in val.split(","):
                entry = entry.strip()
                if ":" not in entry:
                    continue
                channel, _, config = entry.partition(":")
                notifies.append((channel.strip().lower(), config.strip()))

    result = []
    for idx in sorted(groups.keys()):
        g = groups[idx]
        if "stocks" in g and "notifies" in g and g["stocks"] and g["notifies"]:
            result.append({"stocks": g["stocks"], "notifies": g["notifies"]})
    return result


def route_stock_results(results: list, user_groups: list[dict]) -> dict[int, list]:
    """Split analysis results by user group.

    Returns:
        dict mapping group_index → list of results matching that group's stocks
    """
    if not user_groups:
        return {-1: results}  # no groups → all results to default

    routed: dict[int, list] = {}
    assigned: set[str] = set()

    for i, group in enumerate(user_groups):
        group_stocks = set(group["stocks"])
        group_results = [r for r in results if r.code in group_stocks]
        routed[i] = group_results
        assigned.update(r.code for r in group_results)

    # Unassigned stocks go to default channel
    unassigned = [r for r in results if r.code not in assigned]
    if unassigned:
        routed[-1] = unassigned

    return routed


def send_to_group(notifier, content: str, group: dict) -> bool:
    """Send notification to a user group's configured channels.

    Args:
        notifier: NotificationService instance
        content: markdown content
        group: dict with 'notifies' list of (channel, config) tuples

    Returns:
        True if at least one channel succeeded
    """
    success = False
    for channel, config in group.get("notifies", []):
        try:
            if channel in ("wechat", "feishu", "discord"):
                # Use custom webhook sender
                result = _send_to_webhook(channel, content, config, notifier)
            elif channel == "email":
                result = notifier.send(content, email_send_to_all=False, route_type="report")
            elif channel == "telegram":
                result = _send_to_telegram(content, config, notifier)
            else:
                logger.warning("[MultiUser] unknown channel: %s", channel)
                result = False
            if result:
                success = True
        except Exception as exc:
            logger.warning("[MultiUser] send to %s failed: %s", channel, exc)
    return success


def _send_to_webhook(channel: str, content: str, url: str, notifier) -> bool:
    """Send to webhook-based channels."""
    try:
        import requests
        if channel == "wechat":
            payload = {"msgtype": "markdown", "markdown": {"content": content}}
            resp = requests.post(url, json=payload, timeout=10)
            return resp.status_code == 200 and resp.json().get("errcode") == 0
        elif channel == "feishu":
            payload = {"msg_type": "interactive", "card": {"header": {"title": {"tag": "plain_text", "content": "每日分析"}}, "elements": [{"tag": "markdown", "content": content}]}}
            resp = requests.post(url, json=payload, timeout=10)
            return resp.status_code == 200
        elif channel == "discord":
            payload = {"content": content[:2000]}
            resp = requests.post(url, json=payload, timeout=10)
            return resp.status_code == 204
    except Exception:
        pass
    return False


def _send_to_telegram(content: str, config: str, notifier) -> bool:
    """Send to Telegram via bot token and chat ID."""
    try:
        import requests
        parts = config.split(":", 1)
        if len(parts) != 2:
            return False
        bot_token, chat_id = parts[0].strip(), parts[1].strip()
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": content[:4000], "parse_mode": "Markdown"}
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False
