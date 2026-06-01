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

from src.normalizer import L7_EMOJI, GROUP_ICONS, L7_SIGNAL_NAMES


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

        extras = [x for x in [vr_str, sup_str] if x]
        extra_str = f"| {' '.join(extras)}" if extras else ""

        return (
            f"{emoji} **{name}({code})** {price_str} {chg_str}"
            f" | {sys_str}"
            f" | {sig} | 仓位{pos}"
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
            f"## 📊融合决策"
            f"|{now.strftime('%m-%d %H:%M')}"
            f"|有效{len(valid)}"
            f"|TA{len(results)}",
            "",
        ]

        # ── 分歧优先 ──
        if disagree:
            lines.append("**⚡ 系统分歧 — 注意仓位控制**")
            for r in disagree:
                lines.append(self._stock_line(r))
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

        # ── 底部 ──
        if stale_ta:
            lines.append("⏳ TA为昨日结果（定时器16:00运行）")
        if degraded:
            lines.append("⚠ 部分数据缺失，结果仅供参考")

        return "\n".join(lines)

    def push_daily_decision(self, results: List[Dict[str, Any]], date: Optional[str] = None):
        """推送每日融合决策结果"""
        if not self.enabled:
            return

        if date is None:
            date = self._tz_cn_now().strftime("%Y-%m-%d")

        summary = self.format_daily_summary(results, date)
        result = self.send_markdown(summary)

        if result and result.get("errcode") == 0:
            print(f"✅ 企业微信推送成功 ({len(results)} 只股票)")
