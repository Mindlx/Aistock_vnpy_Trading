"""Trader: turns the Research Manager's investment plan into a concrete transaction proposal."""

from __future__ import annotations

import functools

from langchain_core.messages import AIMessage

from mind_tradingagent.agents.schemas import TraderProposal, render_trader_proposal
from mind_tradingagent.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from mind_tradingagent.agents.utils.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_structured_or_freetext,
)


def create_trader(llm):
    structured_llm = bind_structured(llm, TraderProposal, "Trader")

    def trader_node(state, name):
        company_name = state["company_of_interest"]
        instrument_context = get_instrument_context_from_state(state)
        investment_plan = state["investment_plan"]

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a trading agent analyzing market data to make investment decisions for Chinese A-share stocks. "
                    "Key A-share trading rules to incorporate: (1) T+1 settlement — shares purchased today cannot be sold until the next trading day; "
                    "(2) 涨跌停板 ±10% daily price limits (±20% for ChiNext/STAR, ±5% for ST stocks) — orders beyond the limit band are rejected; "
                    "(3) A股交易时间 — 9:15-9:25 集合竞价 (call auction, orders cancellable 9:15-9:20), 9:30-11:30 and 13:00-15:00 连续竞价 (continuous auction); "
                    "(4) Minimum order size: 1手 = 100 shares — all buy/sell quantities must be multiples of 100; "
                    "(5) ST (Special Treatment) stocks — elevated delisting risk, additional trading restrictions, limit portfolio exposure. "
                    "Anchor your reasoning in the analysts' reports and the research plan. "
                    + NO_EXTERNAL_TOOLS
                    + get_language_instruction()
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Based on a comprehensive analysis by a team of analysts, here is an investment "
                    f"plan tailored for {company_name}. {instrument_context} This plan incorporates "
                    f"insights from current technical market trends, macroeconomic indicators, and "
                    f"social media sentiment. Use this plan as a foundation for evaluating your next "
                    f"trading decision.\n\nProposed Investment Plan: {investment_plan}\n\n"
                    f"Leverage these insights to make an informed and strategic decision."
                ),
            },
        ]

        trader_plan = invoke_structured_or_freetext(
            structured_llm,
            llm,
            messages,
            render_trader_proposal,
            "Trader",
        )

        return {
            "messages": [AIMessage(content=trader_plan)],
            "trader_investment_plan": trader_plan,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
