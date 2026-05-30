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


# L7 信号 → 图标/颜色映射（7 级）
L7_EMOJI = {
    "strong_bullish": "🟢",
    "bullish": "🟢",
    "cautious_bullish": "🟡",
    "neutral": "⚪",
    "cautious_bearish": "🟠",
    "bearish": "🔴",
    "strong_bearish": "🔴",
}

L7_LABEL_CN = {
    "strong_bullish": "强烈看多",
    "bullish": "看多",
    "cautious_bullish": "谨慎看多",
    "neutral": "中性/持有",
    "cautious_bearish": "谨慎看空",
    "bearish": "看空",
    "strong_bearish": "强烈看空",
}


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
    def _system_line(r) -> str:
        """三系统得分摘要（紧凑格式）"""
        parts = []
        ls = r.get("lynx_score", 0)
        ms = r.get("mindlynx_score", 0)
        ts = r.get("tradingagent_score", 0)
        parts.append(f"ly{'🟢' if ls > 0 else '🔴'}{ls:+.2f}")
        parts.append(f"ml{'🟢' if ms > 0 else '🔴'}{ms:+.2f}")
        parts.append(f"at{'🟢' if ts > 0 else '🔴'}{ts:+.2f}")
        stale = " ⏳" if r.get("ta_is_stale") else ""
        return " ".join(parts) + stale

    def format_daily_summary(self, results: List[Dict[str, Any]], date: str) -> str:
        """
        格式化每日融合结果摘要。

        按信号强度分组展示，清晰标注操作建议。
        """
        valid = [r for r in results if r.get("valid", False)]
        invalid = [r for r in results if not r.get("valid", False)]

        # 统计
        disagree = [r for r in valid if r.get("has_disagreement")]
        degraded = [r for r in valid if r.get("is_degraded")]
        stale_ta = [r for r in valid if r.get("ta_is_stale")]

        lines = [
            f"## 📊 三系统融合决策 | {date}",
            f"> 有效{len(valid)}只 | "
            f"分歧{len(disagree)} | "
            f"数据降级{len(degraded)}{' | TA⏳' + str(len(stale_ta)) if stale_ta else ''}",
            "",
        ]

        # ── 1. 系统分歧（最高优先级） ──
        if disagree:
            lines.append("### ⚡ 系统分歧 — 注意仓位控制")
            for r in disagree:
                sig = r.get("signal", "neutral")
                emoji = L7_EMOJI.get(sig, "⚪")
                pos = r.get("position_advice", "0成")
                cap = " ⚠分歧仓位上限1成" if r.get("disagreement_capped") else ""
                lines.append(
                    f"{emoji} **{r.get('stock_name', '')}({r['stock_code']})** "
                    f"→ {r.get('signal_name', '中性')} | 仓位:{pos}{cap}"
                )
                lines.append(f"   └ {self._system_line(r)}")
            lines.append("")

        # ── 2. 按信号强度分组 ──
        # 无分歧的股票
        consensuses = [r for r in valid if not r.get("has_disagreement")]

        # 定义信号分组顺序（从强到弱）
        signal_groups = [
            ("🟢 **强烈看多** — 可重点参与，仓位2-3成", "strong_bullish"),
            ("🟢 **看多** — 可参与，仓位1-2成", "bullish"),
            ("🟡 **谨慎看多** — 轻仓试探，仓位0.5-1成", "cautious_bullish"),
            ("⚪ **中性/持有** — 观望或维持现有仓位", "neutral"),
            ("🟠 **谨慎看空** — 减仓观察，仓位减至0.5成以内", "cautious_bearish"),
            ("🔴 **看空** — 大幅减仓", "bearish"),
            ("🔴 **强烈看空** — 清仓离场", "strong_bearish"),
        ]

        grouped = {sig: [] for _, sig in signal_groups}
        for r in consensuses:
            sig = r.get("signal", "neutral")
            if sig in grouped:
                grouped[sig].append(r)
            else:
                grouped["neutral"].append(r)

        has_any_group = False
        for title, sig_key in signal_groups:
            stocks = grouped[sig_key]
            if not stocks:
                continue
            has_any_group = True
            lines.append(f"### {title}")
            for r in stocks:
                deg = " ⚠降级" if r.get("is_degraded") else ""
                lines.append(
                    f"- **{r.get('stock_name', '')}({r['stock_code']})** "
                    f"融合{r.get('fusion_score', 0):+.2f}{deg}"
                )
                lines.append(f"  {self._system_line(r)}")
            lines.append("")

        if not has_any_group:
            lines.append("_无有效信号_")
            lines.append("")

        # ── 3. 无信号股票 ──
        if invalid:
            lines.append("### ⚠️ 无信号")
            for r in invalid:
                lines.append(f"- {r['stock_code']}: {r.get('message', '')}")
            lines.append("")

        # ── 底部说明 ──
        lines.append("---")
        notes = []
        if stale_ta:
            notes.append("⏳ TA数据为昨日结果（TA定时器16:00运行）")
        if degraded:
            notes.append("⚠ 部分系统数据缺失，结果仅供参考")
        if notes:
            lines.extend(notes)
            lines.append("")
        lines.append("> ⚠️ 本报告仅供学习参考，不构成投资建议。市场有风险，投资需谨慎。")

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
