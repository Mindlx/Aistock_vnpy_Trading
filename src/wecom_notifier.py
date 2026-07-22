"""
企业微信推送模块 — v3.0 7 级信号格式

按信号强度分组展示，每只股票附带仓位建议。
⚠️ 仅供学习和研究目的，不构成任何投资建议
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import requests

from src.normalizer import L7_EMOJI, L7_SIGNAL_NAMES, L7_POSITION, SignalNormalizer
from src.position import UnifiedPosition, PositionConstraintEngine, pct_to_label

logger = logging.getLogger(__name__)

# 企业微信 API 速率限制：每个 webhook 最多 20次/分钟，留10%余量
WECOM_RATE_LIMIT = 18  # max sends per 60s window
WECOM_RATE_WINDOW = 60  # seconds


class WeComNotifier:
    """企业微信群机器人消息推送 — v3.0"""

    def __init__(self, webhook_url: str, enabled: bool = True):
        self.webhook_url = webhook_url
        self.enabled = enabled
        self.session = requests.Session()
        self._send_times: deque[float] = deque()  # 滑动窗口时间戳

    def close(self):
        """关闭 HTTP Session，释放连接池"""
        self.session.close()

    def _acquire_rate_limit(self):
        """滑动窗口速率限制：最多 WECOM_RATE_LIMIT 次/60s"""
        now = time.monotonic()
        window_start = now - WECOM_RATE_WINDOW
        while self._send_times and self._send_times[0] < window_start:
            self._send_times.popleft()
        if len(self._send_times) >= WECOM_RATE_LIMIT:
            sleep_time = self._send_times[0] + WECOM_RATE_WINDOW - now
            if sleep_time > 0:
                logger.warning(
                    "企业微信达到速率限制(%d/%ds)，等待%.1fs",
                    WECOM_RATE_LIMIT, WECOM_RATE_WINDOW, sleep_time,
                )
                time.sleep(sleep_time)
        self._send_times.append(time.monotonic())

    def send_markdown(self, content: str) -> Optional[Dict[str, Any]]:
        """发送 Markdown 格式消息（3次指数退避重试 + 速率限制）"""
        if not self.enabled or not self.webhook_url:
            logger.warning("企业微信推送未配置或已禁用，跳过推送")
            return None

        self._acquire_rate_limit()

        data = {
            "msgtype": "markdown",
            "markdown": {"content": content},
        }
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")

        for attempt in range(3):
            try:
                resp = self.session.post(
                    self.webhook_url,
                    headers={"Content-Type": "application/json"},
                    data=payload,
                    timeout=10,
                )
                result = resp.json()
                if result.get("errcode") != 0:
                    logger.error("企业微信推送失败: %s", result)
                return result
            except requests.exceptions.Timeout:
                if attempt < 2:
                    delay = (2 ** attempt) * 1.5
                    logger.warning("企业微信推送超时，%.0fs后重试(%d/3)", delay, attempt + 1)
                    time.sleep(delay)
                else:
                    logger.error("企业微信推送超时（10s），3次重试均失败")
                    return None
            except Exception as e:
                if attempt < 2:
                    delay = (2 ** attempt) * 1.5
                    logger.warning("企业微信推送异常: %s，%.0fs后重试(%d/3)", e, delay, attempt + 1)
                    time.sleep(delay)
                else:
                    logger.error("企业微信推送异常: %s，3次重试均失败", e)
                    return None

    @staticmethod
    def _tz_cn_now() -> datetime:
        return datetime.now(timezone(timedelta(hours=8)))

    @staticmethod
    def _summary_position(valid: List[Dict[str, Any]]) -> str:
        """汇总建议总仓位（% of 总资金）。

        只算非中性仓位，避免大量0%拉低均值。
        """
        if not valid:
            return "总仓位0% (空仓)"
        total_pct = 0.0
        active_count = 0
        for r in valid:
            # 优先用 unified_position
            up = r.get('unified_position')
            if up and isinstance(up, dict) and up.get('pct', 0) > 0:
                pct = up['pct']
            else:
                sig = r.get("signal", "neutral")
                pct = SignalNormalizer.l7_target_pct(sig)
            if pct > 0.5:
                total_pct += pct
                active_count += 1
        label = "重仓" if total_pct > 70 else ("中仓" if total_pct > 30 else "轻仓")
        return f"总仓位{total_pct:.0f}% ({label})"

    @staticmethod
    def _stock_line(r) -> str:
        """单只股票一行（微信自动换行）"""
        emoji = L7_EMOJI.get(r.get("signal", "neutral"), "⚪")
        name = r.get('stock_name', '')
        price = r.get('price', 0)
        pct = r.get('pct_chg', 0)
        ls = r.get("lynx_score", 0)
        ms = r.get("mindlynx_score", 0)
        ts = r.get("tradingagent_score", 0)

        price_str = f"¥{price:.2f}" if price else "-"
        chg_str = f"{pct:+.2f}%" if pct is not None else "-"
        sys_str = f"ly{ls:+.2f} ml{ms:+.2f} at{ts:+.2f}"

        return (
            f"**{name}**{price_str} {chg_str}"
            f"｜{sys_str}"
        )

    def format_daily_summary(self, results: List[Dict[str, Any]]) -> str:
        """
        格式化每日融合结果摘要 — 一行式，适合微信阅读。
        """
        valid = [r for r in results if r.get("valid", False)]
        invalid = [r for r in results if not r.get("valid", False)]

        disagree = [r for r in valid if r.get("has_disagreement")]
        degraded = [r for r in valid if r.get("is_degraded")]
        stale_ta = [r for r in valid if r.get("ta_is_stale")]

        ts = self._tz_cn_now()
        lines = [
            f"🛟 {ts.strftime('%H:%M')} 融合决策",
            "",
        ]

        # ── 3档快速总览（好/中/差）──
        bull_count = sum(1 for r in valid if r.get("signal") in ("strong_bullish", "bullish", "cautious_bullish"))
        neut_count = sum(1 for r in valid if r.get("signal") == "neutral")
        bear_count = sum(1 for r in valid if r.get("signal") in ("cautious_bearish", "bearish", "strong_bearish"))
        overview = []
        if bull_count:
            overview.append(f"看好{bull_count}")
        if neut_count:
            overview.append(f"中立{neut_count}")
        if bear_count:
            overview.append(f"看空{bear_count}")
        if overview:
            lines.append("📊" + "｜".join(overview))
            lines.append("")

        # ── 按信号强度分组（7档，含分歧股票）──
        signal_groups = [
            ("🚀强烈看多", "strong_bullish"),
            ("📈看多", "bullish"),
            ("📈谨慎看多", "cautious_bullish"),
            ("🗂中性/持有", "neutral"),
            ("📉谨慎看空", "cautious_bearish"),
            ("📉看空", "bearish"),
            ("🚨强烈看空", "strong_bearish"),
        ]

        grouped = {sig: {"consensus": [], "disagreement": []} for _, sig in signal_groups}
        for r in valid:
            sig = r.get("signal", "neutral")
            bucket = "disagreement" if r.get("has_disagreement") else "consensus"
            if sig in grouped:
                grouped[sig][bucket].append(r)

        for title, sig_key in signal_groups:
            bucket = grouped[sig_key]
            # 共识股票在前，分歧股票在后
            bucket["consensus"].sort(key=lambda x: x.get("fusion_score", 0), reverse=True)
            bucket["disagreement"].sort(key=lambda x: x.get("fusion_score", 0), reverse=True)
            all_stocks = bucket["consensus"] + bucket["disagreement"]
            if not all_stocks:
                continue
            lines.append(f"{title} ({len(all_stocks)})")
            for r in all_stocks:
                lines.append(self._stock_line(r))
            lines.append("")

        # ── 无信号 ──
        if invalid:
            lines.append("⚠️ 无信号")
            for r in invalid:
                lines.append(f"- {r['stock_code']}: {r.get('message', '')}")
            lines.append("")

        # ── 子系统可用性摘要（健康监控）──
        ly_ok = sum(1 for r in valid if r.get("lynx_valid", False))
        ml_ok = sum(1 for r in valid if r.get("mindlynx_valid", False))
        at_ok = sum(1 for r in valid if r.get("tradingagent_valid", False))
        lines.append(f"📡ly{ly_ok}/{len(valid)}｜ml{ml_ok}/{len(valid)}｜at{at_ok}/{len(valid)}")
        return "\n".join(lines)

    def push_daily_decision(self, results: List[Dict[str, Any]], date: Optional[str] = None,
                            extra_sections: Optional[List[str]] = None):
        """推送每日融合决策结果"""
        if not self.enabled:
            return

        if date is None:
            date = self._tz_cn_now().strftime("%Y-%m-%d")

        summary = self.format_daily_summary(results)

        # 从 bt_results.db 获取当日三批次融合趋势
        try:
            import sqlite3
            db = sqlite3.connect("data/backtest/bt_results.db")
            rows = db.execute(
                "SELECT eval_batch, AVG(fusion_score) FROM bt_predictions "
                "WHERE date=? AND fusion_correct IS NOT NULL GROUP BY eval_batch ORDER BY eval_batch",
                (date,),
            ).fetchall()
            db.close()
            if rows:
                parts = []
                for batch, avg in rows:
                    avg = round(avg, 2)
                    icon = "📈" if avg > 0.3 else ("📉" if avg < -0.3 else "➡️")
                    parts.append(f"{batch}={icon}{avg:+.2f}")
                summary += "\n🔄 今日融合: " + " | ".join(parts)
        except Exception:
            pass

        result = self.send_markdown(summary)

        if result and result.get("errcode") == 0:
            logger.info("✅ 企业微信推送成功 (%d 只股票)", len(results))

        # 可选附加信息（龙虎榜、雪球情绪等）
        if extra_sections:
            for idx, section in enumerate(extra_sections):
                self.send_markdown(section)
                if idx < len(extra_sections) - 1:
                    time.sleep(0.5)
