from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from mind_tradingagent.agents.utils.agent_utils import (
    build_instrument_context,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_insider_transactions,
    get_language_instruction,
)
from mind_tradingagent.dataflows.config import get_config


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
        ]

        system_message = (
            "You are an A-share fundamentals analyst. Analyze the company's financial health with special attention to A-share-specific risk factors."
            + "\n\n"
            + """**🚨 A-share Red Flag Checklist — You MUST explicitly check and report on each:**

1. **ST / *ST Status**: Is the stock under Special Treatment? ST = ±5% limit + delisting risk. *ST = imminent delisting. Flag as CRITICAL immediately.

2. **商誉减值 (Goodwill Impairment)**: Check balance sheet for 商誉 (goodwill) relative to net assets. Ratio >30% of net assets = elevated impairment risk.

3. **股权质押 (Share Pledging)**: High pledge ratios (>50%) create forced liquidation risk if price drops below maintenance thresholds.

4. **非经常性损益 (Non-Recurring P&L)**: If 扣非净利润 diverges significantly from reported 净利润, flag earnings quality concerns.

5. **政府补贴依赖 (Subsidy Dependence)**: 政府补贴/营业外收入 >30% of net profit means earnings are not self-sustaining.

6. **行业政策暴露 (Policy Exposure)**: Flag vulnerability to: 反垄断, 环保, 集采 (pharma), 房地产调控, 数据安全.

7. **大股东减持 (Major Shareholder Reduction)**: Monitor for announcements of 大股东减持 — a strong negative signal.

Use the available tools: get_fundamentals (includes 资金流向 data), get_balance_sheet, get_cashflow, and get_income_statement."""
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + get_language_instruction(),
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
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
