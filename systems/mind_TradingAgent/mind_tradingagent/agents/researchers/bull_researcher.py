from mind_tradingagent.agents.utils.agent_utils import get_language_instruction


def create_bull_researcher(llm):
    def bull_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bull_history = investment_debate_state.get("bull_history", "")

        current_response = investment_debate_state.get("current_response", "")
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        asset_type = state.get("asset_type", "stock")
        target_label = "stock" if asset_type == "stock" else "asset"
        fundamentals_label = (
            "Company fundamentals report"
            if asset_type == "stock"
            else "Asset fundamentals report (may be unavailable for crypto)"
        )

        prompt = f"""You are a Bull Analyst advocating for investing in the {target_label} on China's A-share market. Build a strong, evidence-based case emphasizing growth potential and positive indicators.

**A-share Bull Thesis Dimensions — prioritize when evidence exists:**

- **政策顺风 (Policy Tailwind)**: Is the company in a government-favored industry (半导体, 新能源, AI, 高端制造)? Are there supportive policies or subsidies?

- **北向资金支持 (Foreign Capital Support)**: Is north-bound capital flowing in? Persistent inflows signal international confidence.

- **主力资金跟随 (Major Capital Following)**: Is 主力资金 net buying? Research from PBoC/Tsinghua shows large orders have predictive power in A-shares.

- **零售情绪动能 (Retail Sentiment Momentum)**: With ~80% retail volume, strong Xueqiu/Guba bullish sentiment can drive sustained momentum — not just noise.

- **Growth Potential & Competitive Advantages**: Market opportunities, revenue projections, 国产替代 potential, brand strength.

- **Bear Counterpoints**: Critically address concerns about 商誉, 质押, ST risk, or policy headwinds.

Resources available:
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
{fundamentals_label}: {fundamentals_report}
Conversation history of the debate: {history}
Last bear argument: {current_response}
""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Bull Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bull_history": bull_history + "\n" + argument,
            "bear_history": investment_debate_state.get("bear_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bull_node
