"""Sentiment analyst — multi-source sentiment analysis for a target ticker.

Previously named ``social_media_analyst``. Renamed and redesigned because
the old version had a prompt that demanded social-media analysis but the
only tool available was Yahoo Finance news — which led LLMs to fabricate
Reddit/X/StockTwits content under prompt pressure (verified live).

The redesigned agent pre-fetches three complementary data sources before
the LLM is invoked and injects them into the prompt as structured blocks:

  1. News headlines     — Yahoo Finance (institutional framing)
  2. StockTwits messages — retail-trader posts indexed by cashtag, with
                           user-labeled Bullish/Bearish sentiment tags
  3. Reddit posts        — r/wallstreetbets, r/stocks, r/investing

The agent does not use tool-calling; the data is in the prompt from
turn 0. The LLM produces the sentiment report in a single invocation.

See: https://github.com/TauricResearch/TradingAgents/issues/557
"""

from datetime import datetime, timedelta

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from mind_tradingagent.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
    get_news,
)
from mind_tradingagent.dataflows.reddit import fetch_reddit_posts
from mind_tradingagent.dataflows.stocktwits import fetch_stocktwits_messages
from mind_tradingagent.dataflows.xueqiu import (
    fetch_xueqiu_hot_tweets,
    fetch_xueqiu_stock_comments,
)


def _is_ashare(ticker: str) -> bool:
    """Detect A-share tickers by exchange suffix."""
    return ticker.upper().endswith((".SS", ".SZ", ".SH"))


def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def create_sentiment_analyst(llm):
    """Create a sentiment analyst node for the trading graph.

    Pre-fetches news + social sentiment data, injects them into the
    prompt as structured blocks, and produces a sentiment report in a
    single LLM call.

    For A-share tickers, uses Xueqiu (雪球) + EastMoney (东方财富股吧)
    as sentiment sources instead of StockTwits/Reddit (which only cover US stocks).
    """

    def sentiment_analyst_node(state):
        ticker = state["company_of_interest"]
        end_date = state["trade_date"]
        start_date = _seven_days_back(end_date)
        instrument_context = build_instrument_context(ticker)
        is_ashare = _is_ashare(ticker)

        # Pre-fetch data sources. Each fetcher degrades gracefully.
        news_block = get_news.func(ticker, start_date, end_date)

        if is_ashare:
            # A-share: use Chinese sentiment sources
            stocktwits_block = fetch_xueqiu_hot_tweets(ticker, limit=20)
            reddit_block = fetch_xueqiu_stock_comments(ticker, limit=30)
        else:
            # Non-A-share: use original Western sources
            stocktwits_block = fetch_stocktwits_messages(ticker, limit=30)
            reddit_block = fetch_reddit_posts(ticker)

        system_message = _build_system_message(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            news_block=news_block,
            stocktwits_block=stocktwits_block,
            reddit_block=reddit_block,
            is_ashare=is_ashare,
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    "\n{system_message}\n"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=end_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        # No bind_tools — the data is already in the prompt; a single LLM
        # call produces the report directly.
        chain = prompt | llm
        result = chain.invoke(state["messages"])

        return {
            "messages": [result],
            "sentiment_report": result.content,
        }

    return sentiment_analyst_node


def _build_system_message(
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    news_block: str,
    stocktwits_block: str,
    reddit_block: str,
    is_ashare: bool = False,
) -> str:
    """Assemble the sentiment-analyst system message with structured data blocks."""
    if is_ashare:
        sentiment_source_desc = """
### 雪球 (Xueqiu) hot tweets — Chinese retail-trader social platform
Fast-moving signal. China's largest retail investor community. Hot tweets reflect trending discussion topics with retweet and like counts.

<start_of_social_a>
{stocktwits_block}
<end_of_social_a>

### 东方财富股吧 (EastMoney Guba) stock comments — Chinese stock forum
Community discussion. Largest A-share investor forum. Includes a simple bullish/bearish sentiment breakdown based on keyword analysis.

<start_of_social_b>
{reddit_block}
<end_of_social_b>
"""
        analysis_guide = """
1. **Read the Xueqiu hot tweet engagement** as a leading retail-sentiment signal. High retweet/like counts indicate strong community attention. Topics that appear across multiple tweets suggest a dominant narrative.

2. **Look for cross-source divergences.** If news framing is bearish but Guba comments are overwhelmingly bullish, that mismatch is itself a signal.

3. **Distinguish opinion from event.** A news headline is an event; a Guba comment is opinion. Weight them accordingly.

4. **Identify recurring narrative themes.** What topic keeps coming up across sources? That's the dominant narrative driving current sentiment.

5. **Be honest about data limits.** If a source returned '<unavailable>', flag this caveat explicitly.

6. **Identify catalysts and risks** that emerge across sources.

7. **Past sentiment is not predictive.** Frame as signal for the trader alongside fundamentals and technicals.

8. **北向资金 (North-bound Capital) Context**: Foreign capital via Stock Connect (~6% daily volume) is a leading sentiment indicator. Note: Since April 2024, daily disclosure changed to quarterly — interpret available data with awareness of reporting lag.

9. **两融 (Margin Trading) Signal**: Margin balance (~2.3万亿, ~11% daily volume) is a sentiment barometer. Rising = bullish retail positioning; extremely high = elevated forced-liquidation risk if market turns.

10. **Retail Sentiment Persistence**: Due to ~80% retail volume, extreme sentiment ratios often persist longer than in Western markets due to herd effects. A 90/10 bullish ratio does NOT automatically signal contrarian reversal — it signals momentum that continues until a catalyst (policy change, major news) triggers unwinding.

11. **Policy Signal Detection**: Scan Xueqiu/Guba for mentions of: 政策, 监管, 降准/降息, 国家队, 印花税. When policy terms spike in frequency, flag as a macro sentiment driver.
"""
    else:
        sentiment_source_desc = """
### StockTwits messages — retail-trader social platform indexed by cashtag
Fast-moving signal. Each message carries a user-labeled sentiment tag (Bullish / Bearish / no-label) plus the message body.

<start_of_social_a>
{stocktwits_block}
<end_of_social_a>

### Reddit posts — r/wallstreetbets, r/stocks, r/investing (past 7 days)
Community discussion. Engagement signal via upvote score and comment count. Subreddit character matters (r/wallstreetbets is often contrarian/exuberant; r/stocks more measured; r/investing longer-term).

<start_of_social_b>
{reddit_block}
<end_of_social_b>
"""
        analysis_guide = """
1. **Read the StockTwits Bullish/Bearish ratio as a leading retail-sentiment signal.** A 70/30 bullish/bearish split is moderately bullish; ≥90/10 may indicate over-extension and contrarian risk; 50/50 is uncertainty. Sample size matters — base rates on the actual message count, not percentages alone.

2. **Look for cross-source divergences.** If news framing is bearish but StockTwits is overwhelmingly bullish, that mismatch is itself a signal.

3. **Weight Reddit posts by engagement.** A 400-upvote / 200-comment thread reflects community attention; a 3-upvote post is noise. Read the body excerpts for context.

4. **Distinguish opinion from event.** A news headline is an event; a StockTwits post is opinion. Both are inputs but weight differently.

5. **Identify recurring narrative themes.** What topic keeps coming up across sources?

6. **Be honest about data limits.** If a source returned '<unavailable>', flag this caveat explicitly.

7. **Identify catalysts and risks** that emerge across sources.

8. **Past sentiment is not predictive.** Frame as signal for the trader alongside fundamentals and technicals.
"""

    return f"""You are a financial market sentiment analyst. Your task is to produce a comprehensive sentiment report for {ticker} covering the period from {start_date} to {end_date}, drawing on complementary data sources that have already been collected for you.

## Data sources (pre-fetched, in this prompt)

### News headlines — Yahoo Finance, past 7 days
Institutional framing. Fact-driven, slower-moving signal.

<start_of_news>
{news_block}
<end_of_news>

{sentiment_source_desc}

## How to analyze this data (best practices)

{analysis_guide}

## Output

Produce a sentiment report covering, in order:

1. **Overall sentiment direction** — Bullish / Bearish / Neutral / Mixed — with a brief confidence note based on data quality and sample size.
2. **Source-by-source breakdown** — what each data source is telling you, with specific evidence (cite message counts, ratios, notable posts).
3. **Divergences, alignments, and key narratives** across sources.
4. **Catalysts and risks** surfaced by the data.
5. **Markdown table** at the end summarizing key sentiment signals, their direction, source, and supporting evidence.

{get_language_instruction()}"""


# ---------------------------------------------------------------------------
# Backwards-compatibility shim
# ---------------------------------------------------------------------------
def create_social_media_analyst(llm):
    """Deprecated alias for :func:`create_sentiment_analyst`.

    Kept so existing code that imports ``create_social_media_analyst``
    continues to work.

    .. deprecated::
        Import :func:`create_sentiment_analyst` directly instead.
    """
    import warnings
    warnings.warn(
        "create_social_media_analyst is deprecated and will be removed in a "
        "future version. Use create_sentiment_analyst instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_sentiment_analyst(llm)
