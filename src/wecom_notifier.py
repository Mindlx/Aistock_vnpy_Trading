"""
企业微信推送模块

发送融合决策结果到企业微信群机器人。

⚠️ 仅供学习和研究目的，不构成任何投资建议
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import requests


class WeComNotifier:
    """企业微信群机器人消息推送"""

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
        """获取北京时间"""
        return datetime.now(timezone(timedelta(hours=8)))

    @staticmethod
    def _score_emoji(score: float) -> str:
        """融合分 → 图标"""
        if score > 0.50: return "🟢"
        if score > 0.20: return "🟡"
        if score > -0.10: return "⚪"
        if score > -0.50: return "🟠"
        return "🔴"

    @staticmethod
    def _format_per_system(r) -> str:
        """单只股票的三系统得分摘要"""
        parts = []
        if r.get("lynx_valid"):
            lynx_em = "🟢" if r.get("lynx_score", 0) > 0 else "🔴"
            parts.append(f"lynx{lynx_em}{r['lynx_score']:.2f}")
        parts.append(f"mind{r['mindlynx_score']:.2f}")
        parts.append(f"ta{r['tradingagent_score']:.2f}")
        stale = " ⏳过期" if r.get("ta_is_stale") else ""
        return " ".join(parts) + stale

    def format_daily_summary(self, results: List[Dict[str, Any]], date: str) -> str:
        """
        格式化每日融合结果摘要（Markdown 格式）

        按分歧/共识优先展示，突出融合系统的核心价值。
        """
        valid_results = [r for r in results if r.get("valid", False)]
        invalid = [r for r in results if not r.get("valid", False)]

        total = len(valid_results)
        disagreements = [r for r in valid_results if r.get("has_disagreement")]
        degraded = [r for r in valid_results if r.get("is_degraded")]
        stale_ta = [r for r in valid_results if r.get("ta_is_stale")]

        # 按融合分降序排列
        valid_results.sort(key=lambda r: r.get("fusion_score", 0), reverse=True)

        # 标题
        lines = [
            f"## 🔗 三系统融合信号 | {date}",
            f"> 有效{total}只 | "
            f"分歧{len(disagreements)} | "
            f"降级{len(degraded)} | "
            f"过期TA{len(stale_ta)}",
            "",
        ]

        # ── 第一部分：系统分歧（最高价值信号）──
        if disagreements:
            lines.append("### ⚡ 系统分歧 — 需重点关注")
            for r in disagreements:
                emoji = self._score_emoji(r["fusion_score"])
                cap = " ⚡仓位受限" if r.get("disagreement_capped") else ""
                lines.append(
                    f"{emoji} **{r.get('stock_name', '')}({r['stock_code']})** "
                    f"融合{r['fusion_score']:.2f} {self._format_per_system(r)}"
                    f"{cap}"
                )
            lines.append("")

        # ── 第二部分：三系统共识（高置信度信号）──
        consensuses = [r for r in valid_results if not r.get("has_disagreement")]

        # 只看多共识（所有系统同向且偏多）
        bullish_consensus = [
            r for r in consensuses
            if r.get("fusion_score", 0) > 0.20
        ]
        if bullish_consensus:
            lines.append("### ✅ 三系统共识 — 高置信度")
            for r in bullish_consensus:
                lines.append(
                    f"🟢 **{r.get('stock_name', '')}({r['stock_code']})** "
                    f"融合{r['fusion_score']:.2f} | {r['signal_name']} | {r['position_advice']}"
                )
                lines.append(f"   └ {self._format_per_system(r)}")
            lines.append("")

        # ── 第三部分：其余股票简表 ──
        remaining = [r for r in consensuses if r not in bullish_consensus]
        if remaining:
            lines.append("### 📋 其余信号")
            for r in remaining:
                emoji = self._score_emoji(r["fusion_score"])
                stale_mark = " ⏳" if r.get("ta_is_stale") else ""
                deg_mark = " ⚠降级" if r.get("is_degraded") else ""
                lines.append(
                    f"{emoji} **{r.get('stock_name', '')}({r['stock_code']})** "
                    f"融合{r['fusion_score']:.2f} {self._format_per_system(r)}"
                    f"{stale_mark}{deg_mark}"
                )
            lines.append("")

        # ── 第四部分：无信号股票 ──
        if invalid:
            lines.append("### ⚠️ 无信号")
            for r in invalid:
                lines.append(f"- {r['stock_code']}: {r.get('message', '')}")
            lines.append("")

        # ── 底部建议 ──
        lines.append("---")
        advice_parts = []
        if disagreements:
            advice_parts.append("分歧标的注意仓位控制")
        if stale_ta:
            advice_parts.append("TradingAgent 数据为昨日结果")
        if bullish_consensus:
            advice_parts.append(f"重点关注共识标的 {', '.join(r['stock_code'] for r in bullish_consensus[:3])}")
        if advice_parts:
            lines.append(f"**建议**: {' | '.join(advice_parts)}")
        lines.append(
            "> ⚠️ 本报告仅供学习参考，不构成投资建议。市场有风险，投资需谨慎。"
        )

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
