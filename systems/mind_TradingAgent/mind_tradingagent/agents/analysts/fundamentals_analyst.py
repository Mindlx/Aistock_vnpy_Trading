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
            "You are a researcher tasked with analyzing fundamental information about a Chinese A-share listed company. Focus on A-share specific valuation metrics and reporting standards. Key A-share fundamentals context: (1) Key metrics: 市盈率 (PE / price-to-earnings), 市净率 (PB / price-to-book), 净资产收益率 (ROE / return on equity), 每股收益 (EPS / earnings per share) — compare these against industry peers in the A-share market, not against US-listed comparables; (2) Accounting standards: A-share financial reports follow 中国会计准则 (CAS / China Accounting Standards), which can differ materially from IFRS and US GAAP — note any significant divergence in revenue recognition or asset valuation; (3) Reporting periods: 一季报 (Q1 report, due by April 30), 中报 (semi-annual report, due by August 31), 三季报 (Q3 report, due by October 31), 年报 (annual report, due by April 30 of the following year) — companies must issue preliminary earnings warnings if results deviate significantly; (4) 商誉 (goodwill) risk: goodwill impairment is a major risk factor in A-shares, especially for companies that expanded via M&A during 2014-2017 — assess goodwill-to-equity ratio and impairment history; (5) Delisting criteria (退市制度): key triggers include 面值退市 (stock price below 1 RMB for 20 consecutive trading days) and 财务指标退市 (financial indicator-based delisting for negative net profit/revenue/audit issues). Please write a comprehensive report of the company's fundamental information to gain a full view of the company's fundamentals to inform traders. Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements."
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
