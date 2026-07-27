import functools
import logging
from collections.abc import Mapping
from typing import Any

import yfinance as yf
from langchain_core.messages import HumanMessage, RemoveMessage

# Import tools from separate utility files
from mind_tradingagent.agents.utils.core_stock_tools import (
    get_stock_data,
)
from mind_tradingagent.agents.utils.technical_indicators_tools import (
    get_indicators,
)
from mind_tradingagent.agents.utils.fundamental_data_tools import (
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
)
from mind_tradingagent.agents.utils.macro_data_tools import (
    get_macro_indicators,
)
from mind_tradingagent.agents.utils.market_data_validation_tools import (
    get_verified_market_snapshot,
)
from mind_tradingagent.agents.utils.news_data_tools import (
    get_capital_flows,
    get_global_news,
    get_insider_transactions,
    get_news,
)
from mind_tradingagent.agents.utils.prediction_markets_tools import (
    get_prediction_markets,
)

# Public surface: the data tools are imported here so agents and the graph
# import them from one place, plus the instrument/language helpers defined below.
__all__ = [
    "get_stock_data",
    "get_indicators",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_news",
    "get_global_news",
    "get_insider_transactions",
    "get_capital_flows",
    "get_macro_indicators",
    "get_prediction_markets",
    "get_verified_market_snapshot",
    "build_instrument_context",
    "resolve_instrument_identity",
    "get_instrument_context_from_state",
    "get_language_instruction",
    "create_msg_delete",
]

logger = logging.getLogger(__name__)


def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Applied to every agent whose output reaches the saved report —
    analysts, researchers, debaters, research manager, trader, and
    portfolio manager — so a non-English run produces a fully localized
    report rather than a mix of languages.
    """
    from mind_tradingagent.dataflows.config import get_config
    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return "\nKeep your response under 400 words."
    return f"\n请用中文回复，控制在800字以内。"


def _clean_identity_value(value: Any) -> str | None:
    """Return a trimmed string, or None for empty / placeholder-ish values."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"none", "n/a", "nan", "null"}:
        return None
    return cleaned


@functools.lru_cache(maxsize=256)
def resolve_instrument_identity(ticker: str) -> dict:
    """Resolve deterministic identity metadata (company name, sector, …) for a ticker.

    Two-stage lookup:
      1. A_SHARE_MARKET_MAP (local, zero-network) — returns Chinese name + exchange.
      2. WarehouseReader fundamentals (cache-first) — returns industry, market cap, etc.
      3. Fallback (any 6-digit code without suffix is treated as A-share).
    Cacheable so the lookup happens at most once per ticker per process.
    """
    bare = ticker.replace(".SS", "").replace(".SZ", "").replace(".SH", "").strip()

    identity: dict[str, str] = {}

    try:
        from src.mind_stock_config import A_SHARE_MARKET_MAP
        if bare in A_SHARE_MARKET_MAP:
            yf_ticker, market, cname = A_SHARE_MARKET_MAP[bare]
            identity["company_name"] = cname
            identity["exchange"] = "SH" if market == "SH" else "SZ"
            identity["quote_type"] = "stock"
    except (ImportError, Exception):
        pass

    if "sector" not in identity or "industry" not in identity:
        try:
            from services.data_warehouse import WarehouseReader
            reader = WarehouseReader()
            fund = reader.get_fundamentals(bare)
            if fund:
                if "industry" in fund:
                    identity["industry"] = str(fund["industry"])
                if "sector" in fund:
                    identity["sector"] = str(fund["sector"])
                if not identity.get("company_name") and "company_name" in fund:
                    identity["company_name"] = str(fund["company_name"])
        except (ImportError, Exception):
            pass

    if not identity and len(bare) == 6 and bare.isdigit():
        identity["company_name"] = bare
        identity["exchange"] = "SH" if bare.startswith(("6", "5", "9")) else "SZ"
        identity["quote_type"] = "stock"

    return identity


def build_instrument_context(
    ticker: str,
    asset_type: str = "stock",
    identity: Mapping[str, str] | None = None,
) -> str:
    """Describe the exact instrument so agents preserve identity and ticker.

    When ``identity`` is provided (resolved deterministically via
    :func:`resolve_instrument_identity`), the company name and business
    classification are injected so agents anchor to the real company rather
    than pattern-matching the price chart to a wrong one (#814).
    """
    is_crypto = asset_type == "crypto"
    instrument_label = "asset" if is_crypto else "instrument"
    context = (
        f"The {instrument_label} to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`, `-USD`)."
    )

    details = []
    if identity:
        name = identity.get("company_name") or identity.get("name")
        if name:
            details.append(f"{'Name' if is_crypto else 'Company'}: {name}")
        sector, industry = identity.get("sector"), identity.get("industry")
        if sector and industry:
            details.append(f"Business classification: {sector} / {industry}")
        elif sector:
            details.append(f"Sector: {sector}")
        elif industry:
            details.append(f"Industry: {industry}")
        if identity.get("exchange"):
            details.append(f"Exchange: {identity['exchange']}")

    if details:
        context += (
            f" Resolved identity: {'; '.join(details)}. "
            "Do not substitute a different company or ticker unless a tool "
            "result explicitly disproves this resolved identity."
        )

    if is_crypto:
        context += (
            " Treat it as a crypto asset rather than a company, and do not "
            "assume company fundamentals are available."
        )
    return context


def get_instrument_context_from_state(state: Mapping[str, Any]) -> str:
    """Return the instrument context for the current run.

    Prefers the identity-resolved context computed once at run start and
    stored on the state (see ``TradingAgentsGraph.resolve_instrument_context``).
    Falls back to a ticker-only context — with no network lookup — when the
    state was constructed without it (bare programmatic states, tests), so a
    consumer is never forced to make a yfinance call mid-graph.
    """
    context = state.get("instrument_context")
    if isinstance(context, str) and context.strip():
        return context
    return build_instrument_context(
        str(state["company_of_interest"]),
        state.get("asset_type", "stock"),
    )


def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add a context-anchored placeholder.

        The placeholder must not be a bare ``"Continue"``: some
        OpenAI-compatible providers interpret that literally as the user task
        and produce output about the word "continue" instead of analysing the
        instrument (#888). Anchoring it to the resolved instrument context and
        date keeps the next analyst on-task even if the provider treats the
        placeholder as a standalone request.

        Note: replaces messages wholesale instead of using RemoveMessage
        to avoid race conditions with parallel analyst execution.
        """
        instrument_context = get_instrument_context_from_state(state)
        trade_date = state.get("trade_date", "the requested date")
        placeholder = HumanMessage(
            content=(
                f"Proceed with your assigned analysis for this workflow. "
                f"{instrument_context} The analysis date is {trade_date}."
            )
        )
        return {"messages": [placeholder]}

    return delete_messages



