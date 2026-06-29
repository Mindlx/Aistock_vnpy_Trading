from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from mind_tradingagent.agents.utils.agent_utils import (
    build_instrument_context,
    get_fundamentals,
    get_global_news,
    get_language_instruction,
    get_news,
)
from mind_tradingagent.dataflows.config import get_config


def create_policy_analyst(llm):
    def policy_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
        instrument_context = build_instrument_context(
            state["company_of_interest"], asset_type
        )

        tools = [
            get_news,
            get_global_news,
            get_fundamentals,
        ]

        system_message = (
            f"You are a policy analyst specializing in China A-share regulatory and industrial policy analysis. "
            f"Your task is to analyze how recent policy changes, regulatory announcements, and government directives "
            f"affect the investment outlook for the {asset_label} {state['company_of_interest']}. "
            f"Use the available tools: get_news(ticker, start_date, end_date) for {asset_label}-specific policy news, "
            f"get_global_news(curr_date, look_back_days, limit) for broader macro-policy changes, "
            f"and get_fundamentals(ticker, curr_date) to check the company's industry classification and market cap. "
            f"Focus on: (1) Recent CSRC (证监会) regulatory changes affecting the company's industry, "
            f"(2) National industrial policy (国家产业政策) directives (e.g. '新质生产力', '碳中和', '数字经济'), "
            f"(3) Tax/incentive policy changes, (4) Trade policy and export control impacts, "
            f"(5) State Council (国务院) and NDRC (发改委) policy signals. "
            f"Note: A-share market is heavily policy-driven — government directives can create sector-wide "
            f"rallies or selloffs within days. Policy analysis is HIGH PRIORITY for A-share investing."
            + """ Make sure to append a Markdown table at the end of the report to organize key policy indicators and their market implications."""
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
            "policy_report": report,
        }

    return policy_analyst_node
