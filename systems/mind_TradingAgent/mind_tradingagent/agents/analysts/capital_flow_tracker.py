from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from mind_tradingagent.agents.utils.agent_utils import (
    build_instrument_context,
    get_capital_flows,
    get_language_instruction,
)


def create_capital_flow_tracker(llm):
    def capital_flow_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
        instrument_context = build_instrument_context(
            state["company_of_interest"], asset_type
        )

        tools = [get_capital_flows]

        system_message = (
            f"You are a capital flow analyst specializing in Chinese A-share market money flow. "
            f"Your task is to analyze capital flow data for the {asset_label} {state['company_of_interest']}. "
            f"Use get_capital_flows(ticker, days) to retrieve actual capital flow data. "
            f"Focus on: (1) Main force net inflow/outflow trends over the past 5-10 trading days, "
            f"(2) Distribution between super-large/large/medium/small order flows to gauge "
            f"institutional vs retail participation, "
            f"(3) Whether the multi-day trend is accumulating (consistent inflows) or distributing "
            f"(consistent outflows), "
            f"(4) Note any unusually large single-day flows (>2x the average daily absolute flow). "
            f"Provide specific data-backed insights about where the smart money is moving. "
            f"Note: A-share T+1 settlement means day-trading is not possible for most retail investors, "
            f"so capital flow trends over multiple days are more meaningful than intraday flows."
            + """ Make sure to append a Markdown table at the end of the report to organize key capital flow indicators."""
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
            "capital_flow_report": report,
        }

    return capital_flow_node
