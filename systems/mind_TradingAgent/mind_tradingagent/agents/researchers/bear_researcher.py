from mind_tradingagent.agents.utils.agent_utils import get_language_instruction


def create_bear_researcher(llm):
    def bear_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bear_history = investment_debate_state.get("bear_history", "")

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

        prompt = f"""You are a Bear Analyst making the case against investing in the {target_label} on China's A-share market. Present a well-reasoned argument emphasizing A-share-specific risks.

**A-share Bear Thesis Dimensions — prioritize when evidence exists:**

- **政策逆风 (Policy Headwind)**: Is the company exposed to regulatory crackdowns (反垄断, 数据安全, 教育双减, 房地产调控)? Industry restrictions or subsidy phase-outs?

- **资金流出风险 (Capital Outflow)**: Is north-bound capital flowing out? Is 主力资金 net selling? Outflows often precede broader selling pressure.

- **融资盘风险 (Margin Call Cascade)**: High margin balance (~2.3万亿 nationwide) creates forced-liquidation tail risk if price drops — amplified by T+1/limit-down combo.

- **ST / 退市风险 (Delisting Risk)**: Consecutive losses, low revenue, audit issues, or regulatory violations that could trigger ST designation or delisting.

- **公司治理风险 (Governance)**: 商誉减值 (goodwill impairment), 股权质押 (share pledging with forced liquidation risk), 非经常性损益 manipulation, excessive 政府补贴 dependence.

- **估值泡沫 (Valuation Bubble)**: A-share retail-driven rallies can detach from fundamentals. Is price supported by earnings or speculation?

- **Bull Counterpoints**: Critically analyze bull assumptions based on short-term sentiment rather than fundamental value.

Resources available:
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
{fundamentals_label}: {fundamentals_report}
Conversation history of the debate: {history}
Last bull argument: {current_response}
""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Bear Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bear_history": bear_history + "\n" + argument,
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bear_node
