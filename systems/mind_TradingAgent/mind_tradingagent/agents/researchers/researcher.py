"""Single researcher — balanced bull/bear analysis for A-share trading.

Replaces the separate Bull Researcher + Bear Researcher + Research Manager pipeline.
Prompts a single LLM to weigh both sides and output a clear stance.
"""
from mind_tradingagent.agents.utils.agent_utils import get_language_instruction


def create_researcher(llm):
    def researcher_node(state) -> dict:
        market_report = state.get("market_report", "")
        news_report = state.get("news_report", "")
        fundamentals_report = state.get("fundamentals_report", "")
        sentiment_report = state.get("sentiment_report", "")
        asset_type = state.get("asset_type", "stock")
        target_label = "stock" if asset_type == "stock" else "asset"

        # 提取外部信号注入（来自 mind_agent_wrapper 的 LY/ML 数据注入）
        external_signals = state.get("external_signals", "")
        if not external_signals:
            for msg in state.get("messages", []):
                if getattr(msg, "type", None) == "system":
                    content = getattr(msg, "content", "") or ""
                    if "[系统注入]" in content:
                        external_signals = content
                        break

        prompt = f"""You are a research analyst covering a {target_label} on China's A-share market. \
Your task: weigh the bull case and bear case side by side, then commit to a clear stance.

**Analyst reports available:**
- Market / Technical: {market_report}
- News & Policy: {news_report}
- Fundamentals: {fundamentals_report}
- Social Sentiment: {sentiment_report or "(not available)"}

**External reference signals (from quantitative models):**
{external_signals or "No external reference signals available."}

**A-share analysis priorities (when evidence exists):**
1. 政策方向 (Policy direction) — is the sector government-favored?
2. 技术面 (Technicals) — trend, support/resistance, volume
3. 资金流向 (Capital flow) — north-bound, main force
4. 基本面 (Fundamentals) — PE/PB/ROE, earnings trajectory
5. 风险信号 (Risk flags) — ST/delisting, regulatory, concentration

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction in the bull thesis
- **Overweight**: Constructive view, gradually increase
- **Hold**: Balanced, maintain position
- **Underweight**: Cautious, trim exposure
- **Sell**: Strong conviction in the bear thesis

Output a clear rating with supporting reasoning for both sides.
""" + get_language_instruction()

        messages = [{"role": "user", "content": prompt}]
        response = llm.invoke(messages)

        return {
            "investment_debate_state": {
                "judge_decision": response.content if hasattr(response, "content") else str(response),
                "history": "",
                "bear_history": "",
                "bull_history": "",
                "current_response": response.content if hasattr(response, "content") else str(response),
                "count": 1,
            },
            "investment_plan": response.content if hasattr(response, "content") else str(response),
        }

    return researcher_node
