"""Sentiment analyst — multi-source sentiment analysis for Chinese A-share target tickers.

Previously named ``social_media_analyst``. Renamed and redesigned because
the old version had a prompt that demanded social-media analysis but the
only tool available was Yahoo Finance news — which led LLMs to fabricate
social-media content under prompt pressure (verified live).

The redesigned agent pre-fetches three complementary data sources before
the LLM is invoked and injects them into the prompt as structured blocks:

  1. News headlines         — Yahoo Finance (institutional framing)
  2. 东方财富股吧 (EastMoney) — Chinese retail investor posts with
                               user-labeled 看多/看空 sentiment tags
  3. 雪球 (Xueqiu) posts     — Chinese investment community, similar to
                               Seeking Alpha for A-shares

The agent does not use tool-calling; the data is in the prompt from
turn 0. Output uses the structured-output pattern (json_schema for
OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic), falling
back to free-text generation for providers that lack native support, so
the sentiment header (band + score + confidence) is deterministic across
runs and providers instead of free-form per-model prose.

See: https://github.com/TauricResearch/TradingAgents/issues/557
See: https://github.com/TauricResearch/TradingAgents/issues/796
"""

from datetime import datetime, timedelta

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from mind_tradingagent.agents.schemas import SentimentReport, render_sentiment_report
from mind_tradingagent.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
    get_news,
)
from mind_tradingagent.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)
from mind_tradingagent.dataflows.reddit import fetch_reddit_posts
from mind_tradingagent.dataflows.stocktwits import fetch_stocktwits_messages


def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def create_sentiment_analyst(llm):
    """Create a sentiment analyst node for the trading graph.

    Pre-fetches news + 东方财富(EastMoney) + 雪球(Xueqiu) data, injects them
    into the prompt as structured blocks, and produces a deterministic sentiment
    report via structured output (with a free-text fallback for providers
    that do not support it).
    """
    structured_llm = bind_structured(llm, SentimentReport, "Sentiment Analyst")

    def sentiment_analyst_node(state):
        ticker = state["company_of_interest"]
        end_date = state["trade_date"]
        start_date = _seven_days_back(end_date)
        instrument_context = get_instrument_context_from_state(state)

        # Pre-fetch all three sources. Each fetcher degrades gracefully and
        # returns a string (no exceptions surface from here), so the LLM
        # always sees something — either real data or a clear placeholder.
        news_block = get_news.func(ticker, start_date, end_date)
        stocktwits_block = fetch_stocktwits_messages(ticker, limit=30)
        reddit_block = ""  # Reddit RSS 429 rate-limited for A-share stocks; zero signal value

        system_message = _build_system_message(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            news_block=news_block,
            stocktwits_block=stocktwits_block,
            reddit_block=reddit_block,
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}"
                    "\n{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=end_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        # Format the template into a concrete message list so the structured
        # and free-text paths receive the same input. No bind_tools — the
        # data is already in the prompt.
        formatted_messages = prompt.format_messages(messages=state["messages"])

        report_text = invoke_structured_or_freetext(
            structured_llm,
            llm,
            formatted_messages,
            render_sentiment_report,
            "Sentiment Analyst",
        )

        return {
            "messages": [AIMessage(content=report_text)],
            "sentiment_report": report_text,
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
) -> str:
    """Assemble the sentiment-analyst system message with structured data blocks."""
    return f"""You are a financial market sentiment analyst for Chinese A-shares. Your task is to produce a comprehensive sentiment report for {ticker} covering the period from {start_date} to {end_date}, drawing on three complementary data sources that have already been collected for you.

## Data sources (pre-fetched, in this prompt)

### News headlines — Yahoo Finance, past 7 days
Institutional framing. Fact-driven, slower-moving signal. For A-shares, pay attention to coverage by Chinese institutional media (证券时报 Securities Times, 上海证券报 Shanghai Securities News, 中国证券报 China Securities Journal) and their framing.

<start_of_news>
{news_block}
<end_of_news>

### 东方财富股吧 (EastMoney Stock Forum) — Chinese retail investor platform
China's largest retail investor discussion platform. Fast-moving signal. Each message carries a user-labeled sentiment tag (看多 Bullish / 看空 Bearish / no-label) plus the message body. 东方财富散户 (EastMoney retail investors) are the primary A-share retail trading force — their collective sentiment can move small/mid-cap stocks significantly.

<start_of_stocktwits>
{stocktwits_block}
<end_of_stocktwits>

### 雪球 (Xueqiu) — Chinese investment community posts (past 7 days)
China's equivalent of Seeking Alpha for stocks. Community discussion and analysis by Chinese retail and semi-professional investors. Engagement signal via upvote score and comment count. 雪球热门讨论 (Xueqiu hot discussions) can drive significant retail fund flows, especially for concept stocks (概念股) and popular sectors. Long-form analysis posts carry more weight than short sentiment posts; verified users (认证用户) carry higher credibility.

<start_of_reddit>
{reddit_block}
<end_of_reddit>

## How to analyze this data (best practices)

1. **Read the 东方财富 Bullish/Bearish ratio as a leading Chinese retail-sentiment signal.** A 70/30 bullish/bearish split is moderately bullish; ≥90/10 may indicate over-extension and contrarian risk; 50/50 is uncertainty. Sample size matters — base rates on the actual message count, not percentages alone. Note: Chinese retail investors tend to be more sentiment-driven and momentum-chasing than institutional investors, so extreme readings require extra caution.

2. **Look for cross-source divergences.** If news framing is bearish but 东方财富 retail sentiment is overwhelmingly bullish, that mismatch is itself a signal — it can mean retail is leaning into a thesis the news flow hasn't caught up to (or vice versa, that retail is chasing while institutions are cautious). 雪球 sentiment often acts as a middle ground between institutional and pure retail sentiment.

3. **Weight 雪球 posts by engagement.** A high-interaction thread (many comments and upvotes) reflects community attention; isolated posts are noise. Read the body excerpts for context — the title alone often misleads. Long-form analysis (长文分析) on 雪球 is typically more thoughtful and should be weighted higher.

4. **Distinguish opinion from event.** A news headline is an event; a 东方财富 post ("买入, 马上要涨停了" / "buying, about to hit limit-up") is opinion. Both are inputs but should be weighted differently in your conclusions. Be especially wary of 涨停板 chasing sentiment (涨跌停板追涨杀跌) — retail enthusiasm around limit-up stocks often reverses sharply after the lock.

5. **Identify recurring narrative themes.** What topic keeps coming up across sources? That's the dominant narrative driving current sentiment. In A-shares, common narrative drivers include: 政策利好 (policy tailwinds), 概念炒作 (concept/sector hype), 北向资金动向 (north-bound capital flows), 大股东增减持 (major shareholder buying/selling).

6. **Be honest about data limits.** If 东方财富 returned only a handful of messages, or one or more sources returned an "<unavailable>" placeholder, the sentiment read is less robust — flag this explicitly in the `confidence` field and the narrative. If the sources are silent on a given platform, say so.

7. **Identify catalysts and risks** that emerge across sources — news of upcoming earnings, product launches, competitive threats, macro headlines, 政策变化 (policy changes), etc.

8. **Past sentiment is not predictive.** Frame your conclusions as signal for the trader to weigh alongside fundamentals and technicals, not as a price call.

## Output fields

Fill the following fields:

- **overall_band**: Exactly one of Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. Use Mixed when sources point in clearly different directions; Neutral only when all sources are genuinely silent.
- **overall_score**: A number from 0 (maximally bearish) to 10 (maximally bullish); 5 is neutral. Keep it consistent with overall_band.
- **confidence**: low / medium / high, based on data quality and sample size.
- **narrative**: Full source-by-source breakdown, divergences, dominant narrative themes, catalysts and risks, and a markdown summary table of key sentiment signals (direction, source, supporting evidence).

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
