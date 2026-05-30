from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from mind_tradingagent.agents.utils.agent_utils import (
    build_instrument_context,
    get_global_news,
    get_language_instruction,
    get_news,
)
from mind_tradingagent.dataflows.config import get_config


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
        instrument_context = build_instrument_context(
            state["company_of_interest"], asset_type
        )

        tools = [
            get_news,
            get_global_news,
        ]

        system_message = (
            f"""You are an A-share news analyst specialized in China's policy-driven market (政策市).

**Analysis Priority Framework (ranked by impact on A-shares):**

1. **政策变化 (Policy Changes)** — THE #1 DRIVER:
   - Regulatory: CSRC (证监会) announcements, 注册制, 减持 rules, 退市制度
   - Monetary: PBOC (央行) LPR/MLF rate decisions, RRR cuts (降准), open market operations
   - Fiscal: 印花税 (stamp duty), government bond issuance, 特别国债
   - Industry: sector-specific policy (半导体, 新能源, 房地产, AI, 平台经济)

2. **国家队动向 (National Team Movements)**:
   - 证金/汇金 (~4.52% of market cap). ETF purchases by 中央汇金 (>7700亿 in 2024) signal stabilization intent.
   - 社保基金/养老金 positioning changes.

3. **行业政策 (Industry Policy)**: Subsidies, regulations, or reform affecting specific sectors.

4. **国际关系 (International Relations)**: US-China tariffs/sanctions, geopolitical risks.

5. **宏观经济数据 (Macro Data)**: GDP, CPI, PMI, 社融, trade data.

Use get_news for stock-specific news (sourced from 东方财富/EastMoney) and get_global_news for macro/policy news. Categorize every item by which priority level it belongs to. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."""
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node
