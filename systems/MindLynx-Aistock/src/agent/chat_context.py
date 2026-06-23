"""
Chat context compression for multi-turn conversation efficiency.

Compresses older conversation messages using LLM summarization when
the conversation exceeds a token budget, preserving essential information
(stock codes, signal values, risk alerts) while reducing token usage.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Default max tokens before triggering compression
DEFAULT_MAX_TOKENS = 4000
# Reserve tokens for recent messages (uncompressed)
RECENT_MESSAGE_SLOT = 1000


def estimate_tokens(text: str) -> int:
    """Rough token estimation (~4 chars per token for Chinese/English mix)."""
    if not text:
        return 0
    return len(text) // 2 + 1


def compress_history(
    messages: list[dict[str, Any]],
    llm_adapter: Any | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[dict[str, Any]]:
    """Compress conversation history when it exceeds token budget.

    Strategy:
    1. Count total tokens in message history.
    2. If under budget, return as-is.
    3. If over budget: keep the last N recent messages uncompressed,
       compress the rest using LLM summarization.
    4. If no LLM adapter is available, keep only the last 10 messages
       as a simpler fallback.
    """
    if not messages:
        return messages

    total_tokens = sum(estimate_tokens(m.get("content", "")) for m in messages)

    if total_tokens <= max_tokens:
        return messages

    # Find how many recent messages fit in the reserved slot
    recent: list[dict[str, Any]] = []
    recent_tokens = 0
    for m in reversed(messages):
        tokens = estimate_tokens(m.get("content", ""))
        if recent_tokens + tokens > RECENT_MESSAGE_SLOT:
            break
        recent.insert(0, m)
        recent_tokens += tokens

    older = messages[: len(messages) - len(recent)]

    if not older:
        # All messages fit in the reserved slot already
        return messages

    if llm_adapter is not None:
        try:
            summary = _summarize_older(older, llm_adapter)
            return [{"role": "system", "content": f"[Conversation summary]\n{summary}"}] + recent
        except Exception as e:
            logger.warning("LLM context compression failed: %s. Using truncation fallback.", e)

    # Fallback: keep last 10 messages
    return messages[-10:]


def _summarize_older(messages: list[dict[str, Any]], llm_adapter: Any) -> str:
    """Call LLM to produce a dense summary of older messages."""
    prompt = (
        "Compress the following conversation into a concise summary. "
        "RETURN ONLY THE SUMMARY, no preamble. Preserve these details:\n"
        "- Any stock codes mentioned (e.g., 600519, AAPL, hk00700)\n"
        "- Any analysis signals (buy/sell/hold/strong_buy/strong_sell)\n"
        "- Any price levels, stop-loss, or target prices\n"
        "- Any risk alerts or warnings\n"
        "- Any user preferences expressed\n\nConversation:\n"
    )
    for m in messages:
        role = m.get("role", "unknown")
        content = m.get("content", "")
        prompt += f"\n{role}: {content[:500]}"

    response = llm_adapter.complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500,
        temperature=0.3,
    )
    content = getattr(response, "content", None) or getattr(response, "text", None) or str(response)
    # Remove leading/trailing quotes if present
    if content.startswith('"') and content.endswith('"'):
        content = content[1:-1]
    if content.startswith("'") and content.endswith("'"):
        content = content[1:-1]
    return content.strip()[:2000]


def should_compress(messages: list[dict[str, Any]], max_tokens: int = DEFAULT_MAX_TOKENS) -> bool:
    """Check if compression is needed."""
    total = sum(estimate_tokens(m.get("content", "")) for m in messages)
    return total > max_tokens
