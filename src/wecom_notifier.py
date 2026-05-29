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

    def format_daily_summary(self, results: List[Dict[str, Any]], date: str) -> str:
        """
        格式化每日融合结果摘要（Markdown 格式）

        按信号强度分组展示，含融合得分和仓位建议。
        """
        valid_results = [r for r in results if r.get("valid", False)]

        # 分组
        strong_bullish = [r for r in valid_results if r.get("signal") == "strong_bullish"]
        weak_bullish = [r for r in valid_results if r.get("signal") == "weak_bullish"]
        neutral = [r for r in valid_results if r.get("signal") == "neutral"]
        weak_bearish = [r for r in valid_results if r.get("signal") == "weak_bearish"]
        strong_bearish = [r for r in valid_results if r.get("signal") == "strong_bearish"]
        invalid = [r for r in results if not r.get("valid", False)]

        # 统计信息
        total = len(valid_results)
        degraded_count = sum(1 for r in valid_results if r.get("is_degraded", False))
        disagreement_count = sum(1 for r in valid_results if r.get("has_disagreement", False))

        # 构建消息
        lines = [
            f"## 📊 三系统融合决策报告",
            f"**{date}**",
            f"",
            f"> 统计: {total}只股票 | "
            f"🟢强烈看多{len(strong_bullish)} 🟡弱看多{len(weak_bullish)} "
            f"⚪中性{len(neutral)} 🔴弱看空{len(weak_bearish)} 🔴强烈看空{len(strong_bearish)}",
        ]

        # 降级和分歧提示
        degradation_parts = []
        if degraded_count > 0:
            degradation_parts.append(f"⚠ {degraded_count}只系统降级")
        if disagreement_count > 0:
            degradation_parts.append(f"⚠ {disagreement_count}只系统分歧")
        if degradation_parts:
            lines.append(f"> {' | '.join(degradation_parts)}")
            lines.append("")

        # 强烈看多
        lines.append("### 🟢 强烈看多")
        if strong_bullish:
            for r in strong_bullish:
                extra = ""
                if r.get("is_degraded"):
                    extra += " ⚠降级"
                if r.get("disagreement_capped"):
                    extra += " ⚡分歧"
                lines.append(
                    f"- **{r.get('stock_name', '')}({r['stock_code']})** "
                    f"融合分 {r['fusion_score']:.2f} | 仓位 {r['position_advice']}{extra}"
                )
        else:
            lines.append("- 无")

        # 弱看多
        lines.append("")
        lines.append("### 🟢 弱看多")
        if weak_bullish:
            for r in weak_bullish:
                extra = ""
                if r.get("is_degraded"):
                    extra += " ⚠降级"
                lines.append(
                    f"- **{r.get('stock_name', '')}({r['stock_code']})** "
                    f"融合分 {r['fusion_score']:.2f} | 仓位 {r['position_advice']}{extra}"
                )
        else:
            lines.append("- 无")

        # 中性/观望
        lines.append("")
        lines.append("### ⚪ 中性/观望")
        if neutral:
            for r in neutral:
                lines.append(
                    f"- **{r.get('stock_name', '')}({r['stock_code']})** "
                    f"融合分 {r['fusion_score']:.2f}"
                )
        else:
            lines.append("- 无")

        # 看空汇总
        all_bearish = weak_bearish + strong_bearish
        lines.append("")
        lines.append("### 🔴 弱看空/强烈看空")
        if all_bearish:
            for r in all_bearish:
                lines.append(
                    f"- **{r.get('stock_name', '')}({r['stock_code']})** "
                    f"{r['signal_name']} 融合分 {r['fusion_score']:.2f} | {r['position_advice']}"
                )
        else:
            lines.append("- 无")

        # 失效股票
        if invalid:
            lines.append("")
            lines.append("### ⚠️ 无信号")
            for r in invalid:
                lines.append(f"- {r['stock_code']}: {r.get('message', '')}")

        # 底部建议
        lines.append("")
        lines.append("---")
        lines.append(
            "**操作建议**: 优先关注强烈看多标的，弱看多仅观察或极低仓位试错。"
        )
        if degraded_count > 0 or disagreement_count > 0:
            lines.append(
                "**提示**: 部分系统信号缺失或存在分歧，请降低仓位、注意风险。"
            )
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
