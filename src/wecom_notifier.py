"""
企业微信推送模块 — v3.0 7 级信号格式

按信号强度分组展示，每只股票附带仓位建议。
⚠️ 仅供学习和研究目的，不构成任何投资建议
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import requests

from src.normalizer import L7_EMOJI, GROUP_ICONS, L7_SIGNAL_NAMES, L7_POSITION


# L7 信号 → 图标/颜色映射（7 级，从 normalizer 导入）
# L7 信号 → 图标（国内股市：🔴红涨看多 🟠橙 🟡金 → 🟢绿跌看空）


class WeComNotifier:
    """企业微信群机器人消息推送 — v3.0"""

    def __init__(self, webhook_url: str, enabled: bool = True):
        self.webhook_url = webhook_url
        self.enabled = enabled
        self.session = requests.Session()

    def send_markdown(self, content: str) -> Optional[Dict[str, Any]]:
        """发送 Markdown 格式消息"""
        if not self.enabled or not self.webhook_url:
            print("⚠️  企业微信推送未配置或已禁用，跳过推送")
            return None

        data = {
            "msgtype": "markdown",
            "markdown": {"content": content},
        }

        try:
            resp = self.session.post(
                self.webhook_url,
                headers={"Content-Type": "application/json"},
                data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                timeout=10,
            )
            result = resp.json()
            if result.get("errcode") != 0:
                print(f"企业微信推送失败: {result}")
            return result
        except requests.exceptions.Timeout:
            print("企业微信推送超时（10s）")
            return None
        except Exception as e:
            print(f"企业微信推送异常: {e}")
            return None

    @staticmethod
    def _tz_cn_now() -> datetime:
        return datetime.now(timezone(timedelta(hours=8)))

    @staticmethod
    def _summary_position(valid: List[Dict[str, Any]]) -> str:
        """根据有效结果汇总建议总仓位"""
        if not valid:
            return "0成"
        # 按fusion_score对应的L7_POSITION取中间值，再取均值
        total_pct = 0.0
        count = 0
        for r in valid:
            sig = r.get("signal", "neutral")
            pos_str = L7_POSITION.get(sig, "0成")
            # 解析中文仓位描述：取数字范围的中值
            if "清仓" in pos_str:
                pct = 0.0
            elif "减仓" in pos_str:
                pct = 0.05
            elif "大幅减仓" in pos_str:
                pct = 0.03
            elif "0成" in pos_str:
                pct = 0.0
            elif "-" in pos_str:
                parts = pos_str.replace("成", "").split("-")
                low, high = float(parts[0]), float(parts[1])
                pct = (low + high) / 2 * 0.1
            else:
                pct = 0.0
            total_pct += pct
            count += 1
        avg_pct = total_pct / count if count > 0 else 0.0
        avg_cheng = avg_pct * 10
        if avg_cheng >= 2.0:
            return f"{avg_cheng:.0f}成"
        elif avg_cheng >= 0.5:
            return f"{avg_cheng:.1f}成"
        else:
            return "0成"

    @staticmethod
    def _stock_line(r) -> str:
        """单只股票一行（微信自动换行）"""
        emoji = L7_EMOJI.get(r.get("signal", "neutral"), "⚪")
        name = r.get('stock_name', '')
        code = r['stock_code']
        price = r.get('price', 0)
        pct = r.get('pct_chg', 0)
        vr = r.get('volume_ratio', 0)
        ma5 = r.get('ma5', 0)
        ma10 = r.get('ma10', 0)
        ma20 = r.get('ma20', 0)
        ls = r.get("lynx_score", 0)
        ms = r.get("mindlynx_score", 0)
        ts = r.get("tradingagent_score", 0)
        sig = r.get('signal_name', '中性')
        pos = r.get('position_advice', '0成')

        # 股价和涨跌幅
        price_str = f"¥{price:.2f}" if price else "-"
        chg_str = f"{pct:+.2f}%" if pct else "-"

        # 三系统得分
        sys_str = f"ly{ls:+.2f} ml{ms:+.2f} at{ts:+.2f}"

        # 量比
        vr_str = f"量比{vr:.2f}" if vr else ""

        # 支撑/压力
        sup_str = ""
        if ma10 and ma20:
            support = min(ma10, ma20)
            resist = max(ma10, ma20)
            sup_str = f"支撑{support:.2f} 压力{resist:.2f}"

        # 止损价（仅在看多/持有信号时展示）
        stop_str = ""
        stop_loss = r.get("mindlynx_stop_loss")
        if stop_loss and price and float(stop_loss) > 0:
            stop_price = float(stop_loss)
            dist = (price - stop_price) / price * 100
            if dist > 0:
                urgency = "🛑" if dist < 3 else ("⚠️" if dist < 7 else "")
                stop_str = f"{urgency}止{stop_price:.2f}({dist:.1f}%)"

        extras = [x for x in [vr_str, sup_str, stop_str] if x]
        extra_str = f"｜{' '.join(extras)}" if extras else ""

        return (
            f"{emoji} **{name}({code})** {price_str} {chg_str}"
            f"｜{sys_str}"
            f"｜{sig}｜仓位{pos}"
            f"{extra_str}"
        )

    def format_daily_summary(self, results: List[Dict[str, Any]], date: str) -> str:
        """
        格式化每日融合结果摘要 — 一行式，适合微信阅读。
        """
        valid = [r for r in results if r.get("valid", False)]
        invalid = [r for r in results if not r.get("valid", False)]

        disagree = [r for r in valid if r.get("has_disagreement")]
        degraded = [r for r in valid if r.get("is_degraded")]
        stale_ta = [r for r in valid if r.get("ta_is_stale")]

        now = self._tz_cn_now()
        lines = [
            f"## 🛟 {now.strftime('%H:%M')} 融合决策"
            f"｜有效{len(valid)}"
            f"｜TA{len(results)}",
            "",
        ]

        # ── 一句话总结 ──
        total_score = sum(r.get("fusion_score", 0) for r in valid)
        avg_score = total_score / len(valid) if valid else 0.0
        bullish_count = sum(1 for r in valid if r.get("fusion_score", 0) > 0.5)
        bearish_count = sum(1 for r in valid if r.get("fusion_score", 0) < -0.5)
        if avg_score > 0.3:
            stance = "偏多"
        elif avg_score < -0.3:
            stance = "偏空"
        else:
            stance = "中性/分化"
        lines.append(
            f"📌 **今日立场**: {stance}(融合{avg_score:+.2f})"
            f"｜看多{bullish_count}只看空{bearish_count}只"
            f"｜建议总仓位{self._summary_position(valid)}"
        )
        lines.append("")

        # ── 风险提醒（聚合止损+分歧+降级）──
        risk_lines = []

        stop_alerts = []
        for r in valid:
            stop_loss = r.get("mindlynx_stop_loss")
            price = r.get("price", 0)
            if stop_loss and price and float(stop_loss) > 0:
                dist = (price - float(stop_loss)) / price * 100
                if 0 < dist < 7:
                    stop_alerts.append((dist, r))
        if stop_alerts:
            stop_alerts.sort()
            items = []
            for dist, r in stop_alerts[:3]:
                name = r.get('stock_name', '')
                code = r['stock_code']
                sl = float(r.get("mindlynx_stop_loss", 0))
                items.append(f"{name}({code})距止损仅{dist:.1f}%")
            risk_lines.append(f"🛑 止损关注: {'; '.join(items)}")

        if disagree:
            items = [f"{r['stock_name']}({r['stock_code']})" for r in disagree[:3]]
            risk_lines.append(f"⚡ 系统分歧: {'; '.join(items)}")

        degraded = [r for r in valid if r.get("is_degraded")]
        stale_ta = [r for r in valid if r.get("ta_is_stale")]
        if degraded:
            risk_lines.append(f"⚠️ 子系统降级: {len(degraded)}只")
        if stale_ta:
            risk_lines.append(f"⚠️ TA数据过期: {len(stale_ta)}只")

        if risk_lines:
            lines.append(f"**⚠ 风险提醒**")
            for rl in risk_lines:
                lines.append(f"- {rl}")
            lines.append("")

        # ── 按信号强度分组 ──
        consensuses = [r for r in valid if not r.get("has_disagreement")]

        signal_groups = [
            ("🚀 强烈看多", "strong_bullish"),
            ("📈 看多", "bullish"),
            ("📈 谨慎看多", "cautious_bullish"),
            ("🗂 中性/持有", "neutral"),
            ("📉 谨慎看空", "cautious_bearish"),
            ("📉 看空", "bearish"),
            ("🚨 强烈看空", "strong_bearish"),
        ]

        grouped = {sig: [] for _, sig in signal_groups}
        for r in consensuses:
            sig = r.get("signal", "neutral")
            grouped.get(sig, []).append(r)

        for title, sig_key in signal_groups:
            stocks = grouped[sig_key]
            if not stocks:
                continue
            lines.append(f"**{title}**")
            for r in stocks:
                lines.append(self._stock_line(r))
            lines.append("")

        # ── 无信号 ──
        if invalid:
            lines.append("**⚠️ 无信号**")
            for r in invalid:
                lines.append(f"- {r['stock_code']}: {r.get('message', '')}")
            lines.append("")

        # ── 子系统可用性摘要（健康监控）──
        ly_ok = sum(1 for r in valid if r.get("lynx_valid", False))
        ml_ok = sum(1 for r in valid if r.get("mindlynx_valid", False))
        at_ok = sum(1 for r in valid if r.get("tradingagent_valid", False))
        health = f"ly{ly_ok}/{len(valid)} ml{ml_ok}/{len(valid)} at{at_ok}/{len(valid)}"
        lines.append(f"⚠ {health}")

        return "\n".join(lines)

    def push_daily_decision(self, results: List[Dict[str, Any]], date: Optional[str] = None,
                            extra_sections: Optional[List[str]] = None):
        """推送每日融合决策结果"""
        if not self.enabled:
            return

        if date is None:
            date = self._tz_cn_now().strftime("%Y-%m-%d")

        summary = self.format_daily_summary(results, date)
        result = self.send_markdown(summary)

        if result and result.get("errcode") == 0:
            print(f"✅ 企业微信推送成功 ({len(results)} 只股票)")

        # 可选附加信息（龙虎榜、雪球情绪等）
        if extra_sections:
            for section in extra_sections:
                self.send_markdown(section)
                if extra_sections.index(section) < len(extra_sections) - 1:
                    import time
                    time.sleep(0.5)
